"""Regression test: the LinkedIn creative sync must thread the client-scoped
access token through every linkedin_service call.

Connector-onboarded clients (e.g. sagefrog) hold their LinkedIn OAuth token in
the client-scoped oauth_store, not the global env. If sync_linkedin_creative_daily
doesn't forward the token, the linkedin_service calls fall back to the global
load_linkedin_env(), which raises for these clients -- the exception is swallowed
by the caller and the creative mart (fact_linkedin_ads_creative_daily) is never
built, so the Campaign Explorer shows no LinkedIn creatives.
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

# Stub google.cloud.bigquery (not installed in CI) exactly like other tests.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name, self.type_, self.value = name, type_, value

    class _FakeQueryJobConfig:
        def __init__(self, dry_run=False):
            self.dry_run = dry_run
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig
    service_account_mod.Credentials = _FakeCredentials
    cloud_mod.bigquery = bigquery_mod
    google_mod.cloud = cloud_mod
    google_mod.oauth2 = oauth2_mod
    oauth2_mod.service_account = service_account_mod
    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.service_account"] = service_account_mod

# bq_linkedin_ads_service pulls in dashboard.services.snapshot_metrics_service at
# import time, which transitively imports google_ads_service (the Google Ads SDK,
# not installed here). Stub just that one module -- we never exercise it.
if "dashboard.services.snapshot_metrics_service" not in sys.modules:
    _snap = types.ModuleType("dashboard.services.snapshot_metrics_service")
    _snap.aggregated_paid_media = lambda *a, **k: {}
    sys.modules["dashboard.services.snapshot_metrics_service"] = _snap

import bq_linkedin_ads_service  # noqa: E402


class _RecordingLinkedIn:
    """Records the access_token each API call received."""

    def __init__(self):
        self.tokens: dict[str, object] = {}

    def fetch_creative_daily_metrics(self, account_id, *, start, end, access_token=None, env=None):
        self.tokens["fetch_creative_daily_metrics"] = access_token
        return []

    def creatives_performance(self, account_id, *, date_range="LAST_30_DAYS", access_token=None, **kw):
        self.tokens["creatives_performance"] = access_token
        return {"creatives": []}

    def fetch_creatives_metadata_by_ids(self, account_id, creative_ids, *, access_token=None, env=None):
        self.tokens["fetch_creatives_metadata_by_ids"] = access_token
        return []


class _NoopWarehouse:
    import contextlib as _contextlib

    def __init__(self):
        self.ensured_creative_metadata = False

    @_contextlib.contextmanager
    def route(self, *a, **k):
        yield

    def ensure_linkedin_creative_metadata_table(self, *a, **k):
        self.ensured_creative_metadata = True
        return "test-proj.raw_linkedin_ads.creative_metadata"

    def mirror_linkedin_ad_daily_batch(self, *a, **k):
        return {"rows_upserted": 0}

    def mirror_linkedin_creative_metadata(self, *a, **k):
        return {"rows_upserted": 0}

    def mirror_linkedin_campaign_metadata(self, *a, **k):
        return {"rows_upserted": 0}

    def create_linkedin_creative_mart_view(self):
        return {"created": True}


class LinkedInCreativeSyncTokenTests(unittest.TestCase):
    def test_access_token_threaded_to_all_linkedin_calls(self) -> None:
        fake_li = _RecordingLinkedIn()
        fake_wh = _NoopWarehouse()
        sys.modules["linkedin_service"] = fake_li
        sys.modules["bigquery_warehouse"] = fake_wh
        # Backfill runs a BQ query to find missing creative ids; make it return
        # some so fetch_creatives_metadata_by_ids is exercised too.
        orig_run_query = bq_linkedin_ads_service.bigquery_service.run_query
        bq_linkedin_ads_service.bigquery_service.run_query = lambda *a, **k: [
            {"creative_id": "999"}
        ]
        try:
            with bq_linkedin_ads_service.route(bq_project_id="test-proj"):
                bq_linkedin_ads_service.sync_linkedin_creative_daily(
                    account_id="123456",
                    start=date(2026, 6, 1),
                    end=date(2026, 6, 30),
                    access_token="TOK-CLIENT-SCOPED",
                )
        finally:
            bq_linkedin_ads_service.bigquery_service.run_query = orig_run_query
            sys.modules.pop("linkedin_service", None)
            sys.modules.pop("bigquery_warehouse", None)

        self.assertEqual(fake_li.tokens.get("fetch_creative_daily_metrics"), "TOK-CLIENT-SCOPED")
        self.assertEqual(fake_li.tokens.get("creatives_performance"), "TOK-CLIENT-SCOPED")
        self.assertEqual(fake_li.tokens.get("fetch_creatives_metadata_by_ids"), "TOK-CLIENT-SCOPED")
        # The backfill must ensure creative_metadata exists before its LEFT JOIN,
        # otherwise a client with an empty 30-day creatives call (all paused)
        # never gets the table and creative metadata stays unbuilt forever.
        self.assertTrue(fake_wh.ensured_creative_metadata)


if __name__ == "__main__":
    unittest.main()
