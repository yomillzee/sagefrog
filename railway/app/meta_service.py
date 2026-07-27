from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from dates_util import resolve_date_range
from meta_auth import MetaEnv, load_meta_env

_log = logging.getLogger(__name__)

# Meta's Graph edge intermittently drops connections mid-response — most often
# on the ad-creative fetch, which pages over every ad with a heavy creative{...}
# field expansion, so responses are large and slow. That surfaces as "[Errno 104]
# Connection reset by peer" (an httpx.TransportError), which used to fail the whole
# sync as meta_creative_fetch. Meta also rate-limits (429) and its edge can hiccup
# with 5xx. Retry all of those a few times with exponential backoff; non-transient
# statuses (400/403 — e.g. missing ads_read) fail fast so callers can handle them.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 2.0

# When a single page is too expensive Meta returns a 500 whose body is error
# code 1, "Please reduce the amount of data you're asking for, then retry your
# request". That is driven by page size × field-expansion cost, not a transient
# blip, so retrying the same request just fails again — _graph_get instead shrinks
# the page `limit` and retries. The heavy creative{...} fetch therefore starts at a
# modest page size and the adaptive shrink is the safety net as accounts grow.
_AD_MEDIA_PAGE_LIMIT = 50
_MIN_PAGE_LIMIT = 5

# Meta returns throttling as HTTP 400 (not 429) with one of these error codes in
# the body: 4 (app rate limit), 17 ("User request limit reached"), 32 (page rate
# limit), 613 (custom rate limit), and the 80000-80014 business-use-case codes.
# These are transient — back off and retry rather than failing the sync outright.
_RATE_LIMIT_CODES = frozenset({4, 17, 32, 613})
_RATE_LIMIT_MESSAGES = ("request limit reached", "rate limit", "calls to this api have exceeded")
# Cap per-attempt backoff so a sustained limit fails this cycle (and retries on the
# next scheduled sync) instead of blocking the worker for minutes.
_RATE_LIMIT_MAX_BACKOFF_SECONDS = 30.0

_ACCOUNT_FIELDS = "id,account_id,name,account_status,currency,business_name"
_INSIGHT_FIELDS = (
    "spend,impressions,clicks,actions,action_values,"
    "campaign_id,campaign_name,date_start,date_stop"
)
_ADSET_INSIGHT_FIELDS = (
    "spend,impressions,clicks,actions,action_values,"
    "adset_id,adset_name,campaign_id,campaign_name,date_start,date_stop"
)
_AD_INSIGHT_FIELDS = (
    "spend,impressions,clicks,actions,action_values,"
    "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,date_start,date_stop"
)
_ACCOUNT_STATUS = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
}
# ---------------------------------------------------------------------------
# Meta "Results" reconstruction
#
# Ads Manager's "Results" column is NOT a Marketing API field. For each ad set it
# is the count of that ad set's `optimization_goal` event (for conversion
# optimization, the specific pixel/custom event named in `promoted_object`),
# read from the insights `actions` array as a SINGLE de-duplicated action_type
# and rolled up. We reconstruct it here so the warehouse `conversions` column
# matches what the client sees in Ads Manager.
#
# Each ad set resolves to one or more "result tokens" (e.g. "lead", "purchase").
# A token maps to an ORDERED list of candidate action_types: Meta reports the
# same event under several redundant buckets (offsite_/onsite_/omni_/bare event),
# and summing them is exactly what turns one result into many. So per token we
# take the value of the FIRST candidate present — one family only. DISTINCT
# tokens ARE summed (a campaign optimizing both leads and purchases has
# results = leads + purchases), which mirrors how Ads Manager rolls up.
# ---------------------------------------------------------------------------
_RESULT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "purchase": (
        "offsite_conversion.fb_pixel_purchase",
        "onsite_conversion.purchase",
        "omni_purchase",
        "purchase",
    ),
    "lead": (
        "onsite_conversion.lead_grouped",
        "offsite_conversion.fb_pixel_lead",
        "leadgen_grouped",
        "leadgen.other",
        "omni_lead",
        "lead",
    ),
    "complete_registration": (
        "offsite_conversion.fb_pixel_complete_registration",
        "omni_complete_registration",
        "complete_registration",
    ),
    "submit_application": (
        "offsite_conversion.fb_pixel_submit_application",
        "submit_application",
    ),
    "add_to_cart": (
        "offsite_conversion.fb_pixel_add_to_cart",
        "onsite_conversion.add_to_cart",
        "omni_add_to_cart",
        "add_to_cart",
    ),
    "initiate_checkout": (
        "offsite_conversion.fb_pixel_initiate_checkout",
        "omni_initiated_checkout",
        "initiate_checkout",
    ),
    "view_content": (
        "offsite_conversion.fb_pixel_view_content",
        "omni_view_content",
        "view_content",
    ),
    "search": ("offsite_conversion.fb_pixel_search", "omni_search", "search"),
    "contact": ("offsite_conversion.fb_pixel_contact", "contact"),
    "add_payment_info": (
        "offsite_conversion.fb_pixel_add_payment_info",
        "add_payment_info",
    ),
    "add_to_wishlist": (
        "offsite_conversion.fb_pixel_add_to_wishlist",
        "add_to_wishlist",
    ),
    "subscribe": ("offsite_conversion.fb_pixel_subscribe", "subscribe"),
    "start_trial": ("offsite_conversion.fb_pixel_start_trial", "start_trial"),
    "donate": ("offsite_conversion.fb_pixel_donate", "donate"),
    "schedule": ("offsite_conversion.fb_pixel_schedule", "schedule"),
    "link_click": ("link_click",),
    "landing_page_view": ("landing_page_view",),
    "post_engagement": ("post_engagement",),
    "like": ("like",),
    "rsvp": ("rsvp", "event_responses"),
    "video_view": ("video_view",),
    "thruplay": ("video_thruplay_watched_actions", "video_view"),
    "app_install": ("mobile_app_install", "omni_app_install", "app_install"),
    "messaging_conversation": (
        "onsite_conversion.messaging_conversation_started_7d",
        "onsite_conversion.total_messaging_connection",
    ),
}

# Ad-set optimization_goal → result token. Awareness/reach goals (REACH,
# IMPRESSIONS, AD_RECALL_LIFT, BRAND_AWARENESS) have no action_type result, so
# they map to None → 0 conversions (correct: those campaigns legitimately report
# 0 in a conversions column). "conversion" is a placeholder refined by
# promoted_object.custom_event_type below.
_OPT_GOAL_TOKEN: dict[str, str | None] = {
    "OFFSITE_CONVERSIONS": "conversion",
    "CONVERSIONS": "conversion",
    "VALUE": "conversion",
    "LEAD_GENERATION": "lead",
    "QUALITY_LEAD": "lead",
    "QUALITY_CALL": "contact",
    "LINK_CLICKS": "link_click",
    "LANDING_PAGE_VIEWS": "landing_page_view",
    "POST_ENGAGEMENT": "post_engagement",
    "PAGE_LIKES": "like",
    "EVENT_RESPONSES": "rsvp",
    "THRUPLAY": "thruplay",
    "VIDEO_VIEWS": "video_view",
    "APP_INSTALLS": "app_install",
    "CONVERSATIONS": "messaging_conversation",
    "REACH": None,
    "IMPRESSIONS": None,
    "AD_RECALL_LIFT": None,
    "BRAND_AWARENESS": None,
}

