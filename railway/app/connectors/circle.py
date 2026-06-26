"""Circle community connector — placeholder for future implementation."""

from __future__ import annotations

from typing import Any

from connectors.base import ConnectorHandler, register


class CircleConnector(ConnectorHandler):
    connector_type = "circle"
    display_name = "Circle"
    oauth_platform = "circle"
    default_raw_dataset = "raw_circle"
    no_oauth = True  # not yet implemented

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        return []


register(CircleConnector())
