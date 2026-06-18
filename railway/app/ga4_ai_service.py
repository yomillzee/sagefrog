"""GA4 AI-referred traffic analytics.

Detects sessions from AI tools via traffic_source.source and returns
daily trend data (for the chart) and per-source landing pages (for the
page table filter).

Detection is source-domain based. Anonymized/direct sessions that are
not tagged with a recognisable AI domain are excluded.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ga4_clients import Ga4ClientTarget, resolve_target
from bigquery_service import run_query


# ---------------------------------------------------------------------------
# Source catalogue — single source of truth for id, label, and patterns.
# Patterns are matched with LIKE '%pattern%' against LOWER(source).
# ---------------------------------------------------------------------------

AI_SOURCES: dict[str, dict[str, Any]] = {
    "chatgpt":    {"label": "ChatGPT",    "patterns": ["chatgpt.com", "chat.openai.com"]},
    "perplexity": {"label": "Perplexity", "patterns": ["perplexity.ai"]},
    "gemini":     {"label": "Gemini",     "patterns": ["gemini.google.com", "bard.google.com"]},
    "claude":     {"label": "Claude",     "patterns": ["claude.ai"]},
    "copilot":    {"label": "Copilot",    "patterns": ["copilot.microsoft.com"]},
    "you":        {"label": "You.com",    "patterns": ["you.com"]},
    "meta_ai":    {"label": "Meta AI",    "patterns": ["meta.ai"]},
    "grok":       {"label": "Grok",       "patterns": ["grok.com", "grok.x.com"]},
    "phind":      {"label": "Phind",      "patterns": ["phind.com"]},
}


def _ai_case_sql(src_col: str = "src") -> str:
    """CASE expression mapping a lowercased source column → AI source id."""
    lines = []
    for sid, cfg in AI_SOURCES.items():
        for pattern in cfg["patterns"]:
            lines.append(f"WHEN {src_col} LIKE '%{pattern}%' THEN '{sid}'")
    return "CASE\n        " + "\n        ".join(lines) + "\n        ELSE NULL\n      END"


def _events_table(target: Ga4ClientTarget) -> str:
    return f"`{target.bq_project_id}.{target.bq_dataset_id}.events_*`"


def _run(sql: str, target: Ga4ClientTarget, max_rows: int = 10000) -> list[dict[str, Any]]:
    return run_query(
        sql,
        project_id=target.bq_project_id,
        max_rows=max_rows,
        credentials_env=target.credentials_env,
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def fetch_ai_daily(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
) -> list[dict[str, Any]]:
    """Daily sessions + users per AI source. Used for the trend chart."""
    target = target or resolve_target()
    table = _events_table(target)
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    ai_case = _ai_case_sql()

    sql = f"""
    WITH session_starts AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        PARSE_DATE('%Y%m%d', event_date) AS metric_date,
        {ai_case} AS ai_source
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{s}' AND '{e}'
        AND event_name = 'session_start'
    )
    SELECT
      CAST(metric_date AS STRING)                                       AS date,
      ai_source,
      COUNT(DISTINCT CONCAT(user_pseudo_id, '-',
        CAST(ga_session_id AS STRING)))                                 AS sessions,
      COUNT(DISTINCT user_pseudo_id)                                    AS users
    FROM session_starts
    WHERE ai_source IS NOT NULL
    GROUP BY 1, 2
    ORDER BY 1, sessions DESC
    """
    rows = _run(sql, target, max_rows=10000)
    return [
        {
            "date":     str(r.get("date") or "")[:10],
            "source":   str(r.get("ai_source") or ""),
            "sessions": int(r.get("sessions") or 0),
            "users":    int(r.get("users") or 0),
        }
        for r in rows
        if r.get("ai_source") and r.get("date")
    ]


def fetch_ai_pages(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Landing pages for AI-referred sessions, tagged with source.

    Returns rows suitable for the pages table (same shape as fetch_page_metrics
    output, plus a 'source' field).
    """
    target = target or resolve_target()
    table = _events_table(target)
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    ai_case = _ai_case_sql()
    row_limit = max(1, min(int(limit) * len(AI_SOURCES), 5000))

    sql = f"""
    WITH raw AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value  FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        PARSE_DATE('%Y%m%d', event_date)                                                  AS metric_date,
        event_name,
        event_timestamp,
        {ai_case} AS ai_source,
        COALESCE(
          NULLIF(TRIM((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_path')), ''),
          REGEXP_EXTRACT(
            (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
            r'https?://[^/]+(/[^?#]*)'
          )
        ) AS page_path,
        COALESCE(
          NULLIF(TRIM((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_title')), ''),
          '(not set)'
        ) AS page_title,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec') AS eng_ms
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{s}' AND '{e}'
        AND event_name IN ('session_start', 'page_view')
    ),
    -- which sessions came from an AI source
    ai_sessions AS (
      SELECT user_pseudo_id, ga_session_id, metric_date, ai_source
      FROM raw
      WHERE event_name = 'session_start' AND ai_source IS NOT NULL
    ),
    -- first page_view per session (landing page)
    first_pv AS (
      SELECT
        user_pseudo_id,
        ga_session_id,
        metric_date,
        page_path,
        page_title,
        eng_ms,
        ROW_NUMBER() OVER (
          PARTITION BY user_pseudo_id, ga_session_id, metric_date
          ORDER BY event_timestamp
        ) AS rn
      FROM raw
      WHERE event_name = 'page_view' AND IFNULL(page_path, '') != ''
    )
    SELECT
      ai.ai_source                                                        AS source,
      lp.page_path,
      MAX(lp.page_title)                                                  AS page_title,
      COUNT(DISTINCT CONCAT(lp.user_pseudo_id, '-',
        CAST(lp.ga_session_id AS STRING)))                                AS sessions,
      COUNT(DISTINCT lp.user_pseudo_id)                                   AS users,
      COUNT(DISTINCT IF(IFNULL(lp.eng_ms, 0) > 0,
        CONCAT(lp.user_pseudo_id, '-', CAST(lp.ga_session_id AS STRING)),
        NULL))                                                             AS engaged_sessions
    FROM first_pv lp
    INNER JOIN ai_sessions ai
      ON lp.user_pseudo_id = ai.user_pseudo_id
     AND lp.ga_session_id  = ai.ga_session_id
     AND lp.metric_date    = ai.metric_date
    WHERE lp.rn = 1
    GROUP BY 1, 2
    ORDER BY sessions DESC
    LIMIT {row_limit}
    """
    rows = _run(sql, target, max_rows=row_limit + 20)
    out = []
    for r in rows:
        sessions = int(r.get("sessions") or 0)
        engaged  = int(r.get("engaged_sessions") or 0)
        out.append({
            "source":          str(r.get("source") or ""),
            "page_path":       str(r.get("page_path") or ""),
            "page_title":      str(r.get("page_title") or "(not set)").strip() or "(not set)",
            "sessions":        sessions,
            "users":           int(r.get("users") or 0),
            "engaged_sessions": engaged,
            "engagement_rate": round(engaged / sessions, 4) if sessions else 0.0,
        })
    return out


