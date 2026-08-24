"""Bluesky connector — organic social metrics for a client's account.

No OAuth, and no per-client credential at all: the AT Protocol serves profile
and post reads unauthenticated from the public AppView, so the only per-client
input is the handle, entered manually in the wizard (there's no "list accounts"
API) and stored as source_account_id. An optional agency-level login
(BLUESKY_HANDLE + BLUESKY_APP_PASSWORD, like SEMRUSH_API_KEY) raises the rate
limit but nothing here requires it.

Worth telling clients up front: Bluesky exposes likes, reposts, replies, quotes
and follower counts — and no impressions, reach, or clicks. This connector can
never fill an "impressions" column the way LinkedIn or Meta do, because the
protocol doesn't publish one.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class BlueskyConnector(ConnectorHandler):
    connector_type = "bluesky"
    display_name = "Bluesky"
    oauth_platform = "bluesky"
    default_raw_dataset = "raw_bluesky"
    no_oauth = True
    manual_account_entry = True
    manual_account_label = "Bluesky handle (e.g. sagefrog.bsky.social)"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        """Wizard "Test connection" step — verifies the handle resolves to a real
        account. Not a real account picker (nothing to list), so it returns 0 or
        1 entries.
        """
        import bluesky_service
        import connector_config_store

        cfg = connector_config_store.get_config(client_slug, "bluesky")
        handle = (cfg.source_account_id if cfg else "") or ""
        if not handle:
            raise RuntimeError("Enter a Bluesky handle first.")

        profile = bluesky_service.fetch_profile(handle)
        resolved = profile.get("handle") or bluesky_service.normalize_handle(handle)
        name = profile.get("display_name") or resolved
        followers = profile.get("followers_count") or 0
        return [{
            "id": resolved,
            "name": f"{name} (@{resolved}) — {followers:,} followers",
            "status": "ok",
        }]

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_bluesky_service
        import connector_config_store
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "bluesky")
        handle = cfg.source_account_id if cfg else None
        if not handle:
            return SyncResult(rows_loaded=0, error="No Bluesky handle configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = cfg.raw_dataset_id if cfg else None
        start, end, _ = resolve_date_range(date_range)

        try:
            with bq_bluesky_service.route(
                bq_project_id=bq_project_id,
                bluesky_dataset_id=raw_dataset_id,
            ):
                result = bq_bluesky_service.sync_bluesky_to_bq(
                    handle, client_key=client_slug, start=start, end=end
                )
            errors = result.get("errors") or {}
            error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None
            return SyncResult(
                rows_loaded=result.get("total_rows") or 0,
                error=error_msg,
                range_start=start,
                range_end=end,
            )
        except Exception as exc:
            _log.warning("Bluesky sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(BlueskyConnector())
