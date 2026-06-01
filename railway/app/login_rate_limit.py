"""Rate limit failed sign-in attempts by IP (and email on failure)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

import web_users

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
    with psycopg.connect(_get_db_url()) as conn:
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


def check_login_allowed(*, ip: str | None, email: str | None = None) -> RateLimitStatus:
    if not enabled():
        return RateLimitStatus(allowed=True)
    try:
        ensure_schema()
        now = datetime.now(tz=UTC)
        with psycopg.connect(_get_db_url()) as conn:
            ip_status = _check_bucket(conn, _bucket_key_ip(ip), now)
            if not ip_status.allowed:
                return ip_status
            if email:
                email_status = _check_bucket(conn, _bucket_key_email(email), now)
                if not email_status.allowed:
                    return email_status
        return RateLimitStatus(allowed=True)
    except Exception:
        return RateLimitStatus(allowed=True)


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
        with psycopg.connect(_get_db_url()) as conn:
            _record_failure_conn(conn, _bucket_key_ip(ip), now)
            _record_failure_conn(conn, _bucket_key_email(email), now)
    except Exception:
        pass


def clear_login_limits(*, ip: str | None, email: str) -> None:
    if not enabled():
        return
    try:
        ensure_schema()
        with psycopg.connect(_get_db_url()) as conn:
            conn.execute(
                "DELETE FROM login_rate_buckets WHERE bucket_key = ANY(%s)",
                ([_bucket_key_ip(ip), _bucket_key_email(email)],),
            )
    except Exception:
        pass
