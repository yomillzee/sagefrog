"""Chrome UX Report (CrUX) History API client — real-user Core Web Vitals trend.

PageSpeed Insights already returns a *single* CrUX reading per audit (the
`loadingExperience` block pagespeed_service pulls into crux_lcp_ms/crux_cls/
crux_inp_ms). That is one 28-day window, so a client's field-data trend only
grows as fast as our own weekly sync accumulates rows.

The CrUX History API returns the whole trend in one call: 25 weekly collection
periods (~6 months), each a 28-day rolling aggregate, for an origin or URL. So a
newly connected client gets six months of real-user history immediately instead
of waiting six months for it.

Two things to keep straight when reading this module:

  * **Lab vs field.** PSI/Lighthouse is a lab test — one synthetic load from
    Google's machine. CrUX is field data — what real Chrome users actually
    experienced. They disagree routinely and that is expected; the dashboard
    labels them separately for that reason.
  * **Overlapping windows.** Collection periods step weekly but each spans 28
    days, so consecutive points share three weeks of data. The series is a
    moving average and moves smoothly by construction — don't read a flat week
    as "nothing changed".

Not every site is in CrUX. An origin needs enough real Chrome traffic to be
eligible, and the API answers 404 for one that isn't. That is a normal outcome
for a small B2B site, not a failure: fetch_history() reports it as
``not_enough_data`` so the caller can say so plainly rather than showing an error.
Individual periods can also drop out of eligibility mid-series, which the API
marks with null percentiles and "NaN" densities — both are normalized to None here.

Auth mirrors pagespeed_service (same Google project, same quota story): an API
key if one is set, otherwise the agency service account, otherwise keyless.
CRUX_API_KEY wins if set, else PAGESPEED_API_KEY — one key can serve both APIs
as long as the Chrome UX Report API is enabled on its project. The quota is 150
queries/minute per project, free, and cannot be raised, but a weekly per-client
sync is nowhere near it.

Optional env vars:
    CRUX_API_KEY        — API key; falls back to PAGESPEED_API_KEY.
    CRUX_TIMEOUT        — read timeout in seconds (default 30). The History API
                          is a fast table lookup, not a live audit like PSI.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any

_log = logging.getLogger(__name__)

_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord"

# Our strategy slug (shared with PageSpeed, so the dashboard's device toggle
# drives both) -> the CrUX formFactor enum. CrUX calls mobile "PHONE".
_FORM_FACTORS: dict[str, str] = {
    "desktop": "DESKTOP",
    "mobile": "PHONE",
    "tablet": "TABLET",
}

# CrUX metric name -> our short column prefix. Ordered as the dashboard shows
# them: the three Core Web Vitals first, then the supporting timings.
_METRICS: tuple[tuple[str, str], ...] = (
    ("largest_contentful_paint", "lcp"),
    ("interaction_to_next_paint", "inp"),
    ("cumulative_layout_shift", "cls"),
    ("first_contentful_paint", "fcp"),
    ("experimental_time_to_first_byte", "ttfb"),
)

METRIC_PREFIXES: tuple[str, ...] = tuple(prefix for _, prefix in _METRICS)

# Google's own good / needs-improvement thresholds, used to label the current
# reading. CLS is unitless; everything else is milliseconds.
THRESHOLDS: dict[str, dict[str, float]] = {
    "lcp":  {"good": 2500, "ni": 4000},
    "inp":  {"good": 200,  "ni": 500},
    "cls":  {"good": 0.1,  "ni": 0.25},
    "fcp":  {"good": 1800, "ni": 3000},
    "ttfb": {"good": 800,  "ni": 1800},
}

_RETRY_STATUSES = frozenset({500, 502, 503, 429})
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 2


def _api_key() -> str:
    return (
        (os.getenv("CRUX_API_KEY") or "").strip()
        or (os.getenv("PAGESPEED_API_KEY") or "").strip()
    )


def _timeout() -> int:
    try:
        return max(10, int(os.getenv("CRUX_TIMEOUT") or 30))
    except (TypeError, ValueError):
        return 30


def normalize_origin(url: str) -> str:
    """Reduce a page URL to the origin CrUX aggregates on (scheme + host[:port]).

    CrUX origin records cover every page under the origin, which is what we want
    for a site-level trend: a low-traffic client can be ineligible page by page
    while the origin as a whole still has enough samples to report.
    """
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    # Cut everything from the first '/' after the scheme — path, query, fragment.
    scheme, _, rest = u.partition("://")
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return f"{scheme}://{host}" if host else ""


def _auth() -> tuple[str, dict[str, str]]:
    """Return (query_suffix, headers) carrying whatever credential we have.

    Same precedence as pagespeed_service: explicit API key, else an OAuth bearer
    from the agency service account (so quota lands on our project), else
    keyless.
    """
    key = _api_key()
    if key:
        return f"?key={key}", {}
    try:
        import pagespeed_service

        auth = pagespeed_service.service_account_auth()
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("CrUX SA auth unavailable, falling back to keyless: %s", exc)
        auth = None
    if auth:
        token, _project = auth
        return "", {"Authorization": f"Bearer {token}"}
    return "", {}


class CruxNotFound(RuntimeError):
    """The origin/URL has too little Chrome traffic to appear in CrUX."""


def _post(body: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    """POST the History API and return parsed JSON.

    Raises CruxNotFound on 404 (ineligible origin — an expected outcome, not a
    broken connection) and RuntimeError on anything else.
    """
    suffix, headers = _auth()
    payload = json.dumps(body).encode("utf-8")
    headers = {**headers, "Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(_ENDPOINT + suffix, data=payload, headers=headers, method="POST")

    last_detail, last_code = "", 0
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                detail = json.loads(raw).get("error", {}).get("message", "") or raw[:300]
            except Exception:
                detail = ""
            if exc.code == 404:
                raise CruxNotFound(detail or "No CrUX data for this origin.") from exc
            last_detail, last_code = detail, exc.code
            if exc.code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise RuntimeError(f"CrUX API error (HTTP {exc.code}): {detail}") from exc
    raise RuntimeError(f"CrUX API error (HTTP {last_code}): {last_detail}")


def _num(value: Any) -> float | None:
    """Coerce a CrUX numeric to float, or None.

    Two shapes need care: CLS percentiles arrive as *strings* ("0.08"), and an
    ineligible period arrives as null (percentiles) or the string "NaN"
    (densities). Both become None so downstream code has one missing-value
    convention.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    # float("NaN") parses; it must not reach BigQuery or a chart.
    return None if out != out else out


