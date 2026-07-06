"""Postgres-backed users for browser login (admin / client roles)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import bcrypt
import psycopg
import db

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS web_users (
      id BIGSERIAL PRIMARY KEY,
      email TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('admin', 'client', 'standard')),
      client_slug TEXT,
      is_active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    DROP INDEX IF EXISTS web_users_email_lower_uq
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS web_users_active_email_lower_uq
      ON web_users (LOWER(email)) WHERE is_active = TRUE
    """,
    """
    CREATE INDEX IF NOT EXISTS web_users_role_idx ON web_users (role)
    """,
    # Widen the role CHECK on tables created before the 'standard' role existed.
    """
    ALTER TABLE web_users DROP CONSTRAINT IF EXISTS web_users_role_check
    """,
    """
    ALTER TABLE web_users ADD CONSTRAINT web_users_role_check
      CHECK (role IN ('admin', 'client', 'standard'))
    """,
]


@dataclass(frozen=True)
class WebUser:
    id: int
    email: str
    role: str
    client_slug: str | None
    is_active: bool

    def can_access_client(self, slug: str) -> bool:
        if not self.is_active:
            return False
        if self.role in ("admin", "standard"):
            return True
        return self.role == "client" and (self.client_slug or "").strip() == slug


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return bool(_get_db_url())


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _row_to_user(row: tuple[Any, ...]) -> WebUser:
    return WebUser(
        id=int(row[0]),
        email=str(row[1]),
        role=str(row[2]),
        client_slug=str(row[3]) if row[3] is not None else None,
        is_active=bool(row[4]),
    )


def get_user_by_email(email: str) -> WebUser | None:
    if not enabled():
        return None
    ensure_schema()
    normalized = email.strip().lower()
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT id, email, role, client_slug, is_active
            FROM web_users
            WHERE LOWER(email) = %s AND is_active = TRUE
            """,
            (normalized,),
        ).fetchone()
    if not row:
        return None
    return _row_to_user(row)


def get_user_by_id(user_id: int) -> WebUser | None:
    record = get_user_record(user_id)
    if not record or not record.is_active:
        return None
    return record


def get_user_record(user_id: int) -> WebUser | None:
    if not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT id, email, role, client_slug, is_active
            FROM web_users
            WHERE id = %s
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return _row_to_user(row)


def get_password_hash(email: str) -> str | None:
    if not enabled():
        return None
    ensure_schema()
    normalized = email.strip().lower()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT password_hash FROM web_users WHERE LOWER(email) = %s AND is_active = TRUE",
            (normalized,),
        ).fetchone()
    return str(row[0]) if row else None


def authenticate(email: str, password: str) -> WebUser | None:
    stored = get_password_hash(email)
    if not stored or not verify_password(password, stored):
        return None
    return get_user_by_email(email)


def list_users(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        if include_inactive:
            rows = conn.execute(
                "SELECT id, email, role, client_slug, is_active, created_at "
                "FROM web_users ORDER BY role DESC, LOWER(email)"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, email, role, client_slug, is_active, created_at "
                "FROM web_users WHERE is_active = TRUE ORDER BY role DESC, LOWER(email)"
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": int(row[0]),
                "email": str(row[1]),
                "role": str(row[2]),
                "client_slug": str(row[3]) if row[3] is not None else None,
                "is_active": bool(row[4]),
                "created_at": row[5].isoformat() if row[5] else None,
            }
        )
    return out


def count_admins() -> int:
    if not enabled():
        return 0
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM web_users WHERE role = 'admin' AND is_active = TRUE"
        ).fetchone()
    return int(row[0]) if row else 0


def create_user(
    *,
    email: str,
    password: str,
    role: str,
    client_slug: str | None = None,
) -> WebUser:
    if not enabled():
        raise RuntimeError("DATABASE_URL is not set — web users require Postgres.")
    normalized_email = email.strip().lower()
    if not _EMAIL_RE.match(normalized_email):
        raise ValueError("Invalid email address.")
    role = role.strip().lower()
    if role not in ("admin", "client", "standard"):
        raise ValueError("role must be admin, client, or standard.")
    slug = (client_slug or "").strip().lower() or None
    if role == "client" and not slug:
        raise ValueError("client_slug is required for client users.")
    if role in ("admin", "standard"):
        slug = None
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")

    ensure_schema()
    now = datetime.now(tz=UTC)
    pw_hash = hash_password(password)
    with db.connection() as conn:
        inactive = conn.execute(
            """
            SELECT id
            FROM web_users
            WHERE LOWER(email) = %s AND is_active = FALSE
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_email,),
        ).fetchone()
        if inactive:
            row = conn.execute(
                """
                UPDATE web_users
                SET email = %s,
                    password_hash = %s,
                    role = %s,
                    client_slug = %s,
                    is_active = TRUE,
                    updated_at = %s
                WHERE id = %s
                RETURNING id, email, role, client_slug, is_active
                """,
                (normalized_email, pw_hash, role, slug, now, int(inactive[0])),
            ).fetchone()
        else:
            try:
                row = conn.execute(
                    """
                    INSERT INTO web_users (email, password_hash, role, client_slug, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                    RETURNING id, email, role, client_slug, is_active
                    """,
                    (normalized_email, pw_hash, role, slug, now, now),
                ).fetchone()
            except psycopg.errors.UniqueViolation as e:
                raise ValueError("A user with that email already exists.") from e
    return _row_to_user(row)


def set_password(user_id: int, new_password: str) -> bool:
    if not enabled():
        return False
    if len(new_password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    ensure_schema()
    pw_hash = hash_password(new_password)
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE web_users
            SET password_hash = %s, updated_at = NOW()
            WHERE id = %s AND is_active = TRUE
            """,
            (pw_hash, user_id),
        )
        return cur.rowcount > 0


def deactivate_user(user_id: int) -> bool:
    if not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE web_users
            SET is_active = FALSE, updated_at = NOW()
            WHERE id = %s AND is_active = TRUE
            """,
            (user_id,),
        )
        return cur.rowcount > 0


def bootstrap_admin_from_env() -> WebUser | None:
    """Create the first admin from AUTH_BOOTSTRAP_ADMIN_* when no admins exist."""
    email = (os.getenv("AUTH_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    password = os.getenv("AUTH_BOOTSTRAP_ADMIN_PASSWORD") or ""
    if not email or not password:
        return None
    if count_admins() > 0:
        return None
    try:
        return create_user(email=email, password=password, role="admin")
    except ValueError:
        return get_user_by_email(email)
