from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

import bigquery_service

_PROJECT_ID = "nixon-medical"
_DATASET_ID = "marketing_marts"
_FACT_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.fact_marketing_daily`"
# Paid-media reads use the normalized view (source_platform = paid_google /
# paid_linkedin); raw_source is debug-only. Supersedes fact_marketing_daily here.
_PAID_MEDIA_VIEW = f"`{_PROJECT_ID}.{_DATASET_ID}.vw_paid_media_daily`"
_HEALTH_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.mart_health`"
_GOOGLE_ADS_EXPLORER_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.explorer_google_ads_daily`"
_LINKEDIN_CREATIVE_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.fact_linkedin_ads_creative_daily`"
_META_AD_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.fact_meta_ads_ad_daily`"
_PAGE_PATH_DAILY_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.vw_page_path_daily`"
_PAGE_PATH_SOURCE_DAILY_TABLE = f"`{_PROJECT_ID}.{_DATASET_ID}.vw_page_path_source_daily`"

_GA4_DATASET = "raw_ga4"
_TRAFFIC_ACQ_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_sessions_daily`"
_TECH_DETAILS_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_tech_daily`"
_LANDING_PAGE_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_pages_daily`"
_PAGEVIEWS_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_pageviews_daily`"
_EVENTS_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_events_daily`"
_USER_ACQ_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_user_acq_daily`"
_GEO_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_geo_daily`"
_DEMOGRAPHICS_TABLE = f"`{_PROJECT_ID}.{_GA4_DATASET}.ga4_demographics_daily`"


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

    # Append a Google Analytics freshness row from the page-path mart (GA4 isn't
    # in mart_health, which only tracks paid media). No spend — it's website
    # analytics — so the metric columns stay null and the UI shows them as "—".
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
    FROM {_PAGE_PATH_DAILY_TABLE}
    """
    try:
        ga4_rows = _run_query(ga4_sql, params={}, max_rows=1)
        rows = list(rows) + [r for r in ga4_rows if r.get("row_count")]
    except Exception:
        pass

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
      FROM {_PAID_MEDIA_VIEW}
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
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    rows = _run_query(sql, params=params, max_rows=1)
    summary = rows[0] if rows else {
        "spend": 0.0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0.0,
        "cpc": 0.0,
        "cpa": 0.0,
        "ctr": 0.0,
    }

    # Per-platform breakdown so the dashboard can filter the summary cards by
    # platform (paid_google / paid_linkedin) without a second round-trip.
    by_source_sql = f"""
    SELECT
      source_platform,
      ROUND(SUM(COALESCE(spend, 0)), 2) AS spend,
      CAST(SUM(COALESCE(impressions, 0)) AS INT64) AS impressions,
      CAST(SUM(COALESCE(clicks, 0)) AS INT64) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions
    FROM {_PAID_MEDIA_VIEW}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY source_platform
    ORDER BY spend DESC
    """
    source_rows = _run_query(by_source_sql, params=dict(params), max_rows=50)
    by_source = {
        str(r.get("source_platform") or "").lower(): {
            "spend": r.get("spend"),
            "impressions": r.get("impressions"),
            "clicks": r.get("clicks"),
            "conversions": r.get("conversions"),
        }
        for r in source_rows
        if r.get("source_platform")
    }

    # Per-date-per-source daily series for the trend chart (so it can filter by
    # platform and derive cpc/cpa/ctr per day, client-side).
    daily_sql = f"""
    SELECT
      CAST(`date` AS STRING) AS date,
      source_platform AS source,
      ROUND(SUM(COALESCE(spend, 0)), 2) AS spend,
      CAST(SUM(COALESCE(impressions, 0)) AS INT64) AS impressions,
      CAST(SUM(COALESCE(clicks, 0)) AS INT64) AS clicks,
      SUM(COALESCE(conversions, 0)) AS conversions
    FROM {_PAID_MEDIA_VIEW}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY `date`, source_platform
    ORDER BY `date`, source_platform
    """
    daily = _run_query(daily_sql, params=dict(params), max_rows=20000)

    return {
        "client_key": "nixon",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": summary,
        "by_source": by_source,
        "daily": daily,
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
      ANY_VALUE(headline_1) AS headline_1,
      ANY_VALUE(headline_2) AS headline_2,
      ANY_VALUE(headline_3) AS headline_3,
      ANY_VALUE(description_1) AS description_1,
      ANY_VALUE(description_2) AS description_2,
      ANY_VALUE(image_ad_name) AS image_ad_name,
      ANY_VALUE(ad_name) AS ad_name,
      ANY_VALUE(final_url) AS final_url,
      ANY_VALUE(ad_type) AS ad_type,
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


def fetch_nixon_linkedin_explorer(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """LinkedIn creative-level explorer (campaign group > campaign/ad set > creative).

    Mirrors fetch_nixon_google_ads_explorer. The dashboard renderer maps this
    onto the Google tree levels and shows each creative's thumbnail.
    """
    sql = f"""
    SELECT
      campaign_group_name,
      campaign_name,
      creative_id,
      ANY_VALUE(creative_name) AS creative_name,
      ANY_VALUE(media_type) AS media_type,
      ANY_VALUE(thumbnail_url) AS thumbnail_url,
      ANY_VALUE(image_url) AS image_url,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value
    FROM {_LINKEDIN_CREATIVE_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY campaign_group_name, campaign_name, creative_id
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


def fetch_nixon_meta_explorer(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Meta ad-level explorer (campaign → adset → ad with thumbnails)."""
    sql = f"""
    SELECT
      campaign_name,
      adset_name,
      ad_id,
      ANY_VALUE(ad_name)        AS ad_name,
      ANY_VALUE(media_type)     AS media_type,
      ANY_VALUE(thumbnail_url)  AS thumbnail_url,
      ANY_VALUE(image_url)      AS image_url,
      ROUND(SUM(spend), 2)      AS spend,
      SUM(impressions)          AS impressions,
      SUM(clicks)               AS clicks,
      SUM(conversions)          AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value
    FROM {_META_AD_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY campaign_name, adset_name, ad_id
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


def fetch_nixon_pages_top(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Top pages (all traffic) from vw_page_path_daily, aggregated per page."""
    sql = f"""
    SELECT
      page_path,
      ANY_VALUE(page_group) AS page_group,
      ANY_VALUE(page_topic) AS page_topic,
      SUM(page_views) AS page_views,
      SUM(users) AS users,
      SUM(sessions) AS sessions,
      ROUND(SUM(engagement_seconds), 1) AS engagement_seconds,
      CAST(0 AS INT64) AS key_events
    FROM {_PAGE_PATH_DAILY_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path
    ORDER BY page_views DESC
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
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_nixon_traffic_acquisition(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    by_channel_sql = f"""
    SELECT
      COALESCE(sessionDefaultChannelGroup, '(other)') AS channel,
      SUM(sessions) AS sessions,
      SUM(engagedSessions) AS engaged_sessions,
      ROUND(SAFE_DIVIDE(SUM(engagedSessions), NULLIF(SUM(sessions), 0)) * 100, 1) AS engagement_rate,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events
    FROM {_TRAFFIC_ACQ_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY channel
    ORDER BY sessions DESC
    """
    daily_sql = f"""
    SELECT
      CAST(date AS STRING) AS date,
      SUM(sessions) AS sessions,
      SUM(engagedSessions) AS engaged_sessions
    FROM {_TRAFFIC_ACQ_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY date
    ORDER BY date ASC
    """
    by_source_sql = f"""
    SELECT
      COALESCE(sessionSource, '(direct)') AS source,
      COALESCE(sessionMedium, '(none)') AS medium,
      SUM(sessions) AS sessions,
      SUM(engagedSessions) AS engaged_sessions,
      ROUND(SAFE_DIVIDE(SUM(engagedSessions), NULLIF(SUM(sessions), 0)) * 100, 1) AS engagement_rate,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events
    FROM {_TRAFFIC_ACQ_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY source, medium
    ORDER BY sessions DESC
    LIMIT 25
    """
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_channel": _run_query(by_channel_sql, params=params, max_rows=50),
        "daily": _run_query(daily_sql, params=params, max_rows=2000),
        "by_source": _run_query(by_source_sql, params=params, max_rows=50),
    }


def fetch_nixon_device_split(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    sql = f"""
    SELECT
      COALESCE(deviceCategory, 'unknown') AS device,
      SUM(activeUsers) AS users,
      SUM(engagedSessions) AS engaged_sessions,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events
    FROM {_TECH_DETAILS_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY device
    ORDER BY users DESC
    """
    rows = _run_query(sql, params=params, max_rows=20)
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "rows": rows,
    }


def fetch_nixon_landing_pages(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    sql = f"""
    SELECT
      COALESCE(landingPage, '/') AS page_path,
      SUM(sessions) AS sessions,
      SUM(activeUsers) AS users,
      SUM(newUsers) AS new_users,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events,
      ROUND(SAFE_DIVIDE(SUM(keyEvents), NULLIF(SUM(sessions), 0)) * 100, 1) AS key_event_rate,
      ROUND(AVG(averageSessionDuration), 1) AS avg_engagement_seconds
    FROM {_LANDING_PAGE_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path
    ORDER BY sessions DESC
    LIMIT 100
    """
    rows = _run_query(sql, params=params, max_rows=100)
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_nixon_pages_sources(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Per-page traffic broken out by source / AI referral from
    vw_page_path_source_daily, for filtering the top-pages list."""
    sql = f"""
    SELECT
      page_path,
      ANY_VALUE(page_group) AS page_group,
      ANY_VALUE(page_topic) AS page_topic,
      source_platform,
      is_ai_referral,
      ai_platform,
      utm_campaign,
      SUM(page_views) AS page_views,
      SUM(users) AS users,
      SUM(sessions) AS sessions,
      ROUND(SUM(engagement_seconds), 1) AS engagement_seconds,
      CAST(0 AS INT64) AS key_events
    FROM {_PAGE_PATH_SOURCE_DAILY_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path, source_platform, is_ai_referral, ai_platform, utm_campaign
    ORDER BY page_views DESC
    """
    rows = _run_query(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        },
        max_rows=50000,
    )
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_nixon_conversion_events(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Event breakdown from ga4_Events, filtered to non-automatic events."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    sql = f"""
    SELECT
      eventName AS event_name,
      SUM(eventCount) AS event_count,
      SUM(totalUsers) AS total_users,
      ROUND(AVG(eventCountPerUser), 2) AS event_count_per_user
    FROM {_EVENTS_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
      AND eventName NOT IN (
        'page_view', 'session_start', 'user_engagement', 'first_visit',
        'scroll', 'click', 'Click', 'trackOptanonEvent',
        'Banner Accept Cookies', 'Banner Close Button'
      )
    GROUP BY event_name
    ORDER BY event_count DESC
    """
    rows = _run_query(sql, params=params, max_rows=100)
    # Form funnel subset
    funnel_names = {"form_start", "form_submit", "generate_lead"}
    funnel_map = {r["event_name"]: r["event_count"] for r in rows if r["event_name"] in funnel_names}
    funnel = [
        {"step": "Form start", "event": "form_start", "count": funnel_map.get("form_start", 0)},
        {"step": "Form submit", "event": "form_submit", "count": funnel_map.get("form_submit", 0)},
        {"step": "Lead generated", "event": "generate_lead", "count": funnel_map.get("generate_lead", 0)},
    ]
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "rows": rows,
        "funnel": funnel,
    }


def fetch_nixon_user_acquisition(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """First-touch attribution for new users from ga4_UserAcquisition."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    channel_sql = f"""
    SELECT
      COALESCE(firstUserDefaultChannelGroup, '(not set)') AS channel,
      SUM(newUsers) AS new_users,
      SUM(activeUsers) AS active_users,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events,
      ROUND(SAFE_DIVIDE(SUM(keyEvents), NULLIF(SUM(totalUsers), 0)) * 100, 1) AS key_event_rate
    FROM {_USER_ACQ_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY channel
    ORDER BY new_users DESC
    LIMIT 15
    """
    source_sql = f"""
    SELECT
      COALESCE(firstUserSource, '(direct)') AS source,
      COALESCE(firstUserMedium, '(none)') AS medium,
      SUM(newUsers) AS new_users,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events,
      ROUND(SAFE_DIVIDE(SUM(keyEvents), NULLIF(SUM(totalUsers), 0)) * 100, 1) AS key_event_rate
    FROM {_USER_ACQ_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY source, medium
    ORDER BY new_users DESC
    LIMIT 30
    """
    by_channel = _run_query(channel_sql, params=params, max_rows=15)
    by_source = _run_query(source_sql, params=params, max_rows=30)
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_channel": by_channel,
        "by_source": by_source,
    }


def fetch_nixon_demographics(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Geographic and demographic breakdown from ga4_DemographicDetails."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    city_sql = f"""
    SELECT
      COALESCE(city, '(not set)') AS city,
      COALESCE(region, '') AS region,
      COALESCE(country, '') AS country,
      SUM(activeUsers) AS users,
      SUM(sessions) AS sessions,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events
    FROM {_GEO_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
      AND city IS NOT NULL AND city != '(not set)'
    GROUP BY city, region, country
    ORDER BY users DESC
    LIMIT 20
    """
    age_sql = f"""
    SELECT
      COALESCE(userAgeBracket, 'unknown') AS age_bracket,
      SUM(activeUsers) AS users,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events
    FROM {_DEMOGRAPHICS_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
      AND userAgeBracket IS NOT NULL AND userAgeBracket != '(not set)'
    GROUP BY age_bracket
    ORDER BY
      CASE age_bracket
        WHEN '18-24' THEN 1 WHEN '25-34' THEN 2 WHEN '35-44' THEN 3
        WHEN '45-54' THEN 4 WHEN '55-64' THEN 5 WHEN '65+' THEN 6
        ELSE 7 END
    """
    gender_sql = f"""
    SELECT
      COALESCE(userGender, 'unknown') AS gender,
      SUM(activeUsers) AS users,
      CAST(ROUND(SUM(keyEvents)) AS INT64) AS key_events
    FROM {_DEMOGRAPHICS_TABLE}
    WHERE date BETWEEN @start_date AND @end_date
      AND userGender IS NOT NULL AND userGender NOT IN ('(not set)', 'unknown')
    GROUP BY gender
    ORDER BY users DESC
    """
    by_city = _run_query(city_sql, params=params, max_rows=20)
    by_age = _run_query(age_sql, params=params, max_rows=10)
    by_gender = _run_query(gender_sql, params=params, max_rows=5)
    return {
        "client": "nixon",
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_city": by_city,
        "by_age": by_age,
        "by_gender": by_gender,
    }
