"""Turn raw browser captures into typed, vendor-attributed findings.

Pure functions only (unit-testable). The scanner hands us a per-phase capture of
what the browser did; we translate every script, request, cookie, and storage
entry into a :class:`Finding` that names the vendor, the tracking category, and —
most importantly — the *evidence type*, which draws the bright line the brief
demands:

    script_loaded          a script was downloaded (NOT inherently a violation)
    network_request        a third-party request went out, no identifier
    tracking_beacon        a hit was transmitted to a tracking endpoint
    cookieless_ping        a Consent Mode consent-denied ping (allowed pre-consent)
    identifier_stored      a persistent id was written to a cookie / storage
    identifier_transmitted an id was smuggled inside an outbound request
    cookie_set             a non-identifying cookie was set
    consent_signal         a CMP / TCF / Consent Mode state was observed

Whether any given evidence is a *violation* depends on the phase it occurred in
and the client's consent expectations — that judgement lives in
``consent_evaluator``, deliberately not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import consent_knowledge as kb

# Evidence types (see module docstring).
EV_SCRIPT_LOADED = "script_loaded"
EV_NETWORK_REQUEST = "network_request"
EV_TRACKING_BEACON = "tracking_beacon"
EV_COOKIELESS_PING = "cookieless_ping"
EV_IDENTIFIER_STORED = "identifier_stored"
EV_IDENTIFIER_TRANSMITTED = "identifier_transmitted"
EV_COOKIE_SET = "cookie_set"
EV_STORAGE_SET = "storage_set"
EV_CONSENT_SIGNAL = "consent_signal"

EVIDENCE_LABELS: dict[str, str] = {
    EV_SCRIPT_LOADED: "Script downloaded",
    EV_NETWORK_REQUEST: "Network request",
    EV_TRACKING_BEACON: "Tracking request transmitted",
    EV_COOKIELESS_PING: "Consent Mode cookieless ping",
    EV_IDENTIFIER_STORED: "Identifier stored",
    EV_IDENTIFIER_TRANSMITTED: "Identifier transmitted",
    EV_COOKIE_SET: "Cookie set",
    EV_STORAGE_SET: "Browser storage written",
    EV_CONSENT_SIGNAL: "Consent signal",
}


@dataclass
class Finding:
    kind: str                    # request | script | cookie | storage | signal
    evidence: str                # one of the EV_* constants
    vendor_key: str
    vendor_name: str
    category: str
    label: str                   # host / cookie name — the headline
    detail: str = ""             # supporting text
    carries_identifier: bool = False
    is_third_party: bool = True
    consent_mode: dict[str, Any] | None = None
    url: str = ""
    name: str = ""
    domain: str = ""
    value_preview: str = ""

    def signature(self) -> str:
        """Stable identity for cross-phase and cross-scan diffing."""
        if self.kind in ("cookie", "storage"):
            return f"{self.kind}:{self.vendor_key}:{(self.name or self.label).lower()}"
        return f"{self.kind}:{self.vendor_key}:{self.evidence}:{self.label.lower()}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "evidence": self.evidence,
            "vendor_key": self.vendor_key,
            "vendor_name": self.vendor_name,
            "category": self.category,
            "label": self.label,
            "detail": self.detail,
            "carries_identifier": self.carries_identifier,
            "is_third_party": self.is_third_party,
            "consent_mode": self.consent_mode,
            "url": self.url,
            "name": self.name,
            "domain": self.domain,
            "value_preview": self.value_preview,
            "signature": self.signature(),
        }


def _first_party_host(page_url: str) -> str:
    return kb.normalize_host(page_url).split("/")[0]


def _registrable(host: str) -> str:
    """Cheap eTLD+1 approximation for first-vs-third-party comparison."""
    parts = (host or "").split(":")[0].split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def classify_request(req: dict[str, Any], *, page_host: str) -> Finding | None:
    """Classify one network request. Returns None for uninteresting same-party assets."""
    url = str(req.get("url") or "")
    if not url or url.startswith("data:") or url.startswith("blob:"):
        return None
    host = kb.normalize_host(url).split("/")[0]
    third_party = _registrable(host) != _registrable(page_host)
    vendor = kb.classify_vendor(url)
    resource_type = str(req.get("resource_type") or "")
    post = req.get("post_data_snippet") or req.get("post_data")

    # A request we can't attribute to a tracking vendor and that is same-party or a
    # plain asset isn't worth surfacing as a finding.
    if vendor is None and not third_party:
        return None
    if vendor is None and resource_type in ("image", "font", "stylesheet", "media", "document"):
        return None

    vendor_key = vendor.key if vendor else "third_party"
    vendor_name = vendor.name if vendor else host
    category = vendor.category if vendor else kb.CATEGORY_UNKNOWN

    # Consent Mode: distinguish a cookieless ping from an identified hit.
    cm = kb.parse_consent_mode(url)
    carries_id = kb.request_carries_identifier(url, post if isinstance(post, str) else None)

    if resource_type == "script":
        return Finding(
            kind="script", evidence=EV_SCRIPT_LOADED, vendor_key=vendor_key,
            vendor_name=vendor_name, category=category, label=host,
            detail=f"{vendor.purpose}" if vendor else "Third-party script.",
            carries_identifier=False, is_third_party=third_party, url=url,
        )

    is_beacon = kb.is_beacon_request(url, resource_type)
    if cm.present and cm.cookieless_ping and not carries_id:
        return Finding(
            kind="request", evidence=EV_COOKIELESS_PING, vendor_key=vendor_key,
            vendor_name=vendor_name, category=category, label=host,
            detail=("Consent Mode ping with storage denied (gcs="
                    f"{cm.raw_gcs}) and no client id — measurement without tracking."),
            carries_identifier=False, is_third_party=third_party,
            consent_mode=_cm_dict(cm), url=url,
        )
    if carries_id and is_beacon:
        return Finding(
            kind="request", evidence=EV_IDENTIFIER_TRANSMITTED, vendor_key=vendor_key,
            vendor_name=vendor_name, category=category, label=host,
            detail="A request carrying a visitor identifier was transmitted.",
            carries_identifier=True, is_third_party=third_party,
            consent_mode=_cm_dict(cm) if cm.present else None, url=url,
        )
    if is_beacon:
        return Finding(
            kind="request", evidence=EV_TRACKING_BEACON, vendor_key=vendor_key,
            vendor_name=vendor_name, category=category, label=host,
            detail="A hit was sent to a tracking endpoint (no identifier detected).",
            carries_identifier=False, is_third_party=third_party,
            consent_mode=_cm_dict(cm) if cm.present else None, url=url,
        )
    if third_party and vendor is not None:
        return Finding(
            kind="request", evidence=EV_NETWORK_REQUEST, vendor_key=vendor_key,
            vendor_name=vendor_name, category=category, label=host,
            detail="A request to a known tracking vendor.",
            carries_identifier=carries_id, is_third_party=third_party, url=url,
        )
    return None


def _cm_dict(cm: kb.ConsentModeSignal) -> dict[str, Any]:
    return {
        "present": cm.present,
        "ad_storage_granted": cm.ad_storage_granted,
        "analytics_storage_granted": cm.analytics_storage_granted,
        "raw_gcs": cm.raw_gcs,
        "cookieless_ping": cm.cookieless_ping,
    }


def classify_cookie(cookie: dict[str, Any], *, page_host: str) -> Finding:
    name = str(cookie.get("name") or "")
    value = str(cookie.get("value") or "")
    domain = str(cookie.get("domain") or "").lstrip(".")
    third_party = _registrable(domain) != _registrable(page_host) if domain else False
    vendor = kb.classify_cookie_vendor(name, domain)
    vendor_key = vendor.key if vendor else ("third_party" if third_party else "first_party")
    # Unattributed first-party cookies group under one "First-party" bucket so the
    # third-party vendor inventory stays clean; they remain visible in drill-down.
    vendor_name = vendor.name if vendor else (domain if third_party else "First-party")
    category = vendor.category if vendor else kb.CATEGORY_UNKNOWN

    is_id = kb.looks_like_identifier(name, value) and not kb.is_essential_cookie(name)
    evidence = EV_IDENTIFIER_STORED if is_id else EV_COOKIE_SET
    detail = vendor.purpose if vendor else ""
    if kb.is_essential_cookie(name):
        category = category if category != kb.CATEGORY_UNKNOWN else kb.CATEGORY_ESSENTIAL
        detail = detail or "Strictly-necessary cookie."
    return Finding(
        kind="cookie", evidence=evidence, vendor_key=vendor_key,
        vendor_name=vendor_name, category=category, label=name,
        detail=detail, carries_identifier=is_id, is_third_party=third_party,
        name=name, domain=domain, value_preview=_preview(value),
    )


def classify_storage(entry: dict[str, Any], *, page_host: str) -> Finding:
    key = str(entry.get("key") or "")
    value = str(entry.get("value_snippet") or entry.get("value") or "")
    stype = str(entry.get("type") or "local")
    vendor = kb.classify_cookie_vendor(key)
    vendor_key = vendor.key if vendor else "first_party"
    vendor_name = vendor.name if vendor else "First-party"
    category = vendor.category if vendor else kb.CATEGORY_UNKNOWN
    is_id = kb.looks_like_identifier(key, value)
    evidence = EV_IDENTIFIER_STORED if is_id else EV_STORAGE_SET
    return Finding(
        kind="storage", evidence=evidence, vendor_key=vendor_key,
        vendor_name=vendor_name, category=category,
        label=f"{stype}Storage: {key}",
        detail=vendor.purpose if vendor else f"{stype}Storage entry.",
        carries_identifier=is_id, is_third_party=False,
        name=key, value_preview=_preview(value),
    )


def _preview(value: str, limit: int = 40) -> str:
    v = (value or "").strip()
    if len(v) <= limit:
        return v
    return v[:limit] + "…"


def classify_phase(capture: dict[str, Any]) -> list[Finding]:
    """Classify every artifact in one (page, phase) capture into findings.

    Requests are de-duplicated by host+evidence so a page firing 30 identical
    beacons collapses to one finding with the interesting signal preserved.
    """
    page_url = str(capture.get("url") or capture.get("final_url") or "")
    page_host = _first_party_host(page_url)
    findings: list[Finding] = []
    seen: set[str] = set()

    for req in capture.get("requests", []) or []:
        f = classify_request(req, page_host=page_host)
        if f is None:
            continue
        sig = f.signature()
        # Prefer the "worst" evidence for a repeated host: keep identifier hits.
        if sig in seen:
            continue
        seen.add(sig)
        findings.append(f)

    for cookie in capture.get("cookies", []) or []:
        if not cookie.get("name"):
            continue
        findings.append(classify_cookie(cookie, page_host=page_host))

    for entry in capture.get("storage", []) or []:
        if not entry.get("key"):
            continue
        findings.append(classify_storage(entry, page_host=page_host))

    return findings
