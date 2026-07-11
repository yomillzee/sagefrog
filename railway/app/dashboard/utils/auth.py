"""Dashboard refresh cooldown and edit permissions.

The legacy ?key= share-link mechanism (and its DASHBOARD_SECRET) has been
retired — dashboards are session-only now.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any


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


def can_edit_penn_insights(*, session_is_admin: bool, access_key: str | None = None) -> bool:
    """Only signed-in admins may edit insights text.

    ``access_key`` is accepted for call-site compatibility but ignored: the
    legacy shared-key edit path has been retired along with ?key=.
    """
    return session_is_admin
