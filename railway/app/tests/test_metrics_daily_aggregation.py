"""Regression tests for metrics_daily staging aggregation.

Guards the fix for the BigQuery MERGE error "UPDATE/MERGE must match at most
one source row for each target row" -- triggered when per-campaign LinkedIn
rows (multiple campaigns on the same day) were mirrored into the account+day
metrics_daily fact without being summed first.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bigquery_warehouse as w  # noqa: E402


class AggregateDailyMetricsTests(unittest.TestCase):
    def test_multiple_campaigns_same_day_collapse_to_one_row(self):
        rows = [
            {"campaign_id": "A", "metric_date": "2026-07-01", "spend": 10, "clicks": 5,
             "impressions": 100, "conversions": 1, "conversion_value": 20},
            {"campaign_id": "B", "metric_date": "2026-07-01", "spend": 5, "clicks": 2,
             "impressions": 50, "conversions": 0, "conversion_value": 0},
        ]
        out = w._aggregate_daily_metrics("linkedin", "999", rows, "T")
        self.assertEqual(len(out), 1)  # one row per (source, account, day) -> MERGE-safe
        rec = out[0]
        self.assertEqual(rec["metric_date"], "2026-07-01")
        self.assertEqual(rec["spend"], 15)
        self.assertEqual(rec["clicks"], 7)
        self.assertEqual(rec["impressions"], 150)
        self.assertEqual(rec["conversions"], 1)
        self.assertEqual(rec["conversion_value"], 20)
        self.assertEqual((rec["source"], rec["account_id"]), ("linkedin", "999"))

    def test_distinct_days_kept_separate(self):
        rows = [
            {"metric_date": "2026-07-01", "spend": 3},
            {"metric_date": "2026-07-02", "spend": 4},
        ]
        out = {r["metric_date"]: r for r in w._aggregate_daily_metrics("linkedin", "1", rows, "T")}
        self.assertEqual(set(out), {"2026-07-01", "2026-07-02"})
        self.assertEqual(out["2026-07-02"]["spend"], 4)

    def test_account_daily_input_is_unchanged(self):
        # A caller already at account+day grain (one row per day) must not be
        # altered -- summing a single row per key is a no-op, no double counting.
        rows = [{"metric_date": "2026-07-01", "spend": 42, "clicks": 9}]
        out = w._aggregate_daily_metrics("google", "acc", rows, "T")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["spend"], 42)
        self.assertEqual(out[0]["clicks"], 9)

    def test_field_aliases_and_missing_date(self):
        rows = [
            {"metricDate": "2026-07-03", "spend": 2, "conversionValue": 7},
            {"spend": 999},  # no date -> skipped entirely
        ]
        out = w._aggregate_daily_metrics("linkedin", "1", rows, "T")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["metric_date"], "2026-07-03")
        self.assertEqual(out[0]["conversion_value"], 7)


if __name__ == "__main__":
    unittest.main()
