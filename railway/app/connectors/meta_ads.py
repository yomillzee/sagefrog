"""Meta Ads connector."""

from __future__ import annotations

from typing import Any

from connectors.base import ConnectorHandler, register


class MetaAdsConnector(ConnectorHandler):
    connector_type = "meta_ads"
    display_name = "Meta Ads"
    oauth_platform = "meta"
    default_raw_dataset = "raw_meta_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        import meta_service
        import oauth_store
        token = oauth_store.get_access_token("meta", client_slug=client_slug)
        if not token:
            raise RuntimeError(f"No Meta token for client '{client_slug}'.")
        return meta_service.list_ad_accounts(access_token=token)


register(MetaAdsConnector())
