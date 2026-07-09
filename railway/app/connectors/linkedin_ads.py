"""LinkedIn Ads connector — wraps existing linkedin_service.py."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class LinkedInAdsConnector(ConnectorHandler):
    connector_type = "linkedin_ads"
    display_name = "LinkedIn Ads"
    oauth_platform = "linkedin"
    default_raw_dataset = "raw_linkedin_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        access_token = _get_access_token(client_slug)
        import linkedin_service
        return linkedin_service.list_ad_accounts(access_token=access_token)

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_linkedin_ads_service
        import bigquery_warehouse
        import connector_config_store
        import linkedin_service
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "linkedin_ads")
        account_id = cfg.source_account_id if cfg else None
        if not account_id:
            return SyncResult(rows_loaded=0, error="No LinkedIn account configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = cfg.raw_dataset_id if cfg else None
        start, end, _ = resolve_date_range(date_range)
        rows_written = 0
        try:
            with bq_linkedin_ads_service.route(
                bq_project_id=bq_project_id,
                linkedin_dataset_id=raw_dataset_id,
            ):
                daily_rows = linkedin_service.fetch_campaign_daily_metrics(
                    account_id, start=start, end=end
                )
                # Write campaign-level rows to raw_linkedin_ads.campaign_daily
                # (same table Google writes). Everything downstream — the Overview
                # mart (create_paid_media_mart_views) and the campaign-metadata /
                # explorer sync (sync_campaign_metadata_and_rebuild_mart) — reads
                # campaign_daily, so mirroring to the account-level metrics_daily
                # left those 404-ing on a table that never got created.
                mirrored = bigquery_warehouse.mirror_campaign_daily_batch(
                    "linkedin", account_id, daily_rows
                )
                rows_written += mirrored.get("rows_upserted") or 0
                meta = bq_linkedin_ads_service.sync_campaign_metadata_and_rebuild_mart(
                    account_id=account_id, start=start, end=end
                )
                rows_written += meta.get("rows_upserted") or 0
            # Rebuild the unified paid-media view + data-health mart (app's own
            # replacement for the Dataform marts) so the dashboard Overview
            # populates from this sync. Idempotent; includes whichever of
            # Google/LinkedIn raw tables currently exist.
            with bigquery_warehouse.route(bq_project_id=bq_project_id):
                bigquery_warehouse.create_paid_media_mart_views(client_key=client_slug)
            return SyncResult(rows_loaded=rows_written)
        except Exception as exc:
            _log.warning("LinkedIn sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=rows_written, error=str(exc)[:500])


def _get_access_token(client_slug: str) -> str:
    import oauth_store
    import linkedin_service
    from linkedin_auth import LinkedInEnv, _get_required_env, _get_env, _ENV_ALIASES

    refresh = oauth_store.get_refresh_token("linkedin", client_slug=client_slug)
    if not refresh:
        raise RuntimeError(
            f"No LinkedIn OAuth token found for client '{client_slug}'. "
            "Connect LinkedIn in the connector setup wizard."
        )
    env = LinkedInEnv(
        client_id=_get_required_env(*_ENV_ALIASES["client_id"]),
        client_secret=_get_required_env(*_ENV_ALIASES["client_secret"]),
        refresh_token=refresh,
        version=_get_env(*_ENV_ALIASES["version"]) or "202509",
    )
    data = linkedin_service.refresh_access_token(env)
    return data["access_token"]


register(LinkedInAdsConnector())
