"""BigQuery Search Console mart reader for Penn BQ Test dashboard.

Reads from fact_gsc_url_daily in the marketing_marts dataset.

Required env vars (fall back to defaults if unset):
  BQ_MART_PROJECT_ID   — GCP project (default: penn-community-b-1699391543298)
  BQ_MART_DATASET_ID   — dataset      (default: marketing_marts)
  BQ_GSC_TABLE         — GSC table    (default: fact_gsc_url_daily)

Position note: never average organic_avg_position directly.
Rolled-up avg = SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import bigquery_service

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_DATASET = "marketing_marts"
_DEFAULT_TABLE = "fact_gsc_url_daily"


def _project_id() -> str:
    return (os.getenv("BQ_MART_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _full_table() -> str:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    table = (os.getenv("BQ_GSC_TABLE") or _DEFAULT_TABLE).strip()
    return f"`{_project_id()}.{dataset}.{table}`"


def _where(start: date, end: date) -> str:
    return f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"


def _run(sql: str, max_rows: int = 500) -> list[dict[str, Any]]:
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=max_rows)


def _anon_expr() -> str:
    return (
        "CASE WHEN is_anonymized_query = TRUE OR query IS NULL OR TRIM(query) = '' "
        "THEN '(anonymized query)' ELSE query END"
    )


def _prior_period(start: date, end: date) -> tuple[date, date]:
    """Immediately preceding period of the same length."""
    n = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=n - 1)
    return prior_start, prior_end


def _pct_delta(current: float, prior: float) -> float | None:
    if not prior:
        return None
    return round((current - prior) / prior * 100, 1)


def fetch_kpis(*, start: date, end: date) -> dict[str, Any]:
    table = _full_table()
    sql = f"""
    SELECT
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    """
    rows = _run(sql, max_rows=1)
    if not rows:
        return {"clicks": 0, "impressions": 0, "ctr": 0.0, "avg_position": 0.0}
    r = rows[0]
    return {
        "clicks": int(r.get("clicks") or 0),
        "impressions": int(r.get("impressions") or 0),
        "ctr": float(r.get("ctr") or 0) * 100,
        "avg_position": float(r.get("avg_position") or 0),
    }


def fetch_kpis_with_comparison(*, start: date, end: date) -> dict[str, Any]:
    """Fetch current + prior period KPIs in a single BQ query using conditional aggregation.

    Returns a superset of fetch_kpis: all current-period fields at the top level plus
    prior_clicks, prior_impressions, prior_ctr, prior_avg_position, prior_start, prior_end,
    and delta_* percentage change fields.
    """
    prior_start, prior_end = _prior_period(start, end)
    table = _full_table()
    s = start.isoformat()
    e = end.isoformat()
    ps = prior_start.isoformat()
    pe = prior_end.isoformat()

    sql = f"""
    SELECT
      SUM(IF(date BETWEEN '{s}' AND '{e}', organic_clicks, 0))        AS clicks,
      SUM(IF(date BETWEEN '{s}' AND '{e}', organic_impressions, 0))    AS impressions,
      (SUM(IF(date BETWEEN '{s}' AND '{e}', organic_clicks, 0)) /
       NULLIF(SUM(IF(date BETWEEN '{s}' AND '{e}', organic_impressions, 0)), 0))
                                                                        AS ctr,
      (SUM(IF(date BETWEEN '{s}' AND '{e}', organic_sum_position, 0.0)) /
       NULLIF(SUM(IF(date BETWEEN '{s}' AND '{e}', organic_impressions, 0)), 0)) + 1.0
                                                                        AS avg_position,

      SUM(IF(date BETWEEN '{ps}' AND '{pe}', organic_clicks, 0))       AS prior_clicks,
      SUM(IF(date BETWEEN '{ps}' AND '{pe}', organic_impressions, 0))  AS prior_impressions,
      (SUM(IF(date BETWEEN '{ps}' AND '{pe}', organic_clicks, 0)) /
       NULLIF(SUM(IF(date BETWEEN '{ps}' AND '{pe}', organic_impressions, 0)), 0))
                                                                        AS prior_ctr,
      (SUM(IF(date BETWEEN '{ps}' AND '{pe}', organic_sum_position, 0.0)) /
       NULLIF(SUM(IF(date BETWEEN '{ps}' AND '{pe}', organic_impressions, 0)), 0)) + 1.0
                                                                        AS prior_avg_position
    FROM {table}
    WHERE date BETWEEN '{ps}' AND '{e}'
    """
    rows = _run(sql, max_rows=1)
    if not rows:
        return {}
    r = rows[0]

    clicks = int(r.get("clicks") or 0)
    impressions = int(r.get("impressions") or 0)
    ctr = float(r.get("ctr") or 0) * 100
    avg_position = float(r.get("avg_position") or 0)

    prior_clicks = int(r.get("prior_clicks") or 0)
    prior_impressions = int(r.get("prior_impressions") or 0)
    prior_ctr = float(r.get("prior_ctr") or 0) * 100
    prior_avg_position = float(r.get("prior_avg_position") or 0)

    return {
        # Current period (same keys as fetch_kpis)
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "avg_position": avg_position,
        # Prior period
        "prior_clicks": prior_clicks,
        "prior_impressions": prior_impressions,
        "prior_ctr": prior_ctr,
        "prior_avg_position": prior_avg_position,
        "prior_start": prior_start.isoformat(),
        "prior_end": prior_end.isoformat(),
        # Deltas
        "delta_clicks": _pct_delta(clicks, prior_clicks),
        "delta_impressions": _pct_delta(impressions, prior_impressions),
        "delta_ctr": _pct_delta(ctr, prior_ctr),
        "delta_avg_position": _pct_delta(avg_position, prior_avg_position),
    }


def check_table_has_data() -> dict[str, Any]:
    """Quick health check: does the table exist and does it have rows?"""
    table = _full_table()
    sql = f"SELECT COUNT(*) AS n, MIN(date) AS min_date, MAX(date) AS max_date FROM {table} LIMIT 1"
    try:
        rows = _run(sql, max_rows=1)
        if not rows:
            return {"ok": True, "row_count": 0, "min_date": None, "max_date": None, "error": None}
        r = rows[0]
        n = int(r.get("n") or 0)
        min_d = str(r.get("min_date") or "")[:10] or None
        max_d = str(r.get("max_date") or "")[:10] or None
        return {"ok": True, "row_count": n, "min_date": min_d, "max_date": max_d, "error": None}
    except Exception as exc:
        return {"ok": False, "row_count": None, "min_date": None, "max_date": None, "error": str(exc)[:300]}


def fetch_daily(*, start: date, end: date) -> list[dict[str, Any]]:
    """One row per date with aggregated GSC metrics."""
    table = _full_table()
    sql = f"""
    SELECT
      CAST(date AS STRING) AS date,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1
    ORDER BY 1
    """
    rows = _run(sql, max_rows=400)
    return [
        {
            "date": str(r.get("date") or "")[:10],
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def fetch_top_queries(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    table = _full_table()
    anon = _anon_expr()
    sql = f"""
    SELECT
      {anon} AS query,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run(sql, max_rows=limit + 5)
    return [
        {
            "query": str(r.get("query") or "(anonymized query)"),
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def fetch_top_pages(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    table = _full_table()
    sql = f"""
    SELECT
      page_url,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run(sql, max_rows=limit + 5)
    return [
        {
            "page_url": str(r.get("page_url") or ""),
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def fetch_top_query_page(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    table = _full_table()
    anon = _anon_expr()
    sql = f"""
    SELECT
      {anon} AS query,
      page_url,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1, 2
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run(sql, max_rows=limit + 5)
    return [
        {
            "query": str(r.get("query") or "(anonymized query)"),
            "page_url": str(r.get("page_url") or ""),
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def build_gsc_snapshot(*, start: date, end: date) -> dict[str, Any]:
    """Fetch GSC modules in parallel. Returns kpis, daily trend, top queries, and top pages."""
    from concurrent.futures import ThreadPoolExecutor

    tasks: dict[str, Any] = {
        "kpis": lambda: fetch_kpis_with_comparison(start=start, end=end),
        "daily": lambda: fetch_daily(start=start, end=end),
        "top_queries": lambda: fetch_top_queries(start=start, end=end, limit=5),
        "top_pages": lambda: fetch_top_pages(start=start, end=end, limit=5),
        "top_query_page": lambda: fetch_top_query_page(start=start, end=end),
    }
    result: dict[str, Any] = {k: ({} if k == "kpis" else []) for k in tasks}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {key: pool.submit(fn) for key, fn in tasks.items()}
        for key, fut in futures.items():
            try:
                result[key] = fut.result()
            except Exception as exc:
                errors[key] = str(exc)[:400]

    if errors:
        result["errors"] = errors

    return result


def env_summary() -> dict[str, str]:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    return {
        "project_id": _project_id(),
        "dataset_id": dataset,
        "gsc_table": (os.getenv("BQ_GSC_TABLE") or _DEFAULT_TABLE).strip(),
    }
