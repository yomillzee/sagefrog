"""BigQuery Search Console reader for Penn BQ Test dashboard.

Reads from the native GSC BigQuery export in searchconsole_penn:

  searchdata_site_impression  — (date, query, country, device)
                                → KPIs, daily trend, top queries

  searchdata_url_impression   — (date, url, query, country, device)
                                → top pages

Google auto-exports to these tables daily; no manual sync needed.
Both tables are filtered to search_type = 'WEB' (organic web search).

Position formula: SAFE_DIVIDE(SUM(sum_top_position), SUM(impressions)) + 1
  sum_top_position / sum_position are 0-based cumulative position sums.

Required env vars (fall back to defaults if unset):
  GSC_BQ_PROJECT_ID   GCP project  (default: penn-community-b-1699391543298)
  GSC_BQ_DATASET_ID   dataset      (default: searchconsole_penn)
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import bigquery_service

_DEFAULT_PROJECT  = "penn-community-b-1699391543298"
_DEFAULT_DATASET  = "searchconsole_penn"
_SITE_TABLE       = "searchdata_site_impression"
_URL_TABLE        = "searchdata_url_impression"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _project_id() -> str:
    return (os.getenv("GSC_BQ_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _dataset() -> str:
    return (os.getenv("GSC_BQ_DATASET_ID") or _DEFAULT_DATASET).strip()


def _site_tbl() -> str:
    return f"`{_project_id()}.{_dataset()}.{_SITE_TABLE}`"


def _url_tbl() -> str:
    return f"`{_project_id()}.{_dataset()}.{_URL_TABLE}`"


def _where(start: date, end: date) -> str:
    return f"data_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"


def _run(sql: str, max_rows: int = 500) -> list[dict[str, Any]]:
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=max_rows)


def _anon_label() -> str:
    """CASE expression that turns anonymized queries into a readable label."""
    return (
        "CASE WHEN is_anonymized_query = TRUE OR query IS NULL OR TRIM(query) = '' "
        "THEN '(anonymized query)' ELSE query END"
    )


def _prior_period(start: date, end: date) -> tuple[date, date]:
    n = (end - start).days + 1
    prior_end   = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=n - 1)
    return prior_start, prior_end


def _pct_delta(current: float, prior: float) -> float | None:
    if not prior:
        return None
    return round((current - prior) / prior * 100, 1)


# ---------------------------------------------------------------------------
# KPIs  (site_impression — already site-level, sum over query/country/device)
# ---------------------------------------------------------------------------

def fetch_kpis_with_comparison(*, start: date, end: date) -> dict[str, Any]:
    """Current + prior period KPIs in one query using conditional aggregation."""
    prior_start, prior_end = _prior_period(start, end)
    t  = _site_tbl()
    s, e   = start.isoformat(), end.isoformat()
    ps, pe = prior_start.isoformat(), prior_end.isoformat()

    sql = f"""
    SELECT
      SUM(IF(data_date BETWEEN '{s}'  AND '{e}',  clicks, 0))       AS clicks,
      SUM(IF(data_date BETWEEN '{s}'  AND '{e}',  impressions, 0))  AS impressions,
      SAFE_DIVIDE(
        SUM(IF(data_date BETWEEN '{s}'  AND '{e}',  clicks, 0)),
        NULLIF(SUM(IF(data_date BETWEEN '{s}'  AND '{e}',  impressions, 0)), 0)
      )                                                               AS ctr,
      SAFE_DIVIDE(
        SUM(IF(data_date BETWEEN '{s}'  AND '{e}',  sum_top_position, 0.0)),
        NULLIF(SUM(IF(data_date BETWEEN '{s}'  AND '{e}',  impressions, 0)), 0)
      ) + 1.0                                                         AS avg_position,
      SUM(IF(data_date BETWEEN '{ps}' AND '{pe}', clicks, 0))        AS prior_clicks,
      SUM(IF(data_date BETWEEN '{ps}' AND '{pe}', impressions, 0))   AS prior_impressions,
      SAFE_DIVIDE(
        SUM(IF(data_date BETWEEN '{ps}' AND '{pe}', clicks, 0)),
        NULLIF(SUM(IF(data_date BETWEEN '{ps}' AND '{pe}', impressions, 0)), 0)
      )                                                               AS prior_ctr,
      SAFE_DIVIDE(
        SUM(IF(data_date BETWEEN '{ps}' AND '{pe}', sum_top_position, 0.0)),
        NULLIF(SUM(IF(data_date BETWEEN '{ps}' AND '{pe}', impressions, 0)), 0)
      ) + 1.0                                                         AS prior_avg_position
    FROM {t}
    WHERE search_type = 'WEB'
      AND data_date BETWEEN '{ps}' AND '{e}'
    """
    rows = _run(sql, max_rows=1)
    if not rows:
        return {}
    r = rows[0]

    clicks       = int(r.get("clicks") or 0)
    impressions  = int(r.get("impressions") or 0)
    ctr          = float(r.get("ctr") or 0) * 100
    avg_position = float(r.get("avg_position") or 0)

    prior_clicks       = int(r.get("prior_clicks") or 0)
    prior_impressions  = int(r.get("prior_impressions") or 0)
    prior_ctr          = float(r.get("prior_ctr") or 0) * 100
    prior_avg_position = float(r.get("prior_avg_position") or 0)

    return {
        "clicks":       clicks,
        "impressions":  impressions,
        "ctr":          ctr,
        "avg_position": avg_position,
        "prior_clicks":       prior_clicks,
        "prior_impressions":  prior_impressions,
        "prior_ctr":          prior_ctr,
        "prior_avg_position": prior_avg_position,
        "prior_start": prior_start.isoformat(),
        "prior_end":   prior_end.isoformat(),
        "delta_clicks":       _pct_delta(clicks, prior_clicks),
        "delta_impressions":  _pct_delta(impressions, prior_impressions),
        "delta_ctr":          _pct_delta(ctr, prior_ctr),
        "delta_avg_position": _pct_delta(avg_position, prior_avg_position),
    }


# ---------------------------------------------------------------------------
# Daily trend  (site_impression — sum over query/country/device per day)
# ---------------------------------------------------------------------------

def fetch_daily(*, start: date, end: date) -> list[dict[str, Any]]:
    """One row per date with site-wide aggregated GSC metrics."""
    sql = f"""
    SELECT
      CAST(data_date AS STRING)                                         AS date,
      SUM(clicks)                                                       AS clicks,
      SUM(impressions)                                                  AS impressions,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions))                        AS ctr,
      SAFE_DIVIDE(SUM(sum_top_position), SUM(impressions)) + 1          AS avg_position
    FROM {_site_tbl()}
    WHERE search_type = 'WEB'
      AND {_where(start, end)}
    GROUP BY 1
    ORDER BY 1
    """
    rows = _run(sql, max_rows=400)
    return [
        {
            "date":         str(r.get("date") or "")[:10],
            "clicks":       int(r.get("clicks") or 0),
            "impressions":  int(r.get("impressions") or 0),
            "ctr":          float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Top queries  (site_impression — aggregate over country/device)
# ---------------------------------------------------------------------------

def fetch_top_queries(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    """Top queries by clicks, aggregated over country and device."""
    sql = f"""
    SELECT
      {_anon_label()}                                                    AS query,
      SUM(clicks)                                                        AS clicks,
      SUM(impressions)                                                   AS impressions,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions))                         AS ctr,
      SAFE_DIVIDE(SUM(sum_top_position), SUM(impressions)) + 1           AS avg_position
    FROM {_site_tbl()}
    WHERE search_type = 'WEB'
      AND {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run(sql, max_rows=limit + 5)
    return [
        {
            "query":        str(r.get("query") or "(anonymized query)"),
            "clicks":       int(r.get("clicks") or 0),
            "impressions":  int(r.get("impressions") or 0),
            "ctr":          float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Top pages  (url_impression — aggregate over query/country/device)
# ---------------------------------------------------------------------------

def fetch_top_pages(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    """Top pages by clicks, aggregated over query, country, and device."""
    sql = f"""
    SELECT
      url                                                                AS page_url,
      SUM(clicks)                                                        AS clicks,
      SUM(impressions)                                                   AS impressions,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions))                         AS ctr,
      SAFE_DIVIDE(SUM(sum_position), SUM(impressions)) + 1               AS avg_position
    FROM {_url_tbl()}
    WHERE search_type = 'WEB'
      AND {_where(start, end)}
    GROUP BY 1
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run(sql, max_rows=limit + 5)
    return [
        {
            "page_url":     str(r.get("page_url") or ""),
            "clicks":       int(r.get("clicks") or 0),
            "impressions":  int(r.get("impressions") or 0),
            "ctr":          float(r.get("ctr") or 0) * 100,
            "avg_position": float(r.get("avg_position") or 0),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Table health checks
# ---------------------------------------------------------------------------

def check_tables() -> dict[str, Any]:
    """Row count + date range for both native export tables."""
    results = {}
    for name, tbl, date_col in [
        ("site_impression", _site_tbl(), "data_date"),
        ("url_impression",  _url_tbl(),  "data_date"),
    ]:
        sql = (
            f"SELECT COUNT(*) AS n, MIN({date_col}) AS min_date, MAX({date_col}) AS max_date "
            f"FROM {tbl} WHERE search_type = 'WEB' LIMIT 1"
        )
        try:
            rows = _run(sql, max_rows=1)
            r    = rows[0] if rows else {}
            results[name] = {
                "ok":        True,
                "row_count": int(r.get("n") or 0),
                "min_date":  str(r.get("min_date") or "")[:10] or None,
                "max_date":  str(r.get("max_date") or "")[:10] or None,
                "error":     None,
            }
        except Exception as exc:
            results[name] = {"ok": False, "row_count": None, "min_date": None,
                             "max_date": None, "error": str(exc)[:300]}
    return results


# ---------------------------------------------------------------------------
# Snapshot builder  (called by refresh_service)
# ---------------------------------------------------------------------------

def build_gsc_snapshot(*, start: date, end: date) -> dict[str, Any]:
    """Fetch all GSC modules in parallel. Returns kpis, daily, top_queries, top_pages."""
    from concurrent.futures import ThreadPoolExecutor

    tasks = {
        "kpis":        lambda: fetch_kpis_with_comparison(start=start, end=end),
        "daily":       lambda: fetch_daily(start=start, end=end),
        "top_queries": lambda: fetch_top_queries(start=start, end=end, limit=25),
        "top_pages":   lambda: fetch_top_pages(start=start, end=end, limit=25),
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
    return {
        "project_id": _project_id(),
        "dataset_id": _dataset(),
        "site_table": _SITE_TABLE,
        "url_table":  _URL_TABLE,
    }
