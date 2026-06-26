"""GA4 connector — uses service-account credentials, no user OAuth."""

from __future__ import annotations

from typing import Any

from connectors.base import ConnectorHandler, register


class GA4Connector(ConnectorHandler):
    connector_type = "ga4"
    display_name = "Google Analytics 4"
    oauth_platform = "google_ads"  # reuses Google OAuth client
    default_raw_dataset = "raw_ga4"
    no_oauth = True  # GA4 uses service-account JSON, not user OAuth

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        # GA4 properties are configured via the GA4 client registry, not account selection
        return []


register(GA4Connector())
