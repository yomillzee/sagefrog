"""Google Ads connector — fetches campaign metrics via API and writes to raw_google_ads."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class GoogleAdsConnector(ConnectorHandler):
    connector_type = "google_ads"
    display_name = "Google Ads"
    oauth_platform = "google_ads"
    default_raw_dataset = "raw_google_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import google_ads_service
        import oauth_store
        refresh = oauth_store.get_refresh_token("google_ads", client_slug=client_slug)
        if not refresh:
            raise RuntimeError(f"No Google Ads token for client '{client_slug}'.")
        accounts = google_ads_service.list_accessible_customer_accounts()
        return [{"id": a.get("customer_id", ""), "name": a.get("descriptive_name", "")} for a in accounts]

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bigquery_warehouse
        import bq_google_ads_service
        import connector_config_store
        import oauth_store
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "google_ads")
        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = (cfg.raw_dataset_id if cfg else None) or self.default_raw_dataset
        customer_id = (cfg.source_account_id if cfg else None)

        if not customer_id:
            return SyncResult(rows_loaded=0, error="No Google Ads customer ID configured for this client.")

        refresh = oauth_store.get_refresh_token("google_ads", client_slug=client_slug)
        if not refresh:
            return SyncResult(rows_loaded=0, error="No Google Ads refresh token. Reconnect Google Ads in settings.")

        start, end, _ = resolve_date_range(date_range)
        try:
            with bq_google_ads_service.route(
                bq_project_id=bq_project_id,
                google_dataset_id=raw_dataset_id,
            ):
                result = bq_google_ads_service.sync_google_ads_to_bq(
                    customer_id,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    refresh_token=refresh,
                    client_key=client_slug,
                )
            with bigquery_warehouse.route(bq_project_id=bq_project_id):
                bigquery_warehouse.create_google_campaign_mart_view()
            rows = int(result.get("total_rows") or 0)
            errors = result.get("errors") or {}
            return SyncResult(
                rows_loaded=rows,
                error=str(next(iter(errors.values()))) if errors else None,
                range_start=start,
                range_end=end,
            )
        except Exception as exc:
            _log.warning("Google Ads sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(GoogleAdsConnector())
