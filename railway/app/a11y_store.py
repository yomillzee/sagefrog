"""Postgres persistence for the Accessibility (ADA / WCAG) audit feature.

One table, ``a11y_scan_runs`` — one row per audit, storing the headline counts plus
the full JSON result blob the report renders from. The accessibility scan is
synchronous, so a run is written once, already finished, via :func:`save_run` —
no two-step running→completed lifecycle to manage.

JSON lives in JSONB columns (``urls``, ``summary``, ``result``), written with
``json.dumps(...)::jsonb`` and read back as native objects.

Every function degrades gracefully when ``DATABASE_URL`` is unset (:func:`enabled`
is False): reads return empty and :func:`save_run` returns 0 so the caller falls
back to rendering the report inline without persistence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import db

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS a11y_scan_runs (
      id SERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'completed',
      trigger TEXT NOT NULL DEFAULT 'manual',
      band TEXT NOT NULL DEFAULT 'unknown',
      worst_impact TEXT,
      pages_requested INTEGER NOT NULL DEFAULT 0,
      pages_scanned INTEGER NOT NULL DEFAULT 0,
      total_violations INTEGER NOT NULL DEFAULT 0,
      total_nodes INTEGER NOT NULL DEFAULT 0,
      root_cause_count INTEGER NOT NULL DEFAULT 0,
      axe_version TEXT,
      urls JSONB,
      summary JSONB,
      result JSONB,
      error_message TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_a11y_runs_client ON a11y_scan_runs (client_slug, created_at DESC)",
]


@dataclass
class A11yScanRun:
    id: int
    client_slug: str
    status: str
    trigger: str
    band: str
    worst_impact: str | None
    pages_requested: int
    pages_scanned: int
    total_violations: int
    total_nodes: int
    root_cause_count: int
    axe_version: str | None
    urls: list[str]
    summary: dict[str, Any] | None
    result: dict[str, Any] | None
    error_message: str | None
    created_at: datetime | None
    created_by: str | None


def enabled() -> bool:
    return bool((os.getenv("DATABASE_URL") or "").strip())


def ensure_schema() -> bool:
    if not enabled():
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


def save_run(
    client_slug: str,
    *,
    trigger: str = "manual",
    created_by: str | None = None,
    urls: list[str] | None = None,
    band: str = "unknown",
    worst_impact: str | None = None,
    root_cause_count: int = 0,
    axe_version: str | None = None,
    summary: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    status: str = "completed",
    error_message: str | None = None,
) -> int:
    """Persist a finished audit. Returns the new run id, or 0 if no DB is configured."""
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return 0
    ensure_schema()
    s = summary or {}
    with db.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO a11y_scan_runs (
              client_slug, status, trigger, band, worst_impact,
              pages_requested, pages_scanned, total_violations, total_nodes,
              root_cause_count, axe_version, urls, summary, result, error_message,
              created_at, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
            RETURNING id
            """,
            (
                slug, status, (trigger or "manual").strip(), band, worst_impact,
                int(s.get("pages_requested", 0)), int(s.get("pages_scanned", 0)),
                int(s.get("total_violations", 0)), int(s.get("total_nodes", 0)),
                int(root_cause_count),
                (result or {}).get("axe_version") or axe_version,
                json.dumps(urls or []),
                json.dumps(summary) if summary is not None else None,
                json.dumps(result) if result is not None else None,
                (error_message or None), datetime.now(tz=UTC),
                (created_by or "").strip() or None,
            ),
        ).fetchone()
    return int(row[0]) if row else 0


_RUN_COLS = (
    "id, client_slug, status, trigger, band, worst_impact, pages_requested, "
    "pages_scanned, total_violations, total_nodes, root_cause_count, axe_version, "
    "urls, summary, result, error_message, created_at, created_by"
)


def _row_to_run(row: tuple, *, include_result: bool) -> A11yScanRun:
    return A11yScanRun(
        id=int(row[0]),
        client_slug=str(row[1]),
        status=str(row[2]),
        trigger=str(row[3]),
        band=str(row[4] or "unknown"),
        worst_impact=str(row[5]) if row[5] else None,
        pages_requested=int(row[6] or 0),
        pages_scanned=int(row[7] or 0),
        total_violations=int(row[8] or 0),
        total_nodes=int(row[9] or 0),
        root_cause_count=int(row[10] or 0),
        axe_version=str(row[11]) if row[11] else None,
        urls=[str(u) for u in (_as_obj(row[12]) or [])],
        summary=_as_obj(row[13]),
        result=_as_obj(row[14]) if include_result else None,
        error_message=str(row[15]) if row[15] else None,
        created_at=row[16],
        created_by=str(row[17]) if row[17] else None,
    )


def get_run(run_id: int, *, include_result: bool = True) -> A11yScanRun | None:
    if not run_id or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM a11y_scan_runs WHERE id = %s", (int(run_id),)
        ).fetchone()
    return _row_to_run(row, include_result=include_result) if row else None


def latest_run(client_slug: str, *, include_result: bool = True) -> A11yScanRun | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM a11y_scan_runs "
            "WHERE client_slug = %s ORDER BY created_at DESC LIMIT 1",
            (slug,),
        ).fetchone()
    return _row_to_run(row, include_result=include_result) if row else None


def list_runs(client_slug: str, *, limit: int = 20) -> list[A11yScanRun]:
    """Run history (metadata only — no heavy result blob), newest first."""
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT {_RUN_COLS} FROM a11y_scan_runs "
            "WHERE client_slug = %s ORDER BY created_at DESC LIMIT %s",
            (slug, int(limit)),
        ).fetchall()
    return [_row_to_run(r, include_result=False) for r in rows]
