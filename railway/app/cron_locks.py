"""Simple Postgres-backed locks for long-running cron endpoints.

Prevents two overlapping runs of the same job (e.g. a slow day's
/internal/sync-bq-all still churning through clients when the next day's
cron tick fires) from stacking on top of each other and doubling external
API + BigQuery load for every client at once.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import db

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS cron_locks (
      name TEXT PRIMARY KEY,
      locked_at TIMESTAMPTZ NOT NULL,
      locked_by TEXT
    )
    """,
]


def _db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return bool(_db_url())


def ensure_schema() -> bool:
    if not enabled():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL:
            conn.execute(stmt)
    return True


def try_acquire(name: str, *, ttl_seconds: int = 7200, locked_by: str | None = None) -> bool:
    """Acquire a named lock.

    Returns True if acquired -- either the lock was free, or the previous
    holder's lock is older than ttl_seconds (a crashed/killed run doesn't
    wedge this lock forever; the next tick reclaims it and proceeds).
    Returns False if someone else currently holds a fresh lock.

    Best-effort: if DATABASE_URL isn't set, always returns True (nothing to
    enforce), matching this app's convention of degrading gracefully rather
    than blocking a sync entirely when Postgres isn't configured.
    """
    if not enabled():
        return True
    ensure_schema()
    now = datetime.now(tz=UTC)
    stale_before = now - timedelta(seconds=ttl_seconds)
    with db.connection() as conn:
        conn.execute(
            "DELETE FROM cron_locks WHERE name = %s AND locked_at < %s",
            (name, stale_before),
        )
        row = conn.execute(
            """
            INSERT INTO cron_locks (name, locked_at, locked_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (name) DO NOTHING
            RETURNING name
            """,
            (name, now, locked_by),
        ).fetchone()
    return row is not None


def release(name: str) -> None:
    if not enabled():
        return
    with db.connection() as conn:
        conn.execute("DELETE FROM cron_locks WHERE name = %s", (name,))
