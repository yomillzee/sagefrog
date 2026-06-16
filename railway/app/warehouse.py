"""Daily metrics warehouse in Postgres (metrics_daily), shared schema with linkedin-ads-dashboard."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import psycopg

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS metrics_daily (
      id BIGSERIAL PRIMARY KEY,
      source TEXT NOT NULL,
      account_id TEXT NOT NULL,
      metric_date DATE NOT NULL,
      spend NUMERIC(18,6) NOT NULL DEFAULT 0,
      clicks BIGINT NOT NULL DEFAULT 0,
      impressions BIGINT NOT NULL DEFAULT 0,
      conversions NUMERIC(18,6) NOT NULL DEFAULT 0,
      conversion_value NUMERIC(18,6) NOT NULL DEFAULT 0,
      synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (source, account_id, metric_date)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_metrics_daily_lookup
      ON metrics_daily (source, account_id, metric_date DESC)
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


def status() -> dict[str, Any]:
    if not enabled():
        return {
            "enabled": False,
            "connected": False,
            "metrics_rows": 0,
            "linkedin_rows": 0,
            "google_rows": 0,
            "ga4_rows": 0,
            "meta_rows": 0,
            "error": "DATABASE_URL is missing.",
        }
    try:
        ensure_schema()
        with psycopg.connect(_get_db_url()) as conn:
            conn.execute("SELECT 1")
            metrics_rows = int(conn.execute("SELECT COUNT(*) FROM metrics_daily").fetchone()[0])
            linkedin_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM metrics_daily WHERE source = %s", ("linkedin",)
                ).fetchone()[0]
            )
            google_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM metrics_daily WHERE source = %s", ("google",)
                ).fetchone()[0]
            )
            ga4_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM metrics_daily WHERE source = %s", ("ga4",)
                ).fetchone()[0]
            )
            meta_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM metrics_daily WHERE source = %s", ("meta",)
                ).fetchone()[0]
            )
        return {
            "enabled": True,
            "connected": True,
            "metrics_rows": metrics_rows,
            "linkedin_rows": linkedin_rows,
            "google_rows": google_rows,
            "ga4_rows": ga4_rows,
            "meta_rows": meta_rows,
            "error": None,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "connected": False,
            "metrics_rows": 0,
            "linkedin_rows": 0,
            "google_rows": 0,
            "ga4_rows": 0,
            "meta_rows": 0,
            "error": str(exc)[:500],
        }


def _normalize_source(source: str) -> str:
    key = str(source or "").strip().lower()
    if key == "google":
        return "google"
    if key == "ga4":
        return "ga4"
    if key == "meta":
        return "meta"
    return "linkedin"


def upsert_metrics_daily_batch(
    source: str,
    account_id: str,
    rows: list[dict[str, Any]],
) -> int:
    """Insert or update daily rows. Each row needs metric_date (YYYY-MM-DD) and metrics."""
    if not enabled() or not rows:
        return 0
    ensure_schema()
    account_id = str(account_id).strip()
    source_key = _normalize_source(source)
    written = 0
    sql = """
      INSERT INTO metrics_daily (
        source, account_id, metric_date, spend, clicks, impressions, conversions, conversion_value
      ) VALUES (%s, %s, %s::date, %s, %s, %s, %s, %s)
      ON CONFLICT (source, account_id, metric_date) DO UPDATE SET
        spend = EXCLUDED.spend,
        clicks = EXCLUDED.clicks,
        impressions = EXCLUDED.impressions,
        conversions = EXCLUDED.conversions,
        conversion_value = EXCLUDED.conversion_value,
        synced_at = NOW()
    """
    with psycopg.connect(_get_db_url()) as conn:
        for row in rows:
            metric_date = row.get("metric_date") or row.get("metricDate")
            if not metric_date:
                continue
            conn.execute(
                sql,
                (
                    source_key,
                    account_id,
                    str(metric_date)[:10],
                    float(row.get("spend") or 0),
                    int(row.get("clicks") or 0),
                    int(row.get("impressions") or 0),
                    float(row.get("conversions") or 0),
                    float(row.get("conversion_value") or row.get("conversionValue") or 0),
                ),
            )
            written += 1
    return written


def query_metrics(
    *,
    source: str | None,
    account_id: str | None,
    from_date: date,
    to_date: date,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    if not enabled():
        return []
    ensure_schema()
    params: list[Any] = [from_date, to_date]
    where = "WHERE metric_date >= %s::date AND metric_date <= %s::date"
    if source:
        where += " AND source = %s"
        params.append(_normalize_source(source))
    if account_id and str(account_id).strip():
        where += " AND account_id = %s"
        params.append(str(account_id).strip().split(":")[-1])
    lim = min(max(1, int(limit)), 20000)
    sql = f"""
      SELECT source, account_id, metric_date::text AS metric_date,
             spend::float AS spend, clicks, impressions,
             conversions::float AS conversions, conversion_value::float AS conversion_value,
             synced_at::text AS synced_at
      FROM metrics_daily {where}
      ORDER BY metric_date ASC, source, account_id
      LIMIT {lim}
    """
    with psycopg.connect(_get_db_url()) as conn:
        cur = conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def account_date_coverage(source: str, account_id: str) -> dict[str, Any]:
    if not enabled():
        return {"min_date": None, "max_date": None, "day_count": 0}
    ensure_schema()
    account_id = str(account_id).strip().split(":")[-1]
    source_key = _normalize_source(source)
    sql = """
      SELECT MIN(metric_date)::text, MAX(metric_date)::text, COUNT(*)
      FROM metrics_daily
      WHERE source = %s AND account_id = %s
    """
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(sql, (source_key, account_id)).fetchone()
    if not row or not row[0]:
        return {"min_date": None, "max_date": None, "day_count": 0}
    return {"min_date": row[0], "max_date": row[1], "day_count": int(row[2])}


def fill_date_gaps(
    rows: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Ensure one row per calendar day in range (zeros for missing days)."""
    by_date = {str(r["metric_date"])[:10]: r for r in rows}
    out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        if key in by_date:
            out.append(by_date[key])
        else:
            out.append(
                {
                    "metric_date": key,
                    "spend": 0.0,
                    "clicks": 0,
                    "impressions": 0,
                    "conversions": 0.0,
                    "conversion_value": 0.0,
                }
            )
        cursor += timedelta(days=1)
    return out