# promoted_object.custom_event_type → result token, used when the ad set's
# optimization_goal is conversion-based (maps to "conversion" above).
_CUSTOM_EVENT_TOKEN: dict[str, str] = {
    "PURCHASE": "purchase",
    "LEAD": "lead",
    "COMPLETE_REGISTRATION": "complete_registration",
    "SUBMIT_APPLICATION": "submit_application",
    "ADD_TO_CART": "add_to_cart",
    "INITIATED_CHECKOUT": "initiate_checkout",
    "CONTENT_VIEW": "view_content",
    "VIEW_CONTENT": "view_content",
    "SEARCH": "search",
    "CONTACT": "contact",
    "ADD_PAYMENT_INFO": "add_payment_info",
    "ADD_TO_WISHLIST": "add_to_wishlist",
    "SUBSCRIBE": "subscribe",
    "START_TRIAL": "start_trial",
    "DONATE": "donate",
    "SCHEDULE": "schedule",
}

# Used when an ad set's optimization_goal can't be resolved (unknown goal, a
# custom pixel conversion with no standard custom_event_type, or the legacy paths
# that don't fetch ad-set config). Still de-duplicated PER TOKEN — unlike the old
# "sum every matching bucket" logic — so a single lead reported across four
# buckets counts once, not four times. It can still over-count a single-objective
# campaign that also fires other standard events, which is why the sync path
# resolves the real optimization_goal instead of relying on this.
_FALLBACK_TOKENS: frozenset[str] = frozenset(
    {
        "purchase",
        "lead",
        "complete_registration",
        "submit_application",
        "add_to_cart",
        "initiate_checkout",
    }
)


def _normalize_account_id(account_id: str) -> str:
    raw = str(account_id or "").strip()
    if raw.lower().startswith("act_"):
        raw = raw[4:]
    return raw.split(":")[-1]


def _act_id(account_id: str) -> str:
    clean = _normalize_account_id(account_id)
    return f"act_{clean}"


def _chunked(seq: list[str], size: int) -> list[list[str]]:
    """Split a list into consecutive chunks of at most `size` items."""
    size = max(1, size)
    return [seq[i:i + size] for i in range(0, len(seq), size)]


_ACCESS_TOKEN_RE = re.compile(r"(access_token=)[^&\s]+", re.IGNORECASE)


def _redact(text: str) -> str:
    """Mask any access_token value so credentials never reach logs or error
    strings. Pagination cursor URLs carry the token in their query string, so an
    unredacted `next` URL in an exception would leak it into the connector UI."""
    return _ACCESS_TOKEN_RE.sub(r"\1[REDACTED]", str(text))


def _display_path(path: str) -> str:
    """A short, safe identifier for a request in error/log messages: scheme +
    host + path, with the query string dropped entirely. Pagination `next` URLs
    carry a long `after` cursor (and the token) in their query — keeping it would
    both leak the credential and, because callers truncate the stored error to
    400 chars, push Meta's actual {'error': {...}} body off the end where it can't
    be read. Dropping the query keeps the message compact and diagnostic."""
    base = path.split("?", 1)[0]
    return _redact(base)


def _graph_base(env: MetaEnv) -> str:
    version = env.api_version.strip()
    if not version.startswith("v"):
        version = f"v{version}"
    return f"https://graph.facebook.com/{version}"


def _client_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def _graph_get(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    env = env or load_meta_env(access_token=access_token)
    query = dict(params or {})
    query["access_token"] = access_token
    url = path if path.startswith("http") else f"{_graph_base(env)}{path}"

    # Retry transient failures — a connection reset mid-response (TransportError)
    # or a 429/5xx — with exponential backoff. A non-transient status (e.g. 400
    # missing ads_read) raises immediately so callers keep their existing
    # handling. TransportError covers connect/read/write/protocol errors and
    # timeouts, which is where "Connection reset by peer" lands.
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.get(url, params=query, headers=_client_headers())
        except httpx.TransportError as exc:
            if attempt >= _MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Meta Graph API transport error on {_display_path(path)} after "
                    f"{_MAX_ATTEMPTS} attempts: {exc}"
                ) from exc
            wait = _RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            _log.warning(
                "Meta Graph %s transport error (%s); retrying in %.1fs (attempt %d/%d)",
                _display_path(path), exc, wait, attempt, _MAX_ATTEMPTS,
            )
            time.sleep(wait)
            continue

        # "Please reduce the amount of data..." is about request size, not a
        # transient hiccup: shrink the page limit and retry the same page rather
        # than hammering the identical oversized request until attempts run out.
        if _is_reduce_data_error(response) and attempt < _MAX_ATTEMPTS:
            current = _effective_limit(url, query)
            if current is not None and current > _MIN_PAGE_LIMIT:
                reduced = max(_MIN_PAGE_LIMIT, current // 2)
                url, query = _with_reduced_limit(url, query, reduced)
                _log.warning(
                    "Meta Graph %s over-sized (reduce-data); shrinking page limit "
                    "%d -> %d and retrying (attempt %d/%d)",
                    _display_path(path), current, reduced, attempt, _MAX_ATTEMPTS,
                )
                continue

        # Meta signals throttling with HTTP 400 + a rate-limit error code, not a
        # 429, so it slips past the status check below. Back off and retry those
        # (capped) instead of surfacing a hard sync error on a transient limit.
        if _is_rate_limit_error(response) and attempt < _MAX_ATTEMPTS:
            wait = min(
                _retry_after_seconds(response, _RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))),
                _RATE_LIMIT_MAX_BACKOFF_SECONDS,
            )
            _log.warning(
                "Meta Graph %s rate-limited; backing off %.1fs (attempt %d/%d)",
                _display_path(path), wait, attempt, _MAX_ATTEMPTS,
            )
            time.sleep(wait)
            continue

        if response.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
            wait = _retry_after_seconds(response, _RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
            _log.warning(
                "Meta Graph %s returned %d; retrying in %.1fs (attempt %d/%d)",
                _display_path(path), response.status_code, wait, attempt, _MAX_ATTEMPTS,
            )
            time.sleep(wait)
            continue

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json()
            except Exception:
                pass
            raise RuntimeError(
                f"Meta Graph API error {response.status_code} on {_display_path(path)}: {_redact(detail)}"
            )
        return response.json()

    # Unreachable: the loop either returns, raises, or continues; the final
    # attempt cannot hit a `continue` branch. Guard anyway to satisfy type checkers.
    raise RuntimeError(f"Meta Graph API request to {_display_path(path)} exhausted retries")


def _retry_after_seconds(response: httpx.Response, default: float) -> float:
    """Honour a numeric Retry-After header (delta-seconds); fall back to default."""
    try:
        return max(float(response.headers.get("Retry-After", "")), default)
    except (TypeError, ValueError):
        return default


def _is_reduce_data_error(response: httpx.Response) -> bool:
    """True for Meta's "Please reduce the amount of data you're asking for" error.

    Meta raises this (error code 1, HTTP 500) when a single page is too expensive
    to compute — typically a large `limit` combined with heavy field expansion.
    The distinctive message is the reliable signal across Graph versions.
    """
    try:
        body = response.json()
    except Exception:
        return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    return "reduce the amount of data" in str(err.get("message") or "").lower()


def _is_rate_limit_error(response: httpx.Response) -> bool:
    """True for a Meta throttling error, which arrives as HTTP 400 (not 429).

    Matches by error code — 4 (app), 17 ("User request limit reached"), 32
    (page), 613 (custom), 80000-80014 (business use case) — with a message
    fallback so a code we haven't enumerated but that plainly says "rate limit"
    is still treated as throttling.
    """
    try:
        body = response.json()
    except Exception:
        return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    try:
        code = int(err.get("code"))
    except (TypeError, ValueError):
        code = None
    if code is not None and (code in _RATE_LIMIT_CODES or 80000 <= code <= 80014):
        return True
    msg = str(err.get("message") or "").lower()
    return any(marker in msg for marker in _RATE_LIMIT_MESSAGES)


def _effective_limit(url: str, query: dict[str, Any]) -> int | None:
    """The page `limit` in effect for a request, whether it lives in the params
    dict (first page) or is baked into a pagination cursor URL (later pages)."""
    if "limit" in query:
        try:
            return int(query["limit"])
        except (TypeError, ValueError):
            return None
    for key, val in parse_qsl(urlsplit(url).query):
        if key == "limit":
            try:
                return int(val)
            except ValueError:
                return None
    return None


def _with_reduced_limit(
    url: str, query: dict[str, Any], reduced: int
) -> tuple[str, dict[str, Any]]:
    """Return (url, query) with the page limit forced to `reduced`.

    Any `limit` baked into the URL (Meta pagination cursors carry it) is stripped
    so httpx doesn't send two conflicting `limit` values; the reduced limit rides
    in the params dict instead. The cursor position (`after`) is untouched, so
    paging resumes from the same spot at the smaller size.
    """
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "limit"]
    new_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )
    new_query = dict(query)
    new_query["limit"] = reduced
    return new_url, new_query


def _is_meta_ads_read_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "ads_read" in msg
        or "ads_management" in msg
        or "has not grant" in msg
        or "not grant ads" in msg
    )


def _meta_ads_read_help(account_id: str) -> str:
    act = _act_id(account_id)
    return (
        f"metaVideos requires ads_read on ad account {account_id} ({act}). "
        "metaPerformance only needs read_insights, so campaign metrics can work while "
        "video creatives fail. In Meta Business Manager: (1) confirm the system user token "
        "includes ads_read, (2) assign that system user to this ad account with at least "
        "View performance, or (3) for client accounts, have the client grant your Business "
        f"Manager access to {act}. Then call GET /meta/test-ads-access?account_id={account_id}."
    )


def _graph_get_all(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    payload = _graph_get(path, access_token=access_token, params=params, env=env)
    rows = list(payload.get("data") or [])
    while payload.get("paging", {}).get("next"):
        payload = _graph_get(payload["paging"]["next"], access_token=access_token, env=env)
        rows.extend(payload.get("data") or [])
    return rows


def _graph_insights(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: MetaEnv | None = None,
    poll_timeout: int = 300,
) -> list[dict[str, Any]]:
    """Call Meta Insights endpoint, transparently handling async report jobs.

    Meta may return {"report_run_id": "..."} instead of {"data": [...]} when it
    decides to run the query asynchronously. This function polls until the job
    completes (up to poll_timeout seconds) and then fetches the results.
    """
    import logging
    import time

    _log = logging.getLogger(__name__)
    payload = _graph_get(path, access_token=access_token, params=params, env=env)

    # Synchronous response — normal pagination path
    if "data" in payload:
        rows = list(payload["data"])
        while payload.get("paging", {}).get("next"):
            payload = _graph_get(payload["paging"]["next"], access_token=access_token, env=env)
            rows.extend(payload.get("data") or [])
        return rows

    # Async report — Meta returned a report_run_id
    report_run_id = payload.get("report_run_id") or payload.get("id")
    if not report_run_id:
        _log.warning("Meta insights returned unexpected payload (no data, no report_run_id): %s", payload)
        return []

    _log.info("Meta insights async job started: report_run_id=%s", report_run_id)
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        status_payload = _graph_get(
            f"/{report_run_id}",
            access_token=access_token,
            params={"fields": "async_status,async_percent_completion"},
            env=env,
        )
        async_status = status_payload.get("async_status", "")
        pct = status_payload.get("async_percent_completion", 0)
        _log.info("Meta async job %s: status=%s pct=%s", report_run_id, async_status, pct)
        if async_status == "Job Complete":
            return _graph_get_all(
                f"/{report_run_id}/insights",
                access_token=access_token,
                env=env,
            )
        if async_status in ("Job Failed", "Job Skipped"):
            raise RuntimeError(f"Meta async insights job {report_run_id} failed: {status_payload}")
        time.sleep(5)

    raise RuntimeError(
        f"Meta async insights job {report_run_id} timed out after {poll_timeout}s"
    )


def _account_status_label(value: Any) -> str:
    try:
        return _ACCOUNT_STATUS.get(int(value), str(value or ""))
    except (TypeError, ValueError):
        return str(value or "")


def _result_tokens_for_adset(
    optimization_goal: Any, promoted_object: Any
) -> set[str]:
    """Resolve an ad set's result token set from its optimization_goal.

    Returns the set of result tokens whose counts make up this ad set's Ads
    Manager "Results":
      * a concrete conversion/engagement goal → {token}
      * an awareness goal (REACH/IMPRESSIONS/…) → set() (no action-based result)
      * an unknown goal, or a conversion goal with a non-standard custom event →
        the de-duplicated fallback token set.
    """
    goal = str(optimization_goal or "").upper().strip()
    if goal in _OPT_GOAL_TOKEN:
        token = _OPT_GOAL_TOKEN[goal]
        if token is None:
            return set()
        if token == "conversion":
            custom_event = str(
                (promoted_object or {}).get("custom_event_type") or ""
            ).upper().strip()
            mapped = _CUSTOM_EVENT_TOKEN.get(custom_event)
            if mapped:
                return {mapped}
            # Custom conversion (custom_event_type OTHER/blank) — can't pin the
            # exact standard event, so fall back to de-duplicated counting.
            return set(_FALLBACK_TOKENS)
        return {token}
    return set(_FALLBACK_TOKENS)


class ResultResolver:
    """Maps Meta ad sets and campaigns to the result token set that defines
    their Ads Manager "Results", so warehouse conversions match Ads Manager.

    Ad sets present in the map with an empty token set (awareness goals) resolve
    to 0 results. Ad sets/campaigns MISSING from the map (config we couldn't
    fetch) fall back to de-duplicated counting rather than being zeroed out.
    """

    def __init__(
        self,
        adset_tokens: dict[str, set[str]],
        campaign_tokens: dict[str, set[str]],
    ) -> None:
        self._adset_tokens = adset_tokens
        self._campaign_tokens = campaign_tokens

    def adset_tokens(self, adset_id: Any) -> set[str]:
        key = str(adset_id or "").strip()
        if key in self._adset_tokens:
            return self._adset_tokens[key]
        return set(_FALLBACK_TOKENS)

    def campaign_tokens(self, campaign_id: Any) -> set[str]:
        key = str(campaign_id or "").strip()
        if key in self._campaign_tokens:
            return self._campaign_tokens[key]
        return set(_FALLBACK_TOKENS)


def _actions_by_type(actions: list[dict[str, Any]] | None) -> dict[str, float]:
    by_type: dict[str, float] = {}
    for item in actions or []:
        action_type = str(item.get("action_type") or "").lower()
        if not action_type:
            continue
        by_type[action_type] = by_type.get(action_type, 0.0) + float(
            item.get("value") or 0
        )
    return by_type


def _sum_result_tokens(by_type: dict[str, float], tokens: set[str]) -> float:
    """Sum one value per token, taking the first candidate action_type present.

    Distinct tokens are summed; within a token only the first present candidate
    counts, which de-duplicates the offsite_/onsite_/omni_/bare buckets Meta
    emits for the same event.
    """
    total = 0.0
    for token in tokens:
        for candidate in _RESULT_CANDIDATES.get(token, (token,)):
            if candidate in by_type:
                total += by_type[candidate]
                break
    return total


def _parse_conversions(
    actions: list[dict[str, Any]] | None, tokens: set[str] | None = None
) -> float:
    return _sum_result_tokens(
        _actions_by_type(actions),
        tokens if tokens is not None else set(_FALLBACK_TOKENS),
    )


def _parse_conversion_value(
    action_values: list[dict[str, Any]] | None, tokens: set[str] | None = None
) -> float:
    return _sum_result_tokens(
        _actions_by_type(action_values),
        tokens if tokens is not None else set(_FALLBACK_TOKENS),
    )


def _parse_insight_row(
    row: dict[str, Any], *, result_tokens: set[str] | None = None
) -> dict[str, Any]:
    return {
        "spend": float(row.get("spend") or 0),
        "clicks": int(float(row.get("clicks") or 0)),
        "impressions": int(float(row.get("impressions") or 0)),
        "conversions": _parse_conversions(row.get("actions"), result_tokens),
        "conversion_value": _parse_conversion_value(
            row.get("action_values"), result_tokens
        ),
    }


def fetch_result_resolver(
    account_id: str,
    *,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> ResultResolver:
    """Fetch every ad set's optimization_goal + promoted_object and build a
    ResultResolver so daily insights count only each ad set's true result event.
    """
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)

    rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/adsets",
        access_token=access_token,
        params={
            "fields": "id,campaign_id,optimization_goal,promoted_object",
            "limit": 500,
        },
        env=env,
    )
    _log.info("Meta result resolver: adsets=%d", len(rows))

    adset_tokens: dict[str, set[str]] = {}
    campaign_tokens: dict[str, set[str]] = {}
    for row in rows:
        adset_id = str(row.get("id") or "").strip()
        if not adset_id:
            continue
        tokens = _result_tokens_for_adset(
            row.get("optimization_goal"), row.get("promoted_object")
        )
        adset_tokens[adset_id] = tokens
        campaign_id = str(row.get("campaign_id") or "").strip()
        if campaign_id:
            campaign_tokens.setdefault(campaign_id, set()).update(tokens)
    return ResultResolver(adset_tokens, campaign_tokens)


