"""Google Business Profile API client — local performance metrics and reviews.

What this covers, and why it's worth having: for a client with physical
locations, the actions people take straight off the Google listing — calling,
tapping directions, clicking through to the site — never reach GA4 as anything
useful. They happen on Google's surface, not the client's. This is the only
place those numbers exist.

Three separate Google APIs are involved, each with its own host:

  * ``mybusinessaccountmanagement`` — which accounts this token can see.
  * ``mybusinessbusinessinformation`` — the locations under an account.
    ``readMask`` is mandatory here; omitting it is a 400, not a default.
  * ``businessprofileperformance`` — the daily metric time series.

Reviews are the exception: they only exist on the **legacy v4** endpoint
(``mybusiness.googleapis.com/v4``), which Google no longer publishes a discovery
document for. Everything else in this module was written against the live
discovery docs; the review parsing is written defensively for that reason, and a
review failure is never allowed to fail a sync that got its metrics.

## Access is gated, and the error doesn't say so

These APIs are not on by default. A new Google Cloud project has a quota of
**zero** for them until Google approves an access application, and until then
every call fails with **429**, which reads like rate limiting and is not. That
one fact costs people days, so ``_is_access_not_approved`` detects it and the
connector reports it in plain words instead of retrying a quota that will never
refill. Applying is a form plus a verified profile that has been active a
couple of months; approval takes days to weeks.

Auth is per-client OAuth (the ``google_business`` platform in oauth_flows) with
the ``business.manage`` scope — one scope covers all four hosts. It reuses the
Google Ads OAuth client like the GSC/GA4/GTM connectors do, so there is no new
app to register.

Optional env vars:
    GOOGLE_BUSINESS_TIMEOUT — per-request timeout in seconds (default 60).
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_ACCOUNTS_HOST = "https://mybusinessaccountmanagement.googleapis.com/v1"
_INFO_HOST = "https://mybusinessbusinessinformation.googleapis.com/v1"
_PERF_HOST = "https://businessprofileperformance.googleapis.com/v1"
_LEGACY_HOST = "https://mybusiness.googleapis.com/v4"

# Locations.list rejects a request with no readMask. Keep this to the fields the
# dashboard actually shows — a wide mask is slower and can trip permission
# errors on fields the token isn't scoped for.
_LOCATION_READ_MASK = "name,title,storefrontAddress,websiteUri,phoneNumbers,metadata"

# The daily metrics we store, paired with the snake_case column each becomes.
# Impressions arrive split four ways (Maps/Search x desktop/mobile); we keep the
# split so a client can see whether they're found on Maps or in Search, and sum
# them for the headline.
DAILY_METRICS: tuple[tuple[str, str], ...] = (
    ("BUSINESS_IMPRESSIONS_DESKTOP_MAPS", "impressions_desktop_maps"),
    ("BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", "impressions_desktop_search"),
    ("BUSINESS_IMPRESSIONS_MOBILE_MAPS", "impressions_mobile_maps"),
    ("BUSINESS_IMPRESSIONS_MOBILE_SEARCH", "impressions_mobile_search"),
    ("BUSINESS_CONVERSATIONS", "conversations"),
    ("BUSINESS_DIRECTION_REQUESTS", "direction_requests"),
    ("CALL_CLICKS", "call_clicks"),
    ("WEBSITE_CLICKS", "website_clicks"),
    ("BUSINESS_BOOKINGS", "bookings"),
    ("BUSINESS_FOOD_ORDERS", "food_orders"),
)

METRIC_COLUMNS: tuple[str, ...] = tuple(col for _, col in DAILY_METRICS)

# The four impression columns that make up "how many people saw this listing".
IMPRESSION_COLUMNS: tuple[str, ...] = (
    "impressions_desktop_maps",
    "impressions_desktop_search",
    "impressions_mobile_maps",
    "impressions_mobile_search",
)

# The actions that count as someone doing something about the listing.
ACTION_COLUMNS: tuple[str, ...] = (
    "call_clicks",
    "website_clicks",
    "direction_requests",
    "conversations",
    "bookings",
    "food_orders",
)

# Google's own metrics only go back 18 months, and the API refuses a longer
# range outright rather than clamping it.
MAX_LOOKBACK_DAYS = 540

_STAR_RATINGS: dict[str, int] = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
}


def _timeout() -> float:
    try:
        return max(10.0, float(os.getenv("GOOGLE_BUSINESS_TIMEOUT") or 60))
    except (TypeError, ValueError):
        return 60.0


class GoogleBusinessAccessNotApproved(RuntimeError):
    """The Google Cloud project has no Business Profile quota yet.

    Distinct from ordinary rate limiting: no amount of waiting fixes it, so
    callers should surface it as a setup step rather than retrying.
    """


def _is_access_not_approved(resp: httpx.Response) -> bool:
    """Tell "you were never granted access" apart from "you're going too fast".

    Both are 429. The unapproved case says the *limit itself* is zero, which
    shows up in the message as a rate-limit/quota-exceeded reason against a
    project with no allowance. A genuinely throttled caller has a non-zero limit
    and a Retry-After worth honouring, so we only claim "not approved" when
    Google's message points at the quota being absent.
    """
    if resp.status_code != 429:
        return False
    try:
        message = ((resp.json() or {}).get("error") or {}).get("message") or ""
    except Exception:
        message = resp.text or ""
    lowered = message.lower()
    return any(
        hint in lowered
        for hint in ("quota", "limit: 0", "not been used", "disabled", "before or it is disabled")
    )


def _access_token(refresh_token: str) -> str:
    """Mint an access token from a stored refresh token.

    Reuses the GA4 helper so every Google connector refreshes the same way
    against the same OAuth client.
    """
    from ga4_reporting_service import _get_access_token

    return _get_access_token(refresh_token)


def _get(http: httpx.Client, url: str, access_token: str, params: Any = None) -> dict[str, Any]:
    """GET a Business Profile endpoint and return parsed JSON.

    Raises GoogleBusinessAccessNotApproved for the zero-quota 429, PermissionError
    for a real 403, and RuntimeError otherwise.
    """
    resp = http.get(url, params=params, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code == 429 and _is_access_not_approved(resp):
        raise GoogleBusinessAccessNotApproved(
            "Google has not approved Business Profile API access for this Google Cloud "
            "project yet — its quota is still zero. Submit the Business Profile API "
            "access request and re-run this sync once Google approves it."
        )
    if resp.status_code == 403:
        raise PermissionError(
            f"Google Business Profile denied this request ({resp.status_code}): {resp.text[:300]}"
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Google Business Profile API error ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json() or {}


# ---------------------------------------------------------------------------
# Accounts and locations
# ---------------------------------------------------------------------------

def list_accounts(refresh_token: str) -> list[dict[str, Any]]:
    """Every Business Profile account this token can administer."""
    token = _access_token(refresh_token)
    out: list[dict[str, Any]] = []
    page_token = ""
    with httpx.Client(timeout=_timeout()) as http:
        while True:
            params = {"pageSize": 20}
            if page_token:
                params["pageToken"] = page_token
            data = _get(http, f"{_ACCOUNTS_HOST}/accounts", token, params)
            for acct in data.get("accounts") or []:
                out.append({
                    # "accounts/123456789"
                    "id": acct.get("name") or "",
                    "name": acct.get("accountName") or acct.get("name") or "",
                    "status": (acct.get("verificationState") or "").lower() or "ok",
                })
            page_token = data.get("nextPageToken") or ""
            if not page_token:
                break
    return out


def list_locations(refresh_token: str, account: str) -> list[dict[str, Any]]:
    """Locations under `account` ("accounts/123"), newest API, readMask required."""
    token = _access_token(refresh_token)
    out: list[dict[str, Any]] = []
    page_token = ""
    with httpx.Client(timeout=_timeout()) as http:
        while True:
            params: dict[str, Any] = {"readMask": _LOCATION_READ_MASK, "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            data = _get(http, f"{_INFO_HOST}/{account}/locations", token, params)
            for loc in data.get("locations") or []:
                address = loc.get("storefrontAddress") or {}
                lines = list(address.get("addressLines") or [])
                city = address.get("locality") or ""
                region = address.get("administrativeArea") or ""
                out.append({
                    # "locations/123" — the id the performance API wants.
                    "id": loc.get("name") or "",
                    "name": loc.get("title") or loc.get("name") or "",
                    "address": ", ".join([p for p in (*lines, city, region) if p]),
                    "website": loc.get("websiteUri") or "",
                    "status": "ok",
                })
            page_token = data.get("nextPageToken") or ""
            if not page_token:
                break
    return out


# ---------------------------------------------------------------------------
# Daily performance metrics
# ---------------------------------------------------------------------------

def _date_params(prefix: str, day: date) -> dict[str, int]:
    return {
        f"{prefix}.year": day.year,
        f"{prefix}.month": day.month,
        f"{prefix}.day": day.day,
    }


def _iso(node: dict[str, Any] | None) -> str | None:
    """Google returns {year, month, day} objects rather than ISO strings."""
    if not isinstance(node, dict):
        return None
    try:
        return date(int(node["year"]), int(node["month"]), int(node["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def fetch_daily_metrics(
    refresh_token: str,
    location: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Daily metrics for one location, as one row per date.

    Returns ``[{"metric_date": "2026-01-01", "call_clicks": 3, ...}, ...]`` with
    every metric column present (0 when Google reports nothing). Google omits
    ``value`` entirely on a zero day, so absent is read as 0 — a real zero, not
    missing data, since the location was live and simply had no activity.
    """
    if end < start:
        start, end = end, start
    earliest = date.today() - timedelta(days=MAX_LOOKBACK_DAYS)
    if start < earliest:
        # The API errors on an over-long range instead of clamping it.
        start = earliest

    token = _access_token(refresh_token)
    params: list[tuple[str, Any]] = [("dailyMetrics", m) for m, _ in DAILY_METRICS]
    for key, value in _date_params("dailyRange.startDate", start).items():
        params.append((key, value))
    for key, value in _date_params("dailyRange.endDate", end).items():
        params.append((key, value))

    with httpx.Client(timeout=_timeout()) as http:
        data = _get(
            http,
            f"{_PERF_HOST}/{location}:fetchMultiDailyMetricsTimeSeries",
            token,
            params,
        )

    # Response nests three deep: multiDailyMetricTimeSeries[] ->
    # dailyMetricTimeSeries[] -> timeSeries.datedValues[]. Pivot it to one row
    # per date so it lands in BigQuery as a normal daily fact table.
    by_date: dict[str, dict[str, Any]] = {}
    metric_column = dict(DAILY_METRICS)
    for multi in data.get("multiDailyMetricTimeSeries") or []:
        for series in (multi or {}).get("dailyMetricTimeSeries") or []:
            column = metric_column.get(series.get("dailyMetric") or "")
            if not column:
                continue
            for point in ((series.get("timeSeries") or {}).get("datedValues") or []):
                day = _iso((point or {}).get("date"))
                if not day:
                    continue
                row = by_date.setdefault(day, {"metric_date": day})
                # int64 arrives as a string, and is omitted entirely for zero.
                try:
                    row[column] = int(point.get("value") or 0)
                except (TypeError, ValueError):
                    row[column] = 0

    rows: list[dict[str, Any]] = []
    for day in sorted(by_date):
        row = by_date[day]
        for column in METRIC_COLUMNS:
            row.setdefault(column, 0)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Reviews (legacy v4 — no discovery document published)
