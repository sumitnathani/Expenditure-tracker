"""End-to-end tests through the Flask routes the browser actually calls.

Run with:  python -m unittest -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app as app_module
import tracker


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (tracker.DATA_DIR, tracker.CONFIG_DIR, tracker.GENRE_FILE)
        tracker.DATA_DIR = root / "data"
        tracker.CONFIG_DIR = root / "config"
        tracker.GENRE_FILE = tracker.CONFIG_DIR / "genres.json"

        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        tracker.DATA_DIR, tracker.CONFIG_DIR, tracker.GENRE_FILE = self._saved
        self._tmp.cleanup()

    def add(self, date, amount, item="thing", genre="Food"):
        response = self.client.post("/api/rows", json={
            "date": date, "item": item, "genre": genre, "amount": amount,
        })
        return response

    # -- tests ------------------------------------------------------------

    def test_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Expense Tracker", response.data)

    def test_state_returns_everything_the_page_needs(self):
        self.add("2026-10-01", 400)
        payload = self.client.get("/api/state?month=2026-10").get_json()

        self.assertEqual(payload["month"], "2026-10")
        self.assertEqual(payload["label"], "October 2026")
        self.assertEqual(payload["file"], "october_2026.csv")
        self.assertEqual(len(payload["rows"]), 1)
        self.assertIn("Food", payload["genres"])
        self.assertIn("today", payload)
        self.assertIn({"month": "2026-10", "label": "October 2026"}, payload["months"])

    def test_rows_carry_their_index_and_a_placeholder_flag(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-03", 100)
        rows = self.client.get("/api/months/2026-10").get_json()["rows"]

        self.assertEqual([r["index"] for r in rows], [0, 1, 2])
        self.assertEqual([r["placeholder"] for r in rows], [False, True, False])
        self.assertEqual(rows[1]["item"], "")
        self.assertEqual(rows[2]["deviation"], 400)   # 600 allowed - 200 spent

    def test_adding_returns_the_whole_recalculated_month(self):
        self.add("2026-10-01", 400)
        payload = self.add("2026-10-03", 100).get_json()

        self.assertEqual(payload["month"], "2026-10")
        self.assertEqual([r["date"] for r in payload["rows"]],
                         ["2026-10-01", "2026-10-02", "2026-10-03"])
        self.assertEqual(payload["summary"]["deviation"], 100)

    def test_adding_outside_the_viewed_month_switches_month(self):
        payload = self.add("2026-11-04", 100).get_json()
        self.assertEqual(payload["month"], "2026-11")
        self.assertEqual(payload["label"], "November 2026")

    def test_editing_a_row(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-02", 100)
        payload = self.client.put("/api/rows/0", json={
            "month": "2026-10", "date": "2026-10-01",
            "item": "big spend", "genre": "Rent", "amount": 500,
        }).get_json()

        self.assertEqual([r["deviation"] for r in payload["rows"]], [-300, -200])
        self.assertEqual(payload["rows"][0]["genre"], "Rent")

    def test_deleting_a_row(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-02", 100)
        payload = self.client.delete("/api/rows/0?month=2026-10").get_json()

        self.assertEqual([r["date"] for r in payload["rows"]], ["2026-10-02"])
        self.assertEqual(payload["summary"]["spent"], 100)

    def test_genres_grow_as_you_type_new_ones(self):
        self.add("2026-10-01", 100, genre="Chai")
        genres = self.client.get("/api/genres").get_json()["genres"]
        self.assertIn("Chai", genres)

    def test_bad_input_comes_back_as_a_readable_error(self):
        response = self.add("2026-10-01", "not a number")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_stale_index_is_a_400_not_a_crash(self):
        self.add("2026-10-01", 100)
        response = self.client.delete("/api/rows/9?month=2026-10")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no longer exists", response.get_json()["error"])

    def test_a_missing_month_reads_as_empty_rather_than_404(self):
        payload = self.client.get("/api/months/2026-07").get_json()
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["summary"]["entries"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
