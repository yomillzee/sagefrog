"""Vendor + category knowledge base for the Consent & Tracking Health scanner.

This module is deliberately pure (no I/O, no browser, no DB) so it is trivially
unit-testable and can be reasoned about in isolation. It answers three questions
about a captured artifact (a script, network request, cookie, or storage entry):

    1. *Who* is it? -> a vendor (Google Analytics, Meta Pixel, ...) or None.
    2. *What kind of tracking* is it? -> a category (analytics, advertising, ...).
    3. *Does it carry / store a persistent identifier?* -> the single most
       important signal for consent compliance.

The scanner captures raw browser observations; the classifier (consent_classifier
via these helpers) turns them into findings; the evaluator judges those findings
against a client's configurable consent expectations. Crucially, this layer keeps
three concepts separate that a naive scanner conflates:

    * a script being **downloaded** (loading googletagmanager.com/gtag/js is not,
      by itself, a violation — GTM can run in a consent-gated configuration),
    * a request being **transmitted** (a network beacon left the browser), and
    * an **identifier** being stored (a cookie/localStorage value that can
      re-identify a visitor) or transmitted (an id smuggled in a request).

Google Consent Mode "cookieless pings" — requests to the GA/Ads collect
endpoints that are explicitly flagged consent-denied (``gcs=G100``) and carry no
client id — are recognised here as a distinct, *non-violating* evidence type so
they are never mislabelled as a leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Consent categories
# ---------------------------------------------------------------------------
# Categories mirror the IAB TCF / Google Consent Mode vocabulary marketers know.
# ``requires_consent`` is the *default* expectation under GDPR/ePrivacy; a client
# can override per-category in their consent expectations config.

CATEGORY_ESSENTIAL = "essential"
CATEGORY_FUNCTIONAL = "functional"
CATEGORY_ANALYTICS = "analytics"
CATEGORY_ADVERTISING = "advertising"
CATEGORY_SOCIAL = "social"
CATEGORY_PERSONALIZATION = "personalization"
CATEGORY_CONSENT = "consent"  # the CMP itself (must run before consent)
CATEGORY_UNKNOWN = "unknown"

CATEGORY_ORDER: tuple[str, ...] = (
    CATEGORY_ADVERTISING,
    CATEGORY_ANALYTICS,
    CATEGORY_SOCIAL,
    CATEGORY_PERSONALIZATION,
    CATEGORY_FUNCTIONAL,
    CATEGORY_CONSENT,
    CATEGORY_ESSENTIAL,
    CATEGORY_UNKNOWN,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_ESSENTIAL: "Strictly necessary",
    CATEGORY_FUNCTIONAL: "Functional",
    CATEGORY_ANALYTICS: "Analytics",
    CATEGORY_ADVERTISING: "Advertising",
    CATEGORY_SOCIAL: "Social media",
    CATEGORY_PERSONALIZATION: "Personalization",
    CATEGORY_CONSENT: "Consent management",
    CATEGORY_UNKNOWN: "Unclassified",
}

# Categories that, by default, require prior opt-in consent before they may set
# identifiers or fire identified beacons. Essential + the CMP are always allowed.
DEFAULT_CONSENT_REQUIRED: frozenset[str] = frozenset(
    {CATEGORY_ANALYTICS, CATEGORY_ADVERTISING, CATEGORY_SOCIAL, CATEGORY_PERSONALIZATION}
)


@dataclass(frozen=True)
class Vendor:
    key: str
    name: str
    category: str
    # Substrings matched (case-insensitively) against a request/script hostname.
    host_patterns: tuple[str, ...] = ()
    # Cookie / storage *name* patterns this vendor is known to set.
    cookie_patterns: tuple[str, ...] = ()
    # Human-readable purpose, shown in the UI so a marketer understands the "why".
    purpose: str = ""
    is_cmp: bool = False


# ---------------------------------------------------------------------------
# Vendor registry
# ---------------------------------------------------------------------------
# Ordered roughly by how commonly they appear. Matching is first-hit by host, so
# more specific patterns should precede generic ones where they might overlap.

VENDORS: tuple[Vendor, ...] = (
    # ---- Consent management platforms (must be allowed to run pre-consent) ----
    Vendor("onetrust", "OneTrust", CATEGORY_CONSENT,
           ("cdn.cookielaw.org", "onetrust.com", "cookiepro.com", "otSDKStub"),
           ("OptanonConsent", "OptanonAlertBoxClosed", "eupubconsent"),
           "Consent management platform — collects and stores the visitor's choices.",
           is_cmp=True),
    Vendor("cookiebot", "Cookiebot", CATEGORY_CONSENT,
           ("consent.cookiebot.com", "cookiebot.com"),
           ("CookieConsent",),
           "Consent management platform.", is_cmp=True),
    Vendor("iubenda", "iubenda", CATEGORY_CONSENT,
           ("iubenda.com", "cdn.iubenda.com"),
           ("_iub", "euconsent"),
           "Consent management platform.", is_cmp=True),
    Vendor("termly", "Termly", CATEGORY_CONSENT,
           ("termly.io",), ("TERMLY_",),
           "Consent management platform.", is_cmp=True),
    Vendor("usercentrics", "Usercentrics", CATEGORY_CONSENT,
           ("usercentrics.eu", "app.usercentrics.eu"), ("uc_",),
           "Consent management platform.", is_cmp=True),
    Vendor("cmp_generic", "Consent banner", CATEGORY_CONSENT,
           (), ("euconsent-v2", "__cmpconsent", "cookieconsent_status", "cookie_consent"),
           "Cookie-consent banner.", is_cmp=True),

    # ---- Tag managers ----
    Vendor("gtm", "Google Tag Manager", CATEGORY_FUNCTIONAL,
           ("googletagmanager.com/gtm.js", "googletagmanager.com/ns.html"),
           (),
           "Tag manager — a container that can load other tags. Loading it is not "
           "itself tracking; what matters is which tags it fires and when."),

    # ---- Analytics ----
    Vendor("ga4", "Google Analytics 4", CATEGORY_ANALYTICS,
           ("google-analytics.com", "googletagmanager.com/gtag/js", "analytics.google.com",
            "/g/collect", "/mp/collect"),
           ("_ga", "_gid", "_gat", "__utm", "_ga_"),
           "Measures site usage and attributes sessions. Sets a persistent client "
           "id (_ga) used to recognise returning visitors."),
    Vendor("gads", "Google Ads", CATEGORY_ADVERTISING,
           ("googleadservices.com", "googlesyndication.com", "google.com/ads",
            "google.com/pagead", "doubleclick.net", "/pagead/", "/td/"),
           ("_gcl_au", "_gcl_aw", "_gcl_dc", "IDE", "test_cookie"),
           "Conversion tracking and remarketing for Google Ads. Sets advertising "
           "identifiers used to build audiences and attribute conversions."),
    Vendor("hotjar", "Hotjar", CATEGORY_ANALYTICS,
           ("hotjar.com", "hotjar.io", "static.hotjar.com"),
           ("_hj",),
           "Session recording and heatmaps — records how visitors interact with pages."),
    Vendor("clarity", "Microsoft Clarity", CATEGORY_ANALYTICS,
           ("clarity.ms",), ("_clck", "_clsk", "CLID"),
           "Session recording and heatmaps."),
    Vendor("mixpanel", "Mixpanel", CATEGORY_ANALYTICS,
           ("mixpanel.com", "mxpnl.com"), ("mp_",),
           "Product analytics — tracks per-user events."),
    Vendor("amplitude", "Amplitude", CATEGORY_ANALYTICS,
           ("amplitude.com",), ("amplitude_", "amp_"),
           "Product analytics — tracks per-user events."),
    Vendor("segment", "Segment", CATEGORY_ANALYTICS,
           ("segment.com", "segment.io", "cdn.segment.com"),
           ("ajs_anonymous_id", "ajs_user_id"),
           "Customer data platform — collects events and fans them out to other tools. "
           "Sets a persistent anonymous id."),
    Vendor("matomo", "Matomo", CATEGORY_ANALYTICS,
           ("matomo.cloud", "matomo.org"), ("_pk_id", "_pk_ses"),
           "Web analytics."),
    Vendor("fullstory", "FullStory", CATEGORY_ANALYTICS,
           ("fullstory.com", "fs.js"), ("fs_uid",),
           "Session replay."),
    Vendor("plausible", "Plausible", CATEGORY_ANALYTICS,
           ("plausible.io",), (),
           "Privacy-focused, cookieless analytics."),

    # ---- Advertising / social pixels ----
    Vendor("meta", "Meta Pixel", CATEGORY_ADVERTISING,
           ("facebook.com/tr", "connect.facebook.net", "facebook.net"),
           ("_fbp", "_fbc", "fr"),
           "Facebook/Instagram advertising pixel. Sets _fbp to identify a browser "
           "for ad targeting and conversion attribution."),
    Vendor("linkedin", "LinkedIn Insight Tag", CATEGORY_ADVERTISING,
           ("linkedin.com", "licdn.com", "px.ads.linkedin.com", "snap.licdn.com"),
           ("li_sugr", "bcookie", "UserMatchHistory", "AnalyticsSyncHistory", "li_gc"),
           "LinkedIn advertising and conversion tracking."),
    Vendor("tiktok", "TikTok Pixel", CATEGORY_ADVERTISING,
           ("tiktok.com", "analytics.tiktok.com", "tiktokcdn.com"),
           ("_ttp",),
           "TikTok advertising pixel."),
    Vendor("twitter", "X/Twitter Pixel", CATEGORY_ADVERTISING,
           ("ads-twitter.com", "t.co", "static.ads-twitter.com"),
           ("personalization_id", "muc_ads"),
           "X (Twitter) advertising pixel."),
    Vendor("bing", "Microsoft Advertising (UET)", CATEGORY_ADVERTISING,
           ("bat.bing.com", "clarity.ms/uet"), ("MUID", "_uetsid", "_uetvid"),
           "Microsoft Advertising universal event tracking (UET)."),
    Vendor("pinterest", "Pinterest Tag", CATEGORY_ADVERTISING,
           ("pinterest.com", "ct.pinterest.com"), ("_pin_unauth", "_epik"),
           "Pinterest advertising pixel."),
    Vendor("reddit", "Reddit Pixel", CATEGORY_ADVERTISING,
           ("reddit.com/api", "redditstatic.com", "alb.reddit.com"),
           ("_rdt_uuid",),
           "Reddit advertising pixel."),
    Vendor("doubleclick_fls", "Floodlight (Campaign Manager)", CATEGORY_ADVERTISING,
           ("fls.doubleclick.net", "ad.doubleclick.net"), (),
           "Google Campaign Manager Floodlight conversion tags."),

    # ---- Chat / functional third parties ----
    Vendor("hubspot", "HubSpot", CATEGORY_ANALYTICS,
           ("hs-scripts.com", "hs-analytics.net", "hsubspot.com", "hubspot.com",
            "hsforms.net", "hs-banner.com"),
           ("hubspotutk", "__hstc", "__hssc", "__hssrc"),
           "HubSpot analytics and forms — sets a visitor token (hubspotutk)."),
    Vendor("intercom", "Intercom", CATEGORY_FUNCTIONAL,
           ("intercom.io", "intercomcdn.com"), ("intercom-",),
           "Live chat / customer messaging."),
    Vendor("drift", "Drift", CATEGORY_FUNCTIONAL,
           ("drift.com", "driftt.com"), ("drift_",),
           "Live chat."),
    Vendor("youtube", "YouTube embed", CATEGORY_SOCIAL,
           ("youtube.com", "youtube-nocookie.com", "ytimg.com"),
           ("VISITOR_INFO1_LIVE", "YSC"),
           "Embedded YouTube player. The privacy-enhanced (nocookie) domain avoids "
           "setting identifiers until playback."),
    Vendor("vimeo", "Vimeo embed", CATEGORY_SOCIAL,
           ("vimeo.com", "vimeocdn.com"), ("vuid",),
           "Embedded Vimeo player."),
    Vendor("gstatic", "Google static assets", CATEGORY_ESSENTIAL,
           ("gstatic.com", "googleapis.com/fonts", "fonts.googleapis.com",
            "fonts.gstatic.com"),
           (),
           "Static assets / fonts. Generally no identifier."),
    Vendor("cloudflare", "Cloudflare", CATEGORY_ESSENTIAL,
           ("cloudflare.com", "cloudflareinsights.com"), ("__cf", "cf_"),
           "CDN / security. __cf_bm is a bot-management cookie (strictly necessary)."),
    Vendor("recaptcha", "reCAPTCHA", CATEGORY_ESSENTIAL,
           ("google.com/recaptcha", "gstatic.com/recaptcha", "recaptcha.net"),
           ("_GRECAPTCHA",),
           "Spam / abuse protection (strictly necessary)."),
)

VENDORS_BY_KEY: dict[str, Vendor] = {v.key: v for v in VENDORS}


# ---------------------------------------------------------------------------
# Identifier heuristics
# ---------------------------------------------------------------------------
# Cookie / storage names that are, by their very name, persistent identifiers.
_ID_NAME_PATTERNS: tuple[str, ...] = (
    "_ga", "_gid", "_gcl_au", "_fbp", "_fbc", "_uetvid", "_uetsid", "muid",
    "ide", "_rdt_uuid", "_ttp", "ajs_anonymous_id", "ajs_user_id", "hubspotutk",
    "__hstc", "_hjid", "_pk_id", "amplitude_id", "amp_", "mp_", "clid", "_clck",
    "fs_uid", "_pin_unauth", "personalization_id", "visitor_info1_live", "uid",
    "client_id", "cid", "user_id", "device_id", "anonymous_id",
)

# Query/body parameter names that carry an identifier when present in a beacon.
_ID_PARAM_NAMES: frozenset[str] = frozenset({
    "cid", "uid", "_fbp", "fbp", "_fbc", "fbc", "client_id", "user_id",
    "uuid", "device_id", "anonymous_id", "tid_cid", "en_cid", "sid",
})

# A value that looks like a UUID or a GA-style client id (e.g. "GA1.2.12345.678").
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_GA_CID_RE = re.compile(r"(?:GA\d\.\d\.)?\d{6,}\.\d{6,}")
_LONG_RANDOM_RE = re.compile(r"^[A-Za-z0-9_\-]{16,}$")

# Cookie names that are persistent-but-necessary (do not treat as consent leaks).
_ESSENTIAL_COOKIE_NAMES: frozenset[str] = frozenset({
    "__cf_bm", "cf_clearance", "_grecaptcha", "csrftoken", "csrf_token",
    "xsrf-token", "session", "sessionid", "jsessionid", "phpsessid",
    "optanonconsent", "optanonalertboxclosed", "cookieconsent", "cookieconsent_status",
    "euconsent-v2", "_iub_cs", "termly", "uc_settings",
})


def normalize_host(url_or_host: str) -> str:
    """Return the lowercased hostname (and path hint) for pattern matching."""
    s = (url_or_host or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    parsed = urlparse(s)
    return (parsed.netloc + parsed.path).lower()


def _domain_match(domain: str, netloc: str) -> bool:
    """True when netloc is `domain` or a subdomain of it (boundary-aware)."""
    netloc = netloc.split(":")[0]
    return netloc == domain or netloc.endswith("." + domain)


def _pattern_matches(pattern: str, host_path: str) -> bool:
    """Match a vendor pattern against a "netloc/path" string with domain awareness.

    Three pattern shapes are supported, so a domain like ``t.co`` never matches an
    unrelated host such as ``content.com``:

        "/g/collect"                 -> path substring only
        "facebook.com" / "t.co"      -> domain or subdomain of that registrable name
        "googletagmanager.com/gtag/js" -> domain match AND path substring
        "otSDKStub"                  -> bare token (no dot/slash): plain substring
    """
    p = pattern.lower().strip()
    if not p:
        return False
    netloc = host_path.split("/", 1)[0]
    if p.startswith("/"):
        return p in host_path[len(netloc):]
    if "/" in p:
        dom, _, path_part = p.partition("/")
        return _domain_match(dom, netloc) and ("/" + path_part) in host_path
    if "." in p:
        return _domain_match(p, netloc)
    return p in host_path


def classify_vendor(url: str) -> Vendor | None:
    """Return the first vendor whose host pattern matches this URL, else None."""
    hay = normalize_host(url)
    if not hay:
        return None
    for vendor in VENDORS:
        for pat in vendor.host_patterns:
            if _pattern_matches(pat, hay):
                return vendor
    return None


def classify_cookie_vendor(name: str, domain: str = "") -> Vendor | None:
    """Match a cookie/storage entry to a vendor by name, then by domain."""
    lname = (name or "").strip().lower()
    for vendor in VENDORS:
        for pat in vendor.cookie_patterns:
            if lname.startswith(pat.lower()) or pat.lower() in lname:
                return vendor
    if domain:
        return classify_vendor(domain)
    return None


def is_essential_cookie(name: str) -> bool:
    lname = (name or "").strip().lower()
    if lname in _ESSENTIAL_COOKIE_NAMES:
        return True
    return lname.startswith("__cf") or lname.startswith("_grecaptcha")


def looks_like_identifier(name: str, value: str) -> bool:
    """Heuristic: does this cookie/storage entry hold a persistent identifier?

    True when the *name* is a known id, or the *value* looks like a UUID, a
    GA-style client id, or a long high-entropy random string. Short, obviously
    non-identifying values (flags like "true", "1", timestamps) return False.
    """
    lname = (name or "").strip().lower()
    for pat in _ID_NAME_PATTERNS:
        if lname.startswith(pat) or lname == pat:
            return True
    val = (value or "").strip()
    if not val or len(val) < 12:
        return False
    if _UUID_RE.search(val) or _GA_CID_RE.search(val):
        return True
    # A single long random token with no spaces/punctuation reads as an id.
    first = val.split(".")[0] if "." in val else val
    return bool(_LONG_RANDOM_RE.match(first)) and not val.replace(".", "").isdigit()


# ---------------------------------------------------------------------------
# Google Consent Mode signal parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConsentModeSignal:
    """Parsed state of a Google collect request's Consent Mode parameters."""
    present: bool = False
    ad_storage_granted: bool | None = None
    analytics_storage_granted: bool | None = None
    raw_gcs: str = ""
    # True when the request is a Consent Mode cookieless ping: consent is denied
    # AND the request carries no client identifier. These are explicitly allowed
    # even before consent — they are the *point* of Consent Mode.
    cookieless_ping: bool = False


