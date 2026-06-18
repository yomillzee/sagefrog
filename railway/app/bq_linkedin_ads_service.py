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
_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_CAMPAIGN_FACT_TABLE = "fact_linkedin_ads_campaign_daily"


def _project_id() -> str:
    return (os.getenv("PENN_BQ_LINKEDIN_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _dataset_id() -> str:
    return (os.getenv("PENN_BQ_LINKEDIN_DATASET_ID") or _DEFAULT_DATASET).strip()


def _table(table_name: str) -> str:
    return f"`{_project_id()}.{_dataset_id()}.{table_name}`"


def _mart_table(table_name: str | None = None) -> str:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DATASET).strip()
    table = (
        table_name
        or os.getenv("BQ_LINKEDIN_CAMPAIGN_FACT_TABLE")
        or _DEFAULT_CAMPAIGN_FACT_TABLE
    ).strip()
    return f"`{_project_id()}.{dataset}.{table}`"


_DEFAULT_CREATIVE_FACT_TABLE = "fact_linkedin_ads_creative_daily"


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


def campaign_daily_sql(*, start: date, end: date, account_id: str | None = None) -> str:
    account_filter = ""
    if account_id:
        safe_account = str(account_id).replace("'", "\\'")
        account_filter = f"AND CAST(account_id AS STRING) = '{safe_account}'"
    return f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(clicks) AS clicks,
      SUM(impressions) AS impressions,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value,
      SAFE_DIVIDE(SUM(clicks), SUM(impressions)) AS ctr,
      SAFE_DIVIDE(SUM(spend), SUM(clicks)) AS cpc,
      SAFE_DIVIDE(SUM(spend), SUM(impressions)) * 1000 AS cpm,
      SAFE_DIVIDE(SUM(spend), SUM(conversions)) AS cost_per_conversion
    FROM {_mart_table()}
    WHERE date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """


def _creative_daily_sql(*, start: date, end: date, account_id: str | None = None) -> str:
    account_filter = ""
    if account_id:
        safe_account = str(account_id).replace("'", "\\'")
        account_filter = f"AND CAST(source_account_id AS STRING) = '{safe_account}'"
    creative_table = _mart_table(_DEFAULT_CREATIVE_FACT_TABLE)
    return f"""
    SELECT
      creative_id,
      MAX(campaign_id) AS campaign_id,
      MAX(creative_name) AS creative_name,
      MAX(campaign_name) AS campaign_name,
      MAX(campaign_group_id) AS campaign_group_id,
      MAX(campaign_group_name) AS campaign_group_name,
      MAX(status) AS status,
      MAX(media_type) AS media_type,
      MAX(thumbnail_url) AS thumbnail_url,
      MAX(image_url) AS image_url,
      MAX(video_url) AS video_url,
      SUM(spend) AS spend,
      SUM(clicks) AS clicks,
      SUM(impressions) AS impressions,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {creative_table}
    WHERE date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
    {account_filter}
    GROUP BY 1
    ORDER BY spend DESC
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


def _campaign_ids_sql(*, start: date, end: date, account_id: str) -> str:
    safe_account = str(account_id).replace("'", "\'")
    return f"""
    SELECT DISTINCT CAST(campaign_id AS STRING) AS campaign_id
    FROM {_table(_CAMPAIGN_TABLE)}
    WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
      AND CAST(account_id AS STRING) = '{safe_account}'
      AND campaign_id IS NOT NULL
    """


def _campaign_group_map_sql(*, start: date, end: date, account_id: str) -> str:
    """Return campaign→group mapping from the campaigns metadata table."""
    safe_account = str(account_id).replace("'", "\\'")
    # campaign_daily has no campaign_group_id column; group info lives in campaigns metadata.
    return f"""
    SELECT
      CAST(campaign_id AS STRING) AS campaign_id,
      CAST(campaign_group_id AS STRING) AS campaign_group_id,
      CAST(campaign_group_name AS STRING) AS campaign_group_name
    FROM {_table('campaigns')}
    WHERE CAST(account_id AS STRING) = '{safe_account}'
      AND campaign_id IS NOT NULL
      AND campaign_group_id IS NOT NULL
      AND CAST(campaign_group_id AS STRING) NOT IN ('', 'None', 'null')
    """


def _fetch_campaign_group_map(*, start: date, end: date, account_id: str) -> dict[str, dict[str, str]]:
    """Return {campaign_id: {id, name}} from the raw BQ campaign_daily table."""
    try:
        rows = bigquery_service.run_query(
            _campaign_group_map_sql(start=start, end=end, account_id=account_id),
            project_id=_project_id(),
            max_rows=5000,
        )
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        cid = str(row.get("campaign_id") or "").strip()
        gid = str(row.get("campaign_group_id") or "").strip()
        gname = str(row.get("campaign_group_name") or "").strip()
        if cid and gid:
            out[cid] = {"id": gid, "name": gname or f"Group {gid}"}
    return out


def _campaign_metadata_from_postgres(
    *,
    account_id: str,
    start: date,
    end: date,
) -> dict[str, dict[str, Any]]:
    try:
        import warehouse

        rows = warehouse.query_campaign_daily("linkedin", account_id, start, end)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("campaign_id") or "").strip()
        name = str(row.get("campaign_name") or "").strip()
        if cid and name:
            out[cid] = {
                "source": "linkedin",
                "account_id": str(account_id).strip().split(":")[-1],
                "campaign_id": cid,
                "campaign_name": name,
                "campaign_status": "",
                "campaign_group_id": "",
                "campaign_group_name": "",
            }
    return out