def _normalize_account_row(row: dict[str, Any], *, ownership: str) -> dict[str, Any]:
    account_id = _normalize_account_id(str(row.get("account_id") or row.get("id") or ""))
    return {
        "id": account_id,
        "name": row.get("name") or row.get("business_name") or "",
        "status": _account_status_label(row.get("account_status")),
        "currency": row.get("currency") or "",
        "ownership": ownership,
    }


def list_ad_accounts(
    *,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    business_id = env.business_id

    merged: dict[str, dict[str, Any]] = {}
    for ownership, edge in (("owned", "owned_ad_accounts"), ("client", "client_ad_accounts")):
        rows = _graph_get_all(
            f"/{business_id}/{edge}",
            access_token=access_token,
            params={"fields": _ACCOUNT_FIELDS, "limit": 500},
            env=env,
        )
        for row in rows:
            normalized = _normalize_account_row(row, ownership=ownership)
            if normalized["id"]:
                merged[normalized["id"]] = normalized

    return sorted(merged.values(), key=lambda item: (item.get("name") or item["id"]).lower())


def list_user_ad_accounts(
    *,
    access_token: str,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    """List ad accounts via /me/adaccounts — requires only ads_read, not business_management.

    Use as a fallback when the app lacks business_management Advanced Access.
    """
    env = env or load_meta_env(access_token=access_token)
    rows = _graph_get_all(
        "/me/adaccounts",
        access_token=access_token,
        params={"fields": _ACCOUNT_FIELDS, "limit": 500},
        env=env,
    )
    result = {}
    for row in rows:
        normalized = _normalize_account_row(row, ownership="user")
        if normalized["id"]:
            result[normalized["id"]] = normalized
    return sorted(result.values(), key=lambda item: (item.get("name") or item["id"]).lower())


def test_access_token(env: MetaEnv | None = None) -> dict[str, Any]:
    env = env or load_meta_env()
    try:
        accounts = list_ad_accounts(access_token=env.access_token, env=env)
        return {
            "ok": True,
            "message": "Meta access token is valid for this Business Manager.",
            "account_count": len(accounts),
            "error": None,
        }
    except Exception as exc:
        err = str(exc)
        hint = (
            "Usually: expired token, missing ads_read/business_management scopes, "
            "or system user not assigned to ad accounts in Business Manager."
        )
        return {
            "ok": False,
            "message": "Meta access token test failed.",
            "account_count": 0,
            "error": f"{err} — {hint}",
        }


def test_ads_read_access(
    account_id: str,
    *,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """Probe whether this token can read ads (required for metaVideos)."""
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    ads_ok = False
    ads_error: str | None = None
    try:
        _graph_get(
            f"/{_act_id(account_id_clean)}/ads",
            access_token=access_token,
            params={"fields": "id", "limit": 1},
            env=env,
        )
        ads_ok = True
    except Exception as exc:
        ads_error = str(exc)

    insights_ok = False
    insights_error: str | None = None
    try:
        _graph_get(
            f"/{_act_id(account_id_clean)}/insights",
            access_token=access_token,
            params={
                "fields": "impressions",
                "date_preset": "last_7d",
                "level": "account",
                "limit": 1,
            },
            env=env,
        )
        insights_ok = True
    except Exception as exc:
        insights_error = str(exc)

    ok = ads_ok
    if ok:
        message = f"ads_read access confirmed for account {account_id_clean}."
    elif insights_ok:
        message = (
            f"read_insights works for account {account_id_clean}, but ads_read is missing — "
            "metaVideos will fail until ads are readable."
        )
    else:
        message = f"Neither ads_read nor read_insights works for account {account_id_clean}."

    return {
        "ok": ok,
        "account_id": account_id_clean,
        "ads_read": ads_ok,
        "read_insights": insights_ok,
        "message": message,
        "error": ads_error,
        "insights_error": insights_error,
        "help": None if ads_ok else _meta_ads_read_help(account_id_clean),
    }


def _time_range(start: date, end: date) -> str:
    return json.dumps({"since": start.isoformat(), "until": end.isoformat()})


def _graph_insights_chunked(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any],
    start: date,
    end: date,
    env: MetaEnv | None = None,
    chunk_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetch insights over [start, end] in <=chunk_days windows and concatenate.

    High-cardinality levels (ad, adset) over a wide window make Meta service the
    query as an async report job that frequently times out or fails. Because
    sync_meta_to_bq swallows per-source errors and the BQ write is an upsert
    (never a delete), that silently freezes the raw ad/adset table at its last
    good date while the lighter campaign-level fetch keeps advancing. Splitting
    the range into small windows keeps each request on the fast synchronous
    pagination path. A single failing chunk is logged and skipped so the
    remaining (especially most recent) days still flow; only a total wipeout
    re-raises so the connector surfaces it. `params` must NOT set time_range --
    it is applied per chunk here.
    """
    import logging
    _log = logging.getLogger(__name__)

    rows: list[dict[str, Any]] = []
    step = timedelta(days=max(1, chunk_days))
    chunks = 0
    failures = 0
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + step - timedelta(days=1), end)
        chunks += 1
        try:
            rows.extend(
                _graph_insights(
                    path,
                    access_token=access_token,
                    params={**params, "time_range": _time_range(chunk_start, chunk_end)},
                    env=env,
                )
            )
        except Exception as exc:
            failures += 1
            _log.warning("Meta insights chunk %s..%s failed: %s", chunk_start, chunk_end, exc)
        chunk_start = chunk_end + timedelta(days=1)

    if failures and failures == chunks:
        raise RuntimeError(
            f"All {chunks} Meta insights chunks failed for {start}..{end}"
        )
    return rows


def fetch_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)

    rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "time_increment": 1,
            "level": "account",
            "limit": 500,
        },
        env=env,
    )

    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        metric_day = str(row.get("date_start") or "")[:10]
        if not metric_day:
            continue
        parsed = _parse_insight_row(row)
        by_date[metric_day] = {
            "metric_date": metric_day,
            **parsed,
        }

    out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        out.append(
            by_date.get(key)
            or {
                "metric_date": key,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        )
        cursor += timedelta(days=1)
    return out


def fetch_campaign_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: MetaEnv | None = None,
    resolver: ResultResolver | None = None,
) -> list[dict[str, Any]]:
    """Per-campaign daily metrics for warehouse storage.

    Returns list of {campaign_id, campaign_name, metric_date, spend, clicks, impressions,
    conversions, conversion_value}. One row per (campaign, day).

    When ``resolver`` is provided, ``conversions`` is the campaign's Ads Manager
    "Results" (its ad sets' optimization-goal events); otherwise it falls back to
    de-duplicated conversion counting.
    """
    import logging
    _log = logging.getLogger(__name__)

    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)

    _log.info("Meta insights campaign: account=%s range=%s/%s", account_id_clean, start, end)
    rows = _graph_insights(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "time_increment": 1,
            "level": "campaign",
            "limit": 500,
        },
        env=env,
    )
    _log.info("Meta insights campaign: api_rows=%d", len(rows))

    out: list[dict[str, Any]] = []
    for row in rows:
        metric_day = str(row.get("date_start") or "")[:10]
        campaign_id = str(row.get("campaign_id") or "").strip()
        if not metric_day or not campaign_id:
            continue
        result_tokens = resolver.campaign_tokens(campaign_id) if resolver else None
        parsed = _parse_insight_row(row, result_tokens=result_tokens)
        out.append({
            "campaign_id": campaign_id,
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": metric_day,
            **parsed,
        })
    _log.info("Meta insights campaign: parsed_rows=%d", len(out))
    return out


def fetch_adset_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: MetaEnv | None = None,
    resolver: ResultResolver | None = None,
) -> list[dict[str, Any]]:
    """Per-adset daily metrics for BQ warehouse storage.

    When ``resolver`` is provided, ``conversions`` is each ad set's Ads Manager
    "Results" (its optimization-goal event); otherwise it falls back to
    de-duplicated conversion counting.
    """
    import logging
    _log = logging.getLogger(__name__)

    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)

    _log.info("Meta insights adset: account=%s range=%s/%s", account_id_clean, start, end)
    rows = _graph_insights_chunked(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _ADSET_INSIGHT_FIELDS,
            "time_increment": 1,
            "level": "adset",
            "limit": 500,
        },
        start=start,
        end=end,
        env=env,
    )
    _log.info("Meta insights adset: api_rows=%d", len(rows))

    out: list[dict[str, Any]] = []
    for row in rows:
        metric_day = str(row.get("date_start") or "")[:10]
        adset_id = str(row.get("adset_id") or "").strip()
        if not metric_day or not adset_id:
            continue
        result_tokens = resolver.adset_tokens(adset_id) if resolver else None
        parsed = _parse_insight_row(row, result_tokens=result_tokens)
        out.append({
            "adset_id": adset_id,
            "adset_name": str(row.get("adset_name") or ""),
            "campaign_id": str(row.get("campaign_id") or "").strip(),
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": metric_day,
            **parsed,
        })
    return out


def fetch_ad_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: MetaEnv | None = None,
    resolver: ResultResolver | None = None,
) -> list[dict[str, Any]]:
    """Per-ad daily metrics for BQ warehouse storage.

    When ``resolver`` is provided, ``conversions`` is the result event of the
    ad's parent ad set (its optimization goal); otherwise it falls back to
    de-duplicated conversion counting.
    """
    import logging
    _log = logging.getLogger(__name__)

    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)

    _log.info("Meta insights ad: account=%s range=%s/%s", account_id_clean, start, end)
    rows = _graph_insights_chunked(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _AD_INSIGHT_FIELDS,
            "time_increment": 1,
            "level": "ad",
            "limit": 500,
        },
        start=start,
        end=end,
        env=env,
    )
    _log.info("Meta insights ad: api_rows=%d", len(rows))

    out: list[dict[str, Any]] = []
    for row in rows:
        metric_day = str(row.get("date_start") or "")[:10]
        ad_id = str(row.get("ad_id") or "").strip()
        if not metric_day or not ad_id:
            continue
        result_tokens = (
            resolver.adset_tokens(row.get("adset_id")) if resolver else None
        )
        parsed = _parse_insight_row(row, result_tokens=result_tokens)
        out.append({
            "ad_id": ad_id,
            "ad_name": str(row.get("ad_name") or ""),
            "adset_id": str(row.get("adset_id") or "").strip(),
            "adset_name": str(row.get("adset_name") or ""),
            "campaign_id": str(row.get("campaign_id") or "").strip(),
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": metric_day,
            **parsed,
        })
    return out


def sync_account_to_warehouse(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    import warehouse

    if not warehouse.enabled():
        raise RuntimeError("DATABASE_URL is not set — warehouse storage is disabled.")

    start, end, preset = resolve_date_range(date_range)
    daily_rows = fetch_daily_metrics(
        account_id,
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    account_id_clean = _normalize_account_id(account_id)
    written = warehouse.upsert_metrics_daily_batch("meta", account_id_clean, daily_rows)
    coverage = warehouse.account_date_coverage("meta", account_id_clean)
    return {
        "account_id": account_id_clean,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "days_synced": written,
        "coverage": coverage,
    }


def account_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)

    account_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "level": "account",
            "limit": 500,
        },
        env=env,
    )
    campaign_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "level": "campaign",
            "limit": 500,
        },
        env=env,
    )

    account_parsed = _parse_insight_row(account_rows[0]) if account_rows else {
        "spend": 0.0,
        "clicks": 0,
        "impressions": 0,
        "conversions": 0.0,
        "conversion_value": 0.0,
    }
    totals = {
        **account_parsed,
        "campaign_count": 0,
    }

    campaigns_out: list[dict[str, Any]] = []
    for row in campaign_rows:
        parsed = _parse_insight_row(row)
        campaigns_out.append(
            {
                "id": str(row.get("campaign_id") or ""),
                "entity_level": "campaign",
                "name": row.get("campaign_name") or "",
                "status": "",
                **parsed,
            }
        )
    campaigns_out.sort(key=lambda item: item.get("spend", 0), reverse=True)
    totals["campaign_count"] = len(campaigns_out)

    result = {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "campaigns": campaigns_out,
    }

    try:
        import warehouse

        if warehouse.enabled():
            sync_meta = sync_account_to_warehouse(
                account_id_clean,
                date_range=preset,
                access_token=access_token,
                env=env,
            )
            result["warehouse"] = {
                "stored": True,
                "days_synced": sync_meta["days_synced"],
                "coverage": sync_meta["coverage"],
            }
    except Exception as exc:
        result["warehouse"] = {"stored": False, "error": str(exc)[:500]}

    return result


def adsets_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    campaign_id: str | None = None,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """
    Ad set-level metrics (sub-campaign). Use for ad set dashboards;
    do not roll into campaign totals by name.
    """
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)
    filter_campaign = str(campaign_id or "").strip()

    params: dict[str, Any] = {
        "fields": _ADSET_INSIGHT_FIELDS,
        "time_range": _time_range(start, end),
        "level": "adset",
        "limit": 500,
    }
    if filter_campaign:
        params["filtering"] = json.dumps(
            [{"field": "campaign.id", "operator": "EQUAL", "value": filter_campaign}]
        )

    adset_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params=params,
        env=env,
    )

    adsets_out: list[dict[str, Any]] = []
    for row in adset_rows:
        parsed = _parse_insight_row(row)
        cid = str(row.get("campaign_id") or "")
        if filter_campaign and cid != filter_campaign:
            continue
        adsets_out.append(
            {
                "id": str(row.get("adset_id") or ""),
                "entity_level": "adset",
                "name": row.get("adset_name") or "",
                "status": "",
                "campaign_id": cid,
                "campaign_name": row.get("campaign_name") or "",
                **parsed,
            }
        )
    adsets_out.sort(key=lambda item: item.get("spend", 0), reverse=True)

    totals = {
        "spend": sum(a["spend"] for a in adsets_out),
        "clicks": sum(a["clicks"] for a in adsets_out),
        "impressions": sum(a["impressions"] for a in adsets_out),
        "conversions": sum(a["conversions"] for a in adsets_out),
        "conversion_value": sum(a["conversion_value"] for a in adsets_out),
        "adset_count": len(adsets_out),
    }

    return {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "adsets": adsets_out,
    }


def _fetch_ad_media_index(
    account_id: str,
    *,
    access_token: str,
    env: MetaEnv,
) -> dict[str, dict[str, str]]:
    """Map ad_id -> thumbnail/image metadata from ad creative (requires ads_read)."""
    account_id_clean = _normalize_account_id(account_id)
    params: dict[str, Any] = {
        "fields": _AD_MEDIA_FIELDS,
        "limit": _AD_MEDIA_PAGE_LIMIT,
        "effective_status": json.dumps(
            ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "PENDING_REVIEW", "DISAPPROVED"]
        ),
    }
    try:
        ad_rows = _graph_get_all(
            f"/{_act_id(account_id_clean)}/ads",
            access_token=access_token,
            params=params,
            env=env,
        )
    except Exception as exc:
        if _is_meta_ads_read_error(exc):
            return {}
        raise

    index: dict[str, dict[str, str]] = {}
    for ad in ad_rows:
        ad_id = str(ad.get("id") or "")
        if not ad_id:
            continue
        creative = ad.get("creative") if isinstance(ad.get("creative"), dict) else {}
        entries = _extract_creative_media(creative)
        if not entries:
            continue
        entry = entries[0]
        thumb = str(entry.get("thumbnail_url") or entry.get("image_url") or "")
        index[ad_id] = {
            "thumbnail_url": thumb,
            "image_url": str(entry.get("image_url") or ""),
            "media_type": str(entry.get("media_type") or ""),
            "creative_name": str(entry.get("creative_name") or ""),
            "video_id": str(entry.get("video_id") or ""),
        }

    video_ids = {d["video_id"] for d in index.values() if d.get("video_id")}
    if video_ids:
        details = _fetch_video_details(video_ids, access_token=access_token, env=env)
        for data in index.values():
            vid = data.get("video_id") or ""
            if not vid:
                continue
            detail = details.get(vid) or {}
            data["video_url"] = str(detail.get("video_url") or "")
            if not data.get("thumbnail_url"):
                data["thumbnail_url"] = str(detail.get("thumbnail_url") or "")

    return index


def ads_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    campaign_id: str | None = None,
    adset_id: str | None = None,
    include_creative: bool = True,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """
    Ad-level metrics (below ad set). Optionally merges creative thumbnails when ads_read is available.
    """
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)
    filter_campaign = str(campaign_id or "").strip()
    filter_adset = str(adset_id or "").strip()

    params: dict[str, Any] = {
        "fields": _AD_INSIGHT_FIELDS,
        "time_range": _time_range(start, end),
        "level": "ad",
        "limit": 500,
    }
    if filter_adset:
        params["filtering"] = json.dumps(
            [{"field": "adset.id", "operator": "EQUAL", "value": filter_adset}]
        )
    elif filter_campaign:
        params["filtering"] = json.dumps(
            [{"field": "campaign.id", "operator": "EQUAL", "value": filter_campaign}]
        )

    ad_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params=params,
        env=env,
    )

    media_index: dict[str, dict[str, str]] = {}
    if include_creative:
        try:
            media_index = _fetch_ad_media_index(
                account_id_clean, access_token=access_token, env=env
            )
        except Exception:
            media_index = {}

    ads_out: list[dict[str, Any]] = []
    for row in ad_rows:
        parsed = _parse_insight_row(row)
        aid = str(row.get("ad_id") or "")
        asid = str(row.get("adset_id") or "")
        cid = str(row.get("campaign_id") or "")
        if filter_adset and asid != filter_adset:
            continue
        if filter_campaign and cid != filter_campaign:
            continue
        item: dict[str, Any] = {
            "id": aid,
            "entity_level": "ad",
            "name": row.get("ad_name") or "",
            "status": "",
            "adset_id": asid,
            "adset_name": row.get("adset_name") or "",
            "campaign_id": cid,
            "campaign_name": row.get("campaign_name") or "",
            **parsed,
        }
        media = media_index.get(aid) or {}
        if media:
            item.update(media)
        ads_out.append(item)
    ads_out.sort(key=lambda item: item.get("spend", 0), reverse=True)

    totals = {
        "spend": sum(a["spend"] for a in ads_out),
        "clicks": sum(a["clicks"] for a in ads_out),
        "impressions": sum(a["impressions"] for a in ads_out),
        "conversions": sum(a["conversions"] for a in ads_out),
        "conversion_value": sum(a["conversion_value"] for a in ads_out),
        "ad_count": len(ads_out),
    }

    return {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "ads": ads_out,
    }


_AD_MEDIA_FIELDS = (
    "id,name,status,effective_status,"
    "campaign{id,name},adset{id,name},"
    "creative{"
    "id,name,thumbnail_url,image_url,video_id,object_type,"
    "object_story_spec,asset_feed_spec"
    "}"
)
_VIDEO_DETAIL_FIELDS = "id,source,picture,title,permalink_url"
# Meta's `?ids=` multi-get returns up to 50 objects per request. Batching video
# lookups this way turns N per-video calls into ceil(N/50), which keeps the ad
# account under its Ads API call limit (code 17 / subcode 2446079) on accounts
# with many video creatives.
_VIDEO_DETAIL_BATCH = 50


def _dig_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(key)
    return cur if isinstance(cur, dict) else {}


def _extract_creative_media(creative: dict[str, Any]) -> list[dict[str, Any]]:
    """Return zero or more media entries from a Meta ad creative."""
    if not creative:
        return []

    out: list[dict[str, Any]] = []
    base = {
        "creative_id": str(creative.get("id") or ""),
        "creative_name": str(creative.get("name") or ""),
        "thumbnail_url": str(creative.get("thumbnail_url") or ""),
        "image_url": str(creative.get("image_url") or ""),
    }

    video_id = str(creative.get("video_id") or "")
    if video_id:
        out.append({**base, "media_type": "video", "video_id": video_id})
        return out

    story = creative.get("object_story_spec") if isinstance(creative.get("object_story_spec"), dict) else {}
    video_data = _dig_dict(story, "video_data")
    vid = str(video_data.get("video_id") or "")
    if vid:
        thumb = str(video_data.get("image_url") or base["thumbnail_url"])
        out.append(
            {
                **base,
                "media_type": "video",
                "video_id": vid,
                "thumbnail_url": thumb or base["thumbnail_url"],
            }
        )
        return out

    link_data = _dig_dict(story, "link_data")
    for child in link_data.get("child_attachments") or []:
        if not isinstance(child, dict):
            continue
        child_vid = str(child.get("video_id") or "")
        if child_vid:
            out.append(
                {
                    **base,
                    "media_type": "video",
                    "video_id": child_vid,
                    "creative_name": str(child.get("name") or base["creative_name"]),
                    "thumbnail_url": base["thumbnail_url"],
                }
            )

    asset_feed = creative.get("asset_feed_spec") if isinstance(creative.get("asset_feed_spec"), dict) else {}
    for item in asset_feed.get("videos") or []:
        if not isinstance(item, dict):
            continue
        feed_vid = str(item.get("video_id") or "")
        if feed_vid:
            out.append({**base, "media_type": "video", "video_id": feed_vid})

    if out:
        return out

    if base["image_url"] or base["thumbnail_url"]:
        out.append({**base, "media_type": "image", "video_id": ""})

    return out


def _empty_video_detail() -> dict[str, str]:
    return {"video_url": "", "thumbnail_url": "", "video_title": ""}


def _parse_video_detail(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "video_url": str(payload.get("source") or payload.get("permalink_url") or ""),
        "thumbnail_url": str(payload.get("picture") or ""),
        "video_title": str(payload.get("title") or ""),
    }


def _fetch_video_details(
    video_ids: set[str],
    *,
    access_token: str,
    env: MetaEnv,
) -> dict[str, dict[str, str]]:
    """Map video_id -> {video_url, thumbnail_url, video_title}.

    Videos are fetched in batches via Meta's `?ids=` multi-get so a large
    creative set costs ceil(N/50) calls rather than N — the difference between
    staying under and blowing through the ad account's Ads API call limit. If a
    batch fails wholesale (e.g. one inaccessible id poisons the multi-get), fall
    back to per-id fetches for that chunk so one bad video can't blank the rest.
    """
    ids = [v for v in sorted(video_ids) if v]
    details: dict[str, dict[str, str]] = {}
    for chunk in _chunked(ids, _VIDEO_DETAIL_BATCH):
        try:
            payload = _graph_get(
                "/",
                access_token=access_token,
                params={"ids": ",".join(chunk), "fields": _VIDEO_DETAIL_FIELDS},
                env=env,
            )
        except Exception:
            details.update(_fetch_video_details_individually(chunk, access_token=access_token, env=env))
            continue
        for video_id in chunk:
            item = payload.get(video_id) if isinstance(payload, dict) else None
            details[video_id] = _parse_video_detail(item) if isinstance(item, dict) else _empty_video_detail()
    return details


def _fetch_video_details_individually(
    video_ids: list[str],
    *,
    access_token: str,
    env: MetaEnv,
) -> dict[str, dict[str, str]]:
    details: dict[str, dict[str, str]] = {}
    for video_id in video_ids:
        try:
            payload = _graph_get(
                f"/{video_id}",
                access_token=access_token,
                params={"fields": _VIDEO_DETAIL_FIELDS},
                env=env,
            )
            details[video_id] = _parse_video_detail(payload)
        except Exception:
            details[video_id] = _empty_video_detail()
    return details


def list_videos(
    account_id: str,
    *,
    campaign_id: str | None = None,
    videos_only: bool = True,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """
    Video/image preview URLs for Meta ads via ad creative metadata.
    """
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    filter_campaign = str(campaign_id or "").strip()
    params: dict[str, Any] = {
        "fields": _AD_MEDIA_FIELDS,
        "limit": _AD_MEDIA_PAGE_LIMIT,
        "effective_status": json.dumps(
            ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "PENDING_REVIEW", "DISAPPROVED"]
        ),
    }
    if filter_campaign:
        params["filtering"] = json.dumps(
            [{"field": "campaign.id", "operator": "EQUAL", "value": filter_campaign}]
        )

    try:
        ad_rows = _graph_get_all(
            f"/{_act_id(account_id_clean)}/ads",
            access_token=access_token,
            params=params,
            env=env,
        )
    except Exception as exc:
        if _is_meta_ads_read_error(exc):
            raise ValueError(_meta_ads_read_help(account_id_clean)) from exc
        raise

    pending_video_ids: set[str] = set()
    draft_rows: list[dict[str, Any]] = []

    for ad in ad_rows:
        creative = ad.get("creative") if isinstance(ad.get("creative"), dict) else {}
        campaign = ad.get("campaign") if isinstance(ad.get("campaign"), dict) else {}
        adset = ad.get("adset") if isinstance(ad.get("adset"), dict) else {}
        cid = str(campaign.get("id") or "")
        if filter_campaign and cid != filter_campaign:
            continue

        media_entries = _extract_creative_media(creative)
        if not media_entries:
            continue

        for entry in media_entries:
            if entry.get("media_type") == "video":
                vid = str(entry.get("video_id") or "")
                if vid:
                    pending_video_ids.add(vid)
            elif videos_only:
                continue

            draft_rows.append(
                {
                    "source": "meta_ad",
                    "ad_id": str(ad.get("id") or ""),
                    "ad_name": str(ad.get("name") or ""),
                    "ad_status": str(ad.get("effective_status") or ad.get("status") or ""),
                    "campaign_id": cid,
                    "campaign_name": str(campaign.get("name") or ""),
                    "adset_id": str(adset.get("id") or ""),
                    "adset_name": str(adset.get("name") or ""),
                    **entry,
                    "video_url": "",
                }
            )

    video_details = _fetch_video_details(pending_video_ids, access_token=access_token, env=env)

    videos_out: list[dict[str, Any]] = []
    for row in draft_rows:
        media_type = str(row.get("media_type") or "")
        if videos_only and media_type != "video":
            continue

        video_id = str(row.get("video_id") or "")
        if video_id:
            detail = video_details.get(video_id) or {}
            row["video_url"] = detail.get("video_url") or row.get("video_url") or ""
            if not row.get("thumbnail_url"):
                row["thumbnail_url"] = detail.get("thumbnail_url") or ""
            if not row.get("creative_name") and detail.get("video_title"):
                row["creative_name"] = detail.get("video_title") or ""

        videos_out.append(
            {
                "source": row["source"],
                "ad_id": row["ad_id"],
                "ad_name": row["ad_name"],
                "ad_status": row["ad_status"],
                "campaign_id": row["campaign_id"],
                "campaign_name": row["campaign_name"],
                "adset_id": row["adset_id"],
                "adset_name": row["adset_name"],
                "creative_id": row.get("creative_id") or "",
                "creative_name": row.get("creative_name") or "",
                "media_type": media_type,
                "video_id": video_id,
                "video_url": str(row.get("video_url") or ""),
                "thumbnail_url": str(row.get("thumbnail_url") or ""),
                "image_url": str(row.get("image_url") or ""),
            }
        )

    videos_out.sort(
        key=lambda row: (
            str(row.get("campaign_name") or ""),
            str(row.get("adset_name") or ""),
            str(row.get("ad_name") or ""),
        )
    )

    return {
        "account_id": account_id_clean,
        "row_count": len(videos_out),
        "videos": videos_out,
    }


def fetch_ad_creative_metadata(
    account_id: str,
    *,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    """Return ad creative metadata (thumbnail, image, media_type) for BQ storage.

    Returns a list of {ad_id, ad_name, adset_id, adset_name, campaign_id, campaign_name,
    thumbnail_url, image_url, media_type} — one row per ad.
    """
    env = env or load_meta_env(access_token=access_token)
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    try:
        ad_rows = _graph_get_all(
            f"/{_act_id(account_id_clean)}/ads",
            access_token=access_token,
            params={
                "fields": _AD_MEDIA_FIELDS,
                "limit": _AD_MEDIA_PAGE_LIMIT,
                "effective_status": json.dumps(
                    ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "PENDING_REVIEW", "DISAPPROVED"]
                ),
            },
            env=env,
        )
    except Exception as exc:
        if _is_meta_ads_read_error(exc):
            return []
        raise

    out: list[dict[str, Any]] = []
    video_ids: set[str] = set()
    interim: list[dict[str, Any]] = []

    for ad in ad_rows:
        ad_id = str(ad.get("id") or "")
        if not ad_id:
            continue
        creative = ad.get("creative") if isinstance(ad.get("creative"), dict) else {}
        entries = _extract_creative_media(creative)
        entry = entries[0] if entries else {}
        vid = str(entry.get("video_id") or "").strip()
        if vid:
            video_ids.add(vid)
        campaign = ad.get("campaign") if isinstance(ad.get("campaign"), dict) else {}
        adset = ad.get("adset") if isinstance(ad.get("adset"), dict) else {}
        interim.append({
            "ad_id": ad_id,
            "ad_name": str(ad.get("name") or ""),
            "adset_id": str(adset.get("id") or "").strip(),
            "adset_name": str(adset.get("name") or ""),
            "campaign_id": str(campaign.get("id") or "").strip(),
            "campaign_name": str(campaign.get("name") or ""),
            "thumbnail_url": str(entry.get("thumbnail_url") or ""),
            "image_url": str(entry.get("image_url") or ""),
            "media_type": str(entry.get("media_type") or ""),
            "video_id": vid,
        })

    video_details: dict[str, Any] = {}
    if video_ids:
        try:
            video_details = _fetch_video_details(video_ids, access_token=access_token, env=env)
        except Exception:
            pass

    for item in interim:
        vid = item.pop("video_id", "")
        if vid and video_details.get(vid):
            detail = video_details[vid]
            if not item["thumbnail_url"]:
                item["thumbnail_url"] = str(detail.get("thumbnail_url") or "")
        out.append(item)

    return out
