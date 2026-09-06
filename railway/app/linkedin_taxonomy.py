"""Shared LinkedIn reference taxonomies and URN label resolution.

LinkedIn returns demographic breakdowns as bare ids / URNs — ``urn:li:title:5678``,
``urn:li:seniority:9``, ``SIZE_51_TO_200`` — and a report full of those is
unreadable. Two kinds of lookup turn them into labels:

* **Static taxonomies.** Seniority, function and LinkedIn's original industry
  codes are closed, stable sets, so they resolve locally with no API call at all.
* **Reference-data endpoints.** Geo, title, organization and the newer four-digit
  industry ids are large and version-dependent, so they resolve against the API
  (on the base :data:`STANDARDIZED_DATA_KINDS` documents) and fall back to a
  readable placeholder when the endpoint is unavailable or shaped differently
  across versions. Every id here is immutable, so a resolved label is cached and
  never re-fetched.

Both the *organic* follower demographics (``linkedin_organic_service``) and the
*ads* member demographics (``linkedin_service.fetch_ads_demographics``) draw on the
same taxonomies, which is why they live here rather than in either caller.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from collections.abc import Callable

_log = logging.getLogger(__name__)


# Stable LinkedIn taxonomies — resolved locally so the common demographic
# dimensions need no extra reference-data calls. (Geo, title and organization are
# far larger and version-dependent, so those resolve against the API with a
# raw-id fallback.)
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

# LinkedIn's original industry codes. They are a closed, stable set — every id
# here still resolves to the same industry — so they are answered locally rather
# than costing one reference call per category on every sync. The newer industry
# taxonomy adds four-digit ids (``urn:li:industry:1862``) that are NOT listed
# here and still resolve against the API.
INDUSTRY_LABELS: dict[str, str] = {
    "1": "Defense & Space", "3": "Computer Hardware", "4": "Computer Software",
    "5": "Computer Networking", "6": "Internet", "7": "Semiconductors",
    "8": "Telecommunications", "9": "Law Practice", "10": "Legal Services",
    "11": "Management Consulting", "12": "Biotechnology",
    "13": "Medical Practice", "14": "Hospital & Health Care",
    "15": "Pharmaceuticals", "16": "Veterinary", "17": "Medical Devices",
    "18": "Cosmetics", "19": "Apparel & Fashion", "20": "Sporting Goods",
    "21": "Tobacco", "22": "Supermarkets", "23": "Food Production",
    "24": "Consumer Electronics", "25": "Consumer Goods", "26": "Furniture",
    "27": "Retail", "28": "Entertainment", "29": "Gambling & Casinos",
    "30": "Leisure, Travel & Tourism", "31": "Hospitality",
    "32": "Restaurants", "33": "Sports", "34": "Food & Beverages",
    "35": "Motion Pictures & Film", "36": "Broadcast Media",
    "37": "Museums & Institutions", "38": "Fine Art",
    "39": "Performing Arts", "40": "Recreational Facilities & Services",
    "41": "Banking", "42": "Insurance", "43": "Financial Services",
    "44": "Real Estate", "45": "Investment Banking",
    "46": "Investment Management", "47": "Accounting", "48": "Construction",
    "49": "Building Materials", "50": "Architecture & Planning",
    "51": "Civil Engineering", "52": "Aviation & Aerospace",
    "53": "Automotive", "54": "Chemicals", "55": "Machinery",
    "56": "Mining & Metals", "57": "Oil & Energy", "58": "Shipbuilding",
    "59": "Utilities", "60": "Textiles", "61": "Paper & Forest Products",
    "62": "Railroad Manufacture", "63": "Farming", "64": "Ranching",
    "65": "Dairy", "66": "Fishery", "67": "Primary/Secondary Education",
    "68": "Higher Education", "69": "Education Management", "70": "Research",
    "71": "Military", "72": "Legislative Office", "73": "Judiciary",
    "74": "International Affairs", "75": "Government Administration",
    "76": "Executive Office", "77": "Law Enforcement", "78": "Public Safety",
    "79": "Public Policy", "80": "Marketing & Advertising",
    "81": "Newspapers", "82": "Publishing", "83": "Printing",
    "84": "Information Services", "85": "Libraries",
    "86": "Environmental Services", "87": "Package/Freight Delivery",
    "88": "Individual & Family Services", "89": "Religious Institutions",
    "90": "Civic & Social Organization", "91": "Consumer Services",
    "92": "Transportation/Trucking/Railroad", "93": "Warehousing",
    "94": "Airlines/Aviation", "95": "Maritime",
    "96": "Information Technology & Services", "97": "Market Research",
    "98": "Public Relations & Communications", "99": "Design",
    "100": "Nonprofit Organization Management", "101": "Fund-Raising",
    "102": "Program Development", "103": "Writing & Editing",
    "104": "Staffing & Recruiting", "105": "Professional Training & Coaching",
    "106": "Venture Capital & Private Equity",
    "107": "Political Organization", "108": "Translation & Localization",
    "109": "Computer Games", "110": "Events Services", "111": "Arts & Crafts",
    "112": "Electrical/Electronic Manufacturing", "113": "Online Media",
    "114": "Nanotechnology", "115": "Music",
    "116": "Logistics & Supply Chain", "117": "Plastics",
    "118": "Computer & Network Security", "119": "Wireless",
    "120": "Alternative Dispute Resolution",
    "121": "Security & Investigations", "122": "Facilities Services",
    "123": "Outsourcing/Offshoring", "124": "Health, Wellness & Fitness",
    "125": "Alternative Medicine", "126": "Media Production",
    "127": "Animation", "128": "Commercial Real Estate",
    "129": "Capital Markets", "130": "Think Tanks", "131": "Philanthropy",
    "132": "E-Learning", "133": "Wholesale", "134": "Import & Export",
    "135": "Mechanical or Industrial Engineering", "136": "Photography",
    "137": "Human Resources", "138": "Business Supplies & Equipment",
    "139": "Mental Health Care", "140": "Graphic Design",
    "141": "International Trade & Development", "142": "Wine & Spirits",
    "143": "Luxury Goods & Jewelry", "144": "Renewables & Environment",
    "145": "Glass, Ceramics & Concrete", "146": "Packaging & Containers",
    "147": "Industrial Automation", "148": "Government Relations",
}

# Which static table answers a reference ``kind``, before any API call.
_STATIC_REFERENCE_TABLES: dict[str, dict[str, str]] = {"industry": INDUSTRY_LABELS}


def urn_id(urn: str) -> str:
    """Trailing id of a URN. Non-URN values (``SIZE_51_TO_200``) pass through."""
    return str(urn or "").strip().split(":")[-1]


def static_reference_label(kind: str, ref_id: str) -> str | None:
    """A locally known label for a reference id, or None if we have to ask the API."""
    return _STATIC_REFERENCE_TABLES.get(kind, {}).get(str(ref_id).strip())


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
# The standardized-data taxonomies below live on LinkedIn's **/v2** base, not the
# versioned **/rest** one: asked for through /rest they answer "No virtual
# resource found for: geo" and every label degrades to a raw id. Organizations
# are the exception — organizationsLookup *is* a versioned resource. Callers own
# the transport, so this set is what tells them which base a kind needs.
STANDARDIZED_DATA_KINDS = frozenset({"industry", "geo", "country", "title"})

# An unresolved label is the fallback placeholder plus a bare id — "Region
# 90000070", "Industry 1862", "Job title 5678". Recognising one lets a reader
# re-label it (a static taxonomy may know it now) or drop it, rather than showing
# a client an id dressed up as a category.
_UNRESOLVED_LABEL_RE = re.compile(
    r"^(?:{})\s+\d+$".format("|".join(
        re.escape(word)
        for word in sorted(set(REFERENCE_FALLBACK.values()) | {"Seniority", "Function"})
    ))
)


def is_unresolved_label(label: str) -> bool:
    """True when ``label`` is still a placeholder standing in for a raw id."""
    return bool(_UNRESOLVED_LABEL_RE.match(str(label or "").strip()))


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
    static = static_reference_label(kind, ref_id)
    if static:
        cache[key] = static
        return static
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


# Demographic dimension -> the static table that labels it. Both spellings of the
# function dimension appear: organic follower demographics call it "function",
# ads member demographics "job_function".
_DIMENSION_TABLES: dict[str, dict[str, str]] = {
    "seniority": SENIORITY_LABELS,
    "function": FUNCTION_LABELS,
    "job_function": FUNCTION_LABELS,
    "industry": INDUSTRY_LABELS,
}


def relabel_demographic(dimension: str, category: str, category_urn: str) -> str:
    """Re-label a stored demographic category that never resolved.

    Labels are resolved once, at sync time, and stored alongside the raw URN — so
    a category that fell back to a placeholder keeps that placeholder until the
    next sync. A reader calls this to apply what the static taxonomies know
    *now*, which turns "Industry 105" back into "Professional Training &
    Coaching" on the next page load instead of the next sync. A label that
    already resolved is returned untouched.
    """
    label = str(category or "").strip()
    if label and not is_unresolved_label(label):
        return label
    ref_id = urn_id(category_urn)
    if not ref_id:
        return label
    table = _DIMENSION_TABLES.get(str(dimension or "").strip())
    if table is not None:
        return table.get(ref_id) or label
    if dimension == "company_size":
        return humanize_staff_range(category_urn)
    return label
