"""GA4 on-site attribution for paid media (Google, LinkedIn, Meta) via BigQuery."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from bigquery_service import env_summary, run_query
from dates_util import resolve_date_range
from ga4_clients import Ga4ClientTarget, resolve_target

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

METHODOLOGY: dict[str, str] = {
    "google": (
        "Google Ads sessions: (1) GA4 linked Google Ads campaign ID, "
        "(2) gclid auto-tagging, (3) source/medium google/cpc fallback. "
        "Link GA4 to Google Ads + enable auto-tagging for campaign-level accuracy."
    ),
    "linkedin": (
        "LinkedIn sessions: (1) li_fat_id click parameter, "
        "(2) UTM campaign id/name on the session, "
        "(3) source/medium linkedin + paid/cpc/social fallback. "
        "Use LinkedIn Campaign Manager URL parameters (utm_source=linkedin&utm_medium=paid&utm_campaign=…)."
    ),
    "meta": (
        "Meta sessions: (1) utm_source_platform=Meta tag, (2) fbclid click parameter, "
        "(3) UTM campaign id/name on the session, "
        "(4) source/medium facebook/instagram + paid/cpc fallback. "
        "Enable Meta URL parameters or manual UTMs on ad destination URLs."
    ),
}

PLATFORM_TIER_LABELS: dict[str, dict[str, str]] = {
    "google": {
        "linked": "Linked Google Ads campaign",
        "gclid": "gclid (auto-tagging)",
        "source_medium": "google / cpc fallback",
    },
    "linkedin": {
        "click_id": "li_fat_id (LinkedIn click ID)",
        "utm_campaign": "UTM campaign id/name",
        "source_medium": "linkedin + paid medium fallback",
    },
    "meta": {
        "click_id": "fbclid (Meta click ID)",
        "utm_campaign": "UTM campaign id/name",
        "source_medium": "Meta source + paid medium fallback",
    },
}

CONFIRMED_TIERS: dict[str, tuple[str, ...]] = {
    "google": ("linked", "gclid"),
    "linkedin": ("click_id", "utm_campaign"),
    "meta": ("click_id", "utm_campaign"),
}


def _events_table(target: Ga4ClientTarget) -> str:
    return f"`{target.bq_project_id}.{target.bq_dataset_id}.events_*`"


_GA4_EVENT_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def resolve_key_event_names(client_key: str | None = None) -> tuple[str, ...]:
    """Effective GA4 key-event names for a client.

    Reads the admin-configured list from ``client_dashboard_config.ga4_key_events``
    (newline-separated event names, set on the dashboard Settings page) and falls
    back to the built-in :data:`KEY_EVENT_NAMES` when the client has configured
    none. Names are validated against GA4's event-name grammar (``[A-Za-z0-9_]``)
    so the result is always safe to interpolate into the ``COUNTIF(...)`` SQL.

    This is the single source of truth for "what counts as a conversion" across
    the attribution, page, and warehouse GA4 services — previously each hard-coded
    its own list and the Settings control was wired to nothing.
    """
    slug = (client_key or "").strip().lower()
    if slug:
        raw = ""
        try:
            import client_dashboard_config

            cfg = client_dashboard_config.get_config(slug)
            raw = (cfg.ga4_key_events if cfg else "") or ""
        except Exception:
            raw = ""
        names: list[str] = []
        for line in raw.splitlines():
            name = line.strip()
            if name and _GA4_EVENT_NAME_RE.match(name) and name not in names:
                names.append(name)
        if names:
            return tuple(names)
    return KEY_EVENT_NAMES


def _key_events_sql_list(client_key: str | None = None) -> str:
    return ", ".join(f"'{name}'" for name in resolve_key_event_names(client_key))


def _ensure_bq_ready(credentials_env: str | None = None) -> None:
    summ = env_summary(credentials_env=credentials_env)
    if not summ.get("gcp_service_account_json_parse_ok"):
        raise RuntimeError(
            summ.get("gcp_service_account_json_parse_error")
            or "GCP_SERVICE_ACCOUNT_JSON did not parse."
        )


def _attribution_base_sql(
    table: str, suffix_start: str, suffix_end: str, client_key: str | None = None
) -> str:
    key_events = _key_events_sql_list(client_key)
    return f"""
    WITH raw_events AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        PARSE_DATE('%Y%m%d', event_date) AS metric_date,
        event_name,
        traffic_source.source AS source,
        traffic_source.medium AS medium,
        traffic_source.name AS traffic_campaign_name,
        collected_traffic_source.gclid AS gclid,
        collected_traffic_source.manual_source AS manual_source,
        collected_traffic_source.manual_medium AS manual_medium,
        collected_traffic_source.manual_campaign_name AS manual_campaign_name,
        collected_traffic_source.manual_campaign_id AS manual_campaign_id,
        collected_traffic_source.manual_term AS manual_term,
        collected_traffic_source.manual_content AS manual_content,
        collected_traffic_source.manual_source_platform AS manual_source_platform,
        session_traffic_source_last_click.google_ads_campaign.campaign_id AS linked_campaign_id,
        session_traffic_source_last_click.google_ads_campaign.campaign_name AS linked_campaign_name,
        session_traffic_source_last_click.manual_campaign.campaign_id AS click_campaign_id,
        session_traffic_source_last_click.manual_campaign.campaign_name AS click_campaign_name,
        (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location') AS page_location,
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
        manual_source,
        manual_medium,
        manual_campaign_name,
        manual_campaign_id,
        click_campaign_id,
        click_campaign_name,
        traffic_campaign_name,
        manual_term,
        manual_content,
        REGEXP_EXTRACT(page_location, r'[?&]fbclid=([^&]+)') AS fbclid,
        REGEXP_EXTRACT(page_location, r'[?&]li_fat_id=([^&]+)') AS li_fat_id,
        LOWER(COALESCE(source, '')) AS src,
        LOWER(COALESCE(medium, '')) AS med,
        LOWER(COALESCE(manual_source, '')) AS utm_src,
        LOWER(COALESCE(manual_medium, '')) AS utm_med,
        CASE
          WHEN linked_campaign_id IS NOT NULL
            AND linked_campaign_id NOT IN ('', '(not set)')
            THEN 'google'
          WHEN NULLIF(gclid, '') IS NOT NULL THEN 'google'
          WHEN LOWER(COALESCE(source, '')) = 'google'
            AND LOWER(COALESCE(medium, '')) IN ('cpc', 'ppc', 'paid')
            THEN 'google'
          -- Robust, tag-driven Meta signal: utm_source_platform=Meta lands here
          -- regardless of what utm_source carries ({{site_source_name}} -> fb/ig/an/msg).
          WHEN LOWER(COALESCE(manual_source_platform, '')) = 'meta'
            THEN 'meta'
          WHEN NULLIF(REGEXP_EXTRACT(page_location, r'[?&]fbclid=([^&]+)'), '') IS NOT NULL
            THEN 'meta'
          WHEN LOWER(COALESCE(source, '')) IN ('facebook', 'fb', 'instagram', 'ig', 'meta', 'msg', 'an')
            OR LOWER(COALESCE(manual_source, '')) IN ('facebook', 'fb', 'instagram', 'ig', 'meta', 'msg', 'an')
            OR LOWER(COALESCE(source, '')) LIKE '%facebook%'
            OR LOWER(COALESCE(source, '')) LIKE '%instagram%'
            THEN 'meta'
          WHEN LOWER(COALESCE(medium, '')) IN ('paid', 'cpc', 'cpm', 'social', 'social-paid', 'paidsocial')
            AND (
              LOWER(COALESCE(source, '')) IN ('facebook', 'fb', 'instagram', 'ig', 'meta')
              OR LOWER(COALESCE(manual_source, '')) IN ('facebook', 'fb', 'instagram', 'ig', 'meta')
            )
            THEN 'meta'
          WHEN NULLIF(REGEXP_EXTRACT(page_location, r'[?&]li_fat_id=([^&]+)'), '') IS NOT NULL
            THEN 'linkedin'
          WHEN LOWER(COALESCE(source, '')) IN ('linkedin', 'linkedin.com', 'lnkd.in')
            OR LOWER(COALESCE(source, '')) LIKE '%linkedin%'
            OR LOWER(COALESCE(manual_source, '')) IN ('linkedin', 'linkedin.com')
            OR LOWER(COALESCE(manual_source, '')) LIKE '%linkedin%'
            THEN 'linkedin'
          WHEN LOWER(COALESCE(medium, '')) IN ('paid', 'cpc', 'cpm', 'social', 'social-paid', 'paidsocial')
            AND (
              LOWER(COALESCE(source, '')) LIKE '%linkedin%'
              OR LOWER(COALESCE(manual_source, '')) LIKE '%linkedin%'
            )
            THEN 'linkedin'
        END AS platform,
        CASE
          WHEN linked_campaign_id IS NOT NULL
            AND linked_campaign_id NOT IN ('', '(not set)')
            THEN 'linked'
          WHEN NULLIF(gclid, '') IS NOT NULL THEN 'gclid'
          WHEN NULLIF(REGEXP_EXTRACT(page_location, r'[?&]fbclid=([^&]+)'), '') IS NOT NULL
            THEN 'click_id'
          WHEN NULLIF(REGEXP_EXTRACT(page_location, r'[?&]li_fat_id=([^&]+)'), '') IS NOT NULL
            THEN 'click_id'
          WHEN COALESCE(manual_campaign_id, click_campaign_id, '') NOT IN ('', '(not set)')
            OR COALESCE(manual_campaign_name, click_campaign_name, traffic_campaign_name, '') NOT IN ('', '(not set)')
            THEN 'utm_campaign'
          ELSE 'source_medium'
        END AS attribution_tier,
        CASE
          WHEN linked_campaign_id IS NOT NULL
            AND linked_campaign_id NOT IN ('', '(not set)')
            THEN linked_campaign_id
          ELSE COALESCE(
            NULLIF(manual_campaign_id, '(not set)'),
            NULLIF(click_campaign_id, '(not set)'),
            ''
          )
        END AS campaign_id,
        CASE
          WHEN linked_campaign_name IS NOT NULL
            AND linked_campaign_name NOT IN ('', '(not set)')
            THEN linked_campaign_name
          ELSE COALESCE(
            NULLIF(manual_campaign_name, '(not set)'),
            NULLIF(click_campaign_name, '(not set)'),
            NULLIF(traffic_campaign_name, '(not set)'),
            ''
          )
        END AS campaign_name,
        -- Meta dynamic URL params land ad set id in manual_term (utm_term) and
        -- ad id in manual_content (utm_content). Both are real Meta object ids
        -- that join by exact id to the adset/ad marts. Empty when untagged.
        COALESCE(NULLIF(manual_term, '(not set)'), '') AS adset_id,
        COALESCE(NULLIF(manual_content, '(not set)'), '') AS ad_id
      FROM raw_events
      WHERE event_name = 'session_start'
    ),
    paid_sessions AS (
      SELECT * FROM session_starts WHERE platform IS NOT NULL
    ),
    session_events AS (
      SELECT
        e.user_pseudo_id,
        e.ga_session_id,
        e.metric_date,
        e.event_name,
        e.engagement_time_msec,
        s.platform
      FROM raw_events e
      INNER JOIN paid_sessions s
        ON e.user_pseudo_id = s.user_pseudo_id
        AND e.ga_session_id = s.ga_session_id
        AND e.metric_date = s.metric_date
    ),
    session_rollups AS (
      SELECT
        s.user_pseudo_id,
        s.ga_session_id,
        s.metric_date,
        s.platform,
        s.attribution_tier,
        s.campaign_id,
        s.campaign_name,
        s.adset_id,
        s.ad_id,
        s.gclid,
        s.fbclid,
        s.li_fat_id,
        COUNTIF(e.event_name = 'page_view') AS page_views,
        COALESCE(MAX(e.engagement_time_msec), 0) AS max_engagement_time_msec,
        COUNTIF(e.event_name IN ({key_events})) AS key_events,
        COUNTIF(e.event_name = 'user_engagement') AS user_engagement_events
      FROM paid_sessions s
      LEFT JOIN session_events e
        ON s.user_pseudo_id = e.user_pseudo_id
        AND s.ga_session_id = e.ga_session_id
        AND s.metric_date = e.metric_date
        AND s.platform = e.platform
      GROUP BY
        s.user_pseudo_id,
        s.ga_session_id,
        s.metric_date,
        s.platform,
        s.attribution_tier,
        s.campaign_id,
        s.campaign_name,
        s.adset_id,
        s.ad_id,
        s.gclid,
        s.fbclid,
        s.li_fat_id
    )
    """


def fetch_paid_media_attribution(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
) -> dict[str, Any]:
    target = target or resolve_target()
    _ensure_bq_ready(credentials_env=target.credentials_env)
    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = _events_table(target)
    base = _attribution_base_sql(table, suffix_start, suffix_end, target.client_key)

    daily_sql = (
        base
        + """
    SELECT
      metric_date,
      platform,
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
    GROUP BY metric_date, platform, attribution_tier
    ORDER BY metric_date, platform, attribution_tier
    """
    )

    campaign_sql = (
        base
        + """
    SELECT
      platform,
      COALESCE(NULLIF(campaign_id, '(not set)'), '') AS campaign_id,
      COALESCE(NULLIF(campaign_name, '(not set)'), '') AS campaign_name,
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
    GROUP BY platform, campaign_id, campaign_name, attribution_tier
    ORDER BY platform, sessions DESC
    """
    )

    entity_sql = (
        base
        + """
    SELECT
      platform,
      COALESCE(NULLIF(campaign_id, '(not set)'), '') AS campaign_id,
      COALESCE(NULLIF(campaign_name, '(not set)'), '') AS campaign_name,
      adset_id,
      ad_id,
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
    GROUP BY platform, campaign_id, campaign_name, adset_id, ad_id
    ORDER BY platform, sessions DESC
    """
    )

    events_sql = (
        base
        + """
    SELECT
      s.platform,
      e.event_name,
      COUNT(*) AS event_count
    FROM session_events e
    INNER JOIN paid_sessions s
      ON e.user_pseudo_id = s.user_pseudo_id
      AND e.ga_session_id = s.ga_session_id
      AND e.metric_date = s.metric_date
      AND e.platform = s.platform
    WHERE e.event_name NOT IN ('session_start', 'first_visit', 'user_engagement')
    GROUP BY s.platform, e.event_name
    ORDER BY s.platform, event_count DESC
    """
    )

    daily_rows = run_query(daily_sql, max_rows=10000, project_id=target.bq_project_id, credentials_env=target.credentials_env)
    campaign_rows = run_query(campaign_sql, max_rows=5000, project_id=target.bq_project_id, credentials_env=target.credentials_env)
    entity_rows = run_query(entity_sql, max_rows=20000, project_id=target.bq_project_id, credentials_env=target.credentials_env)
    top_event_rows = run_query(events_sql, max_rows=200, project_id=target.bq_project_id, credentials_env=target.credentials_env)

    return _build_multi_platform_report(
        start=start,
        end=end,
        target=target,
        daily_rows=daily_rows,
        campaign_rows=campaign_rows,
        entity_rows=entity_rows,
        top_event_rows=top_event_rows,
    )


def _empty_tier_totals(platform: str) -> dict[str, dict[str, int]]:
    labels = PLATFORM_TIER_LABELS.get(platform) or {}
    return {tier: {"sessions": 0, "engaged_sessions": 0, "page_views": 0, "key_events": 0} for tier in labels}


def _build_platform_report(
    *,
    platform: str,
    start: date,
    end: date,
    daily_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    top_event_rows: list[dict[str, Any]],
    entity_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tier_totals = _empty_tier_totals(platform)
    by_date: dict[str, dict[str, Any]] = {}

    for row in daily_rows:
        if str(row.get("platform") or "") != platform:
            continue
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
                "by_tier": {},
            },
        )
        bucket["sessions"] += sessions
        bucket["engaged_sessions"] += engaged
        bucket["page_views"] += page_views
        bucket["key_events"] += key_events
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
            "by_tier": {},
        }
        sessions = int(row["sessions"])
        engaged = int(row["engaged_sessions"])
        row["engagement_rate"] = round(engaged / sessions, 4) if sessions else 0.0
        daily_out.append(row)
        cursor += timedelta(days=1)

    campaigns_out: list[dict[str, Any]] = []
    for row in campaign_rows:
        if str(row.get("platform") or "") != platform:
            continue
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

    confirmed_tiers = CONFIRMED_TIERS.get(platform, ())
    confirmed_sessions = sum(int(tier_totals[t]["sessions"]) for t in confirmed_tiers if t in tier_totals)
    confirmed_engaged = sum(
        int(tier_totals[t]["engaged_sessions"]) for t in confirmed_tiers if t in tier_totals
    )
    total_sessions = sum(int(r["sessions"]) for r in daily_out)
    total_engaged = sum(int(r["engaged_sessions"]) for r in daily_out)

    return {
        "platform": platform,
        "methodology": METHODOLOGY.get(platform, ""),
        "totals": {
            "sessions": total_sessions,
            "engaged_sessions": total_engaged,
            "engagement_rate": round(total_engaged / total_sessions, 4) if total_sessions else 0.0,
            "page_views": sum(int(r["page_views"]) for r in daily_out),
            "key_events": sum(int(r["key_events"]) for r in daily_out),
            "confirmed_sessions": confirmed_sessions,
            "confirmed_engagement_rate": (
                round(confirmed_engaged / confirmed_sessions, 4) if confirmed_sessions else 0.0
            ),
            "by_tier": tier_totals,
        },
        "daily": daily_out,
        "by_campaign": campaigns_out,
        "by_entity": [
            {
                "campaign_id": str(row.get("campaign_id") or ""),
                "campaign_name": str(row.get("campaign_name") or ""),
                "adset_id": str(row.get("adset_id") or ""),
                "ad_id": str(row.get("ad_id") or ""),
                "sessions": int(row.get("sessions") or 0),
                "engaged_sessions": int(row.get("engaged_sessions") or 0),
                "page_views": int(row.get("page_views") or 0),
                "key_events": int(row.get("key_events") or 0),
            }
            for row in (entity_rows or [])
            if str(row.get("platform") or "") == platform
        ],
        "top_events": [
            {
                "event_name": str(r.get("event_name") or ""),
                "event_count": int(r.get("event_count") or 0),
            }
            for r in top_event_rows
            if str(r.get("platform") or "") == platform
        ][:25],
    }


def _build_multi_platform_report(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget,
    daily_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    top_event_rows: list[dict[str, Any]],
    entity_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    platforms: dict[str, dict[str, Any]] = {}
    for platform in ("google", "linkedin", "meta"):
        platforms[platform] = _build_platform_report(
            platform=platform,
            start=start,
            end=end,
            daily_rows=daily_rows,
            campaign_rows=campaign_rows,
            top_event_rows=top_event_rows,
            entity_rows=entity_rows,
        )

    combined_by_date: dict[str, dict[str, int]] = {}
    for platform, report in platforms.items():
        for day in report.get("daily") or []:
            key = str(day.get("metric_date") or "")[:10]
            bucket = combined_by_date.setdefault(
                key,
                {"metric_date": key, "sessions": 0, "key_events": 0, "google": 0, "linkedin": 0, "meta": 0},
            )
            bucket["sessions"] += int(day.get("sessions") or 0)
            bucket["key_events"] += int(day.get("key_events") or 0)
            bucket[platform] = int(day.get("sessions") or 0)

    combined_daily: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        combined_daily.append(
            combined_by_date.get(key)
            or {
                "metric_date": key,
                "sessions": 0,
                "key_events": 0,
                "google": 0,
                "linkedin": 0,
                "meta": 0,
            }
        )
        cursor += timedelta(days=1)

    return {
        "client_key": target.client_key,
        "account_id": target.account_id,
        "bq_project_id": target.bq_project_id,
        "bq_dataset_id": target.bq_dataset_id,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": METHODOLOGY,
        "key_event_names": list(KEY_EVENT_NAMES),
        "platforms": platforms,
        "combined_daily": combined_daily,
    }


def normalize_campaign_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower().strip())


def build_ga4_campaign_index(
    ga4_rows: list[dict[str, Any]],
    ad_campaigns: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map ad-platform campaign id -> aggregated GA4 onsite metrics."""
    ads_by_id: dict[str, dict[str, Any]] = {
        str(c.get("id") or ""): c for c in ad_campaigns if c.get("id")
    }
    ads_by_name: dict[str, dict[str, Any]] = {}
    for campaign in ad_campaigns:
        norm = normalize_campaign_name(str(campaign.get("name") or ""))
        if norm and norm not in ads_by_name:
            ads_by_name[norm] = campaign

    index: dict[str, dict[str, Any]] = {}
    for row in ga4_rows:
        cid = str(row.get("campaign_id") or "").strip()
        cname = str(row.get("campaign_name") or "")
        ads = ads_by_id.get(cid) if cid else None
        if not ads and cname:
            ads = ads_by_name.get(normalize_campaign_name(cname))
        if not ads:
            continue
        aid = str(ads.get("id") or "")
        if not aid:
            continue
        bucket = index.setdefault(
            aid,
            {"sessions": 0, "engaged_sessions": 0, "key_events": 0, "page_views": 0},
        )
        bucket["sessions"] += int(row.get("sessions") or 0)
        bucket["engaged_sessions"] += int(row.get("engaged_sessions") or 0)
        bucket["key_events"] += int(row.get("key_events") or 0)
        bucket["page_views"] += int(row.get("page_views") or 0)

    for bucket in index.values():
        sessions = int(bucket["sessions"])
        engaged = int(bucket["engaged_sessions"])
        bucket["engagement_rate"] = round(engaged / sessions, 4) if sessions else 0.0
    return index


