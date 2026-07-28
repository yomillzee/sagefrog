"""Connector configuration and sync-run storage (connector_configs + connector_sync_runs)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import db
import web_users

VALID_CONNECTOR_TYPES = frozenset(
    {"linkedin_ads", "meta_ads", "google_ads", "microsoft_ads", "ga4", "gsc", "hubspot", "circle", "gtm", "semrush", "pagespeed"}
)

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS connector_configs (
      id SERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      connector_type TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'not_connected',
      oauth_platform TEXT,
      oauth_client_slug TEXT NOT NULL DEFAULT '',
      source_account_id TEXT,
      source_account_name TEXT,
      bq_project_id TEXT,
      raw_dataset_id TEXT,
      mart_dataset_id TEXT,
      sync_enabled BOOLEAN NOT NULL DEFAULT false,
      sync_frequency TEXT NOT NULL DEFAULT 'daily',
      backfill_start_date DATE,
      last_sync_started_at TIMESTAMPTZ,
      last_sync_completed_at TIMESTAMPTZ,
      last_success_at TIMESTAMPTZ,
      last_error_message TEXT,
      disconnected_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (client_slug, connector_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connector_sync_runs (
      id SERIAL PRIMARY KEY,
      connector_config_id INTEGER REFERENCES connector_configs(id),
      run_type TEXT NOT NULL DEFAULT 'manual',
      status TEXT NOT NULL DEFAULT 'running',
      started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      completed_at TIMESTAMPTZ,
      rows_loaded INTEGER,
      error_message TEXT,
      triggered_by TEXT NOT NULL DEFAULT 'user'
    )
    """,
    # Date range covered by the most recent successful sync (added post-launch).
    "ALTER TABLE connector_configs ADD COLUMN IF NOT EXISTS last_sync_range_start DATE",
    "ALTER TABLE connector_configs ADD COLUMN IF NOT EXISTS last_sync_range_end DATE",
    # Per-connector sync options as JSON (e.g. HubSpot lifecycle_stage + lookback_days).
    "ALTER TABLE connector_configs ADD COLUMN IF NOT EXISTS sync_options TEXT",
    # Rows loaded by the most recent successful sync (shown next to "Last sync").
    "ALTER TABLE connector_configs ADD COLUMN IF NOT EXISTS last_rows_loaded INTEGER",
]


@dataclass(frozen=True)
class ConnectorConfig:
    id: int
    client_slug: str
    connector_type: str
    status: str
    oauth_platform: str | None
    oauth_client_slug: str
    source_account_id: str | None
    source_account_name: str | None
    bq_project_id: str | None
    raw_dataset_id: str | None
    mart_dataset_id: str | None
    sync_enabled: bool
    sync_frequency: str
    backfill_start_date: date | None
    last_sync_started_at: datetime | None
    last_sync_completed_at: datetime | None
    last_success_at: datetime | None
    last_error_message: str | None
    disconnected_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_sync_range_start: date | None = None
    last_sync_range_end: date | None = None
    sync_options: str | None = None
    last_rows_loaded: int | None = None


@dataclass(frozen=True)
class ConnectorSyncRun:
    id: int
    connector_config_id: int | None
    run_type: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    rows_loaded: int | None
    error_message: str | None
    triggered_by: str


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    if not (os.getenv("DATABASE_URL") or "").strip():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


_SELECT = """
    SELECT id, client_slug, connector_type, status, oauth_platform, oauth_client_slug,
           source_account_id, source_account_name, bq_project_id, raw_dataset_id,
           mart_dataset_id, sync_enabled, sync_frequency, backfill_start_date,
           last_sync_started_at, last_sync_completed_at, last_success_at,
           last_error_message, disconnected_at, created_at, updated_at,
           last_sync_range_start, last_sync_range_end, sync_options, last_rows_loaded
    FROM connector_configs
"""


