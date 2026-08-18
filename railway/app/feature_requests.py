"""Feature requests raised from the client dashboards (super-admin inbox).

Agency users can flag a feature request straight from the floating notes FAB on
any client dashboard. Each request captures *where* it was raised (the client
slug + the page URL/label the presenter was looking at) and *what* was asked for
(a free-text note). They land in a super-admin-only inbox on ``/admin`` and drive
the notification badge there — a lightweight "someone wants X" channel that never
gets lost in a chat thread.

Storage mirrors ``admin_dev_notes`` / ``client_notes`` — a single table, raw SQL
via ``db`` v3, idempotent ``ensure_schema()``, no ORM. A request starts life as
``status = 'new'`` (an unread notification) and a super admin marks it
``'done'`` once handled. A super admin can also ``'archived'`` a request to
dismiss it from the inbox without losing the row, or hard-delete it outright.

Both ends of that life cycle ping the agency's Slack channel — the ask when it's
raised, and the close-out when it's marked done — so whoever asked hears it
shipped without watching ``/admin``. The close-out is a reply in the ask's own
Slack thread, which is why the row keeps ``slack_channel`` / ``slack_thread_ts``:
they're the address of the message the ask created.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import db
import slack_service
import web_users

_log = logging.getLogger(__name__)

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS feature_requests (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT,
      page_path TEXT,
      page_label TEXT,
      body TEXT NOT NULL DEFAULT '',
      scope TEXT NOT NULL DEFAULT 'global',
      status TEXT NOT NULL DEFAULT 'new',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT,
      resolved_at TIMESTAMPTZ,
      resolved_by TEXT,
      slack_channel TEXT,
      slack_thread_ts TEXT
    )
    """,
    # Backfill the scope column on databases created before it existed.
    """
    ALTER TABLE feature_requests
      ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'global'
    """,
    # Where the ask was announced in Slack, so the close-out can reply to it.
    # Rows created before this existed keep NULLs and fall back to a standalone
    # close-out message.
    """
    ALTER TABLE feature_requests
      ADD COLUMN IF NOT EXISTS slack_channel TEXT
    """,
    """
    ALTER TABLE feature_requests
      ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT
    """,
    """
    CREATE INDEX IF NOT EXISTS feature_requests_status_created_idx
      ON feature_requests (status, created_at DESC)
    """,
]

STATUSES = ("new", "done", "archived")

# A request is either a "global" ask (applies to every client's shared
# dashboard) or scoped to the single client it was raised from. The composer
# defaults to global; the "This client only" checkbox flips it to client scope.
SCOPES = ("global", "client")

# Keep the free-text fields bounded so a runaway paste can't bloat a row.
MAX_BODY_LEN = 20_000
MAX_PATH_LEN = 500
MAX_LABEL_LEN = 200


@dataclass(frozen=True)
class FeatureRequest:
    id: int
    client_slug: str | None
    page_path: str | None
    page_label: str | None
    body: str
    scope: str
    status: str
    created_at: str | None
    created_by: str | None
    resolved_at: str | None
    resolved_by: str | None
    # Address of the Slack message announcing this ask, when there is one. Plumbing
    # for threading the close-out — deliberately absent from ``to_dict``, which is
    # what the admin UI and API render.
    slack_channel: str | None = None
    slack_thread_ts: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "client_slug": self.client_slug,
            "page_path": self.page_path,
            "page_label": self.page_label,
            "body": self.body,
            "scope": self.scope,
            "status": self.status,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }


def _get_db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    """Feature requests need the same Postgres-backed layer as the dashboards."""
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _normalize_status(raw: str | None) -> str:
    status = (raw or "new").strip().lower()
    return status if status in STATUSES else "new"


def _normalize_scope(raw: str | None) -> str:
    scope = (raw or "global").strip().lower()
    return scope if scope in SCOPES else "global"


def _clean_body(body: str | None) -> str:
    clean = (body or "").strip()
    return clean[:MAX_BODY_LEN]


def _clean_path(path: str | None) -> str | None:
    clean = (path or "").strip()
    return clean[:MAX_PATH_LEN] or None


def _clean_label(label: str | None) -> str | None:
    clean = (label or "").strip()
    return clean[:MAX_LABEL_LEN] or None


def _clean_slug(client_slug: str | None) -> str | None:
    clean = (client_slug or "").strip().lower()
    return clean or None


_SELECT_COLS = (
    "id, client_slug, page_path, page_label, body, scope, status, "
    "created_at, created_by, resolved_at, resolved_by, "
    "slack_channel, slack_thread_ts"
)


def _row_to_request(row: tuple[Any, ...]) -> FeatureRequest:
    created = row[7]
    resolved = row[9]
    return FeatureRequest(
        id=int(row[0]),
        client_slug=str(row[1]) if row[1] else None,
        page_path=str(row[2]) if row[2] else None,
        page_label=str(row[3]) if row[3] else None,
        body=str(row[4] or ""),
        scope=_normalize_scope(str(row[5] or "")),
        status=_normalize_status(str(row[6] or "")),
        created_at=created.isoformat() if created else None,
        created_by=str(row[8]) if row[8] else None,
        resolved_at=resolved.isoformat() if resolved else None,
        resolved_by=str(row[10]) if row[10] else None,
        slack_channel=str(row[11]) if len(row) > 11 and row[11] else None,
        slack_thread_ts=str(row[12]) if len(row) > 12 and row[12] else None,
    )


