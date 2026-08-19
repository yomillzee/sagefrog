from __future__ import annotations

import contextlib
import contextvars
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

import bigquery_service

_log = logging.getLogger(__name__)

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


def default_destination() -> tuple[str, str]:
    """Nixon's built-in marketing-mart (project_id, dataset_id).

    Nixon predates DB-stored BigQuery routing, so its mart destination lives
    here as the module defaults rather than in its client_dashboard_config row.
    Exposed so other read paths (e.g. the HQ budget overview) can resolve
    Nixon's spend the same way the dashboard does, instead of skipping it for
    having no project on its config row.
    """
    return _DEFAULT_PROJECT_ID, _DEFAULT_DATASET_ID


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


def _google_ads_demographic_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.explorer_google_ads_demographic_daily`"


def _microsoft_ads_explorer_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.explorer_microsoft_ads_daily`"


def _linkedin_creative_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_linkedin_ads_creative_daily`"


def _linkedin_campaign_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_linkedin_ads_campaign_daily`"


def _linkedin_demographics_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_linkedin_ads_demographics`"


def _meta_ad_table() -> str:
    return f"`{_project_id()}.{_dataset_id()}.fact_meta_ads_ad_daily`"


def _ga4_paid_entity_table() -> str:
    # GA4 paid-entity mart lives in raw_ga4 (same project), not the marts dataset.
    return f"`{_project_id()}.raw_ga4.ga4_paid_entity_daily`"


def _ga4_paid_key_event_table() -> str:
    return f"`{_project_id()}.raw_ga4.ga4_paid_key_event_daily`"


def _ga4_google_entity_table() -> str:
    return f"`{_project_id()}.raw_ga4.ga4_google_entity_daily`"


def _ga4_google_key_event_table() -> str:
    return f"`{_project_id()}.raw_ga4.ga4_google_key_event_daily`"


def _ga4_linkedin_key_event_table() -> str:
    return f"`{_project_id()}.raw_ga4.ga4_linkedin_key_event_daily`"


def _normalize_li_name(name: str) -> str:
    """Normalize a LinkedIn campaign-group name for name-based matching.

    LinkedIn has no ids, so verified conversions match by name — normalize away
    the usual drift (case, URL-encoded '+' for spaces, repeated whitespace).
    """
    import re
    s = (name or "").replace("+", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


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


def _geo_page_table() -> str:
    """Geography per page — the only geo source that carries a page_path, so the
    only one a page-path scope can be applied to. Optional: it appears after the
    first GA4 sync that includes the geo_page report."""
    return f"`{_project_id()}.{_dataset_id()}.vw_ga4_geo_page_daily`"


def parse_page_path_filter(text: str | None) -> list[str]:
    """Split an admin's Website Analytics page-path scope into patterns.

    One pattern per line (case-insensitive substring, matched against page paths
    by the page-path fetch functions). Blank lines and ``#`` comments are
    dropped. Returns an empty list when nothing is configured, in which case the
    analytics view shows the whole site.
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _page_path_filter_clause(
    patterns: list[str] | None,
    *,
    column: str,
    params: dict[str, bigquery.ScalarQueryParameter],
) -> str:
    """Build a `` AND (…)`` scope clause for a page-path column and register its
    query params. Each pattern is a case-insensitive substring (CONTAINS_SUBSTR,
    which treats the value as a literal — no wildcard/injection surface); a row
    is kept when *any* pattern matches. Returns '' when there are no patterns."""
    cleaned = [p.strip() for p in (patterns or []) if p and p.strip()]
    if not cleaned:
        return ""
    conds = []
    for i, pat in enumerate(cleaned):
        key = f"pp{i}"
        params[key] = bigquery.ScalarQueryParameter(key, "STRING", pat)
        conds.append(f"CONTAINS_SUBSTR({column}, @{key})")
    return " AND (" + " OR ".join(conds) + ")"


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


def _is_missing_table(exc: Exception) -> bool:
    """True when a query failed because the table/view doesn't exist.

    Optional mart views (geo_page, demographics, tech) only appear after a sync
    provisions them, so a panel that reads one has to tell "not provisioned yet"
    apart from a real query failure. BigQuery reports it as a 404 NotFound whose
    message starts "Not found: Table ..."; match on that rather than swallowing
    every exception.
    """
    if getattr(exc, "code", None) == 404:
        return True
    return "not found: table" in str(exc).lower()


def _is_missing_column(exc: Exception) -> bool:
    """True when a query failed because a column the SQL selects isn't there.

    Mart views are rebuilt by each client's connector sync, so a view can lag a
    deploy that added columns to it by up to one sync cycle. A read that wants
    the new columns uses this to fall back to the old projection instead of
    500-ing the panel; the client self-heals on its next sync. BigQuery reports
    it as a 400 "Unrecognized name: x" or "Name x not found inside y".
    """
    msg = str(exc).lower()
    return "unrecognized name" in msg or "not found inside" in msg


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
      SUM(COALESCE(conversions, 0)) AS conversions,
      ROUND(SUM(COALESCE(conversion_value, 0)), 2) AS conversion_value
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


def fetch_microsoft_explorer(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Microsoft Ads explorer (campaign → ad group → ad).

    One row per ad (with the served ad copy) when ad-level data has synced, so
    the dashboard can drill campaign → ad group → ad like Google text ads. Before
    ad data exists, the explorer view falls back to campaign grain (ad columns
    NULL) and this returns one row per campaign. Returns empty rows (not an error)
    when the view doesn't exist yet.
    """
    sql = f"""
    SELECT
      campaign_id,
      campaign_name,
      ad_group_id,
      ad_group_name,
      ad_id,
      ANY_VALUE(ad_type) AS ad_type,
      ANY_VALUE(ad_title) AS ad_title,
      ANY_VALUE(title_part_1) AS title_part_1,
      ANY_VALUE(title_part_2) AS title_part_2,
      ANY_VALUE(title_part_3) AS title_part_3,
      ANY_VALUE(description_1) AS description_1,
      ANY_VALUE(description_2) AS description_2,
      ANY_VALUE(headlines) AS headlines,
      ANY_VALUE(descriptions) AS descriptions,
      ANY_VALUE(path_1) AS path_1,
      ANY_VALUE(path_2) AS path_2,
      ANY_VALUE(display_url) AS display_url,
      ANY_VALUE(final_url) AS final_url,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value
    FROM {_microsoft_ads_explorer_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY campaign_id, campaign_name, ad_group_id, ad_group_name, ad_id
    ORDER BY spend DESC
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=20000,
        )
    except Exception:
        # No explorer view yet (no sync has run) — degrade to empty so the
        # Campaign Explorer simply omits Microsoft rather than erroring.
        rows = []
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


def pick_demographics_window(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
    window: str | None = None,
) -> str:
    """Choose which synced window to serve for a requested date range.

    Demographic rows are per-window totals that cannot be re-cut to an arbitrary
    range (see linkedin_service.fetch_ads_demographics), so the panel shows a
    whole synced window and says which one. An explicit ``window`` wins when it
    was actually synced; otherwise pick the window whose span is closest to the
    range the user has selected, so a 7-day view lands on the 30-day window
    rather than the 90.
    """
    spans: dict[str, int] = {}
    for row in rows:
        key = str(row.get("window_key") or "").strip()
        if not key or key in spans:
            continue
        w_start, w_end = row.get("window_start"), row.get("window_end")
        try:
            spans[key] = (date.fromisoformat(str(w_end)) - date.fromisoformat(str(w_start))).days + 1
        except (TypeError, ValueError):
            spans[key] = 0
    if not spans:
        return ""
    requested = (window or "").strip().upper()
    if requested in spans:
        return requested
    target = (end_date - start_date).days + 1
    return min(spans, key=lambda k: (abs(spans[k] - target), k))


def fetch_linkedin_demographics(
    *,
    start_date: date,
    end_date: date,
    window: str | None = None,
    top_limit: int = 25,
) -> dict[str, Any]:
    """LinkedIn member demographics — who saw and clicked the ads.

    Reads fact_linkedin_ads_demographics, which stores per-window totals rather
    than a daily series: LinkedIn's MEMBER_* pivots have no date dimension and
    suppress small categories, so these numbers cannot be summed across days or
    sliced to an arbitrary range. The endpoint therefore serves one whole synced
    window — the one closest to the caller's range — and reports which window
    that was so the panel can label itself honestly instead of implying it
    follows the date picker.

    Returns rows grouped by dimension (company / job title / function /
    seniority / industry / company size), each capped to ``top_limit``
    categories by impressions. A client that has never synced demographics (or
    whose LinkedIn permissions don't cover the pivots) gets an empty result, not
    a 500 — the panel hides itself.
    """
    sql = f"""
    SELECT
      window_key, window_start, window_end, dimension,
      category, category_urn, impressions, clicks, spend, conversions, ctr
    FROM {_linkedin_demographics_table()}
    ORDER BY window_key, dimension, impressions DESC
    """
    try:
        rows = _run_query(sql, params={}, max_rows=20000)
    except Exception:
        rows = []

    chosen = pick_demographics_window(
        rows, start_date=start_date, end_date=end_date, window=window
    )
    live = [r for r in rows if str(r.get("window_key") or "") == chosen] if chosen else []

    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for row in live:
        dim = str(row.get("dimension") or "").strip()
        if not dim:
            continue
        bucket = by_dimension.setdefault(dim, [])
        if len(bucket) >= top_limit:
            continue
        bucket.append(row)

    window_start = live[0].get("window_start") if live else None
    window_end = live[0].get("window_end") if live else None
    return {
        "client": _client_key(),
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "window": chosen,
        "window_start": str(window_start) if window_start else None,
        "window_end": str(window_end) if window_end else None,
        "windows_available": sorted({str(r.get("window_key") or "") for r in rows if r.get("window_key")}),
        "row_count": len(live),
        "by_dimension": by_dimension,
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


# ---- Google Ads demographic segments --------------------------------------
#
# Thresholds for the "consider excluding this segment" call. They exist to stop
# the panel making a recommendation off noise: a segment has to have both spent
# real money in absolute terms *and* had a fair shot at converting relative to
# what the rest of the account pays per conversion.
GOOGLE_DEMOGRAPHIC_MIN_SPEND = 50.0
# Zero conversions only becomes evidence once the segment has burned this
# multiple of the dimension's own cost per conversion.
GOOGLE_DEMOGRAPHIC_WASTE_CPA_MULTIPLE = 2.0
# A segment that does convert, but this much worse than the dimension average.
GOOGLE_DEMOGRAPHIC_HIGH_CPA_MULTIPLE = 1.5


_UNDETERMINED_SEGMENTS = frozenset({"AGE_RANGE_UNDETERMINED", "UNDETERMINED", "UNKNOWN"})


def _segment_is_undetermined(segment_value: Any) -> bool:
    return str(segment_value or "").strip().upper() in _UNDETERMINED_SEGMENTS


def assess_demographic_segments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group aggregated demographic rows by dimension and flag wasted spend.

    Pure function over already-aggregated rows so the judgement calls are
    testable without BigQuery. For each dimension it derives a benchmark cost
    per conversion from the segments Google *could* classify, then flags:

      * ``no_conversions`` — spent past the threshold with nothing to show. This
        is the "consider excluding males / under-25s" call.
      * ``high_cpa`` — converts, but materially worse than the dimension average.

    Three things are deliberately never flagged: the Unknown bucket (excluding
    it is not something Google lets you do, and it is often the biggest bucket),
    segments already excluded in every ad group they appear in, and anything at
    all when the dimension has no conversions to benchmark against — with no
    benchmark, "no conversions here" says nothing about this segment.
    """
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        dim = str(row.get("dimension") or "").strip()
        if dim:
            by_dimension.setdefault(dim, []).append(dict(row))

    out: dict[str, Any] = {}
    for dim, segments in by_dimension.items():
        total_spend = sum(float(s.get("spend") or 0.0) for s in segments)
        total_conversions = sum(float(s.get("conversions") or 0.0) for s in segments)

        known = [s for s in segments if not _segment_is_undetermined(s.get("segment_value"))]
        known_spend = sum(float(s.get("spend") or 0.0) for s in known)
        known_conversions = sum(float(s.get("conversions") or 0.0) for s in known)
        benchmark_cpa = (known_spend / known_conversions) if known_conversions > 0 else None

        undetermined_spend = total_spend - known_spend

        for seg in segments:
            spend = float(seg.get("spend") or 0.0)
            conversions = float(seg.get("conversions") or 0.0)
            clicks = int(seg.get("clicks") or 0)
            impressions = int(seg.get("impressions") or 0)

            seg["cpa"] = round(spend / conversions, 2) if conversions > 0 else None
            seg["ctr"] = round(clicks / impressions * 100, 2) if impressions > 0 else None
            seg["avg_cpc"] = round(spend / clicks, 2) if clicks > 0 else None
            seg["conv_rate"] = round(conversions / clicks * 100, 2) if clicks > 0 else None
            seg["spend_share"] = round(spend / total_spend * 100, 1) if total_spend > 0 else None
            seg["conversion_share"] = (
                round(conversions / total_conversions * 100, 1) if total_conversions > 0 else None
            )

            seg["recommendation"] = _demographic_recommendation(
                seg, benchmark_cpa=benchmark_cpa, dimension=dim
            )

        segments.sort(key=lambda s: float(s.get("spend") or 0.0), reverse=True)
        out[dim] = {
            "segments": segments,
            "spend": round(total_spend, 2),
            "conversions": round(total_conversions, 2),
            "benchmark_cpa": round(benchmark_cpa, 2) if benchmark_cpa else None,
            # How much of this dimension's spend Google could not attribute to a
            # segment. High values (Search commonly runs past 50%) mean the
            # panel is reasoning about a minority of the money, and it says so.
            "undetermined_spend_share": (
                round(undetermined_spend / total_spend * 100, 1) if total_spend > 0 else None
            ),
            "recommendation_count": sum(1 for s in segments if s.get("recommendation")),
        }
    return out


def _demographic_recommendation(
    seg: dict[str, Any], *, benchmark_cpa: float | None, dimension: str
) -> dict[str, Any] | None:
    if benchmark_cpa is None or benchmark_cpa <= 0:
        return None
    if _segment_is_undetermined(seg.get("segment_value")):
        return None
    # Excluded in every ad group it runs in — the action has already been taken.
    if seg.get("excluded_everywhere"):
        return None

    spend = float(seg.get("spend") or 0.0)
    conversions = float(seg.get("conversions") or 0.0)
    label = str(seg.get("segment_label") or seg.get("segment_value") or "").strip()
    noun = "age group" if dimension == "age_range" else "gender"

    if spend < GOOGLE_DEMOGRAPHIC_MIN_SPEND:
        return None

    if conversions <= 0:
        if spend < benchmark_cpa * GOOGLE_DEMOGRAPHIC_WASTE_CPA_MULTIPLE:
            return None
        return {
            "kind": "no_conversions",
            "severity": "high",
            "headline": f"Consider excluding {label}",
            "detail": (
                f"{label} has spent {spend:,.2f} with no conversions — over "
                f"{GOOGLE_DEMOGRAPHIC_WASTE_CPA_MULTIPLE:g}× the "
                f"{benchmark_cpa:,.2f} this account normally pays for one."
            ),
        }

    cpa = spend / conversions
    if cpa > benchmark_cpa * GOOGLE_DEMOGRAPHIC_HIGH_CPA_MULTIPLE:
        return {
            "kind": "high_cpa",
            "severity": "medium",
            "headline": f"{label} converts expensively",
            "detail": (
                f"{cpa:,.2f} per conversion against a {noun} average of "
                f"{benchmark_cpa:,.2f}. Worth a bid adjustment before an exclusion."
            ),
        }
    return None


def fetch_google_ads_demographics(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Google Ads age / gender segments — spend and conversions per segment.

    Reads explorer_google_ads_demographic_daily, which only exists once a client
    has synced with the demographic reports; a missing table is treated as "no
    demographic data" (empty result) so the panel hides rather than 500s.

    Note the coverage caveat this data carries: Performance Max contributes
    nothing (it has no ad-group criteria) and Search only reports what Google
    can infer, so these totals are a subset of account spend — never reconcile
    them against the campaign numbers.
    """
    sql = f"""
    SELECT
      dimension,
      segment_value,
      ANY_VALUE(segment_label) AS segment_label,
      ROUND(SUM(spend), 2) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      ROUND(SUM(conversion_value), 2) AS conversion_value,
      COUNT(DISTINCT ad_group_id) AS ad_groups,
      COUNT(DISTINCT IF(is_excluded, ad_group_id, NULL)) AS excluded_ad_groups,
      LOGICAL_AND(IFNULL(is_excluded, FALSE)) AS excluded_everywhere
    FROM {_google_ads_demographic_table()}
    WHERE date BETWEEN @start_date AND @end_date
    GROUP BY dimension, segment_value
    ORDER BY dimension, spend DESC
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=2000,
        )
    except Exception:
        rows = []

    by_dimension = assess_demographic_segments(rows)
    return {
        "client": _client_key(),
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "row_count": len(rows),
        "by_dimension": by_dimension,
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


def fetch_meta_verified_conversions(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """GA4-verified conversions (key events) per Meta ad id, for the explorer.

    Keyed on manual_ad_content (utm_content = Meta ad id) so the Campaign
    Explorer can match by ad id and roll verified conversions up the
    campaign/ad set/ad tree. Returns {ad_id: key_events}. If the mart has not
    been synced yet the table is absent — return an empty map so the explorer
    falls back to dashes rather than erroring.
    """
    sql = f"""
    SELECT manual_ad_content AS ad_id, SUM(key_events) AS key_events
    FROM {_ga4_paid_entity_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND manual_ad_content NOT IN ('', '(not set)', '(other)')
    GROUP BY manual_ad_content
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=50000,
        )
    except Exception as exc:
        if "not found" in str(exc).lower() or "was not found" in str(exc).lower():
            rows = []
        else:
            raise
    by_ad_id = {
        str(r.get("ad_id")): int(r.get("key_events") or 0)
        for r in rows if r.get("ad_id")
    }
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(by_ad_id),
        "by_ad_id": by_ad_id,
    }


def fetch_meta_verified_key_events(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """GA4-verified conversions split by key-event name, per Meta ad id.

    Powers the per-key-event selector on the explorer's Verified conv. column:
    returns {ad_id: {event_name: key_events}} plus the sorted list of key events
    present. Missing table (not synced yet) yields empty maps so the selector
    simply offers "All key events" and the column falls back to the blended total.
    """
    sql = f"""
    SELECT manual_ad_content AS ad_id, event_name, SUM(key_events) AS key_events
    FROM {_ga4_paid_key_event_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND manual_ad_content NOT IN ('', '(not set)', '(other)')
    GROUP BY manual_ad_content, event_name
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=100000,
        )
    except Exception as exc:
        if "not found" in str(exc).lower():
            rows = []
        else:
            raise
    by_ad_id_event: dict[str, dict[str, int]] = {}
    events: dict[str, int] = {}
    for r in rows:
        ad_id = str(r.get("ad_id") or "")
        event = str(r.get("event_name") or "")
        ke = int(r.get("key_events") or 0)
        if not ad_id or not event or ke <= 0:
            continue
        by_ad_id_event.setdefault(ad_id, {})[event] = by_ad_id_event.get(ad_id, {}).get(event, 0) + ke
        events[event] = events.get(event, 0) + ke
    # Order events by total volume so the most common conversion sits on top.
    ordered_events = [e for e, _ in sorted(events.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "events": ordered_events,
        "by_ad_id_event": by_ad_id_event,
    }


def fetch_google_verified_conversions(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """GA4-verified conversions (key events) per Google Ads campaign id.

    Sourced from GA4's native Google Ads link (no URL tagging). Campaign grain,
    matched to explorer_google_ads_daily by campaign id. Missing table (not
    synced yet) yields an empty map so the explorer falls back to dashes.
    """
    sql = f"""
    SELECT campaign_id, SUM(key_events) AS key_events
    FROM {_ga4_google_entity_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND campaign_id NOT IN ('', '(not set)')
    GROUP BY campaign_id
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=50000,
        )
    except Exception as exc:
        if "not found" in str(exc).lower():
            rows = []
        else:
            raise
    by_campaign_id = {
        str(r.get("campaign_id")): int(r.get("key_events") or 0)
        for r in rows if r.get("campaign_id")
    }
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "row_count": len(by_campaign_id),
        "by_campaign_id": by_campaign_id,
    }


def fetch_google_verified_key_events(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """GA4-verified Google conversions split by key-event name, per campaign id.

    Powers the per-key-event selector for Google campaigns. Returns
    {campaign_id: {event_name: key_events}} plus the sorted list of key events.
    Missing table (not synced yet) yields empty maps.
    """
    sql = f"""
    SELECT campaign_id, event_name, SUM(key_events) AS key_events
    FROM {_ga4_google_key_event_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND campaign_id NOT IN ('', '(not set)')
    GROUP BY campaign_id, event_name
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=100000,
        )
    except Exception as exc:
        if "not found" in str(exc).lower():
            rows = []
        else:
            raise
    by_campaign_id_event: dict[str, dict[str, int]] = {}
    events: dict[str, int] = {}
    for r in rows:
        cid = str(r.get("campaign_id") or "")
        event = str(r.get("event_name") or "")
        ke = int(r.get("key_events") or 0)
        if not cid or not event or ke <= 0:
            continue
        by_campaign_id_event.setdefault(cid, {})[event] = by_campaign_id_event.get(cid, {}).get(event, 0) + ke
        events[event] = events.get(event, 0) + ke
    ordered_events = [e for e, _ in sorted(events.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "events": ordered_events,
        "by_campaign_id_event": by_campaign_id_event,
    }


def fetch_linkedin_verified_key_events(
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """GA4-verified LinkedIn conversions split by key event, per campaign group.

    LinkedIn has no ids in GA4, so this matches by normalized campaign-group
    name (utm_campaign). Returns {norm_group_name: {event_name: key_events}},
    the derived {norm_group_name: total}, and the event list. Missing table
    (not synced) yields empty maps.
    """
    sql = f"""
    SELECT campaign_name, event_name, SUM(key_events) AS key_events
    FROM {_ga4_linkedin_key_event_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND campaign_name NOT IN ('', '(not set)')
    GROUP BY campaign_name, event_name
    """
    try:
        rows = _run_query(
            sql,
            params={
                "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            },
            max_rows=100000,
        )
    except Exception as exc:
        if "not found" in str(exc).lower():
            rows = []
        else:
            raise
    by_group_event: dict[str, dict[str, int]] = {}
    by_group: dict[str, int] = {}
    events: dict[str, int] = {}
    for r in rows:
        grp = _normalize_li_name(str(r.get("campaign_name") or ""))
        event = str(r.get("event_name") or "")
        ke = int(r.get("key_events") or 0)
        if not grp or not event or ke <= 0:
            continue
        by_group_event.setdefault(grp, {})[event] = by_group_event.get(grp, {}).get(event, 0) + ke
        by_group[grp] = by_group.get(grp, 0) + ke
        events[event] = events.get(event, 0) + ke
    ordered_events = [e for e, _ in sorted(events.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "events": ordered_events,
        "by_group_name": by_group,
        "by_group_name_event": by_group_event,
    }


def fetch_pages_top(
    *,
    start_date: date,
    end_date: date,
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Top pages (all traffic) from vw_page_path_daily, aggregated per page."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="page_path", params=params)
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
    WHERE date BETWEEN @start_date AND @end_date{scope}
    GROUP BY page_path
    ORDER BY page_views DESC
    """
    rows = _run_query(
        sql,
        params=params,
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
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """GA4 traffic acquisition: a daily sessions series plus channel and
    source/medium breakdowns.

    ``page_path_filter`` scopes the **daily** series only. vw_ga4_traffic_acq_daily
    is session-grained with no page_path, so the page-scoped series is read from
    vw_page_path_daily instead (date x page_path, carrying sessions and
    engaged_sessions). Note that view counts a session once per page it viewed,
    so summing across several matching paths counts a session that viewed more
    than one of them more than once — the same semantics as GA4's own per-page
    Sessions metric. The channel and source/medium breakdowns stay site-wide:
    neither is available at page grain (vw_page_path_source_daily carries no
    default_channel_group), and the Traffic panel is hidden while a scope is set.
    """
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
    # Daily series: page-scoped from vw_page_path_daily when a scope is set,
    # otherwise the site-wide session-grained view. Both yield the same shape
    # ({date, sessions, engaged_sessions}) so the trend chart is unchanged.
    daily_params = dict(params)
    daily_scope = _page_path_filter_clause(
        page_path_filter, column="page_path", params=daily_params
    )
    daily_sql = f"""
    SELECT
      CAST(date AS STRING) AS date,
      SUM(sessions) AS sessions,
      SUM(engaged_sessions) AS engaged_sessions
    FROM {_page_path_daily_table() if daily_scope else _traffic_acq_table()}
    WHERE date BETWEEN @start_date AND @end_date{daily_scope}
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
        "daily": _run_query(daily_sql, params=daily_params, max_rows=2000),
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


def fetch_session_duration(
    *,
    start_date: date,
    end_date: date,
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Site-wide average session duration for a date range — one lean query.

    GA4 only reports averageSessionDuration alongside a dimension, and the
    landing-page report is the one that carries it at session grain: every
    session has exactly one landing page, so re-weighting the per-landing-page
    averages by their session counts rebuilds the site-wide average rather than
    averaging averages (which would let a 1-session page weigh as much as a
    500-session one).

    ``page_path_filter`` scopes it the same way the landing-page table is
    scoped — sessions that *started* on a matching page.
    """
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="landing_page", params=params)
    sql = f"""
    SELECT
      SUM(sessions) AS sessions,
      ROUND(SAFE_DIVIDE(
        SUM(average_session_duration * sessions),
        NULLIF(SUM(sessions), 0)
      ), 1) AS avg_session_duration_seconds
    FROM {_landing_page_table()}
    WHERE date BETWEEN @start_date AND @end_date{scope}
    """
    rows = _run_query(sql, params=params, max_rows=1)
    row = rows[0] if rows else {}
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "sessions": row.get("sessions"),
        "avg_session_duration_seconds": row.get("avg_session_duration_seconds"),
    }


def fetch_landing_pages(
    *,
    start_date: date,
    end_date: date,
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="landing_page", params=params)
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
    WHERE date BETWEEN @start_date AND @end_date{scope}
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
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Per landing_page × event breakdown so the dashboard can recompute the
    landing-page 'key events' column for a client-selected set of events.
    `events` lists every event with its total + GA4 key-event count (the
    default selection is the events where key_events > 0)."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="landing_page", params=params)
    rows_sql = f"""
    SELECT
      COALESCE(landing_page, '/') AS page_path,
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {_landing_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date{scope}
    GROUP BY page_path, event_name
    """
    events_sql = f"""
    SELECT
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {_landing_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date{scope}
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
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Per page_path × event breakdown across ALL traffic (not just entrances)
    so the Top Pages panel can recompute 'key events' for a client-selected
    set of events, same as Landing/Traffic/User acquisition."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="page_path", params=params)
    rows_sql = f"""
    SELECT
      COALESCE(page_path, '/') AS page_path,
      event_name,
      SUM(event_count) AS event_count,
      SUM(key_events)  AS key_events
    FROM {_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date{scope}
    GROUP BY page_path, event_name
    """
    events_sql = f"""
    SELECT event_name, SUM(event_count) AS event_count, SUM(key_events) AS key_events
    FROM {_page_events_table()}
    WHERE date BETWEEN @start_date AND @end_date{scope}
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
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Per-page traffic broken out by source / AI referral from
    vw_page_path_source_daily, for filtering the top-pages list."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="page_path", params=params)
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
    WHERE date BETWEEN @start_date AND @end_date{scope}
    GROUP BY page_path, source_platform, is_ai_referral, ai_platform, utm_campaign
    ORDER BY page_views DESC
    """
    rows = _run_query(
        sql,
        params=params,
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
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Daily AI-referral sessions by ai_platform, for the AI Traffic trend chart.

    One row per (date, ai_platform); the range aggregate lives in
    fetch_pages_sources. Reads vw_page_path_source_daily, which tags AI
    assistant sessions (is_ai_referral) with the referring platform."""
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    scope = _page_path_filter_clause(page_path_filter, column="page_path", params=params)
    sql = f"""
    SELECT
      date,
      ai_platform,
      SUM(sessions) AS sessions
    FROM {_page_path_source_daily_table()}
    WHERE date BETWEEN @start_date AND @end_date
      AND is_ai_referral{scope}
    GROUP BY date, ai_platform
    ORDER BY date
    """
    rows = _run_query(
        sql,
        params=params,
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
    page_path_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Geographic and demographic breakdown from ga4_DemographicDetails.

    ``page_path_filter`` scopes the **geography** half (map + cities) to users
    who viewed a matching page, reading vw_ga4_geo_page_daily instead of the
    site-wide vw_ga4_geo_daily. Age and gender are user-scoped in GA4 with no
    page dimension to scope by, so they come back empty under a scope and the
    panel hides them (``user_scoped_available``).

    Scoped user counts are per-page: the source is aggregated by page, so a user
    who viewed two matching pages contributes to both rows and the totals read
    high. The panel says so; treat the scoped map as relative concentration.

    The scoped source is optional — it only exists once a GA4 sync has run since
    the geo_page report was added. When it is missing the geography rows come
    back empty with ``geo_scope_available: False`` rather than silently falling
    back to site-wide numbers, which would read as page-scoped and be wrong.
    """
    patterns = [p.strip() for p in (page_path_filter or []) if p and p.strip()]
    scoped = bool(patterns)
    params = {
        "start_date": bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        "end_date": bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    }
    geo_source = _geo_page_table() if scoped else _geo_table()
    geo_scope = _page_path_filter_clause(patterns, column="page_path", params=params)
    # engaged_sessions / new_users were added to the geo reports after they
    # shipped, and a client's mart views are only rebuilt by their GA4 sync — so
    # between a deploy and that sync the view still has the old column set.
    # _geo_metrics(enriched=False) is the projection that works against it; the
    # caller falls back to it on "unrecognized name" and the client self-heals on
    # its next sync. Engagement rate is derived from the summed counts, never
    # averaged out of per-day rates (a 1-session day would weigh as much as a
    # 500-session one) — the same shape the traffic-acquisition mart uses.
    def _geo_metrics(enriched: bool) -> str:
        if not enriched:
            return """
      SUM(active_users) AS users,
      SUM(sessions) AS sessions,
      SUM(key_events) AS key_events"""
        return """
      SUM(active_users) AS users,
      SUM(new_users) AS new_users,
      SUM(sessions) AS sessions,
      SUM(engaged_sessions) AS engaged_sessions,
      ROUND(SAFE_DIVIDE(SUM(engaged_sessions), NULLIF(SUM(sessions), 0)) * 100, 1) AS engagement_rate,
      SUM(key_events) AS key_events"""

    def _city_sql(enriched: bool) -> str:
        return f"""
    SELECT
      COALESCE(city, '(not set)') AS city,
      COALESCE(region, '') AS region,
      COALESCE(country, '') AS country,{_geo_metrics(enriched)}
    FROM {geo_source}
    WHERE date BETWEEN @start_date AND @end_date
      AND city IS NOT NULL AND city != '(not set)'{geo_scope}
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
    def _region_sql(enriched: bool) -> str:
        return f"""
    SELECT
      region,{_geo_metrics(enriched)}
    FROM {geo_source}
    WHERE date BETWEEN @start_date AND @end_date
      AND region IS NOT NULL AND region != '' AND region != '(not set)'{geo_scope}
    GROUP BY region
    ORDER BY users DESC
    LIMIT 70
    """

    def _read_geo(enriched: bool) -> tuple[list, list]:
        return (
            _run_query(_city_sql(enriched), params=params, max_rows=20),
            _run_query(_region_sql(enriched), params=params, max_rows=70),
        )

    geo_available = True
    try:
        by_city, by_region = _read_geo(True)
    except Exception as exc:
        if _is_missing_column(exc):
            # Mart view predates the engagement/new-user columns — read what it
            # has. Rebuilt on this client's next GA4 sync.
            _log.info("geo view missing engagement columns [%s]", _client_key())
            by_city, by_region = _read_geo(False)
        elif scoped and _is_missing_table(exc):
            # Only the scoped read can hit a view that doesn't exist yet (it lands
            # on the first GA4 sync after the geo_page report shipped). Degrade to
            # "no scoped geography"; anything else is a real failure.
            _log.info("scoped geo unavailable [%s]: %s", _client_key(), str(exc)[:200])
            by_city, by_region, geo_available = [], [], False
        else:
            raise
    # Age/gender are user-scoped: GA4 has no page dimension to pair them with, so
    # a scope can only hide them, never narrow them.
    by_age = [] if scoped else _run_query(age_sql, params=params, max_rows=10)
    by_gender = [] if scoped else _run_query(gender_sql, params=params, max_rows=5)
    return {
        "client": _client_key(),
        "date_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "by_city": by_city,
        "by_age": by_age,
        "by_gender": by_gender,
        "by_region": by_region,
        "page_path_filter": patterns,
        "scoped": scoped,
        "geo_scope_available": geo_available,
        "user_scoped_available": not scoped,
    }
