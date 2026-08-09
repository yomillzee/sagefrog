"""Project-wide rate governor for the Google Tag Manager API.

GTM API v2 is quota'd far more tightly than the rest of the Google APIs this app
talks to: **0.25 queries per second, enforced per *project*** on a 100-second
sliding window (25 requests / 100s), plus 10,000 requests per project per day.
Two consequences drive this module's design:

1. The budget is **shared by everything** — every client, every code path (the
   container fan-out, the live-version read, the connector test), and every
   Railway worker process draw on the same 25-per-100s allowance. Pacing calls
   inside one fan-out, or de-duplicating per refresh token, cannot keep the
   project under quota because the other callers are invisible to it. So the
   governor is global (one bucket for the whole project) and its state lives in
   Postgres, where every worker sees the same counter.

2. Exceeding it does **not** return 429. Google's quota layer answers with
   **403 Forbidden** carrying a ``rateLimitExceeded``/``userRateLimitExceeded``
   reason, which is easily mistaken for a scope/permission failure. Callers
   (see ``gtm_service._is_rate_limited``) must classify that before deciding
   whether to retry.

Shape: a token bucket of ``GTM_QUOTA_BURST`` tokens refilling at
``GTM_QUOTA_QPS`` per second. Held slightly under the documented ceiling by
default so a burst we don't control (a co-tenant script, a retry storm) doesn't
push the project over. ``acquire()`` blocks until a token is free, and a
circuit breaker (``note_rate_limited``) parks *all* callers for a cooldown once
Google has actually rejected us — continuing to spend quota against a limit
we've already hit only lengthens the outage.

Degradation: Postgres is the shared source of truth, but if it is unset or
unreachable the governor falls back to a per-process bucket rather than failing
open. A per-process limit is wrong-by-a-factor-of-workers, but it is far closer
to the truth than no limit at all.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import db

log = logging.getLogger(__name__)

# One bucket for the whole project — see module docstring.
_BUCKET_KEY = "tagmanager.googleapis.com"

# Google's documented ceiling, for reference in the defaults below:
#   0.25 QPS per project, enforced as 25 requests per 100-second sliding window.
_DOCUMENTED_QPS = 0.25
_DOCUMENTED_BURST = 25

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS gtm_quota_bucket (
      bucket_key TEXT PRIMARY KEY,
      tokens DOUBLE PRECISION NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL,
      cooldown_until TIMESTAMPTZ
    )
    """,
]


class GTMRateLimited(RuntimeError):
    """GTM quota is exhausted and waiting for it would take too long.

    Carries ``retry_after`` (seconds) so callers can surface a concrete "try
    again in N seconds" instead of a bare failure.
    """

    def __init__(self, message: str, *, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after))


# ── Tunables ──────────────────────────────────────────────────────────────────

def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def refill_per_second() -> float:
    """Sustained request rate. Defaults to the documented 0.25 QPS."""
    return _env_float("GTM_QUOTA_QPS", _DOCUMENTED_QPS, minimum=0.01)


def capacity() -> float:
    """Burst allowance. Defaults to 20 of the documented 25-request window, so a
    request we don't govern (a retry, another tenant on the project) still has
    room before Google starts rejecting."""
    return _env_float("GTM_QUOTA_BURST", 20.0, minimum=1.0)


def max_wait_seconds() -> float:
    """Longest an individual caller will block waiting for a token. Beyond this
    the caller gets ``GTMRateLimited`` and should serve cached/stale data rather
    than hold a web request open."""
    return _env_float("GTM_QUOTA_MAX_WAIT_SECONDS", 25.0, minimum=0.0)


def cooldown_seconds() -> float:
    """How long to park every caller after Google actually rejects us. Roughly
    the sliding window, so the window has drained before we try again."""
    return _env_float("GTM_QUOTA_COOLDOWN_SECONDS", 60.0, minimum=1.0)


def _db_enabled() -> bool:
    return bool(db.get_db_url())


_schema_ready = False


def ensure_schema() -> bool:
    global _schema_ready
    if not _db_enabled():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    _schema_ready = True
    return True


def _ensure_schema_once() -> None:
    """Schema check for the request path — the DDL is idempotent but it costs a
    round trip, and this runs before every metered GTM call."""
    if not _schema_ready:
        ensure_schema()


# ── In-process fallback bucket ────────────────────────────────────────────────

