"""GA4 connector — uses service-account credentials, no user OAuth."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class GA4Connector(ConnectorHandler):
    connector_type = "ga4"
    display_name = "Google Analytics 4"
    oauth_platform = "google_ads"  # reuses Google OAuth client
    default_raw_dataset = "raw_ga4"
    no_oauth = True  # GA4 uses service-account JSON, not user OAuth

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import ga4_clients
        rows = ga4_clients.list_clients_public()
        return [
            {
                "id": r.get("client_key") or r.get("account_id") or "",
                "name": r.get("label") or r.get("client_key") or r.get("account_id") or "",
            }
            for r in rows
            if r.get("client_key") or r.get("account_id")
        ]

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import connector_config_store
        import ga4_warehouse_service

        cfg = connector_config_store.get_config(client_slug, "ga4")
        # source_account_id stores the ga4 client_key chosen in step 2
        client_key = (cfg.source_account_id if cfg else None) or client_slug
        try:
            result = ga4_warehouse_service.sync_to_warehouse(
                date_range=date_range,
                client_key=client_key,
            )
            return SyncResult(rows_loaded=result.get("days_synced") or 0)
        except Exception as exc:
            _log.warning("GA4 sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(GA4Connector())
