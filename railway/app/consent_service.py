"""Orchestration for the Consent & Tracking Health feature.

Ties the pieces together and owns the *view model* — the single JSON structure
persisted per scan and rendered by the UI / returned by the JSON API:

    scan raw captures ──▶ consent_classifier ──▶ consent_evaluator
                                                        │
                          previous scan ◀── diff ◀──────┤
                                                        ▼
                                              build_result()  →  stored JSON

``run_scan`` is the entry point used by the manual "Run scan" button and the
scheduled cron. It records a run row, performs the scan in-process (intended to
be called from a background worker thread), and writes the finished result back.

Everything here is defensive: a browser failure, a bad page, or a missing DB
never raises out of ``run_scan`` — it lands as a failed run with a clear message.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import consent_classifier as cc
import consent_evaluator as ev
import consent_knowledge as kb
import consent_scanner
import consent_store

_log = logging.getLogger(__name__)


def default_expectations() -> dict[str, Any]:
    return ev.normalize_expectations(None)


def seed_config(client_slug: str, *, label: str = "", homepage_url: str = "") -> consent_store.ConsentScanConfig:
    """Create a starter config from a homepage URL (used by the setup form)."""
    pages = [homepage_url.strip()] if homepage_url.strip() else []
    return consent_store.save_config(
        client_slug,
        label=label,
        pages=pages,
        expectations=default_expectations(),
        scan_options={},
        scan_enabled=False,
    )


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def _authoritative_banner(page_phases: dict[str, dict]) -> bool:
    """A page 'has a banner' if one was seen before any interaction (pre_consent)."""
    pre = page_phases.get(ev.PHASE_PRE) or {}
    if pre:
        return bool(pre.get("banner_detected"))
    return any(bool(p.get("banner_detected")) for p in page_phases.values())


def build_result(
    scan_raw: dict[str, Any],
    *,
    expectations: dict[str, Any],
    previous_result: dict[str, Any] | None = None,
    pages_config: list[str] | None = None,
) -> dict[str, Any]:
    exp = ev.normalize_expectations(expectations)
    now = datetime.now(tz=UTC).isoformat()
    available = bool(scan_raw.get("available"))
    error = scan_raw.get("error")

    pages_out: list[dict[str, Any]] = []
    vendor_index: dict[str, dict[str, Any]] = {}
    category_tally: dict[str, int] = {}
    phase_totals: dict[str, dict[str, Any]] = {
        ph: {"status": ev.HEALTH_PASS, "violation_count": 0, "identifier_count": 0,
             "counts": {s: 0 for s in ev._SEV_RANK}}
        for ph in ev.PHASE_ORDER
    }
    top_findings: list[dict[str, Any]] = []
    cookieless_ping_count = 0

    for page in scan_raw.get("pages", []) or []:
        url = str(page.get("url") or "")
        page_phases_raw = page.get("phases", {}) or {}
        banner = _authoritative_banner(page_phases_raw)
        phase_out: dict[str, Any] = {}
        page_statuses: list[str] = []

        for phase in ev.PHASE_ORDER:
            cap = page_phases_raw.get(phase) or {}
            findings = [f.as_dict() for f in cc.classify_phase(cap)]
            pe = ev.evaluate_phase(findings, phase=phase, expectations=exp, banner_detected=banner)
            phase_out[phase] = {
                "status": pe["status"],
                "phase_label": pe["phase_label"],
                "banner_detected": banner,
                "banner_issue": pe["banner_issue"],
                "counts": pe["counts"],
                "violation_count": pe["violation_count"],
                "identifier_count": pe["identifier_count"],
                "interaction": cap.get("interaction") or {},
                "request_count": len(cap.get("requests") or []),
                "errors": cap.get("errors") or [],
                "findings": pe["findings"],
            }
            if phase in (ev.PHASE_PRE, ev.PHASE_REJECT):
                page_statuses.append(pe["status"])

            # Roll into phase totals + vendor index + category tally.
            pt = phase_totals[phase]
            pt["violation_count"] += pe["violation_count"]
            pt["identifier_count"] += pe["identifier_count"]
            for sev, n in pe["counts"].items():
                pt["counts"][sev] = pt["counts"].get(sev, 0) + n

            for f in pe["findings"]:
                if f.get("evidence") == cc.EV_COOKIELESS_PING:
                    cookieless_ping_count += 1
                _index_vendor(vendor_index, f, phase)
                category_tally[f.get("category", kb.CATEGORY_UNKNOWN)] = (
                    category_tally.get(f.get("category", kb.CATEGORY_UNKNOWN), 0) + 1
                )
                if f.get("is_violation") and phase in (ev.PHASE_PRE, ev.PHASE_REJECT):
                    top_findings.append({
                        "page": url, "phase": phase, "phase_label": ev.PHASE_LABELS.get(phase, phase),
                        "severity": f.get("severity"), "evidence": f.get("evidence"),
                        "evidence_label": cc.EVIDENCE_LABELS.get(f.get("evidence", ""), ""),
                        "vendor_name": f.get("vendor_name"), "vendor_key": f.get("vendor_key"),
                        "category": f.get("category"), "label": f.get("label"),
                        "rationale": f.get("rationale"), "signature": f.get("signature"),
                    })

        page_health = ev.combine_health(page_statuses) if available else ev.HEALTH_UNKNOWN
        pages_out.append({
            "url": url,
            "health": page_health,
            "banner_detected": banner,
            "phases": phase_out,
        })

    # Finalize phase-total statuses.
    for phase, pt in phase_totals.items():
        c = pt["counts"]
        if c.get(ev.SEV_CRITICAL):
            pt["status"] = ev.HEALTH_FAIL
        elif c.get(ev.SEV_HIGH) or c.get(ev.SEV_MEDIUM):
            pt["status"] = ev.HEALTH_ATTENTION
        else:
            pt["status"] = ev.HEALTH_PASS

    overall_statuses = [p["health"] for p in pages_out]
    if not available:
        health = ev.HEALTH_UNKNOWN
    elif not pages_out:
        health = ev.HEALTH_UNKNOWN
    else:
        health = ev.combine_health(overall_statuses)

    top_findings.sort(key=lambda f: (-ev.severity_rank(f.get("severity", "ok")), f.get("vendor_name") or ""))

    vendors = sorted(
        vendor_index.values(),
        key=lambda v: (-ev.severity_rank(v["worst_severity"]), kb.CATEGORY_ORDER.index(v["category"])
                       if v["category"] in kb.CATEGORY_ORDER else 99, v["vendor_name"]),
    )

    pre_violations = phase_totals[ev.PHASE_PRE]["violation_count"]
    reject_violations = phase_totals[ev.PHASE_REJECT]["violation_count"]
    ids_before_consent = _count_identifier_violations(pages_out)
    banner_pages = sum(1 for p in pages_out if p["banner_detected"])

    summary = {
        "pages_scanned": len(pages_out),
        "vendor_count": len(vendors),
        "pre_consent_violations": pre_violations,
        "reject_violations": reject_violations,
        "identifiers_before_consent": ids_before_consent,
        "cookieless_pings": cookieless_ping_count,
        "banner_pages": banner_pages,
        "banner_required": bool(exp.get("banner_required")),
        "advertising_vendors": sum(1 for v in vendors if v["category"] == kb.CATEGORY_ADVERTISING),
        "analytics_vendors": sum(1 for v in vendors if v["category"] == kb.CATEGORY_ANALYTICS),
    }
    summary["headline"] = _headline(health, summary, available, error)

    result = {
        "scanned_at": now,
        "engine": scan_raw.get("engine", "chromium"),
        "available": available,
        "error": error,
        "health": health,
        "health_label": ev.HEALTH_LABELS.get(health, "Unknown"),
        "expectations": exp,
        "summary": summary,
        "phase_totals": phase_totals,
        "phase_labels": {ph: ev.PHASE_LABELS[ph] for ph in ev.PHASE_ORDER},
        "vendors": vendors,
        "categories": [
            {"category": cat, "label": kb.CATEGORY_LABELS.get(cat, cat),
             "count": category_tally.get(cat, 0),
             "requires_consent": cat in set(exp.get("require_consent_categories") or [])}
            for cat in kb.CATEGORY_ORDER if category_tally.get(cat)
        ],
        "top_findings": top_findings,
        "pages": pages_out,
    }
    result["diff"] = _diff(result, previous_result)
    return result


def _index_vendor(index: dict[str, dict[str, Any]], finding: dict[str, Any], phase: str) -> None:
    key = finding.get("vendor_key") or "unknown"
    if key in ("first_party", "third_party"):
        # Only surface named vendors in the inventory; generic party buckets stay
        # inside per-finding detail.
        if finding.get("vendor_name") in (None, "", "First-party"):
            return
    entry = index.get(key)
    if entry is None:
        entry = {
            "vendor_key": key,
            "vendor_name": finding.get("vendor_name") or key,
            "category": finding.get("category") or kb.CATEGORY_UNKNOWN,
            "category_label": kb.CATEGORY_LABELS.get(finding.get("category", ""), ""),
            "purpose": "",
            "phases": [],
            "sets_identifier": False,
            "worst_severity": ev.SEV_OK,
            "evidence_types": [],
            "count": 0,
        }
        v = kb.VENDORS_BY_KEY.get(key)
        if v:
            entry["purpose"] = v.purpose
            entry["is_cmp"] = v.is_cmp
        index[key] = entry
    entry["count"] += 1
    if phase not in entry["phases"]:
        entry["phases"].append(phase)
    if finding.get("carries_identifier"):
        entry["sets_identifier"] = True
    evd = finding.get("evidence")
    if evd and evd not in entry["evidence_types"]:
        entry["evidence_types"].append(evd)
    # Track the worst severity seen in a consent-denied phase.
    if phase in (ev.PHASE_PRE, ev.PHASE_REJECT):
        if ev.severity_rank(finding.get("severity", "ok")) > ev.severity_rank(entry["worst_severity"]):
            entry["worst_severity"] = finding.get("severity", "ok")


def _count_identifier_violations(pages_out: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    for p in pages_out:
        for phase in (ev.PHASE_PRE, ev.PHASE_REJECT):
            for f in (p["phases"].get(phase, {}).get("findings") or []):
                if f.get("is_violation") and f.get("carries_identifier"):
                    seen.add(f"{p['url']}|{phase}|{f.get('signature')}")
    return len(seen)


def _headline(health: str, summary: dict[str, Any], available: bool, error: Any) -> str:
    if not available:
        return error or "The scan could not run."
    if summary["pages_scanned"] == 0:
        return "No pages were scanned."
    ids = summary["identifiers_before_consent"]
    pre = summary["pre_consent_violations"]
    if health == ev.HEALTH_FAIL:
        if ids:
            return (f"{ids} tracking identifier{'s' if ids != 1 else ''} were set or sent "
                    f"before consent across {summary['pages_scanned']} page(s). Fix before launch.")
        return f"{pre} consent issue(s) were detected before the visitor opted in."
    if health == ev.HEALTH_ATTENTION:
        return ("Tracking is mostly consent-gated, with a few items to review before "
                "and after opt-out.")
    return ("Tracking is correctly gated: nothing that requires consent fired before the "
            "visitor opted in, and Reject All was honoured.")


# ---------------------------------------------------------------------------
# Change tracking (diff vs previous scan)
# ---------------------------------------------------------------------------

def _violation_sig_map(result: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not result:
        return out
    for f in result.get("top_findings", []) or []:
        out[f"{f.get('page')}|{f.get('phase')}|{f.get('signature')}"] = f
    return out


def _diff(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"has_previous": False}
    cur = _violation_sig_map(current)
    prev = _violation_sig_map(previous)
    new = [v for k, v in cur.items() if k not in prev]
    resolved = [v for k, v in prev.items() if k not in cur]
    cur_vendors = {v["vendor_key"]: v for v in current.get("vendors", [])}
    prev_vendors = {v["vendor_key"]: v for v in previous.get("vendors", [])}
    new_vendors = [cur_vendors[k] for k in cur_vendors if k not in prev_vendors]
    removed_vendors = [prev_vendors[k] for k in prev_vendors if k not in cur_vendors]
    new.sort(key=lambda f: -ev.severity_rank(f.get("severity", "ok")))
    resolved.sort(key=lambda f: -ev.severity_rank(f.get("severity", "ok")))
    return {
        "has_previous": True,
        "previous_scanned_at": previous.get("scanned_at"),
        "previous_health": previous.get("health"),
        "health_change": {"from": previous.get("health"), "to": current.get("health")},
        "new_violations": new,
        "resolved_violations": resolved,
        "new_vendors": [{"vendor_key": v["vendor_key"], "vendor_name": v["vendor_name"],
                         "category": v["category"]} for v in new_vendors],
        "removed_vendors": [{"vendor_key": v["vendor_key"], "vendor_name": v["vendor_name"],
                             "category": v["category"]} for v in removed_vendors],
    }


# ---------------------------------------------------------------------------
# Run a scan end to end
# ---------------------------------------------------------------------------

def run_scan(
    client_slug: str,
    *,
    trigger: str = "manual",
    triggered_by: str | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Scan a client's configured pages and persist the result. Never raises.

    Returns a small status dict (the full result is stored on the run row).
    If ``run_id`` is provided the row is assumed already started (the route
    starts it so the UI can show 'running' immediately); otherwise one is created.
    """
    slug = (client_slug or "").strip().lower()
    config = consent_store.get_config(slug)
    if run_id is None:
        run_id = consent_store.start_run(slug, trigger=trigger, triggered_by=triggered_by)

    if not config or not config.pages:
        consent_store.finish_run(
            run_id, status="failed", health=ev.HEALTH_UNKNOWN,
            error_message="No pages are configured to scan.",
        )
        return {"ok": False, "run_id": run_id, "error": "No pages configured."}

    expectations = ev.normalize_expectations(config.expectations)
    scan_options = dict(config.scan_options or {})
    try:
        scan_raw = consent_scanner.scan_pages(config.pages, config=scan_options)
    except Exception as exc:
        _log.exception("consent scan crashed [%s]", slug)
        consent_store.finish_run(
            run_id, status="failed", health=ev.HEALTH_UNKNOWN,
            error_message=f"Scanner error: {str(exc)[:300]}",
        )
        return {"ok": False, "run_id": run_id, "error": str(exc)[:300]}

    prev = consent_store.previous_completed_run(slug, run_id) if run_id else None
    previous_result = prev.result if prev else None

    result = build_result(
        scan_raw, expectations=expectations, previous_result=previous_result,
        pages_config=config.pages,
    )

    if not result.get("available"):
        consent_store.finish_run(
            run_id, status="failed", health=result.get("health", ev.HEALTH_UNKNOWN),
            result=result, summary=result.get("summary"),
            error_message=result.get("error") or "Scanner unavailable.",
        )
        return {"ok": False, "run_id": run_id, "error": result.get("error")}

    summary = result.get("summary", {})
    consent_store.finish_run(
        run_id,
        status="completed",
        health=result.get("health", ev.HEALTH_UNKNOWN),
        result=result,
        summary=summary,
        pages_scanned=summary.get("pages_scanned", 0),
        vendor_count=summary.get("vendor_count", 0),
        violation_count=summary.get("pre_consent_violations", 0) + summary.get("reject_violations", 0),
        identifiers_before_consent=summary.get("identifiers_before_consent", 0),
    )
    return {"ok": True, "run_id": run_id, "health": result.get("health")}


