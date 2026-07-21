from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# harvest_service imports httpx + the DB/oauth modules at import time; stub the
# ones that aren't needed for the pure-logic tests so this runs without the full
# runtime dependency set (mirrors how the other service tests stub google.cloud).
for _name in ("httpx", "db", "oauth_flows", "oauth_store"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

import harvest_service  # noqa: E402


class MonthBoundsTest(unittest.TestCase):
    def test_mid_month(self):
        first, days_in_month, elapsed = harvest_service._month_bounds(date(2026, 7, 21))
        self.assertEqual(first, date(2026, 7, 1))
        self.assertEqual(days_in_month, 31)
        self.assertEqual(elapsed, 21)

    def test_february_leap_year(self):
        first, days_in_month, elapsed = harvest_service._month_bounds(date(2024, 2, 10))
        self.assertEqual(first, date(2024, 2, 1))
        self.assertEqual(days_in_month, 29)
        self.assertEqual(elapsed, 10)


class CumulativeByClientTest(unittest.TestCase):
    def _entries(self):
        return [
            {"client": {"id": 1, "name": "ILC Dover"}, "spent_date": "2026-07-02", "hours": 5},
            {"client": {"id": 1, "name": "ILC Dover"}, "spent_date": "2026-07-02", "hours": 3},
            {"client": {"id": 1, "name": "ILC Dover"}, "spent_date": "2026-07-05", "hours": 4},
            {"client": {"id": 2, "name": "EOS Worldwide"}, "spent_date": "2026-07-03", "hours": 10},
        ]

    def test_daily_hours_summed_per_client(self):
        by = harvest_service._cumulative_by_client(
            self._entries(), first=date(2026, 7, 1), days_elapsed=21
        )
        self.assertEqual(by["1"]["daily"], {2: 8.0, 5: 4.0})
        self.assertEqual(by["1"]["name"], "ILC Dover")
        self.assertEqual(by["2"]["daily"], {3: 10.0})

    def test_future_and_prior_month_entries_excluded(self):
        entries = self._entries() + [
            {"client": {"id": 1, "name": "ILC Dover"}, "spent_date": "2026-07-25", "hours": 99},
            {"client": {"id": 1, "name": "ILC Dover"}, "spent_date": "2026-06-30", "hours": 42},
        ]
        by = harvest_service._cumulative_by_client(
            entries, first=date(2026, 7, 1), days_elapsed=21
        )
        # Day 25 is after the elapsed cutoff; June 30 is before the month start.
        self.assertNotIn(25, by["1"]["daily"])
        self.assertEqual(by["1"]["daily"], {2: 8.0, 5: 4.0})

    def test_missing_client_falls_back_to_zero_bucket(self):
        entries = [{"client": {}, "spent_date": "2026-07-04", "hours": 2}]
        by = harvest_service._cumulative_by_client(
            entries, first=date(2026, 7, 1), days_elapsed=21
        )
        self.assertEqual(by["0"]["daily"], {4: 2.0})
        self.assertEqual(by["0"]["name"], "(No client)")


if __name__ == "__main__":
    unittest.main()
