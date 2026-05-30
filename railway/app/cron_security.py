"""Auth for scheduled / internal sync routes."""

from __future__ import annotations

import os

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_cron_header = APIKeyHeader(name="X-Cron-Secret", auto_error=False)


def configured_cron_secret() -> str | None:
    secret = (os.getenv("CRON_SECRET") or "").strip()
    return secret or None


async def require_cron_secret(x_cron_secret: str | None = Security(_cron_header)) -> None:
    expected = configured_cron_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="CRON_SECRET is not configured on the server.",
        )
    if not x_cron_secret or x_cron_secret.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Cron-Secret header.")
