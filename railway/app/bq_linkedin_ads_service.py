"""BigQuery LinkedIn Ads reader for dashboard snapshots.

This module is intentionally source-specific, not route-global: callers decide
when to use it.  It reads Penn's raw LinkedIn Ads BigQuery tables and returns a
snapshot shaped for the existing Penn dashboard renderer.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from typing import Any

import bigquery_service
from dashboard.services.snapshot_metrics_service import aggregated_paid_media
from penn_business_lines import build_client_segment_campaigns, client_filter_profile
from penn_config import PennDashboardConfig

LOGGER = logging.getLogger(__name__)

_CREATIVE_METADATA_FIELDS = (
    "thumbnail_url",
    "image_url",
    "video_url",
    "media_type",
    "creative_name",
    "creative_id",
    "ad_id",
    "preview_url",
    "asset_url",
)

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_DATASET = "linkedin_ads"
_METRICS_TABLE = "metrics_daily"
_CAMPAIGN_TABLE = "campaign_daily"


def _project_id() -> str:
    return (os.getenv("PENN_BQ_LINKEDIN_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _dataset_id() -> str:
    return (os.getenv("PENN_BQ_LINKEDIN_DATASET_ID") or _DEFAULT_DATASET).strip()


def _table(table_name: str) -> str:
    return f"`{_project_id()}.{_dataset_id()}.{table_name}`"


def _safe_ratio(numerator: float, denominator: float, multiplier: float = 1.0) -> float:
    return (numerator / denominator * multiplier) if denominator else 0.0


def account_summary_sql(*, start: date, end: date, account_id: str | None = None) -> str:
    account_filter = ""
    if account_id:
        safe_account = str(account_id).replace("'", "\\'")
        account_filter = f"AND CAST(account_id AS STRING) = '{safe_account}'"
    return f"""
    SELECT
      CAST(account_id AS STRING) AS account_id,
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(conversions AS FLOAT64)) AS conversions,
      SUM(CAST(conversion_value AS FLOAT64)) AS conversion_value,
      SUM(CAST(COALESCE(reach, 0) AS INT64)) AS reach,
      SAFE_DIVIDE(SUM(CAST(clicks AS FLOAT64)), SUM(CAST(impressions AS FLOAT64))) AS ctr,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(clicks AS FLOAT64))) AS cpc,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(impressions AS FLOAT64))) * 1000 AS cpm,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(conversions AS FLOAT64))) AS cost_per_conversion
    FROM {_table(_METRICS_TABLE)}
    WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
      {account_filter}
    GROUP BY account_id
    ORDER BY spend DESC
    """


def daily_metrics_sql(*, start: date, end: date, account_id: str | None = None) -> str:
    account_filter = ""
    if account_id:
        safe_account = str(account_id).replace("'", "\\'")
        account_filter = f"AND CAST(account_id AS STRING) = '{safe_account}'"
    return f"""
    SELECT
      CAST(metric_date AS STRING) AS metric_date,
      CAST(account_id AS STRING) AS account_id,
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(conversions AS FLOAT64)) AS conversions,
      SUM(CAST(conversion_value AS FLOAT64)) AS conversion_value,
      SUM(CAST(COALESCE(reach, 0) AS INT64)) AS reach,
      SAFE_DIVIDE(SUM(CAST(clicks AS FLOAT64)), SUM(CAST(impressions AS FLOAT64))) AS ctr,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(clicks AS FLOAT64))) AS cpc,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(impressions AS FLOAT64))) * 1000 AS cpm,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(conversions AS FLOAT64))) AS cost_per_conversion
    FROM {_table(_METRICS_TABLE)}
    WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
      {account_filter}
    GROUP BY metric_date, account_id
    ORDER BY metric_date ASC, account_id ASC
    """


def campaign_daily_sql(*, start: date, end: date, account_id: str | None = None, include_reach: bool = True) -> str:
    account_filter = ""
    if account_id:
        safe_account = str(account_id).replace("'", "\\'")
        account_filter = f"AND CAST(account_id AS STRING) = '{safe_account}'"
    reach_expr = "SUM(CAST(COALESCE(reach, 0) AS INT64))" if include_reach else "0"
    return f"""
    SELECT
      CAST(metric_date AS STRING) AS metric_date,
      CAST(account_id AS STRING) AS account_id,
      CAST(campaign_id AS STRING) AS campaign_id,
      COALESCE(NULLIF(TRIM(CAST(campaign_name AS STRING)), ''), CAST(campaign_id AS STRING)) AS campaign_name,
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(conversions AS FLOAT64)) AS conversions,
      SUM(CAST(conversion_value AS FLOAT64)) AS conversion_value,
      {reach_expr} AS reach,
      SAFE_DIVIDE(SUM(CAST(clicks AS FLOAT64)), SUM(CAST(impressions AS FLOAT64))) AS ctr,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(clicks AS FLOAT64))) AS cpc,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(impressions AS FLOAT64))) * 1000 AS cpm,
      SAFE_DIVIDE(SUM(CAST(spend AS FLOAT64)), SUM(CAST(conversions AS FLOAT64))) AS cost_per_conversion
    FROM {_table(_CAMPAIGN_TABLE)}
    WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
      {account_filter}
    GROUP BY metric_date, account_id, campaign_id, campaign_name
    ORDER BY metric_date DESC, spend DESC
    """


def _table_has_column(*, table_name: str, column_name: str, project_id: str) -> bool:
    dataset = _dataset_id().replace("`", "")
    table = table_name.replace("`", "")
    column = column_name.replace("'", "\'")
    sql = f"""
    SELECT 1 AS found
    FROM `{_project_id()}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = '{table}'
      AND column_name = '{column}'
    LIMIT 1
    """
    try:
        return bool(bigquery_service.run_query(sql, project_id=project_id, max_rows=1))
    except Exception:
        return True


def fetch_linkedin_ads(*, start: date, end: date, account_id: str | None = None) -> dict[str, Any]:
    LOGGER.info("LinkedIn source: BigQuery.")
    project = _project_id()
    campaign_has_reach = _table_has_column(
        table_name=_CAMPAIGN_TABLE,
        column_name="reach",
        project_id=project,
    )
    return {
        "account_summary": bigquery_service.run_query(
            account_summary_sql(start=start, end=end, account_id=account_id),
            project_id=project,
            max_rows=1000,
        ),
        "daily_metrics": bigquery_service.run_query(
            daily_metrics_sql(start=start, end=end, account_id=account_id),
            project_id=project,
            max_rows=5000,
        ),
        "campaign_daily": bigquery_service.run_query(
            campaign_daily_sql(
                start=start,
                end=end,
                account_id=account_id,
                include_reach=campaign_has_reach,
            ),
            project_id=project,
            max_rows=10000,
        ),
    }


def _creative_lookup_key(row: dict[str, Any]) -> str:
    """Return a safe creative/ad key without treating campaign IDs as creatives."""
    for key in ("creative_id", "ad_id"):
        val = str(row.get(key) or "").strip()
        if val:
            return val
    if str(row.get("entity_level") or "").strip().lower() in {"creative", "ad"}:
        return str(row.get("id") or "").strip()
    return ""


def _load_postgres_creative_metadata(*, client_key: str = "penn") -> dict[str, dict[str, Any]]:
    """Load existing LinkedIn creative media metadata from the Postgres snapshot store.

    BigQuery remains the source for Penn BQ test performance metrics; this helper
    only reads already-stored dashboard creative metadata so thumbnail/asset links
    continue to use the existing Postgres-backed path.
    """
    try:
        import dashboard_snapshots

        snapshot = dashboard_snapshots.get_snapshot(client_key) or {}
    except Exception:
        return {}

    linkedin = (snapshot.get("breakdowns") or {}).get("linkedin") or {}
    rows = list(linkedin.get("creative") or []) + list(linkedin.get("ad") or [])
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _creative_lookup_key(row)
        if not key:
            continue
        metadata = {
            field: row.get(field)
            for field in _CREATIVE_METADATA_FIELDS
            if str(row.get(field) or "").strip()
        }
        if metadata:
            lookup[key] = metadata
    return lookup


def merge_postgres_creative_metadata(
    rows: list[dict[str, Any]],
    *,
    client_key: str = "penn",
) -> int:
    """Attach Postgres-sourced creative metadata to creative/ad rows in place.

    Campaign-level BigQuery rows intentionally receive no thumbnail because they
    do not identify a specific creative asset.
    """
    if not rows:
        return 0
    metadata_by_id = _load_postgres_creative_metadata(client_key=client_key)
    if not metadata_by_id:
        return 0
    merged = 0
    for row in rows:
        key = _creative_lookup_key(row)
        if not key:
            continue
        metadata = metadata_by_id.get(key)
        if not metadata:
            continue
        for field, value in metadata.items():
            if value and not str(row.get(field) or "").strip():
                row[field] = value
        merged += 1
    return merged


def _campaign_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("campaign_id") or "")
        if not cid:
            continue
        name = str(row.get("campaign_name") or cid)
        item = by_id.setdefault(cid, {"id": cid, "name": name, "entity_level": "campaign_group", "campaign_id": cid, "campaign_name": name, "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "reach": 0})
        item["spend"] += float(row.get("spend") or 0)
        item["clicks"] += int(row.get("clicks") or 0)
        item["impressions"] += int(row.get("impressions") or 0)
        item["conversions"] += float(row.get("conversions") or 0)
        item["reach"] += int(row.get("reach") or 0)
    for item in by_id.values():
        item["ctr"] = _safe_ratio(float(item["clicks"]), float(item["impressions"]))
        item["cpc"] = _safe_ratio(float(item["spend"]), float(item["clicks"]))
        item["cpm"] = _safe_ratio(float(item["spend"]), float(item["impressions"]), 1000)
        item["cost_per_conversion"] = _safe_ratio(float(item["spend"]), float(item["conversions"]))
    return sorted(by_id.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)


def build_snapshot(*, cfg: PennDashboardConfig, start: date, end: date, preset: str) -> dict[str, Any]:
    data = fetch_linkedin_ads(start=start, end=end, account_id=cfg.linkedin_account_id)
    daily = data["daily_metrics"]
    campaigns = _campaign_totals(data["campaign_daily"])
    summary = data["account_summary"][0] if data["account_summary"] else {}
    totals = {
        "spend": float(summary.get("spend") or 0),
        "clicks": int(summary.get("clicks") or 0),
        "impressions": int(summary.get("impressions") or 0),
        "conversions": float(summary.get("conversions") or 0),
        "conversion_value": float(summary.get("conversion_value") or 0),
        "reach": int(summary.get("reach") or 0),
        "ctr": float(summary.get("ctr") or 0),
        "cpc": float(summary.get("cpc") or 0),
        "cpm": float(summary.get("cpm") or 0),
        "cost_per_conversion": float(summary.get("cost_per_conversion") or 0),
        "campaign_count": len(campaigns),
    }
    creative_rows: list[dict[str, Any]] = []
    creative_metadata_rows = merge_postgres_creative_metadata(
        creative_rows,
        client_key="penn",
    )
    platform_totals = {"linkedin": totals}
    breakdowns = {
        "linkedin": {
            "campaign_group": campaigns,
            "campaign": campaigns,
            "creative": creative_rows,
        }
    }
    return {
        "client_key": cfg.client_key,
        "label": cfg.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
        "accounts": {"google": cfg.google_customer_id, "linkedin": cfg.linkedin_account_id or _project_id(), "meta": cfg.meta_account_id},
        "data_sources": {
            "linkedin": "bigquery",
            "linkedin_creative_metadata": "postgres",
        },
        "daily_metrics": {"linkedin": daily},
        "platform_totals": platform_totals,
        "breakdowns": breakdowns,
        "aggregated_paid_media": aggregated_paid_media(platform_totals),
        "business_line_campaigns": build_client_segment_campaigns(breakdowns, client_slug=cfg.client_key, filter_profile=client_filter_profile(cfg.client_key, cfg=cfg)),
        "warehouse_sync": {},
        "ga4_attribution": None,
        "ga4_pages": None,
        "errors": {},
        "creative_metadata": {
            "source": "postgres",
            "merged_rows": creative_metadata_rows,
        },
        "refresh_mode": "bigquery_linkedin",
    }
