"""Core logic for the local expense tracker.

Plain CSV files plus arithmetic -- no database, no hidden state.

Columns, in order: ``date, item, genre, amount, deviation, total``.

Only ``date``, ``item``, ``genre`` and ``amount`` are ever treated as input.
``deviation`` and ``total`` are *always* recomputed from scratch from those
four columns, and rows are always sorted by date before that happens.  A
stored deviation/total is never read back as if it were true.

Budget model
------------
200 rupees of allowance per day, unused allowance carries forward with no
limit.  The clock for a month starts on that month's earliest logged date --
logging your first entry on Oct 3 means Oct 1 and Oct 2 never accrue
allowance (and never get zero-rows).  For each row::

    total     = running sum of every amount so far this month
    deviation = DAILY_ALLOWANCE * days_since_first_logged_date - total

so ``deviation`` is how far above (negative) or below (positive) the
allowance you are at that point in the month.

Genre never touches this math; it is only used for the breakdown report.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import date as Date
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

# ---------------------------------------------------------------- constants

DAILY_ALLOWANCE = 200.0

FIELDS = ("date", "item", "genre", "amount", "deviation", "total")

MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
GENRE_FILE = CONFIG_DIR / "genres.json"

DEFAULT_GENRES = [
    "Food", "Groceries", "Transport", "Rent",
    "Bills", "Health", "Fun", "Shopping", "Other",
]

# A gap this big means a typo (a stray year, say), not a real month of rest days.
MAX_GAP_DAYS = 366

# One writer at a time: the dev server is threaded and every mutation rewrites
# a whole file.
_LOCK = threading.RLock()


class TrackerError(Exception):
    """Something the user can fix: a bad date, a bad amount, a stale row."""


# ------------------------------------------------------------------ numbers

def round2(value: float) -> float:
    """Money is rupees; keep two decimals so floats don't drift.

    Rounds half-up on the decimal text rather than the binary float, so
    60.555 becomes 60.56 the way you would round it on paper.
    """
    decimal = Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(decimal)


def fmt_num(value: float) -> str:
    """Format for the CSV: 400 rather than 400.0, 400.5 rather than 400.50."""
    value = round2(value)
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


# -------------------------------------------------------------------- dates

def parse_date(raw) -> Date:
    """Read a date typed by hand or written by the browser's date picker."""
    text = str(raw or "").strip()
    if not text:
        raise TrackerError("A date is required.")
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise TrackerError(f"Could not read the date {text!r}. Use YYYY-MM-DD.")


def parse_amount(raw) -> float:
    """Read a rupee amount. Blank means zero (that is how rest days are stored)."""
    text = str(raw if raw is not None else "").strip()
    text = text.replace(",", "").replace("₹", "").strip()
    if not text:
        return 0.0
    try:
        amount = float(text)
    except ValueError:
        raise TrackerError(f"Could not read the amount {text!r}.") from None
    if amount < 0:
        raise TrackerError("Amount cannot be negative.")
    return round2(amount)


def month_key(when: Date) -> str:
    """'2026-10' -- the key used between the frontend and the API."""
    return f"{when.year:04d}-{when.month:02d}"


def parse_month_key(key: str) -> tuple[int, int]:
    text = str(key or "").strip()
    try:
        year_s, month_s = text.split("-")
        year, month = int(year_s), int(month_s)
    except ValueError:
        raise TrackerError(f"Could not read the month {text!r}.") from None
    if not 1 <= month <= 12 or not 1900 <= year <= 2999:
        raise TrackerError(f"Could not read the month {text!r}.")
    return year, month


def month_label(key: str) -> str:
    year, month = parse_month_key(key)
    return f"{MONTH_NAMES[month - 1].capitalize()} {year}"


def month_path(key: str) -> Path:
    """One CSV per calendar month, e.g. data/october_2026.csv."""
    year, month = parse_month_key(key)
    return DATA_DIR / f"{MONTH_NAMES[month - 1]}_{year}.csv"


def month_key_from_path(path: Path) -> str | None:
    """Inverse of month_path, for listing what is on disk."""
    stem = path.stem.lower()
    for index, name in enumerate(MONTH_NAMES, start=1):
        prefix = f"{name}_"
        if stem.startswith(prefix):
            tail = stem[len(prefix):]
            if tail.isdigit():
                return f"{int(tail):04d}-{index:02d}"
    return None


# ------------------------------------------------------------------- genres