def sync_campaign_metadata_and_rebuild_mart(
    *,
    account_id: str | None,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Populate linkedin_ads.campaigns and rebuild the LinkedIn campaign mart.

    Uses existing Postgres campaign names first, then fetches missing metadata
    from LinkedIn. This is only called by the Penn BQ test route.
    """
    if not account_id:
        return {"enabled": False, "rows_upserted": 0, "reason": "missing_account_id"}
    account_id_clean = str(account_id).strip().split(":")[-1]
    project = _project_id()
    id_rows = bigquery_service.run_query(
        _campaign_ids_sql(start=start, end=end, account_id=account_id_clean),
        project_id=project,
        max_rows=10000,
    )
    campaign_ids = sorted({str(row.get("campaign_id") or "").strip() for row in id_rows if row.get("campaign_id")})
    metadata_by_id = _campaign_metadata_from_postgres(
        account_id=account_id_clean,
        start=start,
        end=end,
    )
    missing = [cid for cid in campaign_ids if cid not in metadata_by_id]
    if missing:
        import linkedin_service

        for row in linkedin_service.fetch_campaign_metadata_rows(account_id_clean, missing):
            cid = str(row.get("campaign_id") or "").strip()
            if cid:
                metadata_by_id[cid] = row

    import bigquery_warehouse

    mirror = bigquery_warehouse.mirror_linkedin_campaign_metadata(list(metadata_by_id.values()))
    mart = bigquery_warehouse.rebuild_linkedin_campaign_daily_mart()

    creative_sync: dict[str, Any] = {}
    try:
        creative_sync = sync_linkedin_creative_daily(
            account_id=account_id_clean, start=start, end=end
        )
    except Exception as exc:
        creative_sync = {"error": str(exc)[:300]}

    return {
        "enabled": True,
        "campaign_ids": len(campaign_ids),
        "metadata_rows": len(metadata_by_id),
        "missing_after_sync": len([cid for cid in campaign_ids if cid not in metadata_by_id]),
        "bigquery": mirror,
        "mart": mart,
        "creative_sync": creative_sync,
    }


def sync_linkedin_creative_daily(
    *,
    account_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Sync LinkedIn creative daily metrics + metadata to BigQuery.

    Writes to linkedin_ads.ad_daily and linkedin_ads.creative_metadata,
    then creates/replaces the fact_linkedin_ads_creative_daily mart view.
    """
    import bigquery_warehouse
    import linkedin_service

    account_id_clean = str(account_id).strip().split(":")[-1]

    daily_rows = linkedin_service.fetch_creative_daily_metrics(
        account_id_clean, start=start, end=end
    )
    ad_mirror = bigquery_warehouse.mirror_linkedin_ad_daily_batch(account_id_clean, daily_rows)

    creatives_perf = linkedin_service.creatives_performance(
        account_id_clean, date_range="LAST_30_DAYS"
    )
    raw_creatives = creatives_perf.get("creatives") or []
    for row in raw_creatives:
        row.setdefault("account_id", account_id_clean)
    meta_mirror = bigquery_warehouse.mirror_linkedin_creative_metadata(raw_creatives)

    view = bigquery_warehouse.create_linkedin_creative_mart_view()

    return {
        "daily_rows": len(daily_rows),
        "metadata_rows": len(raw_creatives),
        "ad_daily": ad_mirror,
        "creative_metadata": meta_mirror,
        "mart_view": view,
    }


def fetch_linkedin_ads(*, start: date, end: date, account_id: str | None = None) -> dict[str, Any]:
    LOGGER.info("LinkedIn source: BigQuery.")
    project = _project_id()
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


def _build_linkedin_breakdowns(
    campaign_daily_rows: list[dict[str, Any]],
    group_map: dict[str, dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    """Build campaign_group (tier 1 = UI Campaign) and campaign (tier 2 = UI Ad Set) rows."""
    by_campaign: dict[str, dict[str, Any]] = {}
    for row in campaign_daily_rows:
        cid = str(row.get("campaign_id") or "").strip()
        if not cid:
            continue
        name = str(row.get("campaign_name") or cid)
        group_info = group_map.get(cid) or {}
        gid = str(group_info.get("id") or "").strip()
        gname = str(group_info.get("name") or "").strip()
        if cid not in by_campaign:
            by_campaign[cid] = {
                "id": cid,
                "name": name,
                "entity_level": "campaign",
                "campaign_id": cid,
                "campaign_name": name,
                "parent_id": gid,
                "parent_name": gname,
                "campaign_group_id": gid,
                "campaign_group_name": gname,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "reach": 0,
            }
        item = by_campaign[cid]
        item["spend"] += float(row.get("spend") or 0)
        item["clicks"] += int(row.get("clicks") or 0)
        item["impressions"] += int(row.get("impressions") or 0)
        item["conversions"] += float(row.get("conversions") or 0)
        item["reach"] += int(row.get("reach") or 0)

    campaign_list: list[dict[str, Any]] = sorted(
        by_campaign.values(), key=lambda r: float(r.get("spend") or 0), reverse=True
    )
    for item in campaign_list:
        item["ctr"] = _safe_ratio(float(item["clicks"]), float(item["impressions"]))
        item["cpc"] = _safe_ratio(float(item["spend"]), float(item["clicks"]))
        item["cpm"] = _safe_ratio(float(item["spend"]), float(item["impressions"]), 1000)
        item["cost_per_conversion"] = _safe_ratio(float(item["spend"]), float(item["conversions"]))

    # Aggregate campaign_group rows (tier 1 = UI "Campaign") from campaign tier
    by_group: dict[str, dict[str, Any]] = {}
    for camp in campaign_list:
        gid = str(camp.get("campaign_group_id") or "").strip()
        if not gid:
            continue
        if gid not in by_group:
            by_group[gid] = {
                "id": gid,
                "name": str(camp.get("campaign_group_name") or f"Group {gid}"),
                "entity_level": "campaign_group",
                "campaign_group_id": gid,
                "campaign_group_name": str(camp.get("campaign_group_name") or f"Group {gid}"),
                "parent_id": "",
                "parent_name": "",
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "reach": 0,
            }
        g = by_group[gid]
        g["spend"] += camp["spend"]
        g["clicks"] += camp["clicks"]
        g["impressions"] += camp["impressions"]
        g["conversions"] += camp["conversions"]
        g["reach"] += camp.get("reach", 0)

    group_list: list[dict[str, Any]] = sorted(
        by_group.values(), key=lambda r: float(r.get("spend") or 0), reverse=True
    )
    for g in group_list:
        g["ctr"] = _safe_ratio(float(g["clicks"]), float(g["impressions"]))
        g["cpc"] = _safe_ratio(float(g["spend"]), float(g["clicks"]))
        g["cpm"] = _safe_ratio(float(g["spend"]), float(g["impressions"]), 1000)
        g["cost_per_conversion"] = _safe_ratio(float(g["spend"]), float(g["conversions"]))

    return {
        "campaign_group": group_list,
        "campaign": campaign_list,
    }


def _normalize_creative_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape BQ creative mart rows into the dashboard breakdowns format."""
    out = []
    for row in rows:
        crid = str(row.get("creative_id") or "").strip()
        if not crid:
            continue
        cname = str(row.get("creative_name") or crid)
        cid = str(row.get("campaign_id") or "").strip()
        spend = float(row.get("spend") or 0)
        clicks = int(row.get("clicks") or 0)
        impressions = int(row.get("impressions") or 0)
        convs = float(row.get("conversions") or 0)
        item: dict[str, Any] = {
            "id": crid,
            "creative_id": crid,
            "name": cname,
            "creative_name": cname,
            "entity_level": "creative",
            "parent_id": cid,
            "parent_name": str(row.get("campaign_name") or "").strip(),
            "campaign_id": cid,
            "campaign_name": str(row.get("campaign_name") or "").strip(),
            "campaign_group_id": str(row.get("campaign_group_id") or "").strip(),
            "campaign_group_name": str(row.get("campaign_group_name") or "").strip(),
            "status": str(row.get("status") or "").strip(),
            "spend": spend,
            "clicks": clicks,
            "impressions": impressions,
            "conversions": convs,
            "conversion_value": float(row.get("conversion_value") or 0),
            "ctr": _safe_ratio(float(clicks), float(impressions)),
            "cpc": _safe_ratio(spend, float(clicks)),
            "cpm": _safe_ratio(spend, float(impressions), 1000),
            "cost_per_conversion": _safe_ratio(spend, convs),
        }
        for field in ("media_type", "thumbnail_url", "image_url", "video_url"):
            val = str(row.get(field) or "").strip()
            if val:
                item[field] = val
        out.append(item)
    return out


def build_snapshot(
    *,
    cfg: PennDashboardConfig | None = None,
    account_id: str | None = None,
    start: date,
    end: date,
    preset: str,
) -> dict[str, Any]:
    # account_id param takes precedence over cfg.linkedin_account_id for multi-client support
    _raw_id = account_id or (str(cfg.linkedin_account_id or "") if cfg else "")
    account_id_clean = str(_raw_id or "").strip().split(":")[-1]
    data = fetch_linkedin_ads(start=start, end=end, account_id=account_id_clean or None)
    daily = data["daily_metrics"]

    # Fetch campaign→group mapping from raw BQ campaign_daily (has campaign_group_id)
    group_map: dict[str, dict[str, str]] = {}
    if account_id_clean:
        group_map = _fetch_campaign_group_map(start=start, end=end, account_id=account_id_clean)

    li_breakdowns = _build_linkedin_breakdowns(data["campaign_daily"], group_map)

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
        "campaign_count": len(li_breakdowns["campaign"]),
    }
    # Read creatives from BQ mart; fall back to Postgres if mart isn't populated yet.
    creative_rows: list[dict[str, Any]] = []
    creative_source = "bigquery"
    try:
        bq_creative_rows = bigquery_service.run_query(
            _creative_daily_sql(start=start, end=end, account_id=account_id_clean or None),
            project_id=_project_id(),
            max_rows=5000,
        )
        creative_rows = _normalize_creative_rows(bq_creative_rows)
    except Exception:
        creative_source = "postgres"
        merge_postgres_creative_metadata(creative_rows, client_key="penn")
    li_breakdowns["creative"] = creative_rows

    platform_totals = {"linkedin": totals}
    breakdowns = {"linkedin": li_breakdowns}
    return {
        "client_key": cfg.client_key,
        "label": cfg.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
        "accounts": {"google": cfg.google_customer_id, "linkedin": cfg.linkedin_account_id or _project_id(), "meta": cfg.meta_account_id},
        "data_sources": {
            "linkedin": "bigquery",
            "linkedin_creative_metadata": creative_source,
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
            "source": creative_source,
            "merged_rows": len(creative_rows),
        },
        "refresh_mode": "bigquery_linkedin",
    }
