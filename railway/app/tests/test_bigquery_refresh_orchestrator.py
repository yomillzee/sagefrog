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

    def test_connector_failure_does_not_block_other_connectors(self) -> None:
        """One connector failing leaves the others (and the unified mart) intact.

        The refresh now drives each connector's run_sync (the single sync path)
        rather than calling per-source bq services directly.
        """
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

        def _sync_result(ok: bool, rows: int = 0, error: str | None = None):
            return SimpleNamespace(
                ok=ok, rows_loaded=rows, error=error,
                range_start=date(2026, 5, 24), range_end=date(2026, 6, 22),
            )

        meta_handler = SimpleNamespace(run_sync=Mock(return_value=_sync_result(True, rows=28)))
        linkedin_handler = SimpleNamespace(
            run_sync=Mock(return_value=_sync_result(False, error="LinkedIn down"))
        )
        fake_handlers = {"meta_ads": meta_handler, "linkedin_ads": linkedin_handler}

        configs = [
            SimpleNamespace(connector_type="meta_ads", status="connected", id=1),
            SimpleNamespace(connector_type="linkedin_ads", status="connected", id=2),
        ]

        fake_modules = {
            "client_config": SimpleNamespace(load_client_config=lambda _: cfg),
            "ga4_clients": SimpleNamespace(resolve_client_config=lambda client_key: target),
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
                rebuild_unified_marketing_mart=lambda *a, **k: {"status": "success", "table": "mart"},
            ),
            "connector_config_store": SimpleNamespace(
                list_configs=lambda _slug: configs,
                start_sync_run=lambda *a, **k: 1,
                finish_sync_run=lambda *a, **k: None,
                update_sync_timestamps=lambda *a, **k: None,
            ),
            "connectors": SimpleNamespace(),
            "connectors.base": SimpleNamespace(get=lambda ctype: fake_handlers.get(ctype)),
            "dashboard_snapshots": SimpleNamespace(delete_snapshot=lambda _: True),
        }
        with patch.dict(sys.modules, fake_modules):
            result = orchestrator.run_client_bigquery_refresh(
                client_slug="client", trigger="cron", today=date(2026, 6, 22)
            )

        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(result["sources"]["linkedin_ads"]["status"], "failed")
        self.assertEqual(result["sources"]["meta_ads"]["status"], "success")
        self.assertEqual(result["sources"]["meta_ads"]["data_through"], "2026-06-22")
        # Unconfigured connectors are reported, not synced.
        self.assertEqual(result["sources"]["google_ads"]["status"], "not_configured")
        self.assertEqual(result["sources"]["ga4"]["status"], "not_configured")
        # The unified mart still rebuilds despite the LinkedIn failure.
        self.assertEqual(result["transformations"]["unified_mart"]["status"], "success")
        meta_handler.run_sync.assert_called_once_with(
            client_slug="client", date_range="LAST_30_DAYS"
        )


if __name__ == "__main__":
    unittest.main()