# ---------------------------------------------------------------------------
# Builder — used by ga4_page_service
# ---------------------------------------------------------------------------

def build_ai_traffic(
    daily_rows: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Organise raw query results into the shape the renderer expects.

    Returns:
        daily:          [{date, source, sessions, users}]  — chart data
        pages_by_source: {all: [...], chatgpt: [...], ...} — pages table
        sources:        [{id, label, sessions}]            — filter buttons
    """
    # --- aggregate per-source totals for the filter buttons --------------------
    source_totals: dict[str, int] = {}
    for r in daily_rows:
        sid = r.get("source") or ""
        source_totals[sid] = source_totals.get(sid, 0) + int(r.get("sessions") or 0)

    # build catalogue (only sources that have data, sorted by volume)
    sources: list[dict[str, Any]] = [
        {"id": sid, "label": AI_SOURCES[sid]["label"], "sessions": source_totals.get(sid, 0)}
        for sid in AI_SOURCES
        if source_totals.get(sid, 0) > 0
    ]
    sources.sort(key=lambda x: -x["sessions"])

    # --- pages by source -------------------------------------------------------
    pages_by_source: dict[str, list[dict[str, Any]]] = {"all": []}
    all_pages: dict[str, dict[str, Any]] = {}

    for r in page_rows:
        sid = r.get("source") or ""
        if sid not in pages_by_source:
            pages_by_source[sid] = []
        pages_by_source[sid].append(r)

        # merge into "all"
        key = r.get("page_path") or ""
        if key not in all_pages:
            all_pages[key] = dict(r)
            all_pages[key]["source"] = "all"
        else:
            for f in ("sessions", "users", "engaged_sessions"):
                all_pages[key][f] = (all_pages[key].get(f) or 0) + (r.get(f) or 0)

    # recompute engagement_rate on merged rows
    for row in all_pages.values():
        sess = row.get("sessions") or 0
        eng  = row.get("engaged_sessions") or 0
        row["engagement_rate"] = round(eng / sess, 4) if sess else 0.0

    pages_by_source["all"] = sorted(
        all_pages.values(), key=lambda r: -(r.get("sessions") or 0)
    )

    return {
        "daily":           daily_rows,
        "pages_by_source": pages_by_source,
        "sources":         sources,
    }
