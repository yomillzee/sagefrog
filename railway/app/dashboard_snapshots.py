"""Persist dashboard JSON snapshots in Postgres."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import psycopg

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS dashboard_snapshots (
      client_key TEXT PRIMARY KEY,
      payload_json JSONB NOT NULL,
      refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return bool(_get_db_url())


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with psycopg.connect(url) as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def save_snapshot(
    client_key: str,
    payload: dict[str, Any],
    *,
    touch_refreshed_at: bool = True,
) -> None:
    if not enabled():
        raise RuntimeError("DATABASE_URL is not set — dashboard snapshots require Postgres.")
    ensure_schema()
    existing = get_snapshot(client_key)
    if touch_refreshed_at:
        now = datetime.now(tz=UTC)
        payload = {**payload, "refreshed_at": now.isoformat()}
        refreshed_db = now
    else:
        prior = (existing or {}).get("refreshed_at")
        if prior:
            payload = {**payload, "refreshed_at": prior}
        refreshed_db = None
        if existing:
            raw = existing.get("refreshed_at")
            if raw:
                try:
                    text = str(raw).strip().replace("Z", "+00:00")
                    refreshed_db = datetime.fromisoformat(text)
                    if refreshed_db.tzinfo is None:
                        refreshed_db = refreshed_db.replace(tzinfo=UTC)
                except ValueError:
                    refreshed_db = datetime.now(tz=UTC)
        if refreshed_db is None:
            refreshed_db = datetime.now(tz=UTC)
    with psycopg.connect(_get_db_url()) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_snapshots (client_key, payload_json, refreshed_at)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (client_key) DO UPDATE SET
              payload_json = EXCLUDED.payload_json,
              refreshed_at = EXCLUDED.refreshed_at
            """,
            (client_key, json.dumps(payload), refreshed_db),
        )


def get_snapshot(client_key: str) -> dict[str, Any] | None:
    if not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            "SELECT payload_json FROM dashboard_snapshots WHERE client_key = %s",
            (client_key,),
        ).fetchone()
    if not row:
        return None
    payload = row[0]
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def get_snapshot_settings_context(client_key: str) -> dict[str, Any] | None:
    """Lightweight snapshot fields for settings (skip campaign/metrics payloads)."""
    if not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT jsonb_build_object(
              'refreshed_at', payload_json->'refreshed_at',
              'date_range', payload_json->'date_range',
              'sync_meta', payload_json->'sync_meta',
              'errors', payload_json->'errors',
              'warehouse_sync', payload_json->'warehouse_sync',
              'refresh_mode', payload_json->'refresh_mode',
              'insights', payload_json->'insights'
            )
            FROM dashboard_snapshots
            WHERE client_key = %s
            """,
            (client_key,),
        ).fetchone()
    if not row or not row[0]:
        return None
    payload = row[0]
    if isinstance(payload, str):
        return json.loads(payload)
    return payload
