"""BigQuery marketing mart reader for the Penn BQ Test dashboard.

Reads from `fact_google_ads_campaign_daily` in the marketing_marts dataset
and builds a snapshot dict that render_penn_html() can consume directly —
giving Penn BQ Test the same Overview + Campaign Explorer template as other
dashboards without touching the snapshot/refresh workflow.

Required env vars (falls back to defaults if unset):
  BQ_MART_PROJECT_ID   — GCP project (default: penn-community-b-1699391543298)
  BQ_MART_DATASET_ID   — dataset      (default: marketing_marts)
  BQ_MART_TABLE        — table name   (default: fact_google_ads_campaign_daily)

Auth uses the same GCP_SERVICE_ACCOUNT_JSON / BQ_PROJECT_ID credentials as GA4.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import bigquery_service

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_DATASET = "marketing_marts"
_DEFAULT_TABLE = "fact_google_ads_campaign_daily"


def _project_id() -> str:
    return (os.getenv("BQ_MART_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _full_table() -> str:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    table = (os.getenv("BQ_MART_TABLE") or _DEFAULT_TABLE).strip()
    return f"`{_project_id()}.{dataset}.{table}`"


def fetch_campaign_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Return per-campaign per-day rows for the past `days` days.

    Assumes the mart has columns: date, campaign_id, campaign_name,
    spend, impressions, clicks, conversions.
    """
    table = _full_table()
    if start and end:
        where_clause = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where_clause = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      CAST(campaign_id AS STRING) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(conversions AS FLOAT64)) AS conversions
    FROM {table}
    WHERE {where_clause}
    GROUP BY 1, 2
    ORDER BY 1 DESC, 4 DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=5000)


def build_snapshot(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
    preset: str = "LAST_30_DAYS",
) -> dict[str, Any]:
    """Query the mart and return a snapshot dict compatible with render_penn_html().

    Produces:
      daily_metrics.google  — account-level daily rows (aggregated across campaigns)
      platform_totals.google — totals + campaign_count
      breakdowns.google.campaign — one row per campaign (normalized for Campaign Explorer)
      aggregated_paid_media  — cross-platform totals (Google-only here)
    """
    error: str | None = None
    rows: list[dict[str, Any]] = []
    try:
        rows = fetch_campaign_daily(days=days, start=start, end=end)
    except Exception as exc:
        error = str(exc)[:600]

    by_date: dict[str, dict[str, Any]] = {}
    by_campaign: dict[str, dict[str, Any]] = {}

    for row in rows:
        d = str(row.get("metric_date") or "")[:10]
        spend = float(row.get("spend") or 0)
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        conversions = float(row.get("conversions") or 0)
        cid = str(row.get("campaign_id") or "")
        cname = str(row.get("campaign_name") or cid or "—")

        if d:
            day = by_date.setdefault(d, {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0})
            day["spend"] += spend
            day["impressions"] += impressions
            day["clicks"] += clicks
            day["conversions"] += conversions

        if cid:
            camp = by_campaign.setdefault(cid, {
                "campaign_id": cid,
                "campaign_name": cname,
                "spend": 0.0,
                "impressions": 0,
                "clicks": 0,
                "conversions": 0.0,
            })
            camp["spend"] += spend
            camp["impressions"] += impressions
            camp["clicks"] += clicks
            camp["conversions"] += conversions
            if cname and cname != cid and not camp.get("campaign_name"):
                camp["campaign_name"] = cname

    # Account-level daily rows for the chart
    daily_google: list[dict[str, Any]] = [
        {
            "metric_date": d,
            "spend": v["spend"],
            "impressions": v["impressions"],
            "clicks": v["clicks"],
            "conversions": v["conversions"],
            "conversion_value": 0.0,
        }
        for d, v in sorted(by_date.items())
    ]

    # Campaign rows shaped like normalize_entity_row() output for Campaign Explorer
    campaign_breakdowns: list[dict[str, Any]] = sorted(
        [
            {
                "id": c["campaign_id"],
                "name": c["campaign_name"],
                "entity_level": "campaign",
                "spend": c["spend"],
                "clicks": c["clicks"],
                "impressions": c["impressions"],
                "conversions": c["conversions"],
                "parent_id": "",
                "parent_name": "",
            }
            for c in by_campaign.values()
        ],
        key=lambda r: r["spend"],
        reverse=True,
    )

    total_spend = sum(v["spend"] for v in by_date.values())
    total_impressions = sum(v["impressions"] for v in by_date.values())
    total_clicks = sum(v["clicks"] for v in by_date.values())
    total_conversions = sum(v["conversions"] for v in by_date.values())

    platform_totals_google: dict[str, Any] = {
        "spend": total_spend,
        "clicks": total_clicks,
        "impressions": total_impressions,
        "conversions": total_conversions,
        "campaign_count": len(by_campaign),
    }

    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=days - 1)

    errors: dict[str, str] = {}
    if error:
        errors["bq_mart"] = error

    return {
        "client_key": "penn-bq-test",
        "label": "Penn BQ Test",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
        "accounts": {
            "google": _project_id(),
            "linkedin": None,
            "meta": None,
        },
        "daily_metrics": {
            "google": daily_google,
        },
        "platform_totals": {
            "google": platform_totals_google,
        },
        "breakdowns": {
            "google": {
                "campaign": campaign_breakdowns,
                "ad_group": [],
                "ad": [],
            },
        },
        "aggregated_paid_media": {
            "spend": total_spend,
            "clicks": total_clicks,
            "impressions": total_impressions,
            "conversions": total_conversions,
        },
        "business_line_campaigns": [],
        "warehouse_sync": {},
        "ga4_attribution": None,
        "ga4_pages": None,
        "errors": errors,
        "refresh_mode": "bq_mart",
    }
