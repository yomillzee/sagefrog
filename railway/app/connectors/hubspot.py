"""HubSpot connector."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class HubSpotConnector(ConnectorHandler):
    connector_type = "hubspot"
    display_name = "HubSpot"
    oauth_platform = "hubspot"
    default_raw_dataset = "raw_hubspot"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        # HubSpot portals are identified by the OAuth connection, not a separate account list
        return []

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import connector_config_store
        import hubspot_sync_service

        cfg = connector_config_store.get_config(client_slug, "hubspot")
        project_id = cfg.bq_project_id if cfg else None
        dataset_id = cfg.mart_dataset_id if cfg else None

        try:
            result = hubspot_sync_service.sync_hubspot_contacts(
                project_id=project_id or None,
                dataset_id=dataset_id or None,
            )
            rows = result.get("rows_synced") or 0
            ok = result.get("status") == "ok"
            return SyncResult(
                rows_loaded=rows,
                error=result.get("error") if not ok else None,
            )
        except Exception as exc:
            _log.warning("HubSpot sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(HubSpotConnector())