def _has_client_identifier(params: dict[str, list[str]]) -> bool:
    for key in params:
        if key.lower() in _ID_PARAM_NAMES:
            values = params.get(key) or []
            if any((v or "").strip() for v in values):
                return True
    # GA4 sends the client id as `cid`; a real cid is "123456.789012".
    cid = (params.get("cid") or [""])[0]
    return bool(cid and _GA_CID_RE.search(cid))


def parse_consent_mode(url: str) -> ConsentModeSignal:
    """Parse Google Consent Mode signals from a GA/Ads collect request.

    ``gcs`` is a 4-char code: the literal prefix "G1" followed by the ad_storage
    and analytics_storage status digits (0/1). ``G100`` = both denied, ``G111`` =
    both granted, ``G101`` = analytics only, etc. A request whose gcs denies
    storage and which carries no client id is a cookieless ping and must not be
    treated as a tracking leak.
    """
    try:
        q = parse_qs(urlparse(url).query, keep_blank_values=True)
    except Exception:
        return ConsentModeSignal()
    gcs = (q.get("gcs") or [""])[0].strip()
    if not gcs or not gcs.upper().startswith("G") or len(gcs) < 4:
        return ConsentModeSignal()
    try:
        ad = gcs[2] == "1"
        analytics = gcs[3] == "1"
    except IndexError:
        return ConsentModeSignal(present=True, raw_gcs=gcs)
    denied = (not ad) and (not analytics)
    cookieless = denied and not _has_client_identifier(q)
    return ConsentModeSignal(
        present=True,
        ad_storage_granted=ad,
        analytics_storage_granted=analytics,
        raw_gcs=gcs,
        cookieless_ping=cookieless,
    )


