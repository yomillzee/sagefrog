"""Shared LinkedIn reference taxonomies and URN label resolution.

LinkedIn returns demographic breakdowns as bare ids / URNs — ``urn:li:title:5678``,
``urn:li:seniority:9``, ``SIZE_51_TO_200`` — and a report full of those is
unreadable. Two kinds of lookup turn them into labels:

* **Static taxonomies.** Seniority and function are small, stable, closed sets, so
  they resolve locally with no API call at all.
* **Reference-data endpoints.** Industry, geo, title and organization are large and
  version-dependent, so they resolve against the API and fall back to a readable
  placeholder when the endpoint is unavailable or shaped differently across
  versions. Every id here is immutable, so a resolved label is cached and never
  re-fetched.

Both the *organic* follower demographics (``linkedin_organic_service``) and the
*ads* member demographics (``linkedin_service.fetch_ads_demographics``) draw on the
same taxonomies, which is why they live here rather than in either caller.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

_log = logging.getLogger(__name__)


# Stable LinkedIn taxonomies — resolved locally so the common demographic
# dimensions need no extra reference-data calls. (Industry, geo, title and
# organization are far larger and version-dependent, so those resolve against the
# API with a raw-id fallback.)
SENIORITY_LABELS: dict[str, str] = {
    "1": "Unpaid", "2": "In training", "3": "Entry", "4": "Senior",
    "5": "Manager", "6": "Director", "7": "VP", "8": "CXO",
    "9": "Partner", "10": "Owner",
}
FUNCTION_LABELS: dict[str, str] = {
    "1": "Accounting", "2": "Administrative", "3": "Arts & Design",
    "4": "Business Development", "5": "Community & Social Services",
    "6": "Consulting", "7": "Education", "8": "Engineering",
    "9": "Entrepreneurship", "10": "Finance", "11": "Healthcare Services",
    "12": "Human Resources", "13": "Information Technology", "14": "Legal",
    "15": "Marketing", "16": "Media & Communications",
    "17": "Military & Protective Services", "18": "Operations",
    "19": "Product Management", "20": "Program & Project Management",
    "21": "Purchasing", "22": "Quality Assurance", "23": "Real Estate",
    "24": "Research", "25": "Sales", "26": "Support",
}


def humanize_staff_range(value: str) -> str:
    """``SIZE_1_TO_10`` -> ``1-10``; ``SIZE_10001_OR_MORE`` -> ``10,001+``."""
    text = str(value or "").replace("SIZE_", "").strip()
    if not text:
        return "Unknown"
    if text.endswith("OR_MORE"):
        num = text.replace("_OR_MORE", "")
        try:
            return f"{int(num):,}+"
        except ValueError:
            return f"{num}+"
    parts = text.split("_TO_")
    try:
        if len(parts) == 2:
            return f"{int(parts[0]):,}-{int(parts[1]):,}"
        return f"{int(parts[0]):,}"
    except ValueError:
        return text.replace("_", " ").title()


def humanize_enum(value: str) -> str:
    return str(value or "").replace("_", " ").title() or "Unknown"


# Reference-data endpoint paths (NOT a naive plural of the kind — "industry" ->
# "industries", "geo" has no trailing 's', an organization resolves through
# "organizations"). A wrong path 404s and every label silently falls back to the
# raw id, so keep this mapping explicit.
REFERENCE_ENDPOINTS: dict[str, str] = {
    "industry": "industries",
    "geo": "geo",
    "country": "countries",
    "title": "titles",
    "organization": "organizations",
}
# Friendlier placeholder when a lookup can't resolve, per kind.
REFERENCE_FALLBACK: dict[str, str] = {
    "industry": "Industry",
    "geo": "Region",
    "country": "Country",
    "title": "Job title",
    "organization": "Company",
}


def reference_fallback_label(kind: str, ref_id: str) -> str:
    """The placeholder shown when a reference lookup can't resolve an id."""
    return f"{REFERENCE_FALLBACK.get(kind, kind.title())} {ref_id}"


def label_from_payload(payload: dict[str, Any], *, fallback: str) -> str:
    """Pull a display name out of a reference-data payload.

    The reference endpoints are not consistent with each other: some return
    ``localizedName``, some a ``defaultLocalizedName`` wrapper, and ``name`` is
    sometimes a localized object and sometimes a plain string. Organizations add
    ``vanityName``. Try them all before giving up on the fallback.
    """
    name = payload.get("name")
    return (
        payload.get("localizedName")
        or (payload.get("defaultLocalizedName") or {}).get("value")
        # ``name`` is sometimes a localized object, sometimes a plain string.
        or (name.get("localized", {}).get("en_US") if isinstance(name, dict) else name)
        or payload.get("vanityName")
        or fallback
    )


def resolve_reference_label(
    kind: str,
    ref_id: str,
    *,
    get: Callable[[str], dict[str, Any]],
    cache: dict[str, str],
) -> str:
    """Best-effort localized name for a reference id, memoized in ``cache``.

    ``get`` performs one authenticated GET for a path like ``/titles/5678`` and
    returns the decoded payload; the caller supplies it so this module stays free
    of transport and token handling. A failed lookup caches its fallback too — the
    id is immutable, so a second attempt in the same sync would fail identically
    and only cost another call against a rate-limited API.
    """
    key = f"{kind}:{ref_id}"
    if key in cache:
        return cache[key]
    fallback = reference_fallback_label(kind, ref_id)
    endpoint = REFERENCE_ENDPOINTS.get(kind, kind)
    try:
        label = label_from_payload(get(f"/{endpoint}/{ref_id}"), fallback=fallback)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("%s label lookup failed for %s: %s", kind, ref_id, exc)
        label = fallback
    label = str(label or fallback)
    cache[key] = label
    return label
