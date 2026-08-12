"""SEMrush's automated cadence is a cost control, not a freshness setting.

Every client shares one SEMRUSH_API_KEY against a single monthly API-unit
balance, and one sync bills four endpoints, so both automated paths that can
spend units — the connector cron and the dashboard snapshot's SEMrush block —
have to stay on the monthly interval.
"""

from __future__ import annotations

import contextlib
import os
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from connectors import semrush as semrush_connector  # noqa: E402
from dashboard.services import bigquery_refresh_orchestrator as orchestrator  # noqa: E402


class SemrushIntervalConfigTests(unittest.TestCase):
    def test_default_interval_is_monthly(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SEMRUSH_SYNC_INTERVAL_DAYS", None)
            self.assertEqual(semrush_connector.sync_interval_days(), 30)
        self.assertEqual(semrush_connector.SEMrushConnector.min_sync_interval_days, 30)

    def test_env_var_retunes_the_interval(self) -> None:
        with patch.dict(os.environ, {"SEMRUSH_SYNC_INTERVAL_DAYS": "60"}):
            self.assertEqual(semrush_connector.sync_interval_days(), 60)

    def test_garbage_env_var_falls_back_to_default(self) -> None:
        with patch.dict(os.environ, {"SEMRUSH_SYNC_INTERVAL_DAYS": "weekly"}):
            self.assertEqual(semrush_connector.sync_interval_days(), 30)


def _fake_modules(configs, handlers):
    cfg = SimpleNamespace(
        ga4_client_key="client",
        google_customer_id=None,
        linkedin_account_id=None,
        meta_account_id=None,
    )
    target = SimpleNamespace(
        bq_project_id="client-project",
        bq_dataset_id="analytics_123",
        credentials={"client_email": "svc@example.test"},
        credentials_env="GCP_CLIENT",
    )
    return {
        "client_config": SimpleNamespace(load_client_config=lambda _: cfg),
        "ga4_clients": SimpleNamespace(resolve_client_config=lambda client_key: target),
        # Stubbed so the gate is exercised without a live Postgres — the
        # orchestrator reads client_dashboard_config before it reaches the
        # connector loop.
        "client_dashboard_config": SimpleNamespace(
            get_config=lambda _slug: SimpleNamespace(gcp_project_id="client-project"),
        ),
        "client_bigquery_setup": SimpleNamespace(
            ensure_client_bq_resources=lambda **kwargs: {"status": "success", "ok": True},
            ensure_client_datasets=lambda **kwargs: {"status": "success", "ok": True},
            dataset_ids=lambda: {
                "google": "raw_google_ads", "linkedin": "raw_linkedin_ads",
                "meta": "raw_meta_ads", "mart": "marketing_marts",
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
        "connectors.base": SimpleNamespace(get=lambda ctype: handlers.get(ctype)),
        "dashboard_snapshots": SimpleNamespace(delete_snapshot=lambda _: True),
    }


class SemrushSnapshotFetchTests(unittest.TestCase):
    """refresh_bq_client's own SEMrush snapshot fetch is a second live hit on
    the same balance as the connector sync, so it has to follow the same
    monthly interval — and never fire on a read-only cache-miss GET."""

    def _refresh(self, *, cached_age_hours: float | None, trigger: str, fetch_error: str | None = None):
        import dashboard.services.refresh_service as rs
        from dashboard.services import bigquery_refresh_orchestrator as orch

        cached_smr: dict = {"domain": "example.com", "overview": {"organic_traffic": 10}}
        if cached_age_hours is not None:
            cached_smr["fetched_at"] = (
                datetime.now(tz=UTC) - timedelta(hours=cached_age_hours)
            ).isoformat()
        else:
            cached_smr = {}

        build_snapshot = Mock(
            side_effect=RuntimeError(fetch_error) if fetch_error else None,
            return_value={
                "domain": "example.com",
                "overview": {"organic_traffic": 99},
                "fetched_at": datetime.now(tz=UTC).isoformat(),
            },
        )
        saved: dict = {}

        fake_modules = {
            "bq_gsc_service": SimpleNamespace(build_gsc_snapshot=lambda **k: {}),
            "bq_linkedin_ads_service": SimpleNamespace(),
            "bq_meta_ads_service": SimpleNamespace(),
            "bq_mart_service": SimpleNamespace(build_snapshot=lambda **k: {}),
            "client_dashboard_config": SimpleNamespace(get_config=lambda _slug: SimpleNamespace(
                gsc_site_url="", semrush_domain="example.com", gcp_project_id="client-project",
            )),
            "gsc_sync_service": SimpleNamespace(),
            "semrush_service": SimpleNamespace(build_semrush_snapshot=build_snapshot),
            "ga4_warehouse_service": SimpleNamespace(
                fetch_organic_daily_metrics=lambda **k: [],
            ),
        }
        cfg = SimpleNamespace(
            label="Client", client_key="client", ga4_client_key="client",
            google_customer_id=None, linkedin_account_id=None, meta_account_id=None,
        )
        with patch.dict(sys.modules, fake_modules), \
                patch.object(rs, "client_config", SimpleNamespace(load_client_config=lambda _: cfg)), \
                patch.object(rs, "dashboard_snapshots", SimpleNamespace(
                    get_snapshot=lambda _slug: {"semrush": cached_smr},
                    save_snapshot=lambda slug, snap, **k: saved.update(snap),
                )), \
                patch.object(rs, "ga4_attribution_service", SimpleNamespace(
                    fetch_attribution_for_dashboard=lambda **k: {})), \
                patch.object(rs, "ga4_paid_entity_service", SimpleNamespace(
                    fetch_paid_entity_report=lambda **k: {})), \
                patch.object(rs, "ga4_page_service", SimpleNamespace(
                    fetch_pages_for_all_presets=lambda **k: {})), \
                patch.object(orch, "run_client_bigquery_refresh", lambda **k: {"sources": {}}):
            snapshot = rs.refresh_bq_client("client", sync_trigger=trigger)
        return snapshot, build_snapshot

    def test_cache_miss_page_load_never_spends_units(self) -> None:
        """Even with no cached copy at all, a read-only trigger must not fetch."""
        snapshot, build = self._refresh(cached_age_hours=None, trigger="cache_miss")
        build.assert_not_called()

    def test_stale_cache_on_a_page_load_is_served_not_refetched(self) -> None:
        snapshot, build = self._refresh(cached_age_hours=24 * 90, trigger="cache_miss")
        build.assert_not_called()
        self.assertEqual(snapshot["semrush"]["overview"]["organic_traffic"], 10)

    def test_cron_reuses_the_cached_snapshot_inside_the_month(self) -> None:
        snapshot, build = self._refresh(cached_age_hours=24 * 8, trigger="cron")
        build.assert_not_called()
        self.assertEqual(snapshot["semrush"]["overview"]["organic_traffic"], 10)

    def test_cron_refetches_once_the_month_is_up(self) -> None:
        snapshot, build = self._refresh(cached_age_hours=24 * 31, trigger="cron")
        build.assert_called_once()
        self.assertEqual(snapshot["semrush"]["overview"]["organic_traffic"], 99)

    def test_failed_fetch_keeps_the_last_snapshot(self) -> None:
        """A failed fetch must not blank the panel until the next window a
        month later — carry the cached copy and record the error."""
        snapshot, build = self._refresh(
            cached_age_hours=24 * 31, trigger="cron", fetch_error="SEMrush 500"
        )
        build.assert_called_once()
        self.assertEqual(snapshot["semrush"]["overview"]["organic_traffic"], 10)
        self.assertIn("SEMrush 500", snapshot["errors"]["semrush"])


class SemrushCronCadenceTests(unittest.TestCase):
    """The orchestrator's generic interval gate, exercised through SEMrush."""

    def _run(self, *, last_success_at, trigger):
        handler = SimpleNamespace(
            min_sync_interval_days=30,
            run_sync=Mock(return_value=SimpleNamespace(
                ok=True, rows_loaded=5, error=None,
                range_start=date(2026, 5, 24), range_end=date(2026, 6, 22),
            )),
        )
        configs = [SimpleNamespace(
            connector_type="semrush", status="connected", id=1,
            sync_enabled=True, last_success_at=last_success_at,
        )]
        with patch.dict(sys.modules, _fake_modules(configs, {"semrush": handler})):
            result = orchestrator.run_client_bigquery_refresh(
                client_slug="client", trigger=trigger, today=date(2026, 6, 22)
            )
        return result["sources"]["semrush"], handler.run_sync

    def test_cron_skips_semrush_until_a_month_has_passed(self) -> None:
        source, run_sync = self._run(
            last_success_at=datetime.now(tz=UTC) - timedelta(days=8), trigger="cron"
        )
        self.assertEqual(source["status"], "not_due")
        self.assertEqual(source["interval_days"], 30)
        run_sync.assert_not_called()

    def test_cron_runs_semrush_once_the_month_is_up(self) -> None:
        source, run_sync = self._run(
            last_success_at=datetime.now(tz=UTC) - timedelta(days=31), trigger="cron"
        )
        self.assertEqual(source["status"], "success")
        run_sync.assert_called_once()

    def test_first_ever_sync_is_not_held_back(self) -> None:
        source, run_sync = self._run(last_success_at=None, trigger="cron")
        self.assertEqual(source["status"], "success")
        run_sync.assert_called_once()

    def test_manual_refresh_ignores_the_interval(self) -> None:
        source, run_sync = self._run(
            last_success_at=datetime.now(tz=UTC) - timedelta(days=1), trigger="manual_full"
        )
        self.assertEqual(source["status"], "success")
        run_sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
