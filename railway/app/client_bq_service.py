"""Generic BigQuery marketing mart service.

Mirrors nixon_marketing_service but fully parameterized — callers supply
project_id, dataset_id, and optionally a credentials env-var name so any
BQ-test client (arg-bq-test, etc.) can reuse the same SQL without forking
the Nixon service.

All functions return dicts in the same shape the dashboard JS expects.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

import bigquery_service

_DEFAULT_DATASET = "marketing_marts"

# Mart table / view names (must match the BQ mart schema).
_PAID_MEDIA_VIEW = "vw_paid_media_daily"
_HEALTH_TABLE = "mart_health"
_GOOGLE_ADS_EXPLORER_TABLE = "explorer_google_ads_daily"
_LINKEDIN_CREATIVE_TABLE = "fact_linkedin_ads_creative_daily"
_PAGE_PATH_DAILY_TABLE = "vw_page_path_daily"
_PAGE_PATH_SOURCE_DAILY_TABLE = "vw_page_path_source_daily"


def _t(name: str, project_id: str, dataset_id: str) -> str:
    """Build a fully-qualified BQ table/view reference."""
    return f"`{project_id}.{dataset_id}.{name}`"


def _job_config(**params: bigquery.ScalarQueryParameter) -> bigquery.QueryJobConfig:
    cfg = bigquery_service.make_job_config()
    cfg.query_parameters = list(params.values())
    return cfg


def _clean_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _clean_row(row: Any) -> dict[str, Any]:
    return {k: _clean_value(v) for k, v in dict(row.items()).items()}


def _run(
    sql: str,
    *,
    params: dict[str, bigquery.ScalarQueryParameter],
    project_id: str,
    credentials_env: str | None,
    max_rows: int,
) -> list[dict[str, Any]]:
    client = bigquery_service.build_client(
        project_id=project_id,
        credentials_env=credentials_env,
    )
    rows = client.query(sql, job_config=_job_config(**params)).result(max_results=max_rows)
    return [_clean_row(row) for row in rows]


def fetch_summary(
    *,
    client_key: str,
    start_date: date,
    end_date: date,
    project_id: str,
    dataset_id: str = _DEFAULT_DATASET,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    view = _t(_PAID_MEDIA_VIEW, project_id, dataset_id)
    date_params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }

    totals_sql = f"""
    WITH totals AS (
      SELECT
        COALESCE(SUM(COALESCE(spend, 0)), 0) AS spend,
        COALESCE(SUM(COALESCE(impressions, 0)), 0) AS impressions,
        COALESCE(SUM(COALESCE(clicks, 0)), 0) AS clicks,
        COALESCE(SUM(COALESCE(conversions, 0)), 0) AS conversions
      FROM {view}
      WHERE `date` BETWEEN @start_date AND @end_date
    )
    SELECT
      ROUND(spend, 2) AS spend,
      CAST(impressions AS INT64) AS impressions,
      CAST(clicks AS INT64) AS clicks,
      conversions,
      COALESCE(ROUND(SAFE_DIVIDE(spend, clicks), 2), 0) AS cpc,
      COALESCE(ROUND(SAFE_DIVIDE(spend, conversions), 2), 0) AS cpa,
      COALESCE(ROUND(SAFE_DIVIDE(clicks, impressions) * 100, 2), 0) AS ctr
    FROM totals
    """
    rows = _run(totals_sql, params=date_params, project_id=project_id, credentials_env=credentials_env, max_rows=1)
    summary = rows[0] if rows else {
        "spend": 0.0, "impressions": 0, "clicks": 0,
        "conversions": 0.0, "cpc": 0.0, "cpa": 0.0, "ctr": 0.0,
    }

    by_source_sql = f"""
    SELECT
      source_platform,
      ROUND(SUM(COALESCE(spend, 0)), 2) AS spend,
      CAST(SUM(COALESCE(impressions, 0)) AS INT64) AS impressions,
      CAST(SUM(COALESCE(clicks, 0)) AS INT64) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions
    FROM {view}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY source_platform
    ORDER BY spend DESC
    """
    source_rows = _run(by_source_sql, params=dict(date_params), project_id=project_id, credentials_env=credentials_env, max_rows=50)
    by_source = {
        str(r.get("source_platform") or "").lower(): {
            "spend": r.get("spend"),
            "impressions": r.get("impressions"),
            "clicks": r.get("clicks"),
            "conversions": r.get("conversions"),
        }
        for r in source_rows if r.get("source_platform")
    }

    daily_sql = f"""
    SELECT
      CAST(`date` AS STRING) AS date,
      source_platform AS source,
      ROUND(SUM(COALESCE(spend, 0)), 2) AS spend,
      CAST(SUM(COALESCE(impressions, 0)) AS INT64) AS impressions,
      CAST(SUM(COALESCE(clicks, 0)) AS INT64) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions
    FROM {view}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY `date`, source_platform
    ORDER BY `date`, source_platform
    """
    daily = _run(daily_sql, params=dict(date_params), project_id=project_id, credentials_env=credentials_env, max_rows=20000)

    return {
        "client_key": client_key,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": summary,
        "by_source": by_source,
        "daily": daily,
    }


def fetch_health(
    *,
    client_key: str,
    limit: int = 100,
    project_id: str,
    dataset_id: str = _DEFAULT_DATASET,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    health_tbl = _t(_HEALTH_TABLE, project_id, dataset_id)
    sql = f"SELECT * FROM {health_tbl} LIMIT @limit"
    rows = _run(
        sql,
        params={"limit": bigquery.ScalarQueryParameter("limit", "INT64", int(limit))},
        project_id=project_id,
        credentials_env=credentials_env,
        max_rows=limit,
    )

    page_tbl = _t(_PAGE_PATH_DAILY_TABLE, project_id, dataset_id)
    ga4_sql = f"""
    SELECT
      'google_analytics' AS source,
      COUNT(*) AS row_count,
      MIN(`date`) AS earliest_date,
      MAX(`date`) AS latest_date,
      CAST(NULL AS FLOAT64) AS spend,
      CAST(NULL AS INT64) AS impressions,
      CAST(NULL AS INT64) AS clicks,
      CAST(NULL AS FLOAT64) AS conversions
    FROM {page_tbl}
    """
    try:
        ga4_rows = _run(ga4_sql, params={}, project_id=project_id, credentials_env=credentials_env, max_rows=1)
        rows = list(rows) + [r for r in ga4_rows if r.get("row_count")]
    except Exception:
        pass

    return {"client": client_key, "row_count": len(rows), "rows": rows}


def fetch_google_ads_explorer(
    *,
    client_key: str,
    start_date: date,
    end_date: date,
    project_id: str,
    dataset_id: str = _DEFAULT_DATASET,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    tbl = _t(_GOOGLE_ADS_EXPLORER_TABLE, project_id, dataset_id)
    sql = f"""
    SELECT
      source, campaign_id, campaign_name, ad_group_id, ad_group_name, ad_id, ad_label,
      ANY_VALUE(headline_1) AS headline_1, ANY_VALUE(headline_2) AS headline_2,
      ANY_VALUE(headline_3) AS headline_3, ANY_VALUE(description_1) AS description_1,
      ANY_VALUE(description_2) AS description_2, ANY_VALUE(image_ad_name) AS image_ad_name,
      ANY_VALUE(ad_name) AS ad_name, ANY_VALUE(final_url) AS final_url,
      ANY_VALUE(ad_type) AS ad_type,
      ROUND(SUM(spend), 2) AS spend, SUM(impressions) AS impressions,
      SUM(clicks) AS clicks, SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {tbl}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY source, campaign_id, campaign_name, ad_group_id, ad_group_name, ad_id, ad_label
    ORDER BY spend DESC
    """
    rows = _run(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        project_id=project_id,
        credentials_env=credentials_env,
        max_rows=20000,
    )
    return {
        "client": client_key,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_linkedin_explorer(
    *,
    client_key: str,
    start_date: date,
    end_date: date,
    project_id: str,
    dataset_id: str = _DEFAULT_DATASET,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    tbl = _t(_LINKEDIN_CREATIVE_TABLE, project_id, dataset_id)
    sql = f"""
    SELECT
      campaign_group_name, campaign_name, creative_id,
      ANY_VALUE(creative_name) AS creative_name,
      ANY_VALUE(media_type) AS media_type,
      ANY_VALUE(thumbnail_url) AS thumbnail_url,
      ANY_VALUE(image_url) AS image_url,
      ROUND(SUM(spend), 2) AS spend, SUM(impressions) AS impressions,
      SUM(clicks) AS clicks, SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value
    FROM {tbl}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY campaign_group_name, campaign_name, creative_id
    ORDER BY spend DESC
    """
    rows = _run(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        project_id=project_id,
        credentials_env=credentials_env,
        max_rows=20000,
    )
    return {
        "client": client_key,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_pages_top(
    *,
    client_key: str,
    start_date: date,
    end_date: date,
    project_id: str,
    dataset_id: str = _DEFAULT_DATASET,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    tbl = _t(_PAGE_PATH_DAILY_TABLE, project_id, dataset_id)
    sql = f"""
    SELECT
      page_path, ANY_VALUE(page_group) AS page_group, ANY_VALUE(page_topic) AS page_topic,
      SUM(page_views) AS page_views, SUM(users) AS users, SUM(sessions) AS sessions,
      ROUND(SUM(engagement_seconds), 1) AS engagement_seconds
    FROM {tbl}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path
    ORDER BY page_views DESC
    """
    rows = _run(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        project_id=project_id,
        credentials_env=credentials_env,
        max_rows=20000,
    )
    return {
        "client": client_key,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_pages_sources(
    *,
    client_key: str,
    start_date: date,
    end_date: date,
    project_id: str,
    dataset_id: str = _DEFAULT_DATASET,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    tbl = _t(_PAGE_PATH_SOURCE_DAILY_TABLE, project_id, dataset_id)
    sql = f"""
    SELECT
      page_path, ANY_VALUE(page_group) AS page_group, ANY_VALUE(page_topic) AS page_topic,
      source_platform, is_ai_referral, ai_platform, utm_campaign,
      SUM(page_views) AS page_views, SUM(users) AS users, SUM(sessions) AS sessions,
      ROUND(SUM(engagement_seconds), 1) AS engagement_seconds
    FROM {tbl}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path, source_platform, is_ai_referral, ai_platform, utm_campaign
    ORDER BY page_views DESC
    """
    rows = _run(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        project_id=project_id,
        credentials_env=credentials_env,
        max_rows=50000,
    )
    return {
        "client": client_key,
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }
