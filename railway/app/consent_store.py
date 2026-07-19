"""Postgres persistence for the Consent & Tracking Health feature.

Two tables, mirroring the connector_config_store shape (config + runs):

    consent_scan_configs   one row per client: which pages to scan, the consent
                           expectations to judge against, CMP button selectors,
                           and the scheduled-scan cadence.
    consent_scan_runs      one row per scan: status, health verdict, headline
                           counts, and the full JSON result blob the UI renders.

All JSON lives in JSONB columns (``pages``, ``expectations``, ``scan_options``,
``summary``, ``result``), written with ``json.dumps(...)::jsonb`` and read back as
native Python objects — the same convention client_dashboard_config uses.

Every function degrades gracefully when ``DATABASE_URL`` is unset (``enabled()``
is False): reads return empty, writes raise a clear error only where a caller
must know the save failed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import db
import web_users

DEFAULT_FREQUENCY = "weekly"
VALID_FREQUENCIES = frozenset({"daily", "weekly", "monthly", "manual"})
# Minimum days between automated scans per cadence (manual "Run scan" ignores this).
FREQUENCY_MIN_DAYS = {"daily": 1, "weekly": 7, "monthly": 28, "manual": 10 ** 6}

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS consent_scan_configs (
      client_slug TEXT PRIMARY KEY,
      label TEXT NOT NULL DEFAULT '',
      pages JSONB NOT NULL DEFAULT '[]'::jsonb,
      expectations JSONB,
      scan_options JSONB,
      scan_enabled BOOLEAN NOT NULL DEFAULT false,
      scan_frequency TEXT NOT NULL DEFAULT 'weekly',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consent_scan_runs (
      id SERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'running',
      trigger TEXT NOT NULL DEFAULT 'manual',
      health TEXT NOT NULL DEFAULT 'unknown',
      pages_scanned INTEGER NOT NULL DEFAULT 0,
      vendor_count INTEGER NOT NULL DEFAULT 0,
      violation_count INTEGER NOT NULL DEFAULT 0,
      identifiers_before_consent INTEGER NOT NULL DEFAULT 0,
      summary JSONB,
      result JSONB,
      error_message TEXT,
      started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      completed_at TIMESTAMPTZ,
      triggered_by TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_consent_runs_client ON consent_scan_runs (client_slug, started_at DESC)",
]


@dataclass
class ConsentScanConfig:
    client_slug: str
    label: str = ""
    pages: list[str] = field(default_factory=list)
    expectations: dict[str, Any] | None = None
    scan_options: dict[str, Any] | None = None
    scan_enabled: bool = False
    scan_frequency: str = DEFAULT_FREQUENCY
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass
class ConsentScanRun:
    id: int
    client_slug: str
    status: str
    trigger: str
    health: str
    pages_scanned: int
    vendor_count: int
    violation_count: int
    identifiers_before_consent: int
    summary: dict[str, Any] | None
    result: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    triggered_by: str | None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    if not (os.getenv("DATABASE_URL") or "").strip():
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _as_obj(payload: Any) -> Any:
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return None
    return payload


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_config(client_slug: str) -> ConsentScanConfig | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT client_slug, label, pages, expectations, scan_options,
                   scan_enabled, scan_frequency, created_at, updated_at, updated_by
            FROM consent_scan_configs WHERE client_slug = %s
            """,
            (slug,),
        ).fetchone()
    if not row:
        return None
    pages = _as_obj(row[2]) or []
    return ConsentScanConfig(
        client_slug=str(row[0]),
        label=str(row[1] or ""),
        pages=[str(p) for p in pages if str(p).strip()],
        expectations=_as_obj(row[3]),
        scan_options=_as_obj(row[4]),
        scan_enabled=bool(row[5]),
        scan_frequency=str(row[6] or DEFAULT_FREQUENCY),
        created_at=row[7],
        updated_at=row[8],
        updated_by=row[9],
    )


