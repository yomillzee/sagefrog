"""Premium UI for the Consent & Tracking Health feature.

Server-renders the full report from the scan result view model (built by
``consent_service.build_result``) so the page is fast, accessible, and works
without JavaScript; a small progressive-enhancement layer adds filtering,
drill-down, the config drawer, and the live "Run scan" flow.

The page reuses the shared client shell (navy sidebar + topbar) via
``render_client_shell_page`` and scopes all of its own styles under a ``cth-``
prefix so nothing leaks into the rest of the dashboard.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from dashboard.renderers.base_layout import render_client_shell_page

_esc = html.escape

# ── Visual vocabulary ───────────────────────────────────────────────────────
SEVERITY_META: dict[str, dict[str, str]] = {
    "critical": {"label": "Critical", "fg": "#b42318", "bg": "#fef2f2", "bd": "#fecaca"},
    "high":     {"label": "High",     "fg": "#c2410c", "bg": "#fff7ed", "bd": "#fed7aa"},
    "medium":   {"label": "Medium",   "fg": "#b45309", "bg": "#fffbeb", "bd": "#fde68a"},
    "low":      {"label": "Low",      "fg": "#475569", "bg": "#f1f5f9", "bd": "#e2e8f0"},
    "info":     {"label": "Info",     "fg": "#0b5cab", "bg": "#eff6ff", "bd": "#dbeafe"},
    "ok":       {"label": "OK",       "fg": "#15803d", "bg": "#ecfdf3", "bd": "#bbf7d0"},
}
HEALTH_META: dict[str, dict[str, str]] = {
    "pass":      {"label": "Healthy",         "fg": "#15803d", "bg": "#ecfdf3", "ring": "#22c55e"},
    "attention": {"label": "Needs attention", "fg": "#b45309", "bg": "#fffbeb", "ring": "#f59e0b"},
    "fail":      {"label": "Critical issues",  "fg": "#b42318", "bg": "#fef2f2", "ring": "#ef4444"},
    "unknown":   {"label": "Not yet scanned",  "fg": "#64748b", "bg": "#f8fafc", "ring": "#cbd5e1"},
}
PHASE_META = [
    ("pre_consent", "Before consent", "What fires when a visitor first lands, before choosing."),
    ("reject_all", "After “Reject All”", "What still fires once the visitor opts out."),
    ("accept_all", "After “Accept All”", "The full picture with consent granted — your baseline."),
]

_I_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>'
_I_ALERT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
_I_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
_I_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v5h-5"/></svg>'
_I_SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/></svg>'
_I_CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>'
_I_INFO = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" '
           'stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/>'
           '<path d="M8 7.3v3.4"/><circle cx="8" cy="4.9" r=".85" fill="currentColor" stroke="none"/></svg>')

_HEALTH_ICON = {"pass": _I_CHECK, "attention": _I_ALERT, "fail": _I_X, "unknown": _I_SHIELD}

# Plain-English explanations for the jargon on the page. Surfaced through the
# little ⓘ tooltips so a non-technical team member can read a number and know
# both what it means and why it matters.
_TOOLTIPS: dict[str, str] = {
    "identifiers": (
        "A tracking identifier is a value that can single out a visitor — a cookie like "
        "_ga or _fbp, or an ID sent inside a request. If any are set or sent before the "
        "visitor opts in, that's a genuine compliance problem, so this number should be 0."
    ),
    "pre_issues": (
        "Anything that needs consent but fired before the visitor made a choice. These are "
        "the items most likely to be a compliance risk — aim for 0."
    ),
    "reject_issues": (
        "Anything that kept tracking the visitor after they clicked “Reject All”. Once "
        "someone opts out, all non-essential tracking should stop — this should be 0."
    ),
    "cookieless": (
        "Cookieless pings are anonymous signals Google Consent Mode sends before consent. "
        "They carry no cookies or identifiers, so they're privacy-safe — a healthy count is "
        "actually a good sign that Consent Mode is switched on and measuring without "
        "tracking anyone."
    ),
    "vendors": (
        "The distinct third-party companies (Google, Meta, LinkedIn, …) your pages loaded "
        "scripts from or sent data to."
    ),
    "banner": (
        "How many of the scanned pages actually showed a consent banner before anything "
        "could track the visitor."
    ),
    "pre_consent": (
        "What runs when a visitor first lands, before they've chosen anything. Only "
        "strictly-necessary tracking and privacy-safe signals belong here."
    ),
    "reject_all": (
        "What still runs after the visitor clicks “Reject All”. Nothing that needs consent "
        "should fire in this state."
    ),
    "accept_all": (
        "What runs once the visitor grants full consent — your baseline for everything the "
        "site is allowed to load."
    ),
}


def _tip(key: str) -> str:
    """A small ⓘ affordance that reveals a plain-English explanation on hover/focus."""
    text = _TOOLTIPS.get(key, "")
    if not text:
        return ""
    return (f'<span class="cth-tip" tabindex="0" role="img" aria-label="{_esc(text)}">{_I_INFO}'
            f'<span class="cth-tip-pop" aria-hidden="true">{_esc(text)}</span></span>')


# ── Small helpers ───────────────────────────────────────────────────────────

def _sev_badge(sev: str, *, text: str | None = None) -> str:
    m = SEVERITY_META.get(sev, SEVERITY_META["info"])
    return (f'<span class="cth-badge" style="color:{m["fg"]};background:{m["bg"]};'
            f'border-color:{m["bd"]}">{_esc(text or m["label"])}</span>')


def _rel_time(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return _esc(str(iso))
    now = datetime.now(timezone.utc)
    delta = now - dt.astimezone(timezone.utc)
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} hr ago"
    if secs < 2592000:
        return f"{secs // 86400} day{'s' if secs // 86400 != 1 else ''} ago"
    return dt.strftime("%b %-d, %Y")


def _abs_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %-d, %Y at %H:%M UTC")
    except (ValueError, AttributeError):
        return _esc(str(iso))


def _host(url: str) -> str:
    u = url.replace("https://", "").replace("http://", "")
    return u.split("/")[0] or url


def _evidence_pill(evidence: str, label: str) -> str:
    ident = evidence in ("identifier_stored", "identifier_transmitted")
    ping = evidence == "cookieless_ping"
    cls = "cth-ev cth-ev-id" if ident else ("cth-ev cth-ev-ping" if ping else "cth-ev")
    return f'<span class="{cls}">{_esc(label)}</span>'


# ── Section builders ────────────────────────────────────────────────────────

def _health_hero(result: dict[str, Any]) -> str:
    health = result.get("health", "unknown")
    hm = HEALTH_META.get(health, HEALTH_META["unknown"])
    icon = _HEALTH_ICON.get(health, _I_SHIELD)
    summary = result.get("summary", {})
    headline = summary.get("headline", "")
    scanned = result.get("scanned_at")
    return f"""
    <section class="cth-hero cth-health-{health}" aria-label="Overall consent health">
      <div class="cth-hero-medallion" style="--ring:{hm['ring']};color:{hm['fg']}">
        <span class="cth-hero-icon">{icon}</span>
      </div>
      <div class="cth-hero-body">
        <div class="cth-hero-eyebrow">Is consent set up correctly?</div>
        <div class="cth-hero-status" style="color:{hm['fg']}">{_esc(hm['label'])}</div>
        <p class="cth-hero-headline">{_esc(headline)}</p>
        <div class="cth-hero-meta">Scanned {_esc(_rel_time(scanned))} · {summary.get('pages_scanned',0)} page(s) · {_esc(str(result.get('engine','chromium')).title())} headless browser</div>
      </div>
    </section>"""


def _key_numbers(result: dict[str, Any]) -> str:
    """The six numbers that matter, each with a plain-English tooltip.

    This is the page's single source of headline metrics — the hero no longer
    repeats them — so a non-technical reader gets the whole picture in one glance
    and can hover any figure to learn what it means.
    """
    s = result.get("summary", {})
    pt = result.get("phase_totals", {})
    pre = pt.get("pre_consent", {}).get("violation_count", 0)
    rej = pt.get("reject_all", {}).get("violation_count", 0)
    ids = s.get("identifiers_before_consent", 0)
    banner = f"{s.get('banner_pages', 0)}/{s.get('pages_scanned', 0)}"
    # When no opt-out control was ever found, "Issues after Reject All" is not a
    # measurable number — there was no Reject All to honour. Show it as n/a rather
    # than a green "0" that would imply the site correctly stops tracking on opt-out.
    if s.get("reject_control_found"):
        reject_tile = ("Issues after “Reject All”", rej, "after opt-out",
                       "danger" if rej else "ok", "reject_issues")
    else:
        reject_tile = ("Issues after “Reject All”", "n/a", "no opt-out control",
                       "neutral", "reject_issues")
    # (label, value, sub, tone, tooltip-key)
    tiles = [
        ("Trackers before consent", ids, "should be 0", "danger" if ids else "ok", "identifiers"),
        ("Issues before consent", pre, "before any choice", "danger" if pre else "ok", "pre_issues"),
        reject_tile,
        ("Cookieless pings", s.get("cookieless_pings", 0), "privacy-safe", "ok", "cookieless"),
        ("Third-party vendors", s.get("vendor_count", 0), "distinct companies", "neutral", "vendors"),
        ("Banner coverage", banner, "pages with a banner", "neutral", "banner"),
    ]
    cells = []
    for label, value, sub, tone, tip in tiles:
        sub_html = f'<div class="cth-tile-s">{_esc(sub)}</div>' if sub else ""
        cells.append(
            f'<div class="cth-tile cth-tone-{tone}"><div class="cth-tile-v">{_esc(str(value))}</div>'
            f'<div class="cth-tile-l">{_esc(label)}{_tip(tip)}</div>{sub_html}</div>'
        )
    return f'<section class="cth-tiles" aria-label="Key numbers">{"".join(cells)}</section>'


def _diff_section(result: dict[str, Any]) -> str:
    diff = result.get("diff", {})
    if not diff.get("has_previous"):
        return ""
    hc = diff.get("health_change", {})
    new_v = diff.get("new_violations", [])
    resolved = diff.get("resolved_violations", [])
    new_vendors = diff.get("new_vendors", [])
    removed_vendors = diff.get("removed_vendors", [])
    if not any([new_v, resolved, new_vendors, removed_vendors]) and hc.get("from") == hc.get("to"):
        chip = '<span class="cth-diff-none">No change since the previous scan.</span>'
        return f'<section class="cth-card cth-diff"><h2 class="cth-h2">Changes since last scan</h2>{chip}</section>'

    def _delta_pill(frm, to):
        fm = HEALTH_META.get(frm, HEALTH_META["unknown"])
        tm = HEALTH_META.get(to, HEALTH_META["unknown"])
        arrow = "→"
        return (f'<span class="cth-delta"><span style="color:{fm["fg"]}">{_esc(fm["label"])}</span>'
                f' {arrow} <span style="color:{tm["fg"]}">{_esc(tm["label"])}</span></span>')

    rows = []
    if hc.get("from") != hc.get("to"):
        rows.append(f'<div class="cth-diff-row">Overall health {_delta_pill(hc.get("from"), hc.get("to"))}</div>')
    if resolved:
        items = "".join(
            f'<li class="cth-diff-good">{_I_CHECK} Resolved: <strong>{_esc(v.get("vendor_name",""))}</strong> '
            f'{_esc(v.get("label",""))} <span class="cth-muted">({_esc(v.get("phase_label",""))})</span></li>'
            for v in resolved[:8])
        rows.append(f'<div class="cth-diff-col"><div class="cth-diff-h">Resolved ({len(resolved)})</div><ul class="cth-diff-list">{items}</ul></div>')
    if new_v:
        items = "".join(
            f'<li class="cth-diff-bad">{_I_ALERT} New: <strong>{_esc(v.get("vendor_name",""))}</strong> '
            f'{_esc(v.get("label",""))} <span class="cth-muted">({_esc(v.get("phase_label",""))})</span></li>'
            for v in new_v[:8])
        rows.append(f'<div class="cth-diff-col"><div class="cth-diff-h">New issues ({len(new_v)})</div><ul class="cth-diff-list">{items}</ul></div>')
    vend_bits = []
    if new_vendors:
        vend_bits.append("Added: " + ", ".join(_esc(v["vendor_name"]) for v in new_vendors))
    if removed_vendors:
        vend_bits.append("Removed: " + ", ".join(_esc(v["vendor_name"]) for v in removed_vendors))
    if vend_bits:
        rows.append(f'<div class="cth-diff-row cth-muted">Vendor changes — {" · ".join(vend_bits)}</div>')

    prev_when = _rel_time(diff.get("previous_scanned_at"))
    return f"""
    <section class="cth-card cth-diff">
      <div class="cth-card-head"><h2 class="cth-h2">Changes since last scan</h2>
        <span class="cth-muted">vs scan from {_esc(prev_when)}</span></div>
      <div class="cth-diff-grid">{''.join(rows)}</div>
    </section>"""


def _phase_compare(result: dict[str, Any]) -> str:
    pt = result.get("phase_totals", {})
    reject_control_found = result.get("summary", {}).get("reject_control_found", True)
    cols = []
    for key, label, desc in PHASE_META:
        data = pt.get(key, {})
        status = data.get("status", "unknown")
        hm = HEALTH_META.get(status, HEALTH_META["unknown"])
        counts = data.get("counts", {})
        vio = data.get("violation_count", 0)
        ident = data.get("identifier_count", 0)
        # The reject phase only measures a real "rejected" state if an opt-out
        # control was actually found; otherwise it just repeats the pre-consent load.
        reject_no_control = key == "reject_all" and not reject_control_found
        bars = []
        for sev in ("critical", "high", "medium", "low", "info", "ok"):
            n = counts.get(sev, 0)
            if not n:
                continue
            m = SEVERITY_META[sev]
            bars.append(f'<span class="cth-cnt" style="color:{m["fg"]};background:{m["bg"]};border-color:{m["bd"]}">'
                        f'{n} {_esc(m["label"].lower())}</span>')
        if reject_no_control:
            headline = "No opt-out control found"
        else:
            headline = (f'{vio} issue{"s" if vio != 1 else ""}' if vio else "No consent issues")
        return_note = f'{ident} identifier{"s" if ident != 1 else ""} present'
        cols.append(f"""
        <div class="cth-phase cth-health-{status}">
          <div class="cth-phase-top">
            <span class="cth-phase-dot" style="background:{hm['ring']}"></span>
            <div><div class="cth-phase-label">{_esc(label)}{_tip(key)}</div>
            <div class="cth-phase-status" style="color:{hm['fg']}">{_esc(headline)}</div></div>
          </div>
          <p class="cth-phase-desc">{_esc(desc)}</p>
          <div class="cth-phase-counts">{''.join(bars) or '<span class="cth-muted">Nothing captured.</span>'}</div>
          <div class="cth-phase-foot">{_esc(return_note)}</div>
        </div>""")
    return f"""
    <section class="cth-card">
      <div class="cth-card-head"><h2 class="cth-h2">Consent states compared</h2>
        <span class="cth-muted">Each state is scanned in a fresh browser session</span></div>
      <div class="cth-phase-grid">{''.join(cols)}</div>
    </section>"""


_RECOMMENDATIONS = {
    "identifier_stored": "Configure your CMP/tag manager to block this tag until the matching consent category is granted.",
    "identifier_transmitted": "Gate this tag on consent, or switch the vendor to a consent-aware (cookieless) mode until opt-in.",
    "tracking_beacon": "Hold this beacon behind consent, or confirm it is a legitimately consentless measurement signal.",
    "cookie_set": "Move this cookie behind the relevant consent category or drop it if it is not strictly necessary.",
}


def _priority_findings(result: dict[str, Any]) -> str:
    tops = result.get("top_findings", [])
    if not tops:
        return f"""
        <section class="cth-card">
          <div class="cth-card-head"><h2 class="cth-h2">What to fix</h2></div>
          <div class="cth-empty-inline">{_I_CHECK}<div><strong>Nothing to fix.</strong>
          No tracking that requires consent ran before the visitor opted in, and “Reject All” was honoured.</div></div>
        </section>"""
    cards = []
    for f in tops[:20]:
        sev = f.get("severity", "info")
        m = SEVERITY_META.get(sev, SEVERITY_META["info"])
        rec = _RECOMMENDATIONS.get(f.get("evidence", ""), "Review this against your consent policy.")
        cards.append(f"""
        <article class="cth-finding" style="--sev:{m['fg']};--sevbg:{m['bg']}"
                 data-severity="{_esc(sev)}" data-category="{_esc(f.get('category',''))}">
          <div class="cth-finding-side"></div>
          <div class="cth-finding-main">
            <div class="cth-finding-top">
              {_sev_badge(sev)}
              <span class="cth-finding-vendor">{_esc(f.get('vendor_name',''))}</span>
              {_evidence_pill(f.get('evidence',''), f.get('evidence_label',''))}
              <span class="cth-finding-phase">{_esc(f.get('phase_label',''))}</span>
            </div>
            <div class="cth-finding-label">{_esc(f.get('label',''))}</div>
            <p class="cth-finding-why">{_esc(f.get('rationale',''))}</p>
            <p class="cth-finding-rec"><strong>Recommended:</strong> {_esc(rec)}</p>
          </div>
        </article>""")
    return f"""
    <section class="cth-card">
      <div class="cth-card-head"><h2 class="cth-h2">What to fix</h2>
        <span class="cth-muted">{len(tops)} item(s) that need consent but fired before it</span></div>
      <div class="cth-findings">{''.join(cards)}</div>
    </section>"""


def _vendors_section(result: dict[str, Any]) -> str:
    vendors = result.get("vendors", [])
    cats = result.get("categories", [])
    if not vendors:
        return ""
    chips = ['<button type="button" class="cth-chip cth-chip-on" data-catfilter="all">All vendors</button>']
    for c in cats:
        chips.append(f'<button type="button" class="cth-chip" data-catfilter="{_esc(c["category"])}">'
                     f'{_esc(c["label"])} <span class="cth-chip-n">{c["count"]}</span></button>')
    rows = []
    for v in vendors:
        sev = v.get("worst_severity", "ok")
        ev_pills = "".join(
            _evidence_pill(e, _EVIDENCE_SHORT.get(e, e.replace("_", " ")))
            for e in v.get("evidence_types", [])[:4])
        phases = " · ".join(_PHASE_SHORT.get(p, p) for p in v.get("phases", []))
        idflag = ('<span class="cth-idflag">sets identifier</span>' if v.get("sets_identifier")
                  else '<span class="cth-muted">—</span>')
        rows.append(f"""
        <tr data-category="{_esc(v.get('category',''))}">
          <td><div class="cth-vendor-name">{_esc(v.get('vendor_name',''))}</div>
              <div class="cth-vendor-purpose">{_esc(v.get('purpose','') or '')}</div></td>
          <td><span class="cth-cat-tag cth-cat-{_esc(v.get('category',''))}">{_esc(v.get('category_label',''))}</span></td>
          <td class="cth-ev-cell">{ev_pills or '<span class="cth-muted">—</span>'}</td>
          <td>{idflag}</td>
          <td class="cth-nowrap">{_esc(phases)}</td>
          <td>{_sev_badge(sev)}</td>
        </tr>""")
    return f"""
    <section class="cth-card">
      <div class="cth-card-head"><h2 class="cth-h2">Vendors &amp; requests</h2>
        <span class="cth-muted">Everything the pages loaded, categorised</span></div>
      <div class="cth-chips" role="tablist" aria-label="Filter by category">{''.join(chips)}</div>
      <div class="cth-table-wrap">
      <table class="cth-table" id="cth-vendor-table">
        <thead><tr><th>Vendor</th><th>Category</th><th>Evidence</th><th>Identifier</th><th>Seen in</th><th>Worst (pre-consent)</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table></div>
    </section>"""


_EVIDENCE_SHORT = {
    "script_loaded": "script", "network_request": "request", "tracking_beacon": "beacon",
    "cookieless_ping": "cookieless ping", "identifier_stored": "id stored",
    "identifier_transmitted": "id sent", "cookie_set": "cookie", "storage_set": "storage",
}
_PHASE_SHORT = {"pre_consent": "Pre", "reject_all": "Reject", "accept_all": "Accept"}


def _page_drilldown(result: dict[str, Any]) -> str:
    pages = result.get("pages", [])
    if not pages:
        return ""
    blocks = []
    for pi, page in enumerate(pages):
        health = page.get("health", "unknown")
        hm = HEALTH_META.get(health, HEALTH_META["unknown"])
        phase_tabs = []
        phase_panels = []
        for ti, (key, label, _desc) in enumerate(PHASE_META):
            ph = page.get("phases", {}).get(key, {})
            status = ph.get("status", "unknown")
            phm = HEALTH_META.get(status, HEALTH_META["unknown"])
            active = " cth-on" if ti == 0 else ""
            phase_tabs.append(
                f'<button type="button" class="cth-ptab{active}" data-page="{pi}" data-tab="{ti}">'
                f'<span class="cth-phase-dot" style="background:{phm["ring"]}"></span>{_esc(label)}</button>')
            phase_panels.append(_phase_panel(ph, pi, ti, active=(ti == 0)))
        interaction_notes = _interaction_note(page)
        blocks.append(f"""
        <div class="cth-page" data-page="{pi}">
          <button type="button" class="cth-page-head" aria-expanded="{'true' if pi == 0 else 'false'}" data-page="{pi}">
            <span class="cth-page-dot" style="background:{hm['ring']}"></span>
            <span class="cth-page-url">{_esc(_host(page.get('url','')))}<span class="cth-page-path">{_esc(_pathof(page.get('url','')))}</span></span>
            <span class="cth-page-status" style="color:{hm['fg']}">{_esc(hm['label'])}</span>
            <span class="cth-page-chevron">{_I_CHEVRON}</span>
          </button>
          <div class="cth-page-body" {'style="display:block"' if pi == 0 else ''}>
            {interaction_notes}
            <div class="cth-ptabs" role="tablist">{''.join(phase_tabs)}</div>
            {''.join(phase_panels)}
          </div>
        </div>""")
    return f"""
    <section class="cth-card">
      <div class="cth-card-head"><h2 class="cth-h2">Page-by-page detail</h2>
        <span class="cth-muted">Drill into every script, request, cookie and storage entry</span></div>
      <div class="cth-pages">{''.join(blocks)}</div>
    </section>"""


def _interaction_note(page: dict[str, Any]) -> str:
    notes = []
    for key, label, _d in PHASE_META[1:]:
        ph = page.get("phases", {}).get(key, {})
        inter = ph.get("interaction", {})
        if key in ("reject_all", "accept_all") and not inter.get("clicked"):
            notes.append(f'Could not find a “{"Reject" if key=="reject_all" else "Accept"} All” control on this page.')
    if not notes:
        return ""
    return f'<div class="cth-note-warn">{_I_ALERT}<span>{" ".join(_esc(n) for n in notes)}</span></div>'


def _phase_panel(ph: dict[str, Any], pi: int, ti: int, *, active: bool) -> str:
    findings = ph.get("findings", [])
    banner_issue = ph.get("banner_issue", "")
    style = "block" if active else "none"
    if not findings:
        body = '<div class="cth-empty-inline cth-empty-sm">Nothing was captured in this state.</div>'
    else:
        rows = []
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "ok": 5}
        for f in sorted(findings, key=lambda x: order.get(x.get("severity", "ok"), 9)):
            sev = f.get("severity", "info")
            viol = "cth-row-viol" if f.get("is_violation") else ""
            id_dot = '<span class="cth-iddot" title="Carries a persistent identifier"></span>' if f.get("carries_identifier") else ""
            rows.append(f"""
            <tr class="{viol}" data-severity="{_esc(sev)}" data-category="{_esc(f.get('category',''))}">
              <td>{_sev_badge(sev)}</td>
              <td><span class="cth-kind cth-kind-{_esc(f.get('kind',''))}">{_esc(f.get('kind',''))}</span></td>
              <td>{_evidence_pill(f.get('evidence',''), _EVIDENCE_SHORT.get(f.get('evidence',''), f.get('evidence','')))}{id_dot}</td>
              <td>{_esc(f.get('vendor_name',''))}</td>
              <td class="cth-mono">{_esc(f.get('label',''))}</td>
              <td class="cth-why">{_esc(f.get('rationale',''))}</td>
            </tr>""")
        body = f"""
          <div class="cth-table-wrap"><table class="cth-table cth-table-sm">
            <thead><tr><th>Severity</th><th>Type</th><th>Evidence</th><th>Vendor</th><th>Detail</th><th>Assessment</th></tr></thead>
            <tbody>{''.join(rows)}</tbody></table></div>"""
    banner_html = f'<div class="cth-note-warn">{_I_ALERT}<span>{_esc(banner_issue)}</span></div>' if banner_issue else ""
    return f'<div class="cth-ppanel" data-page="{pi}" data-tab="{ti}" style="display:{style}">{banner_html}{body}</div>'


def _pathof(url: str) -> str:
    u = url.replace("https://", "").replace("http://", "")
    slash = u.find("/")
    return u[slash:] if slash >= 0 else ""


def _expectations_section(result: dict[str, Any]) -> str:
    exp = result.get("expectations", {})
    cats = exp.get("require_consent_categories", [])
    from consent_knowledge import CATEGORY_LABELS
    cat_labels = ", ".join(CATEGORY_LABELS.get(c, c) for c in cats) or "none"
    items = [
        ("Categories that require opt-in", cat_labels),
        ("Consent Mode cookieless pings", "Allowed before consent" if exp.get("allow_consent_mode_pings") else "Not allowed"),
        ("Consent banner", "Required on every page" if exp.get("banner_required") else "Optional"),
        ("After “Reject All”", "No non-essential identifiers permitted" if exp.get("strict_reject") else "Lenient"),
    ]
    lis = "".join(f'<div class="cth-exp-item"><dt>{_esc(k)}</dt><dd>{_esc(v)}</dd></div>' for k, v in items)
    return f"""
    <section class="cth-card cth-exp">
      <div class="cth-card-head"><h2 class="cth-h2">What we checked against</h2>
        <span class="cth-muted">Editable consent expectations</span></div>
      <div class="cth-exp-grid">{lis}</div>
      <p class="cth-method">Each page is loaded three times in a fresh, isolated browser session — before any
      choice, after “Reject All”, and after “Accept All”. We record scripts downloaded, requests transmitted,
      cookies, browser storage and Google Consent Mode signals, then judge them against the expectations above.
      A downloaded script or a consent-denied cookieless ping is <strong>not</strong> treated as a violation —
      only identifiers stored or transmitted for a consent-required category are.</p>
    </section>"""


def _history_section(overview: dict[str, Any]) -> str:
    hist = overview.get("history", [])
    completed = [h for h in hist if h and h.get("status") == "completed"]
    if len(completed) < 1:
        return ""
    dots = []
    for h in reversed(completed[:12]):
        hm = HEALTH_META.get(h.get("health", "unknown"), HEALTH_META["unknown"])
        title = f'{h.get("health_label","")} · {_abs_time(h.get("started_at"))} · {h.get("violation_count",0)} issue(s)'
        dots.append(f'<span class="cth-hdot" style="background:{hm["ring"]}" title="{_esc(title)}"></span>')
    return f"""
    <section class="cth-card cth-history">
      <div class="cth-card-head"><h2 class="cth-h2">Scan history</h2>
        <span class="cth-muted">{len(completed)} completed scan(s)</span></div>
      <div class="cth-hdots">{''.join(dots)}</div>
    </section>"""


def _technical_details(result: dict[str, Any], overview: dict[str, Any]) -> str:
    """The full vendor inventory, per-page drill-down, methodology and history,
    collapsed into a single disclosure so the top of the page stays scannable.

    Detail is preserved in full — it's one click away for anyone who wants it,
    but it no longer buries the verdict for team members who just need the answer.
    """
    inner = (
        _vendors_section(result)
        + _page_drilldown(result)
        + _expectations_section(result)
        + _history_section(overview)
    )
    if not inner.strip():
        return ""
    return f"""
    <details class="cth-adv">
      <summary class="cth-adv-summary">
        <span class="cth-adv-title">Technical detail</span>
        <span class="cth-adv-hint">Every vendor, request, cookie and storage entry — page by page</span>
        <span class="cth-adv-chevron">{_I_CHEVRON}</span>
      </summary>
      <div class="cth-adv-body">{inner}</div>
    </details>"""


# ── Empty / loading / error states ──────────────────────────────────────────

def _setup_state(client_slug: str, label: str, config: dict[str, Any] | None) -> str:
    homepage = ""
    if config and config.get("pages"):
        homepage = config["pages"][0]
    return f"""
    <section class="cth-card cth-setup">
      <div class="cth-setup-icon">{_I_SHIELD}</div>
      <h2 class="cth-h2">Set up consent &amp; tracking monitoring</h2>
      <p class="cth-setup-lead">Point the scanner at the pages you want to watch. We’ll load each one in a fresh
      browser — before consent, after “Reject All”, and after “Accept All” — and tell you exactly what tracks,
      when, and whether it should.</p>
      <form class="cth-setup-form" data-consent-config>
        <label class="cth-field">
          <span>Pages to scan <span class="cth-muted">(one URL per line)</span></span>
          <textarea name="pages" rows="3" placeholder="https://www.example.com&#10;https://www.example.com/pricing">{_esc(chr(10).join(config.get('pages', [])) if config else '')}</textarea>
        </label>
        <div class="cth-setup-actions">
          <button type="submit" class="cth-btn cth-btn-primary">Save &amp; run first scan</button>
        </div>
        <div class="cth-form-msg" data-config-msg role="status"></div>
      </form>
    </section>"""


def _running_banner() -> str:
    return f"""
    <div class="cth-runbanner" data-scanning="1">
      <span class="cth-spinner">{_I_REFRESH}</span>
      <span>Scanning in a fresh browser session across all three consent states… this can take a minute.</span>
    </div>"""


def _error_banner(message: str) -> str:
    return f"""
    <div class="cth-errbanner">{_I_ALERT}
      <div><strong>The last scan didn’t finish.</strong> {_esc(message or 'Unknown error.')}</div>
    </div>"""


# ── Config drawer ───────────────────────────────────────────────────────────

def _config_drawer(config: dict[str, Any] | None) -> str:
    from consent_knowledge import CATEGORY_LABELS, DEFAULT_CONSENT_REQUIRED
    exp = (config or {}).get("expectations") or {}
    pages = (config or {}).get("pages") or []
    req = set(exp.get("require_consent_categories") or DEFAULT_CONSENT_REQUIRED)
    freq = (config or {}).get("scan_frequency", "weekly")
    enabled = bool((config or {}).get("scan_enabled"))
    cat_checks = []
    for cat in ("advertising", "analytics", "social", "personalization", "functional"):
        checked = "checked" if cat in req else ""
        cat_checks.append(
            f'<label class="cth-check"><input type="checkbox" name="require_{cat}" {checked}>'
            f'<span>{_esc(CATEGORY_LABELS.get(cat, cat))}</span></label>')
    freq_opts = "".join(
        f'<option value="{f}"{" selected" if f == freq else ""}>{f.title()}</option>'
        for f in ("manual", "daily", "weekly", "monthly"))
    return f"""
    <div class="cth-drawer" id="cth-drawer" hidden>
      <div class="cth-drawer-backdrop" data-close-drawer></div>
      <div class="cth-drawer-panel" role="dialog" aria-label="Consent scan settings">
        <div class="cth-drawer-head"><h2 class="cth-h2">Scan settings</h2>
          <button type="button" class="cth-drawer-x" data-close-drawer aria-label="Close">{_I_X}</button></div>
        <form data-consent-config class="cth-drawer-form">
          <label class="cth-field"><span>Pages to scan <span class="cth-muted">(one URL per line)</span></span>
            <textarea name="pages" rows="4">{_esc(chr(10).join(pages))}</textarea></label>
          <fieldset class="cth-field"><legend>Categories that require consent</legend>
            <div class="cth-checks">{''.join(cat_checks)}</div></fieldset>
          <label class="cth-check"><input type="checkbox" name="allow_consent_mode_pings" {'checked' if exp.get('allow_consent_mode_pings', True) else ''}>
            <span>Allow Google Consent Mode cookieless pings before consent</span></label>
          <label class="cth-check"><input type="checkbox" name="banner_required" {'checked' if exp.get('banner_required', True) else ''}>
            <span>Require a consent banner on every page</span></label>
          <div class="cth-field-row">
            <label class="cth-field"><span>Scheduled scan</span>
              <select name="scan_frequency">{freq_opts}</select></label>
            <label class="cth-check cth-check-inline"><input type="checkbox" name="scan_enabled" {'checked' if enabled else ''}>
              <span>Enable scheduled scanning</span></label>
          </div>
          <div class="cth-drawer-actions">
            <button type="submit" class="cth-btn cth-btn-primary">Save settings</button>
            <button type="button" class="cth-btn" data-close-drawer>Cancel</button>
          </div>
          <div class="cth-form-msg" data-config-msg role="status"></div>
        </form>
      </div>
    </div>"""


# ── Top-level ───────────────────────────────────────────────────────────────

def render_consent_page(
    *,
    client_slug: str,
    label: str,
    overview: dict[str, Any],
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    config = overview.get("config")
    latest = overview.get("latest")
    report = overview.get("report")
    report_run = overview.get("report_run")
    configured = overview.get("configured")

    status_banner = ""
    if latest and latest.get("status") == "running":
        status_banner = _running_banner()
    elif latest and latest.get("status") == "failed":
        status_banner = _error_banner(latest.get("error_message") or "")

    # Header actions
    freq = (config or {}).get("scan_frequency", "weekly")
    freq_badge = ""
    if config and (config.get("scan_enabled")):
        freq_badge = f'<span class="cth-freq">Auto · {_esc(freq)}</span>'
    scanning_attr = "1" if latest and latest.get("status") == "running" else "0"

    last_when = ""
    if report_run:
        last_when = f'Last scan {_esc(_rel_time(report_run.get("started_at")))}'
    header = f"""
    <div class="cth-header">
      <div class="cth-header-titles">
        <h1 class="cth-title">Consent &amp; Tracking Health</h1>
        <p class="cth-sub">{_esc(label)} · {last_when or 'Not yet scanned'} {freq_badge}</p>
      </div>
      <div class="cth-header-actions">
        <button type="button" class="cth-btn" data-open-drawer>{_I_SHIELD}<span>Settings</span></button>
        <button type="button" class="cth-btn cth-btn-primary" data-run-scan {'disabled' if scanning_attr=='1' else ''}>
          {_I_REFRESH}<span>{'Scanning…' if scanning_attr=='1' else 'Run scan'}</span></button>
      </div>
    </div>"""

    if not configured and not report:
        body = header + status_banner + _setup_state(client_slug, label, config)
    elif not report:
        # Configured but no completed scan yet (first scan pending/failed).
        body = header + status_banner + f"""
        <section class="cth-card cth-setup">
          <div class="cth-setup-icon">{_I_SHIELD}</div>
          <h2 class="cth-h2">Ready to scan</h2>
          <p class="cth-setup-lead">Monitoring is configured for {len((config or {}).get('pages', []))} page(s).
          Run your first scan to see the consent &amp; tracking health report.</p>
          <div class="cth-setup-actions"><button type="button" class="cth-btn cth-btn-primary" data-run-scan>{_I_REFRESH}<span>Run first scan</span></button></div>
        </section>"""
    else:
        report_error = ""
        if not report.get("available"):
            report_error = _error_banner(report.get("error") or "")
        # Above the fold: the verdict and the things a non-technical reader needs
        # to answer "is consent set up correctly?" — the health call, the key
        # numbers, what changed, what to fix, and the three states side by side.
        overview_html = (
            _health_hero(report)
            + _key_numbers(report)
            + _diff_section(report)
            + _priority_findings(report)
            + _phase_compare(report)
        )
        # Below the fold: the exhaustive detail, tucked into a collapsed drawer so
        # it's there for anyone who wants it without overwhelming everyone else.
        technical_html = _technical_details(report, overview)
        body = header + status_banner + report_error + overview_html + technical_html

    body += _config_drawer(config)
    body += f'<script>{_page_js(client_slug)}</script>'

    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="consent",
        page_title="Consent & Tracking Health",
        page_subtitle=f"{label} · Consent monitoring",
        content_html=body,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_CSS,
    )


def _page_js(client_slug: str) -> str:
    base = f"/dashboard/{client_slug}/consent"
    return (
        "(function(){"
        f"var BASE={json.dumps(base)};"
        # Vendor category filter
        "document.querySelectorAll('[data-catfilter]').forEach(function(btn){"
        "btn.addEventListener('click',function(){"
        "var cat=btn.getAttribute('data-catfilter');"
        "document.querySelectorAll('[data-catfilter]').forEach(function(b){b.classList.toggle('cth-chip-on',b===btn);});"
        "document.querySelectorAll('#cth-vendor-table tbody tr').forEach(function(tr){"
        "tr.style.display=(cat==='all'||tr.getAttribute('data-category')===cat)?'':'none';});"
        "});});"
        # Page accordion
        "document.querySelectorAll('.cth-page-head').forEach(function(h){"
        "h.addEventListener('click',function(){"
        "var body=h.parentElement.querySelector('.cth-page-body');"
        "var open=body.style.display==='block';"
        "body.style.display=open?'none':'block';"
        "h.setAttribute('aria-expanded',open?'false':'true');});});"
        # Phase tabs within a page
        "document.querySelectorAll('.cth-ptab').forEach(function(t){"
        "t.addEventListener('click',function(){"
        "var pg=t.getAttribute('data-page'),tab=t.getAttribute('data-tab');"
        "t.parentElement.querySelectorAll('.cth-ptab').forEach(function(x){x.classList.toggle('cth-on',x===t);});"
        "document.querySelectorAll('.cth-ppanel[data-page=\"'+pg+'\"]').forEach(function(p){"
        "p.style.display=(p.getAttribute('data-tab')===tab)?'block':'none';});});});"
        # Config drawer open/close
        "var drawer=document.getElementById('cth-drawer');"
        "document.querySelectorAll('[data-open-drawer]').forEach(function(b){b.addEventListener('click',function(){if(drawer)drawer.hidden=false;});});"
        "document.querySelectorAll('[data-close-drawer]').forEach(function(b){b.addEventListener('click',function(){if(drawer)drawer.hidden=true;});});"
        # Save config
        "document.querySelectorAll('form[data-consent-config]').forEach(function(f){"
        "f.addEventListener('submit',function(e){e.preventDefault();"
        "var msg=f.querySelector('[data-config-msg]');if(msg){msg.textContent='Saving…';msg.className='cth-form-msg';}"
        "var pages=(f.querySelector('[name=pages]')||{}).value||'';"
        "var cats=[];['advertising','analytics','social','personalization','functional'].forEach(function(c){"
        "var el=f.querySelector('[name=require_'+c+']');if(el&&el.checked)cats.push(c);});"
        "var payload={pages:pages.split(/\\n+/).map(function(s){return s.trim();}).filter(Boolean),"
        "require_consent_categories:cats,"
        "allow_consent_mode_pings:!!(f.querySelector('[name=allow_consent_mode_pings]')||{}).checked,"
        "banner_required:!!(f.querySelector('[name=banner_required]')||{}).checked,"
        "scan_frequency:(f.querySelector('[name=scan_frequency]')||{}).value||'weekly',"
        "scan_enabled:!!(f.querySelector('[name=scan_enabled]')||{}).checked,"
        "run_now:true};"
        "fetch(BASE+'/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})"
        ".then(function(r){return r.json();}).then(function(d){"
        "if(d.ok){if(msg){msg.textContent='Saved. Starting scan…';msg.className='cth-form-msg cth-ok';}setTimeout(pollUntilDone,1500);}"
        "else{if(msg){msg.textContent=d.error||'Could not save.';msg.className='cth-form-msg cth-bad';}}})"
        ".catch(function(){if(msg){msg.textContent='Network error.';msg.className='cth-form-msg cth-bad';}});});});"
        # Run scan
        "document.querySelectorAll('[data-run-scan]').forEach(function(b){b.addEventListener('click',function(){"
        "b.disabled=true;var sp=b.querySelector('span');if(sp)sp.textContent='Scanning…';b.classList.add('cth-scanning');"
        "fetch(BASE+'/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})"
        ".then(function(r){return r.json();}).then(function(d){if(d.ok){pollUntilDone();}else{alert(d.error||'Could not start scan.');b.disabled=false;}})"
        ".catch(function(){b.disabled=false;});});});"
        # Poll status endpoint until the running scan finishes, then reload.
        "function pollUntilDone(){"
        "fetch(BASE+'/status',{headers:{'Accept':'application/json'}}).then(function(r){return r.json();}).then(function(d){"
        "if(d && d.status==='running'){setTimeout(pollUntilDone,3000);}else{location.reload();}})"
        ".catch(function(){setTimeout(pollUntilDone,4000);});}"
        # If a scan is already running when the page loads, start polling.
        "if(document.querySelector('[data-scanning=\"1\"]')){setTimeout(pollUntilDone,3000);}"
        "})();"
    )


_CSS = """
.cth-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px}
.cth-title{font-size:1.6rem;font-weight:760;letter-spacing:-.02em;color:var(--navy);margin:0}
.cth-sub{margin:5px 0 0;color:var(--muted);font-size:.9rem;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.cth-header-actions{display:flex;gap:10px;flex-wrap:wrap}
.cth-freq{display:inline-flex;align-items:center;padding:2px 9px;border-radius:999px;background:var(--ok-bg);color:var(--ok);font-size:.72rem;font-weight:700;border:1px solid #bbf7d0}
.cth-btn{display:inline-flex;align-items:center;gap:7px;padding:9px 15px;border-radius:10px;font-size:.86rem;font-weight:640;cursor:pointer;border:1px solid var(--border);background:#fff;color:var(--navy);transition:all .15s;text-decoration:none;font-family:inherit}
.cth-btn:hover{background:#f4f8fd;border-color:#cdd9e8;box-shadow:var(--shadow-sm)}
.cth-btn:disabled{opacity:.65;cursor:default}
.cth-btn svg{width:16px;height:16px}
.cth-btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.cth-btn-primary:hover{background:#0a4f95;border-color:#0a4f95}
.cth-btn.cth-scanning svg,.cth-spinner svg{animation:cth-spin .9s linear infinite}
@keyframes cth-spin{to{transform:rotate(360deg)}}

.cth-card{background:var(--panel);border:1px solid var(--border);border-radius:16px;box-shadow:0 1px 2px rgba(16,33,67,.04),0 12px 30px -20px rgba(16,33,67,.22);padding:22px 24px;margin-bottom:20px}
.cth-card-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.cth-h2{font-size:1.06rem;font-weight:720;color:var(--navy);margin:0;letter-spacing:-.01em}
.cth-muted{color:var(--muted);font-size:.82rem}
.cth-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;font-size:.68rem;font-weight:750;text-transform:uppercase;letter-spacing:.04em;border:1px solid}
.cth-nowrap{white-space:nowrap}
.cth-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem}

/* Hero */
.cth-hero{display:flex;gap:24px;align-items:center;border-radius:18px;padding:26px 28px;margin-bottom:20px;border:1px solid var(--border);background:var(--panel);box-shadow:0 1px 2px rgba(16,33,67,.04),0 16px 40px -26px rgba(16,33,67,.3)}
.cth-hero.cth-health-fail{background:linear-gradient(120deg,#fff,#fef4f4)}
.cth-hero.cth-health-attention{background:linear-gradient(120deg,#fff,#fffaf0)}
.cth-hero.cth-health-pass{background:linear-gradient(120deg,#fff,#f0fdf6)}
.cth-hero-medallion{flex:0 0 auto;width:96px;height:96px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--ring) 0 100%,#eef2f7 0);position:relative}
.cth-hero-medallion::after{content:"";position:absolute;inset:7px;border-radius:50%;background:#fff}
.cth-hero-icon{position:relative;z-index:1;width:40px;height:40px;display:grid;place-items:center}
.cth-hero-icon svg{width:40px;height:40px}
.cth-hero-body{flex:1;min-width:0}
.cth-hero-eyebrow{font-size:.74rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:3px}
.cth-hero-status{font-size:1.35rem;font-weight:800;letter-spacing:-.01em}
.cth-hero-headline{margin:4px 0 12px;font-size:1.02rem;color:#1f2d3d;line-height:1.5;max-width:64ch}
.cth-hero-meta{font-size:.76rem;color:var(--muted)}

/* Tooltip (ⓘ) */
.cth-tip{position:relative;display:inline-flex;align-items:center;vertical-align:middle;margin-left:5px;color:#9aa7bd;cursor:help;line-height:0}
.cth-tip svg{width:14px;height:14px;display:block}
.cth-tip:hover,.cth-tip:focus{color:var(--accent);outline:none}
.cth-tip-pop{position:absolute;bottom:calc(100% + 9px);left:50%;transform:translateX(-50%);width:max-content;max-width:250px;background:#0f1f38;color:#fff;font-size:.76rem;font-weight:500;line-height:1.45;letter-spacing:normal;text-transform:none;text-align:left;padding:9px 11px;border-radius:9px;box-shadow:0 10px 28px rgba(10,25,47,.32);z-index:60;display:none;white-space:normal;pointer-events:none}
.cth-tip-pop::after{content:"";position:absolute;top:100%;left:50%;transform:translateX(-50%);border:6px solid transparent;border-top-color:#0f1f38}
.cth-tip:hover .cth-tip-pop,.cth-tip:focus .cth-tip-pop,.cth-tip:focus-within .cth-tip-pop{display:block}

/* Tiles */
.cth-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:20px}
.cth-tile{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px 18px;box-shadow:var(--shadow-sm)}
.cth-tile-v{font-size:1.9rem;font-weight:780;color:var(--navy);line-height:1;letter-spacing:-.02em}
.cth-tile-l{font-size:.8rem;color:#334155;margin-top:6px;font-weight:560;display:flex;align-items:center}
.cth-tile-s{font-size:.7rem;color:var(--muted);margin-top:2px}
.cth-tile.cth-tone-danger{border-color:#fecaca;background:linear-gradient(180deg,#fff,#fef5f5)}
.cth-tile.cth-tone-danger .cth-tile-v{color:#b42318}
.cth-tile.cth-tone-ok .cth-tile-v{color:#15803d}

/* Diff */
.cth-diff-grid{display:flex;flex-direction:column;gap:12px}
.cth-diff-row{font-size:.9rem;color:#1f2d3d}
.cth-delta{font-weight:700}
.cth-diff-col{background:#f8fafc;border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.cth-diff-h{font-weight:700;font-size:.82rem;color:var(--navy);margin-bottom:6px}
.cth-diff-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px}
.cth-diff-list li{font-size:.85rem;display:flex;align-items:flex-start;gap:6px;line-height:1.4}
.cth-diff-list svg{width:15px;height:15px;flex:0 0 auto;margin-top:1px}
.cth-diff-good{color:#15803d}.cth-diff-good strong{color:#0f172a}
.cth-diff-bad{color:#b42318}.cth-diff-bad strong{color:#0f172a}
.cth-diff-none{color:var(--muted);font-size:.88rem}

/* Phase compare */
.cth-phase-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.cth-phase{border:1px solid var(--border);border-radius:14px;padding:16px;background:#fff;border-top:3px solid #cbd5e1}
.cth-phase.cth-health-fail{border-top-color:#ef4444}
.cth-phase.cth-health-attention{border-top-color:#f59e0b}
.cth-phase.cth-health-pass{border-top-color:#22c55e}
.cth-phase-top{display:flex;gap:10px;align-items:flex-start;margin-bottom:8px}
.cth-phase-dot{width:11px;height:11px;border-radius:50%;flex:0 0 auto;margin-top:4px}
.cth-phase-label{font-weight:720;color:var(--navy);font-size:.92rem}
.cth-phase-status{font-size:.82rem;font-weight:640;margin-top:1px}
.cth-phase-desc{font-size:.78rem;color:var(--muted);margin:0 0 10px;line-height:1.4}
.cth-phase-counts{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;min-height:24px}
.cth-cnt{font-size:.7rem;font-weight:680;padding:2px 8px;border-radius:6px;border:1px solid}
.cth-phase-foot{font-size:.74rem;color:var(--muted);border-top:1px dashed var(--border);padding-top:8px}

/* Findings */
.cth-findings{display:flex;flex-direction:column;gap:12px}
.cth-finding{display:flex;background:var(--sevbg);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.cth-finding-side{width:4px;background:var(--sev);flex:0 0 auto}
.cth-finding-main{padding:13px 16px;flex:1;min-width:0}
.cth-finding-top{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:6px}
.cth-finding-vendor{font-weight:720;color:var(--navy);font-size:.9rem}
.cth-finding-phase{font-size:.72rem;color:var(--muted);margin-left:auto}
.cth-finding-label{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.82rem;color:#334155;margin-bottom:5px;word-break:break-all}
.cth-finding-why{margin:0 0 5px;font-size:.86rem;color:#1f2d3d;line-height:1.5}
.cth-finding-rec{margin:0;font-size:.82rem;color:#475569;line-height:1.45}
.cth-finding-rec strong{color:var(--navy)}

/* Evidence pills */
.cth-ev{display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;font-size:.7rem;font-weight:640;background:#eef2f7;color:#475569;border:1px solid var(--border)}
.cth-ev-id{background:#fef2f2;color:#b42318;border-color:#fecaca}
.cth-ev-ping{background:#ecfdf3;color:#15803d;border-color:#bbf7d0}

/* Chips */
.cth-chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.cth-chip{padding:5px 12px;border-radius:999px;border:1px solid var(--border);background:#fff;color:#475569;font-size:.78rem;font-weight:600;cursor:pointer;font-family:inherit}
.cth-chip:hover{border-color:#cdd9e8;background:#f4f8fd}
.cth-chip-on{background:var(--navy);color:#fff;border-color:var(--navy)}
.cth-chip-n{opacity:.7;font-weight:700;margin-left:2px}

/* Tables */
.cth-table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:12px}
.cth-table{width:100%;border-collapse:collapse;font-size:.85rem;min-width:640px}
.cth-table th{text-align:left;padding:10px 14px;font-size:.68rem;font-weight:720;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);background:#f8fafc;border-bottom:1px solid var(--border);white-space:nowrap}
.cth-table td{padding:11px 14px;border-bottom:1px solid #eef2f7;vertical-align:top}
.cth-table tbody tr:last-child td{border-bottom:none}
.cth-table-sm{font-size:.82rem}
.cth-table-sm td{padding:9px 12px}
.cth-vendor-name{font-weight:680;color:var(--navy)}
.cth-vendor-purpose{font-size:.75rem;color:var(--muted);margin-top:2px;max-width:38ch}
.cth-ev-cell{display:flex;flex-wrap:wrap;gap:4px;max-width:220px}
.cth-idflag{display:inline-flex;padding:2px 8px;border-radius:6px;background:#fef2f2;color:#b42318;font-size:.7rem;font-weight:680;border:1px solid #fecaca}
.cth-cat-tag{display:inline-flex;padding:2px 9px;border-radius:999px;font-size:.72rem;font-weight:660;background:#eef2f7;color:#475569}
.cth-cat-advertising{background:#f5f3ff;color:#7c3aed}
.cth-cat-analytics{background:#eff6ff;color:#0b5cab}
.cth-cat-social{background:#fff1f2;color:#be185d}
.cth-cat-essential{background:#ecfdf3;color:#15803d}
.cth-cat-consent{background:#f0fdfa;color:#0f766e}
.cth-cat-functional{background:#fffbeb;color:#b45309}
.cth-row-viol{background:#fef7f7}
.cth-kind{display:inline-flex;padding:1px 7px;border-radius:5px;background:#eef2f7;color:#475569;font-size:.7rem;font-weight:640;text-transform:capitalize}
.cth-iddot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#b42318;margin-left:5px;vertical-align:middle}
.cth-why{max-width:40ch;color:#475569;font-size:.8rem}

/* Pages accordion */
.cth-pages{display:flex;flex-direction:column;gap:10px}
.cth-page{border:1px solid var(--border);border-radius:12px;overflow:hidden}
.cth-page-head{display:flex;align-items:center;gap:12px;width:100%;padding:13px 16px;background:#fff;border:none;cursor:pointer;font-family:inherit;text-align:left}
.cth-page-head:hover{background:#f8fafc}
.cth-page-dot{width:10px;height:10px;border-radius:50%;flex:0 0 auto}
.cth-page-url{font-weight:680;color:var(--navy);font-size:.9rem;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cth-page-path{color:var(--muted);font-weight:500}
.cth-page-status{font-size:.8rem;font-weight:640;white-space:nowrap}
.cth-page-chevron{color:var(--muted);display:grid;place-items:center}
.cth-page-chevron svg{width:18px;height:18px;transition:transform .2s}
.cth-page-head[aria-expanded=true] .cth-page-chevron svg{transform:rotate(180deg)}
.cth-page-body{display:none;padding:12px 16px 16px;border-top:1px solid var(--border);background:#fcfdfe}
.cth-ptabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.cth-ptab{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:#fff;color:#475569;font-size:.8rem;font-weight:600;cursor:pointer;font-family:inherit}
.cth-ptab.cth-on{background:var(--navy);color:#fff;border-color:var(--navy)}
.cth-ptab.cth-on .cth-phase-dot{box-shadow:0 0 0 2px rgba(255,255,255,.4)}

/* Notes / states */
.cth-note-warn{display:flex;gap:9px;align-items:flex-start;padding:10px 13px;border-radius:10px;background:#fffbeb;border:1px solid #fde68a;color:#92400e;font-size:.83rem;margin-bottom:12px}
.cth-note-warn svg{width:17px;height:17px;flex:0 0 auto;margin-top:1px;stroke:#d97706}
.cth-empty-inline{display:flex;gap:12px;align-items:center;padding:16px;background:#ecfdf3;border:1px solid #bbf7d0;border-radius:12px;color:#166534;font-size:.9rem}
.cth-empty-inline svg{width:26px;height:26px;flex:0 0 auto;stroke:#16a34a}
.cth-empty-sm{background:#f8fafc;border-color:var(--border);color:var(--muted);padding:14px}
.cth-runbanner{display:flex;align-items:center;gap:11px;padding:13px 16px;border-radius:12px;background:#eff6ff;border:1px solid #dbeafe;color:#0b5cab;font-size:.88rem;margin-bottom:18px}
.cth-runbanner svg{width:18px;height:18px}
.cth-errbanner{display:flex;gap:11px;align-items:flex-start;padding:13px 16px;border-radius:12px;background:#fef2f2;border:1px solid #fecaca;color:#b42318;font-size:.88rem;margin-bottom:18px}
.cth-errbanner svg{width:20px;height:20px;flex:0 0 auto;stroke:#dc2626}

/* Setup */
.cth-setup{text-align:center;padding:36px 28px}
.cth-setup-icon{width:56px;height:56px;margin:0 auto 14px;border-radius:16px;background:var(--accent);color:#fff;display:grid;place-items:center}
.cth-setup-icon svg{width:30px;height:30px}
.cth-setup-lead{max-width:56ch;margin:6px auto 20px;color:#475569;line-height:1.55}
.cth-setup-form{max-width:520px;margin:0 auto;text-align:left}
.cth-setup-actions{margin-top:14px;text-align:center}
.cth-field{display:flex;flex-direction:column;gap:6px;font-size:.85rem;font-weight:600;color:var(--navy);margin-bottom:14px}
.cth-field textarea,.cth-field select,.cth-field input{font-family:inherit;font-size:.88rem;padding:10px 12px;border:1px solid var(--border);border-radius:10px;font-weight:400;color:#0f172a;background:#fff}
.cth-field textarea{resize:vertical}
.cth-field legend{padding:0;margin-bottom:8px}
.cth-checks{display:flex;flex-wrap:wrap;gap:10px}
.cth-check{display:flex;align-items:center;gap:8px;font-size:.85rem;font-weight:500;color:#334155;cursor:pointer;margin-bottom:8px}
.cth-check-inline{margin-bottom:0}
.cth-check input{width:16px;height:16px;accent-color:var(--accent)}
.cth-field-row{display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap}
.cth-form-msg{font-size:.82rem;margin-top:8px;min-height:18px}
.cth-form-msg.cth-ok{color:#15803d}.cth-form-msg.cth-bad{color:#b42318}

/* Expectations */
.cth-exp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:0 0 8px}
.cth-exp-item{background:#f8fafc;border:1px solid var(--border);border-radius:10px;padding:11px 13px}
.cth-exp-item dt{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:700;margin:0 0 3px}
.cth-exp-item dd{margin:0;font-size:.86rem;color:var(--navy);font-weight:600}
.cth-method{font-size:.82rem;color:var(--muted);line-height:1.55;margin:10px 0 0;max-width:80ch}
.cth-method strong{color:#334155}

/* History */
.cth-hdots{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.cth-hdot{width:16px;height:16px;border-radius:5px;display:inline-block;cursor:default}

/* Technical detail disclosure */
.cth-adv{border:1px solid var(--border);border-radius:16px;background:var(--panel);box-shadow:0 1px 2px rgba(16,33,67,.04);margin-bottom:20px;overflow:hidden}
.cth-adv[open]{box-shadow:0 1px 2px rgba(16,33,67,.04),0 12px 30px -20px rgba(16,33,67,.22)}
.cth-adv-summary{display:flex;align-items:center;gap:12px;padding:18px 22px;cursor:pointer;list-style:none;user-select:none}
.cth-adv-summary::-webkit-details-marker{display:none}
.cth-adv-summary:hover{background:#f8fafc}
.cth-adv-summary:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.cth-adv-title{font-size:1.02rem;font-weight:720;color:var(--navy);letter-spacing:-.01em}
.cth-adv-hint{font-size:.82rem;color:var(--muted)}
.cth-adv-chevron{margin-left:auto;color:var(--muted);display:grid;place-items:center}
.cth-adv-chevron svg{width:20px;height:20px;transition:transform .2s}
.cth-adv[open] .cth-adv-chevron svg{transform:rotate(180deg)}
.cth-adv-body{padding:2px 22px 20px}
.cth-adv-body .cth-card{box-shadow:none;border-radius:12px}
.cth-adv-body .cth-card:last-child{margin-bottom:0}

/* Drawer */
.cth-drawer{position:fixed;inset:0;z-index:1200}
.cth-drawer-backdrop{position:absolute;inset:0;background:rgba(10,25,47,.45)}
.cth-drawer-panel{position:absolute;top:0;right:0;bottom:0;width:min(460px,92vw);background:#fff;box-shadow:-8px 0 40px rgba(10,25,47,.25);padding:22px 24px;overflow-y:auto;animation:cth-slide .2s ease}
@keyframes cth-slide{from{transform:translateX(24px);opacity:.6}to{transform:none;opacity:1}}
.cth-drawer-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.cth-drawer-x{border:none;background:#f1f5f9;border-radius:8px;width:34px;height:34px;display:grid;place-items:center;cursor:pointer;color:#475569}
.cth-drawer-x svg{width:17px;height:17px}
.cth-drawer-form .cth-field,.cth-drawer-form fieldset{margin-bottom:16px}
.cth-drawer-actions{display:flex;gap:10px;margin-top:6px}
.cth-drawer-form fieldset{border:1px solid var(--border);border-radius:10px;padding:12px 14px}

@media (max-width:820px){
  .cth-phase-grid{grid-template-columns:1fr}
  .cth-hero{flex-direction:column;text-align:center}
  .cth-hero-headline{margin-left:auto;margin-right:auto}
  .cth-finding-phase{margin-left:0}
  .cth-adv-hint{display:none}
}
"""
