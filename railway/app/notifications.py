"""Per-user notification inbox for Sagefrog staff.

The portal already had one team-wide inbox — feature requests, which every super
admin sees in full. This is the other half: a notification addressed to *one
person*, so a comment left on an account's dashboard reaches the people staffed
on that account (see ``client_team``) instead of a channel everyone learns to
scroll past.

A notification is a flat, already-addressed row: one per recipient, carrying its
own copy of the headline and the link to open. Nothing about it is recomputed at
read time, so a notification keeps saying what it said when it was sent even if
the comment behind it is later deleted.

Only agency logins ever appear as recipients — ``client_team`` filters to agency
roles before a row is written, and the routes gate on the same roles again.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import db
import db_migrate
import web_users

_log = logging.getLogger(__name__)

# What raised the notification. Kept as a plain string column (no CHECK) so a new
# kind never needs a migration; the UI falls back to a generic icon/verb.
KIND_COMMENT = "comment"
KIND_COMMENT_REPLY = "comment_reply"

MAX_BODY_LEN = 4_000
MAX_TITLE_LEN = 300
MAX_PATH_LEN = 500
MAX_LABEL_LEN = 200

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS user_notifications (
      id BIGSERIAL PRIMARY KEY,
      recipient_email TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'comment',
      client_slug TEXT,
      page_path TEXT,
      page_label TEXT,
      title TEXT NOT NULL DEFAULT '',
      body TEXT NOT NULL DEFAULT '',
      actor_email TEXT,
      comment_id BIGINT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      read_at TIMESTAMPTZ
    )
    """,
    # The inbox query: one recipient's rows, unread first, newest first.
    """
    CREATE INDEX IF NOT EXISTS user_notifications_recipient_created_idx
      ON user_notifications (LOWER(recipient_email), created_at DESC)
    """,
    # The badge query: how many are still unread for this person.
    """
    CREATE INDEX IF NOT EXISTS user_notifications_unread_idx
      ON user_notifications (LOWER(recipient_email)) WHERE read_at IS NULL
    """,
]