def load_genres() -> list[str]:
    """The dropdown's contents, kept in config/genres.json -- never in the CSV."""
    if not GENRE_FILE.exists():
        save_genres(DEFAULT_GENRES)
        return list(DEFAULT_GENRES)
    try:
        data = json.loads(GENRE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_GENRES)

    raw = data.get("genres") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return list(DEFAULT_GENRES)

    genres, seen = [], set()
    for entry in raw:
        name = " ".join(str(entry).split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            genres.append(name)
    return genres


def save_genres(genres: list[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"genres": list(genres)}, indent=2) + "\n"
    GENRE_FILE.write_text(payload, encoding="utf-8")


def add_genre(name: str) -> str:
    """Notion-style tags: a newly typed genre is remembered for next time.

    Returns the canonical spelling, so typing 'food' reuses the existing
    'Food' rather than creating a near-duplicate.
    """
    name = " ".join(str(name or "").split())
    if not name:
        return ""
    genres = load_genres()
    for existing in genres:
        if existing.lower() == name.lower():
            return existing
    genres.append(name)
    save_genres(genres)
    return name


# --------------------------------------------------------------------- rows

def is_placeholder(row: dict) -> bool:
    """A backfilled rest day: no item, no genre, no money."""
    return not row["item"] and not row["genre"] and round2(row["amount"]) == 0


def _to_row(cells: list[str]) -> dict:
    """Trusted columns only -- deviation/total in the file are ignored here."""
    return {
        "date": parse_date(cells[0]).isoformat(),
        "item": cells[1].strip(),
        "genre": cells[2].strip(),
        "amount": parse_amount(cells[3]),
    }


def _serialize(rows: list[dict]) -> list[list[str]]:
    return [
        [r["date"], r["item"], r["genre"], fmt_num(r["amount"]),
         fmt_num(r["deviation"]), fmt_num(r["total"])]
        for r in rows
    ]


def read_raw(path: Path) -> list[list[str]]:
    """Every data line of the CSV as raw cells, header and blank lines dropped."""
    if not path.exists():
        return []
    raw_rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for lineno, cells in enumerate(csv.reader(handle), start=1):
            if not cells or all(not c.strip() for c in cells):
                continue
            if not raw_rows and cells[0].strip().lower() == "date":
                continue
            if len(cells) < 4:
                raise TrackerError(
                    f"{path.name} line {lineno} is missing columns "
                    f"(expected {', '.join(FIELDS)})."
                )
            padded = list(cells[:len(FIELDS)])
            padded += [""] * (len(FIELDS) - len(padded))
            raw_rows.append(padded)
    return raw_rows


def read_rows(path: Path) -> list[dict]:
    """Rows in stored order, holding only the four columns we trust."""
    return [_to_row(cells) for cells in read_raw(path)]


def write_rows(path: Path, rows: list[dict]) -> None:
    """Write the file atomically, so a crash can't leave it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(_serialize(rows))
    os.replace(temp, path)


def recalculate(rows: list[dict]) -> list[dict]:
    """Sort chronologically, then rebuild deviation and total from scratch.

    Rows sharing a date keep the order they were entered in.  Allowance is
    counted per calendar day since the first logged date, so a day whose
    zero-row was deleted still accrues its 200.
    """
    ordered = sorted(rows, key=lambda r: r["date"])
    if not ordered:
        return []

    first = Date.fromisoformat(ordered[0]["date"])
    result, running = [], 0.0
    for row in ordered:
        running = round2(running + row["amount"])
        days = (Date.fromisoformat(row["date"]) - first).days + 1
        result.append({
            "date": row["date"],
            "item": row["item"],
            "genre": row["genre"],
            "amount": round2(row["amount"]),
            "deviation": round2(DAILY_ALLOWANCE * days - running),
            "total": running,
        })
    return result


def load_month(key: str, heal: bool = True) -> list[dict]:
    """A month's rows, freshly recalculated.

    If the numbers on disk disagree with what the four trusted columns imply
    (a hand edit, a truncated write), the file is rewritten to match.  The
    file is the thing you can read and edit; it is never the source of truth
    for deviation or total.
    """
    path = month_path(key)
    raw = read_raw(path)
    fresh = recalculate([_to_row(cells) for cells in raw])
    if heal and raw != _serialize(fresh):
        write_rows(path, fresh)
    return fresh


def list_months() -> list[dict]:
    """Months that have a CSV on disk, newest first."""
    if not DATA_DIR.exists():
        return []
    found = []
    for path in DATA_DIR.glob("*.csv"):
        key = month_key_from_path(path)
        if key:
            found.append(key)
    return sorted(set(found), reverse=True)


# --------------------------------------------------------------- gap filling

def _placeholders(start_iso: str, end_iso: str) -> list[dict]:
    """Blank zero-amount rows for the days strictly between two dates."""
    start, end = Date.fromisoformat(start_iso), Date.fromisoformat(end_iso)
    if (end - start).days > MAX_GAP_DAYS:
        raise TrackerError(
            f"That is a {(end - start).days}-day gap between {start_iso} and "
            f"{end_iso} -- check the dates."
        )
    rows, cursor = [], start + timedelta(days=1)
    while cursor < end:
        rows.append({"date": cursor.isoformat(), "item": "", "genre": "", "amount": 0.0})
        cursor += timedelta(days=1)
    return rows


# ----------------------------------------------------------------- mutations

def add_entry(date_raw, item, genre, amount_raw) -> tuple[str, list[dict]]:
    """Log an expense, backfilling rest days between it and its neighbours.

    Gap filling is driven purely by comparing the new date against the dates
    already in that month's file -- never against today's real date, so
    entries logged late or out of order behave the same as prompt ones.  No
    zero-rows are ever created before the month's first logged entry or after
    its last.

    Returns the month file that was written to, which is the month of the
    date you typed rather than whichever month was on screen.
    """
    when = parse_date(date_raw)
    item = " ".join(str(item or "").split())
    genre = " ".join(str(genre or "").split())
    amount = parse_amount(amount_raw)

    if not item:
        raise TrackerError("An item is required.")
    if amount <= 0:
        raise TrackerError("Amount must be greater than zero.")

    key = month_key(when)
    iso = when.isoformat()

    with _LOCK:
        path = month_path(key)
        rows = read_rows(path)
        already_dated = [r["date"] for r in rows]
        genre = add_genre(genre)   # canonical spelling, remembered for next time

        # Logging on a day that was backfilled as a rest day fills that day in
        # rather than adding a second row for it.
        filled_in = next(
            (r for r in rows if r["date"] == iso and is_placeholder(r)), None
        )
        if filled_in is not None:
            filled_in.update(item=item, genre=genre, amount=amount)
        else:
            # Only a genuinely new day opens up gaps. A second entry on a day
            # that already has one changes nothing, which keeps a rest day you
            # deleted by hand from reappearing.
            is_new_day = iso not in already_dated
            rows.append({"date": iso, "item": item, "genre": genre, "amount": amount})
            if is_new_day:
                before = [d for d in already_dated if d < iso]
                after = [d for d in already_dated if d > iso]
                if before:
                    rows.extend(_placeholders(before[-1], iso))
                if after:
                    rows.extend(_placeholders(iso, after[0]))

        fresh = recalculate(rows)
        write_rows(path, fresh)
    return key, fresh


def update_entry(key: str, index: int, date_raw, item, genre, amount_raw) -> list[dict]:
    """Edit one row, then rebuild the whole file.

    Rows are identified by position, so a stale index (the file changed
    underneath the browser) is an error rather than a silent wrong-row edit.
    """
    when = parse_date(date_raw)
    item = " ".join(str(item or "").split())
    genre = " ".join(str(genre or "").split())
    amount = parse_amount(amount_raw)

    if month_key(when) != key:
        raise TrackerError(
            f"{when.isoformat()} is not in {month_label(key)}. "
            "Log it as a new entry instead."
        )
    if not item and amount > 0:
        raise TrackerError("An item is required.")

    with _LOCK:
        path = month_path(key)
        rows = read_rows(path)
        if not 0 <= index < len(rows):
            raise TrackerError("That row no longer exists. Reload and try again.")

        rows[index] = {
            "date": when.isoformat(),
            "item": item,
            "genre": add_genre(genre),
            "amount": amount,
        }
        # No gap filling here: editing reshuffles what is already logged, and a
        # backfilled rest day you deleted should stay deleted.
        fresh = recalculate(rows)
        write_rows(path, fresh)
    return fresh


def delete_entry(key: str, index: int) -> list[dict]:
    """Remove one row -- rest days included -- then rebuild the whole file."""
    with _LOCK:
        path = month_path(key)
        rows = read_rows(path)
        if not 0 <= index < len(rows):
            raise TrackerError("That row no longer exists. Reload and try again.")

        rows.pop(index)
        fresh = recalculate(rows)
        write_rows(path, fresh)
    return fresh


# ------------------------------------------------------------------ reporting

def summarize(rows: list[dict], today: Date | None = None) -> dict:
    """Month totals for the stat cards. Genre only ever matters here.

    ``today`` is passed only when the month being viewed is the current one.
    The file itself always stops at the last logged entry -- days after it get
    no zero-rows -- so ``projection`` is how the page answers "what does my
    standing look like now that today's 200 has landed too", without
    inventing rows that were never logged.
    """
    if not rows:
        return {
            "entries": 0, "spent": 0.0, "allowance": 0.0, "deviation": 0.0,
            "days_tracked": 0, "per_day": DAILY_ALLOWANCE, "avg_per_day": 0.0,
            "first_date": None, "last_date": None, "by_genre": [],
            "projection": None,
        }

    first = Date.fromisoformat(rows[0]["date"])
    last = Date.fromisoformat(rows[-1]["date"])
    days = (last - first).days + 1
    spent = rows[-1]["total"]

    totals: dict[str, float] = {}
    for row in rows:
        if is_placeholder(row):
            continue
        label = row["genre"] or "Untagged"
        totals[label] = round2(totals.get(label, 0.0) + row["amount"])

    projection = None
    if today is not None and first <= today:
        days_now = (today - first).days + 1
        if days_now > days:
            projection = {
                "date": today.isoformat(),
                "days": days_now,
                "allowance": round2(DAILY_ALLOWANCE * days_now),
                "deviation": round2(DAILY_ALLOWANCE * days_now - spent),
            }

    return {
        "entries": sum(1 for r in rows if not is_placeholder(r)),
        "spent": spent,
        "allowance": round2(DAILY_ALLOWANCE * days),
        "deviation": rows[-1]["deviation"],
        "days_tracked": days,
        "per_day": DAILY_ALLOWANCE,
        "avg_per_day": round2(spent / days),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "projection": projection,
        "by_genre": sorted(
            ({"genre": g, "amount": a} for g, a in totals.items()),
            key=lambda item: -item["amount"],
        ),
    }
