"""GA4 on-site attribution for Google Ads traffic via BigQuery export."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from bigquery_service import env_summary, run_query
from dates_util import resolve_date_range
from ga4_clients import Ga4ClientTarget, resolve_target

# Key / conversion-style events (extend as Penn marks more in GA4 admin).
KEY_EVENT_NAMES: tuple[str, ...] = (
    "purchase",
    "generate_lead",
    "sign_up",
    "form_submit",
    "contact",
    "phone_call",
    "click_to_call",
    "apply_now",
    "get_started",
)

METHODOLOGY = (
    "Google Ads → site sessions are classified in priority order: "
    "(1) GA4 linked Google Ads campaign ID on the session, "
    "(2) gclid present (auto-tagging), "
    "(3) source/medium google/cpc fallback. "
    "Engagement rate uses GA4-style rules: ≥10s engagement time, ≥2 page views, or a key event. "
    "Requires GA4 BigQuery export and Google Ads auto-tagging; link GA4 to Google Ads for campaign-level accuracy."
)


def _events_table(target: Ga4ClientTarget) -> str:
    return f"`{target.bq_project_id}.{target.bq_dataset_id}.events_*`"


def _key_events_sql_list() -> str:
    return ", ".join(f"'{name}'" for name in KEY_EVENT_NAMES)


def _ensure_bq_ready() -> None:
    summ = env_summary()
    if not summ.get("gcp_service_account_json_parse_ok"):
        raise RuntimeError(
            summ.get("gcp_service_account_json_parse_error")
            or "GCP_SERVICE_ACCOUNT_JSON did not parse."
        )


def _attribution_base_sql(
    table: str,
    suffix_start: str,
    suffix_end: str,
) -> str:
    """CTE block: classify Google Ads sessions and roll up session metrics."""
    key_events = _key_events_sql_list()
    return f"""
    WITH raw_events AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        PARSE_DATE('%Y%m%d', event_date) AS metric_date,
        event_name,
        traffic_source.source AS source,
        traffic_source.medium AS medium,
        collected_traffic_source.gclid AS gclid,
        session_traffic_source_last_click.google_ads_campaign.campaign_id AS linked_campaign_id,
        session_traffic_source_last_click.google_ads_campaign.campaign_name AS linked_campaign_name,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec') AS engagement_time_msec
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{suffix_start}' AND '{suffix_end}'
    ),
    session_starts AS (
      SELECT
        user_pseudo_id,
        ga_session_id,
        metric_date,
        source,
        medium,
        gclid,
        linked_campaign_id,
        linked_campaign_name,
        CASE
          WHEN linked_campaign_id IS NOT NULL
            AND linked_campaign_id != '(not set)'
            AND linked_campaign_id != ''
            THEN 'linked'
          WHEN NULLIF(gclid, '') IS NOT NULL THEN 'gclid'
          WHEN source = 'google' AND medium = 'cpc' THEN 'source_medium'
        END AS attribution_tier
      FROM raw_events
      WHERE event_name = 'session_start'
    ),
    google_ads_sessions AS (
      SELECT * FROM session_starts WHERE attribution_tier IS NOT NULL
    ),
    session_events AS (
      SELECT
        e.user_pseudo_id,
        e.ga_session_id,
        e.metric_date,
        e.event_name,
        e.engagement_time_msec
      FROM raw_events e
      INNER JOIN google_ads_sessions s
        ON e.user_pseudo_id = s.user_pseudo_id
        AND e.ga_session_id = s.ga_session_id
        AND e.metric_date = s.metric_date
    ),
    session_rollups AS (
      SELECT
        s.user_pseudo_id,
        s.ga_session_id,
        s.metric_date,
        s.attribution_tier,
        s.linked_campaign_id,
        s.linked_campaign_name,
        s.gclid,
        COUNTIF(e.event_name = 'page_view') AS page_views,
        COALESCE(MAX(e.engagement_time_msec), 0) AS max_engagement_time_msec,
        COUNTIF(e.event_name IN ({key_events})) AS key_events,
        COUNTIF(e.event_name = 'user_engagement') AS user_engagement_events
      FROM google_ads_sessions s
      LEFT JOIN session_events e
        ON s.user_pseudo_id = e.user_pseudo_id
        AND s.ga_session_id = e.ga_session_id
        AND s.metric_date = e.metric_date
      GROUP BY
        s.user_pseudo_id,
        s.ga_session_id,
        s.metric_date,
        s.attribution_tier,
        s.linked_campaign_id,
        s.linked_campaign_name,
        s.gclid
    )
    """


def fetch_google_ads_attribution(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
) -> dict[str, Any]:
    """
    On-site metrics for sessions attributed to Google Ads.

    Returns daily totals, per-campaign rows (when linked), tier breakdown, and top events.
    """
    _ensure_bq_ready()
    target = target or resolve_target()
    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = _events_table(target)
    base = _attribution_base_sql(table, suffix_start, suffix_end)
    key_events = _key_events_sql_list()

    daily_sql = (
        base
        + f"""
    SELECT
      metric_date,
      attribution_tier,
      COUNT(*) AS sessions,
      COUNTIF(
        max_engagement_time_msec >= 10000
        OR page_views >= 2
        OR key_events >= 1
        OR user_engagement_events >= 1
      ) AS engaged_sessions,
      SUM(page_views) AS page_views,
      SUM(key_events) AS key_events,
      COUNTIF(gclid IS NOT NULL AND gclid != '') AS sessions_with_gclid
    FROM session_rollups
    GROUP BY metric_date, attribution_tier
    ORDER BY metric_date, attribution_tier
    """
    )

    campaign_sql = (
        base
        + """
    SELECT
      COALESCE(NULLIF(linked_campaign_id, '(not set)'), '') AS campaign_id,
      COALESCE(NULLIF(linked_campaign_name, '(not set)'), '') AS campaign_name,
      attribution_tier,
      COUNT(*) AS sessions,
      COUNTIF(
        max_engagement_time_msec >= 10000
        OR page_views >= 2
        OR key_events >= 1
        OR user_engagement_events >= 1
      ) AS engaged_sessions,
      SUM(page_views) AS page_views,
      SUM(key_events) AS key_events
    FROM session_rollups
    GROUP BY campaign_id, campaign_name, attribution_tier
    ORDER BY sessions DESC
    """
    )

    events_sql = (
        base
        + f"""
    SELECT
      e.event_name,
      COUNT(*) AS event_count
    FROM session_events e
    INNER JOIN google_ads_sessions s
      ON e.user_pseudo_id = s.user_pseudo_id
      AND e.ga_session_id = s.ga_session_id
      AND e.metric_date = s.metric_date
    WHERE e.event_name NOT IN ('session_start', 'first_visit', 'user_engagement')
    GROUP BY e.event_name
    ORDER BY event_count DESC
    LIMIT 25
    """
    )

    daily_rows = run_query(daily_sql, max_rows=5000, project_id=target.bq_project_id)
    campaign_rows = run_query(campaign_sql, max_rows=2000, project_id=target.bq_project_id)
    top_event_rows = run_query(events_sql, max_rows=50, project_id=target.bq_project_id)

    return _build_report(
        start=start,
        end=end,
        target=target,
        daily_rows=daily_rows,
        campaign_rows=campaign_rows,
        top_event_rows=top_event_rows,
    )


def _build_report(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget,
    daily_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    top_event_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_date: dict[str, dict[str, Any]] = {}
    tier_totals: dict[str, dict[str, float | int]] = {
        "linked": {"sessions": 0, "engaged_sessions": 0, "page_views": 0, "key_events": 0},
        "gclid": {"sessions": 0, "engaged_sessions": 0, "page_views": 0, "key_events": 0},
        "source_medium": {"sessions": 0, "engaged_sessions": 0, "page_views": 0, "key_events": 0},
    }

    for row in daily_rows:
        raw_date = row.get("metric_date")
        if hasattr(raw_date, "isoformat"):
            day = raw_date.isoformat()
        else:
            day = str(raw_date).strip()[:10]
        tier = str(row.get("attribution_tier") or "")
        sessions = int(row.get("sessions") or 0)
        engaged = int(row.get("engaged_sessions") or 0)
        page_views = int(row.get("page_views") or 0)
        key_events = int(row.get("key_events") or 0)

        bucket = by_date.setdefault(
            day,
            {
                "metric_date": day,
                "sessions": 0,
                "engaged_sessions": 0,
                "page_views": 0,
                "key_events": 0,
                "sessions_with_gclid": 0,
                "by_tier": {},
            },
        )
        bucket["sessions"] += sessions
        bucket["engaged_sessions"] += engaged
        bucket["page_views"] += page_views
        bucket["key_events"] += key_events
        bucket["sessions_with_gclid"] += int(row.get("sessions_with_gclid") or 0)
        bucket["by_tier"][tier] = {
            "sessions": sessions,
            "engaged_sessions": engaged,
            "page_views": page_views,
            "key_events": key_events,
        }

        if tier in tier_totals:
            tier_totals[tier]["sessions"] += sessions
            tier_totals[tier]["engaged_sessions"] += engaged
            tier_totals[tier]["page_views"] += page_views
            tier_totals[tier]["key_events"] += key_events

    daily_out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        row = by_date.get(key) or {
            "metric_date": key,
            "sessions": 0,
            "engaged_sessions": 0,
            "page_views": 0,
            "key_events": 0,
            "sessions_with_gclid": 0,
            "by_tier": {},
        }
        sessions = int(row["sessions"])
        engaged = int(row["engaged_sessions"])
        row["engagement_rate"] = round(engaged / sessions, 4) if sessions else 0.0
        daily_out.append(row)
        cursor += timedelta(days=1)

    total_sessions = sum(int(r["sessions"]) for r in daily_out)
    total_engaged = sum(int(r["engaged_sessions"]) for r in daily_out)
    total_page_views = sum(int(r["page_views"]) for r in daily_out)
    total_key_events = sum(int(r["key_events"]) for r in daily_out)

    campaigns_out: list[dict[str, Any]] = []
    for row in campaign_rows:
        sessions = int(row.get("sessions") or 0)
        engaged = int(row.get("engaged_sessions") or 0)
        campaigns_out.append(
            {
                "campaign_id": str(row.get("campaign_id") or ""),
                "campaign_name": str(row.get("campaign_name") or "") or "—",
                "attribution_tier": str(row.get("attribution_tier") or ""),
                "sessions": sessions,
                "engaged_sessions": engaged,
                "engagement_rate": round(engaged / sessions, 4) if sessions else 0.0,
                "page_views": int(row.get("page_views") or 0),
                "key_events": int(row.get("key_events") or 0),
            }
        )

    confirmed_sessions = (
        tier_totals["linked"]["sessions"] + tier_totals["gclid"]["sessions"]
    )
    confirmed_engaged = (
        tier_totals["linked"]["engaged_sessions"] + tier_totals["gclid"]["engaged_sessions"]
    )

    return {
        "client_key": target.client_key,
        "account_id": target.account_id,
        "bq_project_id": target.bq_project_id,
        "bq_dataset_id": target.bq_dataset_id,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": METHODOLOGY,
        "key_event_names": list(KEY_EVENT_NAMES),
        "totals": {
            "sessions": total_sessions,
            "engaged_sessions": total_engaged,
            "engagement_rate": round(total_engaged / total_sessions, 4) if total_sessions else 0.0,
            "page_views": total_page_views,
            "key_events": total_key_events,
            "confirmed_sessions": int(confirmed_sessions),
            "confirmed_engagement_rate": (
                round(confirmed_engaged / confirmed_sessions, 4) if confirmed_sessions else 0.0
            ),
            "by_tier": tier_totals,
        },
        "daily": daily_out,
        "by_campaign": campaigns_out,
        "top_events": [
            {
                "event_name": str(r.get("event_name") or ""),
                "event_count": int(r.get("event_count") or 0),
            }
            for r in top_event_rows
        ],
    }


def fetch_attribution_for_dashboard(
    *,
    date_range: str = "LAST_30_DAYS",
    client_key: str | None = None,
) -> dict[str, Any]:
    """Entry point for Penn dashboard refresh."""
    target = resolve_target(client_key=client_key)
    start, end, preset = resolve_date_range(date_range)
    report = fetch_google_ads_attribution(start=start, end=end, target=target)
    report["date_range"]["preset"] = preset
    return report
