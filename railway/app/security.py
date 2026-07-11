"""API key verification for external callers (e.g. ChatGPT Custom Actions)."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def configured_api_key() -> str | None:
    key = os.getenv("API_KEY", "").strip()
    return key or None


def is_production() -> bool:
    """True when running on Railway (RAILWAY_ENVIRONMENT or RAILWAY_PUBLIC_DOMAIN is present)."""
    return bool(
        (os.getenv("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    )


def session_signing_secret() -> str:
    """Secret for signing session cookies and connect-link / OAuth-state tokens.

    Dedicated to ``AUTH_SESSION_SECRET``. In production this is required with no
    fallback: the old chain to ``CRON_SECRET`` / ``API_KEY`` meant one secret's
    compromise or rotation cascaded across unrelated auth surfaces (sessions,
    cron auth, the API key). Local dev keeps a convenience fallback so setups
    without the dedicated variable still run.
    """
    dedicated = (os.getenv("AUTH_SESSION_SECRET") or "").strip()
    if dedicated:
        return dedicated
    if is_production():
        raise RuntimeError(
            "AUTH_SESSION_SECRET is not set. Set a dedicated session signing "
            "secret in the environment — it is required in production and must "
            "not be shared with CRON_SECRET or API_KEY."
        )
    fallback = (
        (os.getenv("CRON_SECRET") or "").strip()
        or (os.getenv("API_KEY") or "").strip()
    )
    if not fallback:
        raise RuntimeError(
            "Set AUTH_SESSION_SECRET to sign session cookies (the CRON_SECRET / "
            "API_KEY fallback is dev-only and is also unset)."
        )
    return fallback


def session_signing_secret_configured() -> bool:
    """Non-raising check: True when a session signing secret is resolvable."""
    try:
        return bool(session_signing_secret())
    except RuntimeError:
        return False


async def require_api_key(
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    x_api_key: str | None = Security(_api_key_header),
) -> None:
    """Require Bearer token or X-API-Key. Fails closed in production; open in local dev when API_KEY is unset."""
    expected = configured_api_key()
    if not expected:
        if is_production():
            raise HTTPException(
                status_code=503,
                detail=(
                    "API_KEY is not configured on this server. "
                    "Set API_KEY in Railway environment variables."
                ),
            )
        return  # local dev: open access when API_KEY is absent

    token: str | None = None
    if bearer_credentials and bearer_credentials.credentials:
        token = bearer_credentials.credentials.strip()
    elif x_api_key:
        token = x_api_key.strip()

    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid or missing API key. Send Authorization: Bearer <API_KEY> "
                "or header X-API-Key: <API_KEY>."
            ),
        )
