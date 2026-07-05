"""GA4 Data API v1beta — pull reports and list properties via OAuth refresh token.

This is GA4 Data API *report-level* ingestion — aggregated daily metrics by
dimension combinations. It is NOT the native GA4 BigQuery event-level export
(events_* tables from ga4_export). Column names and available dimensions differ
from the BQ export schema. Do not join these tables to event-level data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

import auth as _google_auth

_log = logging.getLogger(__name__)

_GA4_DATA_BASE = "https://analyticsdata.googleapis.com/v1beta/properties"
_GA4_ADMIN_URL = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_access_token(refresh_token: str) -> str:
    client_id = _google_auth._get_required_env(*_google_auth._ENV_ALIASES["client_id"])
    client_secret = _google_auth._get_required_env(*_google_auth._ENV_ALIASES["client_secret"])
    with httpx.Client(timeout=30) as http:
        resp = http.post(_GOOGLE_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Google token refresh failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()["access_token"]


def list_properties(refresh_token: str) -> list[dict[str, Any]]:
    """Return all GA4 properties the token can access."""
    access_token = _get_access_token(refresh_token)
    with httpx.Client(timeout=30) as http:
        resp = http.get(
            _GA4_ADMIN_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"pageSize": 200},
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"GA4 Admin API error ({resp.status_code}): {resp.text[:300]}"
        )
    properties: list[dict[str, Any]] = []
    for account in resp.json().get("accountSummaries", []):
        acct_name = account.get("displayName", "")
        for prop in account.get("propertySummaries", []):
            prop_id = (prop.get("property") or "").removeprefix("properties/")
            if not prop_id:
                continue
            prop_name = prop.get("displayName", prop_id)
            properties.append({
                "id": prop_id,
                "name": f"{prop_name} — {acct_name}" if acct_name else prop_name,
            })
    return properties


def _run_report(
    property_id: str,
    *,
    dimensions: list[str],
    metrics: list[str],
    start: str,
    end: str,
    access_token: str,
) -> list[dict[str, Any]]:
    """Single-property runReport with automatic pagination (up to 1M rows)."""
    url = f"{_GA4_DATA_BASE}/{property_id}:runReport"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    all_rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100_000

    while True:
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "limit": limit,
            "offset": offset,
            "keepEmptyRows": False,
        }
        with httpx.Client(timeout=120) as http:
            resp = http.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"GA4 Data API error ({resp.status_code}): {resp.text[:400]}"
            )
        data = resp.json()
        dim_headers = [h["name"] for h in data.get("dimensionHeaders", [])]
        met_headers = [h["name"] for h in data.get("metricHeaders", [])]
        page_rows = data.get("rows", [])
        for row in page_rows:
            record: dict[str, Any] = {}
            for i, val in enumerate(row.get("dimensionValues", [])):
                record[dim_headers[i]] = val.get("value")
            for i, val in enumerate(row.get("metricValues", [])):
                record[met_headers[i]] = val.get("value")
            all_rows.append(record)

        total = int(data.get("rowCount") or 0)
        offset += len(page_rows)
        if offset >= total or not page_rows:
            break

    _log.info(
        "GA4 report: property=%s dims=%s start=%s end=%s rows=%d",
        property_id, dimensions[:2], start, end, len(all_rows),
    )
    return all_rows


def _parse_date(yyyymmdd: str) -> str:
    """Convert GA4 date dimension value YYYYMMDD → YYYY-MM-DD."""
    s = (yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Per-table fetchers ────────────────────────────────────────────────────────
# All output dicts use snake_case keys matching BQ column names.
# Every row includes client_key, property_id, and synced_at for multi-tenant
# idempotency and audit.

def fetch_sessions_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=[
            "date", "sessionDefaultChannelGroup", "sessionSource",
            "sessionMedium", "sessionCampaignName",
        ],
        metrics=[
            "sessions", "engagedSessions", "keyEvents",
            "totalUsers", "newUsers", "bounceRate", "engagementRate", "screenPageViews",
        ],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "session_default_channel_group": r.get("sessionDefaultChannelGroup") or "(other)",
            "session_source": r.get("sessionSource") or "(direct)",
            "session_medium": r.get("sessionMedium") or "(none)",
            "session_campaign_name": r.get("sessionCampaignName") or "(not set)",
            "sessions": int(r.get("sessions") or 0),
            "engaged_sessions": int(r.get("engagedSessions") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "total_users": int(r.get("totalUsers") or 0),
            "new_users": int(r.get("newUsers") or 0),
            "bounce_rate": float(r.get("bounceRate") or 0),
            "engagement_rate": float(r.get("engagementRate") or 0),
            "screen_page_views": int(r.get("screenPageViews") or 0),
            "synced_at": synced_at,
        })
    return out


def fetch_landing_pages_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "landingPage"],
        metrics=["sessions", "activeUsers", "newUsers", "keyEvents", "averageSessionDuration"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "landing_page": r.get("landingPage") or "/",
            "sessions": int(r.get("sessions") or 0),
            "active_users": int(r.get("activeUsers") or 0),
            "new_users": int(r.get("newUsers") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "average_session_duration": float(r.get("averageSessionDuration") or 0),
            "synced_at": synced_at,
        })
    return out


def fetch_landing_page_events_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    """Landing page × event breakdown. Powers a client-selectable "which events
    count as key events" override for the Landing Pages panel. `key_events` is
    GA4's own key-event count per event (nonzero only for designated key events),
    used as the default set; `event_count` lets the dashboard sum any custom
    selection of events."""
    rows = _run_report(
        property_id,
        dimensions=["date", "landingPage", "eventName"],
        metrics=["eventCount", "keyEvents"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "landing_page": r.get("landingPage") or "/",
            "event_name": r.get("eventName") or "(unknown)",
            "event_count": int(r.get("eventCount") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "synced_at": synced_at,
        })
    return out


def fetch_user_acq_events_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    """First-user channel/source/medium × event breakdown — the user-acquisition
    equivalent of the landing-page events report, so the User Acquisition panel's
    key-events column can honour a client-selected event set. (Traffic uses the
    session-scoped ga4_events_daily; user acquisition is first-user attributed,
    so it needs its own firstUser* dimensions.)"""
    rows = _run_report(
        property_id,
        dimensions=["date", "firstUserDefaultChannelGroup", "firstUserSource",
                    "firstUserMedium", "eventName"],
        metrics=["eventCount", "keyEvents"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "default_channel_group": r.get("firstUserDefaultChannelGroup") or "(not set)",
            "source": r.get("firstUserSource") or "(direct)",
            "medium": r.get("firstUserMedium") or "(none)",
            "event_name": r.get("eventName") or "(unknown)",
            "event_count": int(r.get("eventCount") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "synced_at": synced_at,
        })
    return out


def fetch_pageviews_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "pagePath", "pageTitle"],
        metrics=["screenPageViews", "activeUsers", "newUsers", "keyEvents", "averageSessionDuration"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "page_path": r.get("pagePath") or "/",
            "page_title": r.get("pageTitle") or "(not set)",
            "screen_page_views": int(r.get("screenPageViews") or 0),
            "active_users": int(r.get("activeUsers") or 0),
            "new_users": int(r.get("newUsers") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "average_session_duration": float(r.get("averageSessionDuration") or 0),
            "synced_at": synced_at,
        })
    return out


def fetch_page_source_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    """Page performance broken out by traffic source.

    This is a cross-dimension report (page × source). The GA4 Data API supports
    this combination, but cardinality can be very high for large properties.
    If the API rejects the combination, returns [] and logs a warning instead of
    raising — the table will be empty for this sync but the rest continue.

    IMPORTANT: Do not simulate page-by-source data by joining ga4_pageviews_daily
    to ga4_sessions_daily on date. That creates false attribution.
    """
    try:
        rows = _run_report(
            property_id,
            dimensions=[
                "date", "pagePath", "pageTitle",
                "sessionDefaultChannelGroup", "sessionSource",
                "sessionMedium", "sessionCampaignName",
            ],
            metrics=[
                "sessions", "activeUsers", "screenPageViews",
                "engagedSessions", "keyEvents", "userEngagementDuration",
            ],
            start=start, end=end, access_token=access_token,
        )
    except RuntimeError as exc:
        _log.warning(
            "GA4 page_source_daily skipped — API rejected dimension/metric combination "
            "or returned error. Table will be empty for this sync. Error: %s", exc,
        )
        return []
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "page_path": r.get("pagePath") or "/",
            "page_title": r.get("pageTitle") or "(not set)",
            "session_default_channel_group": r.get("sessionDefaultChannelGroup") or "(other)",
            "session_source": r.get("sessionSource") or "(direct)",
            "session_medium": r.get("sessionMedium") or "(none)",
            "session_campaign_name": r.get("sessionCampaignName") or "(not set)",
            "sessions": int(r.get("sessions") or 0),
            "active_users": int(r.get("activeUsers") or 0),
            "screen_page_views": int(r.get("screenPageViews") or 0),
            "engaged_sessions": int(r.get("engagedSessions") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "user_engagement_duration": float(r.get("userEngagementDuration") or 0),
            "synced_at": synced_at,
        })
    return out


def fetch_events_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=[
            "date", "eventName",
            "sessionDefaultChannelGroup", "sessionSource",
            "sessionMedium", "sessionCampaignName",
        ],
        metrics=["eventCount", "keyEvents", "totalUsers"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "event_name": r.get("eventName") or "(unknown)",
            "session_default_channel_group": r.get("sessionDefaultChannelGroup") or "(other)",
            "session_source": r.get("sessionSource") or "(direct)",
            "session_medium": r.get("sessionMedium") or "(none)",
            "session_campaign_name": r.get("sessionCampaignName") or "(not set)",
            "event_count": int(r.get("eventCount") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "total_users": int(r.get("totalUsers") or 0),
            "synced_at": synced_at,
        })
    return out


def fetch_tech_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "deviceCategory", "browser", "operatingSystem"],
        metrics=["activeUsers", "engagedSessions", "keyEvents"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "device_category": r.get("deviceCategory") or "unknown",
            "browser": r.get("browser") or "(not set)",
            "operating_system": r.get("operatingSystem") or "(not set)",
            "active_users": int(r.get("activeUsers") or 0),
            "engaged_sessions": int(r.get("engagedSessions") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "synced_at": synced_at,
        })
    return out


def fetch_user_acq_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "firstUserDefaultChannelGroup", "firstUserSource", "firstUserMedium"],
        metrics=["newUsers", "activeUsers", "keyEvents", "totalUsers"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "first_user_default_channel_group": r.get("firstUserDefaultChannelGroup") or "(other)",
            "first_user_source": r.get("firstUserSource") or "(direct)",
            "first_user_medium": r.get("firstUserMedium") or "(none)",
            "new_users": int(r.get("newUsers") or 0),
            "active_users": int(r.get("activeUsers") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "total_users": int(r.get("totalUsers") or 0),
            "synced_at": synced_at,
        })
    return out


def fetch_geo_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    """Geographic breakdown by country/region/city. Does not require Google Signals."""
    rows = _run_report(
        property_id,
        dimensions=["date", "country", "region", "city"],
        metrics=["activeUsers", "sessions", "keyEvents"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "country": r.get("country") or "(not set)",
            "region": r.get("region") or "(not set)",
            "city": r.get("city") or "(not set)",
            "active_users": int(r.get("activeUsers") or 0),
            "sessions": int(r.get("sessions") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "synced_at": synced_at,
        })
    return out


def fetch_demographics_daily(
    property_id: str, start: str, end: str, access_token: str,
    *, client_key: str = "",
) -> list[dict[str, Any]]:
    """Age + gender breakdown. Requires Google Signals enabled on the property."""
    rows = _run_report(
        property_id,
        dimensions=["date", "userAgeBracket", "userGender"],
        metrics=["activeUsers", "keyEvents", "engagementRate"],
        start=start, end=end, access_token=access_token,
    )
    synced_at = _now()
    out = []
    for r in rows:
        out.append({
            "client_key": client_key,
            "property_id": property_id,
            "date": _parse_date(r.get("date", "")),
            "user_age_bracket": r.get("userAgeBracket") or "(not set)",
            "user_gender": r.get("userGender") or "(not set)",
            "active_users": int(r.get("activeUsers") or 0),
            "key_events": int(float(r.get("keyEvents") or 0)),
            "engagement_rate": float(r.get("engagementRate") or 0),
            "synced_at": synced_at,
        })
    return out


# ── Report catalogue ──────────────────────────────────────────────────────────
# Documents the grain and API contract for each table. Source of truth for the
# marketing_marts view layer.

GA4_REPORTS = [
    {
        "report_key": "sessions",
        "table_name": "ga4_sessions_daily",
        "dimensions": ["date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium", "sessionCampaignName"],
        "metrics": ["sessions", "engagedSessions", "keyEvents", "totalUsers", "newUsers", "bounceRate", "engagementRate", "screenPageViews"],
        "grain": ["date", "session_default_channel_group", "session_source", "session_medium", "session_campaign_name"],
        "required": True,
        "notes": "Session-scoped traffic acquisition. One row per date × source × medium × campaign × channel group.",
    },
    {
        "report_key": "landing_pages",
        "table_name": "ga4_landing_pages_daily",
        "dimensions": ["date", "landingPage"],
        "metrics": ["sessions", "activeUsers", "newUsers", "keyEvents", "averageSessionDuration"],
        "grain": ["date", "landing_page"],
        "required": True,
        "notes": "Entry page quality. landing_page is the first page seen in a session.",
    },
    {
        "report_key": "pageviews",
        "table_name": "ga4_pageviews_daily",
        "dimensions": ["date", "pagePath", "pageTitle"],
        "metrics": ["screenPageViews", "activeUsers", "newUsers", "keyEvents", "averageSessionDuration"],
        "grain": ["date", "page_path", "page_title"],
        "required": True,
        "notes": "All-traffic page view counts. Use for content performance. Combine with page_source for attribution.",
    },
    {
        "report_key": "page_source",
        "table_name": "ga4_page_source_daily",
        "dimensions": ["date", "pagePath", "pageTitle", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium", "sessionCampaignName"],
        "metrics": ["sessions", "activeUsers", "screenPageViews", "engagedSessions", "keyEvents", "userEngagementDuration"],
        "grain": ["date", "page_path", "session_source", "session_medium", "session_campaign_name"],
        "required": False,
        "notes": "True page-by-source without date-join fabrication. High cardinality — skipped if API rejects.",
    },
    {
        "report_key": "events",
        "table_name": "ga4_events_daily",
        "dimensions": ["date", "eventName", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium", "sessionCampaignName"],
        "metrics": ["eventCount", "keyEvents", "totalUsers"],
        "grain": ["date", "event_name", "session_source", "session_medium", "session_campaign_name"],
        "required": True,
        "notes": "Events with source attribution. Filter out automatic events (page_view, session_start etc.) in views.",
    },
    {
        "report_key": "tech",
        "table_name": "ga4_tech_daily",
        "dimensions": ["date", "deviceCategory", "browser", "operatingSystem"],
        "metrics": ["activeUsers", "engagedSessions", "keyEvents"],
        "grain": ["date", "device_category", "browser", "operating_system"],
        "required": True,
        "notes": "Device / browser / OS breakdown.",
    },
    {
        "report_key": "user_acq",
        "table_name": "ga4_user_acq_daily",
        "dimensions": ["date", "firstUserDefaultChannelGroup", "firstUserSource", "firstUserMedium"],
        "metrics": ["newUsers", "activeUsers", "keyEvents", "totalUsers"],
        "grain": ["date", "first_user_default_channel_group", "first_user_source", "first_user_medium"],
        "required": True,
        "notes": "First-touch attribution. Distinct from session-scoped ga4_sessions_daily.",
    },
    {
        "report_key": "geo",
        "table_name": "ga4_geo_daily",
        "dimensions": ["date", "country", "region", "city"],
        "metrics": ["activeUsers", "sessions", "keyEvents"],
        "grain": ["date", "country", "region", "city"],
        "required": True,
        "notes": "Geographic breakdown. Does not require Google Signals.",
    },
    {
        "report_key": "demographics",
        "table_name": "ga4_demographics_daily",
        "dimensions": ["date", "userAgeBracket", "userGender"],
        "metrics": ["activeUsers", "keyEvents", "engagementRate"],
        "grain": ["date", "user_age_bracket", "user_gender"],
        "required": False,
        "notes": "Requires Google Signals enabled on the property. Empty table is expected when Signals is off.",
    },
]
