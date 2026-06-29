"""GA4 marketing mart — view DDL, provisioning, and health checks.

Creates/replaces BigQuery views in the marketing_marts dataset that front the
raw_ga4 tables.  All views are idempotent (CREATE OR REPLACE VIEW).

Key views:
  vw_page_path_daily          — from ga4_pageviews_daily (users deduplicated)
  vw_page_path_source_daily   — from ga4_page_source_daily (channel/source)
  vw_ga4_traffic_acq_daily    — from ga4_sessions_daily
  vw_ga4_landing_pages_daily  — from ga4_landing_pages_daily
  vw_ga4_tech_daily           — from ga4_tech_daily
  vw_ga4_events_daily         — from ga4_events_daily
  vw_ga4_user_acq_daily       — from ga4_user_acq_daily
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_RAW_DATASET = "raw_ga4"

# Views in dependency order (none depend on each other here).
MART_VIEWS = [
    "vw_page_path_daily",
    "vw_page_path_source_daily",
    "vw_ga4_traffic_acq_daily",
    "vw_ga4_landing_pages_daily",
    "vw_ga4_tech_daily",
    "vw_ga4_events_daily",
    "vw_ga4_user_acq_daily",
    "vw_ga4_geo_daily",
    "vw_ga4_demographics_daily",
]

# Optional views — may have no data; health check treats errors as non-fatal.
OPTIONAL_VIEWS = {"vw_ga4_geo_daily", "vw_ga4_demographics_daily"}


def _view_sql(view_name: str, *, raw: str) -> str:
    """Return the CREATE OR REPLACE VIEW SQL for the given mart view.

    `raw` is the fully-qualified raw_ga4 dataset prefix, e.g.
    ``"nixon-medical.raw_ga4"``.
    """
    if view_name == "vw_page_path_daily":
        # Source: ga4_pageviews_daily — users are already deduplicated at page
        # level so SUM(active_users) is correct here (unlike summing from source
        # rows where a user visiting via two channels appears twice).
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          page_path,
          ANY_VALUE(page_title)               AS page_title,
          CAST(NULL AS STRING)                AS page_group,
          CAST(NULL AS STRING)                AS page_topic,
          SUM(screen_page_views)              AS page_views,
          SUM(active_users)                   AS users,
          CAST(0 AS INT64)                    AS sessions,
          ROUND(SUM(
            COALESCE(average_session_duration, 0) *
            COALESCE(active_users, 0)
          ), 1)                               AS engagement_seconds,
          SUM(key_events)                     AS key_events,
          MAX(synced_at)                      AS synced_at
        FROM `{raw}.ga4_pageviews_daily`
        GROUP BY client_key, property_id, date, page_path
        """

    if view_name == "vw_page_path_source_daily":
        # Source: ga4_page_source_daily — one row per page × channel × date.
        # sessions CAN be summed here; only users should not be summed across
        # channel rows (handled by vw_page_path_daily above).
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          page_path,
          ANY_VALUE(page_title)                                       AS page_title,
          CAST(NULL AS STRING)                                        AS page_group,
          CAST(NULL AS STRING)                                        AS page_topic,
          COALESCE(session_default_channel_group, '(other)')          AS source_platform,
          CAST(FALSE AS BOOL)                                         AS is_ai_referral,
          CAST(NULL AS STRING)                                        AS ai_platform,
          COALESCE(session_campaign_name, '')                         AS utm_campaign,
          SUM(screen_page_views)                                      AS page_views,
          SUM(active_users)                                           AS users,
          SUM(sessions)                                               AS sessions,
          ROUND(SUM(COALESCE(user_engagement_duration, 0)), 1)        AS engagement_seconds,
          SUM(key_events)                                             AS key_events,
          MAX(synced_at)                                              AS synced_at
        FROM `{raw}.ga4_page_source_daily`
        GROUP BY
          client_key, property_id, date, page_path,
          source_platform, is_ai_referral, ai_platform, utm_campaign
        """

    if view_name == "vw_ga4_traffic_acq_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(session_default_channel_group, '(other)')  AS channel_group,
          COALESCE(session_source, '(direct)')                AS session_source,
          COALESCE(session_medium, '(none)')                  AS session_medium,
          COALESCE(session_campaign_name, '')                 AS session_campaign_name,
          SUM(sessions)                                       AS sessions,
          SUM(engaged_sessions)                               AS engaged_sessions,
          SUM(total_users)                                    AS total_users,
          SUM(new_users)                                      AS new_users,
          SUM(key_events)                                     AS key_events,
          ROUND(AVG(COALESCE(bounce_rate, 0)), 4)             AS bounce_rate,
          ROUND(AVG(COALESCE(engagement_rate, 0)), 4)         AS engagement_rate,
          SUM(screen_page_views)                              AS screen_page_views,
          MAX(synced_at)                                      AS synced_at
        FROM `{raw}.ga4_sessions_daily`
        GROUP BY
          client_key, property_id, date,
          channel_group, session_source, session_medium, session_campaign_name
        """

    if view_name == "vw_ga4_landing_pages_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(landing_page, '/')             AS landing_page,
          SUM(sessions)                           AS sessions,
          SUM(active_users)                       AS active_users,
          SUM(new_users)                          AS new_users,
          SUM(key_events)                         AS key_events,
          ROUND(AVG(COALESCE(average_session_duration, 0)), 2) AS average_session_duration,
          MAX(synced_at)                          AS synced_at
        FROM `{raw}.ga4_landing_pages_daily`
        GROUP BY client_key, property_id, date, landing_page
        """

    if view_name == "vw_ga4_tech_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(device_category, 'unknown')  AS device_category,
          COALESCE(browser, 'unknown')           AS browser,
          COALESCE(operating_system, 'unknown')  AS operating_system,
          SUM(active_users)                      AS active_users,
          SUM(engaged_sessions)                  AS engaged_sessions,
          SUM(key_events)                        AS key_events,
          MAX(synced_at)                         AS synced_at
        FROM `{raw}.ga4_tech_daily`
        GROUP BY client_key, property_id, date, device_category, browser, operating_system
        """

    if view_name == "vw_ga4_events_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(event_name, '(unknown)')                        AS event_name,
          COALESCE(session_default_channel_group, '(other)')       AS channel_group,
          COALESCE(session_source, '(direct)')                     AS session_source,
          COALESCE(session_medium, '(none)')                       AS session_medium,
          COALESCE(session_campaign_name, '')                      AS session_campaign_name,
          SUM(event_count)                                         AS event_count,
          SUM(key_events)                                          AS key_events,
          SUM(total_users)                                         AS total_users,
          MAX(synced_at)                                           AS synced_at
        FROM `{raw}.ga4_events_daily`
        GROUP BY
          client_key, property_id, date,
          event_name, channel_group, session_source, session_medium, session_campaign_name
        """

    if view_name == "vw_ga4_user_acq_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(first_user_default_channel_group, '(other)')   AS channel_group,
          COALESCE(first_user_source, '(direct)')                  AS first_user_source,
          COALESCE(first_user_medium, '(none)')                    AS first_user_medium,
          SUM(new_users)                                           AS new_users,
          SUM(active_users)                                        AS active_users,
          SUM(total_users)                                         AS total_users,
          SUM(key_events)                                          AS key_events,
          MAX(synced_at)                                           AS synced_at
        FROM `{raw}.ga4_user_acq_daily`
        GROUP BY
          client_key, property_id, date,
          channel_group, first_user_source, first_user_medium
        """

    if view_name == "vw_ga4_geo_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(country, '(unknown)')        AS country,
          COALESCE(region, '(unknown)')         AS region,
          COALESCE(city, '(unknown)')           AS city,
          SUM(active_users)                     AS active_users,
          SUM(sessions)                         AS sessions,
          SUM(key_events)                       AS key_events,
          MAX(synced_at)                        AS synced_at
        FROM `{raw}.ga4_geo_daily`
        GROUP BY client_key, property_id, date, country, region, city
        """

    if view_name == "vw_ga4_demographics_daily":
        return f"""
        SELECT
          client_key,
          property_id,
          date,
          COALESCE(user_age_bracket, '(unknown)')  AS user_age_bracket,
          COALESCE(user_gender, '(unknown)')        AS user_gender,
          SUM(active_users)                         AS active_users,
          SUM(key_events)                           AS key_events,
          ROUND(AVG(COALESCE(engagement_rate, 0)), 4) AS engagement_rate,
          MAX(synced_at)                            AS synced_at
        FROM `{raw}.ga4_demographics_daily`
        GROUP BY client_key, property_id, date, user_age_bracket, user_gender
        """

    raise ValueError(f"Unknown mart view: {view_name!r}")


def provision_ga4_mart_views(
    *,
    project: str,
    mart_dataset: str = _DEFAULT_MART_DATASET,
    raw_project: str | None = None,
    raw_dataset: str = _DEFAULT_RAW_DATASET,
    views: list[str] | None = None,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    """CREATE OR REPLACE all GA4 mart views in ``{project}.{mart_dataset}``.

    ``raw_project`` defaults to ``project`` when omitted.
    ``views`` restricts which views are rebuilt; defaults to all MART_VIEWS.
    """
    import bigquery_service
    raw_proj = (raw_project or project).strip()
    raw_ref = f"{raw_proj}.{raw_dataset}"
    client = bigquery_service.build_client(project, credentials_env=credentials_env)
    target = views or MART_VIEWS
    results: dict[str, str] = {}
    for vname in target:
        try:
            sql_body = _view_sql(vname, raw=raw_ref)
            view_id = f"{project}.{mart_dataset}.{vname}"
            client.query(
                f"CREATE OR REPLACE VIEW `{view_id}` AS\n{sql_body}"
            ).result(timeout=60)
            _log.info("Provisioned mart view %s", view_id)
            results[vname] = "ok"
        except Exception as exc:
            _log.warning("Failed to provision mart view %s: %s", vname, exc)
            results[vname] = f"error: {str(exc)[:200]}"
    return {"project": project, "mart_dataset": mart_dataset, "views": results}


def check_ga4_mart_health(
    *,
    project: str,
    mart_dataset: str = _DEFAULT_MART_DATASET,
    client_key: str,
    property_id: str | None = None,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    """Return per-view health stats: row_count, earliest/latest date, latest synced_at."""
    import bigquery_service
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bq_client = bigquery_service.build_client(project, credentials_env=credentials_env)
    dataset_ref = f"{project}.{mart_dataset}"

    prop_filter = f"AND property_id = '{property_id}'" if property_id else ""

    all_views = MART_VIEWS + list(OPTIONAL_VIEWS)
    results: dict[str, Any] = {}
    for vname in all_views:
        try:
            sql = f"""
            SELECT
              COUNT(*) AS row_count,
              MIN(date) AS earliest_date,
              MAX(date) AS latest_date,
              MAX(synced_at) AS latest_synced_at,
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
                "latest_synced_at": str(r.get("latest_synced_at") or ""),
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
