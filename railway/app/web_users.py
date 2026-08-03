"""Postgres-backed users for browser login (admin / client roles)."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import bcrypt
import psycopg
import db
import db_migrate

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
    # Avatar headshot, stored as a small data: URI (resized client-side).
    """
    ALTER TABLE web_users ADD COLUMN IF NOT EXISTS avatar TEXT
    """,
    # Per-client access list for 'standard' users. NULL means a pre-migration
    # row that has not been scoped yet (grandfathered to all-access by the
    # startup backfill); an empty array means "explicitly no access". Only
    # consulted for the 'standard' role.
    """
    ALTER TABLE web_users ADD COLUMN IF NOT EXISTS allowed_client_slugs TEXT[]
    """,
    # Client groups: a named bundle of one-or-more client dashboards. A 'client'
    # user assigned to a group draws its access from the group's slug list
    # instead of a hand-typed client_slug — organization plus a guard against
    # granting the wrong access.
    """
    CREATE TABLE IF NOT EXISTS client_groups (
      id BIGSERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      client_slugs TEXT[] NOT NULL DEFAULT '{}',
      description TEXT,
      is_active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS client_groups_active_name_lower_uq
      ON client_groups (LOWER(name)) WHERE is_active = TRUE
    """,
    # Group membership for 'client' users. NULL = ungrouped (legacy single-slug
    # access via client_slug). Set only for the 'client' role.
    """
    ALTER TABLE web_users ADD COLUMN IF NOT EXISTS group_id BIGINT
      REFERENCES client_groups(id) ON DELETE SET NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS web_users_group_idx ON web_users (group_id)
    """,
    # Timestamp of the user's most recent successful sign-in. NULL until they
    # log in for the first time; surfaced on the admin user list so usage is
    # visible at a glance.
    """
    ALTER TABLE web_users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ
    """,
    # Timestamp of the user's most recent authenticated activity (any request,
    # not just a sign-in). Sessions are long-lived (a 14-day cookie), so a
    # week-old last_login_at can't tell whether the user has actually come back
    # since. This is stamped — throttled to at most once every few minutes per
    # session — on active requests, so the admin list reflects real recency of
    # use rather than only when a fresh login last happened.
    """
    ALTER TABLE web_users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ
    """,
]

# Register this module's schema with the central migration runner. The runner
# applies + records this once at startup as web_users:0001_baseline; the
# read/write helpers no longer run schema DDL themselves (Phase 2).
db_migrate.register(
    [db_migrate.Migration(id="web_users:0001_baseline", statements=tuple(SCHEMA_SQL_STATEMENTS))]
)


def _normalize_slug_list(slugs: list[str] | tuple[str, ...] | None) -> list[str]:
    """Lowercase, strip, drop blanks, and de-dupe a list of client slugs."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in slugs or ():
        slug = (raw or "").strip().lower()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


@dataclass(frozen=True)
class WebUser:
    id: int
    email: str
    role: str
    client_slug: str | None
    is_active: bool
    avatar: str | None = None
    # Client slugs a 'standard' user may access. Empty means no access.
    allowed_client_slugs: tuple[str, ...] = ()
    # Group membership for 'client' users (None = ungrouped / legacy single-slug).
    group_id: int | None = None
    # Resolved slug list of the user's group, if any. Populated via LEFT JOIN in
    # the user queries; drives access for grouped 'client' users.
    group_client_slugs: tuple[str, ...] = ()

    def accessible_client_slugs(self) -> tuple[str, ...]:
        """Every client slug this user may access (empty for admins = all)."""
        if self.role == "standard":
            return self.allowed_client_slugs
        if self.role == "client":
            if self.group_id is not None:
                return self.group_client_slugs
            single = (self.client_slug or "").strip().lower()
            return (single,) if single else ()
        return ()

    def can_access_client(self, slug: str) -> bool:
        if not self.is_active:
            return False
        if self.role == "admin":
            return True
        target = (slug or "").strip().lower()
        if self.role == "standard":
            return target in self.allowed_client_slugs
        if self.role != "client":
            return False
        if self.group_id is not None:
            return target in self.group_client_slugs
        return (self.client_slug or "").strip().lower() == target


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return bool(_get_db_url())


# Schema creation now lives in the central migration runner: db_migrate applies
# web_users:0001_baseline once at startup, before any request is served. The
# read/write helpers below no longer call ensure_schema() (Phase 2) — that used
# to run the full DDL on every authenticated request and deadlocked under
# concurrency (see #297).
#
# ensure_schema() is retained only as a startup safety net (still invoked once
# from main.py alongside the runner). It stays guarded two ways so it can never
# reintroduce that problem: a process-level flag (with a lock, since sync FastAPI
# handlers run in a threadpool) so the DDL runs at most once per process, and a
# transaction-scoped advisory lock shared with the runner so the two startup
# paths serialize on one lock instead of racing on web_users' exclusive locks.
_schema_ready = False
_schema_lock = threading.Lock()
# Shared with the central migration runner so this path and the runner serialize
# on one lock (see db_migrate.SCHEMA_ADVISORY_LOCK_KEY).
_SCHEMA_ADVISORY_LOCK_KEY = db_migrate.SCHEMA_ADVISORY_LOCK_KEY


def ensure_schema() -> bool:
    global _schema_ready
    url = _get_db_url()
    if not url:
        return False
    if _schema_ready:
        return True
    with _schema_lock:
        if _schema_ready:
            return True
        with db.connection() as conn:
            # Only one process runs the migration at a time; the rest block here,
            # then re-run the IF NOT EXISTS statements as cheap no-ops. The lock
            # is released automatically when the transaction commits or rolls back.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_LOCK_KEY,))
            for stmt in SCHEMA_SQL_STATEMENTS:
                conn.execute(stmt)
        _schema_ready = True
    return True


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _row_to_user(row: tuple[Any, ...]) -> WebUser:
    allowed = row[6] if len(row) > 6 and row[6] is not None else ()
    group_id = int(row[7]) if len(row) > 7 and row[7] is not None else None
    group_slugs = row[8] if len(row) > 8 and row[8] is not None else ()
    return WebUser(
        id=int(row[0]),
        email=str(row[1]),
        role=str(row[2]),
        client_slug=str(row[3]) if row[3] is not None else None,
        is_active=bool(row[4]),
        avatar=str(row[5]) if len(row) > 5 and row[5] is not None else None,
        allowed_client_slugs=tuple(str(s) for s in allowed),
        group_id=group_id,
        group_client_slugs=tuple(str(s) for s in group_slugs),
    )


# Shared SELECT column list + JOIN for building a WebUser. The group's slug list
# is folded in so grouped 'client' users resolve their access in one query.
_USER_SELECT = (
    "u.id, u.email, u.role, u.client_slug, u.is_active, u.avatar, "
    "u.allowed_client_slugs, u.group_id, g.client_slugs"
)
_USER_FROM = "FROM web_users u LEFT JOIN client_groups g ON g.id = u.group_id"


def get_user_by_email(email: str) -> WebUser | None:
    if not enabled():
        return None
    normalized = email.strip().lower()
    with db.connection() as conn:
        row = conn.execute(
            f"""
            SELECT {_USER_SELECT}
            {_USER_FROM}
            WHERE LOWER(u.email) = %s AND u.is_active = TRUE
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
    with db.connection() as conn:
        row = conn.execute(
            f"""
            SELECT {_USER_SELECT}
            {_USER_FROM}
            WHERE u.id = %s
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return _row_to_user(row)


def get_password_hash(email: str) -> str | None:
    if not enabled():
        return None
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


def record_login(user_id: int) -> None:
    """Stamp a user's most recent successful sign-in time.

    Also seeds last_seen_at so activity recency is never behind the login it
    started from. Best-effort: a failure here must never block an otherwise-valid
    login, so callers can ignore the return. Only touches active rows."""
    if not enabled():
        return
    try:
        with db.connection() as conn:
            conn.execute(
                "UPDATE web_users SET last_login_at = NOW(), last_seen_at = NOW() "
                "WHERE id = %s AND is_active = TRUE",
                (int(user_id),),
            )
    except Exception:
        pass


def record_activity(user_id: int) -> None:
    """Stamp a user's most recent authenticated activity time.

    Unlike record_login this fires on ordinary authenticated requests, so an
    admin can tell a still-active user (using a long-lived session) apart from
    one who signed in once and never returned. Callers are expected to throttle
    (see web_auth.touch_last_seen) so this is not a per-request write.

    Best-effort: a failure here must never affect the request. Only active
    rows are touched."""
    if not enabled():
        return
    try:
        with db.connection() as conn:
            conn.execute(
                "UPDATE web_users SET last_seen_at = NOW() "
                "WHERE id = %s AND is_active = TRUE",
                (int(user_id),),
            )
    except Exception:
        pass


def list_users(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not enabled():
        return []
    cols = (
        "u.id, u.email, u.role, u.client_slug, u.is_active, u.created_at, "
        "u.avatar, u.allowed_client_slugs, u.group_id, g.name, g.client_slugs, "
        "u.last_login_at, u.last_seen_at"
    )
    where = "" if include_inactive else "WHERE u.is_active = TRUE"
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT {cols} {_USER_FROM} {where} ORDER BY u.role DESC, LOWER(u.email)"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        allowed = row[7] if row[7] is not None else ()
        group_slugs = row[10] if row[10] is not None else ()
        out.append(
            {
                "id": int(row[0]),
                "email": str(row[1]),
                "role": str(row[2]),
                "client_slug": str(row[3]) if row[3] is not None else None,
                "is_active": bool(row[4]),
                "created_at": row[5].isoformat() if row[5] else None,
                "avatar": str(row[6]) if row[6] is not None else None,
                "allowed_client_slugs": [str(s) for s in allowed],
                "group_id": int(row[8]) if row[8] is not None else None,
                "group_name": str(row[9]) if row[9] is not None else None,
                "group_client_slugs": [str(s) for s in group_slugs],
                "last_login_at": row[11].isoformat() if len(row) > 11 and row[11] else None,
                "last_seen_at": row[12].isoformat() if len(row) > 12 and row[12] else None,
            }
        )
    return out


def set_avatar(user_id: int, avatar: str | None) -> bool:
    """Set (or clear, with None) a user's avatar data URI."""
    if not enabled():
        return False
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE web_users
            SET avatar = %s, updated_at = NOW()
            WHERE id = %s AND is_active = TRUE
            """,
            (avatar, user_id),
        )
        return cur.rowcount > 0


def count_admins() -> int:
    if not enabled():
        return 0
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM web_users WHERE role = 'admin' AND is_active = TRUE"
        ).fetchone()
    return int(row[0]) if row else 0


def _resolve_client_scope(
    role: str, client_slug: str | None, group_id: int | None
) -> tuple[str | None, int | None]:
    """Resolve the (client_slug, group_id) to persist for a user of ``role``.

    Only the 'client' role carries either. A group takes precedence over a
    hand-typed slug (the group defines access); a group is validated to exist
    and be active. Ungrouped client users still require a legacy single slug."""
    if role != "client":
        return None, None
    gid = int(group_id) if group_id else None
    if gid is not None:
        grp = get_group(gid)
        if not grp or not grp.get("is_active", True):
            raise ValueError("Selected client group was not found.")
        return None, gid
    slug = (client_slug or "").strip().lower() or None
    if not slug:
        raise ValueError("Pick a client group (or enter a client slug) for client users.")
    return slug, None


def create_user(
    *,
    email: str,
    password: str,
    role: str,
    client_slug: str | None = None,
    allowed_client_slugs: list[str] | None = None,
    group_id: int | None = None,
) -> WebUser:
    if not enabled():
        raise RuntimeError("DATABASE_URL is not set — web users require Postgres.")
    normalized_email = email.strip().lower()
    if not _EMAIL_RE.match(normalized_email):
        raise ValueError("Invalid email address.")
    role = role.strip().lower()
    if role not in ("admin", "client", "standard"):
        raise ValueError("role must be admin, client, or standard.")
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    slug, gid = _resolve_client_scope(role, client_slug, group_id)
    # Only 'standard' users carry a per-client access list; other roles store NULL.
    allowed = _normalize_slug_list(allowed_client_slugs) if role == "standard" else None

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
            new_id = int(inactive[0])
            conn.execute(
                """
                UPDATE web_users
                SET email = %s,
                    password_hash = %s,
                    role = %s,
                    client_slug = %s,
                    allowed_client_slugs = %s,
                    group_id = %s,
                    is_active = TRUE,
                    updated_at = %s
                WHERE id = %s
                """,
                (normalized_email, pw_hash, role, slug, allowed, gid, now, new_id),
            )
        else:
            try:
                row = conn.execute(
                    """
                    INSERT INTO web_users (email, password_hash, role, client_slug, allowed_client_slugs, group_id, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                    RETURNING id
                    """,
                    (normalized_email, pw_hash, role, slug, allowed, gid, now, now),
                ).fetchone()
                new_id = int(row[0])
            except psycopg.errors.UniqueViolation as e:
                raise ValueError("A user with that email already exists.") from e
    created = get_user_record(new_id)
    if created is None:
        raise RuntimeError("User was created but could not be re-read.")
    return created


def set_password(user_id: int, new_password: str) -> bool:
    if not enabled():
        return False
    if len(new_password) < 10:
        raise ValueError("Password must be at least 10 characters.")
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


def set_role(
    user_id: int,
    role: str,
    client_slug: str | None = None,
    allowed_client_slugs: list[str] | None = None,
    group_id: int | None = None,
) -> WebUser | None:
    """Change an active user's role (and client scoping). Returns the updated
    user, or None if no active user matched. Raises ValueError on bad input."""
    if not enabled():
        return None
    role = (role or "").strip().lower()
    if role not in ("admin", "client", "standard"):
        raise ValueError("role must be admin, client, or standard.")
    slug, gid = _resolve_client_scope(role, client_slug, group_id)
    # Only 'standard' users carry a per-client access list; other roles reset to NULL.
    allowed = _normalize_slug_list(allowed_client_slugs) if role == "standard" else None
    with db.connection() as conn:
        row = conn.execute(
            """
            UPDATE web_users
            SET role = %s, client_slug = %s, allowed_client_slugs = %s,
                group_id = %s, updated_at = NOW()
            WHERE id = %s AND is_active = TRUE
            RETURNING id
            """,
            (role, slug, allowed, gid, user_id),
        ).fetchone()
    return get_user_record(int(row[0])) if row else None


def deactivate_user(user_id: int) -> bool:
    if not enabled():
        return False
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


def backfill_standard_all_access(all_slugs: list[str]) -> int:
    """Grandfather pre-migration 'standard' users to all-access.

    Standard users used to see every client. After per-client scoping, an empty
    list means no access. To avoid locking anyone out on rollout, any standard
    user whose access list was never set (NULL) is granted every current client
    slug once; an admin can then trim them down. New/edited standard users store
    an explicit (possibly empty) array and are left untouched (IS NULL guard)."""
    if not enabled():
        return 0
    slugs = _normalize_slug_list(all_slugs)
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE web_users
            SET allowed_client_slugs = %s, updated_at = NOW()
            WHERE role = 'standard' AND allowed_client_slugs IS NULL
            """,
            (slugs,),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Client groups: named bundles of client dashboards that 'client' users join.
# ---------------------------------------------------------------------------


def _group_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    slugs = row[2] if row[2] is not None else ()
    return {
        "id": int(row[0]),
        "name": str(row[1]),
        "client_slugs": [str(s) for s in slugs],
        "description": str(row[3]) if row[3] is not None else None,
        "is_active": bool(row[4]),
        "member_count": int(row[5]) if len(row) > 5 and row[5] is not None else 0,
    }


_GROUP_SELECT = (
    "g.id, g.name, g.client_slugs, g.description, g.is_active, "
    "(SELECT COUNT(*) FROM web_users u WHERE u.group_id = g.id AND u.is_active = TRUE)"
)


def list_groups(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not enabled():
        return []
    where = "" if include_inactive else "WHERE g.is_active = TRUE"
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT {_GROUP_SELECT} FROM client_groups g {where} ORDER BY LOWER(g.name)"
        ).fetchall()
    return [_group_row_to_dict(r) for r in rows]


def get_group(group_id: int) -> dict[str, Any] | None:
    if not enabled():
        return None
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_GROUP_SELECT} FROM client_groups g WHERE g.id = %s",
            (int(group_id),),
        ).fetchone()
    return _group_row_to_dict(row) if row else None


def create_group(
    *, name: str, client_slugs: list[str] | None = None, description: str | None = None
) -> dict[str, Any]:
    if not enabled():
        raise RuntimeError("DATABASE_URL is not set — client groups require Postgres.")
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Group name is required.")
    slugs = _normalize_slug_list(client_slugs)
    desc = (description or "").strip() or None
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        try:
            row = conn.execute(
                """
                INSERT INTO client_groups (name, client_slugs, description, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, TRUE, %s, %s)
                RETURNING id
                """,
                (clean_name, slugs, desc, now, now),
            ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ValueError("A group with that name already exists.") from e
    created = get_group(int(row[0]))
    if created is None:
        raise RuntimeError("Group was created but could not be re-read.")
    return created


def update_group(
    group_id: int,
    *,
    name: str | None = None,
    client_slugs: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    """Update a group's name / dashboards / description. Only non-None fields
    are changed. Returns the updated group, or None if it doesn't exist."""
    if not enabled():
        return None
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Group name cannot be blank.")
        sets.append("name = %s")
        params.append(clean_name)
    if client_slugs is not None:
        sets.append("client_slugs = %s")
        params.append(_normalize_slug_list(client_slugs))
    if description is not None:
        sets.append("description = %s")
        params.append(description.strip() or None)
    if not sets:
        return get_group(group_id)
    sets.append("updated_at = NOW()")
    params.append(int(group_id))
    with db.connection() as conn:
        try:
            row = conn.execute(
                f"UPDATE client_groups SET {', '.join(sets)} "
                "WHERE id = %s AND is_active = TRUE RETURNING id",
                tuple(params),
            ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ValueError("A group with that name already exists.") from e
    return get_group(int(row[0])) if row else None


def delete_group(group_id: int) -> bool:
    """Soft-delete a group. Refuses (returns False) if it still has active
    members — reassign or remove them first so nobody silently loses access."""
    if not enabled():
        return False
    with db.connection() as conn:
        members = conn.execute(
            "SELECT COUNT(*) FROM web_users WHERE group_id = %s AND is_active = TRUE",
            (int(group_id),),
        ).fetchone()
        if members and int(members[0]) > 0:
            return False
        cur = conn.execute(
            "UPDATE client_groups SET is_active = FALSE, updated_at = NOW() "
            "WHERE id = %s AND is_active = TRUE",
            (int(group_id),),
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
