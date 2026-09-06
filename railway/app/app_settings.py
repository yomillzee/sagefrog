"""Global, super-admin-managed application settings, persisted in Postgres.

A tiny key/value store for a handful of instance-wide knobs that a super admin
can flip from the Admin page without a redeploy — currently just the dashboard
read-cache TTL floor (see ``dashboard_cache_ttl_seconds``).

Reads are memoized in-process for a few seconds so hot paths (every dashboard
card read consults the TTL floor) don't pay a Postgres round-trip per request;
a write clears the memo so a change from the Admin page is visible within the
refresh interval at worst.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime

import db

LOGGER = logging.getLogger(__name__)

# Setting keys. Kept as constants so the store, the reader, and the Admin UI all
# refer to the same string.
DASHBOARD_CACHE_TTL_KEY = "dashboard_cache_ttl_seconds"

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS app_settings (
      key TEXT PRIMARY KEY,
      value TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by TEXT
    )
    """,
]

# In-process memo: {key: (value, fetched_at_monotonic)}. Short TTL so an Admin
# change lands quickly while keeping per-request reads off the database.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[str | None, float]] = {}
_cache_lock = threading.Lock()


def enabled() -> bool:
    return bool(db.get_db_url())


def ensure_schema() -> bool:
    if not enabled():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _invalidate(key: str) -> None:
    with _cache_lock:
        _cache.pop(key, None)


def get(key: str, default: str | None = None) -> str | None:
    """Return a raw setting value (memoized), or ``default`` if unset/unavailable."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and (now - hit[1]) < _CACHE_TTL_SECONDS:
            value = hit[0]
            return value if value is not None else default

    value: str | None = None
    if enabled():
        try:
            ensure_schema()
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key = %s", (key,)
                ).fetchone()
            value = row[0] if row else None
        except Exception:
            # A settings-store hiccup must never break a caller — fall back to
            # the default (i.e. behave as if the setting were unset).
            LOGGER.warning("app_settings read failed for %s", key, exc_info=True)
            value = None

    with _cache_lock:
        _cache[key] = (value, now)
    return value if value is not None else default


def set(key: str, value: str | None, *, updated_by: str | None = None) -> None:
    if not enabled():
        raise RuntimeError("DATABASE_URL is not set — app settings require Postgres.")
    ensure_schema()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at, updated_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
              value = EXCLUDED.value,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (key, value, datetime.now(tz=UTC), updated_by),
        )
    _invalidate(key)


# ── Dashboard read-cache TTL floor ──────────────────────────────────────────
# The BigQuery-mart dashboard reads (api_routes._cached_bq_read) apply a global
# TTL floor so warmed/viewed cards stay warm between syncs. A sync invalidates
# the cache immediately, so a longer floor adds zero staleness — only the first
# load after a sync is ever cold. This value is the floor; unset/0 keeps each
# endpoint's own per-call TTL.

# Choices surfaced in the Admin UI (seconds → human label). None = use defaults.
DASHBOARD_CACHE_TTL_CHOICES: list[tuple[int, str]] = [
    (0, "Default (per-card, ~15 min)"),
    (3600, "1 hour"),
    (21600, "6 hours"),
    (86400, "24 hours"),
]


def dashboard_cache_ttl_seconds() -> int:
    """Resolve the dashboard read-cache TTL floor.

    Precedence: the super-admin app setting (if a positive value is stored) wins;
    otherwise the ``DASH_CACHE_TTL_SECONDS`` environment variable; otherwise 0
    (no floor — per-endpoint defaults apply).
    """
    raw = get(DASHBOARD_CACHE_TTL_KEY)
    if raw is not None and str(raw).strip() != "":
        try:
            val = int(str(raw).strip())
            if val > 0:
                return val
            # A stored 0 is an explicit "use defaults" — don't fall through to
            # the env var, which would otherwise silently override the choice.
            return 0
        except ValueError:
            LOGGER.warning("app_settings: bad %s value %r; ignoring", DASHBOARD_CACHE_TTL_KEY, raw)
    try:
        return max(0, int(os.getenv("DASH_CACHE_TTL_SECONDS") or 0))
    except ValueError:
        return 0


def set_dashboard_cache_ttl_seconds(value: int, *, updated_by: str | None = None) -> None:
    if value < 0:
        raise ValueError("TTL seconds must be >= 0.")
    set(DASHBOARD_CACHE_TTL_KEY, str(int(value)), updated_by=updated_by)


def _reset_cache_for_tests() -> None:  # pragma: no cover - test hook
    with _cache_lock:
        _cache.clear()