def request_carries_identifier(url: str, post_data: str | None = None) -> bool:
    """True when a network request smuggles an identifier in its query or body."""
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query, keep_blank_values=True)
    except Exception:
        q = {}
    if _has_client_identifier(q):
        return True
    body = (post_data or "").strip()
    if not body:
        return False
    # Form-encoded or query-style bodies (GA4/Meta batch beacons).
    try:
        bq = parse_qs(body, keep_blank_values=True)
        if _has_client_identifier(bq):
            return True
    except Exception:
        pass
    low = body.lower()
    return any(f'"{name}"' in low or f"{name}=" in low for name in _ID_PARAM_NAMES)


# Endpoints that are unambiguously *transmission* endpoints (a beacon fired),
# as opposed to a mere script download. Used to tell "downloaded gtag.js" apart
# from "sent a hit to /g/collect".
_BEACON_PATH_HINTS: tuple[str, ...] = (
    "/collect", "/g/collect", "/mp/collect", "/j/collect", "/tr", "/tr/",
    "/pagead/", "/td/", "/pixel", "/p.gif", "/ping", "/batch", "/e/", "/i/adsct",
    "/track", "/beacon", "/v1/", "/api/v2/track", "/li/track", "/attribution",
)


def is_beacon_request(url: str, resource_type: str = "") -> bool:
    """Heuristic: is this request a tracking beacon (a transmission), not an asset?"""
    rtype = (resource_type or "").lower()
    if rtype in ("image", "beacon", "ping", "xhr", "fetch"):
        hay = normalize_host(url)
        if any(h in hay for h in _BEACON_PATH_HINTS):
            return True
    hay = normalize_host(url)
    return any(h in hay for h in _BEACON_PATH_HINTS)
