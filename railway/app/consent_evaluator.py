"""Evaluate classified findings against a client's consent expectations.

This is where a *finding* becomes a *verdict*. The same evidence means different
things in different phases: an advertising identifier stored after the visitor
clicked "Accept All" is expected; the identical cookie appearing before they
chose anything, or after they clicked "Reject All", is a compliance failure.

Expectations are configurable per client (persisted as JSON). The defaults encode
a GDPR/ePrivacy posture:

    * analytics, advertising, social and personalization require prior opt-in,
    * strictly-necessary cookies and the consent banner itself are always allowed,
    * Google Consent Mode cookieless pings are allowed before consent,
    * after "Reject All" nothing beyond strictly-necessary may set an identifier.

Pure functions only.
"""

from __future__ import annotations

from typing import Any

import consent_classifier as cc
import consent_knowledge as kb

# Scan phases.
PHASE_PRE = "pre_consent"
PHASE_REJECT = "reject_all"
PHASE_ACCEPT = "accept_all"
PHASE_LABELS: dict[str, str] = {
    PHASE_PRE: "Before consent",
    PHASE_REJECT: "After “Reject All”",
    PHASE_ACCEPT: "After “Accept All”",
}
PHASE_ORDER: tuple[str, ...] = (PHASE_PRE, PHASE_REJECT, PHASE_ACCEPT)

# Severity ladder (worst first).
SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"
SEV_INFO = "info"
SEV_OK = "ok"
_SEV_RANK: dict[str, int] = {
    SEV_CRITICAL: 5, SEV_HIGH: 4, SEV_MEDIUM: 3, SEV_LOW: 2, SEV_INFO: 1, SEV_OK: 0,
}

# Overall health states.
HEALTH_PASS = "pass"
HEALTH_ATTENTION = "attention"
HEALTH_FAIL = "fail"
HEALTH_UNKNOWN = "unknown"
HEALTH_LABELS: dict[str, str] = {
    HEALTH_PASS: "Healthy",
    HEALTH_ATTENTION: "Needs attention",
    HEALTH_FAIL: "Critical issues",
    HEALTH_UNKNOWN: "Not yet scanned",
}


DEFAULT_EXPECTATIONS: dict[str, Any] = {
    "require_consent_categories": sorted(kb.DEFAULT_CONSENT_REQUIRED),
    "allow_consent_mode_pings": True,
    "banner_required": True,
    # Vendor keys explicitly permitted to *load* (scripts/requests, never
    # identifiers) before consent — e.g. the tag manager and the CMP.
    "allowed_pre_consent_vendors": ["gtm"],
    # After Reject All, forbid any identifier from a consent-required category.
    "strict_reject": True,
}


def normalize_expectations(raw: dict[str, Any] | None) -> dict[str, Any]:
    exp = dict(DEFAULT_EXPECTATIONS)
    if not raw:
        return exp
    if isinstance(raw.get("require_consent_categories"), list):
        cats = [str(c).strip().lower() for c in raw["require_consent_categories"] if str(c).strip()]
        exp["require_consent_categories"] = [c for c in cats if c in kb.CATEGORY_LABELS]
    for key in ("allow_consent_mode_pings", "banner_required", "strict_reject"):
        if key in raw:
            exp[key] = bool(raw[key])
    if isinstance(raw.get("allowed_pre_consent_vendors"), list):
        exp["allowed_pre_consent_vendors"] = [
            str(v).strip().lower() for v in raw["allowed_pre_consent_vendors"] if str(v).strip()
        ]
    return exp


def _requires_consent(category: str, expectations: dict[str, Any]) -> bool:
    return category in set(expectations.get("require_consent_categories") or [])


def _vendor_allowed_pre_consent(vendor_key: str, expectations: dict[str, Any]) -> bool:
    return vendor_key in set(expectations.get("allowed_pre_consent_vendors") or [])


