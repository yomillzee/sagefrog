"""Google Ads connector — fetches campaign metrics via API and writes to raw_google_ads."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


def _friendly_sync_error(exc: Exception, customer_id: str) -> str:
    """Translate opaque Google Ads API failures into an actionable message.

    A suspended, cancelled, or not-yet-billed Google Ads account returns a raw
    gRPC ``PERMISSION_DENIED`` ("The caller does not have permission") when
    queried — even when the account is correctly linked to the manager (login)
    account. That verbatim ``_InactiveRpcError`` string is unreadable and
    misleadingly points at a credentials/config problem, so surface the likely
    account-side cause instead. Non-permission errors fall through unchanged.
    """
    raw = str(exc)
    low = raw.lower()
    if "permission_denied" in low or "caller does not have permission" in low:
        return (
            f"Google Ads account {customer_id} is not accessible (PERMISSION_DENIED). "
            "It's linked to your manager account, so this usually means the account "
            "itself is suspended, cancelled, or has no active billing set up. Confirm "
            "it's active with billing enabled in Google Ads, then re-sync."
        )
    return raw[:500]


class GoogleAdsConnector(ConnectorHandler):
    connector_type = "google_ads"
    display_name = "Google Ads"
    oauth_platform = "google_ads"
    default_raw_dataset = "raw_google_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import os

        import google_ads_service
        import oauth_store
        from auth import GoogleAdsEnv

        refresh = oauth_store.get_refresh_token("google_ads", client_slug=client_slug)
        if not refresh:
            raise RuntimeError(f"No Google Ads token for client '{client_slug}'.")
        # Build the client from THIS client's refresh token — list_accessible_customer_accounts()
        # defaults to the global/agency-wide token (auth._resolve_refresh_token(), which
        # ignores client_slug) when no client is passed, which silently ignores the token
        # just looked up above. Mirrors the pattern already used correctly in
        # bq_google_ads_service.sync_google_ads_to_bq for the same reason.
        env = GoogleAdsEnv(
            developer_token=(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or os.getenv("GOOGLE_DEVELOPER_TOKEN") or "").strip(),
            client_id=(os.getenv("GOOGLE_ADS_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip(),
            client_secret=(os.getenv("GOOGLE_ADS_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip(),
            refresh_token=refresh,
            login_customer_id=(os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or os.getenv("GOOGLE_LOGIN_CUSTOMER_ID") or "").strip() or None,
        )
        client = google_ads_service.build_client(env)
        accounts = google_ads_service.list_accessible_customer_accounts(client=client)
        return [{"id": a.get("customer_id", ""), "name": a.get("descriptive_name", "")} for a in accounts]

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bigquery_warehouse
        import bq_google_ads_service
        import connector_config_store
        import oauth_store
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "google_ads")
        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = (cfg.raw_dataset_id if cfg else None) or self.default_raw_dataset
        customer_id = (cfg.source_account_id if cfg else None)

        if not customer_id:
            return SyncResult(rows_loaded=0, error="No Google Ads customer ID configured for this client.")

        refresh = oauth_store.get_refresh_token("google_ads", client_slug=client_slug)
        if not refresh:
            return SyncResult(rows_loaded=0, error=oauth_store.token_error(
                "google_ads", client_slug=client_slug,
                missing="No Google Ads refresh token. Reconnect Google Ads in settings.",
            ))

        start, end, _ = resolve_date_range(date_range)
        try:
            with bq_google_ads_service.route(
                bq_project_id=bq_project_id,
                google_dataset_id=raw_dataset_id,
            ):
                result = bq_google_ads_service.sync_google_ads_to_bq(
                    customer_id,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    refresh_token=refresh,
                    client_key=client_slug,
                )
            with bigquery_warehouse.route(bq_project_id=bq_project_id):
                bigquery_warehouse.create_google_campaign_mart_view()
                # Rebuild the unified paid-media view + data-health mart (app's
                # own replacement for the Dataform marts) so the dashboard
                # Overview populates from this sync. Idempotent; includes
                # whichever of Google/LinkedIn raw tables currently exist.
                bigquery_warehouse.create_paid_media_mart_views(client_key=client_slug)
            rows = int(result.get("total_rows") or 0)
            errors = result.get("errors") or {}
            return SyncResult(
                rows_loaded=rows,
                error=str(next(iter(errors.values()))) if errors else None,
                range_start=start,
                range_end=end,
            )
        except Exception as exc:
            _log.warning("Google Ads sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=_friendly_sync_error(exc, customer_id))


register(GoogleAdsConnector())
