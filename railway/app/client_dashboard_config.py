"""Per-client dashboard account mapping stored in Postgres (admin-editable)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_dashboard_config (
      client_slug TEXT PRIMARY KEY,
      label TEXT NOT NULL DEFAULT '',
      google_customer_id TEXT,
      linkedin_account_id TEXT,
      meta_account_id TEXT,
      ga4_client_key TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by TEXT
    )
    """,
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS theme_json JSONB
    """,
]


@dataclass(frozen=True)
class ClientConfigRow:
    client_slug: str
    label: str
    google_customer_id: str | None
    linkedin_account_id: str | None
    meta_account_id: str | None
    ga4_client_key: str | None
    updated_at: str | None = None
    updated_by: str | None = None


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with psycopg.connect(url) as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def get_config(client_slug: str) -> ClientConfigRow | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT client_slug, label, google_customer_id, linkedin_account_id,
                   meta_account_id, ga4_client_key, updated_at, updated_by
            FROM client_dashboard_config
            WHERE client_slug = %s
            """,
            (slug,),
        ).fetchone()
    if not row:
        return None
    updated = row[6]
    return ClientConfigRow(
        client_slug=str(row[0]),
        label=str(row[1] or ""),
        google_customer_id=str(row[2]).strip() if row[2] else None,
        linkedin_account_id=str(row[3]).strip() if row[3] else None,
        meta_account_id=str(row[4]).strip() if row[4] else None,
        ga4_client_key=str(row[5]).strip() if row[5] else None,
        updated_at=updated.isoformat() if updated else None,
        updated_by=str(row[7]).strip() if row[7] else None,
    )


def save_config(
    client_slug: str,
    *,
    label: str,
    google_customer_id: str | None,
    linkedin_account_id: str | None,
    meta_account_id: str | None,
    ga4_client_key: str | None,
    updated_by: str | None = None,
) -> ClientConfigRow:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")

    def _clean(val: str | None) -> str | None:
        text = (val or "").strip()
        return text or None

    ensure_schema()
    now = datetime.now(tz=UTC)
    with psycopg.connect(_get_db_url()) as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, google_customer_id, linkedin_account_id,
              meta_account_id, ga4_client_key, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              label = EXCLUDED.label,
              google_customer_id = EXCLUDED.google_customer_id,
              linkedin_account_id = EXCLUDED.linkedin_account_id,
              meta_account_id = EXCLUDED.meta_account_id,
              ga4_client_key = EXCLUDED.ga4_client_key,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (
                slug,
                (label or "").strip() or slug,
                _clean(google_customer_id),
                _clean(linkedin_account_id),
                _clean(meta_account_id),
                _clean(ga4_client_key),
                now,
                (updated_by or "").strip() or None,
            ),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def list_config_labels() -> dict[str, str]:
    """Return {client_slug: label} for all rows with a non-empty label."""
    if not enabled():
        return {}
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        rows = conn.execute(
            "SELECT client_slug, label FROM client_dashboard_config WHERE label <> ''"
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows if row[1]}
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            "SELECT theme_json FROM client_dashboard_config WHERE client_slug = %s",
            (slug,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    payload = row[0]
    if isinstance(payload, str):
        return json.loads(payload)
    if isinstance(payload, dict):
        return payload
    return None


def save_theme(
    client_slug: str,
    theme: dict[str, Any],
    *,
    updated_by: str | None = None,
) -> dict[str, Any]:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard theme.")

    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with psycopg.connect(_get_db_url()) as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, theme_json, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              theme_json = EXCLUDED.theme_json,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (
                slug,
                label,
                json.dumps(theme),
                now,
                (updated_by or "").strip() or None,
            ),
        )
    saved = get_theme(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client theme.")
    return saved


def as_dict(row: ClientConfigRow | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "client_slug": row.client_slug,
        "label": row.label,
        "google_customer_id": row.google_customer_id,
        "linkedin_account_id": row.linkedin_account_id,
        "meta_account_id": row.meta_account_id,
        "ga4_client_key": row.ga4_client_key,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }
