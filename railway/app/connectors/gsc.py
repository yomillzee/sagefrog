"""Google Search Console connector — service-account, no user OAuth."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class GSCConnector(ConnectorHandler):
    connector_type = "gsc"
    display_name = "Search Console"
    oauth_platform = "gsc"
    default_raw_dataset = "raw_gsc"
    no_oauth = True  # uses service-account credentials

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        try:
            import gsc_sync_service
            props = gsc_sync_service.list_accessible_properties()
            # list_accessible_properties returns dicts keyed "site_url" (not "siteUrl");
            # using the wrong key collapsed every property to an empty id → the wizard
            # showed "No accounts found" even when the service account had access.
            return [
                {"id": p.get("site_url", ""), "name": p.get("site_url", "")}
                for p in props
                if p.get("site_url")
            ]
        except Exception as exc:
            _log.warning("GSC list_accounts failed: %s", exc)
            return []

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import client_dashboard_config
        import connector_config_store
        import gsc_sync_service

        cfg = connector_config_store.get_config(client_slug, "gsc")
        # source_account_id stores the site URL chosen in step 2
        site_url = cfg.source_account_id if cfg else None
        if not site_url:
            db_cfg = client_dashboard_config.get_config(client_slug)
            site_url = (db_cfg.gsc_site_url if db_cfg else None) or ""

        try:
            result = gsc_sync_service.sync_for_refresh(
                site_url=site_url or None,
                client_slug=client_slug,
            )
            ok = result.get("ok", True)
            rows = result.get("rows_synced") or result.get("rows_written") or 0
            return SyncResult(
                rows_loaded=rows,
                error=result.get("error") if not ok else None,
            )
        except Exception as exc:
            _log.warning("GSC sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(GSCConnector())
