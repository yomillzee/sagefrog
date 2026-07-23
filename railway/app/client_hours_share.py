"""Read-only public share links for the Client Hours page.

An admin can mint an unguessable, revocable token that lets anyone view the
Client Hours burn-up (`/share/client-hours/{token}`) without logging in — a
read-only mirror of `/admin/client-hours`. Tokens are stored in Postgres so they
can be listed, tracked (view count / last viewed), and revoked at any time;
there is no expiry by default (revoke to disable).

The shared view serves the same cached Harvest overview as the admin page but
never forces a live Harvest pull, so a link that leaks can't be used to hammer
the Harvest API. All the goal / project-tag mutations stay admin-only.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime
from typing import Any

import db

_log = logging.getLogger(__name__)

# 24 bytes → ~32 url-safe chars: unguessable, and short enough to paste.
_TOKEN_BYTES = 24

SHARE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS client_hours_share_links (
  token          TEXT PRIMARY KEY,
  label          TEXT,
  created_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at     TIMESTAMPTZ,
  revoked_by     TEXT,
  last_viewed_at TIMESTAMPTZ,
  view_count     BIGINT NOT NULL DEFAULT 0
)
"""


def _enabled() -> bool:
    return bool((os.getenv("DATABASE_URL") or "").strip())


def _ensure_schema() -> bool:
    if not _enabled():
        return False
    with db.connection() as conn:
        conn.execute(SHARE_SCHEMA_SQL)
    return True


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def create_share_link(*, created_by: str = "", label: str = "") -> dict[str, Any]:
    """Mint a new active share token and return its row (as a dict)."""
    if not _enabled():
        raise RuntimeError("DATABASE_URL is required to create share links.")
    _ensure_schema()
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    clean_label = (label or "").strip()[:120] or None
    creator = (created_by or "").strip() or None
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO client_hours_share_links (token, label, created_by) "
            "VALUES (%s, %s, %s)",
            (token, clean_label, creator),
        )
    return {
        "token": token,
        "label": clean_label,
        "created_by": creator,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "revoked_at": None,
        "last_viewed_at": None,
        "view_count": 0,
        "active": True,
    }


def list_share_links(*, include_revoked: bool = False) -> list[dict[str, Any]]:
    """All share links, newest first. Active-only unless ``include_revoked``."""
    if not _enabled():
        return []
    try:
        _ensure_schema()
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT token, label, created_by, created_at, revoked_at, revoked_by, "
                "last_viewed_at, view_count "
                f"FROM client_hours_share_links {where} ORDER BY created_at DESC"
            ).fetchall()
    except Exception as exc:
        _log.warning("Client Hours share-link list failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for token, label, created_by, created_at, revoked_at, revoked_by, last_viewed, views in rows:
        out.append(
            {
                "token": token,
                "label": label,
                "created_by": created_by,
                "created_at": _iso(created_at),
                "revoked_at": _iso(revoked_at),
                "revoked_by": revoked_by,
                "last_viewed_at": _iso(last_viewed),
                "view_count": int(views or 0),
                "active": revoked_at is None,
            }
        )
    return out


def revoke_share_link(*, token: str, revoked_by: str = "") -> bool:
    """Revoke a link (idempotent). Returns True if an active link was revoked."""
    tok = (token or "").strip()
    if not tok:
        return False
    if not _enabled():
        raise RuntimeError("DATABASE_URL is required to revoke share links.")
    _ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE client_hours_share_links SET revoked_at = NOW(), revoked_by = %s "
            "WHERE token = %s AND revoked_at IS NULL",
            ((revoked_by or "").strip() or None, tok),
        )
        return bool(cur.rowcount)


def resolve_share_token(token: str, *, record_view: bool = True) -> dict[str, Any] | None:
    """Return the link row for a valid, non-revoked token, else None. Used by the
    public share routes to gate access. ``record_view`` bumps the view counter —
    the page load counts, the follow-up data fetch shouldn't double-count."""
    tok = (token or "").strip()
    if not tok or not _enabled():
        return None
    try:
        _ensure_schema()
        with db.connection() as conn:
            if record_view:
                # Bump the view counter as part of the lookup so the admin list
                # shows real usage; RETURNING gives the row only when active.
                row = conn.execute(
                    "UPDATE client_hours_share_links "
                    "SET view_count = view_count + 1, last_viewed_at = NOW() "
                    "WHERE token = %s AND revoked_at IS NULL "
                    "RETURNING token, label, created_by",
                    (tok,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT token, label, created_by FROM client_hours_share_links "
                    "WHERE token = %s AND revoked_at IS NULL",
                    (tok,),
                ).fetchone()
    except Exception as exc:
        _log.warning("Client Hours share-token resolve failed: %s", exc)
        return None
    if not row:
        return None
    return {"token": row[0], "label": row[1], "created_by": row[2]}
