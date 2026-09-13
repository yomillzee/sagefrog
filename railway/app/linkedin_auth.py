from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date

log = logging.getLogger(__name__)

# LinkedIn publishes a new Marketing API version every month and supports each
# one for a year from release, so an unset LINKEDIN_VERSION is not a neutral
# default — it is a dated one with a sunset baked in. This sat at 202509, which
# LinkedIn sunset on 2026-09-15; because nothing alerts on a failed connector
# sync, the first sign would have been a client asking why their LinkedIn
# numbers stopped moving. Keep this on a version the code has actually been
# exercised against (see the adCampaigns note in
# linkedin_service.account_performance) and move it before its own sunset —
# tests/test_linkedin_api_version.py goes red while there is still time to.
DEFAULT_LINKEDIN_VERSION = "202604"

_VERSION_RE = re.compile(r"^20\d{4}$")


def version_sunset_date(version: str | None) -> date | None:
    """The day LinkedIn stops serving ``version``, read off the string itself.

    A version is ``YYYYMM`` and is supported for "a minimum of one year";
    LinkedIn sunsets mid-month a year on (202509 went on 2026-09-15, 202510 on
    2026-10-15). This rounds down to the 1st so the estimate lands early rather
    than late — erring the other way would mean calling a dead version live.
    Returns None for anything that is not a ``YYYYMM`` version, which is left
    alone rather than second-guessed.
    """
    candidate = (version or "").strip()
    if not _VERSION_RE.match(candidate):
        return None
    year, month = int(candidate[:4]), int(candidate[4:])
    if not 1 <= month <= 12:
        return None
    return date(year + 1, month, 1)


def resolve_version(configured: str | None) -> str:
    """The Linkedin-Version to send: what is configured, unless it is dead.

    LINKEDIN_VERSION is set once in Railway and then nobody looks at it again,
    so it outlives the version it names — ours said 202509, which LinkedIn
    sunset on 2026-09-15. A sunset version does not degrade, it rejects every
    call with 426, and while the fallback ladder in linkedin_service now steps
    past that, plenty of calls go straight through ``_linkedin_get`` and would
    simply fail. Preferring the app's default over a version known to be dead
    keeps those working; the warning is there so the stale variable still gets
    cleaned up rather than quietly papered over forever.
    """
    candidate = (configured or "").strip()
    if not candidate:
        return DEFAULT_LINKEDIN_VERSION
    sunset = version_sunset_date(candidate)
    if sunset is not None and sunset <= date.today():
        log.warning(
            "LINKEDIN_VERSION=%s was sunset by LinkedIn around %s; sending %s instead. "
            "Update or unset LINKEDIN_VERSION.",
            candidate,
            sunset,
            DEFAULT_LINKEDIN_VERSION,
        )
        return DEFAULT_LINKEDIN_VERSION
    return candidate


_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "client_id": ("LINKEDIN_CLIENT_ID",),
    "client_secret": ("LINKEDIN_CLIENT_SECRET",),
    "version": ("LINKEDIN_VERSION",),
}

# The LinkedIn *organic* connector authenticates against its own app (approved
# for the Community Management API) rather than the paid/ads app. Its credentials
# live under LINKEDIN_ORGANIC_* env vars, falling back to the shared paid-app
# vars so single-app deployments that never split the two keep working.
_ORGANIC_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "client_id": ("LINKEDIN_ORGANIC_CLIENT_ID", "LINKEDIN_CLIENT_ID"),
    "client_secret": ("LINKEDIN_ORGANIC_CLIENT_SECRET", "LINKEDIN_CLIENT_SECRET"),
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
        version=resolve_version(_get_env(*_ENV_ALIASES["version"])),
    )


def _resolve_refresh_token(required: bool = True) -> str:
    try:
        import oauth_store

        db_token = oauth_store.get_refresh_token("linkedin")
        if db_token:
            return db_token
    except Exception as exc:
        # Falls through to the environment variable below, which is the normal
        # path when tokens are not stored in the database.
        log.debug("no stored linkedin refresh token: %s", exc)
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
        "linkedin_version": resolve_version(_get_env(*_ENV_ALIASES["version"])),
        "refresh_token_stored": has_refresh,
    }


def organic_env_summary() -> dict:
    """Credential readiness for the organic app (Community Management)."""
    return {
        "has_client_id": bool(_get_env(*_ORGANIC_ENV_ALIASES["client_id"])),
        "has_client_secret": bool(_get_env(*_ORGANIC_ENV_ALIASES["client_secret"])),
    }