# ---------------------------------------------------------------------------

def fetch_reviews(
    refresh_token: str,
    account: str,
    location: str,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Reviews for one location, newest first, plus the rating summary.

    ``location`` is the bare "locations/123" name; v4 wants it nested under the
    account, so the two are recombined here.

    This is the one call in this module against the legacy v4 API, which has no
    published discovery document — the parsing below tolerates missing fields
    rather than assuming the documented shape holds.
    """
    token = _access_token(refresh_token)
    location_id = location.split("/")[-1]
    account_id = account.split("/")[-1]
    url = f"{_LEGACY_HOST}/accounts/{account_id}/locations/{location_id}/reviews"

    reviews: list[dict[str, Any]] = []
    average, total = None, None
    page_token = ""
    with httpx.Client(timeout=_timeout()) as http:
        while len(reviews) < limit:
            params: dict[str, Any] = {"pageSize": min(50, limit - len(reviews))}
            if page_token:
                params["pageToken"] = page_token
            data = _get(http, url, token, params)
            if average is None:
                average = data.get("averageRating")
                total = data.get("totalReviewCount")
            page = data.get("reviews") or []
            if not page:
                # A page token that keeps returning nothing would spin forever;
                # no reviews means there is nothing further to page through.
                break
            for review in page:
                reviewer = review.get("reviewer") or {}
                reply = review.get("reviewReply") or {}
                reviews.append({
                    "review_id": review.get("reviewId") or review.get("name") or "",
                    "reviewer_name": ("Anonymous" if reviewer.get("isAnonymous")
                                      else (reviewer.get("displayName") or "")),
                    "star_rating": _STAR_RATINGS.get(review.get("starRating") or ""),
                    "comment": review.get("comment") or "",
                    "create_time": review.get("createTime"),
                    "update_time": review.get("updateTime"),
                    "reply_comment": reply.get("comment") or "",
                    "reply_time": reply.get("updateTime"),
                })
            page_token = data.get("nextPageToken") or ""
            if not page_token:
                break

    return {
        "reviews": reviews,
        "average_rating": float(average) if average is not None else None,
        "total_review_count": int(total) if total is not None else len(reviews),
    }
