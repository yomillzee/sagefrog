"""Dashboard access keys, refresh cooldown, and edit permissions."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import web_users
from security import is_production


def configured_dashboard_secret() -> str | None:
    # Dedicated to DASHBOARD_SECRET so the legacy ?key= link no longer piggybacks
    # on CRON_SECRET. Fail-closed in production (no CRON_SECRET fallback); local
    # dev keeps the fallback for convenience.
    dedicated = (os.getenv("DASHBOARD_SECRET") or "").strip()
    if dedicated:
        return dedicated
    if is_production():
        return None
    fallback = (os.getenv("CRON_SECRET") or "").strip()
    return fallback or None


def min_refresh_seconds(*, quick: bool = False) -> int:
    """Minimum seconds between manual dashboard refreshes. Default 0 (no cooldown)."""
    env_key = "DASHBOARD_MIN_QUICK_REFRESH_SECONDS" if quick else "DASHBOARD_MIN_REFRESH_SECONDS"
    raw = (os.getenv(env_key) or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def parse_refreshed_at(snapshot: dict[str, Any] | None) -> datetime | None:
    raw = (snapshot or {}).get("refreshed_at")
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def refresh_cooldown_status(
    snapshot: dict[str, Any] | None, *, quick: bool = False
) -> tuple[bool, int]:
    """Return (allowed_now, seconds_remaining)."""
    wait = min_refresh_seconds(quick=quick)
    if wait <= 0:
        return True, 0
    last = parse_refreshed_at(snapshot)
    if not last:
        return True, 0
    elapsed = (datetime.now(tz=UTC) - last).total_seconds()
    if elapsed >= wait:
        return True, 0
    return False, int(wait - elapsed)


def verify_dashboard_key(key: str | None) -> None:
    from fastapi import HTTPException

    expected = configured_dashboard_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Dashboard access is not configured (set DASHBOARD_SECRET).",
        )
    if not key or key.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing key query parameter.")


def can_edit_penn_insights(*, session_is_admin: bool, access_key: str | None) -> bool:
    """Admins (session) or legacy shared-key mode may edit insights text."""
    if session_is_admin:
        return True
    if not web_users.enabled() and access_key:
        return True
    return False
