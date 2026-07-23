"""In-dashboard UI for the Accessibility (ADA / WCAG) scoping audit.

Server-renders an on-demand axe-core audit: an admin edits the page list (seeded
from the client's Consent-scan pages, if configured), clicks **Run audit**, and
the scan runs synchronously and re-renders with a scoping report — severity
summary, highest-leverage rules, per-page breakdown, size band, and a coarse
effort estimate. It reuses the shared client shell and scopes all styles under an
``a11y-`` prefix so nothing leaks into the rest of the dashboard.

The scan itself is `a11y_scanner.scan_pages`; the roll-up is `a11y_scanner.aggregate`
— the same code the `scripts/a11y_audit.py` CLI uses, so the numbers match.
"""

from __future__ import annotations

import html
from typing import Any

from dashboard.renderers.base_layout import render_client_shell_page

_esc = html.escape

_IMPACT_META: dict[str, dict[str, str]] = {
    "critical": {"label": "Critical", "fg": "#b42318", "bg": "#fef2f2", "bd": "#fecaca"},
    "serious":  {"label": "Serious",  "fg": "#c2410c", "bg": "#fff7ed", "bd": "#fed7aa"},
    "moderate": {"label": "Moderate", "fg": "#b45309", "bg": "#fffbeb", "bd": "#fde68a"},
    "minor":    {"label": "Minor",    "fg": "#475569", "bg": "#f1f5f9", "bd": "#e2e8f0"},
}
_BAND_STATE = {"Clean": "pass", "Small": "pass", "Medium": "attention", "Large": "fail"}

_EXTRA_CSS = """
.a11y-wrap { max-width: 1040px; }
.a11y-card { background:#fff; border:1px solid #e6eaf0; border-radius:16px; padding:22px 24px; margin-bottom:20px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
.a11y-card h2 { margin:0 0 4px; font-size:1.05rem; }
.a11y-card .a11y-sub { color:#64748b; font-size:.85rem; margin:0 0 16px; }
.a11y-note { background:#eff6ff; border:1px solid #dbeafe; color:#1e3a5f; border-radius:12px; padding:12px 14px; font-size:.83rem; line-height:1.5; margin:0 0 4px; }
.a11y-urls { width:100%; min-height:120px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.82rem; border:1px solid #cbd5e1; border-radius:10px; padding:10px 12px; resize:vertical; }
.a11y-form-row { display:flex; align-items:center; gap:14px; margin-top:12px; flex-wrap:wrap; }
.a11y-btn { background:#0b5cab; color:#fff; border:none; border-radius:10px; padding:10px 20px; font-weight:600; font-size:.9rem; cursor:pointer; }
.a11y-btn:disabled { opacity:.6; cursor:progress; }
.a11y-muted { color:#64748b; font-size:.8rem; }
.a11y-band { display:inline-flex; align-items:center; gap:7px; font-weight:700; font-size:.8rem; padding:4px 12px; border-radius:999px; border:1px solid var(--line,#e2e8f0); }
.a11y-band::before { content:''; width:8px; height:8px; border-radius:50%; }
.a11y-band[data-state=pass] { color:#15803d; background:#e9f7ef; border-color:#b8dfc8; } .a11y-band[data-state=pass]::before { background:#22c55e; }
.a11y-band[data-state=attention] { color:#8a5a00; background:#fdf6e6; border-color:#f0d9a6; } .a11y-band[data-state=attention]::before { background:#d99400; }
.a11y-band[data-state=fail] { color:#b42318; background:#fdecea; border-color:#f3c0bb; } .a11y-band[data-state=fail]::before { background:#ef4444; }
.a11y-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin:16px 0 4px; }
.a11y-stat { background:#f8fafc; border:1px solid #eef2f7; border-radius:12px; padding:12px 14px; }
.a11y-stat .n { font-size:1.5rem; font-weight:700; color:#0f172a; line-height:1.1; }
.a11y-stat .l { font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; color:#64748b; margin-top:4px; }
.a11y-table { width:100%; border-collapse:collapse; font-size:.83rem; margin-top:6px; }
.a11y-table th, .a11y-table td { text-align:left; padding:8px 10px; border-bottom:1px solid #eef2f7; vertical-align:top; }
.a11y-table th { font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; color:#64748b; }
.a11y-table td.num, .a11y-table th.num { text-align:right; font-variant-numeric:tabular-nums; }
.a11y-pill { display:inline-block; font-size:.7rem; font-weight:700; padding:2px 8px; border-radius:999px; }
.a11y-rule-id { font-family:ui-monospace,Menlo,monospace; font-size:.78rem; }
.a11y-warn { background:#fffbeb; border:1px solid #fde68a; color:#8a5a00; border-radius:10px; padding:10px 12px; font-size:.8rem; margin-top:12px; }
.a11y-empty { color:#64748b; font-size:.9rem; padding:8px 0; }
"""