_LEVEL_ID_FIELD: dict[str, str] = {
    "campaign": "campaign_id",
    "adset": "adset_id",
    "ad": "ad_id",
}


def build_ga4_level_index(
    ga4_entity_rows: list[dict[str, Any]],
    ad_entities: list[dict[str, Any]],
    *,
    level: str,
) -> dict[str, dict[str, Any]]:
    """Map an ad-platform entity id -> aggregated GA4 onsite metrics, matched by exact id.

    Works at campaign / adset / ad level using the per-(campaign,adset,ad) GA4 rows
    (report["by_entity"]). Matching is id-only: Meta stamps the real campaign/adset/ad
    object ids into utm_id/utm_term/utm_content, so the GA4 id joins straight to the
    ad-platform entity id — no fragile name matching. Rows whose id has no matching
    ad entity (untagged / stale) are dropped so we never invent attribution.
    """
    id_field = _LEVEL_ID_FIELD.get(level)
    if not id_field:
        raise ValueError(f"Unknown level '{level}'. Expected campaign/adset/ad.")
    known_ids = {str(e.get("id") or "").strip() for e in ad_entities if e.get("id")}
    index: dict[str, dict[str, Any]] = {}
    for row in ga4_entity_rows:
        eid = str(row.get(id_field) or "").strip()
        if not eid or eid not in known_ids:
            continue
        bucket = index.setdefault(
            eid,
            {"sessions": 0, "engaged_sessions": 0, "key_events": 0, "page_views": 0},
        )
        bucket["sessions"] += int(row.get("sessions") or 0)
        bucket["engaged_sessions"] += int(row.get("engaged_sessions") or 0)
        bucket["key_events"] += int(row.get("key_events") or 0)
        bucket["page_views"] += int(row.get("page_views") or 0)
    for bucket in index.values():
        sessions = int(bucket["sessions"])
        engaged = int(bucket["engaged_sessions"])
        bucket["engagement_rate"] = round(engaged / sessions, 4) if sessions else 0.0
    return index


def match_ga4_campaigns_to_ads(
    ga4_rows: list[dict[str, Any]],
    ad_campaigns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join GA4 campaign rows to ad platform campaigns by id, then normalized name."""
    ads_by_id: dict[str, dict[str, Any]] = {
        str(c.get("id") or ""): c for c in ad_campaigns if c.get("id")
    }
    ads_by_name: dict[str, dict[str, Any]] = {}
    for campaign in ad_campaigns:
        norm = normalize_campaign_name(str(campaign.get("name") or ""))
        if norm and norm not in ads_by_name:
            ads_by_name[norm] = campaign

    merged: dict[str, dict[str, Any]] = {}

    def _ensure(key: str, *, campaign_id: str, campaign_name: str) -> dict[str, Any]:
        return merged.setdefault(
            key,
            {
                "campaign_id": campaign_id,
                "campaign_name": campaign_name or "—",
                "attribution_tier": "",
                "sessions": 0,
                "engaged_sessions": 0,
                "page_views": 0,
                "key_events": 0,
                "spend": 0.0,
                "ad_clicks": 0,
            },
        )

    for row in ga4_rows:
        cid = str(row.get("campaign_id") or "").strip()
        cname = str(row.get("campaign_name") or "")
        ads = ads_by_id.get(cid) if cid else None
        if not ads and cname:
            ads = ads_by_name.get(normalize_campaign_name(cname))
        merge_key = str(ads.get("id") if ads else cid or normalize_campaign_name(cname) or cname)
        bucket = _ensure(merge_key, campaign_id=cid or str(ads.get("id") if ads else ""), campaign_name=cname)
        bucket["sessions"] += int(row.get("sessions") or 0)
        bucket["engaged_sessions"] += int(row.get("engaged_sessions") or 0)
        bucket["page_views"] += int(row.get("page_views") or 0)
        bucket["key_events"] += int(row.get("key_events") or 0)
        if cname and cname != "—":
            bucket["campaign_name"] = cname

    for cid, ads in ads_by_id.items():
        bucket = _ensure(
            cid,
            campaign_id=cid,
            campaign_name=str(ads.get("name") or "—"),
        )
        bucket["spend"] = float(ads.get("spend") or 0)
        bucket["ad_clicks"] = int(ads.get("clicks") or 0)
        if ads.get("name"):
            bucket["campaign_name"] = str(ads.get("name"))

    out: list[dict[str, Any]] = []
    for row in merged.values():
        sessions = int(row["sessions"])
        engaged = int(row["engaged_sessions"])
        row["engagement_rate"] = round(engaged / sessions, 4) if sessions else 0.0
        out.append(row)
    out.sort(key=lambda r: (r.get("spend", 0), r.get("sessions", 0)), reverse=True)
    return out


def fetch_attribution_for_dashboard(
    *,
    date_range: str = "LAST_30_DAYS",
    client_key: str | None = None,
) -> dict[str, Any]:
    target = resolve_target(client_key=client_key)
    start, end, preset = resolve_date_range(date_range)
    report = fetch_paid_media_attribution(start=start, end=end, target=target)
    report["date_range"]["preset"] = preset
    return report


def _landing_pages_sql_suffix(
    *,
    table: str,
    suffix_start: str,
    suffix_end: str,
    row_limit: int,
    client_key: str | None = None,
) -> str:
    key_events = _key_events_sql_list(client_key)
    return f"""
    , page_view_events AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        PARSE_DATE('%Y%m%d', event_date) AS metric_date,
        event_timestamp,
        event_name,
        COALESCE(
          NULLIF(TRIM((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_path')), ''),
          REGEXP_EXTRACT(
            (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
            r'https?://[^/]+(/[^?#]*)'
          )
        ) AS page_path,
        COALESCE(
          NULLIF(TRIM((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_title')), ''),
          '(not set)'
        ) AS page_title,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec') AS engagement_time_msec
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{suffix_start}' AND '{suffix_end}'
        AND (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NOT NULL
        AND event_name IN ('page_view', {key_events})
    ),
    first_landing AS (
      SELECT
        user_pseudo_id,
        ga_session_id,
        metric_date,
        page_path,
        page_title
      FROM (
        SELECT
          user_pseudo_id,
          ga_session_id,
          metric_date,
          page_path,
          page_title,
          ROW_NUMBER() OVER (
            PARTITION BY user_pseudo_id, ga_session_id, metric_date
            ORDER BY event_timestamp ASC
          ) AS rn
        FROM page_view_events
        WHERE event_name = 'page_view'
          AND IFNULL(page_path, '') != ''
      )
      WHERE rn = 1
    ),
    landing_session_metrics AS (
      SELECT
        s.platform,
        s.attribution_tier,
        s.campaign_name,
        fl.page_path,
        fl.page_title,
        s.user_pseudo_id,
        s.ga_session_id,
        s.metric_date,
        COUNTIF(e.event_name = 'page_view') AS page_views,
        COALESCE(MAX(e.engagement_time_msec), 0) AS max_engagement_time_msec,
        COUNTIF(e.event_name IN ({key_events})) AS key_events,
        COUNTIF(e.event_name = 'user_engagement') AS user_engagement_events
      FROM paid_sessions s
      INNER JOIN first_landing fl
        ON s.user_pseudo_id = fl.user_pseudo_id
        AND s.ga_session_id = fl.ga_session_id
        AND s.metric_date = fl.metric_date
      LEFT JOIN page_view_events e
        ON s.user_pseudo_id = e.user_pseudo_id
        AND s.ga_session_id = e.ga_session_id
        AND s.metric_date = e.metric_date
      GROUP BY
        s.platform,
        s.attribution_tier,
        s.campaign_name,
        fl.page_path,
        fl.page_title,
        s.user_pseudo_id,
        s.ga_session_id,
        s.metric_date
    )
    SELECT
      platform,
      campaign_name,
      page_path,
      page_title,
      COUNT(*) AS sessions,
      COUNT(DISTINCT user_pseudo_id) AS users,
      COUNTIF(
        max_engagement_time_msec >= 10000
        OR page_views >= 2
        OR key_events >= 1
        OR user_engagement_events >= 1
      ) AS engaged_sessions,
      SUM(page_views) AS page_views,
      SUM(key_events) AS key_events
    FROM landing_session_metrics
    GROUP BY platform, campaign_name, page_path, page_title
    ORDER BY platform, sessions DESC
    LIMIT {row_limit}
    """


def fetch_landing_page_rows(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Granular paid landing page rows (platform, campaign, path) from BigQuery."""
    target = target or resolve_target()
    _ensure_bq_ready(credentials_env=target.credentials_env)
    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = _events_table(target)
    row_limit = max(1, min(int(limit) * 6, 12000))
    sql = _attribution_base_sql(
        table, suffix_start, suffix_end, target.client_key
    ) + _landing_pages_sql_suffix(
        table=table,
        suffix_start=suffix_start,
        suffix_end=suffix_end,
        row_limit=row_limit,
        client_key=target.client_key,
    )
    return run_query(sql, max_rows=row_limit, project_id=target.bq_project_id, credentials_env=target.credentials_env)


LANDING_PAGE_ATTRIBUTION_LABELS: dict[str, str] = {
    "google": "Google Ads landing pages (gclid / linked campaigns)",
    "linkedin": "LinkedIn landing pages (li_fat_id / UTM)",
    "meta": "Meta landing pages (fbclid / UTM)",
}


# Backward-compatible alias
fetch_google_ads_attribution = fetch_paid_media_attribution
