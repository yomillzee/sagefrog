"""Google Ads connector."""

from __future__ import annotations

from typing import Any

from connectors.base import ConnectorHandler, register


class GoogleAdsConnector(ConnectorHandler):
    connector_type = "google_ads"
    display_name = "Google Ads"
    oauth_platform = "google_ads"
    default_raw_dataset = "raw_google_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import google_ads_service
        import oauth_store
        refresh = oauth_store.get_refresh_token("google_ads", client_slug=client_slug)
        if not refresh:
            raise RuntimeError(f"No Google Ads token for client '{client_slug}'.")
        accounts = google_ads_service.list_accessible_customer_accounts()
        return [{"id": a.get("customer_id", ""), "name": a.get("descriptive_name", "")} for a in accounts]


register(GoogleAdsConnector())
