"""GA4 daily metrics from BigQuery export into metrics_daily (source=ga4)."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from bigquery_service import env_summary, run_query
from dates_util import resolve_date_range


def ga4_account_id() -> str:
    """Stable warehouse key for GA4 property (dataset id or explicit GA4_PROPERTY_ID)."""
    explicit = (os.getenv("GA4_PROPERTY_ID") or "").strip()
    if explicit:
        return explicit.replace("properties/", "").split("/")[-1]
    summ = env_summary()
    dataset = summ.get("bq_dataset_id") or ""
    if dataset:
        return str(dataset).replace("analytics_", "")
    project = summ.get("bq_project_id") or "ga4"
    return str(project)


def fetch_daily_metrics(*, start: date, end: date) -> list[dict[str, Any]]:
    """
    Site-wide GA4 daily metrics from BigQuery events_* export.

    Warehouse field mapping:
    - clicks = sessions (session_start events)
    - impressions = page_view events
    - conversions = purchase + generate_lead + sign_up events
    - spend = 0 (not applicable to GA4)
    """
    summ = env_summary()
    project = summ.get("bq_project_id")
    dataset = summ.get("bq_dataset_id")
    if not project or not dataset:
        raise RuntimeError("BQ_PROJECT_ID and BQ_DATASET_ID are required for GA4 warehouse sync.")
    if not summ.get("gcp_service_account_json_parse_ok"):
        raise RuntimeError(
            summ.get("gcp_service_account_json_parse_error")
            or "GCP_SERVICE_ACCOUNT_JSON did not parse."
        )

    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = f"`{project}.{dataset}.events_*`"
    sql = f"""
        SELECT
          PARSE_DATE('%Y%m%d', event_date) AS metric_date,
          COUNTIF(event_name = 'session_start') AS sessions,
          COUNTIF(event_name = 'page_view') AS page_views,
          COUNTIF(event_name IN ('purchase', 'generate_lead', 'sign_up', 'form_submit')) AS conversions
        FROM {table}
        WHERE _TABLE_SUFFIX BETWEEN '{suffix_start}' AND '{suffix_end}'
        GROUP BY metric_date
        ORDER BY metric_date
    """
    rows = run_query(sql, max_rows=2000)
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw_date = row.get("metric_date") or row.get("event_date")
        if hasattr(raw_date, "isoformat"):
            key = raw_date.isoformat()
        else:
            key = str(raw_date).strip()[:10]
        if not key:
            continue
        by_day[key] = {
            "metric_date": key,
            "spend": 0.0,
            "clicks": int(row.get("sessions") or 0),
            "impressions": int(row.get("page_views") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": 0.0,
        }

    out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        out.append(
            by_day.get(key)
            or {
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


def sync_to_warehouse(*, date_range: str = "LAST_30_DAYS") -> dict[str, Any]:
    import warehouse

    if not warehouse.enabled():
        raise RuntimeError("DATABASE_URL is not set — warehouse storage is disabled.")

    start, end, preset = resolve_date_range(date_range)
    daily_rows = fetch_daily_metrics(start=start, end=end)
    account_id = ga4_account_id()
    written = warehouse.upsert_metrics_daily_batch("ga4", account_id, daily_rows)
    coverage = warehouse.account_date_coverage("ga4", account_id)
    return {
        "account_id": account_id,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "days_synced": written,
        "coverage": coverage,
        "bq_project_id": env_summary().get("bq_project_id"),
        "bq_dataset_id": env_summary().get("bq_dataset_id"),
    }
