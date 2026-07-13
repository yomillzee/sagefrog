from __future__ import annotations

import contextlib
import contextvars
from datetime import date
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

import bigquery_service

# This module started as Nixon-only (hardcoded project/dataset/client_key).
# route() lets any other BigQuery-mode client reuse the exact same queries
# against their own project/dataset — default (no route() context) preserves
# the original Nixon-only behavior unchanged, so Nixon's existing routes and
# bookmarks keep working exactly as before.
_DEFAULT_CLIENT_KEY = "nixon"
_DEFAULT_PROJECT_ID = "nixon-medical"
_DEFAULT_DATASET_ID = "marketing_marts"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "nixon_marketing_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    client_key: str | None = None,
    project_id: str | None = None,
    mart_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    """Scope every fetch_* call in this module to one client's BQ destination.

    All four params are optional and independently overridable; anything left
    unset falls back to Nixon's own defaults, not to whatever the last route()
    call set — each call starts from the same baseline.
    """
    payload = {
        "client_key": (client_key or "").strip() or None,
        "project_id": (project_id or "").strip() or None,
        "dataset_id": (mart_dataset_id or "").strip() or None,
        "credentials_env": (credentials_env or "").strip() or None,
    }
    token = _route_ctx.set(payload if any(payload.values()) else None)
    try:
        yield
    finally:
        _route_ctx.reset(token)


def _ctx() -> dict:
    return _route_ctx.get() or {}


def _client_key() -> str:
    return _ctx().get("client_key") or _DEFAULT_CLIENT_KEY


def _project_id() -> str:
    return _ctx().get("project_id") or _DEFAULT_PROJECT_ID


def _dataset_id() -> str:
    return _ctx().get("dataset_id") or _DEFAULT_DATASET_ID


def _credentials_env() -> str | None:
    return _ctx().get("credentials_env")


def _fact_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_marketing_daily`"


def _paid_media_view() -> str:
    # Paid-media reads use the normalized view (source_platform = paid_google /
    # paid_linkedin); raw_source is debug-only. Supersedes fact_marketing_daily.
    return f"`{_project_id()}.{_dataset_id()}.vw_paid_media_daily`"


def _health_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.mart_health`"


def _google_ads_explorer_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.explorer_google_ads_daily`"


def _google_ads_keyword_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.explorer_google_ads_keyword_daily`"


def _linkedin_creative_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_linkedin_ads_creative_daily`"


def _linkedin_campaign_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_linkedin_ads_campaign_daily`"


def _meta_ad_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_meta_ads_ad_daily`"


def _page_path_daily_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_page_path_daily`"


def _page_path_source_daily_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_page_path_source_daily`"


# All GA4 reads go through marketing_marts views — never raw_ga4 directly.
def _traffic_acq_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_traffic_acq_daily`"


def _tech_details_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_tech_daily`"


def _landing_page_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_landing_pages_daily`"


def _events_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_events_daily`"


def _user_acq_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_user_acq_daily`"


def _geo_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_geo_daily`"


def _demographics_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_demographics_daily`"


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
    client = bigquery_service.build_client(project_id=_project_id(), credentials_env=_credentials_env())
    rows = client.query(sql, job_config=_job_config(**params)).result(max_results=max_rows)
    return [_clean_row(row) for row in rows]


