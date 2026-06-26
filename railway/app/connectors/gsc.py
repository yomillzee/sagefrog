"""Search Console connector."""

from __future__ import annotations

from typing import Any

from connectors.base import ConnectorHandler, register


class GSCConnector(ConnectorHandler):
    connector_type = "gsc"
    display_name = "Search Console"
    oauth_platform = "gsc"
    default_raw_dataset = "raw_gsc"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        # GSC properties are specified by URL, not by account selection
        return []


register(GSCConnector())
