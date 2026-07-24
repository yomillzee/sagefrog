"""LinkedIn Organic connector — company-page posts, followers, page analytics.

Sibling to ``linkedin_ads``. Reuses the same LinkedIn OAuth connection (same
``oauth_platform = "linkedin"`` token store) but reads organic organization
metrics instead of sponsored/paid ones, writing to the ``raw_linkedin_organic``
dataset. The "account" here is a LinkedIn *organization* (company page), not an
ad account, so ``list_accounts`` discovers admin'd organizations.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)

# Scope required for follower + page + share statistics. Tokens minted before
# this scope was added to oauth_flows.LINKEDIN_SCOPES won't carry it, so the
# wizard prompts a reconnect (see needs_scope_reauth).
ORGANIC_ADMIN_SCOPE = "r_organization_admin"


class LinkedInOrganicConnector(ConnectorHandler):
    connector_type = "linkedin_organic"
    display_name = "LinkedIn Organic"
    oauth_platform = "linkedin"
    default_raw_dataset = "raw_linkedin_organic"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        access_token = _get_access_token(client_slug)
        import linkedin_organic_service
        return linkedin_organic_service.list_organizations(access_token=access_token)

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_linkedin_organic_service
        import connector_config_store
        from dates_util import resolve_date_range

        cfg = connector_config_store.get_config(client_slug, "linkedin_organic")
        org_id = cfg.source_account_id if cfg else None
        if not org_id:
            return SyncResult(rows_loaded=0, error="No LinkedIn organization configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = cfg.raw_dataset_id if cfg else None
        start, end, _ = resolve_date_range(date_range)
        try:
            # Resolve THIS client's access token up front and thread it through —
            # connector clients store their LinkedIn token client-scoped, so the
            # service layer would otherwise fall back to the tokenless global env.
            access_token = _get_access_token(client_slug)
            with bq_linkedin_organic_service.route(
                bq_project_id=bq_project_id,
                organic_dataset_id=raw_dataset_id,
            ):
                result = bq_linkedin_organic_service.run_organic_sync(
                    org_id=org_id, start=start, end=end, access_token=access_token
                )
            return SyncResult(
                rows_loaded=int(result.get("rows_upserted") or 0),
                range_start=start,
                range_end=end,
            )
        except Exception as exc:
            _log.warning("LinkedIn organic sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])

    def test_connection(self, *, client_slug: str) -> str:
        accounts = self.list_accounts(client_slug=client_slug)
        return accounts[0]["name"] if accounts else ""


def needs_scope_reauth(client_slug: str) -> bool:
    """True when a LinkedIn token exists for this client but predates the
    organic-admin scope, so the wizard should prompt a reconnect.

    Returns False when there's no token yet (the normal connect flow will request
    the current scope set) or when the stored scopes already include it.
    """
    try:
        import oauth_store
        status = oauth_store.public_status("linkedin", client_slug=client_slug)
    except Exception:
        return False
    if not status or not status.connected:
        return False
    scopes = str(status.scopes or "")
    if not scopes.strip():
        # Older tokens stored no scope string; can't prove it has admin — prompt.
        return True
    return ORGANIC_ADMIN_SCOPE not in scopes


def _get_access_token(client_slug: str) -> str:
    import oauth_store
    import linkedin_service
    from linkedin_auth import LinkedInEnv, _get_required_env, _get_env, _ENV_ALIASES

    refresh = oauth_store.get_refresh_token("linkedin", client_slug=client_slug)
    if not refresh:
        raise RuntimeError(oauth_store.token_error(
            "linkedin", client_slug=client_slug,
            missing=(
                f"No LinkedIn OAuth token found for client '{client_slug}'. "
                "Connect LinkedIn in the connector setup wizard."
            ),
        ))
    env = LinkedInEnv(
        client_id=_get_required_env(*_ENV_ALIASES["client_id"]),
        client_secret=_get_required_env(*_ENV_ALIASES["client_secret"]),
        refresh_token=refresh,
        version=_get_env(*_ENV_ALIASES["version"]) or "202509",
    )
    data = linkedin_service.refresh_access_token(env)
    return data["access_token"]


register(LinkedInOrganicConnector())
