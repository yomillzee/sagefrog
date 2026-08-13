"""Append-only audit trail for sign-ins and admin actions."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import psycopg
import db

import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS audit_events (
      id BIGSERIAL PRIMARY KEY,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      action TEXT NOT NULL,
      actor_user_id BIGINT,
      actor_email TEXT,
      subject_email TEXT,
      detail_json JSONB,
      ip_address TEXT,
      user_agent TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS audit_events_created_at_idx
      ON audit_events (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS audit_events_action_idx
      ON audit_events (action)
    """,
]

_ACTION_LABELS = {
    "login.success": "Signed in",
    "login.failed": "Failed sign-in",
    "logout": "Signed out",
    "user.created": "Created user",
    "user.invited": "Issued invite link",
    "user.invite_accepted": "Accepted invite",
    "user.invite_rejected": "Invalid invite link",
    "user.deactivated": "Deactivated user",
    "user.role_changed": "Changed role",
    "user.bootstrap_admin": "Bootstrap admin created",
    "group.created": "Created client group",
    "group.updated": "Updated client group",
    "group.deleted": "Deleted client group",
    "dashboard.config_saved": "Saved dashboard config",
    "dashboard.created": "Created dashboard",
    "dashboard.deleted": "Deleted dashboard",
    "dashboard.sections_saved": "Saved dashboard sections",
    "dashboard.industry_set": "Set dashboard industries",
    "dev_note.created": "Created dev note",
    "dev_note.updated": "Updated dev note",
    "dev_note.deleted": "Deleted dev note",
    "oauth.connected": "OAuth connected",
    "oauth.disconnected": "OAuth disconnected",
}


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    if not _get_db_url():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def record(
    *,
    action: str,
    actor_user_id: int | None = None,
    actor_email: str | None = None,
    subject_email: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    if not enabled():
        return
    try:
        ensure_schema()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                  action, actor_user_id, actor_email, subject_email,
                  detail_json, ip_address, user_agent, created_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    action.strip(),
                    actor_user_id,
                    (actor_email or "").strip().lower() or None,
                    (subject_email or "").strip().lower() or None,
                    json.dumps(detail or {}),
                    (ip_address or "").strip() or None,
                    (user_agent or "")[:500] or None,
                    datetime.now(tz=UTC),
                ),
            )
    except Exception:
        pass


def request_context(request) -> dict[str, str | None]:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()[:45]
    if not xff:
        client = getattr(request, "client", None)
        xff = getattr(client, "host", None) if client else None
    ua = (request.headers.get("user-agent") or "").strip()
    return {"ip_address": xff or None, "user_agent": ua[:500] if ua else None}


def list_recent(*, limit: int = 100) -> list[dict[str, Any]]:
    if not enabled():
        return []
    ensure_schema()
    limit = max(1, min(int(limit), 500))
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at, action, actor_email, subject_email, detail_json, ip_address
            FROM audit_events
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        detail = row[4]
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                detail = {}
        elif detail is None:
            detail = {}
        created = row[0]
        out.append(
            {
                "created_at": created.isoformat() if created else None,
                "action": str(row[1]),
                "action_label": _ACTION_LABELS.get(str(row[1]), str(row[1])),
                "actor_email": str(row[2]) if row[2] else None,
                "subject_email": str(row[3]) if row[3] else None,
                "detail": detail if isinstance(detail, dict) else {},
                "ip_address": str(row[5]) if row[5] else None,
            }
        )
    return out


def format_detail(event: dict[str, Any]) -> str:
    action = event.get("action") or ""
    detail = event.get("detail") or {}
    subject = event.get("subject_email")
    parts: list[str] = []
    if subject:
        parts.append(subject)
    if action == "user.created":
        role = detail.get("role")
        slug = detail.get("client_slug")
        allowed = detail.get("allowed_client_slugs")
        if role:
            parts.append(f"role={role}")
        if slug:
            parts.append(f"client={slug}")
        if role == "standard" and isinstance(allowed, list):
            parts.append(f"clients={', '.join(allowed) if allowed else 'none'}")
    elif action == "login.failed":
        reason = detail.get("reason")
        if reason:
            parts.append(reason)
    elif action == "user.deactivated":
        role = detail.get("role")
        if role:
            parts.append(f"was {role}")
    elif action == "user.role_changed":
        role = detail.get("role")
        slug = detail.get("client_slug")
        allowed = detail.get("allowed_client_slugs")
        if role:
            parts.append(f"role={role}")
        if slug:
            parts.append(f"client={slug}")
        if role == "standard" and isinstance(allowed, list):
            parts.append(f"clients={', '.join(allowed) if allowed else 'none'}")
    elif action in ("group.created", "group.updated", "group.deleted"):
        name = detail.get("name")
        slugs = detail.get("client_slugs")
        if name:
            parts.append(str(name))
        if isinstance(slugs, list):
            parts.append(f"dashboards={', '.join(slugs) if slugs else 'none'}")
    elif action == "dashboard.industry_set":
        # An account can carry several buckets; the log reads as a list rather
        # than as Python's repr of one.
        slug = detail.get("client_slug")
        keys = detail.get("industries")
        if keys is None and detail.get("industry"):  # pre-multi-select events
            keys = [detail["industry"]]
        if slug:
            parts.append(f"client={slug}")
        parts.append(
            f"industries={', '.join(keys) if keys else 'none'}"
            if isinstance(keys, list)
            else "industries=none"
        )
    elif action == "dashboard.config_saved":
        slug = detail.get("client_slug")
        if slug:
            parts.append(f"client={slug}")
    elif action in ("oauth.connected", "oauth.disconnected"):
        plat = detail.get("platform")
        if plat:
            parts.append(str(plat))
    elif action in ("dev_note.created", "dev_note.updated", "dev_note.deleted"):
        title = detail.get("title")
        note_id = detail.get("note_id")
        if title:
            parts.append(str(title))
        elif note_id:
            parts.append(f"id={note_id}")
    elif detail:
        parts.append(
            ", ".join(f"{k}={v}" for k, v in detail.items() if v is not None and k != "reason")
        )
    return " · ".join(parts) if parts else "—"
