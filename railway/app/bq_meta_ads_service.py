"""Sync Meta Ads API → BigQuery and build dashboard snapshot from mart views."""

from __future__ import annotations

from datetime import date
from typing import Any

import bigquery_service
import bigquery_warehouse
import meta_service


_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_CAMPAIGN_FACT = "fact_meta_ads_campaign_daily"
_DEFAULT_ADSET_FACT = "fact_meta_ads_adset_daily"
_DEFAULT_AD_FACT = "fact_meta_ads_ad_daily"


def _project_id() -> str:
    import os
    return (os.getenv("BQ_META_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _mart_table(name: str) -> str:
    import os
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DATASET).strip()
    return f"`{_project_id()}.{dataset}.{name}`"


def _safe_ratio(numerator: float, denominator: float, multiplier: float = 1.0) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * multiplier, 4)


def sync_meta_to_bq(
    account_id: str,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Fetch Meta campaign/adset/ad daily metrics and upsert into BigQuery."""
    from concurrent.futures import ThreadPoolExecutor

    account_id_clean = meta_service._normalize_account_id(account_id)

    campaign_rows: list[dict[str, Any]] = []
    adset_rows: list[dict[str, Any]] = []
    ad_rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        _cf = pool.submit(meta_service.fetch_campaign_daily_metrics, account_id_clean, start=start, end=end)
        _af = pool.submit(meta_service.fetch_adset_daily_metrics, account_id_clean, start=start, end=end)
        _adf = pool.submit(meta_service.fetch_ad_daily_metrics, account_id_clean, start=start, end=end)
        try:
            campaign_rows = _cf.result()
        except Exception as exc:
            errors["meta_campaign_fetch"] = str(exc)[:400]
        try:
            adset_rows = _af.result()
        except Exception as exc:
            errors["meta_adset_fetch"] = str(exc)[:400]
        try:
            ad_rows = _adf.result()
        except Exception as exc:
            errors["meta_ad_fetch"] = str(exc)[:400]

    campaign_mirror = bigquery_warehouse.mirror_meta_campaign_daily_batch(account_id_clean, campaign_rows)
    adset_mirror = bigquery_warehouse.mirror_meta_adset_daily_batch(account_id_clean, adset_rows)
    ad_mirror = bigquery_warehouse.mirror_meta_ad_daily_batch(account_id_clean, ad_rows)

    views = bigquery_warehouse.create_meta_mart_views()

    return {
        "account_id": account_id_clean,
        "campaign_rows": campaign_mirror.get("rows_upserted", 0),
        "adset_rows": adset_mirror.get("rows_upserted", 0),
        "ad_rows": ad_mirror.get("rows_upserted", 0),
        "views": views.get("views", []),
        "errors": errors,
    }


def fetch_meta_campaign_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    table = _mart_table(_DEFAULT_CAMPAIGN_FACT)
    if start and end:
        where = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=5000)


def fetch_meta_adset_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    table = _mart_table(_DEFAULT_ADSET_FACT)
    if start and end:
        where = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      adset_id,
      MAX(adset_name) AS adset_name,
      MAX(campaign_id) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=10000)


def fetch_meta_ad_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    table = _mart_table(_DEFAULT_AD_FACT)
    if start and end:
        where = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      ad_id,
      MAX(ad_name) AS ad_name,
      MAX(adset_id) AS adset_id,
      MAX(adset_name) AS adset_name,
      MAX(campaign_id) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), max_rows=20000)


def _build_campaign_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_c: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("campaign_id") or "").strip()
        if not cid:
            continue
        if cid not in by_c:
            by_c[cid] = {
                "id": cid,
                "name": str(row.get("campaign_name") or cid),
                "entity_level": "campaign",
                "parent_id": None,
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        c = by_c[cid]
        c["spend"] += float(row.get("spend") or 0)
        c["clicks"] += int(row.get("clicks") or 0)
        c["impressions"] += int(row.get("impressions") or 0)
        c["conversions"] += float(row.get("conversions") or 0)
        c["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_c.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for c in out:
        c["ctr"] = _safe_ratio(float(c["clicks"]), float(c["impressions"]))
        c["cpc"] = _safe_ratio(float(c["spend"]), float(c["clicks"]))
        c["cpm"] = _safe_ratio(float(c["spend"]), float(c["impressions"]), 1000)
        c["cost_per_conversion"] = _safe_ratio(float(c["spend"]), float(c["conversions"]))
    return out


def _build_adset_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_as: dict[str, dict[str, Any]] = {}
    for row in rows:
        asid = str(row.get("adset_id") or "").strip()
        if not asid:
            continue
        if asid not in by_as:
            by_as[asid] = {
                "id": asid,
                "name": str(row.get("adset_name") or asid),
                "entity_level": "adset",
                "parent_id": str(row.get("campaign_id") or "").strip(),
                "parent_name": str(row.get("campaign_name") or "").strip(),
                "campaign_id": str(row.get("campaign_id") or "").strip(),
                "campaign_name": str(row.get("campaign_name") or "").strip(),
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        a = by_as[asid]
        a["spend"] += float(row.get("spend") or 0)
        a["clicks"] += int(row.get("clicks") or 0)
        a["impressions"] += int(row.get("impressions") or 0)
        a["conversions"] += float(row.get("conversions") or 0)
        a["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_as.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for a in out:
        a["ctr"] = _safe_ratio(float(a["clicks"]), float(a["impressions"]))
        a["cpc"] = _safe_ratio(float(a["spend"]), float(a["clicks"]))
        a["cpm"] = _safe_ratio(float(a["spend"]), float(a["impressions"]), 1000)
        a["cost_per_conversion"] = _safe_ratio(float(a["spend"]), float(a["conversions"]))
    return out


def _build_ad_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ad: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = str(row.get("ad_id") or "").strip()
        if not aid:
            continue
        if aid not in by_ad:
            by_ad[aid] = {
                "id": aid,
                "name": str(row.get("ad_name") or f"Ad {aid}"),
                "entity_level": "ad",
                "parent_id": str(row.get("adset_id") or "").strip(),
                "parent_name": str(row.get("adset_name") or "").strip(),
                "adset_id": str(row.get("adset_id") or "").strip(),
                "adset_name": str(row.get("adset_name") or "").strip(),
                "campaign_id": str(row.get("campaign_id") or "").strip(),
                "campaign_name": str(row.get("campaign_name") or "").strip(),
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        a = by_ad[aid]
        a["spend"] += float(row.get("spend") or 0)
        a["clicks"] += int(row.get("clicks") or 0)
        a["impressions"] += int(row.get("impressions") or 0)
        a["conversions"] += float(row.get("conversions") or 0)
        a["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_ad.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for a in out:
        a["ctr"] = _safe_ratio(float(a["clicks"]), float(a["impressions"]))
        a["cpc"] = _safe_ratio(float(a["spend"]), float(a["clicks"]))
        a["cpm"] = _safe_ratio(float(a["spend"]), float(a["impressions"]), 1000)
        a["cost_per_conversion"] = _safe_ratio(float(a["spend"]), float(a["conversions"]))
    return out


def build_meta_breakdowns(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Read Meta mart views and return breakdowns + platform_totals."""
    from concurrent.futures import ThreadPoolExecutor
    errors: dict[str, str] = {}
    campaign_rows: list[dict[str, Any]] = []
    adset_rows: list[dict[str, Any]] = []
    ad_rows: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        _cf = pool.submit(fetch_meta_campaign_daily, days=days, start=start, end=end)
        _af = pool.submit(fetch_meta_adset_daily, days=days, start=start, end=end)
        _adf = pool.submit(fetch_meta_ad_daily, days=days, start=start, end=end)
        try:
            campaign_rows = _cf.result()
        except Exception as exc:
            errors["meta_campaign_read"] = str(exc)[:400]
        try:
            adset_rows = _af.result()
        except Exception as exc:
            errors["meta_adset_read"] = str(exc)[:400]
        try:
            ad_rows = _adf.result()
        except Exception as exc:
            errors["meta_ad_read"] = str(exc)[:400]

    campaigns = _build_campaign_breakdowns(campaign_rows)
    adsets = _build_adset_breakdowns(adset_rows)
    ads = _build_ad_breakdowns(ad_rows)

    totals = {
        "spend": sum(float(c.get("spend") or 0) for c in campaigns),
        "clicks": sum(int(c.get("clicks") or 0) for c in campaigns),
        "impressions": sum(int(c.get("impressions") or 0) for c in campaigns),
        "conversions": sum(float(c.get("conversions") or 0) for c in campaigns),
        "campaign_count": len(campaigns),
    }

    return {
        "breakdowns": {"campaign": campaigns, "adset": adsets, "ad": ads},
        "platform_totals": totals,
        "errors": errors,
    }