db_migrate.register(
    [
        db_migrate.Migration(
            id="notifications:0001_baseline", statements=tuple(SCHEMA_SQL_STATEMENTS)
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


@dataclass(frozen=True)
class Notification:
    id: int
    recipient_email: str
    kind: str
    client_slug: str | None
    page_path: str | None
    page_label: str | None
    title: str
    body: str
    actor_email: str | None
    comment_id: int | None
    created_at: str | None
    read_at: str | None

    @property
    def is_unread(self) -> bool:
        return not self.read_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "client_slug": self.client_slug,
            "page_path": self.page_path,
            "page_label": self.page_label,
            "title": self.title,
            "body": self.body,
            "actor_email": self.actor_email,
            "comment_id": self.comment_id,
            "created_at": self.created_at,
            "read_at": self.read_at,
            "unread": self.is_unread,
        }


_SELECT_COLS = (
    "id, recipient_email, kind, client_slug, page_path, page_label, title, body, "
    "actor_email, comment_id, created_at, read_at"
)


def _row(row: tuple[Any, ...]) -> Notification:
    created = row[10]
    read = row[11]
    return Notification(
        id=int(row[0]),
        recipient_email=str(row[1] or ""),
        kind=str(row[2] or KIND_COMMENT),
        client_slug=str(row[3]) if row[3] else None,
        page_path=str(row[4]) if row[4] else None,
        page_label=str(row[5]) if row[5] else None,
        title=str(row[6] or ""),
        body=str(row[7] or ""),
        actor_email=str(row[8]) if row[8] else None,
        comment_id=int(row[9]) if row[9] is not None else None,
        created_at=created.isoformat() if created else None,
        read_at=read.isoformat() if read else None,
    )


def _clean(value: str | None, limit: int) -> str | None:
    clean = (value or "").strip()
    return clean[:limit] or None


def notify(
    recipients: list[str] | tuple[str, ...],
    *,
    kind: str,
    title: str,
    body: str = "",
    client_slug: str | None = None,
    page_path: str | None = None,
    page_label: str | None = None,
    actor_email: str | None = None,
    comment_id: int | None = None,
    skip_actor: bool = True,
) -> int:
    """Write one notification per recipient. Returns how many were written.

    ``skip_actor`` keeps the author out of their own inbox — you already know you
    left that comment. Best-effort by design: a failure here is logged, never
    raised, because losing a notification must not lose the comment that caused
    it.
    """
    if not enabled():
        return 0
    actor = (actor_email or "").strip().lower() or None
    targets: list[str] = []
    for raw in recipients or ():
        email = (raw or "").strip().lower()
        if not email or email in targets:
            continue
        if skip_actor and actor and email == actor:
            continue
        targets.append(email)
    if not targets:
        return 0
    ensure_schema()
    now = datetime.now(tz=UTC)
    written = 0
    try:
        with db.connection() as conn:
            for email in targets:
                conn.execute(
                    """
                    INSERT INTO user_notifications
                      (recipient_email, kind, client_slug, page_path, page_label,
                       title, body, actor_email, comment_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        email,
                        (kind or KIND_COMMENT).strip().lower(),
                        (client_slug or "").strip().lower() or None,
                        _clean(page_path, MAX_PATH_LEN),
                        _clean(page_label, MAX_LABEL_LEN),
                        (title or "").strip()[:MAX_TITLE_LEN],
                        (body or "").strip()[:MAX_BODY_LEN],
                        actor,
                        int(comment_id) if comment_id is not None else None,
                        now,
                    ),
                )
                written += 1
    except Exception:  # noqa: BLE001 — the caller's write already succeeded.
        _log.exception("Failed to write notifications for %s", kind)
    return written


def list_for(
    email: str, *, include_read: bool = True, limit: int = 100
) -> list[Notification]:
    """One person's inbox, newest first."""
    target = (email or "").strip().lower()
    if not target or not enabled():
        return []
    ensure_schema()
    limit = max(1, min(int(limit), 500))
    where = "WHERE LOWER(recipient_email) = %s"
    if not include_read:
        where += " AND read_at IS NULL"
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM user_notifications
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (target, limit),
        ).fetchall()
    return [_row(r) for r in rows]


def get_for(notification_id: int, *, email: str) -> Notification | None:
    """One row, scoped to its recipient — a guessed id reads as "not found"."""
    target = (email or "").strip().lower()
    if not target or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM user_notifications "
            "WHERE id = %s AND LOWER(recipient_email) = %s",
            (int(notification_id), target),
        ).fetchone()
    return _row(row) if row else None


def unread_count(email: str) -> int:
    """Unread total for the sidebar badge. Never raises — a badge is not worth a 500."""
    target = (email or "").strip().lower()
    if not target or not enabled():
        return 0
    try:
        ensure_schema()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM user_notifications "
                "WHERE LOWER(recipient_email) = %s AND read_at IS NULL",
                (target,),
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001 — degrade to "no badge".
        _log.exception("Failed to count notifications for %s", target)
        return 0


def mark_read(notification_id: int, *, email: str) -> bool:
    """Mark one row read. Scoped by recipient so an id can't clear someone else's."""
    target = (email or "").strip().lower()
    if not target or not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE user_notifications SET read_at = %s "
            "WHERE id = %s AND LOWER(recipient_email) = %s AND read_at IS NULL",
            (datetime.now(tz=UTC), int(notification_id), target),
        )
    return bool(cur.rowcount)


def mark_all_read(email: str) -> int:
    target = (email or "").strip().lower()
    if not target or not enabled():
        return 0
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE user_notifications SET read_at = %s "
            "WHERE LOWER(recipient_email) = %s AND read_at IS NULL",
            (datetime.now(tz=UTC), target),
        )
    return int(cur.rowcount or 0)
