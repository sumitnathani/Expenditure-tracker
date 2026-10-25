"""Tests for the budget arithmetic, gap filling and file rewriting.

Run with:  python -m unittest -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tracker
from tracker import TrackerError


class TrackerTestCase(unittest.TestCase):
    """Every test gets its own empty data/ and config/ directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (tracker.DATA_DIR, tracker.CONFIG_DIR, tracker.GENRE_FILE)
        tracker.DATA_DIR = root / "data"
        tracker.CONFIG_DIR = root / "config"
        tracker.GENRE_FILE = tracker.CONFIG_DIR / "genres.json"

    def tearDown(self):
        tracker.DATA_DIR, tracker.CONFIG_DIR, tracker.GENRE_FILE = self._saved
        self._tmp.cleanup()

    # -- helpers ----------------------------------------------------------

    def rows(self, key="2026-10"):
        return tracker.load_month(key)

    def brief(self, key="2026-10"):
        """(date, amount, deviation) for every row, for readable assertions."""
        return [(r["date"], r["amount"], r["deviation"]) for r in self.rows(key)]

    def csv_text(self, key="2026-10"):
        return tracker.month_path(key).read_text(encoding="utf-8")

    def add(self, date, amount, item="thing", genre="Food"):
        return tracker.add_entry(date, item, genre, amount)


class TestSpecExamples(TrackerTestCase):
    """The two worked examples from the brief, exactly as written."""

    def test_overspend_then_break_even(self):
        # Day 1: spend 400 -> deviation -200.
        self.add("2026-10-01", 400)
        self.assertEqual(self.rows()[-1]["deviation"], -200)

        # Day 2: spend nothing. Logging day 3 is what makes day 2 exist.
        self.add("2026-10-03", 100)
        by_date = {r["date"]: r for r in self.rows()}

        # Day 2's allowance (200) cancels day 1's -200.
        self.assertEqual(by_date["2026-10-02"]["deviation"], 0)
        self.assertEqual(by_date["2026-10-02"]["amount"], 0)

        # 3 days x 200 = 600 allowance, 500 spent.
        self.assertEqual(by_date["2026-10-03"]["deviation"], 100)
        self.assertEqual(by_date["2026-10-03"]["total"], 500)

    def test_underspend_accumulates(self):
        self.add("2026-10-01", 60)
        self.assertEqual(self.rows()[-1]["deviation"], 140)   # 200 - 60

        self.add("2026-10-02", 40)
        self.assertEqual(self.rows()[-1]["deviation"], 300)   # 140 + 200 - 40

    def test_genre_never_affects_the_math(self):
        self.add("2026-10-01", 60, genre="Rent")
        self.add("2026-10-02", 40, genre="Fun")
        self.assertEqual(self.rows()[-1]["deviation"], 300)