# ---------------------------------------------------------------------------
# Read model for the UI / API
# ---------------------------------------------------------------------------

def get_overview(client_slug: str) -> dict[str, Any]:
    """Everything the page/API needs: config, latest run status, the latest
    completed report to render, and recent history."""
    slug = (client_slug or "").strip().lower()
    config = consent_store.get_config(slug)
    latest = consent_store.latest_run_any(slug)
    # The report we render is the most recent *completed* scan — a later failed or
    # in-progress run shouldn't wipe the last good report off the screen.
    if latest and latest.status == "completed":
        completed = latest
    else:
        completed = consent_store.latest_completed_run(slug, include_result=True)
    history = consent_store.list_runs(slug, limit=12)
    return {
        "configured": bool(config and config.pages),
        "config": _config_dict(config),
        "latest": _run_dict(latest, include_result=False),
        "report": completed.result if completed else None,
        "report_run": _run_dict(completed, include_result=False) if completed else None,
        "history": [_run_dict(r, include_result=False) for r in history],
    }


def _config_dict(config: consent_store.ConsentScanConfig | None) -> dict[str, Any] | None:
    if not config:
        return None
    return {
        "client_slug": config.client_slug,
        "label": config.label,
        "pages": config.pages,
        "expectations": ev.normalize_expectations(config.expectations),
        "scan_options": config.scan_options or {},
        "scan_enabled": config.scan_enabled,
        "scan_frequency": config.scan_frequency,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        "updated_by": config.updated_by,
    }


def _run_dict(run: consent_store.ConsentScanRun | None, *, include_result: bool) -> dict[str, Any] | None:
    if not run:
        return None
    out = {
        "id": run.id,
        "status": run.status,
        "trigger": run.trigger,
        "health": run.health,
        "health_label": ev.HEALTH_LABELS.get(run.health, "Unknown"),
        "pages_scanned": run.pages_scanned,
        "vendor_count": run.vendor_count,
        "violation_count": run.violation_count,
        "identifiers_before_consent": run.identifiers_before_consent,
        "summary": run.summary,
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "triggered_by": run.triggered_by,
    }
    if include_result:
        out["result"] = run.result
    return out