def _row_to_config(row: tuple) -> ConnectorConfig:
    return ConnectorConfig(
        id=int(row[0]),
        client_slug=str(row[1]),
        connector_type=str(row[2]),
        status=str(row[3]),
        oauth_platform=str(row[4]) if row[4] else None,
        oauth_client_slug=str(row[5] or ""),
        source_account_id=str(row[6]) if row[6] else None,
        source_account_name=str(row[7]) if row[7] else None,
        bq_project_id=str(row[8]) if row[8] else None,
        raw_dataset_id=str(row[9]) if row[9] else None,
        mart_dataset_id=str(row[10]) if row[10] else None,
        sync_enabled=bool(row[11]),
        sync_frequency=str(row[12] or "daily"),
        backfill_start_date=row[13],
        last_sync_started_at=row[14],
        last_sync_completed_at=row[15],
        last_success_at=row[16],
        last_error_message=str(row[17]) if row[17] else None,
        disconnected_at=row[18],
        created_at=row[19],
        updated_at=row[20],
        last_sync_range_start=row[21] if len(row) > 21 else None,
        last_sync_range_end=row[22] if len(row) > 22 else None,
        sync_options=str(row[23]) if len(row) > 23 and row[23] else None,
        last_rows_loaded=int(row[24]) if len(row) > 24 and row[24] is not None else None,
    )


def set_sync_options(client_slug: str, connector_type: str, sync_options: str | None) -> None:
    """Persist a connector's JSON sync options (e.g. HubSpot lifecycle_stage)."""
    if not enabled():
        return
    ensure_schema()
    with db.connection() as conn:
        conn.execute(
            "UPDATE connector_configs SET sync_options = %s, updated_at = %s "
            "WHERE client_slug = %s AND connector_type = %s",
            (sync_options, datetime.now(tz=UTC), client_slug.strip().lower(), connector_type.strip().lower()),
        )


def get_config(client_slug: str, connector_type: str) -> ConnectorConfig | None:
    if not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            _SELECT + " WHERE client_slug = %s AND connector_type = %s",
            (client_slug.strip().lower(), connector_type.strip().lower()),
        ).fetchone()
    return _row_to_config(row) if row else None


def client_slugs_with_configs() -> set[str]:
    """Distinct client slugs that have at least one connector configured.

    Cheap single-query signal for "this client is on the new connector platform"
    — used to scope the client selector to new-build clients and treat everything
    else as legacy.
    """
    if not enabled():
        return set()
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute("SELECT DISTINCT client_slug FROM connector_configs").fetchall()
    return {str(r[0]).strip().lower() for r in rows if r[0]}


def list_configs(client_slug: str) -> list[ConnectorConfig]:
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            _SELECT + " WHERE client_slug = %s ORDER BY connector_type",
            (client_slug.strip().lower(),),
        ).fetchall()
    return [_row_to_config(r) for r in rows]


