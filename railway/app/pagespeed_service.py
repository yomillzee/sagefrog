"""Google PageSpeed Insights (PSI) API client — Lighthouse scores + Core Web Vitals.

PSI runs a live Lighthouse audit against a URL and returns four category scores
(Performance, Accessibility, Best Practices, SEO) on a 0–100 scale, plus lab
Core Web Vitals (LCP, CLS, TBT, etc.). Unlike ad platforms there is no historical
API — every call is a point-in-time measurement, so history is built by
snapshotting one row per sync (see bq_pagespeed_service).

No OAuth: PSI is authenticated with an optional single shared PAGESPEED_API_KEY
env var (agency-level, like SEMRUSH_API_KEY). Keyless calls work at a lower quota
(~25k/day), so the key is optional but recommended. The only per-client input is
the URL to audit, entered manually in the connector wizard and stored as
source_account_id.

Error handling mirrors semrush_service: build_pagespeed_snapshot() never raises
(errors surface under the 'error' key) so snapshot refresh is never interrupted.
fetch_scores() DOES raise — the wizard's "Test connection" step relies on it.

Auth precedence (all optional — keyless still works at low quota):
    1. PAGESPEED_API_KEY — API key; quota billed to the key's project.
    2. Agency service account (GCP_SERVICE_ACCOUNT_JSON, the same SA used for
       BigQuery/GA4/GSC) — an OAuth bearer token, quota billed to the SA's GCP
       project. Used when no API key is set (e.g. org policy blocks API keys).
       Requires the PageSpeed Insights API enabled in that project.
    3. Keyless — shared anonymous quota (~25k/day globally, routinely exhausted).

Optional env vars:
    PAGESPEED_API_KEY   — API key (create at console.cloud.google.com, enable
                          the PageSpeed Insights API). Raises the daily quota.
    PAGESPEED_STRATEGY  — "desktop" (default) or "mobile".
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_DEFAULT_STRATEGY = "desktop"

# Lighthouse category id -> the snake_case score key we expose. Note the API
# spells best practices "best-practices" (hyphen) in its category map.
_CATEGORY_KEYS: tuple[tuple[str, str], ...] = (
    ("performance", "performance"),
    ("accessibility", "accessibility"),
    ("best-practices", "best_practices"),
    ("seo", "seo"),
)

# Lab Core Web Vitals we pull from lighthouseResult.audits. numericValue is in ms
# for time metrics, unitless for CLS.
_LAB_AUDITS: tuple[tuple[str, str], ...] = (
    ("largest-contentful-paint", "lcp_ms"),
    ("cumulative-layout-shift", "cls"),
    ("total-blocking-time", "tbt_ms"),
    ("first-contentful-paint", "fcp_ms"),
    ("speed-index", "speed_index_ms"),
    ("interactive", "tti_ms"),
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _api_key() -> str:
    return (os.getenv("PAGESPEED_API_KEY") or "").strip()


def _default_strategy() -> str:
    s = (os.getenv("PAGESPEED_STRATEGY") or _DEFAULT_STRATEGY).strip().lower()
    return s if s in ("desktop", "mobile") else _DEFAULT_STRATEGY


# Cached agency service-account credentials (minted once, refreshed on expiry).
_sa_creds: Any = None
_sa_project: str | None = None


def _service_account_auth() -> tuple[str, str | None] | None:
    """OAuth bearer token from the agency service account, so PSI quota is billed
    to our GCP project rather than the exhausted anonymous pool. Returns
    (access_token, project_id), or None if no SA is configured (→ keyless).

    Uses the same GCP_SERVICE_ACCOUNT_JSON the BigQuery/GA4/GSC layers use. The
    project_id is returned for optional quota attribution but is not currently
    sent as x-goog-user-project (that would require the SA to hold
    roles/serviceusage.serviceUsageConsumer).
    """
    global _sa_creds, _sa_project
    try:
        from google.auth.transport.requests import Request as _GARequest

        if _sa_creds is None:
            from google.oauth2 import service_account
            from ga4_credentials import (
                GLOBAL_GCP_CREDENTIALS_ENV,
                load_service_account_info_from_env,
            )

            info = load_service_account_info_from_env(GLOBAL_GCP_CREDENTIALS_ENV)
            _sa_creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            _sa_project = info.get("project_id")
        if not _sa_creds.valid:
            _sa_creds.refresh(_GARequest())
        return _sa_creds.token, _sa_project
    except Exception as exc:
        _log.warning("PageSpeed SA auth unavailable, falling back to keyless: %s", exc)
        return None


def normalize_url(url: str) -> str:
    """Coerce a user-entered URL into something PSI accepts (needs a scheme)."""
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(url: str, strategy: str, timeout: int = 60) -> dict[str, Any]:
    """GET the PSI API and return parsed JSON. Raises on HTTP/transport errors."""
    params: list[tuple[str, str]] = [
        ("url", url),
        ("strategy", strategy),
    ]
    # category is a repeated param — request all four scores in one call.
    for cat, _ in _CATEGORY_KEYS:
        params.append(("category", cat.upper().replace("-", "_")))

    headers: dict[str, str] = {}
    key = _api_key()
    if key:
        # Explicit API key wins; quota billed to the key's project.
        params.append(("key", key))
    else:
        # No key → authorize with the agency service account so quota lands on
        # our GCP project instead of the shared anonymous pool. Keyless if the
        # SA is unavailable. The bearer token already identifies the SA's project
        # (sagefrog), so quota attributes there without an explicit
        # x-goog-user-project header (which would require the SA to hold
        # roles/serviceusage.serviceUsageConsumer).
        auth = _service_account_auth()
        if auth:
            token, _project = auth
            headers["Authorization"] = f"Bearer {token}"

    full_url = _ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Surface Google's structured error message (quota, invalid URL, etc.).
        try:
            body = exc.read().decode("utf-8", errors="replace")
            detail = json.loads(body).get("error", {}).get("message", "") or body[:300]
        except Exception:
            detail = ""
        raise RuntimeError(f"PageSpeed API error (HTTP {exc.code}): {detail}") from exc


def _score_pct(node: dict[str, Any]) -> int | None:
    """Lighthouse category score is 0..1 (or null when not applicable) -> 0..100."""
    score = (node or {}).get("score")
    if score is None:
        return None
    try:
        return round(float(score) * 100)
    except (TypeError, ValueError):
        return None


def _audit_numeric(audits: dict[str, Any], audit_id: str) -> float | None:
    v = (audits.get(audit_id) or {}).get("numericValue")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_scores(url: str, strategy: str | None = None) -> dict[str, Any]:
    """Run a single PSI audit for `url`. Raises on failure (used by Test connection).

    Returns a flat dict: url, strategy, fetched_at, the four 0–100 category scores,
    and lab Core Web Vitals (lcp_ms, cls, tbt_ms, fcp_ms, speed_index_ms, tti_ms).
    Also includes field-data CWV percentiles under crux_* when Google has enough
    real-user data (absent for low-traffic sites).
    """
    target = normalize_url(url)
    if not target:
        raise ValueError("A URL is required.")
    strat = (strategy or _default_strategy()).strip().lower()
    if strat not in ("desktop", "mobile"):
        strat = _DEFAULT_STRATEGY

    data = _get(target, strat)
    lh = data.get("lighthouseResult") or {}
    categories = lh.get("categories") or {}
    audits = lh.get("audits") or {}

    result: dict[str, Any] = {
        "url": target,
        "strategy": strat,
        "final_url": lh.get("finalUrl") or target,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    got_any_score = False
    for cat_id, out_key in _CATEGORY_KEYS:
        pct = _score_pct(categories.get(cat_id) or {})
        result[out_key] = pct
        got_any_score = got_any_score or pct is not None
    if not got_any_score:
        raise RuntimeError("PSI returned no Lighthouse scores for this URL.")

    for audit_id, out_key in _LAB_AUDITS:
        result[out_key] = _audit_numeric(audits, audit_id)

    # Field data (CrUX real-user metrics), when available.
    crux = (data.get("loadingExperience") or {}).get("metrics") or {}
    result["crux_lcp_ms"] = (crux.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile")
    result["crux_cls"] = (crux.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile")
    result["crux_inp_ms"] = (crux.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile")
    return result


def build_pagespeed_snapshot(url: str, strategy: str | None = None) -> dict[str, Any]:
    """Fetch PSI scores and return a snapshot dict. Never raises.

    Errors surface under the 'error' key so the daily sync / snapshot refresh is
    never interrupted by a PSI hiccup.
    """
    target = normalize_url(url)
    strat = (strategy or _default_strategy()).strip().lower()
    if strat not in ("desktop", "mobile"):
        strat = _DEFAULT_STRATEGY
    if not target:
        return {"url": target, "strategy": strat, "error": "No URL configured"}
    try:
        return fetch_scores(target, strat)
    except Exception as exc:
        _log.warning("PageSpeed fetch failed [%s]: %s", target, exc)
        return {"url": target, "strategy": strat, "error": str(exc)[:300]}