def create_request(
    *,
    body: str,
    client_slug: str | None = None,
    page_path: str | None = None,
    page_label: str | None = None,
    scope: str | None = None,
    created_by: str | None = None,
) -> FeatureRequest:
    clean_body = _clean_body(body)
    if not clean_body:
        raise ValueError("A feature request needs a description.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to log feature requests.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    author = (created_by or "").strip() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO feature_requests
              (client_slug, page_path, page_label, body, scope, status, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s, 'new', %s, %s)
            RETURNING {_SELECT_COLS}
            """,
            (
                _clean_slug(client_slug),
                _clean_path(page_path),
                _clean_label(page_label),
                clean_body,
                _normalize_scope(scope),
                now,
                author,
            ),
        ).fetchone()
    assert row
    request = _row_to_request(row)
    return _notify_slack(request)


def _notify_slack(request: FeatureRequest) -> FeatureRequest:
    """Announce the new request in Slack and remember the message it created.

    Best-effort on both counts — never break the write. Returns the request with
    ``slack_channel`` / ``slack_thread_ts`` filled in when Slack accepted the post,
    otherwise unchanged; without them the close-out just can't be threaded.
    """
    posted = _send_slack(request, slack_service.notify_feature_request, "new")
    if not posted:
        return request
    if not _remember_slack_message(request.id, posted):
        return request
    return replace(request, slack_channel=posted.channel, slack_thread_ts=posted.ts)


def _remember_slack_message(request_id: int, posted) -> bool:
    """Store the ask's Slack address on the row. Best-effort: the row is saved."""
    try:
        with db.connection() as conn:
            conn.execute(
                """
                UPDATE feature_requests
                SET slack_channel = %s, slack_thread_ts = %s
                WHERE id = %s
                """,
                (posted.channel, posted.ts, int(request_id)),
            )
        return True
    except Exception:  # noqa: BLE001 — worst case the close-out isn't threaded.
        _log.exception("Failed to record Slack thread for feature request %s", request_id)
        return False


def _notify_slack_done(request: FeatureRequest) -> None:
    """Reply in the ask's Slack thread that it's handled. Best-effort.

    Falls back to a standalone message in the configured channel for requests
    raised before we started recording the thread.
    """
    _send_slack(
        request,
        lambda req: slack_service.notify_feature_request_done(
            req, channel=req.slack_channel, thread_ts=req.slack_thread_ts
        ),
        "done",
    )


def _send_slack(request: FeatureRequest, send, kind: str):
    if not slack_service.enabled():
        return None
    try:
        return send(request)
    except Exception:  # noqa: BLE001 — logging is enough; the row is already saved.
        _log.exception(
            "Failed to send Slack %s notification for feature request %s", kind, request.id
        )
        return None


def list_requests(
    *, status: str | None = None, include_archived: bool = False, limit: int = 200
) -> list[FeatureRequest]:
    """List requests newest-first.

    Archived requests are dismissed from the inbox: they're excluded unless
    ``include_archived`` is set or ``status='archived'`` is asked for explicitly.
    """
    if not enabled():
        return []
    ensure_schema()
    limit = max(1, min(int(limit), 500))
    where = ""
    params: list[Any] = []
    if status:
        where = "WHERE status = %s"
        params.append(_normalize_status(status))
    elif not include_archived:
        where = "WHERE status <> 'archived'"
    params.append(limit)
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_COLS}
            FROM feature_requests
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_request(row) for row in rows]


def new_count() -> int:
    """Number of unread ('new') requests — drives the admin notification badge."""
    if not enabled():
        return 0
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM feature_requests WHERE status = 'new'"
        ).fetchone()
    return int(row[0]) if row else 0


def mark_done(request_id: int, *, resolved_by: str | None = None) -> bool:
    """Mark a request handled and ping Slack that it shipped.

    The ``status <> 'done'`` guard makes this idempotent, so a double-click on the
    admin button updates nothing the second time — and posts nothing either.
    """
    if not enabled():
        return False
    ensure_schema()
    now = datetime.now(tz=UTC)
    resolver = (resolved_by or "").strip() or None
    with db.connection() as conn:
        row = conn.execute(
            f"""
            UPDATE feature_requests
            SET status = 'done', resolved_at = %s, resolved_by = %s
            WHERE id = %s AND status <> 'done'
            RETURNING {_SELECT_COLS}
            """,
            (now, resolver, int(request_id)),
        ).fetchone()
    if not row:
        return False
    _notify_slack_done(_row_to_request(row))
    return True


def reopen(request_id: int) -> bool:
    if not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE feature_requests
            SET status = 'new', resolved_at = NULL, resolved_by = NULL
            WHERE id = %s AND status <> 'new'
            """,
            (int(request_id),),
        )
    return bool(cur.rowcount)


def archive_request(request_id: int, *, archived_by: str | None = None) -> bool:
    """Dismiss a request from the inbox without deleting it.

    Reuses ``resolved_at`` / ``resolved_by`` to record who cleared it and when.
    """
    if not enabled():
        return False
    ensure_schema()
    now = datetime.now(tz=UTC)
    actor = (archived_by or "").strip() or None
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE feature_requests
            SET status = 'archived', resolved_at = %s, resolved_by = %s
            WHERE id = %s AND status <> 'archived'
            """,
            (now, actor, int(request_id)),
        )
    return bool(cur.rowcount)


def delete_request(request_id: int) -> bool:
    """Permanently remove a request. Unlike archiving, this can't be undone."""
    if not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM feature_requests WHERE id = %s",
            (int(request_id),),
        )
    return bool(cur.rowcount)
