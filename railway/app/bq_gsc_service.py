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
from datetime import date
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


def fetch_device_breakdown(*, start: date, end: date) -> list[dict[str, Any]]:
    table = _full_table()
    sql = f"""
    SELECT
      device,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC
    """
    rows = _run(sql, max_rows=20)
    return [
        {
            "device": str(r.get("device") or ""),
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def fetch_country_breakdown(*, start: date, end: date, limit: int = 15) -> list[dict[str, Any]]:
    table = _full_table()
    sql = f"""
    SELECT
      country,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC
    LIMIT {int(limit)}
    """
    rows = _run(sql, max_rows=limit + 5)
    return [
        {
            "country": str(r.get("country") or ""),
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def fetch_search_type_breakdown(*, start: date, end: date) -> list[dict[str, Any]]:
    table = _full_table()
    sql = f"""
    SELECT
      search_type,
      SUM(organic_clicks) AS clicks,
      SUM(organic_impressions) AS impressions,
      SAFE_DIVIDE(SUM(organic_clicks), SUM(organic_impressions)) AS ctr,
      SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1 AS avg_position
    FROM {table}
    WHERE {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC
    """
    rows = _run(sql, max_rows=20)
    return [
        {
            "search_type": str(r.get("search_type") or ""),
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


def build_gsc_snapshot(*, start: date, end: date) -> dict[str, Any]:
    """Fetch all GSC modules. Returns a dict with kpis, daily, and breakdown tables."""
    errors: dict[str, str] = {}
    result: dict[str, Any] = {
        "kpis": {},
        "daily": [],
        "top_queries": [],
        "top_pages": [],
        "top_query_page": [],
        "device_breakdown": [],
        "country_breakdown": [],
        "search_type_breakdown": [],
    }

    def _try(key: str, fn):
        try:
            result[key] = fn()
        except Exception as exc:
            errors[key] = str(exc)[:400]

    _try("kpis", lambda: fetch_kpis(start=start, end=end))
    _try("daily", lambda: fetch_daily(start=start, end=end))
    _try("top_queries", lambda: fetch_top_queries(start=start, end=end))
    _try("top_pages", lambda: fetch_top_pages(start=start, end=end))
    _try("top_query_page", lambda: fetch_top_query_page(start=start, end=end))
    _try("device_breakdown", lambda: fetch_device_breakdown(start=start, end=end))
    _try("country_breakdown", lambda: fetch_country_breakdown(start=start, end=end))
    _try("search_type_breakdown", lambda: fetch_search_type_breakdown(start=start, end=end))

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
