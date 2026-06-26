"""LinkedIn Ads connector — wraps existing linkedin_service.py."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, register

_log = logging.getLogger(__name__)


class LinkedInAdsConnector(ConnectorHandler):
    connector_type = "linkedin_ads"
    display_name = "LinkedIn Ads"
    oauth_platform = "linkedin"
    default_raw_dataset = "raw_linkedin_ads"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        access_token = _get_access_token(client_slug)
        import linkedin_service
        return linkedin_service.list_ad_accounts(access_token=access_token)


def _get_access_token(client_slug: str) -> str:
    import oauth_store
    import linkedin_service
    from linkedin_auth import LinkedInEnv, _get_required_env, _get_env, _ENV_ALIASES

    refresh = oauth_store.get_refresh_token("linkedin", client_slug=client_slug)
    if not refresh:
        raise RuntimeError(
            f"No LinkedIn OAuth token found for client '{client_slug}'. "
            "Connect LinkedIn in the connector setup wizard."
        )
    env = LinkedInEnv(
        client_id=_get_required_env(*_ENV_ALIASES["client_id"]),
        client_secret=_get_required_env(*_ENV_ALIASES["client_secret"]),
        refresh_token=refresh,
        version=_get_env(*_ENV_ALIASES["version"]) or "202509",
    )
    data = linkedin_service.refresh_access_token(env)
    return data["access_token"]


register(LinkedInAdsConnector())