def evaluate_finding(
    finding: dict[str, Any],
    *,
    phase: str,
    expectations: dict[str, Any],
) -> dict[str, Any]:
    """Return the finding annotated with severity, is_violation, and a rationale."""
    category = str(finding.get("category") or kb.CATEGORY_UNKNOWN)
    evidence = str(finding.get("evidence") or "")
    vendor_key = str(finding.get("vendor_key") or "")
    carries_id = bool(finding.get("carries_identifier"))
    needs_consent = _requires_consent(category, expectations)

    severity = SEV_INFO
    is_violation = False
    rationale = ""

    # In the accept phase everything is expected — we record it as the baseline
    # inventory, never as a violation.
    if phase == PHASE_ACCEPT:
        severity = SEV_OK
        rationale = "Observed with full consent granted — expected."
        return _annotate(finding, severity, False, rationale)

    consent_denied_phase = phase in (PHASE_PRE, PHASE_REJECT)

    # --- The three evidence classes the brief insists we keep distinct --------
    if evidence == cc.EV_COOKIELESS_PING:
        if expectations.get("allow_consent_mode_pings", True):
            severity = SEV_OK
            rationale = ("Consent Mode cookieless ping — storage denied and no "
                         "identifier. Measurement without tracking; allowed before consent.")
        else:
            severity = SEV_LOW
            rationale = "Consent Mode ping fired (your policy disallows pings before consent)."
        return _annotate(finding, severity, severity != SEV_OK, rationale)

    if evidence == cc.EV_SCRIPT_LOADED:
        # A downloaded script is NOT a violation on its own.
        if category in (kb.CATEGORY_ESSENTIAL, kb.CATEGORY_CONSENT):
            severity = SEV_OK
            rationale = "Essential / consent-management script."
        elif _vendor_allowed_pre_consent(vendor_key, expectations):
            severity = SEV_INFO
            rationale = "Allowed to load before consent (it holds its tags until consent)."
        elif needs_consent:
            severity = SEV_LOW
            rationale = ("Script downloaded before consent. Loading alone isn't a breach, "
                         "but confirm it fires no tags until consent is granted.")
        else:
            severity = SEV_INFO
            rationale = "Third-party script downloaded."
        return _annotate(finding, severity, False, rationale)

    # --- Identifiers: the highest-signal violations ---------------------------
    if evidence in (cc.EV_IDENTIFIER_STORED, cc.EV_IDENTIFIER_TRANSMITTED):
        if category in (kb.CATEGORY_ESSENTIAL, kb.CATEGORY_CONSENT):
            severity = SEV_OK
            rationale = "Strictly-necessary identifier (e.g. security / consent state)."
            return _annotate(finding, severity, False, rationale)
        if needs_consent and consent_denied_phase:
            severity = SEV_CRITICAL
            stored = "stored" if evidence == cc.EV_IDENTIFIER_STORED else "transmitted"
            where = "before the visitor made a choice" if phase == PHASE_PRE \
                else "after the visitor clicked “Reject All”"
            cat_word = kb.CATEGORY_LABELS.get(category, category).lower()
            article = "An" if cat_word[:1] in "aeiou" else "A"
            rationale = (f"{article} {cat_word} identifier was "
                         f"{stored} {where}. This is a consent violation.")
            return _annotate(finding, severity, True, rationale)
        severity = SEV_LOW
        rationale = "Identifier present (category does not require consent under your policy)."
        return _annotate(finding, severity, False, rationale)

    if evidence == cc.EV_TRACKING_BEACON:
        if needs_consent and consent_denied_phase:
            severity = SEV_HIGH
            rationale = ("A tracking request was transmitted with consent denied. No "
                         "identifier was detected, but behavioural data left the browser.")
            return _annotate(finding, severity, True, rationale)
        severity = SEV_INFO
        rationale = "Tracking request (category does not require consent under your policy)."
        return _annotate(finding, severity, False, rationale)

    if evidence == cc.EV_COOKIE_SET:
        if category in (kb.CATEGORY_ESSENTIAL, kb.CATEGORY_CONSENT):
            severity = SEV_OK
            rationale = "Strictly-necessary / consent cookie."
        elif needs_consent and consent_denied_phase and finding.get("is_third_party"):
            severity = SEV_MEDIUM
            rationale = "A non-essential third-party cookie was set without consent."
            return _annotate(finding, severity, True, rationale)
        else:
            severity = SEV_INFO
            rationale = "Cookie set (no persistent identifier detected)."
        return _annotate(finding, severity, False, rationale)

    if evidence == cc.EV_STORAGE_SET:
        severity = SEV_INFO
        rationale = "Browser storage written (no identifier detected)."
        return _annotate(finding, severity, False, rationale)

    if evidence == cc.EV_NETWORK_REQUEST:
        if needs_consent and consent_denied_phase and finding.get("is_third_party"):
            severity = SEV_LOW
            rationale = "A request to a tracking vendor was made before consent."
        else:
            severity = SEV_INFO
            rationale = "Third-party request."
        return _annotate(finding, severity, False, rationale)

    return _annotate(finding, SEV_INFO, False, rationale or "Observed.")


