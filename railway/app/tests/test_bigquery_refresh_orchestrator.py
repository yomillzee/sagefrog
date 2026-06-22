from __future__ import annotations

import contextlib
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.services import bigquery_refresh_orchestrator as orchestrator  # noqa: E402


class _MissingGoogleClient:
    def get_table(self, table_id):
        raise RuntimeError(f"Not found: {table_id}")

    def list_tables(self, dataset_id):
        return [SimpleNamespace(table_id="events_20260622")]


class BigQueryRefreshOrchestratorTests(unittest.TestCase):
    def test_dashboard_get_and_date_range_requests_never_run_ad_ingestion(self) -> None:
        self.assertFalse(orchestrator.should_run_ingestion("cache_miss"))
        self.assertFalse(orchestrator.should_run_ingestion("date_range"))
        self.assertTrue(orchestrator.should_run_ingestion("manual_full"))
        self.assertTrue(orchestrator.should_run_ingestion("cron"))

    def test_cron_reprocesses_30_days_and_onboarding_uses_180(self) -> None:
        today = date(2026, 6, 22)
        self.assertEqual(
            orchestrator.ingestion_window("cron", today=today),
            (date(2026, 5, 24), today),
        )
        self.assertEqual(
            orchestrator.ingestion_window("onboarding", today=today),
            (date(2025, 12, 25), today),
        )

    def test_missing_google_transfer_is_pending_not_fabricated(self) -> None:
        statuses = orchestrator.verify_native_sources(
            client=_MissingGoogleClient(),
            project_id="client-project",
            ga4_dataset_id="analytics_123",
            google_required=True,
        )
        self.assertEqual(statuses["google"]["status"], "pending_data")
        self.assertEqual(statuses["ga4"]["status"], "success")

    def test_platform_failure_does_not_block_successful_platform_storage(self) -> None:
        cfg = SimpleNamespace(
            ga4_client_key="client",
            google_customer_id=None,
            linkedin_account_id="li-1",
            meta_account_id="meta-1",
        )
        target = SimpleNamespace(
            bq_project_id="client-project",
            bq_dataset_id="analytics_123",
            credentials={"client_email": "svc@example.test"},
            credentials_env="GCP_CLIENT",
        )
        meta_sync = Mock(
            return_value={
                "campaign_rows": 10,
                "adset_rows": 8,
                "ad_rows": 6,
                "creative_rows": 4,
                "errors": {},
            }
        )
        fake_modules = {
            "client_config": SimpleNamespace(load_client_config=lambda _: cfg),
            "ga4_clients": SimpleNamespace(resolve_client_config=lambda client_key: target),
            "bigquery_service": SimpleNamespace(build_client=lambda *a, **k: object()),
            "client_bigquery_setup": SimpleNamespace(
                ensure_client_bq_resources=lambda **kwargs: {"status": "success", "ok": True},
                dataset_ids=lambda: {
                    "google": "raw_google_ads",
                    "linkedin": "raw_linkedin_ads",
                    "meta": "raw_meta_ads",
                    "mart": "marketing_marts",
                },
            ),
            "bigquery_warehouse": SimpleNamespace(
                route=lambda **kwargs: contextlib.nullcontext(),
                mirror_campaign_daily_batch=lambda *a, **k: {"rows_upserted": 0},
                create_google_campaign_mart_view=lambda: {"status": "not_configured"},
                rebuild_unified_marketing_mart=lambda: {"status": "success", "table": "mart"},
            ),
            "bq_linkedin_ads_service": SimpleNamespace(
                route=lambda **kwargs: contextlib.nullcontext(),
                sync_campaign_metadata_and_rebuild_mart=Mock()
            ),
            "bq_meta_ads_service": SimpleNamespace(
                route=lambda **kwargs: contextlib.nullcontext(),
                sync_meta_to_bq=meta_sync,
            ),
            "linkedin_service": SimpleNamespace(
                fetch_campaign_daily_metrics=Mock(side_effect=RuntimeError("LinkedIn down"))
            ),
            "dashboard_snapshots": SimpleNamespace(delete_snapshot=lambda _: True),
        }
        with patch.dict(sys.modules, fake_modules), patch.object(
            orchestrator,
            "verify_native_sources",
            return_value={
                "google": {"status": "not_configured"},
                "ga4": {"status": "success"},
                "search_console": {"status": "success"},
            },
        ):
            result = orchestrator.run_client_bigquery_refresh(
                client_slug="client", trigger="cron", today=date(2026, 6, 22)
            )

        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(result["sources"]["linkedin"]["status"], "failed")
        self.assertEqual(result["sources"]["meta"]["status"], "success")
        meta_sync.assert_called_once_with(
            "meta-1", start=date(2026, 5, 24), end=date(2026, 6, 22)
        )


if __name__ == "__main__":
    unittest.main()
