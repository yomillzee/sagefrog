"""Automated accessibility (ADA / WCAG) scanner built on Playwright + axe-core.

Drives a fresh, isolated Chromium session (via Playwright's synchronous API, so
it runs cleanly inside a background worker thread — same model as
:mod:`consent_scanner`), loads each page, injects Deque's **axe-core** rules
engine, and runs it against the fully-rendered DOM. axe-core reports the concrete
WCAG success criteria a page fails, grouped by impact (critical / serious /
moderate / minor) with a CSS-selector + failure summary for every offending node.

The intended use is *scoping*: point it at a client's key templates (home,
services, contact, a form, a blog post) and it produces a machine-readable
inventory of what's broken and how bad, which :mod:`scripts.a11y_audit` rolls up
into a per-client remediation estimate. It is a triage tool, not a substitute for
manual testing — axe reliably catches ~30-50% of WCAG issues (contrast, missing
alt text, form labels, ARIA misuse, heading order, landmark structure); keyboard
traps and screen-reader semantics still need a human. Every finding here is a
*true* problem, so it's a safe floor for an estimate.

This module performs the browser work only. Like :mod:`consent_scanner` it
degrades gracefully: if Playwright or a Chromium binary is unavailable,
:func:`scan_pages` returns ``available=False`` with a clear message instead of
raising, so an audit is recorded as failed rather than crashing the caller.

The Chromium launch/discovery/auto-install plumbing, the SSRF URL guard, and the
navigation-timeout / ignore-TLS knobs are all shared with :mod:`consent_scanner`
(same ``CONSENT_SCANNER_*`` environment variables) so a container that can run one
scanner can run the other with no extra configuration.

Environment knobs specific to this scanner (all optional):
    A11Y_WCAG_TAGS       comma-separated axe tag set to run
                         (default "wcag2a,wcag2aa,wcag21a,wcag21aa,best-practice").
    A11Y_SETTLE_MS       ms to wait after load before running axe (default 2500).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

# Reuse the battle-tested Chromium plumbing and URL guard from the consent
# scanner rather than duplicating it — both drive the same browser the same way.
from consent_scanner import (
    _ignore_https,
    _launch_kwargs,
    _timeout_ms,
    ensure_chromium_installed,
    validate_scan_url,
)

_log = logging.getLogger(__name__)

# Vendored axe-core (see vendor/axe-core/). Bundling the engine keeps a scan
# hermetic — no CDN fetch at run time, and the ruleset is pinned so results are
# reproducible across deploys. Bump the file to upgrade the rules.
_AXE_PATH = Path(__file__).resolve().parent / "vendor" / "axe-core" / "axe.min.js"

# WCAG 2.0/2.1 A + AA is the ADA-compliance baseline US courts and the DOJ point
# at; "best-practice" adds axe's non-WCAG recommendations (they scope real work
# too, and the report keeps them in their own bucket so they never inflate the
# strict-conformance count).
_DEFAULT_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice")

# axe impact -> rough remediation weight (engineer-hours per *distinct* rule
# violation on a page, order-of-magnitude, for first-pass scoping only). A single
# rule failing on 40 nodes is usually one fix applied 40 times, so the estimator
# weights per rule-instance, not per node.
IMPACT_WEIGHTS: dict[str, float] = {
    "critical": 2.0,
    "serious": 1.5,
    "moderate": 0.75,
    "minor": 0.25,
}

# Severity ordering, most-severe first — used when sorting rules for the report.
IMPACT_ORDER: tuple[str, ...] = ("critical", "serious", "moderate", "minor")


def _settle_ms() -> int:
    try:
        return max(0, int(os.getenv("A11Y_SETTLE_MS") or 2500))
    except ValueError:
        return 2500


def _wcag_tags() -> list[str]:
    raw = (os.getenv("A11Y_WCAG_TAGS") or "").strip()
    if not raw:
        return list(_DEFAULT_TAGS)
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    return tags or list(_DEFAULT_TAGS)


def _load_axe_source() -> str | None:
    try:
        return _AXE_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - only if the vendored file is missing
        _log.error("a11y scanner: could not read axe-core at %s: %s", _AXE_PATH, exc)
        return None


def axe_version() -> str | None:
    """Best-effort read of the vendored axe-core version string, for reports."""
    try:
        head = _AXE_PATH.read_text(encoding="utf-8")[:4000]
    except Exception:
        return None
    marker = 'axe.version="'
    idx = head.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = head.find('"', start)
    return head[start:end] if end != -1 else None


# axe-core runs entirely in-page. We inject the engine, then call axe.run with the
# configured tag set and a trimmed result payload (dropping the verbose "passes"
# and per-node DOM snapshots we don't need for scoping) to keep the transfer small.
_AXE_RUN_JS = r"""
async (tags) => {
  const opts = {
    runOnly: { type: 'tag', values: tags },
    resultTypes: ['violations', 'incomplete'],
    // Don't ship the passing checks or the raw element handles back over the wire.
    elementRef: false,
  };
  const r = await axe.run(document, opts);
  const trim = (arr) => (arr || []).map(v => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    helpUrl: v.helpUrl,
    description: v.description,
    tags: v.tags,
    nodes: (v.nodes || []).map(n => ({
      target: n.target,
      html: (n.html || '').slice(0, 300),
      failureSummary: n.failureSummary || '',
    })),
  }));
  return {
    testEngine: r.testEngine,
    violations: trim(r.violations),
    // "incomplete" = axe couldn't decide automatically; these need a human. We
    // surface the count so scoping accounts for manual-review time.
    incomplete: trim(r.incomplete),
  };
};
"""


def _scan_one(browser, url: str, *, axe_src: str, tags: list[str]) -> dict[str, Any]:
    """Load `url` in a fresh context, inject axe-core, run it, and summarise."""
    timeout = _timeout_ms()
    settle = _settle_ms()
    errors: list[str] = []
    context = browser.new_context(
        ignore_https_errors=_ignore_https(),
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SagefrogA11yBot/1.0"),
        viewport={"width": 1366, "height": 900},
    )
    page = context.new_page()
    final_url = url
    title = ""
    result: dict[str, Any] = {}
    try:
        try:
            page.goto(url, wait_until="load", timeout=timeout)
        except Exception as exc:
            errors.append(f"navigation: {str(exc)[:200]}")
        # Let late-rendering (SPA hydration, deferred images, web fonts) settle so
        # axe sees the DOM a real user would.
        try:
            page.wait_for_timeout(settle)
        except Exception:
            pass
        try:
            final_url = page.url
            title = page.title() or ""
        except Exception:
            pass
        try:
            page.evaluate(axe_src)  # define window.axe
            result = page.evaluate(_AXE_RUN_JS, tags) or {}
        except Exception as exc:
            errors.append(f"axe: {str(exc)[:200]}")
    finally:
        try:
            context.close()
        except Exception:
            pass

    violations = result.get("violations") or []
    incomplete = result.get("incomplete") or []

    by_impact: dict[str, int] = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    node_total = 0
    for v in violations:
        impact = (v.get("impact") or "minor").lower()
        if impact not in by_impact:
            by_impact[impact] = 0
        by_impact[impact] += 1
        node_total += len(v.get("nodes") or [])

    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "violations": violations,
        "incomplete": incomplete,
        "violation_count": len(violations),          # distinct failing rules
        "node_count": node_total,                     # total offending elements
        "incomplete_count": len(incomplete),          # needs manual review
        "by_impact": by_impact,
        "errors": errors,
        "scanned": not bool([e for e in errors if e.startswith("axe:")]),
    }


def scan_pages(urls: list[str], *, tags: list[str] | None = None) -> dict[str, Any]:
    """Run an axe-core accessibility scan over `urls`.

    Returns a dict::

      {
        "available": bool,   # was a browser usable at all?
        "error": str | None, # top-level failure (browser missing, etc.)
        "engine": "axe-core",
        "axe_version": "4.12.1" | None,
        "tags": [...],       # the WCAG tag set that was run
        "pages": [ { ...per-page result from _scan_one... }, ... ],
      }

    Never raises for the ordinary failure modes (no browser, unreachable page):
    those are reported in-band so a caller can record a failed audit cleanly.
    """
    tags = tags or _wcag_tags()

    # Normalise + SSRF-guard every URL up front (shared with the consent scanner).
    clean: list[str] = []
    rejected: list[dict[str, str]] = []
    for u in urls or []:
        ok, norm = validate_scan_url(u)
        if ok:
            clean.append(norm)
        else:
            rejected.append({"url": u, "reason": norm})

    if not clean:
        return {
            "available": True,
            "error": "No valid pages to scan." if not rejected else "All supplied URLs were rejected.",
            "engine": "axe-core",
            "axe_version": axe_version(),
            "tags": tags,
            "pages": [],
            "rejected": rejected,
        }

    axe_src = _load_axe_source()
    if not axe_src:
        return {
            "available": False,
            "error": f"Vendored axe-core not found at {_AXE_PATH}.",
            "engine": "axe-core",
            "axe_version": None,
            "tags": tags,
            "pages": [],
            "rejected": rejected,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "available": False,
            "error": (f"Playwright is not installed ({exc}). Add it to requirements "
                      "and `playwright install chromium`."),
            "engine": "axe-core",
            "axe_version": axe_version(),
            "tags": tags,
            "pages": [],
            "rejected": rejected,
        }

    resolved_exe = ensure_chromium_installed()
    results: list[dict[str, Any]] = []
    with sync_playwright() as p:
        launch_kwargs = _launch_kwargs()
        if resolved_exe and "executable_path" not in launch_kwargs:
            launch_kwargs["executable_path"] = resolved_exe
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception as exc:
            hint = ""
            msg = str(exc)
            if "Executable doesn't exist" in msg or "looks like Playwright" in msg:
                hint = (" Chromium is missing — run `playwright install chromium` "
                        "(or use the mcr.microsoft.com/playwright image, as the Dockerfile does).")
            return {
                "available": False,
                "error": f"Could not launch Chromium: {msg[:220]}{hint}",
                "engine": "axe-core",
                "axe_version": axe_version(),
                "tags": tags,
                "pages": [],
                "rejected": rejected,
            }
        try:
            for url in clean:
                try:
                    results.append(_scan_one(browser, url, axe_src=axe_src, tags=tags))
                except Exception as exc:
                    results.append({
                        "url": url, "final_url": url, "title": "",
                        "violations": [], "incomplete": [],
                        "violation_count": 0, "node_count": 0, "incomplete_count": 0,
                        "by_impact": {"critical": 0, "serious": 0, "moderate": 0, "minor": 0},
                        "errors": [f"scan: {str(exc)[:200]}"], "scanned": False,
                    })
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return {
        "available": True,
        "error": None,
        "engine": "axe-core",
        "axe_version": axe_version(),
        "tags": tags,
        "pages": results,
        "rejected": rejected,
    }


# ── Scoping roll-up ──────────────────────────────────────────────────────────
# Turns the per-page scan into client-level numbers a proposal can quote. Shared
# by the CLI (scripts/a11y_audit.py) and the in-dashboard Accessibility page so
# both count and estimate identically.

def aggregate(scan: dict[str, Any]) -> dict[str, Any]:
    """Roll per-page axe results up into client-level scoping numbers."""
    pages = scan.get("pages") or []
    totals = {k: 0 for k in IMPACT_ORDER}
    node_totals = {k: 0 for k in IMPACT_ORDER}
    incomplete_total = 0
    # rule id -> {impact, help, helpUrl, pages: set, rule_instances, nodes}
    rules: dict[str, dict[str, Any]] = {}

    for pg in pages:
        incomplete_total += pg.get("incomplete_count", 0)
        for v in pg.get("violations") or []:
            impact = (v.get("impact") or "minor").lower()
            if impact not in totals:
                totals[impact] = 0
                node_totals[impact] = 0
            n_nodes = len(v.get("nodes") or [])
            totals[impact] += 1
            node_totals[impact] += n_nodes
            r = rules.setdefault(v["id"], {
                "id": v["id"], "impact": impact, "help": v.get("help", ""),
                "helpUrl": v.get("helpUrl", ""), "pages": set(),
                "rule_instances": 0, "nodes": 0,
            })
            r["pages"].add(pg.get("url"))
            r["rule_instances"] += 1
            r["nodes"] += n_nodes

    # Effort estimate: weight each *rule instance* (a rule failing on a page) by
    # its impact. Fixing a rule usually generalises across the nodes it hit, so we
    # don't multiply by node count — that would wildly over-count a single CSS or
    # template fix. A floor for a first-pass quote, not a bid.
    est_hours = 0.0
    for impact, count in totals.items():
        est_hours += count * IMPACT_WEIGHTS.get(impact, 0.5)
    manual_hours = round(incomplete_total * 0.1, 1)  # coarse manual-review budget

    rule_rows = sorted(
        rules.values(),
        key=lambda r: (IMPACT_ORDER.index(r["impact"]) if r["impact"] in IMPACT_ORDER else 9,
                       -r["nodes"]),
    )
    for r in rule_rows:
        r["page_count"] = len(r["pages"])
        r.pop("pages", None)

    scanned_pages = [p for p in pages if p.get("scanned")]
    return {
        "totals_by_impact": totals,
        "nodes_by_impact": node_totals,
        "total_violations": sum(totals.values()),
        "total_nodes": sum(node_totals.values()),
        "incomplete_total": incomplete_total,
        "pages_scanned": len(scanned_pages),
        "pages_requested": len(pages),
        "rules": rule_rows,
        "est_dev_hours": round(est_hours, 1),
        "est_manual_review_hours": manual_hours,
    }


def size_band(agg: dict[str, Any]) -> str:
    """A one-word size band for the top of a proposal."""
    by_impact = agg.get("totals_by_impact") or {}
    crit = by_impact.get("critical", 0) + by_impact.get("serious", 0)
    total = agg.get("total_violations", 0)
    if total == 0:
        return "Clean"
    if crit >= 8 or total >= 25:
        return "Large"
    if crit >= 3 or total >= 10:
        return "Medium"
    return "Small"


# ── Root-cause clustering ────────────────────────────────────────────────────
# A raw axe report can read like hundreds of independent problems when most of
# them are one broken template hit N times (e.g. a nav that emits 100 empty links
# and 100 mis-parented <li>s). For scoping, what matters is the *root cause*, not
# the element count. We cluster affected elements by a shared CSS-selector root so
# a repeated component defect shows up as one item spanning many elements, across
# whatever mix of rules it trips. This is a heuristic — labelled as "likely" in the
# UI — not a claim of certainty.

# Sibling-index pseudo-classes are exactly what differ between repeated instances
# of the same component, so we strip them before comparing selectors.
_NTH_RE = re.compile(r":nth-(?:child|of-type|last-child|last-of-type)\([^)]*\)")
# Generic wrappers that carry no component identity — skip them when picking a root.
_GENERIC_SEGMENTS = {"html", "body", ":root", "div", "span"}

# Component-type hints, matched against the root selector to give a friendly label.
_COMPONENT_HINTS: tuple[tuple[str, str], ...] = (
    ("nav", "Navigation"), ("header", "Header"), ("footer", "Footer"),
    ("menu", "Menu / navigation"), ("breadcrumb", "Breadcrumbs"),
    ("form", "Form"), ("search", "Search"), ("table", "Table"),
    ("modal", "Modal / dialog"), ("dialog", "Modal / dialog"),
    ("carousel", "Carousel / slider"), ("slider", "Carousel / slider"),
    ("accordion", "Accordion"), ("tab", "Tabs"), ("aside", "Sidebar"),
    ("sidebar", "Sidebar"), ("hero", "Hero"), ("banner", "Banner"),
    ("card", "Cards"), ("gallery", "Gallery"), ("main", "Main content"),
    ("article", "Article body"), ("widget", "Widget"),
)


def _selector_of(node: dict[str, Any]) -> str:
    target = node.get("target") or []
    if isinstance(target, list):
        # For iframe/shadow targets axe nests selectors; the last is the deepest.
        return str(target[-1]) if target else ""
    return str(target)


def _component_key(selector: str, *, depth: int = 2) -> str:
    """Reduce a node selector to a stable 'component root' others can group on.

    Strips sibling indices, drops generic top-level wrappers, and keeps the first
    ``depth`` meaningful path segments — so ``nav.main > ul > li:nth-child(9) > a``
    and ``nav.main > ul > li:nth-child(2)`` and ``nav.main > ul`` all reduce to
    ``nav.main > ul`` and cluster together.
    """
    norm = _NTH_RE.sub("", selector or "").strip()
    if not norm:
        return ""
    segs = [s.strip() for s in norm.split(">") if s.strip()]

    def _base(seg: str) -> str:
        return seg.split(":")[0].split("[")[0].split(".")[0].split("#")[0].strip().lower()

    meaningful = [s for s in segs if _base(s) not in _GENERIC_SEGMENTS]
    if not meaningful:
        meaningful = segs
    return " > ".join(meaningful[:depth]) if meaningful else norm


def _component_label(key: str) -> str | None:
    low = key.lower()
    for kw, label in _COMPONENT_HINTS:
        if kw in low:
            return label
    return None


def cluster_components(scan: dict[str, Any], *, min_component_elements: int = 5) -> list[dict[str, Any]]:
    """Group violation elements by shared selector root — likely single root causes.

    Returns a list of clusters (most significant first)::

      {
        "key": "nav.main > ul",       # the shared selector root
        "label": "Navigation" | None, # friendly component type, if recognised
        "element_count": 205,         # affected elements in this cluster
        "page_count": 2,
        "rules": [ {"id","impact","help","helpUrl","count"}, ... ],  # rules it trips
        "rule_count": 3,
        "worst_impact": "serious",
        "is_component": True,         # heuristic: enough elements to be a shared component
      }

    A cluster with many elements spanning several rules is the tell-tale of one
    broken template — fix it once, not per element.
    """
    clusters: dict[str, dict[str, Any]] = {}
    for pg in scan.get("pages") or []:
        page_url = pg.get("url", "")
        for v in pg.get("violations") or []:
            rid = v["id"]
            impact = (v.get("impact") or "minor").lower()
            for n in v.get("nodes") or []:
                key = _component_key(_selector_of(n))
                c = clusters.setdefault(key, {"key": key, "element_count": 0,
                                              "pages": set(), "rules": {}})
                c["element_count"] += 1
                c["pages"].add(page_url)
                rr = c["rules"].setdefault(rid, {"id": rid, "impact": impact,
                                                 "help": v.get("help", ""),
                                                 "helpUrl": v.get("helpUrl", ""), "count": 0})
                rr["count"] += 1

    out: list[dict[str, Any]] = []
    for c in clusters.values():
        rules = sorted(
            c["rules"].values(),
            key=lambda r: (IMPACT_ORDER.index(r["impact"]) if r["impact"] in IMPACT_ORDER else 9,
                           -r["count"]),
        )
        worst = rules[0]["impact"] if rules else "minor"
        out.append({
            "key": c["key"],
            "label": _component_label(c["key"]),
            "element_count": c["element_count"],
            "page_count": len(c["pages"]),
            "rules": rules,
            "rule_count": len(rules),
            "worst_impact": worst,
            "is_component": c["element_count"] >= min_component_elements,
        })

    out.sort(key=lambda c: (0 if c["is_component"] else 1,
                            IMPACT_ORDER.index(c["worst_impact"]) if c["worst_impact"] in IMPACT_ORDER else 9,
                            -c["element_count"]))
    return out
