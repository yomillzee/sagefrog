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


def save_snapshot(client_key: str, payload: dict[str, Any]) -> None:
    if not enabled():
        raise RuntimeError("DATABASE_URL is not set — dashboard snapshots require Postgres.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    payload = {**payload, "refreshed_at": now.isoformat()}
    with psycopg.connect(_get_db_url()) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_snapshots (client_key, payload_json, refreshed_at)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (client_key) DO UPDATE SET
              payload_json = EXCLUDED.payload_json,
              refreshed_at = EXCLUDED.refreshed_at
            """,
            (client_key, json.dumps(payload), now),
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
