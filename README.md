# Expense tracker

A local expense tracker for a **₹200/day** budget, with unused allowance
carrying forward. Runs on `127.0.0.1` only — nothing is ever hosted, and
nothing leaves your machine.

Data lives in plain CSV files you can open in Excel or edit by hand.

## Running it

```bash
pip install -r requirements.txt
python app.py
```

It opens <http://127.0.0.1:5000> in your browser. `Ctrl+C` to stop.

Run the tests with `python -m unittest`.

## How the budget math works

Allowance is **₹200 per calendar day** and never expires.

```
total     = running sum of every amount so far this month
deviation = 200 × days since the month's first logged entry − total
```

`deviation` is how far you are *above* the line (positive, money in hand) or
*below* it (negative, overspent). Both columns are rewritten for the whole
file after every change.

Worked examples from the brief, as the app actually produces them:

| Date | Spent | Deviation | Total |
|------|-------|-----------|-------|
| Oct 1 | 400 | −200 | 400 |
| Oct 2 | 0 | 0 | 400 |
| Oct 3 | 100 | +100 | 500 |

Day 1 leaves you ₹200 down; day 2's untouched ₹200 cancels it exactly.

And the second example — Oct 1 spend ₹60 (deviation +140), Oct 2 spend ₹40
(deviation +300).

**Genre never touches this math.** It is only used for the "Where it went"
breakdown.

## The one judgement call

The clock for a month starts on that month's **first logged entry**, not on
the 1st. If your first entry is Oct 3, Oct 1 and Oct 2 never accrue allowance
and never get zero-rows — which follows from your "no gap-filling before the
first entry" rule, since the deviation is derived from the rows that exist.

The flip side: deleting the *first* entry of a month moves the clock forward
and shifts every later deviation. Change `DAILY_ALLOWANCE` or this rule in
`tracker.py` if you want it otherwise.

## Gap filling

Log Oct 3 and then Oct 10, and Oct 4–9 are inserted as blank zero-amount rows
before anything is recalculated.

- Driven **only** by comparing the new date to dates already in that month's
  file — never by today's real date, so logging days late or out of order
  behaves identically.
- **Never** before the first entry of a month, and never after the last.
- Rest days have genuinely empty `item` and `genre` cells, not placeholder text.
- Logging on a day that was already backfilled **fills that row in** rather
  than adding a second row for the same day.
- A rest day you delete by hand stays deleted. Deleting one doesn't lose its
  ₹200, because allowance is counted per calendar day, not per row.
- Gap filling happens on **add** only. Edits and deletes just re-sort and
  recalculate.

## Files

```
data/october_2026.csv      one file per month, named by month and year
config/genres.json         the genre dropdown, kept out of the CSVs
```

Columns, in order: `date,item,genre,amount,deviation,total`.

Only `date`, `item`, `genre` and `amount` are ever treated as input.
`deviation` and `total` are **never** trusted from the file: every add, edit
and delete re-sorts the rows by date and rebuilds both columns from scratch.
Opening a month also repairs the file if the numbers on disk disagree with the
four trusted columns — so a hand edit that breaks the math fixes itself, and
the file is always safe to read.

Rows are identified by **position**, not by an ID column. Every mutation
returns the whole freshly-recalculated month and the page redraws from that,
so a stale position can never silently edit the wrong row — it errors instead.

## Genres

Notion-tag style. Pick from the dropdown or type a new one; submitting saves it
to `config/genres.json` for next time. Matching is case-insensitive, so typing
`food` reuses the existing `Food`. Blank genres are allowed and not saved.

## Notes and limits

- Dates are stored as `YYYY-MM-DD`. Hand-typed `05/10/2026` and `05-10-2026`
  are also accepted and normalised on read.
- Amounts can't be negative (no refunds), and must be above zero on a new
  entry. Amounts round half-up to paise.
- Editing a row can't move it to another month — that would corrupt the file
  it's in. Log it in the target month instead.
- Adding an entry dated in a different month writes it to that month's file
  and switches the view there.
- The "Deviation" card shows your standing at your last logged entry. When
  you're viewing the current month and haven't logged today yet, a second line
  shows where you'd stand once today's ₹200 lands too — the file itself is
  never given rows you didn't log.
