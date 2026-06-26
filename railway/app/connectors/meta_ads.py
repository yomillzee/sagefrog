"""Meta Ads connector."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class MetaAdsConnector(ConnectorHandler):
    connector_type = "meta_ads"
    display_name = "Meta Ads"
    oauth_platform = "meta"
    default_raw_dataset = "raw_meta_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import meta_service
        import oauth_store
        token = oauth_store.get_access_token("meta", client_slug=client_slug)
        if not token:
            raise RuntimeError(f"No Meta token for client '{client_slug}'.")
        try:
            return meta_service.list_ad_accounts(access_token=token)
        except RuntimeError as exc:
            # business_management Advanced Access not approved — fall back to
            # /me/adaccounts which only requires ads_read.
            if "business_management" in str(exc) or "#100" in str(exc):
                _log.info("Meta business manager unavailable, falling back to /me/adaccounts")
                return meta_service.list_user_ad_accounts(access_token=token)
            raise

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_meta_ads_service
        import connector_config_store
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "meta_ads")
        account_id = cfg.source_account_id if cfg else None
        if not account_id:
            return SyncResult(rows_loaded=0, error="No Meta account configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        start, end, _ = resolve_date_range(date_range)
        try:
            with bq_meta_ads_service.route(bq_project_id=bq_project_id):
                result = bq_meta_ads_service.sync_meta_to_bq(
                    account_id, start=start, end=end
                )
            rows = (
                (result.get("campaign_rows") or 0)
                + (result.get("adset_rows") or 0)
                + (result.get("ad_rows") or 0)
            )
            errors = result.get("errors") or {}
            error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None
            return SyncResult(rows_loaded=rows, error=error_msg)
        except Exception as exc:
            _log.warning("Meta sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(MetaAdsConnector())
