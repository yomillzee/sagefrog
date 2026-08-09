"""Google Tag Manager Reporting API — live container version audit.

Everything here is shaped by one constraint: the GTM API allows **0.25 requests
per second per project** (25 per 100-second sliding window), shared across every
client, code path and worker process, and it reports exhaustion as **403 with a
``rateLimitExceeded`` reason** rather than 429. See ``gtm_quota`` for the
governor that meters the calls, and ``_is_rate_limited`` below for the
classification that keeps a quota 403 from being mistaken for a missing scope.

The defences layer up, cheapest first:

  L1  in-process cache      — repeat reads in one worker cost nothing
  L2  Postgres ``api_cache`` — shared across workers and surviving restarts,
                               and retained past its TTL so a rate-limited read
                               can serve slightly-stale data instead of failing
  L3  single-flight locks    — concurrent misses collapse into one fetch
  L4  ``gtm_quota``          — every surviving request is metered project-wide,
                               with a breaker that parks callers after a
                               genuine rejection
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

import db_cache
import gtm_quota
from gtm_quota import GTMRateLimited

_log = logging.getLogger(__name__)

_GTM_BASE = "https://tagmanager.googleapis.com/tagmanager/v2"
_CACHE_TTL = timedelta(minutes=15)
# The account/container fan-out (one call per account) is the burst that trips
# GTM's quota, and the wizard's "Select account" step re-fetches it on every
# render and retry. Memoise it so a single onboarding session pays the fan-out
# at most once. The TTL is generous (a wizard walk-through — connect, pick
# account, test — easily spans several minutes) so the listing survives the
# whole session, not just the first few steps.
_CONTAINERS_CACHE_TTL = timedelta(minutes=15)

# Shared (L2) cache. Rows outlive their TTL in `api_cache`: past it they stop
# counting as fresh, but they remain the fallback that lets a rate-limited read
# answer with stale data instead of an error — up to _STALE_MAX_AGE, beyond
# which a tag audit is too old to be worth showing without saying so.
_SHARED_CACHE_TTL_SECONDS = int(_CACHE_TTL.total_seconds())
_STALE_MAX_AGE = timedelta(hours=24)

# Transient upstream failures worth retrying. Quota exhaustion is *not* in this
# set — it is classified separately by _is_rate_limited(), because GTM signals it
# with a 403 that is otherwise indistinguishable from a permission error.
_RETRY_STATUSES = frozenset({429, 500, 502, 503})
_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 2

# Reasons Google puts in a 403 body when the request was refused for quota
# rather than for permissions. Anything else in a 403 is a real access problem.
_QUOTA_REASONS = frozenset({
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
    "resource_exhausted",
})

# key: (client_slug, container_id) → (fetched_at, payload)
_cache: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}

# key: refresh_token → (fetched_at, container list) — see list_containers().
_containers_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}

# Single-flight guard: overlapping wizard loads (rapid step clicks, multiple
# workers) must not each launch the fan-out — that multiplies the very burst we
# are trying to avoid. Callers that miss the cache serialise on a per-token lock
# and re-check the cache once inside, so the fan-out runs once and everyone else
# rides its result.
_containers_locks: dict[str, threading.Lock] = {}
_containers_locks_guard = threading.Lock()


def _containers_lock(refresh_token: str) -> threading.Lock:
    with _containers_locks_guard:
        lock = _containers_locks.get(refresh_token)
        if lock is None:
            lock = threading.Lock()
            _containers_locks[refresh_token] = lock
        return lock

# ── Friendly-name maps ────────────────────────────────────────────────────────

_TAG_TYPES: dict[str, str] = {
    "html": "Custom HTML",
    "img": "Custom Image",
    "ua": "Universal Analytics",
    "ga4Config": "GA4 Configuration",
    "ga4Event": "GA4 Event",
    "gaawe": "GA4 Event",
    "googtag": "Google tag (gtag.js)",
    "google_gtagjs": "Google tag",
    "awct": "Google Ads Conversion Tracking",
    "sp": "Google Ads Remarketing",
    "ogt_td": "Conversion Linker",
    "gclidw": "Google Click Conversion",
    "floodlightActivity": "Floodlight Activity",
    "fls": "Floodlight Sales",
    "flc": "Floodlight Counter",
    "bb": "Bing UET",
    "ms": "Microsoft Ads",
    "lcl": "LinkedIn Insight Tag",
    "fcs": "Meta Pixel",
    "twitter_pixel": "Twitter Pixel",
    "hotjar": "Hotjar",
    "cegg": "Crazy Egg",
    "gcs": "Google Consent Mode",
    "bzi": "Bizible",
    "csp": "Cookiebot",
    "scrollDepth": "Scroll Depth Listener",
}

_TRIGGER_TYPES: dict[str, str] = {
    "pageview": "Pageview",
    "domReady": "DOM Ready",
    "windowLoaded": "Window Loaded",
    "click": "All Element Clicks",
    "linkClick": "Just Links Click",
    "elementVisibility": "Element Visibility",
    "formSubmission": "Form Submission",
    "scrollDepth": "Scroll Depth",
    "customEvent": "Custom Event",
    "historyChange": "History Change",
    "youTubeVideo": "YouTube Video",
    "timer": "Timer",
    "jsError": "JavaScript Error",
    "always": "Initialization – All Pages",
    "consentInit": "Consent Initialization – All Pages",
    "serverPageview": "Server-Side Pageview",
}

_CONSENT_STATUS: dict[str, str] = {
    "notSet": "not_set",
    "notRequired": "not_required",
    "needed": "required",
}

# Special built-in GTM trigger IDs
_BUILTIN_TRIGGERS: dict[str, dict[str, str]] = {
    "2147479553": {"name": "All Pages", "type": "pageview"},
    "2147479572": {"name": "All Pages (DOM Ready)", "type": "domReady"},
    "2147479573": {"name": "All Pages (Window Loaded)", "type": "windowLoaded"},
    "2147479574": {"name": "Initialization – All Pages", "type": "always"},
    "2147479575": {"name": "Consent Initialization – All Pages", "type": "consentInit"},
}


# ── GTM API helpers ───────────────────────────────────────────────────────────

# Google access tokens are valid for an hour; minting a fresh one per GTM call
# added a second round-trip to every request (and its own quota) for no benefit.
# Reused just under that lifetime, with a margin for clock skew.
_ACCESS_TOKEN_TTL = timedelta(minutes=50)
_access_tokens: dict[str, tuple[datetime, str]] = {}
_access_token_lock = threading.Lock()


def _get_access_token(refresh_token: str) -> str:
    now = datetime.now(tz=UTC)
    with _access_token_lock:
        entry = _access_tokens.get(refresh_token)
        if entry is not None and now - entry[0] < _ACCESS_TOKEN_TTL:
            return entry[1]

    from ga4_reporting_service import _get_access_token as _ga4_refresh
    token = _ga4_refresh(refresh_token)

    with _access_token_lock:
        _access_tokens[refresh_token] = (datetime.now(tz=UTC), token)
    return token


def _invalidate_access_token(access_token: str) -> None:
    """Drop a cached access token Google has rejected, so the next call mints a
    fresh one instead of replaying the bad one for the rest of its TTL."""
    with _access_token_lock:
        for key, (_, token) in list(_access_tokens.items()):
            if token == access_token:
                del _access_tokens[key]


def _retry_after_seconds(resp: httpx.Response, default: float) -> float:
    """Honour a numeric Retry-After header (delta-seconds); fall back to default."""
    try:
        return max(float(resp.headers.get("Retry-After", "")), 0.0)
    except (TypeError, ValueError):
        return default


def _error_reasons(resp: httpx.Response) -> set[str]:
    """Lower-cased reason/status strings from a Google JSON error body."""
    try:
        body = resp.json()
    except Exception:
        return set()
    if not isinstance(body, dict):
        return set()
    error = body.get("error")
    if not isinstance(error, dict):
        return set()

    reasons: set[str] = set()
    status = error.get("status")
    if isinstance(status, str):
        reasons.add(status.lower())
    for item in error.get("errors") or []:
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            reasons.add(item["reason"].lower())
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
            reasons.add(detail["reason"].lower())
    return reasons


def _is_rate_limited(resp: httpx.Response) -> bool:
    """True when GTM refused this request for quota rather than permissions.

    GTM answers an exhausted quota with **403**, not 429 — the same status it
    uses for "you don't have access to this container". Only the body's reason
    (``rateLimitExceeded`` and friends) distinguishes them, so a 403 without one
    of those reasons is left alone as a genuine permission error.
    """
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    return bool(_error_reasons(resp) & _QUOTA_REASONS)


def _rate_limit_error(status_code: int, retry_after: float) -> GTMRateLimited:
    wait_hint = (
        f" Try again in about {int(round(retry_after))}s."
        if retry_after else " Try again in a minute."
    )
    return GTMRateLimited(
        f"Google Tag Manager is rate-limiting requests (HTTP {status_code}). Tag "
        "Manager allows only 0.25 requests/second for the whole project, so this "
        f"clears on its own.{wait_hint}",
        retry_after=retry_after,
    )


def _backoff_seconds(resp: httpx.Response, attempt: int) -> float:
    """Exponentially growing backoff, floored at Google's Retry-After when it
    sends one, plus jitter.

    Jitter matters here because the quota is project-wide: without it, every
    worker that got throttled in the same second retries in the same second and
    re-trips the limit together.
    """
    default = _RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
    wait = _retry_after_seconds(resp, default)
    return wait + random.uniform(0, min(1.0, wait * 0.25))


def _gtm_get(http: httpx.Client, url: str, access_token: str) -> httpx.Response:
    """GET a GTM endpoint under the project-wide quota governor.

    Every request first takes a token from ``gtm_quota`` so the app as a whole
    stays under GTM's 0.25 QPS ceiling. Transient failures (5xx) and rate-limit
    responses are retried with jittered backoff; a rate limit that survives the
    retries trips the shared breaker and raises ``GTMRateLimited`` so callers can
    fall back to cached data instead of hammering an exhausted quota.

    Status-specific handling (401/permission-403/404) stays with each caller.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    resp: httpx.Response | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        gtm_quota.acquire()
        resp = http.get(url, headers=headers)

        if _is_rate_limited(resp):
            if attempt == _MAX_ATTEMPTS:
                break
            wait = _backoff_seconds(resp, attempt)
            _log.warning(
                "GTM %s returned %d (quota); retrying in %.1fs (attempt %d/%d)",
                url, resp.status_code, wait, attempt, _MAX_ATTEMPTS,
            )
            time.sleep(wait)
            continue

        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
            wait = _backoff_seconds(resp, attempt)
            _log.warning(
                "GTM %s returned %d; retrying in %.1fs (attempt %d/%d)",
                url, resp.status_code, wait, attempt, _MAX_ATTEMPTS,
            )
            time.sleep(wait)
            continue

        if resp.status_code < 400:
            # A clean response proves the quota recovered — lift any breaker so
            # the next caller isn't parked on a stale cooldown.
            gtm_quota.note_success()
        return resp

    if resp is None:  # unreachable unless _MAX_ATTEMPTS is misconfigured to 0
        raise GTMRateLimited("No Google Tag Manager request was attempted.")
    retry_after = _retry_after_seconds(resp, 0.0)
    gtm_quota.note_rate_limited(retry_after)
    raise _rate_limit_error(resp.status_code, retry_after)


