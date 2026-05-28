from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _get_ttl_seconds() -> int:
    raw = (os.getenv("CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return 3600
    try:
        return max(0, int(raw))
    except ValueError:
        return 3600


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_key(source: str, payload: dict[str, Any]) -> str:
    material = source + ":" + _canonical_json(payload)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS api_cache (
      id BIGSERIAL PRIMARY KEY,
      source TEXT NOT NULL,
      request_key TEXT NOT NULL,
      request_json JSONB NOT NULL,
      response_json JSONB,
      row_count INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'ok',
      error TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS api_cache_source_key_uq
      ON api_cache (source, request_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS api_cache_expires_at_idx
      ON api_cache (expires_at)
    """,
]


def _get_db_url() -> str | None:
    # Railway usually injects DATABASE_URL when you add a Postgres plugin.
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


@dataclass(frozen=True)
class CacheHit:
    row_count: int
    response_json: Any
    created_at: datetime
    expires_at: datetime


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with psycopg.connect(url) as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def status() -> dict[str, Any]:
    """
    Lightweight diagnostics for Railway deploy validation.
    """
    url = _get_db_url()
    if not url:
        return {
            "has_database_url": False,
            "schema_ready": False,
            "table_exists": False,
            "cache_rows": 0,
            "error": "DATABASE_URL is missing.",
        }
    try:
        ensure_schema()
        with psycopg.connect(url) as conn:
            table_exists = bool(
                conn.execute("SELECT to_regclass('public.api_cache') IS NOT NULL").fetchone()[0]
            )
            cache_rows = int(conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]) if table_exists else 0
        return {
            "has_database_url": True,
            "schema_ready": True,
            "table_exists": table_exists,
            "cache_rows": cache_rows,
            "error": None,
        }
    except Exception as exc:
        return {
            "has_database_url": True,
            "schema_ready": False,
            "table_exists": False,
            "cache_rows": 0,
            "error": str(exc)[:500],
        }


def get_cached(source: str, payload: dict[str, Any]) -> CacheHit | None:
    url = _get_db_url()
    if not url:
        return None

    key = _hash_key(source, payload)
    now = _utcnow()

    sql = """
      SELECT row_count, response_json, created_at, expires_at
      FROM api_cache
      WHERE source = %s AND request_key = %s AND expires_at > %s
      LIMIT 1
    """
    with psycopg.connect(url) as conn:
        row = conn.execute(sql, (source, key, now)).fetchone()
        if not row:
            return None
        row_count, response_json, created_at, expires_at = row
        return CacheHit(
            row_count=int(row_count or 0),
            response_json=response_json,
            created_at=created_at,
            expires_at=expires_at,
        )


def put_cached(
    source: str,
    payload: dict[str, Any],
    *,
    response_json: Any,
    row_count: int,
    status: str = "ok",
    error: str | None = None,
    ttl_seconds: int | None = None,
) -> None:
    url = _get_db_url()
    if not url:
        return

    ttl = _get_ttl_seconds() if ttl_seconds is None else max(0, int(ttl_seconds))
    now = _utcnow()
    expires = now + timedelta(seconds=ttl)
    key = _hash_key(source, payload)

    sql = """
      INSERT INTO api_cache
        (source, request_key, request_json, response_json, row_count, status, error, expires_at)
      VALUES
        (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
      ON CONFLICT (source, request_key)
      DO UPDATE SET
        request_json = EXCLUDED.request_json,
        response_json = EXCLUDED.response_json,
        row_count = EXCLUDED.row_count,
        status = EXCLUDED.status,
        error = EXCLUDED.error,
        created_at = now(),
        expires_at = EXCLUDED.expires_at
    """
    req_json = _canonical_json(payload)
    resp_json = _canonical_json(response_json)
    with psycopg.connect(url) as conn:
        conn.execute(sql, (source, key, req_json, resp_json, int(row_count or 0), status, error, expires))

