"""HubSpot connector."""

from __future__ import annotations

from typing import Any

from connectors.base import ConnectorHandler, register


class HubSpotConnector(ConnectorHandler):
    connector_type = "hubspot"
    display_name = "HubSpot"
    oauth_platform = "hubspot"  # will be added to oauth_flows when fully implemented
    default_raw_dataset = "raw_hubspot"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        # HubSpot portals are identified by the OAuth connection, not a separate account list
        return []


register(HubSpotConnector())
