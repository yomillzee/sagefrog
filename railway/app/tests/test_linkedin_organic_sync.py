"""Orchestration tests for bq_linkedin_organic_service.run_organic_sync.

Verifies the client-scoped access token is threaded to every organic fetcher,
rows_upserted aggregates across families, and a single failing family degrades
gracefully instead of failing the whole run.
"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bq_linkedin_organic_service as sync  # noqa: E402


class _RecordingOrganic:
    def __init__(self):
        self.tokens: dict[str, object] = {}

    def fetch_posts_with_stats(self, org_id, *, start, end, access_token=None):
        self.tokens["posts"] = access_token
        return [{"post_id": "1", "impressions": 10}]

    def fetch_follower_daily(self, org_id, *, start, end, access_token=None):
        self.tokens["followers"] = access_token
        return [{"metric_date": "2026-06-01", "organic_follower_gain": 2}]

    def fetch_page_daily(self, org_id, *, start, end, access_token=None):
        self.tokens["pages"] = access_token
        return [{"metric_date": "2026-06-01", "page_views": 5}]

    def fetch_follower_demographics(self, org_id, *, access_token=None):
        self.tokens["demographics"] = access_token
        return [{"dimension": "seniority", "category": "Senior",
                 "category_urn": "urn:li:seniority:4", "total_followers": 9}]

    def fetch_engagement_daily(self, org_id, *, start, end, access_token=None):
        self.tokens["engagement"] = access_token
        return [{"metric_date": "2026-06-01", "impressions": 40}]


class _RecordingWarehouse:
    def mirror_linkedin_post_stats(self, org_id, rows):
        return {"rows_upserted": len(rows)}

    def mirror_linkedin_follower_daily(self, org_id, rows):
        return {"rows_upserted": len(rows)}

    def mirror_linkedin_page_daily(self, org_id, rows):
        return {"rows_upserted": len(rows)}

    def mirror_linkedin_follower_demographics(self, org_id, rows):
        return {"rows_upserted": len(rows)}

    def mirror_linkedin_engagement_daily(self, org_id, rows):
        return {"rows_upserted": len(rows)}

    def create_linkedin_organic_post_mart_view(self):
        return {"enabled": True, "table": "proj.marketing_marts.fact_linkedin_organic_post_stats"}


class _FailingPagesOrganic(_RecordingOrganic):
    def fetch_page_daily(self, org_id, *, start, end, access_token=None):
        raise RuntimeError("page statistics scope missing")


class RunOrganicSyncTests(unittest.TestCase):
    def _run(self, organic_mod):
        wh = _RecordingWarehouse()
        sys.modules["linkedin_organic_service"] = organic_mod
        sys.modules["bigquery_warehouse"] = wh
        try:
            return sync.run_organic_sync(
                org_id="urn:li:organization:777",
                start=date(2026, 6, 1),
                end=date(2026, 6, 30),
                access_token="TOK-CLIENT-SCOPED",
            )
        finally:
            sys.modules.pop("linkedin_organic_service", None)
            sys.modules.pop("bigquery_warehouse", None)

    def test_token_threaded_and_rows_aggregated(self) -> None:
        organic_mod = _RecordingOrganic()
        result = self._run(organic_mod)
        self.assertEqual(organic_mod.tokens["posts"], "TOK-CLIENT-SCOPED")
        self.assertEqual(organic_mod.tokens["followers"], "TOK-CLIENT-SCOPED")
        self.assertEqual(organic_mod.tokens["pages"], "TOK-CLIENT-SCOPED")
        self.assertEqual(organic_mod.tokens["demographics"], "TOK-CLIENT-SCOPED")
        self.assertEqual(organic_mod.tokens["engagement"], "TOK-CLIENT-SCOPED")
        # 1 each: post + follower + page + demographic + engagement
        self.assertEqual(result["rows_upserted"], 5)
        self.assertEqual(result["org_id"], "777")

    def test_failing_family_is_isolated(self) -> None:
        result = self._run(_FailingPagesOrganic())
        # Every other family still counted; pages recorded an error, run not aborted.
        self.assertEqual(result["rows_upserted"], 4)
        self.assertIn("error", result["pages"])
        self.assertNotIn("error", result["posts"])
        self.assertNotIn("error", result["demographics"])

    def test_missing_org_id_short_circuits(self) -> None:
        result = sync.run_organic_sync(
            org_id="", start=date(2026, 6, 1), end=date(2026, 6, 30), access_token="X"
        )
        self.assertEqual(result["rows_upserted"], 0)
        self.assertEqual(result["error"], "missing_org_id")


if __name__ == "__main__":
    unittest.main()
