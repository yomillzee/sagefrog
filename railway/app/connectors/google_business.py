"""Google Business Profile connector — local listing performance and reviews.

The wizard picks an **account** ("accounts/123"), not a location: a client's
Business Profile account holds every one of their locations, and syncing all of
them is what a multi-location client wants. run_sync enumerates the locations
each time, so a location added in Google shows up without anyone touching the
connector.

Access is gated on Google's side. Until Google approves a Business Profile API
access application for the Cloud project, every call fails with a 429 against a
zero quota — which looks exactly like rate limiting and is not. That surfaces
here as a plain-language setup message rather than a retry loop; see
google_business_service._is_access_not_approved.
"""

from __future__ import annotations

import logging
from typing import Any

import connector_config_store
import google_business_service
import oauth_store
from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class GoogleBusinessConnector(ConnectorHandler):
    connector_type = "google_business"
    display_name = "Google Business Profile"
    oauth_platform = "google_business"
    default_raw_dataset = "raw_google_business"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        refresh_token = oauth_store.get_refresh_token(
            "google_business", client_slug=client_slug
        )
        if not refresh_token:
            raise RuntimeError("No google_business token found for this client.")
        return google_business_service.list_accounts(refresh_token)

    def test_connection(self, *, client_slug: str) -> str:
        """Verify the token works and, once an account is chosen, that it has
        locations — an account with none would sync cleanly and show nothing,
        which is the confusing outcome worth catching in the wizard."""
        refresh_token = oauth_store.get_refresh_token(
            "google_business", client_slug=client_slug
        )
        if not refresh_token:
            raise RuntimeError("No google_business token found for this client.")

        cfg = connector_config_store.get_config(client_slug, "google_business")
        account = (cfg.source_account_id or "") if cfg else ""
        if not account:
            return super().test_connection(client_slug=client_slug)

        locations = google_business_service.list_locations(refresh_token, account)
        if not locations:
            raise RuntimeError(
                "That Business Profile account has no locations this login can see."
            )
        label = cfg.source_account_name or account
        return f"{label} — {len(locations)} location{'s' if len(locations) != 1 else ''}"

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_google_business_service
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "google_business")
        account = (cfg.source_account_id or "") if cfg else ""
        if not account:
            return SyncResult(rows_loaded=0, error="No Business Profile account configured.")

        refresh_token = oauth_store.get_refresh_token(
            "google_business", client_slug=client_slug
        )
        if not refresh_token:
            return SyncResult(rows_loaded=0, error=oauth_store.token_error(
                "google_business", client_slug=client_slug,
                missing="No google_business token found.",
            ))

        start, end, _ = resolve_date_range(date_range)
        try:
            locations = google_business_service.list_locations(refresh_token, account)
        except google_business_service.GoogleBusinessAccessNotApproved as exc:
            return SyncResult(rows_loaded=0, error=str(exc))
        except Exception as exc:
            _log.warning("Google Business location listing failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])

        if not locations:
            return SyncResult(rows_loaded=0, error="No locations found for this account.")

        try:
            with bq_google_business_service.route(
                bq_project_id=cfg.bq_project_id if cfg else None,
                dataset_id=cfg.raw_dataset_id if cfg else None,
            ):
                result = bq_google_business_service.sync_google_business_to_bq(
                    client_key=client_slug,
                    refresh_token=refresh_token,
                    account=account,
                    locations=locations,
                    start=start,
                    end=end,
                )
        except google_business_service.GoogleBusinessAccessNotApproved as exc:
            return SyncResult(rows_loaded=0, error=str(exc))
        except Exception as exc:
            _log.warning("Google Business sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])

        errors = result.get("errors") or {}
        error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None
        return SyncResult(
            rows_loaded=result.get("total_rows") or 0,
            error=error_msg,
            range_start=start,
            range_end=end,
        )


register(GoogleBusinessConnector())
