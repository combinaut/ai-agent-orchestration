import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from test_support import IsolatedTestCase

SCRIPT = Path(__file__).resolve().parents[2] / "orch-usage"
_loader = SourceFileLoader("orch_usage", str(SCRIPT))
orch_usage = module_from_spec(spec_from_loader("orch_usage", _loader))
_loader.exec_module(orch_usage)

NOW = 1_900_000_000
HEADER = (
    "closed_at\towner\tworkspace_label\tagent_name\tpane\tsession_id\tmodel\tcost_usd\n"
)


class WeeklyTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def usage(self, name, data, age_s):
        path = self.dir / f"{name}.usage"
        path.write_text(json.dumps(data))
        os.utime(path, (NOW - age_s, NOW - age_s))

    def test_newest_file_by_mtime_wins(self):
        self.usage("old", {"seven_day_pct": 10, "seven_day_resets_at": NOW + 3600}, 600)
        self.usage("new", {"seven_day_pct": 55, "seven_day_resets_at": NOW + 3600}, 60)
        line = orch_usage.weekly(self.dir, NOW)
        self.assertTrue(line.startswith("weekly: 55% resets "), line)
        self.assertTrue(line.endswith("(reading 1m old)"), line)

    def test_reading_older_than_six_hours_is_unknown(self):
        self.usage(
            "a", {"seven_day_pct": 55, "seven_day_resets_at": NOW + 3600}, 6 * 3600 + 1
        )
        self.assertEqual(orch_usage.weekly(self.dir, NOW), "weekly: unknown")

    def test_no_files_is_unknown(self):
        self.assertEqual(orch_usage.weekly(self.dir, NOW), "weekly: unknown")

    def test_passed_reset_is_unknown_and_names_the_time(self):
        self.usage("a", {"seven_day_pct": 90, "seven_day_resets_at": NOW - 5}, 60)
        line = orch_usage.weekly(self.dir, NOW)
        self.assertTrue(line.startswith("weekly: unknown (window reset at "), line)
        self.assertNotIn("90", line)

    def test_missing_pct_is_unknown_never_zero(self):
        self.usage("a", {"model": "m", "cost_usd": 1}, 60)
        line = orch_usage.weekly(self.dir, NOW)
        self.assertEqual(line, "weekly: unknown")
        self.assertNotIn("0%", line)


class LedgerTests(IsolatedTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "usage-ledger.tsv"

    def write(self, *rows):
        self.path.write_text(HEADER + "".join("\t".join(r) + "\n" for r in rows))

    def row(self, label, session, cost):
        return (
            "2026-10-24T10:00:00+02:00",
            "wA",
            label,
            "n",
            "wW:p1",
            session,
            "m",
            cost,
        )

    def test_resumed_session_counts_once_with_its_last_line(self):
        self.write(self.row("alpha", "s1", "1.00"), self.row("alpha", "s1", "2.50"))
        self.assertEqual(orch_usage.ledger_lines(self.path), ["alpha: 1 session $2.50"])

    def test_labels_sum_and_sort_highest_first(self):
        self.write(
            self.row("small", "s1", "0.50"),
            self.row("big", "s2", "3.00"),
            self.row("big", "s3", "4.00"),
            self.row("mid", "s4", "5.00"),
        )
        self.assertEqual(
            orch_usage.ledger_lines(self.path),
            ["big: 2 sessions $7.00", "mid: 1 session $5.00", "small: 1 session $0.50"],
        )

    def test_unknown_cost_is_counted_not_added(self):
        self.write(self.row("alpha", "s1", ""), self.row("alpha", "s2", "1.00"))
        self.assertEqual(
            orch_usage.ledger_lines(self.path),
            ["alpha: 2 sessions $1.00 (1 without cost)"],
        )

    def test_missing_ledger(self):
        self.assertEqual(orch_usage.ledger_lines(self.path), ["ledger: none yet"])


if __name__ == "__main__":
    unittest.main()
