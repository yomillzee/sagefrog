"""GA4 page path / title metrics from BigQuery events export."""

from __future__ import annotations

from datetime import date
from typing import Any

from bigquery_service import env_summary, run_query
from dates_util import resolve_date_range
from ga4_attribution_service import KEY_EVENT_NAMES
from ga4_clients import Ga4ClientTarget, resolve_target


def _events_table(target: Ga4ClientTarget) -> str:
    return f"`{target.bq_project_id}.{target.bq_dataset_id}.events_*`"


def _key_events_sql_list() -> str:
    return ", ".join(f"'{name}'" for name in KEY_EVENT_NAMES)


def _ensure_bq_ready() -> None:
    summ = env_summary()
    if not summ.get("gcp_service_account_json_parse_ok"):
        raise RuntimeError(
            summ.get("gcp_service_account_json_parse_error")
            or "GCP_SERVICE_ACCOUNT_JSON did not parse."
        )


def fetch_page_metrics(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Aggregate page path/title metrics for the date range (site-wide traffic)."""
    target = target or resolve_target()
    _ensure_bq_ready()

    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = _events_table(target)
    key_events = _key_events_sql_list()
    row_limit = max(1, min(int(limit), 2000))

    sql = f"""
    WITH page_events AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        event_name,
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
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec') AS engagement_time_msec
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{suffix_start}' AND '{suffix_end}'
        AND event_name IN ('page_view', {key_events})
    ),
    normalized AS (
      SELECT
        user_pseudo_id,
        ga_session_id,
        event_name,
        IFNULL(page_path, '') AS page_path,
        page_title,
        engagement_time_msec
      FROM page_events
      WHERE page_path != ''
    )
    SELECT
      page_path,
      page_title,
      COUNTIF(event_name = 'page_view') AS page_views,
      COUNT(DISTINCT IF(
        event_name = 'page_view',
        CONCAT(user_pseudo_id, '-', CAST(ga_session_id AS STRING)),
        NULL
      )) AS sessions,
      COUNT(DISTINCT IF(event_name = 'page_view', user_pseudo_id, NULL)) AS users,
      COUNT(DISTINCT IF(
        event_name = 'page_view' AND IFNULL(engagement_time_msec, 0) > 0,
        CONCAT(user_pseudo_id, '-', CAST(ga_session_id AS STRING)),
        NULL
      )) AS engaged_sessions,
      COUNTIF(event_name IN ({key_events})) AS key_events
    FROM normalized
    GROUP BY page_path, page_title
    ORDER BY page_views DESC
    LIMIT {row_limit}
    """
    rows = run_query(sql, max_rows=row_limit, project_id=target.bq_project_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        path = str(row.get("page_path") or "").strip()
        if not path:
            continue
        sessions = int(row.get("sessions") or 0)
        engaged = int(row.get("engaged_sessions") or 0)
        out.append(
            {
                "page_path": path,
                "page_title": str(row.get("page_title") or "(not set)").strip() or "(not set)",
                "page_views": int(row.get("page_views") or 0),
                "sessions": sessions,
                "users": int(row.get("users") or 0),
                "engaged_sessions": engaged,
                "engagement_rate": round(engaged / sessions, 4) if sessions else 0.0,
                "key_events": int(row.get("key_events") or 0),
            }
        )
    return out


def fetch_pages_for_dashboard(
    *,
    date_range: str = "LAST_30_DAYS",
    client_key: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    target = resolve_target(client_key=client_key)
    start, end, preset = resolve_date_range(date_range)
    pages = fetch_page_metrics(start=start, end=end, target=target, limit=limit)
    return {
        "client_key": target.client_key,
        "account_id": target.account_id,
        "bq_project_id": target.bq_project_id,
        "bq_dataset_id": target.bq_dataset_id,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "row_count": len(pages),
        "pages": pages,
    }