class TestGapFilling(TrackerTestCase):
    def test_fills_the_days_between_two_entries(self):
        self.add("2026-10-03", 100)
        self.add("2026-10-10", 50)
        self.assertEqual(
            [r["date"] for r in self.rows()],
            [f"2026-10-{d:02d}" for d in range(3, 11)],
        )

    def test_never_fills_before_the_first_entry(self):
        self.add("2026-10-03", 100)
        self.assertEqual([r["date"] for r in self.rows()], ["2026-10-03"])

    def test_never_fills_after_the_last_entry(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-03", 100)
        self.assertEqual(self.rows()[-1]["date"], "2026-10-03")

    def test_placeholder_rows_are_blank_not_placeholders_text(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-04", 100)
        for row in self.rows():
            if row["date"] in {"2026-10-02", "2026-10-03"}:
                self.assertEqual(row["item"], "")
                self.assertEqual(row["genre"], "")
                self.assertEqual(row["amount"], 0)
                self.assertTrue(tracker.is_placeholder(row))

    def test_out_of_order_entry_backfills_the_gap_it_creates(self):
        self.add("2026-10-10", 100)
        self.add("2026-10-05", 100)          # logged days late, out of order
        self.assertEqual(
            [r["date"] for r in self.rows()],
            [f"2026-10-{d:02d}" for d in range(5, 11)],
        )

    def test_logging_on_a_rest_day_fills_that_row_in(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-05", 100)
        self.assertEqual(len(self.rows()), 5)

        self.add("2026-10-03", 70, item="Coffee")
        rows = self.rows()
        self.assertEqual(len(rows), 5)                     # replaced, not appended
        self.assertEqual(rows[2]["date"], "2026-10-03")
        self.assertEqual(rows[2]["item"], "Coffee")
        self.assertEqual(rows[2]["amount"], 70)

    def test_gap_filling_ignores_todays_real_date(self):
        # Far-future dates behave exactly like near ones: only the neighbouring
        # logged dates matter.
        self.add("2027-03-04", 100)
        self.add("2027-03-07", 100)
        self.assertEqual(
            [r["date"] for r in self.rows("2027-03")],
            ["2027-03-04", "2027-03-05", "2027-03-06", "2027-03-07"],
        )

    def test_gap_filling_never_crosses_into_another_month_file(self):
        self.add("2026-10-31", 100)
        self.add("2026-11-03", 100)

        self.assertEqual([r["date"] for r in self.rows("2026-10")], ["2026-10-31"])
        # November starts fresh on its own first entry: no Nov 1 or Nov 2 rows,
        # and nothing appended to October either.
        self.assertEqual([r["date"] for r in self.rows("2026-11")], ["2026-11-03"])

    def test_absurd_gap_is_rejected_rather_than_written(self):
        # Can only arise from a corrupt file, since a real month file spans a
        # month at most -- but it should fail loudly rather than write 85k rows.
        with self.assertRaises(TrackerError):
            tracker._placeholders("2026-10-01", "2260-10-01")


class TestRecalculation(TrackerTestCase):
    def test_stored_deviation_and_total_are_never_trusted(self):
        tracker.DATA_DIR.mkdir(parents=True)
        tracker.month_path("2026-10").write_text(
            "date,item,genre,amount,deviation,total\n"
            "2026-10-01,Chai,Food,60,99999,-42\n"
            "2026-10-02,Bus,Transport,40,99999,-42\n",
            encoding="utf-8",
        )
        rows = self.rows()
        self.assertEqual(rows[0]["deviation"], 140)
        self.assertEqual(rows[0]["total"], 60)
        self.assertEqual(rows[1]["deviation"], 300)
        self.assertEqual(rows[1]["total"], 100)

    def test_reading_repairs_a_hand_edited_file_on_disk(self):
        tracker.DATA_DIR.mkdir(parents=True)
        path = tracker.month_path("2026-10")
        path.write_text(
            "date,item,genre,amount,deviation,total\n"
            "2026-10-01,Chai,Food,60,99999,-42\n",
            encoding="utf-8",
        )
        self.rows()
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "date,item,genre,amount,deviation,total\n"
            "2026-10-01,Chai,Food,60,140,60\n",
        )

    def test_rows_are_sorted_chronologically_before_calculating(self):
        self.add("2026-10-20", 100)
        self.add("2026-10-02", 100)
        self.add("2026-10-11", 100)
        dates = [r["date"] for r in self.rows()]
        self.assertEqual(dates, sorted(dates))
        # And the file on disk is sorted too, not just the API response.
        self.assertEqual(
            [line.split(",")[0] for line in self.csv_text().strip().splitlines()[1:]],
            dates,
        )

    def test_deviation_counts_calendar_days_not_logged_days(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-03", 100)
        rows = {r["date"]: r for r in self.rows()}

        # Oct 2 was never logged, but its 200 still accrued.
        self.assertEqual(rows["2026-10-03"]["deviation"], 600 - 200)

    def test_two_entries_on_the_same_day_share_one_days_allowance(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-01", 50, item="second")
        rows = self.rows()
        self.assertEqual(rows[0]["deviation"], 100)    # 200 - 100
        self.assertEqual(rows[1]["deviation"], 50)     # still one day: 200 - 150


class TestEditingAndDeleting(TrackerTestCase):
    def test_edit_recalculates_the_entire_file(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-02", 100)
        self.add("2026-10-03", 100)

        tracker.update_entry("2026-10", 0, "2026-10-01", "big", "Food", 500)
        rows = self.rows()
        self.assertEqual([r["deviation"] for r in rows], [-300, -200, -100])
        self.assertEqual([r["total"] for r in rows], [500, 600, 700])

    def test_edit_can_move_a_row_to_a_different_date(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-02", 100)
        tracker.update_entry("2026-10", 0, "2026-10-02", "moved", "Food", 100)
        rows = self.rows()
        self.assertEqual([r["date"] for r in rows], ["2026-10-02", "2026-10-02"])
        self.assertEqual(rows[-1]["deviation"], 0)     # one day, 200 spent

    def test_edit_refuses_a_date_in_another_month(self):
        self.add("2026-10-01", 100)
        with self.assertRaises(TrackerError):
            tracker.update_entry("2026-10", 0, "2026-11-01", "x", "Food", 100)

    def test_delete_recalculates_the_entire_file(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-02", 300)
        self.add("2026-10-03", 100)

        tracker.delete_entry("2026-10", 1)
        rows = self.rows()
        self.assertEqual([r["amount"] for r in rows], [100, 100])
        self.assertEqual([r["total"] for r in rows], [100, 200])
        # Oct 2's 300 is gone, but its 200 of allowance still accrued, so the
        # month is 600 allowed - 200 spent.
        self.assertEqual([r["deviation"] for r in rows], [100, 400])

    def test_deleting_a_rest_day_does_not_lose_its_allowance(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-03", 100)
        before = self.rows()[-1]["deviation"]

        tracker.delete_entry("2026-10", 1)             # drop the Oct 2 rest row
        after = self.rows()
        self.assertEqual([r["date"] for r in after], ["2026-10-01", "2026-10-03"])
        self.assertEqual(after[-1]["deviation"], before)

    def test_a_deleted_rest_day_is_not_resurrected_by_the_next_entry(self):
        self.add("2026-10-01", 100)
        self.add("2026-10-03", 100)
        tracker.delete_entry("2026-10", 1)             # delete Oct 2

        self.add("2026-10-03", 10, item="later")
        self.assertNotIn("2026-10-02", [r["date"] for r in self.rows()])

    def test_deleting_the_last_row_leaves_an_empty_month(self):
        self.add("2026-10-01", 100)
        tracker.delete_entry("2026-10", 0)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.csv_text(), "date,item,genre,amount,deviation,total\n")

    def test_stale_row_index_is_an_error_not_a_wrong_row_edit(self):
        self.add("2026-10-01", 100)
        with self.assertRaises(TrackerError):
            tracker.delete_entry("2026-10", 7)
        with self.assertRaises(TrackerError):
            tracker.update_entry("2026-10", 7, "2026-10-01", "x", "Food", 10)

    def test_served_index_matches_the_order_mutations_use(self):
        for day in ("2026-10-09", "2026-10-02", "2026-10-05"):
            self.add(day, 100)
        served = [r["date"] for r in self.rows()]
        stored = [r["date"] for r in tracker.read_rows(tracker.month_path("2026-10"))]
        self.assertEqual(served, stored)


class TestMonths(TrackerTestCase):
    def test_file_is_named_for_the_month(self):
        self.add("2026-10-01", 100)
        self.assertEqual(tracker.month_path("2026-10").name, "october_2026.csv")

    def test_each_month_starts_fresh(self):
        self.add("2026-10-30", 100)
        self.add("2026-11-02", 100)

        october = self.rows("2026-10")
        november = self.rows("2026-11")
        self.assertEqual([r["date"] for r in october], ["2026-10-30"])
        self.assertEqual([r["date"] for r in november], ["2026-11-02"])
        self.assertEqual(november[0]["deviation"], 100)   # its own day 1

    def test_listing_months_newest_first(self):
        self.add("2026-10-01", 100)
        self.add("2026-12-01", 100)
        self.add("2026-11-01", 100)
        self.assertEqual(tracker.list_months(), ["2026-12", "2026-11", "2026-10"])

    def test_absent_month_reads_as_empty(self):
        self.assertEqual(self.rows("2026-07"), [])


class TestValidation(TrackerTestCase):
    def test_rejects_bad_input(self):
        with self.assertRaises(TrackerError):
            self.add("not-a-date", 100)
        with self.assertRaises(TrackerError):
            self.add("2026-10-01", "abc")
        with self.assertRaises(TrackerError):
            self.add("2026-10-01", -5)
        with self.assertRaises(TrackerError):
            self.add("2026-10-01", 100, item="   ")
        with self.assertRaises(TrackerError):
            self.add("2026-10-01", 0)

    def test_accepts_the_date_formats_a_hand_edited_file_might_use(self):
        self.assertEqual(tracker.parse_date("2026-10-05").isoformat(), "2026-10-05")
        self.assertEqual(tracker.parse_date("05/10/2026").isoformat(), "2026-10-05")
        self.assertEqual(tracker.parse_date("05-10-2026").isoformat(), "2026-10-05")

    def test_amounts_round_to_paise(self):
        self.add("2026-10-01", "60.555")
        self.assertEqual(self.rows()[0]["amount"], 60.56)

    def test_whole_amounts_are_written_without_a_decimal_point(self):
        self.add("2026-10-01", 400)
        self.assertIn("2026-10-01,thing,Food,400,-200,400", self.csv_text())


class TestGenres(TrackerTestCase):
    def test_starts_with_the_predefined_list(self):
        self.assertEqual(tracker.load_genres(), tracker.DEFAULT_GENRES)

    def test_a_new_genre_is_remembered_for_next_time(self):
        self.add("2026-10-01", 100, genre="Chai")
        self.assertIn("Chai", tracker.load_genres())

        # And it survives into a fresh read, i.e. it is really on disk.
        self.assertIn("Chai", tracker.load_genres())
        self.assertIn("Chai", tracker.GENRE_FILE.read_text(encoding="utf-8"))

    def test_a_new_genre_is_kept_out_of_the_csv_columns(self):
        self.add("2026-10-01", 100, genre="Chai")
        header = self.csv_text().splitlines()[0]
        self.assertEqual(header, "date,item,genre,amount,deviation,total")

    def test_matching_is_case_insensitive(self):
        self.add("2026-10-01", 100, genre="food")
        self.assertIn("Food", tracker.load_genres())
        self.assertNotIn("food", tracker.load_genres())
        self.assertEqual(self.rows()[0]["genre"], "Food")

    def test_blank_genre_is_allowed_and_not_saved(self):
        self.add("2026-10-01", 100, genre="")
        self.assertEqual(self.rows()[0]["genre"], "")
        self.assertEqual(tracker.load_genres(), tracker.DEFAULT_GENRES)


class TestSummary(TrackerTestCase):
    def test_reports_the_months_standing(self):
        self.add("2026-10-01", 400, genre="Rent")
        self.add("2026-10-03", 100, genre="Food")
        summary = tracker.summarize(self.rows())

        self.assertEqual(summary["spent"], 500)
        self.assertEqual(summary["allowance"], 600)
        self.assertEqual(summary["deviation"], 100)
        self.assertEqual(summary["days_tracked"], 3)
        self.assertEqual(summary["entries"], 2)          # the rest day is not an entry
        self.assertEqual(summary["avg_per_day"], 166.67)
        self.assertEqual(
            summary["by_genre"],
            [{"genre": "Rent", "amount": 400}, {"genre": "Food", "amount": 100}],
        )

    def test_projection_only_covers_days_after_the_last_entry(self):
        self.add("2026-10-01", 400)
        rows = self.rows()

        self.assertIsNone(tracker.summarize(rows, today=tracker.Date(2026, 10, 1))["projection"])
        projection = tracker.summarize(rows, today=tracker.Date(2026, 10, 2))["projection"]
        self.assertEqual(projection["deviation"], 0)     # the brief's Day 2 = 0
        self.assertEqual(projection["days"], 2)

    def test_empty_month_summarises_to_zeroes(self):
        summary = tracker.summarize([])
        self.assertEqual(summary["spent"], 0)
        self.assertEqual(summary["entries"], 0)
        self.assertIsNone(summary["first_date"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