_mem_lock = threading.Lock()
_mem_state: dict[str, float | datetime | None] = {}


def _mem_reset() -> None:
    _mem_state.clear()


def _refill(tokens: float, updated_at: datetime, now: datetime) -> float:
    elapsed = max(0.0, (now - updated_at).total_seconds())
    return min(capacity(), tokens + elapsed * refill_per_second())


def _wait_for_next_token(tokens: float) -> float:
    """Seconds until the bucket holds a full token."""
    return max(0.0, (1.0 - tokens) / refill_per_second())


def _take_token_mem(now: datetime) -> float:
    """Take one token from the process-local bucket.

    Returns 0.0 when a token was taken, else the seconds to wait before
    retrying. Raises ``GTMRateLimited`` while the breaker is tripped.
    """
    with _mem_lock:
        cooldown_until = _mem_state.get("cooldown_until")
        if isinstance(cooldown_until, datetime) and cooldown_until > now:
            raise GTMRateLimited(
                _cooldown_message((cooldown_until - now).total_seconds()),
                retry_after=(cooldown_until - now).total_seconds(),
            )

        tokens = _mem_state.get("tokens")
        updated_at = _mem_state.get("updated_at")
        if not isinstance(tokens, float) or not isinstance(updated_at, datetime):
            tokens, updated_at = capacity(), now

        tokens = _refill(tokens, updated_at, now)
        if tokens >= 1.0:
            _mem_state["tokens"] = tokens - 1.0
            _mem_state["updated_at"] = now
            return 0.0

        # Persist the refill so the next caller doesn't recompute from a stale
        # timestamp, but don't consume — this caller has to wait its turn.
        _mem_state["tokens"] = tokens
        _mem_state["updated_at"] = now
        return _wait_for_next_token(tokens)


def _note_rate_limited_mem(until: datetime) -> None:
    with _mem_lock:
        current = _mem_state.get("cooldown_until")
        if not isinstance(current, datetime) or until > current:
            _mem_state["cooldown_until"] = until
        # We demonstrably overspent — drain the bucket so the refill clock, not
        # a stale token balance, gates the first call after the cooldown.
        _mem_state["tokens"] = 0.0
        _mem_state["updated_at"] = until


def _clear_cooldown_mem() -> None:
    with _mem_lock:
        _mem_state["cooldown_until"] = None


# ── Postgres-backed shared bucket ─────────────────────────────────────────────

def _take_token_db(now: datetime) -> float:
    """Shared-bucket counterpart to ``_take_token_mem``.

    ``SELECT … FOR UPDATE`` serialises concurrent workers on the bucket row, so
    two processes cannot both read "1 token left" and both spend it.
    """
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO gtm_quota_bucket (bucket_key, tokens, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (bucket_key) DO NOTHING
            """,
            (_BUCKET_KEY, capacity(), now),
        )
        row = conn.execute(
            """
            SELECT tokens, updated_at, cooldown_until
            FROM gtm_quota_bucket
            WHERE bucket_key = %s
            FOR UPDATE
            """,
            (_BUCKET_KEY,),
        ).fetchone()
        if not row:
            # Lost a race with a concurrent delete; treat as a fresh bucket.
            return 0.0

        tokens, updated_at, cooldown_until = float(row[0]), row[1], row[2]
        if cooldown_until and cooldown_until > now:
            remaining = (cooldown_until - now).total_seconds()
            raise GTMRateLimited(_cooldown_message(remaining), retry_after=remaining)

        tokens = _refill(tokens, updated_at, now)
        acquired = tokens >= 1.0
        conn.execute(
            """
            UPDATE gtm_quota_bucket
            SET tokens = %s, updated_at = %s
            WHERE bucket_key = %s
            """,
            (tokens - 1.0 if acquired else tokens, now, _BUCKET_KEY),
        )
        return 0.0 if acquired else _wait_for_next_token(tokens)


def _note_rate_limited_db(until: datetime) -> None:
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO gtm_quota_bucket (bucket_key, tokens, updated_at, cooldown_until)
            VALUES (%s, 0, %s, %s)
            ON CONFLICT (bucket_key) DO UPDATE SET
              tokens = 0,
              updated_at = EXCLUDED.updated_at,
              cooldown_until = GREATEST(
                COALESCE(gtm_quota_bucket.cooldown_until, EXCLUDED.cooldown_until),
                EXCLUDED.cooldown_until
              )
            """,
            (_BUCKET_KEY, until, until),
        )


