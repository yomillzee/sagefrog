"""Comments left by Sagefrog staff on a client dashboard page.

An agency user reading a page can say something about *that page* — "this
number looks off", "we should walk them through this on Thursday" — from the
floating FAB, and the people staffed on that account (see ``client_team``) find
it in their notification inbox. It is the small conversation that would
otherwise happen in a chat thread nobody can find again three weeks later, kept
next to the thing it is about.

Two properties shape the storage:

**Comments are scoped to a page, not just a client.** Each row carries a
``page_key`` — the normalized path plus the ``view`` that dashboards switch on
(``?view=analytics``), with the rest of the query string dropped so a date-range
change doesn't fork the thread. The FAB loads the thread for the page you are
standing on.

**A thread is one level deep.** A comment either starts a thread (``parent_id``
NULL) or replies to one. Replies to replies are re-parented onto the root, so a
thread stays a readable list rather than a tree.

Internal, like ``client_notes``: the routes require an agency role, so a
client-role login never sees the FAB, the API, or the notifications it raises.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import client_team
import db
import db_migrate
import notifications
import web_users

_log = logging.getLogger(__name__)

MAX_BODY_LEN = 10_000
MAX_PATH_LEN = 500
MAX_LABEL_LEN = 200

# Query parameters that genuinely identify a different page rather than a
# different slice of the same one. The dashboards keep their sections in
# ``?view=``; date ranges, comparison toggles and the like are all the same page.
_KEY_QUERY_PARAMS = ("view", "tab")

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS page_comments (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      page_key TEXT NOT NULL,
      page_path TEXT,
      page_label TEXT,
      body TEXT NOT NULL,
      parent_id BIGINT REFERENCES page_comments(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT,
      deleted_at TIMESTAMPTZ,
      deleted_by TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS page_comments_page_idx
      ON page_comments (client_slug, page_key, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS page_comments_client_created_idx
      ON page_comments (client_slug, created_at DESC)
    """,
]

db_migrate.register(
    [
        db_migrate.Migration(
            id="page_comments:0001_baseline", statements=tuple(SCHEMA_SQL_STATEMENTS)
        )
    ]
)

_schema_ready = False
_schema_lock = threading.Lock()


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
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


def page_key(page_path: str | None) -> str:
    """Normalize a page URL to the key a thread hangs off.

    Path (lowercased, trailing slash trimmed) plus any of ``_KEY_QUERY_PARAMS``
    it carried, so ``/dashboard/acme?view=analytics&start=2026-01-01`` and the
    same page on a different date range share one thread, while the Analytics
    and Overview views keep their own.
    """
    raw = (page_path or "").strip()
    if not raw:
        return "/"
    parts = urlsplit(raw)
    path = (parts.path or "/").rstrip("/").lower() or "/"
    kept = [
        f"{name}={value.strip().lower()}"
        for name, value in parse_qsl(parts.query, keep_blank_values=False)
        if name.strip().lower() in _KEY_QUERY_PARAMS and value.strip()
    ]
    return f"{path}?{'&'.join(sorted(kept))}" if kept else path


@dataclass(frozen=True)
class Comment:
    id: int
    client_slug: str
    page_key: str
    page_path: str | None
    page_label: str | None
    body: str
    parent_id: int | None
    created_at: str | None
    created_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "client_slug": self.client_slug,
            "page_key": self.page_key,
            "page_path": self.page_path,
            "page_label": self.page_label,
            "body": self.body,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


_SELECT_COLS = (
    "id, client_slug, page_key, page_path, page_label, body, parent_id, "
    "created_at, created_by"
)


def _row(row: tuple[Any, ...]) -> Comment:
    created = row[7]
    return Comment(
        id=int(row[0]),
        client_slug=str(row[1] or ""),
        page_key=str(row[2] or ""),
        page_path=str(row[3]) if row[3] else None,
        page_label=str(row[4]) if row[4] else None,
        body=str(row[5] or ""),
        parent_id=int(row[6]) if row[6] is not None else None,
        created_at=created.isoformat() if created else None,
        created_by=str(row[8]) if row[8] else None,
    )


def _clean_slug(client_slug: str | None) -> str:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("A client slug is required.")
    return slug


def _clean(value: str | None, limit: int) -> str | None:
    clean = (value or "").strip()
    return clean[:limit] or None


