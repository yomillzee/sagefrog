"""Client-specific presenter notepads.

Lightweight notes the agency keeps *per client* — talking points, reminders,
and context to have on hand while presenting a dashboard live to that client.
They are deliberately client-scoped (not user-scoped): every agency user sees
the same set of notepads for a given client, so notes survive whoever happens
to be driving the presentation.

These are internal agency notes, not client-facing content. The routes that
expose them gate on an agency role (admin / standard); a ``client``-role user
never sees the floating pad or its data. Editing is therefore agency-only too,
which is what "can only be updated by the Sagefrog user" asks for.

Storage mirrors ``admin_dev_notes`` — a single table, raw SQL via ``db`` v3,
idempotent ``ensure_schema()``, no ORM. Every read/write is scoped by
``client_slug`` so one client's notes can never leak into another's dashboard.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import db
import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_notepads (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      title TEXT NOT NULL,
      body TEXT NOT NULL DEFAULT '',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT,
      updated_by TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS client_notepads_client_updated_idx
      ON client_notepads (client_slug, updated_at DESC)
    """,
]

# Keep titles/bodies bounded so a runaway paste can't bloat a row.
MAX_TITLE_LEN = 200
MAX_BODY_LEN = 100_000


def default_title(now: datetime | None = None) -> str:
    """Default title for a new note: a nicely formatted current date.

    Creating a note with no title stamps it with today's date (e.g.
    "July 18, 2026") so notepads read as a dated running log out of the box,
    while staying fully editable. The widget prefills the same thing client-side
    from the browser's local date; this is the server-side fallback (UTC) for
    the popup window and any blank-title save.
    """
    now = now or datetime.now(tz=UTC)
    return f"{now:%B} {now.day}, {now.year}"


@dataclass(frozen=True)
class Notepad:
    id: int
    client_slug: str
    title: str
    body: str
    created_at: str | None
    updated_at: str | None
    created_by: str | None
    updated_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "client_slug": self.client_slug,
            "title": self.title,
            "body": self.body,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }

    def summary(self) -> dict[str, Any]:
        """Lightweight shape for the switcher dropdown (no body)."""
        return {
            "id": self.id,
            "title": self.title,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    """Notepads require the same Postgres-backed user layer as the dashboards."""
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _clean_slug(client_slug: str) -> str:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    return slug


def _clean_title(title: str | None) -> str:
    clean = (title or "").strip()
    if not clean:
        clean = default_title()
    return clean[:MAX_TITLE_LEN]


def _clean_body(body: str | None) -> str:
    return (body or "")[:MAX_BODY_LEN]


def _row_to_notepad(row: tuple[Any, ...]) -> Notepad:
    created = row[4]
    updated = row[5]
    return Notepad(
        id=int(row[0]),
        client_slug=str(row[1] or ""),
        title=str(row[2] or ""),
        body=str(row[3] or ""),
        created_at=created.isoformat() if created else None,
        updated_at=updated.isoformat() if updated else None,
        created_by=str(row[6]) if row[6] else None,
        updated_by=str(row[7]) if row[7] else None,
    )


_SELECT_COLS = (
    "id, client_slug, title, body, created_at, updated_at, created_by, updated_by"
)


def list_notepads(client_slug: str, *, limit: int = 200) -> list[Notepad]:
    if not enabled():
        return []
    ensure_schema()
    slug = _clean_slug(client_slug)
    limit = max(1, min(int(limit), 500))
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM client_notepads
            WHERE client_slug = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            (slug, limit),
        ).fetchall()
    return [_row_to_notepad(row) for row in rows]


def get_notepad(client_slug: str, notepad_id: int) -> Notepad | None:
    if not enabled():
        return None
    ensure_schema()
    slug = _clean_slug(client_slug)
    with db.connection() as conn:
        row = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM client_notepads
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(notepad_id)),
        ).fetchone()
    return _row_to_notepad(row) if row else None


def create_notepad(
    client_slug: str,
    *,
    title: str | None = None,
    body: str | None = None,
    created_by: str | None = None,
) -> Notepad:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save notes.")
    ensure_schema()
    slug = _clean_slug(client_slug)
    now = datetime.now(tz=UTC)
    author = (created_by or "").strip() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO client_notepads
              (client_slug, title, body, created_at, updated_at, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING {_SELECT_COLS}
            """,
            (slug, _clean_title(title), _clean_body(body), now, now, author, author),
        ).fetchone()
    assert row
    return _row_to_notepad(row)


def update_notepad(
    client_slug: str,
    notepad_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    updated_by: str | None = None,
) -> Notepad:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save notes.")
    ensure_schema()
    slug = _clean_slug(client_slug)
    now = datetime.now(tz=UTC)
    editor = (updated_by or "").strip() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            UPDATE client_notepads
            SET title = %s, body = %s, updated_at = %s, updated_by = %s
            WHERE client_slug = %s AND id = %s
            RETURNING {_SELECT_COLS}
            """,
            (_clean_title(title), _clean_body(body), now, editor, slug, int(notepad_id)),
        ).fetchone()
    if not row:
        raise ValueError("Notepad not found.")
    return _row_to_notepad(row)


def delete_notepad(client_slug: str, notepad_id: int) -> bool:
    if not enabled():
        return False
    ensure_schema()
    slug = _clean_slug(client_slug)
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM client_notepads WHERE client_slug = %s AND id = %s",
            (slug, int(notepad_id)),
        )
    return bool(cur.rowcount)