def upsert_config(
    client_slug: str,
    connector_type: str,
    *,
    status: str | None = None,
    oauth_platform: str | None = None,
    oauth_client_slug: str | None = None,
    source_account_id: str | None = None,
    source_account_name: str | None = None,
    bq_project_id: str | None = None,
    raw_dataset_id: str | None = None,
    mart_dataset_id: str | None = None,
    sync_enabled: bool | None = None,
    sync_frequency: str | None = None,
    backfill_start_date: date | None = None,
) -> ConnectorConfig:
    if not enabled():
        raise RuntimeError("DATABASE_URL is required.")
    ensure_schema()
    slug = client_slug.strip().lower()
    ctype = connector_type.strip().lower()
    now = datetime.now(tz=UTC)
    existing = get_config(slug, ctype)

    _status = status if status is not None else (existing.status if existing else "not_connected")
    _oauth_platform = oauth_platform if oauth_platform is not None else (existing.oauth_platform if existing else None)
    _oauth_cs = oauth_client_slug if oauth_client_slug is not None else (existing.oauth_client_slug if existing else "")
    _acct_id = source_account_id if source_account_id is not None else (existing.source_account_id if existing else None)
    _acct_name = source_account_name if source_account_name is not None else (existing.source_account_name if existing else None)
    _bq_proj = bq_project_id if bq_project_id is not None else (existing.bq_project_id if existing else None)
    _raw_ds = raw_dataset_id if raw_dataset_id is not None else (existing.raw_dataset_id if existing else None)
    _mart_ds = mart_dataset_id if mart_dataset_id is not None else (existing.mart_dataset_id if existing else None)
    _sync_en = sync_enabled if sync_enabled is not None else (existing.sync_enabled if existing else False)
    _sync_freq = sync_frequency or (existing.sync_frequency if existing else "daily")
    _backfill = backfill_start_date or (existing.backfill_start_date if existing else None)

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO connector_configs (
              client_slug, connector_type, status, oauth_platform, oauth_client_slug,
              source_account_id, source_account_name, bq_project_id, raw_dataset_id,
              mart_dataset_id, sync_enabled, sync_frequency, backfill_start_date,
              created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug, connector_type)
            DO UPDATE SET
              status = EXCLUDED.status,
              oauth_platform = EXCLUDED.oauth_platform,
              oauth_client_slug = EXCLUDED.oauth_client_slug,
              source_account_id = EXCLUDED.source_account_id,
              source_account_name = EXCLUDED.source_account_name,
              bq_project_id = EXCLUDED.bq_project_id,
              raw_dataset_id = EXCLUDED.raw_dataset_id,
              mart_dataset_id = EXCLUDED.mart_dataset_id,
              sync_enabled = EXCLUDED.sync_enabled,
              sync_frequency = EXCLUDED.sync_frequency,
              backfill_start_date = EXCLUDED.backfill_start_date,
              updated_at = EXCLUDED.updated_at
            """,
            (
                slug, ctype, _status, _oauth_platform, _oauth_cs or "",
                _acct_id, _acct_name, _bq_proj, _raw_ds, _mart_ds,
                _sync_en, _sync_freq, _backfill,
                now, now,
            ),
        )
    result = get_config(slug, ctype)
    if not result:
        raise RuntimeError("Failed to load saved connector config.")
    return result


def update_status(
    client_slug: str,
    connector_type: str,
    *,
    status: str,
    error_message: str | None = None,
    disconnected_at: datetime | None = None,
    sync_enabled: bool | None = None,
) -> None:
    if not enabled():
        return
    ensure_schema()
    now = datetime.now(tz=UTC)
    sets = ["status = %s", "last_error_message = %s", "disconnected_at = %s", "updated_at = %s"]
    vals: list[Any] = [status, error_message, disconnected_at, now]
    if sync_enabled is not None:
        sets.append("sync_enabled = %s")
        vals.append(sync_enabled)
    vals.extend([client_slug.lower(), connector_type.lower()])
    with db.connection() as conn:
        conn.execute(
            f"UPDATE connector_configs SET {', '.join(sets)} WHERE client_slug = %s AND connector_type = %s",
            vals,
        )


def update_sync_timestamps(
    client_slug: str,
    connector_type: str,
    *,
    started: bool = False,
    completed: bool = False,
    success: bool = False,
    error: str | None = None,
    range_start: date | None = None,
    range_end: date | None = None,
    rows_loaded: int | None = None,
) -> None:
    if not enabled():
        return
    ensure_schema()
    now = datetime.now(tz=UTC)
    sets = ["updated_at = %s"]
    vals: list[Any] = [now]
    if started:
        sets += ["last_sync_started_at = %s", "status = %s"]
        vals += [now, "syncing"]
    if completed:
        sets += ["last_sync_completed_at = %s"]
        vals.append(now)
    if success:
        sets += ["last_success_at = %s", "last_error_message = %s", "status = %s"]
        vals += [now, None, "connected"]
        if range_start and range_end:
            sets += ["last_sync_range_start = %s", "last_sync_range_end = %s"]
            vals += [range_start, range_end]
        if rows_loaded is not None:
            sets += ["last_rows_loaded = %s"]
            vals += [rows_loaded]
    if error:
        sets += ["last_error_message = %s", "status = %s"]
        vals += [error[:500], "error"]
    vals += [client_slug.lower(), connector_type.lower()]
    with db.connection() as conn:
        conn.execute(
            f"UPDATE connector_configs SET {', '.join(sets)} WHERE client_slug = %s AND connector_type = %s",
            vals,
        )


def start_sync_run(
    connector_config_id: int,
    *,
    run_type: str = "manual",
    triggered_by: str = "user",
) -> int:
    if not enabled():
        return 0
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO connector_sync_runs
              (connector_config_id, run_type, status, started_at, triggered_by)
            VALUES (%s, %s, 'running', %s, %s)
            RETURNING id
            """,
            (connector_config_id, run_type, now, triggered_by),
        ).fetchone()
    return int(row[0]) if row else 0


