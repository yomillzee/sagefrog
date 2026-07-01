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
        # The "account" is the HubSpot portal behind this client's OAuth token.
        import oauth_flows
        import oauth_store

        refresh = oauth_store.get_refresh_token("hubspot", client_slug=client_slug)
        if not refresh:
            return []
        try:
            token = oauth_flows.refresh_hubspot_access_token(refresh)
            info = oauth_flows.fetch_hubspot_portal_info(token)
            pid = str(info.get("portal_id") or "").strip()
            if not pid:
                return []
            name = info.get("company_name") or f"Portal {pid}"
            return [{"id": pid, "name": f"{name} ({pid})"}]
        except Exception as exc:
            _log.warning("HubSpot list_accounts failed [%s]: %s", client_slug, exc)
            return []

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import json
        import connector_config_store
        import hubspot_sync_service

        cfg = connector_config_store.get_config(client_slug, "hubspot")
        project_id = cfg.bq_project_id if cfg else None
        dataset_id = cfg.mart_dataset_id if cfg else None

        # Lifecycle stage + backfill window come from the connector's saved options.
        lifecycle_stage = None
        lookback_days = 90
        if cfg and cfg.sync_options:
            try:
                opts = json.loads(cfg.sync_options)
                lifecycle_stage = opts.get("lifecycle_stage") or None
                lookback_days = int(opts.get("lookback_days") or 90)
            except Exception:
                pass

        # Per-client HubSpot OAuth: refresh the stored token into an access token.
        # Falls back to the global HUBSPOT_ACCESS_TOKEN env when no client token
        # is connected (so existing single-portal setups keep working).
        access_token = None
        try:
            import oauth_store
            refresh = oauth_store.get_refresh_token("hubspot", client_slug=client_slug)
            if refresh:
                import oauth_flows
                access_token = oauth_flows.refresh_hubspot_access_token(refresh)
        except Exception as exc:
            _log.warning("HubSpot token refresh failed [%s]: %s", client_slug, exc)

        rows = 0
        errors: list[str] = []

        # Contacts (lifecycle-filtered) then deals (created/closed in window).
        try:
            c = hubspot_sync_service.sync_hubspot_contacts(
                project_id=project_id or None,
                dataset_id=dataset_id or None,
                lifecycle_stage=lifecycle_stage,
                lookback_days=lookback_days,
                access_token=access_token,
            )
            rows += c.get("rows_synced") or 0
        except Exception as exc:
            _log.warning("HubSpot contacts sync failed [%s]: %s", client_slug, exc)
            errors.append(f"contacts: {str(exc)[:200]}")

        try:
            d = hubspot_sync_service.sync_hubspot_deals(
                project_id=project_id or None,
                dataset_id=dataset_id or None,
                lookback_days=lookback_days,
                access_token=access_token,
            )
            rows += d.get("rows_synced") or 0
        except Exception as exc:
            _log.warning("HubSpot deals sync failed [%s]: %s", client_slug, exc)
            errors.append(f"deals: {str(exc)[:200]}")

        return SyncResult(rows_loaded=rows, error="; ".join(errors) if errors else None)


register(HubSpotConnector())
