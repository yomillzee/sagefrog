from __future__ import annotations

import os
from dataclasses import dataclass

_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "client_id": ("LINKEDIN_CLIENT_ID",),
    "client_secret": ("LINKEDIN_CLIENT_SECRET",),
    "version": ("LINKEDIN_VERSION",),
}


def _strip_env_value(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1].strip()
    return val


def _get_env(*keys: str) -> str | None:
    for key in keys:
        raw = os.getenv(key)
        if not raw:
            continue
        val = _strip_env_value(raw)
        if val:
            return val
    return None


def _get_required_env(*keys: str) -> str:
    val = _get_env(*keys)
    if not val:
        raise RuntimeError(f"Missing required environment variable (one of): {', '.join(keys)}")
    return val


@dataclass(frozen=True)
class LinkedInEnv:
    client_id: str
    client_secret: str
    refresh_token: str
    version: str


def load_linkedin_env(*, require_token: bool = True) -> LinkedInEnv:
    """Build the LinkedIn env. With require_token=False the global refresh token
    is optional (blank if absent) instead of raising — used by callers that
    already hold a client-scoped access token and only need client_id / secret /
    version, so a client whose token lives under its own slug can still sync when
    the global token is missing/undecryptable."""
    return LinkedInEnv(
        client_id=_get_required_env(*_ENV_ALIASES["client_id"]),
        client_secret=_get_required_env(*_ENV_ALIASES["client_secret"]),
        refresh_token=_resolve_refresh_token(required=require_token),
        version=_get_env(*_ENV_ALIASES["version"]) or "202509",
    )


def _resolve_refresh_token(required: bool = True) -> str:
    try:
        import oauth_store

        db_token = oauth_store.get_refresh_token("linkedin")
        if db_token:
            return db_token
    except Exception:
        pass
    if not required:
        return ""
    raise RuntimeError(
        "Missing LinkedIn refresh token. Connect LinkedIn in dashboard settings "
        "(Settings → Connect LinkedIn)."
    )


def _has_refresh_token() -> bool:
    try:
        import oauth_store

        return bool(oauth_store.get_refresh_token("linkedin"))
    except Exception:
        return False


def env_summary() -> dict:
    has_refresh = _has_refresh_token()
    return {
        "has_client_id": bool(_get_env(*_ENV_ALIASES["client_id"])),
        "has_client_secret": bool(_get_env(*_ENV_ALIASES["client_secret"])),
        "has_refresh_token": has_refresh,
        "linkedin_version": _get_env(*_ENV_ALIASES["version"]) or "202509",
        "refresh_token_stored": has_refresh,
    }