def finish_sync_run(
    run_id: int,
    *,
    status: str = "completed",
    rows_loaded: int | None = None,
    error_message: str | None = None,
) -> None:
    if not enabled() or not run_id:
        return
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE connector_sync_runs
            SET status = %s, completed_at = %s, rows_loaded = %s, error_message = %s
            WHERE id = %s AND status != 'cancelled'
            """,
            (status, now, rows_loaded, error_message, run_id),
        )


def cancel_sync_run(run_id: int) -> None:
    """Mark a running sync as cancelled. Does not stop the background thread."""
    if not enabled() or not run_id:
        return
    ensure_schema()
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE connector_sync_runs
            SET status = 'cancelled', completed_at = %s
            WHERE id = %s AND status = 'running'
            """,
            (datetime.now(tz=UTC), run_id),
        )


def fail_orphaned_sync_runs(*, older_than_minutes: int = 0) -> int:
    """Mark leftover 'running' sync-runs as failed. Called once at startup.

    Manual syncs run as in-process FastAPI BackgroundTasks (see
    connector_routes.connector_sync), which do NOT survive a process exit — a
    redeploy or crash mid-sync hard-kills the thread, so `finish_sync_run` never
    fires and the run stays 'running' forever while its connector stays stuck
    showing 'syncing' in the UI/nav. On a fresh boot this process is running no
    syncs, so any 'running' row is orphaned and safe to close out.

    `older_than_minutes` guards a zero-downtime-deploy overlap where the draining
    old replica may still be finishing a sync: only runs started at least that
    long ago are touched (0 = clear all). If the old replica does finish first,
    its `finish_sync_run`/`update_sync_timestamps(success=...)` re-corrects the
    row and connector status, so the race is benign either way.

    Returns the number of runs reset.
    """
    if not enabled():
        return 0
    ensure_schema()
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(minutes=max(0, older_than_minutes))
    msg = "Sync interrupted by a server restart."
    with db.connection() as conn:
        rows = conn.execute(
            """
            UPDATE connector_sync_runs
            SET status = 'failed', completed_at = %s, error_message = %s
            WHERE status = 'running' AND started_at <= %s
            RETURNING id
            """,
            (now, msg, cutoff),
        ).fetchall()
        # Un-stick the parent connectors left flagged 'syncing'. A connector that
        # had a prior successful sync reverts to 'connected' (its existing data is
        # still valid); one that never succeeded shows 'error' so it's clearly
        # actionable (re-run the sync).
        conn.execute(
            """
            UPDATE connector_configs
            SET status = CASE WHEN last_success_at IS NOT NULL THEN 'connected' ELSE 'error' END,
                last_error_message = CASE WHEN last_success_at IS NOT NULL
                                          THEN last_error_message ELSE %s END,
                updated_at = %s
            WHERE status = 'syncing'
              AND (last_sync_started_at IS NULL OR last_sync_started_at <= %s)
            """,
            (msg, now, cutoff),
        )
    return len(rows)


def list_sync_runs(connector_config_id: int, *, limit: int = 10) -> list[ConnectorSyncRun]:
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, connector_config_id, run_type, status, started_at,
                   completed_at, rows_loaded, error_message, triggered_by
            FROM connector_sync_runs
            WHERE connector_config_id = %s
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (connector_config_id, limit),
        ).fetchall()
    return [
        ConnectorSyncRun(
            id=int(r[0]),
            connector_config_id=int(r[1]) if r[1] else None,
            run_type=str(r[2]),
            status=str(r[3]),
            started_at=r[4],
            completed_at=r[5],
            rows_loaded=int(r[6]) if r[6] is not None else None,
            error_message=str(r[7]) if r[7] else None,
            triggered_by=str(r[8]),
        )
        for r in rows
    ]
