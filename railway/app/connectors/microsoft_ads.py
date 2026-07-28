"""Microsoft Ads connector — Google-OAuth authenticated, writes to raw_microsoft_ads.

Mirrors the LinkedIn Ads connector: it fetches per-campaign daily metrics from
the Microsoft Advertising Reporting API, writes them to the raw
``campaign_daily`` table, and rebuilds the unified paid-media mart so the
dashboard Overview picks up Microsoft spend. Authentication is delegated to
Google (see oauth_flows / microsoft_ads_service).
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class MicrosoftAdsConnector(ConnectorHandler):
    connector_type = "microsoft_ads"
    display_name = "Microsoft Ads"
    oauth_platform = "microsoft_ads"
    default_raw_dataset = "raw_microsoft_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import microsoft_ads_service
        return microsoft_ads_service.list_accounts(client_slug=client_slug)

    def test_connection(self, *, client_slug: str) -> str:
        import connector_config_store
        import microsoft_ads_service

        cfg = connector_config_store.get_config(client_slug, self.connector_type)
        accounts = microsoft_ads_service.list_accounts(client_slug=client_slug)
        if cfg and cfg.source_account_id:
            match = next((a for a in accounts if str(a.get("id")) == str(cfg.source_account_id)), None)
            if match:
                return str(match.get("name") or cfg.source_account_id)
        return accounts[0]["name"] if accounts else ""

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bigquery_warehouse
        import connector_config_store
        import microsoft_ads_service
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, self.connector_type)
        account_id = cfg.source_account_id if cfg else None
        if not account_id:
            return SyncResult(rows_loaded=0, error="No Microsoft Ads account configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = (cfg.raw_dataset_id if cfg else None) or self.default_raw_dataset
        start, end, _ = resolve_date_range(date_range)
        rows_written = 0
        try:
            access_token = microsoft_ads_service.resolve_access_token(client_slug)
            # Campaign-grain metrics drive the Overview totals (source of truth —
            # covers every campaign type). Ad-grain rows (with copy) drive the
            # Campaign Explorer drilldown; not all campaign types have text ads, so
            # ad_daily supplements — it never replaces — campaign_daily.
            daily_rows = microsoft_ads_service.fetch_campaign_daily_metrics(
                account_id, start=start, end=end, access_token=access_token
            )
            ad_rows = microsoft_ads_service.fetch_ad_daily_metrics(
                account_id, start=start, end=end, access_token=access_token
            )
            with bigquery_warehouse.route(
                bq_project_id=bq_project_id,
                microsoft_dataset_id=raw_dataset_id,
            ):
                # Write per-campaign daily rows to raw_microsoft_ads.campaign_daily
                # — the same shape Google/LinkedIn/Meta write, so the unified
                # paid-media mart unions them with no double counting.
                mirrored = bigquery_warehouse.mirror_campaign_daily_batch(
                    "microsoft", account_id, daily_rows
                )
                rows_written += mirrored.get("rows_upserted") or 0
                # Per-ad rows (campaign → ad group → ad, with ad copy) for the
                # Campaign Explorer drilldown.
                ad_mirrored = bigquery_warehouse.mirror_microsoft_ad_daily_batch(
                    account_id, ad_rows
                )
                rows_written += ad_mirrored.get("rows_upserted") or 0
                # Rebuild vw_paid_media_daily + mart_health so the dashboard
                # Overview populates from this sync. Idempotent; includes whichever
                # of the raw campaign_daily tables currently exist.
                bigquery_warehouse.create_paid_media_mart_views(client_key=client_slug)
                # Explorer view the Campaign Explorer tab reads
                # (explorer_microsoft_ads_daily) — ad-level when ad_daily exists,
                # campaign-level fallback otherwise.
                bigquery_warehouse.create_microsoft_ads_mart_view()
            return SyncResult(rows_loaded=rows_written, range_start=start, range_end=end)
        except Exception as exc:
            _log.warning("Microsoft Ads sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=rows_written, error=str(exc)[:500])


register(MicrosoftAdsConnector())
