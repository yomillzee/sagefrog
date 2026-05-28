"""GA4 daily metrics from BigQuery export into metrics_daily (source=ga4)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from bigquery_service import env_summary, run_query
from dates_util import resolve_date_range
from ga4_clients import Ga4ClientTarget, list_clients_public, resolve_target


def fetch_daily_metrics(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
) -> list[dict[str, Any]]:
    """
    Site-wide GA4 daily metrics from BigQuery events_* export.

    Warehouse field mapping:
    - clicks = sessions (session_start events)
    - impressions = page_view events
    - conversions = purchase + generate_lead + sign_up events
    - spend = 0 (not applicable to GA4)
    """
    target = target or resolve_target()
    summ = env_summary()
    if not summ.get("gcp_service_account_json_parse_ok"):
        raise RuntimeError(
            summ.get("gcp_service_account_json_parse_error")
            or "GCP_SERVICE_ACCOUNT_JSON did not parse."
        )

    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = f"`{target.bq_project_id}.{target.bq_dataset_id}.events_*`"
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
    rows = run_query(sql, max_rows=2000, project_id=target.bq_project_id)
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


def sync_to_warehouse(
    *,
    date_range: str = "LAST_30_DAYS",
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    import warehouse

    if not warehouse.enabled():
        raise RuntimeError("DATABASE_URL is not set — warehouse storage is disabled.")

    target = resolve_target(
        client_key=client_key,
        bq_project_id=bq_project_id,
        bq_dataset_id=bq_dataset_id,
        account_id=account_id,
    )
    start, end, preset = resolve_date_range(date_range)
    daily_rows = fetch_daily_metrics(start=start, end=end, target=target)
    written = warehouse.upsert_metrics_daily_batch("ga4", target.account_id, daily_rows)
    coverage = warehouse.account_date_coverage("ga4", target.account_id)
    return {
        "account_id": target.account_id,
        "client_key": target.client_key,
        "label": target.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "days_synced": written,
        "coverage": coverage,
        "bq_project_id": target.bq_project_id,
        "bq_dataset_id": target.bq_dataset_id,
    }


def list_configured_clients() -> list[dict[str, Any]]:
    return list_clients_public()
