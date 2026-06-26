"""GA4 connector — uses OAuth (analytics.readonly) to pull Reporting API data into BQ."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class GA4Connector(ConnectorHandler):
    connector_type = "ga4"
    display_name = "Google Analytics 4"
    oauth_platform = "google_analytics"
    default_raw_dataset = "raw_ga4"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import ga4_reporting_service
        import oauth_store
        refresh_token = oauth_store.get_refresh_token("google_analytics", client_slug=client_slug)
        if not refresh_token:
            raise RuntimeError(f"No GA4 refresh token for '{client_slug}'. Reconnect via the wizard.")
        return ga4_reporting_service.list_properties(refresh_token)

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_5_DAYS") -> SyncResult:
        import bq_ga4_service
        import connector_config_store
        import oauth_store
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "ga4")
        property_id = cfg.source_account_id if cfg else None
        if not property_id:
            return SyncResult(rows_loaded=0, error="No GA4 property configured.")

        refresh_token = oauth_store.get_refresh_token("google_analytics", client_slug=client_slug)
        if not refresh_token:
            return SyncResult(rows_loaded=0, error="No GA4 OAuth token — reconnect via the connector wizard.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = cfg.raw_dataset_id if cfg else None
        start, end, _ = resolve_date_range(date_range)
        _log.info("GA4 sync [%s] property=%s range=%s→%s", client_slug, property_id, start, end)

        try:
            with bq_ga4_service.route(
                bq_project_id=bq_project_id,
                ga4_dataset_id=raw_dataset_id,
            ):
                result = bq_ga4_service.sync_ga4_to_bq(
                    property_id,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    refresh_token=refresh_token,
                    client_key=client_slug,
                )
            total = result.get("total_rows") or 0
            errors = result.get("errors") or {}
            error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None
            _log.info(
                "GA4 sync [%s] rows: %s",
                client_slug, result.get("counts", {}),
            )
            return SyncResult(rows_loaded=total, error=error_msg)
        except Exception as exc:
            _log.warning("GA4 sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(GA4Connector())
