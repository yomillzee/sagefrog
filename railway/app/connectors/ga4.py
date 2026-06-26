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
        import ga4_clients
        rows = ga4_clients.list_clients_public()
        return [
            {
                "id": r.get("client_key") or r.get("account_id") or "",
                "name": r.get("label") or r.get("client_key") or r.get("account_id") or "",
            }
            for r in rows
            if r.get("client_key") or r.get("account_id")
        ]


register(GA4Connector())