def fetch_marketing(
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
    FROM {_fact_table()}
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
    FROM {_fact_table()}
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
    FROM {_fact_table()}
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
    FROM {_fact_table()}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY source, campaign_id
    ORDER BY spend DESC
    LIMIT @top_limit
    """

    summary_rows = _run_query(summary_sql, params=params, max_rows=1)
    top_params = dict(params)
    top_params["top_limit"] = bigquery.ScalarQueryParameter("top_limit", "INT64", int(top_limit))
    return {
        "client": _client_key(),
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


def fetch_marketing_health(*, limit: int = 100) -> dict[str, Any]:
    sql = f"""
    SELECT
      source,
      row_count,
      earliest_date,
      latest_date,
      spend,
      impressions,
      clicks,
      conversions
    FROM {_health_table()}
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
    FROM {_page_path_daily_table()}
    """
    try:
        ga4_rows = _run_query(ga4_sql, params={}, max_rows=1)
        rows = list(rows) + [r for r in ga4_rows if r.get("row_count")]
    except Exception:
        pass

    # Append a Search Console freshness row too -- same reasoning as GA4 above,
    # and lets the Overview date-comparison feature warn when the requested
    # comparison period predates GSC's synced history.
    try:
        import bq_gsc_service
        gsc_row = bq_gsc_service.gsc_health_row(client_slug=_client_key())
        if gsc_row:
            rows = list(rows) + [gsc_row]
    except Exception:
        pass

    return {"client": _client_key(), "row_count": len(rows), "rows": rows}


def fetch_summary(
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
      FROM {_paid_media_view()}
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
    FROM {_paid_media_view()}
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
    FROM {_paid_media_view()}
    WHERE `date` BETWEEN @start_date AND @end_date
    GROUP BY `date`, source_platform
    ORDER BY `date`, source_platform
    """
    daily = _run_query(daily_sql, params=dict(params), max_rows=20000)

    return {
        "client_key": _client_key(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": summary,
        "by_source": by_source,
        "daily": daily,
    }


def fetch_google_ads_explorer(
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
      ANY_VALUE(headlines) AS headlines,
      ANY_VALUE(descriptions) AS descriptions,
      ANY_VALUE(image_ad_name) AS image_ad_name,
      ANY_VALUE(ad_name) AS ad_name,
      ANY_VALUE(final_url) AS final_url,
      ANY_VALUE(ad_type) AS ad_type,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {_google_ads_explorer_table()}
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
        "client": _client_key(),
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_linkedin_explorer(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """LinkedIn explorer (campaign group > campaign/ad set > creative).

    Mirrors fetch_google_ads_explorer. The dashboard renderer maps this
    onto the Google tree levels and shows each creative's thumbnail.

    Prefers the creative-level mart (`fact_linkedin_ads_creative_daily`) so the
    tree can drill down to individual creatives with thumbnails. Many clients
    only sync LinkedIn at the campaign level, though -- their mart has
    `fact_linkedin_ads_campaign_daily` but no creative table. For them the
    creative query would raise "table not found", the endpoint would 500, and
    the frontend would silently drop LinkedIn (leaving only Google). So when the
    creative table is missing or empty for the range, fall back to the
    campaign-level mart: same row shape, minus the per-creative breakdown.
    """
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    creative_sql = f"""
    SELECT
      campaign_group_name,
      campaign_name,
      creative_id,
      ANY_VALUE(creative_name) AS creative_name,
      ANY_VALUE(media_type) AS media_type,
      ANY_VALUE(thumbnail_url) AS thumbnail_url,
      ANY_VALUE(image_url) AS image_url,
      ANY_VALUE(video_url) AS video_url,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value
    FROM {_linkedin_creative_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY campaign_group_name, campaign_name, creative_id
    ORDER BY spend DESC
    """
    # Campaign-level fallback -- no creative_id/thumbnail, but the same columns
    # the renderer reads (campaign_group_name/campaign_name/metrics). One row per
    # campaign; NULL creative fields degrade to empty in normalizeExplorerRows.
    campaign_sql = f"""
    SELECT
      campaign_group_name,
      campaign_name,
      CAST(NULL AS INT64) AS creative_id,
      CAST(NULL AS STRING) AS creative_name,
      CAST(NULL AS STRING) AS media_type,
      CAST(NULL AS STRING) AS thumbnail_url,
      CAST(NULL AS STRING) AS image_url,
      CAST(NULL AS STRING) AS video_url,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value
    FROM {_linkedin_campaign_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY campaign_group_name, campaign_name
    ORDER BY spend DESC
    """

    level = "creative"
    try:
        rows = _run_query(creative_sql, params=params, max_rows=20000)
    except Exception:
        rows = []
    if not rows:
        # Creative table missing/empty -- try the campaign-level mart. If that
        # also doesn't exist (client has no LinkedIn data at all), return an
        # empty result rather than 500ing the endpoint.
        try:
            rows = _run_query(campaign_sql, params=params, max_rows=20000)
            level = "campaign"
        except Exception:
            rows = []
            level = "none"

    return {
        "client": _client_key(),
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "level": level,
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_google_ads_keywords(
    *,
    start_date: date,
    end_date: date,
    top_limit: int = 200,
) -> dict[str, Any]:
    """Search-keyword performance ("Cost by Keyword"), aggregated over the range.

    One row per keyword (criterion), ordered by spend desc. CTR and Avg CPC are
    derived. Reads explorer_google_ads_keyword_daily, which only exists once a
    client has synced with the keyword report -- callers treat a missing table
    as "no keyword data" (empty table).
    """
    sql = f"""
    SELECT
      criterion_id,
      ANY_VALUE(keyword_text) AS keyword_text,
      ANY_VALUE(match_type) AS match_type,
      ANY_VALUE(ad_group_name) AS ad_group_name,
      ANY_VALUE(campaign_name) AS campaign_name,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value,
      ROUND(SAFE_DIVIDE(SUM(clicks), NULLIF(SUM(impressions), 0)) * 100, 2) AS ctr,
      ROUND(SAFE_DIVIDE(SUM(spend), NULLIF(SUM(clicks), 0)), 2) AS avg_cpc
    FROM {_google_ads_keyword_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY criterion_id
    ORDER BY spend DESC
    LIMIT @top_limit
    """
    rows = _run_query(
        sql,
        params={
            "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            "top_limit": bigquery.ScalarQueryParameter("top_limit", "INT64", int(top_limit)),
        },
        max_rows=top_limit,
    )
    return {
        "client": _client_key(),
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_meta_explorer(
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
    FROM {_meta_ad_table()}
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
        "client": _client_key(),
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_pages_top(
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
      SUM(key_events) AS key_events
    FROM {_page_path_daily_table()}
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
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_traffic_acquisition(
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
      COALESCE(default_channel_group, '(other)') AS channel,
      SUM(sessions) AS sessions,
      SUM(engaged_sessions) AS engaged_sessions,
      ROUND(SAFE_DIVIDE(SUM(engaged_sessions), NULLIF(SUM(sessions), 0)) * 100, 1) AS engagement_rate,
      SUM(key_events) AS key_events
    FROM {_traffic_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY channel
    ORDER BY sessions DESC
    """
    daily_sql = f"""
    SELECT
      CAST(date AS STRING) AS date,
      SUM(sessions) AS sessions,
      SUM(engaged_sessions) AS engaged_sessions
    FROM {_traffic_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY date
    ORDER BY date ASC
    """
    by_source_sql = f"""
    SELECT
      COALESCE(source, '(direct)') AS source,
      COALESCE(medium, '(none)') AS medium,
      SUM(sessions) AS sessions,
      SUM(engaged_sessions) AS engaged_sessions,
      ROUND(SAFE_DIVIDE(SUM(engaged_sessions), NULLIF(SUM(sessions), 0)) * 100, 1) AS engagement_rate,
      SUM(key_events) AS key_events
    FROM {_traffic_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY source, medium
    ORDER BY sessions DESC
    LIMIT 25
    """
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_channel": _run_query(by_channel_sql, params=params, max_rows=50),
        "daily": _run_query(daily_sql, params=params, max_rows=2000),
        "by_source": _run_query(by_source_sql, params=params, max_rows=50),
    }


def fetch_sessions_daily(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Daily total GA4 sessions for a date range — one lean query.

    A trimmed slice of fetch_traffic_acquisition (just the daily series, no
    channel/source breakdowns) so callers that only need a sessions sparkline
    pay for a single scan of vw_ga4_traffic_acq_daily.
    """
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    daily_sql = f"""
    SELECT
      CAST(date AS STRING) AS date,
      SUM(sessions) AS sessions
    FROM {_traffic_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY date
    ORDER BY date ASC
    """
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "daily": _run_query(daily_sql, params=params, max_rows=2000),
    }


def fetch_device_split(
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
      COALESCE(device_category, 'unknown') AS device,
      SUM(users) AS users,
      SUM(sessions) AS engaged_sessions,
      SUM(key_events) AS key_events
    FROM {_tech_details_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY device
    ORDER BY users DESC
    """
    try:
        rows = _run_query(sql, params=params, max_rows=20)
    except Exception:
        rows = []
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "rows": rows,
    }


def fetch_landing_pages(
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
      COALESCE(landing_page, '/') AS page_path,
      SUM(sessions) AS sessions,
      SUM(active_users) AS users,
      SUM(new_users) AS new_users,
      SUM(key_events) AS key_events,
      ROUND(SAFE_DIVIDE(SUM(key_events), NULLIF(SUM(sessions), 0)) * 100, 1) AS key_event_rate,
      ROUND(AVG(average_session_duration), 1) AS avg_engagement_seconds
    FROM {_landing_page_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path
    ORDER BY sessions DESC
    LIMIT 100
    """
    rows = _run_query(sql, params=params, max_rows=100)
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def _landing_page_events_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_landing_page_events_daily`"


def fetch_landing_page_events(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Per landing_page × event breakdown so the dashboard can recompute the
    landing-page 'key events' column for a client-selected set of events.
    `events` lists every event with its total + GA4 key-event count (the
    default selection is the events where key_events > 0)."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    rows_sql = f"""
    SELECT
      COALESCE(landing_page, '/') AS page_path,
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {_landing_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path, event_name
    """
    events_sql = f"""
    SELECT
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {_landing_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY event_name
    ORDER BY event_count DESC
    """
    try:
        rows = _run_query(rows_sql, params=params, max_rows=100000)
        events = _run_query(events_sql, params=dict(params), max_rows=1000)
    except Exception:
        rows, events = [], []
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "rows": rows,
        "events": events,
    }


def _page_events_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_page_events_daily`"


def fetch_page_key_events(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Per page_path × event breakdown across ALL traffic (not just entrances)
    so the Top Pages panel can recompute 'key events' for a client-selected
    set of events, same as Landing/Traffic/User acquisition."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    rows_sql = f"""
    SELECT
      COALESCE(page_path, '/') AS page_path,
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY page_path, event_name
    """
    events_sql = f"""
    SELECT event_name, SUM(event_count) AS event_count, SUM(key_events) AS key_events
    FROM {_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY event_name
    ORDER BY event_count DESC
    """
    try:
        rows = _run_query(rows_sql, params=params, max_rows=100000)
        events = _run_query(events_sql, params=dict(params), max_rows=1000)
    except Exception:
        rows, events = [], []
    return {"client": _client_key(), "rows": rows, "events": events}


def fetch_active_key_events(*, start_date: date, end_date: date) -> dict[str, Any]:
    """GA4's designated key events for this client: distinct event names that
    GA4 counted as key events (key_events > 0) over the window. Used to cross-
    reference which GTM GA4-event tags fire a real key event."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    sql = f"""
    SELECT event_name, SUM(key_events) AS key_events
    FROM {_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY event_name
    HAVING SUM(key_events) > 0
    ORDER BY key_events DESC
    """
    try:
        rows = _run_query(sql, params=params, max_rows=1000)
    except Exception:
        rows = []
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "event_names": [r["event_name"] for r in rows if r.get("event_name")],
        "events": rows,
    }


def _user_acq_events_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_user_acq_events_daily`"


def _key_events_by_source(table_expr: str, params: dict) -> dict[str, Any]:
    """Shared shape for the Traffic / User-Acquisition key-event overrides:
    per (source, medium, event) counts + a catalog of events (with GA4 key
    flag), so the dashboard can recompute the source table's key-events column
    for a client-selected event set."""
    rows_sql = f"""
    SELECT
      COALESCE(source, '(direct)') AS source,
      COALESCE(medium, '(none)') AS medium,
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {table_expr}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY source, medium, event_name
    """
    events_sql = f"""
    SELECT event_name, SUM(event_count) AS event_count, SUM(key_events) AS key_events
    FROM {table_expr}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY event_name
    ORDER BY event_count DESC
    """
    try:
        rows = _run_query(rows_sql, params=params, max_rows=100000)
        events = _run_query(events_sql, params=dict(params), max_rows=1000)
    except Exception:
        rows, events = [], []
    return {"client": _client_key(), "by_source_events": rows, "events": events}


def fetch_traffic_key_events(*, start_date: date, end_date: date) -> dict[str, Any]:
    """Session-scoped source/medium × event (from vw_ga4_events_daily) so the
    Traffic panel's source table can honour the selected key-event set."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    return _key_events_by_source(_events_table(), params)


def fetch_user_acq_key_events(*, start_date: date, end_date: date) -> dict[str, Any]:
    """First-user source/medium × event (from vw_ga4_user_acq_events_daily)."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    return _key_events_by_source(_user_acq_events_table(), params)


def fetch_pages_sources(
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
    FROM {_page_path_source_daily_table()}
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
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_ai_traffic_daily(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Daily AI-referral sessions by ai_platform, for the AI Traffic trend chart.

    One row per (date, ai_platform); the range aggregate lives in
    fetch_pages_sources. Reads vw_page_path_source_daily, which tags AI
    assistant sessions (is_ai_referral) with the referring platform."""
    sql = f"""
    SELECT
      date,
      ai_platform,
      SUM(sessions) AS sessions
    FROM {_page_path_source_daily_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND is_ai_referral
    GROUP BY date, ai_platform
    ORDER BY date
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
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_conversion_events(
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
      event_name,
      SUM(event_count) AS event_count,
      SUM(users) AS total_users,
      ROUND(SAFE_DIVIDE(SUM(event_count), NULLIF(SUM(users), 0)), 2) AS event_count_per_user
    FROM {_events_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND event_name NOT IN (
        'page_view', 'session_start', 'user_engagement', 'first_visit',
        'scroll', 'click', 'Click', 'trackOptanonEvent',
        'Banner Accept Cookies', 'Banner Close Button'
      )
    GROUP BY event_name
    ORDER BY event_count DESC
    """
    rows = _run_query(sql, params=params, max_rows=100)
    funnel_names = {"form_start", "form_submit", "generate_lead"}
    funnel_map = {r["event_name"]: r["event_count"] for r in rows if r["event_name"] in funnel_names}
    funnel = [
        {"step": "Form start", "event": "form_start", "count": funnel_map.get("form_start", 0)},
        {"step": "Form submit", "event": "form_submit", "count": funnel_map.get("form_submit", 0)},
        {"step": "Lead generated", "event": "generate_lead", "count": funnel_map.get("generate_lead", 0)},
    ]
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "rows": rows,
        "funnel": funnel,
    }


def fetch_user_acquisition(
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
      COALESCE(default_channel_group, '(not set)') AS channel,
      SUM(new_users) AS new_users,
      SUM(users) AS active_users,
      SUM(key_events) AS key_events,
      ROUND(SAFE_DIVIDE(SUM(key_events), NULLIF(SUM(total_users), 0)) * 100, 1) AS key_event_rate
    FROM {_user_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY channel
    ORDER BY new_users DESC
    LIMIT 15
    """
    source_sql = f"""
    SELECT
      COALESCE(source, '(direct)') AS source,
      COALESCE(medium, '(none)') AS medium,
      SUM(new_users) AS new_users,
      SUM(key_events) AS key_events,
      ROUND(SAFE_DIVIDE(SUM(key_events), NULLIF(SUM(total_users), 0)) * 100, 1) AS key_event_rate
    FROM {_user_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY source, medium
    ORDER BY new_users DESC
    LIMIT 30
    """
    by_channel = _run_query(channel_sql, params=params, max_rows=15)
    by_source = _run_query(source_sql, params=params, max_rows=30)
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_channel": by_channel,
        "by_source": by_source,
    }


def fetch_demographics(
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
      SUM(active_users) AS users,
      SUM(sessions) AS sessions,
      SUM(key_events) AS key_events
    FROM {_geo_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND city IS NOT NULL AND city != '(not set)'
    GROUP BY city, region, country
    ORDER BY users DESC
    LIMIT 20
    """
    age_sql = f"""
    SELECT
      COALESCE(user_age_bracket, 'unknown') AS age_bracket,
      SUM(active_users) AS users,
      SUM(key_events) AS key_events
    FROM {_demographics_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND user_age_bracket IS NOT NULL AND user_age_bracket != '(not set)'
    GROUP BY age_bracket
    ORDER BY
      CASE age_bracket
        WHEN '18-24' THEN 1 WHEN '25-34' THEN 2 WHEN '35-44' THEN 3
        WHEN '45-54' THEN 4 WHEN '55-64' THEN 5 WHEN '65+' THEN 6
        ELSE 7 END
    """
    gender_sql = f"""
    SELECT
      COALESCE(user_gender, 'unknown') AS gender,
      SUM(active_users) AS users,
      SUM(key_events) AS key_events
    FROM {_demographics_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND user_gender IS NOT NULL AND user_gender NOT IN ('(not set)', 'unknown')
    GROUP BY gender
    ORDER BY users DESC
    """
    # State-level rollup for the demographics map. Aggregated straight from the
    # geo table (not summed from the top-20 cities above) so the map reflects
    # every user's region, not just the largest cities.
    region_sql = f"""
    SELECT
      region,
      SUM(active_users) AS users,
      SUM(sessions) AS sessions,
      SUM(key_events) AS key_events
    FROM {_geo_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND region IS NOT NULL AND region != '' AND region != '(not set)'
    GROUP BY region
    ORDER BY users DESC
    LIMIT 70
    """
    by_city = _run_query(city_sql, params=params, max_rows=20)
    by_age = _run_query(age_sql, params=params, max_rows=10)
    by_gender = _run_query(gender_sql, params=params, max_rows=5)
    by_region = _run_query(region_sql, params=params, max_rows=70)
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_city": by_city,
        "by_age": by_age,
        "by_gender": by_gender,
        "by_region": by_region,
    }
