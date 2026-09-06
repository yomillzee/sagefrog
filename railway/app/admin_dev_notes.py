"""Admin dev notes for tracking updates, features, and internal work."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import db

import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS admin_dev_notes (
      id BIGSERIAL PRIMARY KEY,
      title TEXT NOT NULL,
      body TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT 'note',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT,
      updated_by TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS admin_dev_notes_updated_at_idx
      ON admin_dev_notes (updated_at DESC)
    """,
]

CATEGORIES = ("update", "feature", "bug", "note")


@dataclass(frozen=True)
class DevNote:
    id: int
    title: str
    body: str
    category: str
    created_at: str | None
    updated_at: str | None
    created_by: str | None
    updated_by: str | None


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _normalize_category(raw: str | None) -> str:
    cat = (raw or "note").strip().lower()
    return cat if cat in CATEGORIES else "note"


def _row_to_note(row: tuple[Any, ...]) -> DevNote:
    created = row[4]
    updated = row[5]
    return DevNote(
        id=int(row[0]),
        title=str(row[1] or ""),
        body=str(row[2] or ""),
        category=_normalize_category(str(row[3] or "")),
        created_at=created.isoformat() if created else None,
        updated_at=updated.isoformat() if updated else None,
        created_by=str(row[6]) if row[6] else None,
        updated_by=str(row[7]) if row[7] else None,
    )


def list_notes(*, limit: int = 100) -> list[DevNote]:
    if not enabled():
        return []
    ensure_schema()
    limit = max(1, min(int(limit), 500))
    url = _get_db_url()
    assert url
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, body, category, created_at, updated_at, created_by, updated_by
            FROM admin_dev_notes
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [_row_to_note(row) for row in rows]


def get_note(note_id: int) -> DevNote | None:
    if not enabled():
        return None
    ensure_schema()
    url = _get_db_url()
    assert url
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT id, title, body, category, created_at, updated_at, created_by, updated_by
            FROM admin_dev_notes
            WHERE id = %s
            """,
            (int(note_id),),
        ).fetchone()
    return _row_to_note(row) if row else None


def create_note(
    *,
    title: str,
    body: str,
    category: str | None = None,
    created_by: str | None = None,
) -> DevNote:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Title is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save dev notes.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    cat = _normalize_category(category)
    author = (created_by or "").strip() or None
    url = _get_db_url()
    assert url
    with db.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO admin_dev_notes (title, body, category, created_at, updated_at, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, title, body, category, created_at, updated_at, created_by, updated_by
            """,
            (clean_title, (body or "").strip(), cat, now, now, author, author),
        ).fetchone()
    assert row
    return _row_to_note(row)


def update_note(
    note_id: int,
    *,
    title: str,
    body: str,
    category: str | None = None,
    updated_by: str | None = None,
) -> DevNote:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Title is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save dev notes.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    cat = _normalize_category(category)
    editor = (updated_by or "").strip() or None
    url = _get_db_url()
    assert url
    with db.connection() as conn:
        row = conn.execute(
            """
            UPDATE admin_dev_notes
            SET title = %s, body = %s, category = %s, updated_at = %s, updated_by = %s
            WHERE id = %s
            RETURNING id, title, body, category, created_at, updated_at, created_by, updated_by
            """,
            (clean_title, (body or "").strip(), cat, now, editor, int(note_id)),
        ).fetchone()
    if not row:
        raise ValueError("Dev note not found.")
    return _row_to_note(row)


def delete_note(note_id: int) -> bool:
    if not enabled():
        return False
    ensure_schema()
    url = _get_db_url()
    assert url
    with db.connection() as conn:
        cur = conn.execute("DELETE FROM admin_dev_notes WHERE id = %s", (int(note_id),))
    return bool(cur.rowcount)
