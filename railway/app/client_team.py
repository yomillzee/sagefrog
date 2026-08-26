"""The Sagefrog team assigned to each client account.

Who at the agency actually works on an account. Until now the portal only knew
who *may* open a dashboard (``web_users.allowed_client_slugs`` for the
'standard' role, and admins who can open everything) — which is an access list,
not a staffing list. An admin can open all forty accounts; that does not make
them the person who should hear about a comment left on one of them.

This module is that missing list: a plain ``client_slug -> user_email`` mapping,
edited from the account's kebab menu on ``/admin/clients`` and read by
``page_comments`` to decide whose notification inbox a comment lands in.

**Unset falls back to access, it does not mean "nobody".** An account nobody has
staffed yet resolves to the agency users who can already reach it (see
``default_team``), so comments route sensibly on day one and the admin picker
opens pre-ticked with that same set — saving it is what turns the inferred team
into an explicit one. ``list_team`` returns only what was explicitly saved;
``resolve_team`` is the one to ask when you need "who should hear about this".

Storage mirrors ``client_notes`` / ``feature_requests`` — one table, raw SQL via
``db`` v3, no ORM.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import db
import db_migrate
import web_users

# Agency roles. A 'client'-role login is never on a team — these are internal
# staffing rows, and a client user must never receive an internal comment.
AGENCY_ROLES = ("admin", "standard")

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_team_members (
      client_slug TEXT NOT NULL,
      user_email  TEXT NOT NULL,
      added_by    TEXT,
      added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (client_slug, user_email)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS client_team_members_email_idx
      ON client_team_members (user_email)
    """,
]

db_migrate.register(
    [db_migrate.Migration(id="client_team:0001_baseline", statements=tuple(SCHEMA_SQL_STATEMENTS))]
)

_schema_ready = False
_schema_lock = threading.Lock()


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    """Idempotent DDL, run at most once per process (the runner does it at boot)."""
    global _schema_ready
    if not _get_db_url():
        return False
    if _schema_ready:
        return True
    with _schema_lock:
        if _schema_ready:
            return True
        with db.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s)", (db_migrate.SCHEMA_ADVISORY_LOCK_KEY,)
            )
            for stmt in SCHEMA_SQL_STATEMENTS:
                conn.execute(stmt)
        _schema_ready = True
    return True


def _clean_slug(client_slug: str | None) -> str:
    return (client_slug or "").strip().lower()


def _clean_email(email: str | None) -> str:
    return (email or "").strip().lower()


def agency_users() -> list[dict[str, Any]]:
    """Every active agency login, in the order the admin roster shows them."""
    if not enabled():
        return []
    return [u for u in web_users.list_users() if u.get("role") in AGENCY_ROLES]


def default_team_map(users: list[dict[str, Any]] | None = None) -> dict[str, tuple[str, ...]]:
    """Fallback team for every account, from the access roster in a single pass.

    The agency users already scoped to an account — i.e. 'standard' users whose
    access list names it. Admins are deliberately left out: their access is
    blanket, so including them would make every admin the default recipient for
    every account, which is exactly the noise an explicit team exists to avoid.

    Pass ``users`` when you already hold the roster (the admin grid does) to save
    a query per account.
    """
    roster = agency_users() if users is None else users
    out: dict[str, list[str]] = {}
    for user in roster:
        if user.get("role") != "standard":
            continue
        email = _clean_email(user.get("email"))
        if not email:
            continue
        for raw in user.get("allowed_client_slugs") or []:
            slug = _clean_slug(raw)
            if slug:
                out.setdefault(slug, []).append(email)
    return {slug: tuple(emails) for slug, emails in out.items()}


def default_team(client_slug: str) -> tuple[str, ...]:
    """``default_team_map`` for one account."""
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return ()
    return default_team_map().get(slug, ())


def list_team(client_slug: str) -> tuple[str, ...]:
    """Emails explicitly saved as this account's team — empty when unset."""
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return ()
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT user_email FROM client_team_members WHERE client_slug = %s "
            "ORDER BY user_email",
            (slug,),
        ).fetchall()
    return tuple(_clean_email(str(r[0])) for r in rows if r and r[0])


def resolve_team(client_slug: str) -> tuple[str, ...]:
    """Who should hear about this account — the explicit team, else the fallback.

    This is the routing question ``page_comments`` asks. Rows are filtered back
    through the live user roster so a deactivated or demoted login stops
    receiving notifications without anyone having to prune the team list.
    """
    slug = _clean_slug(client_slug)
    if not slug or not enabled():
        return ()
    saved = list_team(slug)
    candidates = saved or default_team(slug)
    if not candidates:
        return ()
    active = {_clean_email(u.get("email")) for u in agency_users()}
    return tuple(e for e in candidates if e in active)


def set_team(
    client_slug: str, emails: list[str] | tuple[str, ...] | None, *, updated_by: str | None = None
) -> tuple[str, ...]:
    """Replace an account's team with ``emails``. Returns what was stored.

    Only active agency logins are accepted — anything else (a client-role login,
    a typo, a deactivated account) is dropped rather than saved and silently
    never notified.
    """
    slug = _clean_slug(client_slug)
    if not slug:
        raise ValueError("A client slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to assign a client team.")
    ensure_schema()
    allowed = {_clean_email(u.get("email")) for u in agency_users()}
    wanted: list[str] = []
    for raw in emails or ():
        email = _clean_email(raw)
        if email and email in allowed and email not in wanted:
            wanted.append(email)
    actor = (updated_by or "").strip() or None
    with db.connection() as conn:
        conn.execute("DELETE FROM client_team_members WHERE client_slug = %s", (slug,))
        for email in wanted:
            conn.execute(
                "INSERT INTO client_team_members (client_slug, user_email, added_by) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (slug, email, actor),
            )
    return tuple(wanted)


def teams_by_slug() -> dict[str, tuple[str, ...]]:
    """Every explicitly saved team, keyed by slug — one query for the admin grid."""
    if not enabled():
        return {}
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug, user_email FROM client_team_members "
            "ORDER BY client_slug, user_email"
        ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(_clean_slug(str(row[0])), []).append(_clean_email(str(row[1])))
    return {slug: tuple(emails) for slug, emails in out.items()}