def list_containers(
    refresh_token: str, *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    """Return one entry per GTM container the token can access.

    Each entry: {"id": "accountId:containerId", "name": "Container (Account) • GTM-XXXXX"}

    This call fans out one request per account — by far the most quota-hungry
    thing the app does — and the wizard's "Select account" step re-fetches on
    every render. So it is cached at three levels: in-process, then the shared
    Postgres cache (so a second worker doesn't repeat the fan-out), then a
    per-token single-flight lock that collapses concurrent misses into one
    fetch. What survives all three is metered by ``gtm_quota``.

    If the fan-out is rate-limited and a previous listing is available, that
    listing is returned rather than an error: a slightly stale container list
    still lets the user finish the wizard.
    """
    cached = _cached_containers(refresh_token, force_refresh=force_refresh)
    if cached is not None:
        return cached

    lock = _containers_lock(refresh_token)
    with lock:
        # Another caller may have completed the fan-out while we waited on the
        # lock — re-check the cache before paying for it again.
        cached = _cached_containers(refresh_token, force_refresh=force_refresh)
        if cached is not None:
            return cached
        try:
            return _fetch_containers(refresh_token)
        except GTMRateLimited:
            stale = _stale_containers(refresh_token)
            if stale is None:
                raise
            _log.warning(
                "GTM containers rate-limited; serving %d cached container(s)",
                len(stale),
            )
            return stale


def _containers_cache_payload(refresh_token: str) -> dict[str, Any]:
    """Shared-cache key material for a token.

    The token itself never goes into the key: `api_cache` stores request_json in
    plaintext, and a refresh token is a credential. A digest identifies the
    token just as well for cache purposes.
    """
    digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    return {"token_sha256": digest}


def _cached_containers(
    refresh_token: str, *, force_refresh: bool
) -> list[dict[str, Any]] | None:
    """Return the cached container list if fresh, else None (L1 then L2)."""
    if force_refresh:
        return None
    entry = _containers_cache.get(refresh_token)
    if entry is not None:
        fetched_at, cached = entry
        age = datetime.now(tz=UTC) - fetched_at
        if age < _CONTAINERS_CACHE_TTL:
            _log.debug(
                "GTM containers cache hit: age=%ds count=%d", age.seconds, len(cached),
            )
            return cached

    shared = _shared_containers(refresh_token, allow_stale=False)
    if shared is None:
        return None
    # Promote into L1 so the rest of this worker's requests skip the DB too,
    # keeping the original fetch time so L1 expires when the shared row does.
    containers, fetched_at = shared
    _containers_cache[refresh_token] = (fetched_at, containers)
    return containers


def _shared_containers(
    refresh_token: str, *, allow_stale: bool
) -> tuple[list[dict[str, Any]], datetime] | None:
    """Read the container list from the cross-worker Postgres cache.

    Returns the list *and when it was actually fetched*, so a caller promoting
    it into the in-process cache can keep its real age rather than restarting
    the TTL and serving it for another full window.
    """
    try:
        hit = db_cache.get_cached(
            "gtm.containers",
            _containers_cache_payload(refresh_token),
            allow_stale=allow_stale,
        )
    except Exception:
        _log.debug("GTM containers shared-cache read failed", exc_info=True)
        return None
    if hit is None:
        return None
    if allow_stale and hit.age_seconds > _STALE_MAX_AGE.total_seconds():
        return None
    value = hit.response_json
    if not isinstance(value, list):
        return None
    return value, hit.created_at


def _stale_containers(refresh_token: str) -> list[dict[str, Any]] | None:
    """Best available container list regardless of freshness, for the
    rate-limited fallback path."""
    entry = _containers_cache.get(refresh_token)
    if entry is not None:
        fetched_at, cached = entry
        if datetime.now(tz=UTC) - fetched_at <= _STALE_MAX_AGE:
            return cached
    shared = _shared_containers(refresh_token, allow_stale=True)
    return shared[0] if shared else None


def _fetch_containers(refresh_token: str) -> list[dict[str, Any]]:
    """Run the accounts → containers fan-out and cache the result.

    Callers hold the per-token single-flight lock. Pacing is not this function's
    job any more: every request inside goes through ``gtm_quota``, which meters
    the whole project — including the calls other clients and workers are making
    at the same time, which no local pacing could see.
    """
    access_token = _get_access_token(refresh_token)
    with httpx.Client(timeout=30) as http:
        resp = _gtm_get(http, f"{_GTM_BASE}/accounts", access_token)
        if resp.status_code in (401, 403):
            if resp.status_code == 401:
                _invalidate_access_token(access_token)
            raise PermissionError(
                f"Google returned {resp.status_code} listing GTM accounts. "
                "Ensure tagmanager.readonly scope is granted."
            )
        resp.raise_for_status()
        accounts = resp.json().get("account", [])

        containers: list[dict[str, Any]] = []
        for acct in accounts:
            acct_id = str(acct.get("accountId", ""))
            acct_name = str(acct.get("name", acct_id))
            cr = _gtm_get(
                http, f"{_GTM_BASE}/accounts/{acct_id}/containers", access_token
            )
            if cr.status_code >= 400:
                continue
            for cont in cr.json().get("container", []):
                cid = str(cont.get("containerId", ""))
                cname = str(cont.get("name", cid))
                public_id = str(cont.get("publicId", ""))
                label = f"{cname} ({acct_name})"
                if public_id:
                    label += f" • {public_id}"
                containers.append({"id": f"{acct_id}:{cid}", "name": label})

    _containers_cache[refresh_token] = (datetime.now(tz=UTC), containers)
    _store_shared_containers(refresh_token, containers)
    return containers


def _store_shared_containers(
    refresh_token: str, containers: list[dict[str, Any]]
) -> None:
    """Publish the fan-out result to the cross-worker cache (best effort)."""
    try:
        db_cache.put_cached(
            "gtm.containers",
            _containers_cache_payload(refresh_token),
            response_json=containers,
            row_count=len(containers),
            ttl_seconds=int(_CONTAINERS_CACHE_TTL.total_seconds()),
        )
    except Exception:
        _log.debug("GTM containers shared-cache write failed", exc_info=True)


def _fetch_live_version(
    account_id: str,
    container_id: str,
    access_token: str,
) -> dict[str, Any]:
    url = f"{_GTM_BASE}/accounts/{account_id}/containers/{container_id}/versions:live"
    with httpx.Client(timeout=30) as http:
        resp = _gtm_get(http, url, access_token)
    if resp.status_code == 401:
        _invalidate_access_token(access_token)
        raise PermissionError(
            "Google returned 401 — the stored token lacks tagmanager.readonly. "
            "Reconnect the Google Analytics connector to grant GTM access."
        )
    if resp.status_code == 403:
        # A quota 403 never reaches here — _gtm_get classifies those by their
        # error reason and raises GTMRateLimited — so this is a real access
        # problem, and saying so is safe.
        raise PermissionError(
            f"Google returned 403 — the authenticated account does not have read "
            f"access to GTM container {container_id} under account {account_id}."
        )
    if resp.status_code == 404:
        raise LookupError(
            f"GTM container {container_id} / account {account_id} not found. "
            "Verify the IDs in the client config."
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"GTM API error {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


# ── Normalisation helpers ─────────────────────────────────────────────────────

_OPERATOR_LABELS: dict[str, str] = {
    "contains":       "contains",
    "equals":         "equals",
    "startsWith":     "starts with",
    "endsWith":       "ends with",
    "matchRegex":     "matches regex",
    "greater":        "greater than",
    "greaterOrEquals": "≥",
    "less":           "less than",
    "lessOrEquals":   "≤",
    "cssSelector":    "matches CSS selector",
    "negate":         "negation",
}


def _parse_conditions(conditions: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for cond in conditions:
        params = {p["key"]: p.get("value", "") for p in cond.get("parameter", [])}
        op = cond.get("type", "")
        out.append({
            "operator":       op,
            "operator_label": _OPERATOR_LABELS.get(op, op),
            "variable":       params.get("arg0", ""),
            "value":          params.get("arg1", ""),
        })
    return out


def _extract_trigger_settings(trigger: dict[str, Any]) -> dict[str, Any]:
    t_type = trigger.get("type", "")
    settings: dict[str, Any] = {}

    if t_type == "customEvent":
        for f in trigger.get("customEventFilter", []):
            params = {p["key"]: p.get("value") for p in f.get("parameter", [])}
            settings["event_name_pattern"] = params.get("arg1", "")
            settings["use_regex"] = f.get("type", "") == "MATCH_REGEX"

    if t_type in ("click", "linkClick"):
        settings["wait_for_tags"] = (
            trigger.get("waitForTags", {}).get("value") == "true"
        )
        settings["check_validation"] = (
            trigger.get("checkValidation", {}).get("value") == "true"
        )

    if t_type == "elementVisibility":
        settings["selector"] = (trigger.get("visibilitySelector") or {}).get("value")
        settings["min_visible_pct"] = (
            trigger.get("visiblePercentageMin") or {}
        ).get("value")

    if t_type == "scrollDepth":
        settings["vertical_threshold_pct"] = (
            trigger.get("verticalScrollThresholdPercent") or {}
        ).get("value")
        settings["horizontal_threshold_pct"] = (
            trigger.get("horizontalScrollThresholdPercent") or {}
        ).get("value")

    if t_type == "timer":
        settings["interval_ms"] = (trigger.get("interval") or {}).get("value")
        settings["limit"] = (trigger.get("limit") or {}).get("value")

    if t_type == "youTubeVideo":
        settings["pause"] = bool((trigger.get("pause") or {}).get("value") == "true")
        settings["seek"] = bool((trigger.get("seek") or {}).get("value") == "true")

    # Catch-all: include named parameters for types not explicitly handled above
    if not settings:
        params = {}
        for p in trigger.get("parameter", []):
            k = p.get("key", "")
            if k:
                params[k] = p.get("value", "")
        if params:
            settings["parameters"] = params

    return settings


def _normalise_trigger(trigger: dict[str, Any]) -> dict[str, Any]:
    t_type = trigger.get("type", "")
    all_conditions = (
        trigger.get("filter", [])
        + trigger.get("autoEventFilter", [])
        + trigger.get("customEventFilter", [])
    )
    return {
        "trigger_id": trigger.get("triggerId", ""),
        "trigger_name": trigger.get("name", ""),
        "trigger_raw_type": t_type,
        "trigger_friendly_type": _TRIGGER_TYPES.get(t_type, t_type or "Unknown"),
        "trigger_criteria": _parse_conditions(all_conditions),
        "trigger_settings": _extract_trigger_settings(trigger),
        "trigger_logic": "all",  # GTM always ANDs conditions within a trigger
    }


_GA4_EVENT_TYPES = ("gaawe", "ga4Event")


def _tag_event_name(tag: dict[str, Any]) -> str:
    """The GA4 event name a GA4-event tag sends (its `eventName` parameter).
    May be a literal ('generate_lead') or a GTM variable ref ('{{dlv - event}}')."""
    for p in tag.get("parameter", []):
        if p.get("key") == "eventName":
            return str(p.get("value", "") or "")
    return ""


def _normalise_tag(
    tag: dict[str, Any],
    trigger_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw_type = tag.get("type", "")
    is_ga4_event = raw_type in _GA4_EVENT_TYPES
    event_name = _tag_event_name(tag) if is_ga4_event else ""
    firing_ids: list[str] = tag.get("firingTriggerId") or []

    trigger_rows = []
    for tid in firing_ids:
        if tid in trigger_index:
            trigger_rows.append(_normalise_trigger(trigger_index[tid]))
        elif tid in _BUILTIN_TRIGGERS:
            bt = _BUILTIN_TRIGGERS[tid]
            t_type = bt["type"]
            trigger_rows.append({
                "trigger_id": tid,
                "trigger_name": bt["name"],
                "trigger_raw_type": t_type,
                "trigger_friendly_type": _TRIGGER_TYPES.get(t_type, t_type),
                "trigger_criteria": [],
                "trigger_settings": {},
                "trigger_logic": "all",
            })
        else:
            trigger_rows.append({
                "trigger_id": tid,
                "trigger_name": f"(unknown trigger {tid})",
                "trigger_raw_type": "",
                "trigger_friendly_type": "Unknown",
                "trigger_criteria": [],
                "trigger_settings": {},
                "trigger_logic": "all",
            })

    consent_raw = (tag.get("consentSettings") or {}).get("consentStatus", "notSet")

    triggers = [
        {
            "id":       tr["trigger_id"],
            "name":     tr["trigger_name"],
            "type":     tr["trigger_friendly_type"],
            "criteria": tr["trigger_criteria"],  # [{operator, operator_label, variable, value}]
            "logic":    "all",  # GTM ANDs conditions within a trigger
        }
        for tr in trigger_rows
    ]

    return {
        "tag_name":     tag.get("name", ""),
        "raw_type":     raw_type,
        "friendly_type": _TAG_TYPES.get(raw_type, raw_type or "Unknown"),
        "is_ga4_event": is_ga4_event,
        "event_name":   event_name,
        "paused":       bool(tag.get("paused", False)),
        "consent_status": _CONSENT_STATUS.get(consent_raw, consent_raw),
        "triggers":     triggers,
        # convenience flat lists kept for backward compat
        "firing_trigger_names": [tr["trigger_name"] for tr in trigger_rows],
        "trigger_types":        [tr["trigger_friendly_type"] for tr in trigger_rows],
    }


def _normalise_version(raw: dict[str, Any]) -> list[dict[str, Any]]:
    triggers = raw.get("trigger") or []
    trigger_index: dict[str, dict[str, Any]] = {
        t["triggerId"]: t for t in triggers if t.get("triggerId")
    }
    tags = raw.get("tag") or []
    return [_normalise_tag(tag, trigger_index) for tag in tags]


# ── Public API ────────────────────────────────────────────────────────────────

def _live_tags_cache_payload(
    client_slug: str, account_id: str, container_id: str
) -> dict[str, Any]:
    return {
        "client_slug": client_slug,
        "account_id": account_id,
        "container_id": container_id,
    }


def _shared_live_tags(
    client_slug: str, account_id: str, container_id: str, *, allow_stale: bool
) -> tuple[dict[str, Any], datetime] | None:
    """Read the audit from the cross-worker cache, with its real fetch time so a
    caller promoting it into L1 doesn't restart its TTL."""
    try:
        hit = db_cache.get_cached(
            "gtm.live_tags",
            _live_tags_cache_payload(client_slug, account_id, container_id),
            allow_stale=allow_stale,
        )
    except Exception:
        _log.debug("GTM live-tags shared-cache read failed", exc_info=True)
        return None
    if hit is None:
        return None
    if allow_stale and hit.age_seconds > _STALE_MAX_AGE.total_seconds():
        return None
    value = hit.response_json
    if not isinstance(value, dict):
        return None
    if hit.is_stale:
        # Mark it so the UI shows "as of <fetched_at>" rather than implying
        # this reflects the container right now.
        value = {**value, "stale": True}
    return value, hit.created_at


def _stale_live_tags(
    client_slug: str, account_id: str, container_id: str
) -> dict[str, Any] | None:
    """Best available audit regardless of freshness, for the rate-limited path."""
    entry = _cache.get((client_slug, container_id))
    if entry is not None:
        fetched_at, payload = entry
        if datetime.now(tz=UTC) - fetched_at <= _STALE_MAX_AGE:
            return {**payload, "stale": True}
    shared = _shared_live_tags(
        client_slug, account_id, container_id, allow_stale=True
    )
    if shared is None:
        return None
    payload, _ = shared
    return {**payload, "stale": True}


def get_live_tags(
    client_slug: str,
    account_id: str,
    container_id: str,
    refresh_token: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Fetch, normalise, and cache the live GTM container version.
    Returns {"fetched_at": iso_str, "rows": [...], "container_version": str}.

    Cached in-process and in Postgres (so every worker and every restart shares
    one read). If GTM's project-wide quota blocks a live fetch, the last known
    audit is returned with ``"stale": True`` instead of an error — a tag list
    from minutes ago is far more useful than a broken page, and retrying into an
    exhausted quota is what keeps it exhausted.
    """
    cache_key = (client_slug, container_id)
    now = datetime.now(tz=UTC)

    if not force_refresh:
        entry = _cache.get(cache_key)
        if entry is not None:
            fetched_at, cached_payload = entry
            if now - fetched_at < _CACHE_TTL:
                _log.debug(
                    "GTM cache hit: client=%s container=%s age=%ds",
                    client_slug, container_id, (now - fetched_at).seconds,
                )
                return cached_payload

        shared = _shared_live_tags(
            client_slug, account_id, container_id, allow_stale=False
        )
        if shared is not None:
            payload, fetched_at = shared
            _cache[cache_key] = (fetched_at, payload)
            return payload

    _log.info(
        "GTM fetch: client=%s account=%s container=%s",
        client_slug, account_id, container_id,
    )
    try:
        access_token = _get_access_token(refresh_token)
        raw = _fetch_live_version(account_id, container_id, access_token)
    except GTMRateLimited:
        stale = _stale_live_tags(client_slug, account_id, container_id)
        if stale is None:
            raise
        _log.warning(
            "GTM live tags rate-limited for %s/%s; serving cached audit from %s",
            client_slug, container_id, stale.get("fetched_at"),
        )
        return stale

    rows = _normalise_version(raw)
    container_version = raw.get("containerVersionId", "")
    payload: dict[str, Any] = {
        "fetched_at": now.isoformat(),
        "container_version": container_version,
        "tag_count": len(rows),
        "rows": rows,
    }
    _cache[cache_key] = (now, payload)
    _store_shared_live_tags(client_slug, account_id, container_id, payload)
    _log.info(
        "GTM fetched: client=%s container=%s version=%s tags=%d",
        client_slug, container_id, container_version, len(rows),
    )
    return payload


def _store_shared_live_tags(
    client_slug: str, account_id: str, container_id: str, payload: dict[str, Any]
) -> None:
    try:
        db_cache.put_cached(
            "gtm.live_tags",
            _live_tags_cache_payload(client_slug, account_id, container_id),
            response_json=payload,
            row_count=int(payload.get("tag_count") or 0),
            ttl_seconds=_SHARED_CACHE_TTL_SECONDS,
        )
    except Exception:
        _log.debug("GTM live-tags shared-cache write failed", exc_info=True)