def list_for_page(client_slug: str, page_path: str | None, *, limit: int = 200) -> list[Comment]:
    """Every live comment on one page, oldest first — the thread as it reads."""
    slug = _clean_slug(client_slug)
    if not enabled():
        return []
    ensure_schema()
    limit = max(1, min(int(limit), 500))
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM page_comments
            WHERE client_slug = %s AND page_key = %s AND deleted_at IS NULL
            ORDER BY created_at ASC, id ASC
            LIMIT %s
            """,
            (slug, page_key(page_path), limit),
        ).fetchall()
    return [_row(r) for r in rows]


def get_comment(comment_id: int) -> Comment | None:
    if not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM page_comments WHERE id = %s AND deleted_at IS NULL",
            (int(comment_id),),
        ).fetchone()
    return _row(row) if row else None


def _thread_participants(root_id: int) -> tuple[str, ...]:
    """Everyone who has written in a thread — the people a reply concerns."""
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT LOWER(created_by)
            FROM page_comments
            WHERE created_by IS NOT NULL
              AND (id = %s OR parent_id = %s)
            """,
            (int(root_id), int(root_id)),
        ).fetchall()
    return tuple(str(r[0]) for r in rows if r and r[0])


def create_comment(
    *,
    client_slug: str,
    body: str,
    page_path: str | None = None,
    page_label: str | None = None,
    parent_id: int | None = None,
    created_by: str | None = None,
) -> Comment:
    """Save a comment and notify the account's team. The notification is best-effort.

    A reply inherits its root's page, so a thread can never end up split across
    two pages by a stale client-side path.
    """
    slug = _clean_slug(client_slug)
    clean_body = (body or "").strip()[:MAX_BODY_LEN]
    if not clean_body:
        raise ValueError("A comment needs some text.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to leave comments.")
    ensure_schema()

    root: Comment | None = None
    if parent_id is not None:
        root = get_comment(int(parent_id))
        if root is None or root.client_slug != slug:
            raise ValueError("That comment thread no longer exists.")
        # One level deep: a reply to a reply joins the same root thread.
        if root.parent_id is not None:
            root = get_comment(root.parent_id) or root
        page_path = root.page_path
        page_label = root.page_label

    key = root.page_key if root else page_key(page_path)
    author = (created_by or "").strip().lower() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO page_comments
              (client_slug, page_key, page_path, page_label, body, parent_id, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_SELECT_COLS}
            """,
            (
                slug,
                key,
                _clean(page_path, MAX_PATH_LEN),
                _clean(page_label, MAX_LABEL_LEN),
                clean_body,
                root.id if root else None,
                datetime.now(tz=UTC),
                author,
            ),
        ).fetchone()
    assert row
    comment = _row(row)
    _notify(comment, root=root)
    return comment


def _notify(comment: Comment, *, root: Comment | None) -> None:
    """Route a new comment to the account's team (plus the thread, for a reply)."""
    try:
        recipients = list(client_team.resolve_team(comment.client_slug))
        kind = notifications.KIND_COMMENT
        if root is not None:
            kind = notifications.KIND_COMMENT_REPLY
            for email in _thread_participants(root.id):
                if email not in recipients:
                    recipients.append(email)
        who = display_name(comment.created_by)
        where = comment.page_label or comment.page_path or "a dashboard page"
        title = (
            f"{who} replied on {where}" if root is not None else f"{who} commented on {where}"
        )
        notifications.notify(
            recipients,
            kind=kind,
            title=title,
            body=comment.body,
            client_slug=comment.client_slug,
            page_path=comment.page_path,
            page_label=comment.page_label,
            actor_email=comment.created_by,
            comment_id=comment.id,
        )
    except Exception:  # noqa: BLE001 — the comment is saved; notifying is extra.
        _log.exception("Failed to notify for comment %s", comment.id)


def display_name(email: str | None) -> str:
    """"someone@sagefrog.com" → "Someone" — the same shape the sidebar shows."""
    local = (email or "").split("@", 1)[0].strip()
    if not local:
        return "Someone"
    parts = [p for p in local.replace(".", " ").replace("_", " ").split() if p]
    return " ".join(p.capitalize() for p in parts) or "Someone"


def delete_comment(comment_id: int, *, deleted_by: str | None = None) -> bool:
    """Soft-delete a comment (and, for a root, the replies under it).

    Soft so a notification that already went out still has something to point at,
    and so a deletion can be undone in the database if someone asks.
    """
    if not enabled():
        return False
    ensure_schema()
    actor = (deleted_by or "").strip().lower() or None
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE page_comments
            SET deleted_at = %s, deleted_by = %s
            WHERE (id = %s OR parent_id = %s) AND deleted_at IS NULL
            """,
            (datetime.now(tz=UTC), actor, int(comment_id), int(comment_id)),
        )
    return bool(cur.rowcount)
