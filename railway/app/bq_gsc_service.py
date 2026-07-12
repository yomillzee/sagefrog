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
from decimal import Decimal
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


def _run(sql: str, max_rows: int = 500, *, query_parameters: list[Any] | None = None) -> list[dict[str, Any]]:
    target = _resolved_target()
    credentials_env = None if target.is_default_fallback else target.credentials_env
    return bigquery_service.run_query(
        sql, project_id=_project_id(), credentials_env=credentials_env, max_rows=max_rows,
        query_parameters=query_parameters,
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
    """Top queries for [start, end] with each query's prior-period avg position.

    The CTE is widened to cover the prior window too so we can compute, per
    query, the position it held in the immediately-preceding same-length period.
    `delta_position` is prior − current: positive means the keyword *improved*
    (moved toward rank 1, since a lower position number is better), negative
    means it sank. It's None when the query had no impressions in the prior
    period (i.e. newly ranking), so the UI can flag it as "new".
    """
    prior_start, prior_end = _prior_period(start, end)
    s, e   = start.isoformat(), end.isoformat()
    ps, pe = prior_start.isoformat(), prior_end.isoformat()
    sql = f"""
    WITH {_query_cte(prior_start, end)}
    SELECT
      {_anon_label()}                                          AS query,
      SUM(IF(date BETWEEN '{s}' AND '{e}', clicks, 0))         AS clicks,
      SUM(IF(date BETWEEN '{s}' AND '{e}', impressions, 0))    AS impressions,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{s}' AND '{e}', clicks, 0)),
        NULLIF(SUM(IF(date BETWEEN '{s}' AND '{e}', impressions, 0)), 0)
      )                                                         AS ctr,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{s}' AND '{e}', pos_sum, 0.0)),
        NULLIF(SUM(IF(date BETWEEN '{s}' AND '{e}', impressions, 0)), 0)
      ) + 1                                                     AS avg_position,
      SAFE_DIVIDE(
        SUM(IF(date BETWEEN '{ps}' AND '{pe}', pos_sum, 0.0)),
        NULLIF(SUM(IF(date BETWEEN '{ps}' AND '{pe}', impressions, 0)), 0)
      ) + 1                                                     AS prior_avg_position
    FROM gsc
    GROUP BY 1
    -- Qualify `impressions` with the CTE name: an unqualified reference here
    -- binds to the SELECT-list alias of the same name (itself a SUM), which
    -- BigQuery rejects as "aggregations of aggregations" and would blank the
    -- whole table.
    HAVING SUM(IF(date BETWEEN '{s}' AND '{e}', gsc.impressions, 0)) > 0
    ORDER BY clicks DESC, impressions DESC
    LIMIT {int(limit)}
    """
    rows = _run_with_hist_fallback(sql, max_rows=limit + 5)
    out: list[dict[str, Any]] = []
    for r in rows:
        avg_position = float(r.get("avg_position") or 0)
        prior_raw = r.get("prior_avg_position")
        prior_position = float(prior_raw) if prior_raw is not None else None
        delta_position = (
            round(prior_position - avg_position, 1)
            if (prior_position and avg_position) else None
        )
        out.append({
            "query":              str(r.get("query") or "(anonymized query)"),
            "clicks":             int(r.get("clicks") or 0),
            "impressions":        int(r.get("impressions") or 0),
            "ctr":                float(r.get("ctr") or 0) * 100,
            "avg_position":       avg_position,
            "prior_avg_position": round(prior_position, 1) if prior_position else None,
            "delta_position":     delta_position,
        })
    return out


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

    # Skip entirely for clients with no GSC BQ config — avoids leaking Penn's data
    # into an unrelated client's dashboard when resolve_target falls back to default.
    if client_slug:
        import gsc_clients as _gsc_c
        _t = _gsc_c.resolve_target(client_slug)
        _is_penn = client_slug in ("penn",) or client_slug.startswith("penn-")
        if _t.is_default_fallback and not _is_penn:
            return {}

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


# ---------------------------------------------------------------------------
# GSC mart views — normalised reporting surface over the raw hist tables.
# vw_gsc_query_daily / vw_gsc_page_daily: one clean per-day-per-dimension grain
# (date, dimension, clicks, impressions, pos_sum). vw_gsc_daily: per-day totals.
# These are what the Search Console dashboard reads.
# ---------------------------------------------------------------------------

_QUERY_VIEW = "vw_gsc_query_daily"
_PAGE_VIEW  = "vw_gsc_page_daily"
_DAILY_VIEW = "vw_gsc_daily"


def _is_penn_slug(client_slug: str | None) -> bool:
    return bool(client_slug) and (client_slug == "penn" or client_slug.startswith("penn-"))


def _reporting_mart_ds() -> str:
    """The reporting mart dataset (marketing_marts) — where the vw_gsc_* views live.

    Distinct from _mart_ds()/the GSC routing dataset, which for connector clients
    is the raw dataset (raw_gsc) where the fact_gsc_* tables are written.
    """
    return (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DS).strip()


def create_gsc_mart_views(client_slug: str | None = None) -> dict[str, Any]:
    """Create/replace the GSC mart views in marketing_marts, reading from raw_gsc."""
    with _client_context(client_slug):
        target = _resolved_target()
        if target.is_default_fallback and not _is_penn_slug(client_slug):
            return {"ok": False, "reason": "no_gsc_target", "views": {}}
        project = _project_id()
        raw_ds = _mart_ds()              # GSC routing dataset — fact_gsc_* live here (raw_gsc)
        mart_ds = _reporting_mart_ds()   # reporting mart — views go here (marketing_marts)
        creds_env = None if target.is_default_fallback else target.credentials_env
        client = bigquery_service.build_client(project, credentials_env=creds_env)
        try:
            from google.cloud import bigquery as _bq
            client.create_dataset(_bq.Dataset(f"{project}.{mart_ds}"), exists_ok=True)
        except Exception:
            pass
        q = f"`{project}.{raw_ds}.{_QUERY_HIST_TABLE}`"
        p = f"`{project}.{raw_ds}.{_PAGE_HIST_TABLE}`"
        views = {
            _QUERY_VIEW: f"""
                SELECT
                  date,
                  IF(is_anonymized_query, '(anonymized query)', query) AS query,
                  organic_clicks       AS clicks,
                  organic_impressions  AS impressions,
                  organic_sum_position AS pos_sum
                FROM {q}
            """,
            _PAGE_VIEW: f"""
                SELECT
                  date,
                  page_url,
                  organic_clicks       AS clicks,
                  organic_impressions  AS impressions,
                  organic_sum_position AS pos_sum
                FROM {p}
            """,
            _DAILY_VIEW: f"""
                SELECT
                  date,
                  SUM(clicks)      AS clicks,
                  SUM(impressions) AS impressions,
                  ROUND(SAFE_DIVIDE(SUM(clicks), NULLIF(SUM(impressions), 0)) * 100, 2) AS ctr,
                  ROUND(SAFE_DIVIDE(SUM(pos_sum), NULLIF(SUM(impressions), 0)) + 1, 1)  AS avg_position
                FROM `{project}.{mart_ds}.{_QUERY_VIEW}`
                GROUP BY date
            """,
        }
        results: dict[str, str] = {}
        for name, sql in views.items():
            try:
                client.query(f"CREATE OR REPLACE VIEW `{project}.{mart_ds}.{name}` AS\n{sql}").result(timeout=60)
                results[name] = "ok"
            except Exception as exc:
                results[name] = f"error: {str(exc)[:200]}"
        return {"ok": True, "project": project, "raw_dataset": raw_ds, "mart_dataset": mart_ds, "views": results}


def _gsc_metric_cols() -> str:
    return (
        "SUM(clicks) AS clicks, SUM(impressions) AS impressions, "
        "ROUND(SAFE_DIVIDE(SUM(clicks), NULLIF(SUM(impressions), 0)) * 100, 2) AS ctr, "
        "ROUND(SAFE_DIVIDE(SUM(pos_sum), NULLIF(SUM(impressions), 0)) + 1, 1) AS avg_position"
    )


def _query_delta_metric_cols(s: str, e: str, ps: str, pe: str, alias: str = "q") -> str:
    """SELECT-list metric columns for a per-query GROUP BY that reports the
    current window's clicks/impressions/ctr/avg_position AND the prior window's
    avg_position, via conditional aggregation over a single scan widened to
    cover both windows.

    Every base column is qualified with `alias` on purpose: the SELECT aliases
    `clicks`/`impressions` shadow the same-named base columns, and an
    unqualified reference to them inside the HAVING (see
    `_query_delta_having`) or ORDER BY binds to the aggregate alias, which
    BigQuery rejects as "aggregations of aggregations". Callers MUST alias the
    scanned source `AS {alias}`.
    """
    a = alias
    cur_impr = f"SUM(IF({a}.date BETWEEN '{s}' AND '{e}', {a}.impressions, 0))"
    cur_clk  = f"SUM(IF({a}.date BETWEEN '{s}' AND '{e}', {a}.clicks, 0))"
    cur_pos  = f"SUM(IF({a}.date BETWEEN '{s}' AND '{e}', {a}.pos_sum, 0.0))"
    pri_impr = f"SUM(IF({a}.date BETWEEN '{ps}' AND '{pe}', {a}.impressions, 0))"
    pri_pos  = f"SUM(IF({a}.date BETWEEN '{ps}' AND '{pe}', {a}.pos_sum, 0.0))"
    return (
        f"{cur_clk} AS clicks, "
        f"{cur_impr} AS impressions, "
        f"ROUND(SAFE_DIVIDE({cur_clk}, NULLIF({cur_impr}, 0)) * 100, 2) AS ctr, "
        f"ROUND(SAFE_DIVIDE({cur_pos}, NULLIF({cur_impr}, 0)) + 1, 1) AS avg_position, "
        f"ROUND(SAFE_DIVIDE({pri_pos}, NULLIF({pri_impr}, 0)) + 1, 1) AS prior_avg_position"
    )


def _query_delta_having(s: str, e: str, alias: str = "q") -> str:
    """HAVING predicate keeping only queries with current-window impressions,
    so a widened scan doesn't surface queries that existed only in the prior
    window. Qualified with `alias` for the shadowing reason above."""
    return f"SUM(IF({alias}.date BETWEEN '{s}' AND '{e}', {alias}.impressions, 0)) > 0"


def _attach_delta_position(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add `delta_position` = prior_avg_position − avg_position (positive =
    improved toward rank 1) to each row. None when either side is missing —
    e.g. a query that didn't rank in the prior window, flagged "New" in the UI.
    """
    for r in rows:
        cur = r.get("avg_position")
        pri = r.get("prior_avg_position")
        r["delta_position"] = round(pri - cur, 1) if (cur and pri) else None
    return rows


def build_gsc_mart_summary(*, start: date, end: date, client_slug: str | None = None) -> dict[str, Any]:
    """Read the GSC mart views and return {kpis, daily, top_queries, top_pages}.

    Same shape build_gsc_snapshot returns, so the dashboard tab is unchanged.
    """
    with _client_context(client_slug):
        target = _resolved_target()
        if target.is_default_fallback and not _is_penn_slug(client_slug):
            return {}
        project, ds = _project_id(), _reporting_mart_ds()
        s, e = start.isoformat(), end.isoformat()
        qv = f"`{project}.{ds}.{_QUERY_VIEW}`"
        pv = f"`{project}.{ds}.{_PAGE_VIEW}`"
        dv = f"`{project}.{ds}.{_DAILY_VIEW}`"
        where = f"date BETWEEN '{s}' AND '{e}'"
        m = _gsc_metric_cols()

        def _clean(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for r in rows:
                rr = {}
                for k, v in r.items():
                    if isinstance(v, Decimal):
                        v = float(v)
                    elif isinstance(v, date):
                        v = v.isoformat()
                    rr[k] = v
                out.append(rr)
            return out

        errors: dict[str, str] = {}
        result: dict[str, Any] = {"kpis": {}, "daily": [], "top_queries": [], "top_pages": []}
        try:
            kpi = _clean(_run(f"SELECT {m} FROM {qv} WHERE {where}", max_rows=1))
            result["kpis"] = kpi[0] if kpi else {}
        except Exception as exc:
            errors["kpis"] = str(exc)[:300]
        # Prior-period KPIs (same-length window immediately before) so the
        # dashboard can show vs-previous deltas. Additive to the kpis dict; the
        # frontend computes the % change from these prior_* values.
        if result["kpis"]:
            try:
                ps, pe = _prior_period(start, end)
                pw = f"date BETWEEN '{ps.isoformat()}' AND '{pe.isoformat()}'"
                prior = _clean(_run(f"SELECT {m} FROM {qv} WHERE {pw}", max_rows=1))
                pr = prior[0] if prior else {}
                result["kpis"].update({
                    "prior_clicks":       pr.get("clicks"),
                    "prior_impressions":  pr.get("impressions"),
                    "prior_ctr":          pr.get("ctr"),
                    "prior_avg_position": pr.get("avg_position"),
                    "prior_start":        ps.isoformat(),
                    "prior_end":          pe.isoformat(),
                })
            except Exception as exc:
                errors["kpis_prior"] = str(exc)[:300]
        try:
            result["daily"] = _clean(_run(
                f"SELECT CAST(date AS STRING) AS date, clicks, impressions, ctr, avg_position "
                f"FROM {dv} WHERE {where} ORDER BY date", max_rows=2000))
        except Exception as exc:
            errors["daily"] = str(exc)[:300]
        try:
            # Top queries with each query's prior-period avg position, so the UI
            # can show which keywords rose or sank. Conditional aggregation over
            # a window widened to cover the prior period; delta_position is
            # prior − current (positive = improved toward rank 1). None when the
            # query had no impressions in the prior period (newly ranking).
            ps, pe = _prior_period(start, end)
            ps_s, pe_s = ps.isoformat(), pe.isoformat()
            tq_sql = f"""
            SELECT q.query AS query, {_query_delta_metric_cols(s, e, ps_s, pe_s)}
            FROM {qv} AS q
            WHERE q.date BETWEEN '{ps_s}' AND '{e}'
            GROUP BY q.query
            HAVING {_query_delta_having(s, e)}
            ORDER BY clicks DESC, impressions DESC
            LIMIT 25
            """
            result["top_queries"] = _attach_delta_position(_clean(_run(tq_sql, max_rows=30)))
        except Exception as exc:
            errors["top_queries"] = str(exc)[:300]
        try:
            result["top_pages"] = _clean(_run(
                f"SELECT page_url, {m} FROM {pv} WHERE {where} GROUP BY page_url "
                f"ORDER BY clicks DESC, impressions DESC LIMIT 25", max_rows=30))
        except Exception as exc:
            errors["top_pages"] = str(exc)[:300]
        if errors:
            result["errors"] = errors
        return result


def gsc_keyword_matches(
    *, start: date, end: date, terms: list[str], client_slug: str | None = None
) -> list[dict[str, Any]]:
    """Queries whose text contains any of `terms`, aggregated over the full
    date range -- unlike top_queries in build_gsc_mart_summary (LIMIT 25 by
    clicks), this scans every matching query regardless of its click rank, so
    a branded/target keyword that isn't a top performer still shows up.
    """
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return []
    with _client_context(client_slug):
        target = _resolved_target()
        if target.is_default_fallback and not _is_penn_slug(client_slug):
            return []
        project, ds = _project_id(), _reporting_mart_ds()
        qv = f"`{project}.{ds}.{_QUERY_VIEW}`"
        s, e = start.isoformat(), end.isoformat()
        # Same per-query current metrics + prior-period avg position as
        # build_gsc_mart_summary's top_queries, so branded/target rows carry the
        # same sortable Δ Position. Scan is widened to the prior window; the
        # HAVING keeps only queries active in the selected range.
        ps, pe = _prior_period(start, end)
        ps_s, pe_s = ps.isoformat(), pe.isoformat()
        sql = f"""
        SELECT q.query AS query, {_query_delta_metric_cols(s, e, ps_s, pe_s)}
        FROM {qv} AS q
        WHERE q.date BETWEEN '{ps_s}' AND '{e}'
          AND EXISTS (
            SELECT 1 FROM UNNEST(@terms) AS t
            WHERE LOWER(q.query) LIKE CONCAT('%', t, '%')
          )
        GROUP BY q.query
        HAVING {_query_delta_having(s, e)}
        ORDER BY clicks DESC, impressions DESC
        LIMIT 500
        """
        from google.cloud import bigquery as _bq
        rows = _run(
            sql, max_rows=500,
            query_parameters=[_bq.ArrayQueryParameter("terms", "STRING", terms)],
        )
        out = []
        for r in rows:
            rr = {}
            for k, v in r.items():
                if isinstance(v, Decimal):
                    v = float(v)
                rr[k] = v
            out.append(rr)
        return _attach_delta_position(out)


def gsc_keyword_weekly_trend(
    *, start: date, end: date, terms: list[str], client_slug: str | None = None
) -> list[dict[str, Any]]:
    """Weekly (Monday-start) clicks/impressions rollup for queries matching
    `terms`, over the full date range -- same term-matching as
    gsc_keyword_matches, grouped by week instead of collapsed to one total,
    so branded/target keyword performance can be plotted over time.
    """
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return []
    with _client_context(client_slug):
        target = _resolved_target()
        if target.is_default_fallback and not _is_penn_slug(client_slug):
            return []
        project, ds = _project_id(), _reporting_mart_ds()
        qv = f"`{project}.{ds}.{_QUERY_VIEW}`"
        m = _gsc_metric_cols()
        s, e = start.isoformat(), end.isoformat()
        sql = f"""
        SELECT
          CAST(DATE_TRUNC(date, WEEK(MONDAY)) AS STRING) AS week_start,
          {m}
        FROM {qv}
        WHERE date BETWEEN '{s}' AND '{e}'
          AND EXISTS (
            SELECT 1 FROM UNNEST(@terms) AS t
            WHERE LOWER(query) LIKE CONCAT('%', t, '%')
          )
        GROUP BY week_start
        ORDER BY week_start
        """
        from google.cloud import bigquery as _bq
        rows = _run(
            sql, max_rows=200,
            query_parameters=[_bq.ArrayQueryParameter("terms", "STRING", terms)],
        )
        out = []
        for r in rows:
            rr = {}
            for k, v in r.items():
                if isinstance(v, Decimal):
                    v = float(v)
                rr[k] = v
            out.append(rr)
        return out


def gsc_health_row(client_slug: str | None = None) -> dict[str, Any]:
    """MIN/MAX(date) + row count over the GSC mart, for the marketing/health
    endpoint's data-availability check (mirrors the GA4 row it already adds).
    """
    with _client_context(client_slug):
        target = _resolved_target()
        if target.is_default_fallback and not _is_penn_slug(client_slug):
            return {}
        project, ds = _project_id(), _reporting_mart_ds()
        dv = f"`{project}.{ds}.{_DAILY_VIEW}`"
        try:
            rows = _run(
                f"SELECT COUNT(*) AS row_count, MIN(date) AS earliest_date, "
                f"MAX(date) AS latest_date FROM {dv}",
                max_rows=1,
            )
        except Exception:
            return {}
        if not rows or not rows[0].get("row_count"):
            return {}
        r = rows[0]
        earliest = r.get("earliest_date")
        latest = r.get("latest_date")
        return {
            "source": "gsc",
            "row_count": int(r["row_count"]),
            "earliest_date": earliest.isoformat() if isinstance(earliest, date) else earliest,
            "latest_date": latest.isoformat() if isinstance(latest, date) else latest,
            "spend": None,
            "impressions": None,
            "clicks": None,
            "conversions": None,
        }