def _impact_pill(impact: str) -> str:
    m = _IMPACT_META.get(impact, _IMPACT_META["minor"])
    return (f'<span class="a11y-pill" style="color:{m["fg"]};background:{m["bg"]};'
            f'border:1px solid {m["bd"]}">{m["label"]}</span>')


def _results_html(scan: dict[str, Any], agg: dict[str, Any]) -> str:
    band = "Clean" if agg["total_violations"] == 0 else _band_word(agg)
    state = _BAND_STATE.get(band, "attention")
    t = agg["totals_by_impact"]
    nodes = agg["nodes_by_impact"]

    stats = f"""
    <div class="a11y-stats">
      <div class="a11y-stat"><div class="n">{agg['total_violations']}</div><div class="l">Rule failures</div></div>
      <div class="a11y-stat"><div class="n">{agg['total_nodes']}</div><div class="l">Affected elements</div></div>
      <div class="a11y-stat"><div class="n">{agg['incomplete_total']}</div><div class="l">Needs manual review</div></div>
      <div class="a11y-stat"><div class="n">~{agg['est_dev_hours']}h</div><div class="l">Est. dev effort</div></div>
      <div class="a11y-stat"><div class="n">{agg['pages_scanned']}/{agg['pages_requested']}</div><div class="l">Pages scanned</div></div>
    </div>"""

    sev_rows = "".join(
        f"<tr><td>{_impact_pill(i)}</td><td class='num'>{t.get(i, 0)}</td>"
        f"<td class='num'>{nodes.get(i, 0)}</td></tr>"
        for i in ("critical", "serious", "moderate", "minor")
    )

    rule_rows = ""
    for r in agg["rules"][:25]:
        help_txt = _esc(r.get("help") or r["id"])
        help_cell = (f'<a href="{_esc(r["helpUrl"])}" target="_blank" rel="noopener">{help_txt}</a>'
                     if r.get("helpUrl") else help_txt)
        rule_rows += (f"<tr><td class='a11y-rule-id'>{_esc(r['id'])}</td><td>{_impact_pill(r['impact'])}</td>"
                      f"<td class='num'>{r['page_count']}</td><td class='num'>{r['nodes']}</td>"
                      f"<td>{help_cell}</td></tr>")
    rules_block = (f"""
    <div class="a11y-card">
      <h2>Highest-leverage fixes</h2>
      <p class="a11y-sub">Rules that fail on the most elements — start here.</p>
      <table class="a11y-table">
        <thead><tr><th>Rule</th><th>Impact</th><th class="num">Pages</th><th class="num">Elements</th><th>Guidance</th></tr></thead>
        <tbody>{rule_rows}</tbody>
      </table>
    </div>""" if rule_rows else "")

    page_rows = ""
    for pg in scan.get("pages") or []:
        bi = pg.get("by_impact") or {}
        label = _esc(pg.get("title") or pg.get("final_url") or pg.get("url") or "")
        warn = " ⚠️" if (pg.get("errors") and not pg.get("scanned")) else ""
        page_rows += (f"<tr><td>{label}{warn}<br><span class='a11y-muted'>{_esc(pg.get('url',''))}</span></td>"
                      f"<td class='num'>{bi.get('critical',0)}</td><td class='num'>{bi.get('serious',0)}</td>"
                      f"<td class='num'>{bi.get('moderate',0)}</td><td class='num'>{bi.get('minor',0)}</td>"
                      f"<td class='num'>{pg.get('incomplete_count',0)}</td></tr>")

    warnings = ""
    prob = [p for p in (scan.get("pages") or []) if p.get("errors")]
    if prob:
        items = "".join(f"<li><code>{_esc(p.get('url',''))}</code>: {_esc('; '.join(p.get('errors') or []))}</li>"
                        for p in prob)
        warnings += f'<div class="a11y-warn"><strong>Scan warnings</strong><ul>{items}</ul></div>'
    if scan.get("rejected"):
        items = "".join(f"<li><code>{_esc(r['url'])}</code>: {_esc(r['reason'])}</li>" for r in scan["rejected"])
        warnings += f'<div class="a11y-warn"><strong>Rejected URLs</strong><ul>{items}</ul></div>'

    return f"""
    <div class="a11y-card">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <h2 style="margin:0">Results</h2>
        <span class="a11y-band" data-state="{state}">{_esc(band)}</span>
        <span class="a11y-muted">axe-core {_esc(scan.get('axe_version') or '?')} · {_esc(', '.join(scan.get('tags') or []))}</span>
      </div>
      {stats}
      <table class="a11y-table" style="margin-top:14px">
        <thead><tr><th>Severity</th><th class="num">Rule failures</th><th class="num">Affected elements</th></tr></thead>
        <tbody>{sev_rows}</tbody>
      </table>
    </div>
    {rules_block}
    <div class="a11y-card">
      <h2>Per-page breakdown</h2>
      <table class="a11y-table">
        <thead><tr><th>Page</th><th class="num">Crit</th><th class="num">Serious</th><th class="num">Moderate</th><th class="num">Minor</th><th class="num">Review</th></tr></thead>
        <tbody>{page_rows}</tbody>
      </table>
      {warnings}
    </div>"""