def _annotate(finding: dict[str, Any], severity: str, is_violation: bool, rationale: str) -> dict[str, Any]:
    out = dict(finding)
    out["severity"] = severity
    out["is_violation"] = is_violation
    out["rationale"] = rationale
    return out


def evaluate_phase(
    findings: list[dict[str, Any]],
    *,
    phase: str,
    expectations: dict[str, Any],
    banner_detected: bool = True,
) -> dict[str, Any]:
    """Evaluate one phase's findings and roll up its status + counts."""
    evaluated = [evaluate_finding(f, phase=phase, expectations=expectations) for f in findings]
    counts = {s: 0 for s in _SEV_RANK}
    for ev in evaluated:
        counts[ev.get("severity", SEV_INFO)] = counts.get(ev.get("severity", SEV_INFO), 0) + 1
    violations = [e for e in evaluated if e.get("is_violation")]

    status = HEALTH_PASS
    if counts.get(SEV_CRITICAL) or counts.get(SEV_HIGH):
        status = HEALTH_FAIL if counts.get(SEV_CRITICAL) else HEALTH_ATTENTION
    elif counts.get(SEV_MEDIUM):
        status = HEALTH_ATTENTION

    # Banner expectations only matter in the consent-gated phases.
    banner_issue = ""
    if phase in (PHASE_PRE, PHASE_REJECT) and expectations.get("banner_required") and not banner_detected:
        if status == HEALTH_PASS:
            status = HEALTH_ATTENTION
        banner_issue = "No consent banner was detected on this page."

    return {
        "phase": phase,
        "phase_label": PHASE_LABELS.get(phase, phase),
        "status": status,
        "findings": evaluated,
        "counts": counts,
        "violation_count": len(violations),
        "identifier_count": sum(1 for e in evaluated if e.get("carries_identifier")),
        "banner_detected": banner_detected,
        "banner_issue": banner_issue,
    }


def worst_severity(evaluated: list[dict[str, Any]]) -> str:
    worst = SEV_OK
    for e in evaluated:
        if _SEV_RANK.get(e.get("severity", SEV_OK), 0) > _SEV_RANK.get(worst, 0):
            worst = e.get("severity", SEV_OK)
    return worst


def combine_health(statuses: list[str]) -> str:
    """The overall health is the worst phase status (accept phase excluded upstream)."""
    order = [HEALTH_FAIL, HEALTH_ATTENTION, HEALTH_PASS]
    present = [s for s in statuses if s in order]
    if not present:
        return HEALTH_UNKNOWN
    for level in order:
        if level in present:
            return level
    return HEALTH_PASS


def severity_rank(sev: str) -> int:
    return _SEV_RANK.get(sev, 0)
