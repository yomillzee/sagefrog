"""GA4 marketing mart — canonical view DDL, provisioning, and health checks.

This module is the SINGLE SOURCE OF TRUTH for the GA4 presentation layer that
every client dashboard reads. Running ``provision_ga4_mart_views`` against a
client reproduces the exact same UDF + views in that client's marketing_marts
dataset, so onboarding a new client is one idempotent call.

Layer (all CREATE OR REPLACE — stateless, safe to re-run):

  fn_ga4_source_platform(UDF)             classifies default_channel_group/source/
                                          medium → paid_google / paid_linkedin /
                                          organic_search / ai_assistant / …
   ├─ vw_ga4_traffic_acquisition_daily    ← raw_ga4.ga4_sessions_daily   [intermediate]
   │   ├─ vw_ga4_traffic_acq_daily
   │   └─ vw_ga4_user_acq_daily
   ├─ vw_ga4_events_daily                 ← raw_ga4.ga4_events_daily
   └─ vw_page_path_source_daily           ← raw_ga4.ga4_page_source_daily
        └─ vw_page_path_daily
  vw_ga4_landing_pages_daily              ← raw_ga4.ga4_landing_pages_daily
  vw_ga4_demographics_daily               ← raw_ga4.ga4_demographics_daily   [optional]
  vw_ga4_geo_daily                        ← raw_ga4.ga4_geo_daily            [optional]
  vw_ga4_geo_page_daily                   ← raw_ga4.ga4_geo_page_daily       [optional]
  vw_ga4_tech_daily                       empty placeholder stub             [optional]

Page taxonomy (page_group / page_topic / is_core_website_page) is the only
client-specific piece — it lives in PAGE_TAXONOMY keyed by client_key, with a
generic default so a brand-new client gets sensible groupings out of the box.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_RAW_DATASET = "raw_ga4"

_UDF_NAME = "fn_ga4_source_platform"

# Views in dependency order. The UDF is provisioned before any of these; the
# intermediate views (traffic_acquisition, page_path_source) come before their
# dependents.
MART_VIEWS = [
    "vw_ga4_traffic_acquisition_daily",  # intermediate (UDF) — must precede acq views
    "vw_ga4_traffic_acq_daily",
    "vw_ga4_user_acq_daily",
    "vw_ga4_events_daily",
    "vw_ga4_landing_pages_daily",
    "vw_ga4_landing_page_events_daily",
    "vw_ga4_user_acq_events_daily",
    "vw_ga4_page_events_daily",
    "vw_ga4_tech_daily",
    "vw_ga4_demographics_daily",
    "vw_ga4_geo_daily",
    "vw_ga4_geo_page_daily",
    "vw_page_path_source_daily",          # intermediate (UDF) — must precede page_path
    "vw_page_path_daily",
]

# Optional views — may have no underlying data (geo/demographics need extra GA4
# scopes; tech is a deliberate placeholder). Health check treats these as non-fatal.
OPTIONAL_VIEWS = {
    "vw_ga4_geo_daily", "vw_ga4_geo_page_daily", "vw_ga4_demographics_daily",
    "vw_ga4_tech_daily",
}


# ── Page taxonomy (the only client-specific DDL) ──────────────────────────────
#
# Each entry supplies three SQL expressions over the `page_path` column. The
# default is generic (homepage / first path segment); register a client key to
# override with a site-specific taxonomy. GROUP BY references the output aliases
# (page_group / page_topic / is_core_website_page), so only the SELECT bodies vary.

_DEFAULT_PAGE_TAXONOMY = {
    "page_group": """
      CASE
        WHEN page_path = '/' THEN 'homepage'
        ELSE COALESCE(NULLIF(SPLIT(page_path, '/')[SAFE_OFFSET(1)], ''), 'other')
      END""",
    "page_topic": "CAST(NULL AS STRING)",
    "is_core_website_page": "TRUE",
}

PAGE_TAXONOMY: dict[str, dict[str, str]] = {
    "nixon": {
        "page_group": """
      CASE
        WHEN STARTS_WITH(page_path, '/hs/preferences-center') THEN 'system_hubspot'
        WHEN STARTS_WITH(page_path, '/careers') THEN 'careers'
        WHEN STARTS_WITH(page_path, '/products') THEN 'products'
        WHEN STARTS_WITH(page_path, '/services') THEN 'services'
        WHEN STARTS_WITH(page_path, '/resources') THEN 'resources'
        WHEN STARTS_WITH(page_path, '/about') THEN 'about'
        WHEN STARTS_WITH(page_path, '/contact') THEN 'contact'
        WHEN page_path = '/' THEN 'homepage'
        ELSE 'other'
      END""",
        "page_topic": """
      CASE
        WHEN REGEXP_CONTAINS(LOWER(page_path), r'scrub') THEN 'scrubs'
        WHEN REGEXP_CONTAINS(LOWER(page_path), r'linen|laundry') THEN 'linens'
        WHEN REGEXP_CONTAINS(LOWER(page_path), r'apparel|gown|lab-coat|lab-coats') THEN 'apparel'
        ELSE 'other'
      END""",
        "is_core_website_page": """
      CASE
        WHEN STARTS_WITH(page_path, '/hs/preferences-center') THEN FALSE
        ELSE TRUE
      END""",
    },
}


def _taxonomy_for(client_key: str | None) -> dict[str, str]:
    return PAGE_TAXONOMY.get((client_key or "").strip().lower(), _DEFAULT_PAGE_TAXONOMY)


# ── DDL ───────────────────────────────────────────────────────────────────────

def _udf_sql(marts: str) -> str:
    """CREATE OR REPLACE for the source_platform classifier UDF."""
    return f"""
    CREATE OR REPLACE FUNCTION `{marts}.{_UDF_NAME}`(
      default_channel_group STRING, source STRING, medium STRING
    ) AS (
      CASE
        WHEN LOWER(COALESCE(default_channel_group, '')) = 'cross-network' THEN 'paid_google'
        WHEN LOWER(COALESCE(source, '')) = 'google'
          AND LOWER(COALESCE(medium, '')) IN ('cpc', 'ppc', 'paid') THEN 'paid_google'

        WHEN LOWER(COALESCE(source, '')) = 'bing'
          AND LOWER(COALESCE(medium, '')) IN ('cpc', 'ppc', 'paid') THEN 'paid_microsoft'

        WHEN LOWER(COALESCE(default_channel_group, '')) = 'paid social' THEN 'paid_social'
        WHEN LOWER(COALESCE(source, '')) = 'linkedin'
          AND LOWER(COALESCE(medium, '')) IN ('paid_social', 'cpc', 'paid', 'ppc') THEN 'paid_linkedin'
        WHEN LOWER(COALESCE(source, '')) IN ('facebook', 'instagram')
          AND LOWER(COALESCE(medium, '')) IN ('paid_social', 'cpc', 'paid', 'ppc') THEN 'paid_meta'

        WHEN LOWER(COALESCE(source, '')) IN ('google-my-business', 'google-business-profile') THEN 'organic_local'
        WHEN LOWER(COALESCE(default_channel_group, '')) = 'organic social' THEN 'organic_social'
        WHEN LOWER(COALESCE(medium, '')) = 'organic' THEN 'organic_search'
        WHEN LOWER(COALESCE(medium, '')) = 'email' THEN 'email'
        WHEN LOWER(COALESCE(source, '')) = '(direct)' THEN 'direct'

        WHEN LOWER(COALESCE(medium, '')) IN ('ai-assistant', 'ai_assistant') THEN 'ai_assistant'
        WHEN LOWER(COALESCE(source, '')) IN ('chatgpt.com', 'perplexity.ai', 'claude.ai', 'gemini.google.com') THEN 'ai_assistant'

        WHEN LOWER(COALESCE(medium, '')) = 'referral' THEN 'referral'
        WHEN LOWER(COALESCE(default_channel_group, '')) = 'unassigned' THEN 'unassigned'
        ELSE 'other'
      END
    )
    """


def _view_sql(view_name: str, *, raw: str, marts: str, taxonomy: dict[str, str]) -> str:
    """Return the SELECT body for the given mart view, parameterized by dataset.

    ``raw``   — fully-qualified raw_ga4 prefix, e.g. ``nixon-medical.raw_ga4``.
    ``marts`` — fully-qualified marts prefix, e.g. ``nixon-medical.marketing_marts``.
    """
    udf = f"`{marts}.{_UDF_NAME}`"

    if view_name == "vw_ga4_traffic_acquisition_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          session_default_channel_group AS default_channel_group,
          session_source AS source,
          session_medium AS medium,
          session_campaign_name AS campaign,
          {udf}(
            session_default_channel_group,
            session_source,
            session_medium
          ) AS source_platform,
          SUM(sessions) AS sessions,
          SUM(engaged_sessions) AS engaged_sessions,
          SUM(key_events) AS key_events,
          SUM(total_users) AS users,
          SUM(new_users) AS new_users,
          SUM(screen_page_views) AS page_views,
          SAFE_DIVIDE(SUM(engaged_sessions), SUM(sessions)) AS engagement_rate,
          1 - SAFE_DIVIDE(SUM(engaged_sessions), SUM(sessions)) AS bounce_rate
        FROM `{raw}.ga4_sessions_daily`
        GROUP BY
          client_key, property_id, date,
          default_channel_group, source, medium, campaign, source_platform
        """

    if view_name == "vw_ga4_traffic_acq_daily":
        return f"""
        SELECT
          client_key, property_id, date,
          default_channel_group, source, medium, campaign, source_platform,
          sessions, engaged_sessions, key_events,
          users, users AS total_users, new_users, page_views,
          engagement_rate, bounce_rate
        FROM `{marts}.vw_ga4_traffic_acquisition_daily`
        """

    if view_name == "vw_ga4_user_acq_daily":
        return f"""
        SELECT
          client_key, property_id, date,
          default_channel_group, source, medium, campaign, source_platform,
          users, users AS total_users, new_users,
          sessions, engaged_sessions, key_events, page_views,
          engagement_rate, bounce_rate
        FROM `{marts}.vw_ga4_traffic_acquisition_daily`
        """

    if view_name == "vw_ga4_events_daily":
        return f"""
        SELECT
          client_key, property_id, date, event_name,
          session_default_channel_group AS default_channel_group,
          session_source AS source,
          session_medium AS medium,
          session_campaign_name AS campaign,
          {udf}(
            session_default_channel_group,
            session_source,
            session_medium
          ) AS source_platform,
          SUM(event_count) AS event_count,
          SUM(key_events) AS key_events,
          SUM(total_users) AS users
        FROM `{raw}.ga4_events_daily`
        GROUP BY
          client_key, property_id, date, event_name,
          default_channel_group, source, medium, campaign, source_platform
        """

    if view_name == "vw_ga4_tech_daily":
        # Deliberate empty placeholder — device-split data isn't synced yet.
        # Exposes the contract columns so dependents resolve and return 0 rows.
        return """
        SELECT
          CAST(NULL AS STRING) AS client_key,
          CAST(NULL AS STRING) AS property_id,
          CAST(NULL AS DATE) AS date,
          CAST(NULL AS STRING) AS device_category,
          CAST(NULL AS STRING) AS operating_system,
          CAST(NULL AS STRING) AS browser,
          CAST(NULL AS INT64) AS users,
          CAST(NULL AS INT64) AS total_users,
          CAST(NULL AS INT64) AS sessions,
          CAST(NULL AS INT64) AS key_events
        FROM UNNEST([]) AS empty_row
        """

    if view_name == "vw_ga4_landing_pages_daily":
        return f"""
        SELECT
          client_key, property_id, date,
          CASE WHEN landing_page = '/' THEN '/'
               ELSE REGEXP_REPLACE(landing_page, r'/$', '') END AS landing_page,
          SUM(sessions) AS sessions,
          SUM(active_users) AS active_users,
          SUM(new_users) AS new_users,
          SUM(key_events) AS key_events,
          SAFE_DIVIDE(SUM(key_events), SUM(sessions)) AS session_key_event_rate,
          AVG(average_session_duration) AS average_session_duration
        FROM `{raw}.ga4_landing_pages_daily`
        GROUP BY client_key, property_id, date, landing_page
        """

    if view_name == "vw_ga4_landing_page_events_daily":
        # Per landing_page × event breakdown, so the dashboard can recompute
        # "key events" for a client-selected set of events. key_events carries
        # GA4's own designation (the default set); event_count powers overrides.
        return f"""
        SELECT
          client_key, property_id, date,
          CASE WHEN landing_page = '/' THEN '/'
               ELSE REGEXP_REPLACE(landing_page, r'/$', '') END AS landing_page,
          event_name,
          SUM(event_count) AS event_count,
          SUM(key_events)  AS key_events
        FROM `{raw}.ga4_landing_page_events_daily`
        GROUP BY client_key, property_id, date, landing_page, event_name
        """

    if view_name == "vw_ga4_user_acq_events_daily":
        # First-user channel/source/medium × event, so the User Acquisition
        # panel can recompute key events for a client-selected event set.
        return f"""
        SELECT
          client_key, property_id, date,
          default_channel_group, source, medium, event_name,
          SUM(event_count) AS event_count,
          SUM(key_events)  AS key_events
        FROM `{raw}.ga4_user_acq_events_daily`
        GROUP BY client_key, property_id, date, default_channel_group, source, medium, event_name
        """

    if view_name == "vw_ga4_page_events_daily":
        # Per page_path × event breakdown across ALL traffic (not just
        # entrances), so the Top Pages panel can recompute "key events" for a
        # client-selected set of events. Normalizes the trailing slash the same
        # way vw_page_path_source_daily does, so page_path matches Top Pages' rows.
        return f"""
        SELECT
          client_key, property_id, date,
          CASE WHEN page_path = '/' THEN '/'
               ELSE REGEXP_REPLACE(page_path, r'/$', '') END AS page_path,
          event_name,
          SUM(event_count) AS event_count,
          SUM(key_events)  AS key_events
        FROM `{raw}.ga4_page_events_daily`
        GROUP BY client_key, property_id, date, page_path, event_name
        """

    if view_name == "vw_ga4_demographics_daily":
        return f"""
        SELECT
          client_key, property_id, date,
          user_age_bracket, user_gender,
          SUM(active_users) AS active_users,
          SUM(key_events) AS key_events,
          AVG(engagement_rate) AS engagement_rate
        FROM `{raw}.ga4_demographics_daily`
        GROUP BY client_key, property_id, date, user_age_bracket, user_gender
        """

    if view_name == "vw_ga4_geo_daily":
        return f"""
        SELECT
          client_key, property_id, date,
          country, region, city,
          SUM(active_users) AS active_users,
          SUM(sessions) AS sessions,
          SUM(key_events) AS key_events
        FROM `{raw}.ga4_geo_daily`
        GROUP BY client_key, property_id, date, country, region, city
        """

    if view_name == "vw_ga4_geo_page_daily":
        # Geography per page. Only the page-path-scoped Demographics panel reads
        # this; unscoped geography still comes from vw_ga4_geo_daily, which is
        # cheaper and counts each user once site-wide. Trailing slash normalized
        # the same way vw_page_path_source_daily does so the page-path scope
        # patterns match the same paths Top Pages shows.
        return f"""
        SELECT
          client_key, property_id, date,
          CASE WHEN page_path = '/' THEN '/'
               ELSE REGEXP_REPLACE(page_path, r'/$', '') END AS page_path,
          country, region, city,
          SUM(active_users) AS active_users,
          SUM(sessions) AS sessions,
          SUM(key_events) AS key_events
        FROM `{raw}.ga4_geo_page_daily`
        GROUP BY client_key, property_id, date, page_path, country, region, city
        """

    if view_name == "vw_page_path_source_daily":
        return f"""
        WITH base AS (
          SELECT
            client_key, property_id, date,
            CASE WHEN page_path = '/' THEN '/'
                 ELSE REGEXP_REPLACE(page_path, r'/$', '') END AS page_path,
            page_title,
            session_default_channel_group AS default_channel_group,
            session_source AS source,
            session_medium AS medium,
            session_campaign_name AS campaign,
            {udf}(
              session_default_channel_group,
              session_source,
              session_medium
            ) AS source_platform,
            sessions, active_users, screen_page_views,
            engaged_sessions, key_events, user_engagement_duration
          FROM `{raw}.ga4_page_source_daily`
        )
        SELECT
          client_key, property_id, date, page_path,
          ANY_VALUE(page_title) AS page_title,
          {taxonomy["page_group"]} AS page_group,
          {taxonomy["page_topic"]} AS page_topic,
          {taxonomy["is_core_website_page"]} AS is_core_website_page,
          source_platform,
          source AS utm_source,
          medium AS utm_medium,
          campaign AS utm_campaign,
          CASE WHEN source_platform = 'ai_assistant' THEN TRUE ELSE FALSE END AS is_ai_referral,
          CASE WHEN source_platform = 'ai_assistant' THEN source ELSE NULL END AS ai_platform,
          SUM(screen_page_views) AS page_views,
          SUM(active_users) AS users,
          SUM(sessions) AS sessions,
          SUM(engaged_sessions) AS engaged_sessions,
          SUM(key_events) AS key_events,
          SUM(user_engagement_duration) AS engagement_seconds
        FROM base
        GROUP BY
          client_key, property_id, date, page_path,
          page_group, page_topic, is_core_website_page,
          source_platform, utm_source, utm_medium, utm_campaign,
          is_ai_referral, ai_platform
        """

    if view_name == "vw_page_path_daily":
        return f"""
        SELECT
          client_key, property_id, date, page_path,
          ANY_VALUE(page_title) AS page_title,
          page_group, page_topic, is_core_website_page,
          SUM(page_views) AS page_views,
          SUM(users) AS users,
          SUM(sessions) AS sessions,
          SUM(engaged_sessions) AS engaged_sessions,
          SUM(key_events) AS key_events,
          SUM(engagement_seconds) AS engagement_seconds
        FROM `{marts}.vw_page_path_source_daily`
        GROUP BY
          client_key, property_id, date, page_path,
          page_group, page_topic, is_core_website_page
        """

    raise ValueError(f"Unknown mart view: {view_name!r}")


