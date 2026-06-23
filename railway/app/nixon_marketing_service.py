from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

import bigquery_service

_PROJECT_ID = "nixon-medical"
_DATASET_ID = "marketing_marts"
_FACT_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.fact_marketing_daily`"
_HEALTH_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.mart_health`"
_GOOGLE_ADS_EXPLORER_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.explorer_google_ads_daily`"


def _job_config(**params: bigquery.ScalarQueryParameter) -> bigquery.QueryJobConfig:
    config = bigquery_service.make_job_config()
    config.query_parameters = list(params.values())
    return config


def _clean_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _clean_row(row: Any) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in dict(row.items()).items()}


def _run_query(
    sql: str,
    *,
    params: dict[str, bigquery.ScalarQueryParameter],
    max_rows: int,
) -> list[dict[str, Any]]:
    client = bigquery_service.build_client(project_id=_PROJECT_ID)
    rows = client.query(sql, job_config=_job_config(**params)).result(max_results=max_rows)
    return [_clean_row(row) for row in rows]


def fetch_nixon_marketing(
    *,
    start_date: date,
    end_date: date,
    top_limit: int = 10,
) -> dict[str, Any]:
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    summary_sql = f"""
    SELECT
      COALESCE(SUM(COALESCE(spend, 0)), 0) AS spend,
      COALESCE(SUM(COALESCE(impressions, 0)), 0) AS impressions,
      COALESCE(SUM(COALESCE(clicks, 0)), 0) AS clicks,
      COALESCE(SUM(COALESCE(conversions, 0)), 0) AS conversions,
      COALESCE(SUM(COALESCE(conversion_value, 0)), 0) AS conversion_value
    FROM {_FACT_TABLE}
    WHERE `date` BETWEEN @start_date AND @end_date
    """
    by_source_sql = f"""
    SELECT
      source,
      SUM(COALESCE(spend, 0)) AS spend,
      SUM(COALESCE(impressions, 0)) AS impressions,
      SUM(COALESCE(clicks, 0)) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions,
      SUM(COALESCE(conversion_value, 0)) AS conversion_value
    FROM {_FACT_TABLE}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY source
    ORDER BY spend DESC
    """
    daily_trend_sql = f"""
    SELECT
      CAST(`date` AS STRING) AS date,
      source,
      SUM(COALESCE(spend, 0)) AS spend,
      SUM(COALESCE(impressions, 0)) AS impressions,
      SUM(COALESCE(clicks, 0)) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions,
      SUM(COALESCE(conversion_value, 0)) AS conversion_value
    FROM {_FACT_TABLE}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY `date`, source
    ORDER BY `date` ASC, source ASC
    """
    top_campaigns_sql = f"""
    SELECT
      source,
      CAST(campaign_id AS STRING) AS campaign_id,
      ANY_VALUE(campaign_name) AS campaign_name,
      SUM(COALESCE(spend, 0)) AS spend,
      SUM(COALESCE(impressions, 0)) AS impressions,
      SUM(COALESCE(clicks, 0)) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions,
      SUM(COALESCE(conversion_value, 0)) AS conversion_value
    FROM {_FACT_TABLE}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY source, campaign_id
    ORDER BY spend DESC
    LIMIT @top_limit
    """

    summary_rows = _run_query(summary_sql, params=params, max_rows=1)
    top_params = dict(params)
    top_params["top_limit"] = bigquery.ScalarQueryParameter("top_limit", "INT64", int(top_limit))
    return {
        "client": "nixon",
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "summary": summary_rows[0] if summary_rows else {
            "spend": 0.0,
            "impressions": 0,
            "clicks": 0,
            "conversions": 0.0,
            "conversion_value": 0.0,
        },
        "by_source": _run_query(by_source_sql, params=params, max_rows=100),
        "daily_trend": _run_query(daily_trend_sql, params=params, max_rows=20000),
        "top_campaigns_by_spend": _run_query(top_campaigns_sql, params=top_params, max_rows=top_limit),
    }


def fetch_nixon_marketing_health(*, limit: int = 100) -> dict[str, Any]:
    sql = f"""
    SELECT *
    FROM {_HEALTH_TABLE}
    LIMIT @limit
    """
    rows = _run_query(
        sql,
        params={"limit": bigquery.ScalarQueryParameter("limit", "INT64", int(limit))},
        max_rows=limit,
    )
    return {"client": "nixon", "row_count": len(rows), "rows": rows}


def fetch_nixon_summary(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    sql = f"""
    WITH totals AS (
      SELECT
        COALESCE(SUM(COALESCE(spend, 0)), 0) AS spend,
        COALESCE(SUM(COALESCE(impressions, 0)), 0) AS impressions,
        COALESCE(SUM(COALESCE(clicks, 0)), 0) AS clicks,
        COALESCE(SUM(COALESCE(conversions, 0)), 0) AS conversions
      FROM {_FACT_TABLE}
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
    rows = _run_query(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        max_rows=1,
    )
    summary = rows[0] if rows else {
        "spend": 0.0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0.0,
        "cpc": 0.0,
        "cpa": 0.0,
        "ctr": 0.0,
    }
    return {
        "client_key": "nixon",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": summary,
    }


def fetch_nixon_google_ads_explorer(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    sql = f"""
    SELECT
      source,
      campaign_id,
      campaign_name,
      ad_group_id,
      ad_group_name,
      ad_id,
      ad_label,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {_GOOGLE_ADS_EXPLORER_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY
      source, campaign_id, campaign_name, ad_group_id, ad_group_name, ad_id, ad_label
    ORDER BY spend DESC
    """
    rows = _run_query(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        max_rows=20000,
    )
    return {
        "client": "nixon",
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "row_count": len(rows),
        "rows": rows,
    }