def save_config(
    client_slug: str,
    *,
    label: str = "",
    pages: list[str],
    expectations: dict[str, Any] | None = None,
    scan_options: dict[str, Any] | None = None,
    scan_enabled: bool = False,
    scan_frequency: str = DEFAULT_FREQUENCY,
    updated_by: str | None = None,
) -> ConsentScanConfig:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save the consent scan config.")
    ensure_schema()
    freq = (scan_frequency or DEFAULT_FREQUENCY).strip().lower()
    if freq not in VALID_FREQUENCIES:
        freq = DEFAULT_FREQUENCY
    clean_pages = [p.strip() for p in (pages or []) if p and p.strip()]
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO consent_scan_configs (
              client_slug, label, pages, expectations, scan_options,
              scan_enabled, scan_frequency, created_at, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              label = EXCLUDED.label,
              pages = EXCLUDED.pages,
              expectations = EXCLUDED.expectations,
              scan_options = EXCLUDED.scan_options,
              scan_enabled = EXCLUDED.scan_enabled,
              scan_frequency = EXCLUDED.scan_frequency,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (
                slug, (label or "").strip(), json.dumps(clean_pages),
                json.dumps(expectations) if expectations is not None else None,
                json.dumps(scan_options) if scan_options is not None else None,
                bool(scan_enabled), freq, now, now,
                (updated_by or "").strip() or None,
            ),
        )
    config = get_config(slug)
    assert config is not None
    return config


def set_scan_enabled(client_slug: str, enabled_flag: bool, *, updated_by: str | None = None) -> None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return
    ensure_schema()
    with db.connection() as conn:
        conn.execute(
            "UPDATE consent_scan_configs SET scan_enabled = %s, updated_at = %s, updated_by = %s "
            "WHERE client_slug = %s",
            (bool(enabled_flag), datetime.now(tz=UTC), (updated_by or "").strip() or None, slug),
        )


def client_slugs_with_configs() -> list[str]:
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute("SELECT client_slug FROM consent_scan_configs").fetchall()
    return sorted(str(r[0]).strip().lower() for r in rows if r[0])


def clients_due_for_scan() -> list[str]:
    """Slugs with scanning enabled whose last completed scan is older than their cadence."""
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.client_slug, c.scan_frequency,
                   (SELECT MAX(r.started_at) FROM consent_scan_runs r
                      WHERE r.client_slug = c.client_slug AND r.status = 'completed')
            FROM consent_scan_configs c
            WHERE c.scan_enabled = true
              AND jsonb_array_length(COALESCE(c.pages, '[]'::jsonb)) > 0
            """
        ).fetchall()
    due: list[str] = []
    now = datetime.now(tz=UTC)
    for slug, freq, last in rows:
        min_days = FREQUENCY_MIN_DAYS.get(str(freq or DEFAULT_FREQUENCY), 7)
        if last is None:
            due.append(str(slug))
            continue
        age_days = (now - last).total_seconds() / 86400.0
        if age_days >= min_days:
            due.append(str(slug))
    return due


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def start_run(client_slug: str, *, trigger: str = "manual", triggered_by: str | None = None) -> int:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return 0
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO consent_scan_runs (client_slug, status, trigger, triggered_by, started_at)
            VALUES (%s, 'running', %s, %s, %s) RETURNING id
            """,
            (slug, (trigger or "manual").strip(), (triggered_by or "").strip() or None,
             datetime.now(tz=UTC)),
        ).fetchone()
    return int(row[0]) if row else 0


