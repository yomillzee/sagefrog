"""Rate limit failed sign-in attempts by IP (and email on failure)."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import db

import web_users

log = logging.getLogger(__name__)

# In-memory fallback used when Postgres is unreachable. Brute-force protection
# must NOT silently disappear when the DB is degraded (that is exactly when an
# attacker would push). This is per-process, but failing to a local limiter is
# far safer than failing open. State here is best-effort and ephemeral.
_MEM_LOCK = threading.Lock()
_MEM_BUCKETS: dict[str, dict] = {}

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS login_rate_buckets (
      bucket_key TEXT PRIMARY KEY,
      failed_count INT NOT NULL DEFAULT 0,
      window_start TIMESTAMPTZ NOT NULL,
      locked_until TIMESTAMPTZ
    )
    """,
]


@dataclass(frozen=True)
class RateLimitStatus:
    allowed: bool
    retry_after_seconds: int = 0
    message: str | None = None


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def max_failures() -> int:
    raw = (os.getenv("AUTH_LOGIN_MAX_FAILURES") or "5").strip()
    try:
        return max(3, int(raw))
    except ValueError:
        return 5


def window_seconds() -> int:
    raw = (os.getenv("AUTH_LOGIN_WINDOW_SECONDS") or "900").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 900


def lockout_seconds() -> int:
    raw = (os.getenv("AUTH_LOGIN_LOCKOUT_SECONDS") or "900").strip()
    try:
        return max(60, int(raw))
    except ValueError:
        return 900


def ensure_schema() -> bool:
    if not _get_db_url():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _bucket_key_ip(ip: str | None) -> str:
    host = (ip or "unknown").strip()[:128]
    return f"ip:{host}"


def _bucket_key_email(email: str) -> str:
    return f"email:{email.strip().lower()[:254]}"