# ── Provisioning ──────────────────────────────────────────────────────────────

def provision_ga4_mart_views(
    *,
    project: str,
    mart_dataset: str = _DEFAULT_MART_DATASET,
    raw_project: str | None = None,
    raw_dataset: str = _DEFAULT_RAW_DATASET,
    client_key: str | None = None,
    views: list[str] | None = None,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    """Provision the GA4 source_platform UDF + all mart views for one client.

    Idempotent (CREATE OR REPLACE throughout). ``raw_project`` defaults to
    ``project``. ``client_key`` selects the page taxonomy (default = generic).
    ``views`` restricts which views are rebuilt; the UDF is always ensured first.
    """
    import bigquery_service

    raw_proj = (raw_project or project).strip()
    raw_ref = f"{raw_proj}.{raw_dataset}"
    marts_ref = f"{project}.{mart_dataset}"
    taxonomy = _taxonomy_for(client_key)
    client = bigquery_service.build_client(project, credentials_env=credentials_env)

    results: dict[str, str] = {}

    # 1) UDF first — every classifier-using view depends on it.
    try:
        client.query(_udf_sql(marts_ref)).result(timeout=60)
        results[_UDF_NAME] = "ok"
        _log.info("Provisioned GA4 UDF %s.%s", marts_ref, _UDF_NAME)
    except Exception as exc:
        results[_UDF_NAME] = f"error: {str(exc)[:200]}"
        _log.warning("Failed to provision GA4 UDF: %s", exc)

    # 2) Views in dependency order.
    target = views or MART_VIEWS
    ordered = [v for v in MART_VIEWS if v in set(target)]
    for vname in ordered:
        try:
            sql_body = _view_sql(vname, raw=raw_ref, marts=marts_ref, taxonomy=taxonomy)
            view_id = f"{marts_ref}.{vname}"
            client.query(f"CREATE OR REPLACE VIEW `{view_id}` AS\n{sql_body}").result(timeout=60)
            _log.info("Provisioned mart view %s", view_id)
            results[vname] = "ok"
        except Exception as exc:
            _log.warning("Failed to provision mart view %s: %s", vname, exc)
            results[vname] = f"error: {str(exc)[:200]}"

    return {"project": project, "mart_dataset": mart_dataset, "client_key": client_key, "results": results}


def check_ga4_mart_health(
    *,
    project: str,
    mart_dataset: str = _DEFAULT_MART_DATASET,
    client_key: str,
    property_id: str | None = None,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    """Return per-view health stats: row_count + earliest/latest date.

    The canonical views don't carry synced_at, so freshness is by max(date).
    """
    import bigquery_service
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bq_client = bigquery_service.build_client(project, credentials_env=credentials_env)
    dataset_ref = f"{project}.{mart_dataset}"

    prop_filter = f"AND property_id = '{property_id}'" if property_id else ""

    results: dict[str, Any] = {}
    for vname in MART_VIEWS:
        try:
            sql = f"""
            SELECT
              COUNT(*) AS row_count,
              MIN(date) AS earliest_date,
              MAX(date) AS latest_date,
              COUNTIF(date = '{yesterday}') AS yesterday_rows
            FROM `{dataset_ref}.{vname}`
            WHERE client_key = '{client_key}'
            {prop_filter}
            """
            rows = list(bq_client.query(sql).result(timeout=30))
            r = dict(rows[0]) if rows else {}
            results[vname] = {
                "row_count": int(r.get("row_count") or 0),
                "earliest_date": str(r.get("earliest_date") or ""),
                "latest_date": str(r.get("latest_date") or ""),
                "has_yesterday": int(r.get("yesterday_rows") or 0) > 0,
                "optional": vname in OPTIONAL_VIEWS,
            }
        except Exception as exc:
            results[vname] = {
                "error": str(exc)[:200],
                "optional": vname in OPTIONAL_VIEWS,
            }
    return {
        "project": project,
        "mart_dataset": mart_dataset,
        "client_key": client_key,
        "property_id": property_id,
        "views": results,
        "required_ok": all(
            "error" not in v and v.get("row_count", 0) > 0
            for vn, v in results.items()
            if vn not in OPTIONAL_VIEWS
        ),
    }