def finish_run(
    run_id: int,
    *,
    status: str,
    health: str = "unknown",
    result: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    pages_scanned: int = 0,
    vendor_count: int = 0,
    violation_count: int = 0,
    identifiers_before_consent: int = 0,
    error_message: str | None = None,
) -> None:
    if not run_id or not enabled():
        return
    ensure_schema()
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE consent_scan_runs SET
              status = %s, health = %s, result = %s::jsonb, summary = %s::jsonb,
              pages_scanned = %s, vendor_count = %s, violation_count = %s,
              identifiers_before_consent = %s, error_message = %s, completed_at = %s
            WHERE id = %s
            """,
            (
                status, health,
                json.dumps(result) if result is not None else None,
                json.dumps(summary) if summary is not None else None,
                int(pages_scanned), int(vendor_count), int(violation_count),
                int(identifiers_before_consent), (error_message or None),
                datetime.now(tz=UTC), int(run_id),
            ),
        )


def _row_to_run(row: tuple, *, include_result: bool) -> ConsentScanRun:
    result = _as_obj(row[10]) if include_result else None
    return ConsentScanRun(
        id=int(row[0]),
        client_slug=str(row[1]),
        status=str(row[2]),
        trigger=str(row[3]),
        health=str(row[4]),
        pages_scanned=int(row[5] or 0),
        vendor_count=int(row[6] or 0),
        violation_count=int(row[7] or 0),
        identifiers_before_consent=int(row[8] or 0),
        summary=_as_obj(row[9]),
        result=result,
        error_message=str(row[11]) if row[11] else None,
        started_at=row[12],
        completed_at=row[13],
        triggered_by=str(row[14]) if row[14] else None,
    )


_RUN_COLS = (
    "id, client_slug, status, trigger, health, pages_scanned, vendor_count, "
    "violation_count, identifiers_before_consent, summary, result, error_message, "
    "started_at, completed_at, triggered_by"
)


def get_run(run_id: int, *, include_result: bool = True) -> ConsentScanRun | None:
    if not run_id or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM consent_scan_runs WHERE id = %s", (int(run_id),)
        ).fetchone()
    return _row_to_run(row, include_result=include_result) if row else None


def latest_completed_run(client_slug: str, *, include_result: bool = True) -> ConsentScanRun | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM consent_scan_runs "
            "WHERE client_slug = %s AND status = 'completed' "
            "ORDER BY started_at DESC LIMIT 1",
            (slug,),
        ).fetchone()
    return _row_to_run(row, include_result=include_result) if row else None


def latest_run_any(client_slug: str) -> ConsentScanRun | None:
    """Most recent run of any status (to surface an in-progress or failed scan)."""
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM consent_scan_runs "
            "WHERE client_slug = %s ORDER BY started_at DESC LIMIT 1",
            (slug,),
        ).fetchone()
    return _row_to_run(row, include_result=True) if row else None


def previous_completed_run(client_slug: str, before_run_id: int) -> ConsentScanRun | None:
    """The completed run immediately preceding `before_run_id` (for change tracking)."""
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM consent_scan_runs "
            "WHERE client_slug = %s AND status = 'completed' AND id < %s "
            "ORDER BY started_at DESC LIMIT 1",
            (slug, int(before_run_id)),
        ).fetchone()
    return _row_to_run(row, include_result=True) if row else None


def list_runs(client_slug: str, *, limit: int = 20) -> list[ConsentScanRun]:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT {_RUN_COLS} FROM consent_scan_runs "
            "WHERE client_slug = %s ORDER BY started_at DESC LIMIT %s",
            (slug, int(limit)),
        ).fetchall()
    return [_row_to_run(r, include_result=False) for r in rows]


def latest_health_by_slug() -> dict[str, dict[str, Any]]:
    """Latest completed scan health for every client, in a single query.

    Used by the admin HQ tracker to show a per-client consent indicator without
    N per-client reads. Returns {slug: {health, violation_count,
    identifiers_before_consent, scanned_at}}.
    """
    if not enabled():
        return {}
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (client_slug)
                   client_slug, health, violation_count,
                   identifiers_before_consent, started_at
            FROM consent_scan_runs
            WHERE status = 'completed'
            ORDER BY client_slug, started_at DESC
            """
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[str(r[0]).strip().lower()] = {
            "health": str(r[1] or "unknown"),
            "violation_count": int(r[2] or 0),
            "identifiers_before_consent": int(r[3] or 0),
            "scanned_at": r[4].isoformat() if r[4] else None,
        }
    return out


def fail_orphaned_runs(*, older_than_minutes: int = 0) -> int:
    """Close out 'running' scans left behind by a crash/redeploy (called at startup)."""
    if not enabled():
        return 0
    ensure_schema()
    from datetime import timedelta
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=max(0, older_than_minutes))
    with db.connection() as conn:
        rows = conn.execute(
            """
            UPDATE consent_scan_runs
            SET status = 'failed', completed_at = %s,
                error_message = 'Scan interrupted by a server restart.'
            WHERE status = 'running' AND started_at <= %s
            RETURNING id
            """,
            (datetime.now(tz=UTC), cutoff),
        ).fetchall()
    return len(rows)