def _check_bucket(conn, bucket_key: str, now: datetime) -> RateLimitStatus:
    row = conn.execute(
        """
        SELECT failed_count, window_start, locked_until
        FROM login_rate_buckets
        WHERE bucket_key = %s
        """,
        (bucket_key,),
    ).fetchone()
    if not row:
        return RateLimitStatus(allowed=True)

    failed_count = int(row[0])
    window_start = row[1]
    locked_until = row[2]

    if locked_until and locked_until > now:
        retry = int((locked_until - now).total_seconds())
        mins = max(1, (retry + 59) // 60)
        return RateLimitStatus(
            allowed=False,
            retry_after_seconds=retry,
            message=f"Too many failed attempts. Try again in about {mins} minutes.",
        )

    if window_start and (now - window_start).total_seconds() > window_seconds():
        conn.execute("DELETE FROM login_rate_buckets WHERE bucket_key = %s", (bucket_key,))
        return RateLimitStatus(allowed=True)

    if failed_count >= max_failures():
        lock_until = now + timedelta(seconds=lockout_seconds())
        conn.execute(
            "UPDATE login_rate_buckets SET locked_until = %s WHERE bucket_key = %s",
            (lock_until, bucket_key),
        )
        retry = lockout_seconds()
        mins = max(1, (retry + 59) // 60)
        return RateLimitStatus(
            allowed=False,
            retry_after_seconds=retry,
            message=f"Too many failed attempts. Try again in about {mins} minutes.",
        )

    return RateLimitStatus(allowed=True)


def _mem_check(bucket_key: str, now: datetime) -> RateLimitStatus:
    with _MEM_LOCK:
        row = _MEM_BUCKETS.get(bucket_key)
        if not row:
            return RateLimitStatus(allowed=True)
        locked_until = row.get("locked_until")
        if locked_until and locked_until > now:
            retry = int((locked_until - now).total_seconds())
            mins = max(1, (retry + 59) // 60)
            return RateLimitStatus(
                allowed=False,
                retry_after_seconds=retry,
                message=f"Too many failed attempts. Try again in about {mins} minutes.",
            )
        window_start = row.get("window_start")
        if window_start and (now - window_start).total_seconds() > window_seconds():
            _MEM_BUCKETS.pop(bucket_key, None)
            return RateLimitStatus(allowed=True)
        if int(row.get("failed_count", 0)) >= max_failures():
            lock_until = now + timedelta(seconds=lockout_seconds())
            row["locked_until"] = lock_until
            retry = lockout_seconds()
            mins = max(1, (retry + 59) // 60)
            return RateLimitStatus(
                allowed=False,
                retry_after_seconds=retry,
                message=f"Too many failed attempts. Try again in about {mins} minutes.",
            )
    return RateLimitStatus(allowed=True)


def _mem_record_failure(bucket_key: str, now: datetime) -> None:
    with _MEM_LOCK:
        row = _MEM_BUCKETS.get(bucket_key)
        if not row:
            _MEM_BUCKETS[bucket_key] = {"failed_count": 1, "window_start": now, "locked_until": None}
            return
        window_start = row.get("window_start")
        if window_start and (now - window_start).total_seconds() > window_seconds():
            row.update({"failed_count": 1, "window_start": now, "locked_until": None})
            return
        new_count = int(row.get("failed_count", 0)) + 1
        row["failed_count"] = new_count
        if new_count >= max_failures():
            row["locked_until"] = now + timedelta(seconds=lockout_seconds())


def _mem_clear(keys: list[str]) -> None:
    with _MEM_LOCK:
        for key in keys:
            _MEM_BUCKETS.pop(key, None)


def _check_login_allowed_mem(ip: str | None, email: str | None) -> RateLimitStatus:
    now = datetime.now(tz=UTC)
    ip_status = _mem_check(_bucket_key_ip(ip), now)
    if not ip_status.allowed:
        return ip_status
    if email:
        email_status = _mem_check(_bucket_key_email(email), now)
        if not email_status.allowed:
            return email_status
    return RateLimitStatus(allowed=True)


def check_login_allowed(*, ip: str | None, email: str | None = None) -> RateLimitStatus:
    if not enabled():
        return RateLimitStatus(allowed=True)
    try:
        ensure_schema()
        now = datetime.now(tz=UTC)
        with db.connection() as conn:
            ip_status = _check_bucket(conn, _bucket_key_ip(ip), now)
            if not ip_status.allowed:
                return ip_status
            if email:
                email_status = _check_bucket(conn, _bucket_key_email(email), now)
                if not email_status.allowed:
                    return email_status
        return RateLimitStatus(allowed=True)
    except Exception:
        # Fail to the in-memory limiter instead of failing open.
        log.warning("login rate limiter DB unavailable; using in-memory fallback", exc_info=True)
        return _check_login_allowed_mem(ip, email)


def _record_failure_conn(conn, bucket_key: str, now: datetime) -> None:
    row = conn.execute(
        """
        SELECT failed_count, window_start, locked_until
        FROM login_rate_buckets
        WHERE bucket_key = %s
        """,
        (bucket_key,),
    ).fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO login_rate_buckets (bucket_key, failed_count, window_start, locked_until)
            VALUES (%s, 1, %s, NULL)
            """,
            (bucket_key, now),
        )
        return

    failed_count = int(row[0])
    window_start = row[1]
    if window_start and (now - window_start).total_seconds() > window_seconds():
        conn.execute(
            """
            UPDATE login_rate_buckets
            SET failed_count = 1, window_start = %s, locked_until = NULL
            WHERE bucket_key = %s
            """,
            (now, bucket_key),
        )
        return

    new_count = failed_count + 1
    locked_until = None
    if new_count >= max_failures():
        locked_until = now + timedelta(seconds=lockout_seconds())
    conn.execute(
        """
        UPDATE login_rate_buckets
        SET failed_count = %s, locked_until = %s
        WHERE bucket_key = %s
        """,
        (new_count, locked_until, bucket_key),
    )


def record_login_failure(*, ip: str | None, email: str) -> None:
    if not enabled():
        return
    try:
        ensure_schema()
        now = datetime.now(tz=UTC)
        with db.connection() as conn:
            _record_failure_conn(conn, _bucket_key_ip(ip), now)
            _record_failure_conn(conn, _bucket_key_email(email), now)
    except Exception:
        log.warning("login rate limiter DB unavailable; recording failure in memory", exc_info=True)
        now = datetime.now(tz=UTC)
        _mem_record_failure(_bucket_key_ip(ip), now)
        _mem_record_failure(_bucket_key_email(email), now)


def clear_login_limits(*, ip: str | None, email: str) -> None:
    if not enabled():
        return
    try:
        ensure_schema()
        with db.connection() as conn:
            conn.execute(
                "DELETE FROM login_rate_buckets WHERE bucket_key = ANY(%s)",
                ([_bucket_key_ip(ip), _bucket_key_email(email)],),
            )
    except Exception:
        pass
    finally:
        # Always clear the in-memory fallback so a successful login resets it.
        _mem_clear([_bucket_key_ip(ip), _bucket_key_email(email)])