def _clear_cooldown_db() -> None:
    with db.connection() as conn:
        conn.execute(
            "UPDATE gtm_quota_bucket SET cooldown_until = NULL WHERE bucket_key = %s",
            (_BUCKET_KEY,),
        )


# ── Public API ────────────────────────────────────────────────────────────────

def _cooldown_message(remaining: float) -> str:
    secs = max(1, int(round(remaining)))
    return (
        "Google Tag Manager is rate-limiting this project, so Tag Manager "
        f"requests are paused for another {secs}s to let the quota recover."
    )


def _take_token(now: datetime) -> float:
    """Take a token from the shared bucket, falling back to the local one."""
    if not _db_enabled():
        return _take_token_mem(now)
    try:
        _ensure_schema_once()
        return _take_token_db(now)
    except GTMRateLimited:
        raise
    except Exception:
        log.warning(
            "GTM quota bucket unavailable in Postgres; using per-process limiter",
            exc_info=True,
        )
        return _take_token_mem(now)


def acquire(*, timeout: float | None = None) -> None:
    """Block until this process may issue one GTM API request.

    Raises ``GTMRateLimited`` if the breaker is tripped, or if a token will not
    be free within ``timeout`` (default ``max_wait_seconds()``) — callers should
    then serve cached data instead of stalling a request behind the quota.
    """
    budget = max_wait_seconds() if timeout is None else max(0.0, float(timeout))
    deadline = time.monotonic() + budget

    while True:
        wait = _take_token(datetime.now(tz=UTC))
        if wait <= 0.0:
            return
        remaining = deadline - time.monotonic()
        if wait > remaining:
            raise GTMRateLimited(
                "Google Tag Manager's per-project quota (0.25 requests/second) is "
                f"saturated; the next request slot is about {int(round(wait))}s away.",
                retry_after=wait,
            )
        time.sleep(wait)


def note_rate_limited(retry_after: float | None = None) -> None:
    """Trip the breaker after Google actually rejected a request for quota.

    Every caller is parked for ``max(retry_after, cooldown_seconds())`` — once
    the project is over its window, further requests only extend the outage.
    """
    hold = max(float(retry_after or 0.0), cooldown_seconds())
    until = datetime.now(tz=UTC) + timedelta(seconds=hold)
    log.warning("GTM quota exhausted; pausing all GTM requests for %.0fs", hold)
    if _db_enabled():
        try:
            _ensure_schema_once()
            _note_rate_limited_db(until)
        except Exception:
            log.warning("could not persist GTM cooldown; holding locally", exc_info=True)
    _note_rate_limited_mem(until)


def note_success() -> None:
    """Clear a cooldown after a request succeeds (the quota has recovered)."""
    if isinstance(_mem_state.get("cooldown_until"), datetime):
        _clear_cooldown_mem()
        if _db_enabled():
            try:
                _clear_cooldown_db()
            except Exception:
                log.debug("could not clear GTM cooldown in Postgres", exc_info=True)


def cooldown_remaining() -> float:
    """Seconds left on the breaker, 0.0 when requests are allowed.

    Read-only: never consumes a token, so callers can cheaply decide to serve
    cached data before entering the request path.
    """
    now = datetime.now(tz=UTC)
    if _db_enabled():
        try:
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT cooldown_until FROM gtm_quota_bucket WHERE bucket_key = %s",
                    (_BUCKET_KEY,),
                ).fetchone()
            if row and row[0] and row[0] > now:
                return (row[0] - now).total_seconds()
            return 0.0
        except Exception:
            log.debug("could not read GTM cooldown from Postgres", exc_info=True)

    cooldown_until = _mem_state.get("cooldown_until")
    if isinstance(cooldown_until, datetime) and cooldown_until > now:
        return (cooldown_until - now).total_seconds()
    return 0.0


def reset() -> None:
    """Drop all governor state. For tests and admin recovery."""
    _mem_reset()
    if _db_enabled():
        try:
            with db.connection() as conn:
                conn.execute("DELETE FROM gtm_quota_bucket WHERE bucket_key = %s", (_BUCKET_KEY,))
        except Exception:
            log.debug("could not reset GTM quota bucket in Postgres", exc_info=True)