def _band_word(agg: dict[str, Any]) -> str:
    # Local import avoids a hard module dependency at import time.
    import a11y_scanner
    return a11y_scanner.size_band(agg)


def render_accessibility_page(
    *,
    client_slug: str,
    label: str,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    default_urls: list[str] | None = None,
    submitted_urls: list[str] | None = None,
    scan: dict[str, Any] | None = None,
    agg: dict[str, Any] | None = None,
    scan_error: str | None = None,
) -> str:
    urls_text = "\n".join(submitted_urls if submitted_urls is not None else (default_urls or []))
    post_action = f"/dashboard/{_esc(client_slug)}/accessibility/scan"
    key_field = (f'<input type="hidden" name="key" value="{_esc(access_key)}">'
                 if access_key and not use_session else "")
    seed_note = ("Seeded from this client's Consent-scan pages."
                 if (default_urls and submitted_urls is None) else
                 "Add one page per distinct template — home, a service page, a form, a blog post.")

    error_html = f'<div class="a11y-warn">{_esc(scan_error)}</div>' if scan_error else ""
    if scan is not None and agg is not None and not scan_error:
        results_html = _results_html(scan, agg)
    else:
        results_html = ('<div class="a11y-card"><h2>Results</h2>'
                        '<p class="a11y-empty">Run an audit to see the scoping report here.</p></div>'
                        if scan is None else "")

    content = f"""
    <div class="a11y-wrap">
      <p class="a11y-note">
        <strong>Automated ADA / WCAG scan.</strong> Loads each page in a real browser and runs
        Deque's axe-core rules engine (WCAG 2.1 A + AA). Every finding is a real failure, so it's a
        safe floor for scoping — but axe catches roughly a third to a half of WCAG issues; keyboard
        and screen-reader testing still need a human.
      </p>
      <div class="a11y-card">
        <h2>Pages to audit</h2>
        <p class="a11y-sub">{_esc(seed_note)} One URL per line.</p>
        <form method="post" action="{post_action}" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Scanning… this can take a moment';">
          {key_field}
          <textarea class="a11y-urls" name="urls" spellcheck="false" placeholder="https://www.example.com/">{_esc(urls_text)}</textarea>
          <div class="a11y-form-row">
            <button class="a11y-btn" type="submit">Run audit</button>
            <span class="a11y-muted">Scans synchronously — allow ~5–15s per page.</span>
          </div>
        </form>
      </div>
      {error_html}
      {results_html}
    </div>"""

    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="settings",
        page_title=f"{label} — Accessibility",
        page_subtitle="ADA / WCAG scoping audit",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
    )
