"""Localhost-only web frontend for the expense tracker.

Every file read and write happens here; the browser never touches the CSVs.
Run with ``python app.py`` and open http://127.0.0.1:5000.
"""

from __future__ import annotations

import webbrowser
from threading import Timer

from flask import Flask, jsonify, render_template, request

import tracker
from tracker import TrackerError

app = Flask(__name__)


def current_month_key() -> str:
    """Which month to open on. Display only -- never used for gap filling."""
    return tracker.month_key(tracker.Date.today())


def month_payload(key: str) -> dict:
    """Everything the page needs to draw one month."""
    rows = tracker.load_month(key)
    today = tracker.Date.today()
    return {
        "month": key,
        "label": tracker.month_label(key),
        "file": tracker.month_path(key).name,
        "rows": [
            {"index": i, "placeholder": tracker.is_placeholder(row), **row}
            for i, row in enumerate(rows)
        ],
        # Only the current month gets a "where you'd stand today" figure.
        "summary": tracker.summarize(
            rows, today=today if tracker.month_key(today) == key else None
        ),
        "genres": tracker.load_genres(),
    }


@app.errorhandler(TrackerError)
def handle_tracker_error(error: TrackerError):
    return jsonify({"error": str(error)}), 400


@app.get("/")
def index():
    return render_template("index.html", today=tracker.Date.today().isoformat())


@app.get("/api/state")
def api_state():
    """The month list plus whichever month the page should open on."""
    requested = request.args.get("month") or current_month_key()
    try:
        key = tracker.month_key(tracker.parse_date(f"{requested}-01"))
    except TrackerError:
        key = current_month_key()

    payload = month_payload(key)
    payload["months"] = [
        {"month": m, "label": tracker.month_label(m)} for m in tracker.list_months()
    ]
    payload["today"] = tracker.Date.today().isoformat()
    return jsonify(payload)


@app.get("/api/months/<key>")
def api_month(key: str):
    return jsonify(month_payload(key))


@app.get("/api/genres")
def api_genres():
    return jsonify({"genres": tracker.load_genres()})


@app.post("/api/rows")
def api_add_row():
    body = request.get_json(silent=True) or {}
    key, _ = tracker.add_entry(
        body.get("date"), body.get("item"), body.get("genre"), body.get("amount")
    )
    # The entry may belong to a different month than the one on screen.
    return jsonify(month_payload(key))


@app.put("/api/rows/<int:index>")
def api_update_row(index: int):
    body = request.get_json(silent=True) or {}
    key = body.get("month") or current_month_key()
    tracker.update_entry(
        key, index, body.get("date"), body.get("item"), body.get("genre"), body.get("amount")
    )
    return jsonify(month_payload(key))


@app.delete("/api/rows/<int:index>")
def api_delete_row(index: int):
    key = request.args.get("month") or current_month_key()
    tracker.delete_entry(key, index)
    return jsonify(month_payload(key))


def main() -> None:
    tracker.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tracker.load_genres()  # create config/genres.json on first run
    url = "http://127.0.0.1:5000"
    print(f"\n  Expense tracker running at {url}\n  Press Ctrl+C to stop.\n")
    Timer(1.0, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 only: this never listens on the network.
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
