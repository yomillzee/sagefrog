"""BigQuery Search Console reader for Penn BQ Test dashboard.

Data sources (combined via UNION):

  searchconsole_penn.searchdata_site_impression  — native GSC BQ export
    Google auto-exports here daily. Starts from whenever the connector was
    set up (Jun 2026 for Penn). Covers recent data going forward.

  marketing_marts.fact_gsc_query_daily          — API-backfilled history
    Populated by gsc_sync_service (triggered from Settings > Data sync, or
    by the daily cron). Covers historical dates before the native export started.
    ~300–400 queries/day × 480 days ≈ 150–200K rows total.

  searchconsole_penn.searchdata_url_impression  — native export, URL level
    Used for top-pages only. Same date coverage as site_impression.

  marketing_marts.fact_gsc_page_daily           — API-backfilled URL history
    Populated by gsc_sync_service. Historical top pages.

Position formula: SAFE_DIVIDE(SUM(sum_position), SUM(impressions)) + 1
  Both sources store 0-based cumulative position sums under different column
  names (sum_top_position / sum_position vs organic_sum_position); this module
  normalises them to a single column in the combined CTE.

Required env vars (fall back to defaults if unset):
  GSC_BQ_PROJECT_ID    GCP project for native export (default: penn-community-b-1699391543298)
  GSC_BQ_DATASET_ID    dataset for native export     (default: searchconsole_penn)
  BQ_MART_PROJECT_ID   GCP project for mart tables  (default: penn-community-b-1699391543298)
  BQ_MART_DATASET_ID   dataset for mart tables       (default: marketing_marts)
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import bigquery_service

_DEFAULT_PROJECT       = "penn-community-b-1699391543298"
_DEFAULT_NATIVE_DS     = "searchconsole_penn"
_DEFAULT_MART_DS       = "marketing_marts"
_SITE_TABLE            = "searchdata_site_impression"
_URL_TABLE             = "searchdata_url_impression"
_QUERY_HIST_TABLE      = "fact_gsc_query_daily"
_PAGE_HIST_TABLE       = "fact_gsc_page_daily"

# Set by build_gsc_snapshot(client_slug=...) so every helper below (_project_id,
# _run, the CTE builders, etc.) routes into that client's BQ destination without
# threading client_slug through every function signature individually. Falls
# back to the legacy Penn-only env vars when unset, so existing callers that
# don't pass client_slug keep working unchanged.
_client_slug_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bq_gsc_client_slug", default=None
)


@contextmanager
def _client_context(client_slug: str | None):
    token = _client_slug_ctx.set(client_slug)
    try:
        yield
    finally:
        _client_slug_ctx.reset(token)


def _resolved_target():
    import gsc_clients
    slug = _client_slug_ctx.get()
    return gsc_clients.resolve_target(slug) if slug else gsc_clients.default_target()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _project_id() -> str:
    target = _resolved_target()
    if not target.is_default_fallback:
        return target.bq_project_id
    return (os.getenv("GSC_BQ_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _mart_project_id() -> str:
    target = _resolved_target()
    if not target.is_default_fallback:
        return target.bq_project_id
    return (os.getenv("BQ_MART_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _native_ds() -> str:
    target = _resolved_target()
    if not target.is_default_fallback and target.native_dataset_id:
        return target.native_dataset_id
    return (os.getenv("GSC_BQ_DATASET_ID") or _DEFAULT_NATIVE_DS).strip()


def _has_native_export() -> bool:
    """True when this client has a native GSC → BQ export to query."""
    target = _resolved_target()
    if target.is_default_fallback:
        return True  # Penn always has native export
    return bool(target.native_dataset_id)


def _mart_ds() -> str:
    target = _resolved_target()
    if not target.is_default_fallback:
        return target.bq_dataset_id
    return (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DS).strip()


def _run(sql: str, max_rows: int = 500) -> list[dict[str, Any]]:
    target = _resolved_target()
    credentials_env = None if target.is_default_fallback else target.credentials_env
    return bigquery_service.run_query(
        sql, project_id=_project_id(), credentials_env=credentials_env, max_rows=max_rows
    )


# ---------------------------------------------------------------------------
# Combined CTE helpers
#
# Both sources use 0-based position sums but different column names:
#   Native export  → sum_top_position / sum_position
#   Backfill table → organic_sum_position
# Both are normalised to `pos_sum` in the combined CTE.
# ---------------------------------------------------------------------------

def _query_cte(start: date, end: date) -> str:
    """CTE `gsc` combining native site_impression + historical fact table.

    Covers dates in [start, end].  Historical rows only flow in for dates
    not already covered by the native export (avoids double-counting).
    If fact_gsc_query_daily doesn't exist yet, the native-only branch is used
    and the query still succeeds.
    For clients with no native GSC → BQ export, only the hist table is used.
    """
    mp = _mart_project_id()
    md = _mart_ds()
    s, e = start.isoformat(), end.isoformat()

    if not _has_native_export():
        return f"""
    gsc AS (
      SELECT
        date,
        query,
        is_anonymized_query,
        organic_clicks                                   AS clicks,
        organic_impressions                              AS impressions,
        organic_sum_position                             AS pos_sum
      FROM `{mp}.{md}.{_QUERY_HIST_TABLE}`
      WHERE date BETWEEN DATE '{s}' AND DATE '{e}'
    )"""

    p  = _project_id()
    nd = _native_ds()

    return f"""
    native_min AS (
      SELECT COALESCE(MIN(data_date), DATE '2099-01-01') AS min_date
      FROM `{p}.{nd}.{_SITE_TABLE}`
      WHERE search_type = 'WEB'
    ),
    gsc_native AS (
      SELECT
        data_date                                        AS date,
        CASE WHEN is_anonymized_query = TRUE OR query IS NULL
             THEN NULL ELSE query END                   AS query,
        is_anonymized_query,
        SUM(clicks)                                      AS clicks,
        SUM(impressions)                                 AS impressions,
        SUM(sum_top_position)                            AS pos_sum
      FROM `{p}.{nd}.{_SITE_TABLE}`
      WHERE search_type = 'WEB'
        AND data_date BETWEEN DATE '{s}' AND DATE '{e}'
      GROUP BY 1, 2, 3
    ),
    gsc_hist AS (
      SELECT
        date,
        query,
        is_anonymized_query,
        organic_clicks                                   AS clicks,
        organic_impressions                              AS impressions,
        organic_sum_position                             AS pos_sum
      FROM `{mp}.{md}.{_QUERY_HIST_TABLE}`
      WHERE date BETWEEN DATE '{s}' AND DATE '{e}'
        AND date < (SELECT min_date FROM native_min)
    ),
    gsc AS (
      SELECT * FROM gsc_native
      UNION ALL
      SELECT * FROM gsc_hist
    )"""


def _page_cte(start: date, end: date) -> str:
    """CTE `gsc_pages` combining native url_impression + historical page table.
    For clients with no native GSC → BQ export, only the hist table is used.
    """
    mp = _mart_project_id()
    md = _mart_ds()
    s, e = start.isoformat(), end.isoformat()

    if not _has_native_export():
        return f"""
    gsc_pages AS (
      SELECT
        date,
        page_url,
        organic_clicks      AS clicks,
        organic_impressions AS impressions,
        organic_sum_position AS pos_sum
      FROM `{mp}.{md}.{_PAGE_HIST_TABLE}`
      WHERE date BETWEEN DATE '{s}' AND DATE '{e}'
    )"""

    p  = _project_id()
    nd = _native_ds()

    return f"""
    page_native_min AS (
      SELECT COALESCE(MIN(data_date), DATE '2099-01-01') AS min_date
      FROM `{p}.{nd}.{_URL_TABLE}`
      WHERE search_type = 'WEB'
    ),
    gsc_pages_native AS (
      SELECT
        data_date    AS date,
        url          AS page_url,
        SUM(clicks)  AS clicks,
        SUM(impressions) AS impressions,
        SUM(sum_position)   AS pos_sum
      FROM `{p}.{nd}.{_URL_TABLE}`
      WHERE search_type = 'WEB'
        AND data_date BETWEEN DATE '{s}' AND DATE '{e}'
      GROUP BY 1, 2
    ),
    gsc_pages_hist AS (
      SELECT
        date,
        page_url,
        organic_clicks      AS clicks,
        organic_impressions AS impressions,
        organic_sum_position AS pos_sum
      FROM `{mp}.{md}.{_PAGE_HIST_TABLE}`
      WHERE date BETWEEN DATE '{s}' AND DATE '{e}'
        AND date < (SELECT min_date FROM page_native_min)
    ),
    gsc_pages AS (
      SELECT * FROM gsc_pages_native
      UNION ALL
      SELECT * FROM gsc_pages_hist
    )"""


def _anon_label() -> str:
    return (
        "CASE WHEN is_anonymized_query = TRUE OR query IS NULL OR TRIM(COALESCE(query,'')) = '' "
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


def _run_with_hist_fallback(sql: str, max_rows: int) -> list[dict[str, Any]]:
    """Run SQL; if historical table is missing, retry using native-only variant."""
    try:
        return _run(sql, max_rows=max_rows)
    except Exception as exc:
        if _QUERY_HIST_TABLE in str(exc) or _PAGE_HIST_TABLE in str(exc):
            # Historical table doesn't exist yet — retry without it by running
            # native-only (replace the gsc_hist / gsc_pages_hist CTEs with empty).
            no_hist = sql.replace(
                "UNION ALL\n      SELECT * FROM gsc_hist", ""
            ).replace(
                "UNION ALL\n      SELECT * FROM gsc_pages_hist", ""
            )
            return _run(no_hist, max_rows=max_rows)
        raise


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

def fetch_kpis_with_comparison(*, start: date, end: date) -> dict[str, Any]:
    """Current + prior-period KPIs in one conditional-aggregation query."""
    prior_start, prior_end = _prior_period(start, end)
    # Extend date range to cover both periods for the CTE
    full_start = prior_start
    full_end   = end
    s, e   = start.isoformat(), end.isoformat()
    ps, pe = prior_start.isoformat(), prior_end.isoformat()

    sql = f"""
    WITH {_query_cte(full_start, full_end)}
    SELECT
      SUM(IF(date BETWEEN '{s}'  AND '{e}',  clicks, 0))          AS clicks,
      SUM(IF(date BETWEEN '{s}'  AND '{e}',  impressions, 0))     AS impressions,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{s}'  AND '{e}',  clicks, 0)),
        NULLIF(SUM(IF(date BETWEEN '{s}'  AND '{e}',  impressions, 0)), 0)
      )                                                             AS ctr,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{s}'  AND '{e}',  pos_sum, 0.0)),
        NULLIF(SUM(IF(date BETWEEN '{s}'  AND '{e}',  impressions, 0)), 0)
      ) + 1.0                                                       AS avg_position,
      SUM(IF(date BETWEEN '{ps}' AND '{pe}', clicks, 0))           AS prior_clicks,
      SUM(IF(date BETWEEN '{ps}' AND '{pe}', impressions, 0))      AS prior_impressions,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{ps}' AND '{pe}', clicks, 0)),
        NULLIF(SUM(IF(date BETWEEN '{ps}' AND '{pe}', impressions, 0)), 0)
      )                                                             AS prior_ctr,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{ps}' AND '{pe}', pos_sum, 0.0)),
        NULLIF(SUM(IF(date BETWEEN '{ps}' AND '{pe}', impressions, 0)), 0)
      ) + 1.0                                                       AS prior_avg_position
    FROM gsc
    """
    rows = _run_with_hist_fallback(sql, max_rows=1)
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
        "clicks": clicks, "impressions": impressions,
        "ctr": ctr, "avg_position": avg_position,
        "prior_clicks": prior_clicks, "prior_impressions": prior_impressions,
        "prior_ctr": prior_ctr, "prior_avg_position": prior_avg_position,
        "prior_start": prior_start.isoformat(),
        "prior_end":   prior_end.isoformat(),
        "delta_clicks":       _pct_delta(clicks, prior_clicks),
        "delta_impressions":  _pct_delta(impressions, prior_impressions),
        "delta_ctr":          _pct_delta(ctr, prior_ctr),
        "delta_avg_position": _pct_delta(avg_position, prior_avg_position),
    }


# ---------------------------------------------------------------------------
# Daily trend
# ---------------------------------------------------------------------------

def fetch_daily(*, start: date, end: date) -> list[dict[str, Any]]:
    sql = f"""
    WITH {_query_cte(start, end)}
    SELECT
      CAST(date AS STRING)                                    AS date,
      SUM(clicks)                                             AS clicks,
      SUM(impressions)                                        AS impressions,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions))              AS ctr,
      SAFE_DIVIDE(SUM(pos_sum), SUM(impressions)) + 1         AS avg_position
    FROM gsc
    GROUP BY 1
    ORDER BY 1
    """
    rows = _run_with_hist_fallback(sql, max_rows=400)
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
# Top queries
# ---------------------------------------------------------------------------

def fetch_top_queries(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    sql = f"""
    WITH {_query_cte(start, end)}
    SELECT
      {_anon_label()}                                          AS query,
      SUM(clicks)                                              AS clicks,
      SUM(impressions)                                         AS impressions,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions))               AS ctr,
      SAFE_DIVIDE(SUM(pos_sum), SUM(impressions)) + 1          AS avg_position
    FROM gsc
    GROUP BY 1
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run_with_hist_fallback(sql, max_rows=limit + 5)
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
# Top pages
# ---------------------------------------------------------------------------

