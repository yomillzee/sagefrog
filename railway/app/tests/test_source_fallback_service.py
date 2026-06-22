from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.services.source_fallback_service import (  # noqa: E402
    configured_fallback_sources,
    merge_api_fallback_sources,
)


class SourceFallbackServiceTests(unittest.TestCase):
    def test_requests_fallback_for_configured_error_or_empty_source(self) -> None:
        cfg = SimpleNamespace(
            google_customer_id="111",
            linkedin_account_id="222",
            meta_account_id=None,
        )
        snapshot = {
            "date_range": {"end": "2026-06-21"},
            "daily_metrics": {"google": [], "linkedin": [{"metric_date": "2026-06-21"}]},
            "breakdowns": {},
            "errors": {"bq_mart_google": "403 Access Denied"},
        }

        self.assertEqual(configured_fallback_sources(snapshot, cfg), {"google"})

    def test_requests_fallback_when_bigquery_data_is_stale(self) -> None:
        cfg = SimpleNamespace(
            google_customer_id=None,
            linkedin_account_id="222",
            meta_account_id=None,
        )
        snapshot = {
            "date_range": {"end": "2026-06-21"},
            "daily_metrics": {"linkedin": [{"metric_date": "2026-06-10"}]},
            "breakdowns": {},
            "errors": {},
        }

        self.assertEqual(configured_fallback_sources(snapshot, cfg), {"linkedin"})

    def test_merges_api_data_and_demotes_repaired_bq_error(self) -> None:
        snapshot = {
            "daily_metrics": {"google": []},
            "platform_totals": {"google": {}},
            "breakdowns": {"google": {}},
            "data_sources": {"google": "bigquery"},
            "errors": {"bq_mart_google": "403 Access Denied"},
        }
        api_snapshot = {
            "daily_metrics": {"google": [{"metric_date": "2026-06-21", "spend": 10}]},
            "platform_totals": {"google": {"spend": 10}},
            "breakdowns": {"google": {"campaign": [{"id": "1"}]}},
            "warehouse_sync": {"google": {"days_synced": 1}},
            "errors": {},
        }

        repaired = merge_api_fallback_sources(snapshot, api_snapshot, {"google"})

        self.assertEqual(repaired, {"google"})
        self.assertEqual(snapshot["data_sources"]["google"], "api_fallback")
        self.assertEqual(snapshot["platform_totals"]["google"]["spend"], 10)
        self.assertNotIn("bq_mart_google", snapshot["errors"])
        self.assertIn("bq_mart_google", snapshot["warnings"])


if __name__ == "__main__":
    unittest.main()

