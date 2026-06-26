"""Google Ads connector.

Raw data arrives via Google Ads Data Transfer Service into BigQuery — this
connector does not call the Google Ads API for ingest. run_sync rebuilds the
campaign mart view from whatever raw data is already in BQ.
"""

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
        import connector_config_store

        cfg = connector_config_store.get_config(client_slug, "google_ads")
        bq_project_id = cfg.bq_project_id if cfg else None
        try:
            with bigquery_warehouse.route(bq_project_id=bq_project_id):
                result = bigquery_warehouse.create_google_campaign_mart_view()
            table = result.get("table") if result else None
            ok = result.get("status") == "success" if result else False
            return SyncResult(
                rows_loaded=0,
                error=None if ok else (result.get("error") if result else "Mart view rebuild returned no result"),
            )
        except Exception as exc:
            _log.warning("Google Ads sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(GoogleAdsConnector())