def _iso_date(node: dict[str, Any] | None) -> str | None:
    """CrUX dates are {year, month, day} objects, not ISO strings."""
    if not isinstance(node, dict):
        return None
    try:
        return date(int(node["year"]), int(node["month"]), int(node["day"])).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _densities_by_bucket(metric: dict[str, Any], index: int) -> tuple[float | None, float | None, float | None]:
    """Pull (good, needs_improvement, poor) fractions for one collection period.

    CrUX always returns exactly three histogram bins, ordered best to worst, so
    they map positionally onto good/NI/poor. A shorter array (a metric Google
    bins differently in future) yields Nones rather than a mis-labeled number.
    """
    bins = metric.get("histogramTimeseries") or []
    if len(bins) < 3:
        return None, None, None
    out: list[float | None] = []
    for b in bins[:3]:
        densities = (b or {}).get("densities") or []
        out.append(_num(densities[index]) if index < len(densities) else None)
    return out[0], out[1], out[2]


def fetch_history(url: str, strategy: str = "desktop", *, timeout: int | None = None) -> dict[str, Any]:
    """Fetch the weekly CrUX history for `url`'s origin. Raises on API failure.

    Returns::

        {"origin": ..., "form_factor": "desktop", "not_enough_data": False,
         "periods": [{"period_start": "2025-01-01", "period_end": "2025-01-28",
                      "lcp_p75": 1362.0, "lcp_good": 0.91, "lcp_ni": 0.05,
                      "lcp_poor": 0.03, ... }, ...]}

    Periods run oldest to newest. An origin with too little traffic comes back
    with ``not_enough_data: True`` and no periods — that is a fact about the
    site, not an error, so it does not raise.
    """
    origin = normalize_origin(url)
    if not origin:
        raise ValueError("A URL is required.")
    strat = (strategy or "desktop").strip().lower()
    form_factor = _FORM_FACTORS.get(strat)
    if not form_factor:
        strat, form_factor = "desktop", "DESKTOP"

    body = {
        "origin": origin,
        "formFactor": form_factor,
        "metrics": [name for name, _ in _METRICS],
    }
    # Deliberately no collectionPeriodCount: the field is newer than the API's
    # published schema and an unknown field is a hard 400 here, so we take the
    # default 25 weeks (~6 months) rather than risk failing the whole call for
    # the extra 15.
    try:
        data = _post(body, timeout=timeout if timeout is not None else _timeout())
    except CruxNotFound as exc:
        _log.info("CrUX has no data for %s (%s): %s", origin, strat, exc)
        return {"origin": origin, "form_factor": strat, "not_enough_data": True, "periods": []}

    record = data.get("record") or {}
    metrics = record.get("metrics") or {}
    collection_periods = record.get("collectionPeriods") or []

    periods: list[dict[str, Any]] = []
    for i, period in enumerate(collection_periods):
        row: dict[str, Any] = {
            "period_start": _iso_date((period or {}).get("firstDate")),
            "period_end": _iso_date((period or {}).get("lastDate")),
        }
        if not row["period_end"]:
            # Without an end date the row has no key to store or plot against.
            continue
        for metric_name, prefix in _METRICS:
            metric = metrics.get(metric_name) or {}
            p75s = ((metric.get("percentilesTimeseries") or {}).get("p75s")) or []
            row[f"{prefix}_p75"] = _num(p75s[i]) if i < len(p75s) else None
            good, ni, poor = _densities_by_bucket(metric, i)
            row[f"{prefix}_good"] = good
            row[f"{prefix}_ni"] = ni
            row[f"{prefix}_poor"] = poor
        periods.append(row)

    return {
        "origin": record.get("key", {}).get("origin") or origin,
        "form_factor": strat,
        "not_enough_data": not periods,
        "periods": periods,
    }


def build_crux_snapshot(url: str, strategy: str = "desktop") -> dict[str, Any]:
    """fetch_history() that never raises — errors surface under 'error'.

    Mirrors pagespeed_service.build_pagespeed_snapshot so a CrUX hiccup can
    never interrupt the weekly sync that also writes the Lighthouse scores.
    """
    origin = normalize_origin(url)
    strat = (strategy or "desktop").strip().lower()
    if not origin:
        return {"origin": origin, "form_factor": strat, "periods": [], "error": "No URL configured"}
    try:
        return fetch_history(origin, strat)
    except Exception as exc:
        _log.warning("CrUX history fetch failed [%s/%s]: %s", origin, strat, exc)
        return {"origin": origin, "form_factor": strat, "periods": [], "error": str(exc)[:300]}
