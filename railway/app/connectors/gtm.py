"""Google Tag Manager connector — live-container audit via OAuth."""

from __future__ import annotations

import logging
from typing import Any

import connector_config_store
import gtm_service
import oauth_store
from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class GTMConnector(ConnectorHandler):
    connector_type = "gtm"
    display_name = "Google Tag Manager"
    oauth_platform = "google_tag_manager"
    default_raw_dataset = "raw_gtm"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        refresh_token = oauth_store.get_refresh_token(
            "google_tag_manager", client_slug=client_slug
        )
        if not refresh_token:
            raise RuntimeError("No google_tag_manager token found for this client.")
        return gtm_service.list_containers(refresh_token)

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        cfg = connector_config_store.get_config(client_slug, "gtm")
        if not cfg or not cfg.source_account_id:
            return SyncResult(rows_loaded=0, error="No container configured.")

        parts = (cfg.source_account_id or "").split(":")
        if len(parts) != 2 or not all(parts):
            return SyncResult(rows_loaded=0, error=f"Invalid source_account_id: {cfg.source_account_id!r}")

        account_id, container_id = parts[0], parts[1]
        refresh_token = oauth_store.get_refresh_token(
            "google_tag_manager", client_slug=client_slug
        )
        if not refresh_token:
            return SyncResult(rows_loaded=0, error="No google_tag_manager token found.")

        try:
            result = gtm_service.get_live_tags(
                client_slug,
                account_id,
                container_id,
                refresh_token,
                force_refresh=True,
            )
            tag_count = result.get("tag_count", 0)
            _log.info(
                "GTM audit %s/%s: %d tags", account_id, container_id, tag_count
            )
            return SyncResult(rows_loaded=tag_count)
        except PermissionError as exc:
            return SyncResult(rows_loaded=0, error=str(exc))
        except Exception as exc:
            _log.exception("GTM run_sync error for %s", client_slug)
            return SyncResult(rows_loaded=0, error=str(exc))


register(GTMConnector())
