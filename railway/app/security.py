"""API key verification for external callers (e.g. ChatGPT Custom Actions)."""

from __future__ import annotations

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

    if not token or token != expected:
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid or missing API key. Send Authorization: Bearer <API_KEY> "
                "or header X-API-Key: <API_KEY>."
            ),
        )
