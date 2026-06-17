"""BigQuery marketing mart reader for the Penn BQ Test dashboard.

Reads from `fact_google_ads_campaign_daily` and `fact_linkedin_ads_campaign_daily`
in the marketing_marts dataset and builds a snapshot dict that render_penn_html()
can consume directly — giving Penn BQ Test the same Overview + Campaign Explorer
template as other dashboards without touching the snapshot/refresh workflow.

Required env vars (falls back to defaults if unset):
  BQ_MART_PROJECT_ID         — GCP project (default: penn-community-b-1699391543298)
  BQ_MART_DATASET_ID         — dataset      (default: marketing_marts)
  BQ_MART_TABLE              — Google table (default: fact_google_ads_campaign_daily)
  BQ_MART_LINKEDIN_TABLE     — LinkedIn table (default: fact_linkedin_ads_campaign_daily)

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
_DEFAULT_LINKEDIN_TABLE = "fact_linkedin_ads_campaign_daily"


def _project_id() -> str:
    return (os.getenv("BQ_MART_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _full_table() -> str:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    table = (os.getenv("BQ_MART_TABLE") or _DEFAULT_TABLE).strip()
    return f"`{_project_id()}.{dataset}.{table}`"


def _linkedin_full_table() -> str:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    table = (os.getenv("BQ_MART_LINKEDIN_TABLE") or _DEFAULT_LINKEDIN_TABLE).strip()
    return f"`{_project_id()}.{dataset}.{table}`"


def fetch_campaign_daily(*, days: int = 30) -> list[dict[str, Any]]:
    """Return per-campaign per-day rows for the past `days` days (Google Ads mart)."""
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


def fetch_linkedin_campaign_daily(*, days: int = 30) -> list[dict[str, Any]]:
    """Return per-campaign per-day rows for the past `days` days (LinkedIn Ads mart)."""
    table = _linkedin_full_table()
    sql = f"""
    SELECT
      CAST(metric_date AS STRING) AS metric_date,
      CAST(campaign_id AS STRING) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(conversions AS FLOAT64)) AS conversions,
      SUM(CAST(conversion_value AS FLOAT64)) AS conversion_value,
      MAX(account_id) AS account_id
    FROM {table}
    WHERE metric_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)
    GROUP BY 1, 2
    ORDER BY 1 DESC, 4 DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=5000)


def _aggregate_campaign_rows(rows: list[dict[str, Any]]) -> tuple[
    dict[str, dict[str, Any]],  # by_date
    dict[str, dict[str, Any]],  # by_campaign
]:
    by_date: dict[str, dict[str, Any]] = {}
    by_campaign: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = str(row.get("metric_date") or "")[:10]
        spend = float(row.get("spend") or 0)
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        conversions = float(row.get("conversions") or 0)
        conversion_value = float(row.get("conversion_value") or 0)
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
                "conversion_value": 0.0,
            })
            camp["spend"] += spend
            camp["impressions"] += impressions
            camp["clicks"] += clicks
            camp["conversions"] += conversions
            camp["conversion_value"] += conversion_value
            if cname and cname != cid and not camp.get("campaign_name"):
                camp["campaign_name"] = cname

    return by_date, by_campaign


def _daily_rows(by_date: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
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


def _campaign_breakdowns(by_campaign: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
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


def _platform_totals(by_date: dict, by_campaign: dict) -> dict[str, Any]:
    return {
        "spend": sum(v["spend"] for v in by_date.values()),
        "clicks": sum(v["clicks"] for v in by_date.values()),
        "impressions": sum(v["impressions"] for v in by_date.values()),
        "conversions": sum(v["conversions"] for v in by_date.values()),
        "campaign_count": len(by_campaign),
    }


def build_snapshot(*, days: int = 30) -> dict[str, Any]:
    """Query Google and LinkedIn marts and return a snapshot dict compatible with render_penn_html()."""
    errors: dict[str, str] = {}

    # Google Ads mart
    google_rows: list[dict[str, Any]] = []
    try:
        google_rows = fetch_campaign_daily(days=days)
    except Exception as exc:
        errors["bq_mart_google"] = str(exc)[:600]

    by_date_g, by_campaign_g = _aggregate_campaign_rows(google_rows)
    totals_g = _platform_totals(by_date_g, by_campaign_g)

    # LinkedIn Ads mart
    linkedin_rows: list[dict[str, Any]] = []
    linkedin_account_id: str | None = None
    try:
        linkedin_rows = fetch_linkedin_campaign_daily(days=days)
        # Pull account_id from data so snapshot shows a real ID
        for r in linkedin_rows:
            if r.get("account_id"):
                linkedin_account_id = str(r["account_id"]).strip().split(":")[-1]
                break
    except Exception as exc:
        errors["bq_mart_linkedin"] = str(exc)[:600]

    by_date_li, by_campaign_li = _aggregate_campaign_rows(linkedin_rows)
    totals_li = _platform_totals(by_date_li, by_campaign_li)

    end = date.today()
    start = end - timedelta(days=days - 1)

    return {
        "client_key": "penn-bq-test",
        "label": "Penn BQ Test",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": "LAST_30_DAYS",
        },
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
        "accounts": {
            "google": _project_id(),
            "linkedin": linkedin_account_id,
            "meta": None,
        },
        "daily_metrics": {
            "google": _daily_rows(by_date_g),
            "linkedin": _daily_rows(by_date_li),
        },
        "platform_totals": {
            "google": totals_g,
            "linkedin": totals_li,
        },
        "breakdowns": {
            "google": {
                "campaign": _campaign_breakdowns(by_campaign_g),
                "ad_group": [],
                "ad": [],
            },
            "linkedin": {
                "campaign": _campaign_breakdowns(by_campaign_li),
                "ad_group": [],
                "ad": [],
            },
        },
        "aggregated_paid_media": {
            "spend": totals_g["spend"] + totals_li["spend"],
            "clicks": totals_g["clicks"] + totals_li["clicks"],
            "impressions": totals_g["impressions"] + totals_li["impressions"],
            "conversions": totals_g["conversions"] + totals_li["conversions"],
        },
        "business_line_campaigns": [],
        "warehouse_sync": {},
        "ga4_attribution": None,
        "ga4_pages": None,
        "errors": errors,
        "refresh_mode": "bq_mart",
    }


def env_summary() -> dict[str, Any]:
    """Return BQ mart config for display in dashboard settings."""
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    return {
        "project_id": _project_id(),
        "dataset_id": dataset,
        "google_table": (os.getenv("BQ_MART_TABLE") or _DEFAULT_TABLE).strip(),
        "linkedin_table": (os.getenv("BQ_MART_LINKEDIN_TABLE") or _DEFAULT_LINKEDIN_TABLE).strip(),
    }
