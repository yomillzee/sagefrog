"""BigQuery marketing mart reader for the Penn BQ Test dashboard.

Reads from `fact_google_ads_campaign_daily` in the marketing_marts dataset.

Required env vars (falls back to defaults if unset):
  BQ_MART_PROJECT_ID   — GCP project (default: penn-community-b-1699391543298)
  BQ_MART_DATASET_ID   — dataset      (default: marketing_marts)
  BQ_MART_TABLE        — table name   (default: fact_google_ads_campaign_daily)

Auth uses the same GCP_SERVICE_ACCOUNT_JSON / BQ_PROJECT_ID credentials as GA4.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
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


def fetch_campaign_daily(*, days: int = 30) -> list[dict[str, Any]]:
    """Return per-campaign per-day rows for the past `days` days.

    Assumes the mart has columns: date, campaign_id, campaign_name,
    spend (or cost), impressions, clicks, conversions.
    """
    table = _full_table()
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
    WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)
    GROUP BY 1, 2
    ORDER BY 1 DESC, 4 DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=5000)


def build_dashboard_payload(*, days: int = 30) -> dict[str, Any]:
    """Query the mart and aggregate into a dashboard-ready payload.

    Returns:
        {
          "rows": [...],          # raw per-campaign-per-day rows
          "totals": {...},        # spend/impressions/clicks/conversions summed
          "by_date": {...},       # {date_str: {spend, impressions, clicks, conversions}}
          "by_campaign": [...],   # [{campaign_id, campaign_name, spend, ...}] sorted by spend desc
          "date_range": {"start": ..., "end": ..., "days": ...},
          "row_count": int,
          "error": str | None,
        }
    """
    error: str | None = None
    rows: list[dict[str, Any]] = []
    try:
        rows = fetch_campaign_daily(days=days)
    except Exception as exc:
        error = str(exc)[:500]

    totals: dict[str, float] = {
        "spend": 0.0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0.0,
    }
    by_date: dict[str, dict[str, Any]] = {}
    by_campaign: dict[str, dict[str, Any]] = {}

    for row in rows:
        d = str(row.get("metric_date") or "")[:10]
        spend = float(row.get("spend") or 0)
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        conversions = float(row.get("conversions") or 0)
        cid = str(row.get("campaign_id") or "")
        cname = str(row.get("campaign_name") or cid)

        totals["spend"] += spend
        totals["impressions"] += impressions
        totals["clicks"] += clicks
        totals["conversions"] += conversions

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
            if cname and not camp.get("campaign_name"):
                camp["campaign_name"] = cname

    sorted_campaigns = sorted(by_campaign.values(), key=lambda c: c["spend"], reverse=True)
    end = date.today()
    start = end - timedelta(days=days - 1)

    return {
        "rows": rows,
        "totals": totals,
        "by_date": by_date,
        "by_campaign": sorted_campaigns,
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
        },
        "row_count": len(rows),
        "error": error,
    }
