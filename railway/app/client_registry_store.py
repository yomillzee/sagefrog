"""Postgres-backed storage for per-client GA4 and GSC BigQuery configs.

Replaces hand-editing the GA4_CLIENTS / GSC_CLIENTS Railway env vars.
Entries here take precedence over env var entries for the same slug.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
import db

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS client_ga4_config (
      client_slug     TEXT PRIMARY KEY,
      bq_project_id   TEXT NOT NULL,
      bq_dataset_id   TEXT NOT NULL,
      credentials_env TEXT,
      label           TEXT,
      updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS client_gsc_config (
      client_slug       TEXT PRIMARY KEY,
      bq_project_id     TEXT NOT NULL,
      bq_dataset_id     TEXT NOT NULL,
      credentials_env   TEXT NOT NULL DEFAULT 'GCP_SERVICE_ACCOUNT_JSON',
      native_dataset_id TEXT,
      label             TEXT,
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by        TEXT
    )
    """,
]


def _db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or "").strip() or None


def enabled() -> bool:
    return bool(_db_url())


def ensure_schema() -> None:
    url = _db_url()
    if not url:
        return
    with db.connection() as conn:
        for stmt in SCHEMA_SQL:
            conn.execute(stmt)


@dataclass(frozen=True)
class Ga4ConfigRow:
    client_slug: str
    bq_project_id: str
    bq_dataset_id: str
    credentials_env: str | None = None
    label: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class GscConfigRow:
    client_slug: str
    bq_project_id: str
    bq_dataset_id: str
    credentials_env: str = "GCP_SERVICE_ACCOUNT_JSON"
    native_dataset_id: str | None = None
    label: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


def list_ga4_configs() -> list[Ga4ConfigRow]:
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug, bq_project_id, bq_dataset_id, credentials_env, label, "
            "updated_at, updated_by FROM client_ga4_config ORDER BY client_slug"
        ).fetchall()
    return [
        Ga4ConfigRow(
            client_slug=r[0],
            bq_project_id=r[1],
            bq_dataset_id=r[2],
            credentials_env=r[3],
            label=r[4],
            updated_at=r[5].isoformat() if r[5] else None,
            updated_by=r[6],
        )
        for r in rows
    ]


def save_ga4_config(
    *,
    client_slug: str,
    bq_project_id: str,
    bq_dataset_id: str,
    credentials_env: str | None = None,
    label: str | None = None,
    updated_by: str | None = None,
) -> None:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save GA4 config.")
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not bq_project_id.strip():
        raise ValueError("bq_project_id is required.")
    if not bq_dataset_id.strip():
        raise ValueError("bq_dataset_id is required.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_ga4_config
              (client_slug, bq_project_id, bq_dataset_id, credentials_env, label, updated_at, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              bq_project_id   = EXCLUDED.bq_project_id,
              bq_dataset_id   = EXCLUDED.bq_dataset_id,
              credentials_env = EXCLUDED.credentials_env,
              label           = EXCLUDED.label,
              updated_at      = EXCLUDED.updated_at,
              updated_by      = EXCLUDED.updated_by
            """,
            (
                slug,
                bq_project_id.strip(),
                bq_dataset_id.strip(),
                credentials_env.strip() if credentials_env else None,
                label.strip() if label else None,
                now,
                updated_by,
            ),
        )


def delete_ga4_config(client_slug: str) -> bool:
    if not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM client_ga4_config WHERE client_slug = %s",
            ((client_slug or "").strip().lower(),),
        )
        return bool(getattr(cur, "rowcount", 0))


def list_gsc_configs() -> list[GscConfigRow]:
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug, bq_project_id, bq_dataset_id, credentials_env, "
            "native_dataset_id, label, updated_at, updated_by "
            "FROM client_gsc_config ORDER BY client_slug"
        ).fetchall()
    return [
        GscConfigRow(
            client_slug=r[0],
            bq_project_id=r[1],
            bq_dataset_id=r[2],
            credentials_env=r[3] or "GCP_SERVICE_ACCOUNT_JSON",
            native_dataset_id=r[4],
            label=r[5],
            updated_at=r[6].isoformat() if r[6] else None,
            updated_by=r[7],
        )
        for r in rows
    ]


def save_gsc_config(
    *,
    client_slug: str,
    bq_project_id: str,
    bq_dataset_id: str,
    credentials_env: str = "GCP_SERVICE_ACCOUNT_JSON",
    native_dataset_id: str | None = None,
    label: str | None = None,
    updated_by: str | None = None,
) -> None:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save GSC config.")
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not bq_project_id.strip():
        raise ValueError("bq_project_id is required.")
    if not bq_dataset_id.strip():
        raise ValueError("bq_dataset_id is required.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_gsc_config
              (client_slug, bq_project_id, bq_dataset_id, credentials_env,
               native_dataset_id, label, updated_at, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              bq_project_id     = EXCLUDED.bq_project_id,
              bq_dataset_id     = EXCLUDED.bq_dataset_id,
              credentials_env   = EXCLUDED.credentials_env,
              native_dataset_id = EXCLUDED.native_dataset_id,
              label             = EXCLUDED.label,
              updated_at        = EXCLUDED.updated_at,
              updated_by        = EXCLUDED.updated_by
            """,
            (
                slug,
                bq_project_id.strip(),
                bq_dataset_id.strip(),
                credentials_env.strip() or "GCP_SERVICE_ACCOUNT_JSON",
                native_dataset_id.strip() if native_dataset_id else None,
                label.strip() if label else None,
                now,
                updated_by,
            ),
        )


def delete_gsc_config(client_slug: str) -> bool:
    if not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM client_gsc_config WHERE client_slug = %s",
            ((client_slug or "").strip().lower(),),
        )
        return bool(getattr(cur, "rowcount", 0))
