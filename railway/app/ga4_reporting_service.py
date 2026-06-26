"""GA4 Data API v1beta — pull reports and list properties via OAuth refresh token."""

from __future__ import annotations

import logging
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


# ── Per-table fetchers ────────────────────────────────────────────────────────

def fetch_sessions_daily(
    property_id: str, start: str, end: str, access_token: str
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium"],
        metrics=["sessions", "engagedSessions", "keyEvents"],
        start=start, end=end, access_token=access_token,
    )
    out = []
    for r in rows:
        out.append({
            "date": _parse_date(r.get("date", "")),
            "sessionDefaultChannelGroup": r.get("sessionDefaultChannelGroup") or "(other)",
            "sessionSource": r.get("sessionSource") or "(direct)",
            "sessionMedium": r.get("sessionMedium") or "(none)",
            "sessions": int(r.get("sessions") or 0),
            "engagedSessions": int(r.get("engagedSessions") or 0),
            "keyEvents": float(r.get("keyEvents") or 0),
        })
    return out


def fetch_tech_daily(
    property_id: str, start: str, end: str, access_token: str
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "deviceCategory"],
        metrics=["activeUsers", "engagedSessions", "keyEvents"],
        start=start, end=end, access_token=access_token,
    )
    out = []
    for r in rows:
        out.append({
            "date": _parse_date(r.get("date", "")),
            "deviceCategory": r.get("deviceCategory") or "unknown",
            "activeUsers": int(r.get("activeUsers") or 0),
            "engagedSessions": int(r.get("engagedSessions") or 0),
            "keyEvents": float(r.get("keyEvents") or 0),
        })
    return out


def fetch_pages_daily(
    property_id: str, start: str, end: str, access_token: str
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "landingPage"],
        metrics=["sessions", "activeUsers", "newUsers", "keyEvents", "averageSessionDuration"],
        start=start, end=end, access_token=access_token,
    )
    out = []
    for r in rows:
        out.append({
            "date": _parse_date(r.get("date", "")),
            "landingPage": r.get("landingPage") or "/",
            "sessions": int(r.get("sessions") or 0),
            "activeUsers": int(r.get("activeUsers") or 0),
            "newUsers": int(r.get("newUsers") or 0),
            "keyEvents": float(r.get("keyEvents") or 0),
            # stored under the name the queries expect
            "userEngagementDurationPerSession": float(r.get("averageSessionDuration") or 0),
        })
    return out


def fetch_events_daily(
    property_id: str, start: str, end: str, access_token: str
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "eventName"],
        metrics=["eventCount", "totalUsers", "eventCountPerUser"],
        start=start, end=end, access_token=access_token,
    )
    out = []
    for r in rows:
        out.append({
            "date": _parse_date(r.get("date", "")),
            "eventName": r.get("eventName") or "(unknown)",
            "eventCount": int(r.get("eventCount") or 0),
            "totalUsers": int(r.get("totalUsers") or 0),
            "eventCountPerUser": float(r.get("eventCountPerUser") or 0),
        })
    return out


def fetch_user_acq_daily(
    property_id: str, start: str, end: str, access_token: str
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "firstUserDefaultChannelGroup", "firstUserSource", "firstUserMedium"],
        metrics=["newUsers", "activeUsers", "keyEvents", "totalUsers"],
        start=start, end=end, access_token=access_token,
    )
    out = []
    for r in rows:
        out.append({
            "date": _parse_date(r.get("date", "")),
            "firstUserDefaultChannelGroup": r.get("firstUserDefaultChannelGroup") or "(other)",
            "firstUserSource": r.get("firstUserSource") or "(direct)",
            "firstUserMedium": r.get("firstUserMedium") or "(none)",
            "newUsers": int(r.get("newUsers") or 0),
            "activeUsers": int(r.get("activeUsers") or 0),
            "keyEvents": float(r.get("keyEvents") or 0),
            "totalUsers": int(r.get("totalUsers") or 0),
        })
    return out


def fetch_demographics_daily(
    property_id: str, start: str, end: str, access_token: str
) -> list[dict[str, Any]]:
    rows = _run_report(
        property_id,
        dimensions=["date", "city", "region", "userAgeBracket", "userGender"],
        metrics=["activeUsers", "keyEvents", "engagementRate"],
        start=start, end=end, access_token=access_token,
    )
    out = []
    for r in rows:
        out.append({
            "date": _parse_date(r.get("date", "")),
            "city": r.get("city") or "(not set)",
            "region": r.get("region") or "",
            "userAgeBracket": r.get("userAgeBracket") or "(not set)",
            "userGender": r.get("userGender") or "(not set)",
            "activeUsers": int(r.get("activeUsers") or 0),
            "keyEvents": float(r.get("keyEvents") or 0),
            "engagementRate": float(r.get("engagementRate") or 0),
        })
    return out
