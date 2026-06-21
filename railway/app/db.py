"""Central Postgres access — optional connection pooling behind one helper.

The app otherwise opens a brand-new TCP connection for every query (117+ raw
`psycopg.connect()` calls), which exhausts Postgres `max_connections` under any
real concurrency. This module funnels DB access through a single pooled helper.

Rollout safety: pooling is **opt-in** via `DB_POOL_ENABLED=1`. When the flag is
off (the default), `connection()` behaves byte-for-byte like the legacy
`with psycopg.connect(DATABASE_URL) as conn:` pattern, so this can be deployed
with zero behavior change and enabled later in staging. If the flag is on but
`psycopg_pool` is unavailable or the pool fails to open, it falls back to a
direct connection rather than erroring.

Tunables: DB_POOL_MIN_SIZE (default 1), DB_POOL_MAX_SIZE (default 10),
DB_POOL_TIMEOUT seconds to wait for a free connection (default 10).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

log = logging.getLogger(__name__)


def get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def _pool_enabled() -> bool:
    return (os.getenv("DB_POOL_ENABLED") or "").strip().lower() in ("1", "true", "yes")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """Lazily build the process-wide pool. Returns None if it cannot be built."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        url = get_db_url()
        if not url:
            return None
        try:
            from psycopg_pool import ConnectionPool
        except Exception:
            log.warning("DB_POOL_ENABLED set but psycopg_pool is not installed; using direct connections")
            return None
        try:
            _pool = ConnectionPool(
                conninfo=url,
                min_size=_env_int("DB_POOL_MIN_SIZE", 1),
                max_size=_env_int("DB_POOL_MAX_SIZE", 10),
                timeout=_env_int("DB_POOL_TIMEOUT", 10),
                open=True,
            )
            return _pool
        except Exception:
            log.exception("failed to open Postgres connection pool; using direct connections")
            return None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Drop-in for `with psycopg.connect(DATABASE_URL) as conn:`.

    Commits on clean exit, rolls back on exception, and (when pooling is on)
    returns the connection to the pool instead of closing it.
    """
    if _pool_enabled():
        pool = _get_pool()
        if pool is not None:
            with pool.connection() as conn:
                yield conn
            return

    url = get_db_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    with psycopg.connect(url) as conn:
        yield conn


def close_pool() -> None:
    """Close the pool if open (e.g. on shutdown). Safe to call when unset."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            finally:
                _pool = None