def fetch_top_pages(*, start: date, end: date, limit: int = 25) -> list[dict[str, Any]]:
    sql = f"""
    WITH {_page_cte(start, end)}
    SELECT
      page_url,
      SUM(clicks)                                              AS clicks,
      SUM(impressions)                                         AS impressions,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions))               AS ctr,
      SAFE_DIVIDE(SUM(pos_sum), SUM(impressions)) + 1          AS avg_position
    FROM gsc_pages
    GROUP BY 1
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run_with_hist_fallback(sql, max_rows=limit + 5)
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
# Health check
# ---------------------------------------------------------------------------

def check_tables(client_slug: str | None = None) -> dict[str, Any]:
    with _client_context(client_slug):
        results = {}
        checks = [
            ("native_site",  f"`{_project_id()}.{_native_ds()}.{_SITE_TABLE}`",  "data_date"),
            ("native_url",   f"`{_project_id()}.{_native_ds()}.{_URL_TABLE}`",   "data_date"),
            ("hist_query",   f"`{_mart_project_id()}.{_mart_ds()}.{_QUERY_HIST_TABLE}`", "date"),
            ("hist_page",    f"`{_mart_project_id()}.{_mart_ds()}.{_PAGE_HIST_TABLE}`",  "date"),
        ]
        for name, tbl, date_col in checks:
            sql = f"SELECT COUNT(*) AS n, MIN({date_col}) AS min_d, MAX({date_col}) AS max_d FROM {tbl} LIMIT 1"
            try:
                rows = _run(sql, max_rows=1)
                r    = rows[0] if rows else {}
                results[name] = {
                    "ok": True,
                    "row_count": int(r.get("n") or 0),
                    "min_date":  str(r.get("min_d") or "")[:10] or None,
                    "max_date":  str(r.get("max_d") or "")[:10] or None,
                    "error": None,
                }
            except Exception as exc:
                results[name] = {"ok": False, "row_count": None, "min_date": None,
                                 "max_date": None, "error": str(exc)[:200]}
        return results


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

def build_gsc_snapshot(*, start: date, end: date, client_slug: str | None = None) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    tasks = {
        "kpis":        lambda: fetch_kpis_with_comparison(start=start, end=end),
        "daily":       lambda: fetch_daily(start=start, end=end),
        "top_queries": lambda: fetch_top_queries(start=start, end=end, limit=25),
        "top_pages":   lambda: fetch_top_pages(start=start, end=end, limit=25),
    }
    result: dict[str, Any] = {k: ({} if k == "kpis" else []) for k in tasks}
    errors: dict[str, str] = {}

    with _client_context(client_slug):
        # ThreadPoolExecutor worker threads do NOT inherit the calling thread's
        # contextvars — a new thread starts with default ContextVar values. So
        # _client_slug_ctx, set above via _client_context, is invisible inside a
        # bare pool.submit(fn) and the fetch helpers fall back to the Penn-only
        # default_target() — silently querying the wrong project.
        #
        # Capture a fresh copy of the current context (which includes the slug
        # set above) for each task and run the callable inside it. A separate
        # copy per task avoids the "cannot enter context: already entered" error
        # that a single shared Context object raises across concurrent threads.
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {
                key: pool.submit(contextvars.copy_context().run, fn)
                for key, fn in tasks.items()
            }
            for key, fut in futures.items():
                try:
                    result[key] = fut.result()
                except Exception as exc:
                    errors[key] = str(exc)[:400]

    if errors:
        result["errors"] = errors
    return result


def env_summary(client_slug: str | None = None) -> dict[str, str]:
    with _client_context(client_slug):
        return {
            "native_project":  _project_id(),
            "native_dataset":  _native_ds(),
            "mart_project":    _mart_project_id(),
            "mart_dataset":    _mart_ds(),
            "site_table":      _SITE_TABLE,
            "url_table":       _URL_TABLE,
            "hist_query_table": _QUERY_HIST_TABLE,
            "hist_page_table":  _PAGE_HIST_TABLE,
        }
