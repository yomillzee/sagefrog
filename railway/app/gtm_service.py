"""Google Tag Manager Reporting API — live container version audit."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_GTM_BASE = "https://tagmanager.googleapis.com/tagmanager/v2"
_CACHE_TTL = timedelta(minutes=15)

# key: (client_slug, container_id) → (fetched_at, payload)
_cache: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}

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

def _get_access_token(refresh_token: str) -> str:
    from ga4_reporting_service import _get_access_token as _ga4_refresh
    return _ga4_refresh(refresh_token)


def list_containers(refresh_token: str) -> list[dict[str, Any]]:
    """Return one entry per GTM container the token can access.

    Each entry: {"id": "accountId:containerId", "name": "Container (Account) • GTM-XXXXX"}
    """
    access_token = _get_access_token(refresh_token)
    with httpx.Client(timeout=30) as http:
        resp = http.get(
            f"{_GTM_BASE}/accounts",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code in (401, 403):
        raise PermissionError(
            f"Google returned {resp.status_code} listing GTM accounts. "
            "Ensure tagmanager.readonly scope is granted."
        )
    resp.raise_for_status()
    accounts = resp.json().get("account", [])

    containers: list[dict[str, Any]] = []
    with httpx.Client(timeout=30) as http:
        for acct in accounts:
            acct_id = str(acct.get("accountId", ""))
            acct_name = str(acct.get("name", acct_id))
            cr = http.get(
                f"{_GTM_BASE}/accounts/{acct_id}/containers",
                headers={"Authorization": f"Bearer {access_token}"},
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
    return containers


def _fetch_live_version(
    account_id: str,
    container_id: str,
    access_token: str,
) -> dict[str, Any]:
    url = f"{_GTM_BASE}/accounts/{account_id}/containers/{container_id}/versions:live"
    with httpx.Client(timeout=30) as http:
        resp = http.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code == 401:
        raise PermissionError(
            "Google returned 401 — the stored token lacks tagmanager.readonly. "
            "Reconnect the Google Analytics connector to grant GTM access."
        )
    if resp.status_code == 403:
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
    """
    cache_key = (client_slug, container_id)
    now = datetime.now(tz=UTC)

    if not force_refresh and cache_key in _cache:
        fetched_at, cached_payload = _cache[cache_key]
        if now - fetched_at < _CACHE_TTL:
            _log.debug(
                "GTM cache hit: client=%s container=%s age=%ds",
                client_slug, container_id, (now - fetched_at).seconds,
            )
            return cached_payload

    _log.info(
        "GTM fetch: client=%s account=%s container=%s",
        client_slug, account_id, container_id,
    )
    access_token = _get_access_token(refresh_token)
    raw = _fetch_live_version(account_id, container_id, access_token)
    rows = _normalise_version(raw)

    container_version = raw.get("containerVersionId", "")
    payload: dict[str, Any] = {
        "fetched_at": now.isoformat(),
        "container_version": container_version,
        "tag_count": len(rows),
        "rows": rows,
    }
    _cache[cache_key] = (now, payload)
    _log.info(
        "GTM fetched: client=%s container=%s version=%s tags=%d",
        client_slug, container_id, container_version, len(rows),
    )
    return payload
