"""Focused settings page for a BigQuery-mart client.

Only the controls needed to set up the account and push/pull data from
BigQuery: account mapping (account IDs) and data controls (refresh / backfill /
mart freshness). The BigQuery destination itself is owned by the Connectors
page, which sets the project + datasets and can verify access. Reuses the test
page's sidebar + helpers so it stays import-light and previewable offline via
.preview/gen.py.
"""

from __future__ import annotations

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    admin_top_tabs_html,
    dashboard_topbar_js,
    favicon_head_html,
    dashboard_sidebar_view_nav_html,
    render_sidebar,
    site_footer_html,
)
from dashboard.renderers.bigquery_dashboard_renderer import _api_url
from dashboard.renderers import budget_tracker
from dashboard.services import kpi_registry
from dashboard.services import metric_goals as metric_goals_service
from dashboard.utils.formatting import esc as _esc
from dashboard.utils.urls import accessibility_page_url as _accessibility_page_url
from dashboard.utils.urls import consent_page_url as _consent_page_url


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()


def render_bigquery_settings_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    account_ids: dict | None = None,
    flash: str | None = None,
    flash_error: str | None = None,
    client_slug: str = "nixon-bq-test",
    api_client_key: str = "nixon",
    label: str = "Nixon Medical",
    show_linkedin_backfill: bool = True,
    explorer_filters: str = "",
    monthly_budget: float | None = None,
    budget_tracker_enabled: bool = True,
    consent_sidebar_enabled: bool = False,
    primary_kpi: dict | None = None,
    segment_filter_profile: str | None = None,
    metric_goals: dict | None = None,
) -> str:
    """Settings page for any BigQuery-mart (Nixon-style) client.

    Defaults preserve Nixon's exact page. client_slug drives dashboard-facing
    URLs + the connectors nav; api_client_key drives the /api/clients/* refresh
    + health endpoints (see render_bigquery_dashboard_page for why these are
    separate for Nixon itself). show_linkedin_backfill hides the LinkedIn-only
    onboarding button for generic clients, whose full refresh already syncs
    every connected platform.
    """
    account_ids = account_ids or {}

    admin_class = "is-admin" if session_is_admin else ""

    # Same canonical section nav as the dashboard (as links back to it), so the
    # sidebar is identical across the dashboard, settings, connectors, and files.
    view_nav_html = dashboard_sidebar_view_nav_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
        as_tabs=False,
    )

    sidebar_html = render_sidebar(
        client_slug=client_slug,
        label=label,
        active_nav="settings",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=_docs_enabled(),
        # Connector-driven dashboards always expose the connectors nav so a
        # brand-new client can reach the setup wizards before any connector
        # exists (otherwise the link only appears once one is connected).
        show_connectors=True,
        view_nav_html=view_nav_html,
    )

    # The Insights page is the "Insights" tab of the Admin surface, so admins get
    # the same top tab strip the Connectors/Consent/tool pages carry.
    admin_tabs_html = (
        admin_top_tabs_html(
            client_slug=client_slug, active_tab="insights",
            access_key=access_key, use_session=use_session,
        )
        if session_is_admin
        else ""
    )

    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{_esc(flash)}</div>'
    elif flash_error:
        flash_html = f'<div class="flash err">{_esc(flash_error)}</div>'

    # Budget tracker — the shared module, always shown here (admins get the goal
    # editor). Admins also get a toggle for whether it appears on the client-facing
    # Campaign Explorer (server-persisted per client).
    budget_css = budget_tracker.css()
    budget_scripts = budget_tracker.scripts(
        api_client_key=api_client_key,
        access_key=access_key,
        monthly_budget=monthly_budget,
        can_edit=session_is_admin,
        client_slug=client_slug,
    )
    budget_visibility_url = _api_url(
        f"/dashboard/{client_slug}/budget-visibility", access_key=access_key
    )
    budget_toggle_checked = " checked" if budget_tracker_enabled else ""

    # "Show on Campaign Explorer" folded into the budget card's title as a
    # low-profile toggle (admin only), instead of its own section.
    budget_vis_control = "" if not session_is_admin else f"""<label class="hdr-toggle" title="Show the budget tracker on the client-facing Campaign Explorer">
              <span class="hdr-toggle-label">Show on Explorer</span>
              <span class="toggle-switch"><input type="checkbox" id="budgetVisibilityToggle"{budget_toggle_checked}><span class="toggle-track"></span></span>
              <span class="status" id="budgetVisibilityStatus"></span>
            </label>"""
    budget_module_html = budget_tracker.section_html(
        can_edit=session_is_admin, header_extra_html=budget_vis_control
    )

    # Consent & Tracking Health visibility (admin only). Off by default: most
    # clients don't need the scanner in their nav, so it's hidden unless an admin
    # turns it on here. The "Open Consent Health" link keeps the page reachable for
    # admins to configure / run scans even while it's hidden from the sidebar.
    consent_url = _consent_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    consent_visibility_url = _api_url(
        f"/dashboard/{client_slug}/consent-visibility", access_key=access_key
    )
    consent_toggle_checked = " checked" if consent_sidebar_enabled else ""
    consent_visibility_html = "" if not session_is_admin else f"""
    <section class="summary-card">
      <div class="sc-main">
        <div class="sc-head">
          <span class="sc-title">Consent health</span>
          <span class="consent-pill" id="consentPill" data-state="loading">Checking…</span>
        </div>
        <span class="sc-sub" id="consentSub">Scans this client's site for tracking that fires before consent.</span>
      </div>
      <div class="sc-actions">
        <label class="hdr-toggle" title="Show Consent Health in this client's sidebar">
          <span class="hdr-toggle-label">In sidebar</span>
          <span class="toggle-switch"><input type="checkbox" id="consentSidebarToggle"{consent_toggle_checked}><span class="toggle-track"></span></span>
          <span class="status" id="consentSidebarStatus"></span>
        </label>
        <a class="sc-link" href="{_esc(consent_url)}">Open &rarr;</a>
      </div>
    </section>"""

    # Accessibility (ADA / WCAG) scoping audit — an on-demand axe-core scan of the
    # client's site. Admin-only, and a plain link (the scan runs on its own page),
    # mirroring the Consent card above.
    accessibility_url = _accessibility_page_url(
        client_slug=client_slug, access_key=access_key, use_session=use_session
    ) or "#"
    accessibility_card_html = "" if not session_is_admin else f"""
    <section class="summary-card">
      <div class="sc-main">
        <div class="sc-head">
          <span class="sc-title">Accessibility</span>
          <span class="consent-pill" id="a11yPill" data-state="loading">Checking…</span>
        </div>
        <span class="sc-sub" id="a11ySub">Run an axe-core scan of this client's site to scope ADA remediation work.</span>
      </div>
      <div class="sc-actions">
        <a class="sc-link" href="{_esc(accessibility_url)}">Open &rarr;</a>
      </div>
    </section>"""

    # Primary KPI (admin only): the headline metric shown for this client on the
    # HQ overview. Each client tracks a different KPI, so this is a small picker
    # (type + optional custom label + optional monthly goal) backed by the KPI
    # registry, saved per client.
    primary_kpi = primary_kpi or {}
    kpi_save_url = _api_url(f"/dashboard/{client_slug}/primary-kpi", access_key=access_key)
    cur_type = str(primary_kpi.get("type") or "")
    cur_label = str(primary_kpi.get("label") or "")
    cur_goal = primary_kpi.get("goal")
    cur_goal_val = "" if cur_goal in (None, "") else _esc(cur_goal)
    kpi_options = "".join(
        f'<option value="{_esc(val)}"{" selected" if val == cur_type else ""}>{_esc(text)}</option>'
        for val, text in kpi_registry.KPI_CHOICES
    )
    kpi_section_html = "" if not session_is_admin else f"""
    <section id="sec-kpi">
      <h2>Primary KPI</h2>
      <p class="hint">The headline metric shown for this client on the HQ overview. Google Ads KPIs come from the paid-media mart; MQLs come from HubSpot. Leave the goal blank to just show the value.</p>
      <form class="form-grid" id="kpiForm" autocomplete="off">
        <label for="kpiType">Metric
          <select id="kpiType" name="type">{kpi_options}</select>
        </label>
        <label for="kpiLabel">Label (optional)
          <input type="text" id="kpiLabel" name="label" maxlength="40" placeholder="Defaults to the metric name" value="{_esc(cur_label)}">
        </label>
        <label for="kpiGoal">Monthly goal (optional)
          <input type="number" id="kpiGoal" name="goal" min="0" step="any" placeholder="e.g. 50" value="{cur_goal_val}">
        </label>
        <div class="form-actions btn-row">
          <button type="submit" class="primary">Save KPI</button>
          <span class="status" id="kpiStatus"></span>
        </div>
      </form>
    </section>"""

    # Metric goals (admin only): a target per paid summary card, so the dashboard
    # can say whether a number landed where it was meant to instead of only which
    # way it moved. Cumulative metrics are entered as monthly totals and the
    # dashboard prorates them to whatever range is selected; rate metrics are the
    # rate itself. Blank clears that metric's target.
    stored_goals = metric_goals or {}
    goals_save_url = _api_url(f"/dashboard/{client_slug}/metric-goals", access_key=access_key)
    goal_field_rows = "".join(
        f"""<label for="goal-{_esc(m["key"])}">{_esc(m["label"])}
          <input type="number" id="goal-{_esc(m["key"])}" data-goal-key="{_esc(m["key"])}"
                 min="0" step="any" placeholder="{'Monthly total' if m["cumulative"] else 'Target rate'}"
                 value="{'' if stored_goals.get(m["key"]) in (None, "") else _esc(stored_goals.get(m["key"]))}">
          <span class="hint">{_esc(m["hint"])}</span>
        </label>"""
        for m in metric_goals_service.catalog()
    )
    goals_section_html = "" if not session_is_admin else f"""
    <section id="sec-metric-goals">
      <h2>Metric goals <span class="sc-pill">Admin preview</span></h2>
      <p class="hint">Targets for the Overview summary cards. Spend, impressions, clicks and conversions are <strong>monthly totals</strong> — the dashboard scales them to whatever date range is selected. CTR, CPC and CPA are the rate itself and are compared directly. Leave a field blank for no target. While this is in preview only admins see the result on the dashboard.</p>
      <form class="form-grid" id="goalsForm" autocomplete="off">
        {goal_field_rows}
        <div class="form-actions btn-row">
          <button type="submit" class="primary">Save goals</button>
          <span class="status" id="goalsStatus"></span>
        </div>
      </form>
    </section>"""

    # Segment filters — how this client's campaigns/pages are grouped in the
    # Campaign Explorer and Website Analytics filters. Config-driven (no client
    # name in code): 'business_lines' = keyword business-line rules, 'regions' =
    # geographic regions, or none.
    segment_save_url = _api_url(
        f"/dashboard/{client_slug}/segment-filter-profile", access_key=access_key
    )
    cur_profile = (segment_filter_profile or "").strip().lower()
    _segment_choices = (("", "None"), ("business_lines", "Business lines"), ("regions", "Regions"))
    segment_options = "".join(
        f'<option value="{_esc(val)}"{" selected" if val == cur_profile else ""}>{_esc(text)}</option>'
        for val, text in _segment_choices
    )
    segment_section_html = "" if not session_is_admin else f"""
    <section id="sec-segment">
      <h2>Segment filters</h2>
      <p class="hint">How this client's campaigns and pages are grouped in the Campaign Explorer and Website Analytics filters. Business lines use keyword rules; Regions use geographic rules. Choose None to hide segment filters.</p>
      <form class="form-grid" id="segmentForm" autocomplete="off">
        <label for="segmentProfile">Filter type
          <select id="segmentProfile" name="profile">{segment_options}</select>
        </label>
        <div class="form-actions btn-row">
          <button type="submit" class="primary">Save filters</button>
          <span class="status" id="segmentStatus"></span>
        </div>
      </form>
    </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — Insights</title>
  {favicon_head_html()}
  <!-- Charts: Chart.js, vendored locally (served from /static/vendor) -->
  <script src="/static/vendor/chart.umd.min.js"></script>
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    /* SIDEBAR_CSS (shared) covers .dash-sidebar* only — the flex-container
       rules that lay the sidebar and content side by side normally live in
       render_client_shell_page's own style block, which this standalone page
       doesn't use, so they're declared explicitly here instead. */
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1 1 auto; min-width: 0; }}
    {SIDEBAR_CSS}
    .debug-only {{ display:none; }} .is-admin .debug-only {{ display:block; }}
    main {{ max-width:1000px; margin:0 auto; padding:30px 28px 56px; }}
    .page-head {{ margin-bottom:24px; }}
    h1 {{ margin:0; color:var(--navy); font-size:1.5rem; font-weight:800; letter-spacing:-.01em; }}
    h2 {{ margin:0 0 4px; color:var(--navy); font-size:1.1rem; font-weight:750; }}
    h3.sub {{ margin:20px 0 4px; color:var(--navy); font-size:.92rem; font-weight:700; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    .hint {{ font-size:.82rem; color:var(--muted); margin:4px 0 12px; }}
    .hint code {{ background:#eef4fb; padding:1px 5px; border-radius:4px; }}
    .err-hint {{ color:var(--bad); }}
    /* Marks a section whose effect is still admin-only on the client dashboard,
       so nobody configures it expecting the client to see it yet. */
    .sc-pill {{ display:inline-block; vertical-align:middle; margin-left:8px; padding:2px 8px; border-radius:999px; background:#eef4fb; border:1px solid #cfe0f3; color:var(--accent); font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }}
    /* Per-field helper text inside a label in the goals grid — the label's own
       uppercase/bold treatment would otherwise shout it. */
    label > .hint {{ margin:0; font-size:.7rem; font-weight:500; text-transform:none; letter-spacing:0; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow); }}
    .flash {{ padding:11px 14px; border-radius:var(--radius-sm); margin-bottom:18px; font-size:.9rem; background:#e9f7ef; border:1px solid #b8dfc8; color:var(--ok); }}
    .flash.err {{ background:#fdecea; border-color:#f3c0bb; color:var(--bad); }}
    .kv-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; margin-top:14px; }}
    .kv-grid > div {{ display:flex; flex-direction:column; gap:3px; }}
    .kv-label {{ font-size:.66rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }}
    .kv-val {{ font-size:.9rem; color:var(--navy); font-weight:600; word-break:break-all; }}
    .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.82rem; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:14px; margin-top:14px; }}
    .stack {{ display:flex; flex-direction:column; gap:10px; align-items:flex-start; }}
    label {{ display:grid; gap:6px; color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; }}
    input {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:9px 12px; font:inherit; font-weight:500; text-transform:none; letter-spacing:0; background:#fff; color:#102033; }}
    input:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    select {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:9px 12px; font:inherit; font-weight:500; text-transform:none; letter-spacing:0; background:#fff; color:#102033; }}
    select:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    textarea {{ width:100%; border:1px solid var(--line); border-radius:var(--radius-sm); padding:10px 12px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.84rem; font-weight:500; text-transform:none; letter-spacing:0; background:#fff; color:#102033; resize:vertical; min-height:150px; }}
    textarea:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    .form-actions {{ grid-column:1 / -1; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:10px 16px; background:var(--accent); color:#fff; font-weight:700; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover:not(:disabled) {{ background:#1a62b8; }}
    button.primary:disabled {{ opacity:.5; cursor:default; }}
    button.ghost {{ background:#fff; color:var(--navy); border:1px solid var(--line); box-shadow:none; }}
    button.ghost:hover:not(:disabled) {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .btn-row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:6px; }}
    .status {{ color:var(--muted); font-size:.84rem; margin:0; }}
    .status.error {{ color:var(--bad); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line-soft); border-radius:var(--radius-sm); margin-top:8px; }}
    table {{ border-collapse:collapse; width:100%; font-size:.84rem; }}
    th,td {{ padding:9px 12px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    th {{ background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.68rem; letter-spacing:.04em; font-weight:800; }}
    th.left,td.left {{ text-align:left; }}
    .empty {{ color:var(--muted); padding:18px; text-align:center; }}
    .toggle-switch {{ position:relative; display:inline-block; width:42px; height:24px; flex-shrink:0; }}
    .toggle-switch input {{ opacity:0; width:0; height:0; position:absolute; }}
    .toggle-track {{ position:absolute; cursor:pointer; inset:0; background:#c5cdd9; border-radius:24px; transition:background .2s; }}
    .toggle-track:before {{ content:''; position:absolute; left:3px; top:3px; width:18px; height:18px; background:#fff; border-radius:50%; transition:transform .2s; box-shadow:0 1px 3px rgba(0,0,0,.2); }}
    .toggle-switch input:checked + .toggle-track {{ background:var(--accent); }}
    .toggle-switch input:checked + .toggle-track:before {{ transform:translateX(18px); }}
    .toggle-switch input:focus-visible + .toggle-track {{ outline:2px solid #bcd4f0; outline-offset:2px; }}
    /* Low-profile header toggle (folded into a card title) */
    .hdr-toggle {{ display:inline-flex; align-items:center; gap:8px; margin:0; padding:0; text-transform:none; letter-spacing:0; cursor:pointer; }}
    .hdr-toggle-label {{ font-size:.78rem; font-weight:650; color:var(--muted); }}
    .hdr-toggle .status {{ font-size:.74rem; }}
    /* Compact summary card (consent health) */
    .summary-card {{ display:flex; align-items:center; justify-content:space-between; gap:18px; flex-wrap:wrap; }}
    .sc-main {{ display:flex; flex-direction:column; gap:4px; min-width:0; }}
    .sc-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
    .sc-title {{ font-size:1.05rem; font-weight:750; color:var(--navy); }}
    .sc-sub {{ font-size:.8rem; color:var(--muted); }}
    .sc-actions {{ display:flex; align-items:center; gap:18px; flex-shrink:0; flex-wrap:wrap; }}
    .sc-link {{ font-size:.84rem; font-weight:700; color:var(--accent); text-decoration:none; white-space:nowrap; }}
    .sc-link:hover {{ text-decoration:underline; }}
    .consent-pill {{ display:inline-flex; align-items:center; gap:6px; font-size:.75rem; font-weight:700; padding:3px 10px; border-radius:999px; border:1px solid var(--line); background:#f7f9fc; color:var(--muted); }}
    .consent-pill::before {{ content:''; width:7px; height:7px; border-radius:50%; background:#c5cdd9; flex-shrink:0; }}
    .consent-pill[data-state="pass"] {{ color:var(--ok); border-color:#b8dfc8; background:#e9f7ef; }}
    .consent-pill[data-state="pass"]::before {{ background:var(--ok); }}
    .consent-pill[data-state="attention"] {{ color:#8a5a00; border-color:#f0d9a6; background:#fdf6e6; }}
    .consent-pill[data-state="attention"]::before {{ background:#d99400; }}
    .consent-pill[data-state="fail"] {{ color:var(--bad); border-color:#f3c0bb; background:#fdecea; }}
    .consent-pill[data-state="fail"]::before {{ background:var(--bad); }}
    @media (max-width:600px) {{
      main {{ padding:22px 14px 44px; }}
      h1 {{ font-size:1.3rem; }}
      section {{ padding:16px 15px; }}
      .summary-card {{ gap:12px; }}
      .sc-actions {{ width:100%; justify-content:space-between; }}
    }}
    {budget_css}
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    {sidebar_html}
    <div class="dash-main">
  <main>
    {admin_tabs_html}
    <div class="page-head">
      <h1>{_esc(label)} — Insights</h1>
      <p class="debug-only">Budget pacing and consent health.</p>
    </div>
    {flash_html}

    {consent_visibility_html}
    {accessibility_card_html}
    {kpi_section_html}
    {goals_section_html}
    {segment_section_html}
    {budget_module_html}
  </main>
  {site_footer_html()}
    </div>
  </div>
  <script>
    const CONSENT_STATUS_API = "{_api_url(f'/dashboard/{client_slug}/consent/status', access_key=access_key)}";
    function setStatus(id, text, isErr) {{ const el = document.getElementById(id); if (!el) return; el.textContent = text; el.className = isErr ? 'status error' : 'status'; }}

    // ---- Consent summary card: pull the latest scan health into the pill ----
    (function(){{
      const pill = document.getElementById('consentPill'); if (!pill) return;
      const sub = document.getElementById('consentSub');
      const LABELS = {{ pass:'Healthy', attention:'Needs attention', fail:'Critical issues', unknown:'Not yet scanned' }};
      fetch(CONSENT_STATUS_API, {{ credentials:'same-origin' }})
        .then(r => r.json())
        .then(b => {{
          const st = b && b.status;
          if (!st || st === 'none') {{ pill.dataset.state = 'unknown'; pill.textContent = 'Not yet scanned'; if (sub) sub.textContent = 'No scan has run for this client yet.'; return; }}
          if (st === 'running' || st === 'queued') {{ pill.dataset.state = 'loading'; pill.textContent = 'Scanning…'; return; }}
          if (st === 'error') {{ pill.dataset.state = 'fail'; pill.textContent = 'Scan error'; if (sub && b.error_message) sub.textContent = b.error_message; return; }}
          const h = b.health || 'unknown';
          pill.dataset.state = h;
          pill.textContent = LABELS[h] || h;
          const vc = Number(b.violation_count || 0);
          if (sub) sub.textContent = vc > 0 ? (vc + ' issue' + (vc === 1 ? '' : 's') + ' in the latest scan.') : 'No issues in the latest scan.';
        }})
        .catch(() => {{ pill.dataset.state = 'unknown'; pill.textContent = 'Status unavailable'; }});
    }})();

    // ---- Accessibility summary card: pull the latest audit into the pill ----
    (function(){{
      const pill = document.getElementById('a11yPill'); if (!pill) return;
      const sub = document.getElementById('a11ySub');
      // Size band -> pill state: Clean/Small look healthy, Medium needs attention, Large is critical.
      const STATE = {{ Clean:'pass', Small:'pass', Medium:'attention', Large:'fail' }};
      fetch("{_api_url(f'/dashboard/{client_slug}/accessibility/status', access_key=access_key)}", {{ credentials:'same-origin' }})
        .then(r => r.json())
        .then(b => {{
          const st = b && b.status;
          if (!st || st === 'none') {{ pill.dataset.state = 'unknown'; pill.textContent = 'Not yet scanned'; return; }}
          const band = b.band || 'unknown';
          pill.dataset.state = STATE[band] || 'attention';
          pill.textContent = band;
          if (sub) {{
            const nodes = Number(b.total_nodes || 0), aa = Number(b.aa_issues || 0);
            sub.textContent = nodes > 0
              ? (aa + ' issue' + (aa === 1 ? '' : 's') + ' to reach WCAG AA' + (b.when ? ' · ' + b.when : ''))
              : 'No issues in the latest audit.';
          }}
        }})
        .catch(() => {{ pill.dataset.state = 'unknown'; pill.textContent = 'Status unavailable'; }});
    }})();

    // ---- Budget tracker: show-on-explorer toggle ----
    (function(){{
      const t = document.getElementById('budgetVisibilityToggle');
      if (!t) return;
      t.addEventListener('change', async () => {{
        t.disabled = true;
        setStatus('budgetVisibilityStatus', 'Saving…');
        try {{
          const r = await fetch("{budget_visibility_url}", {{
            method:'POST', credentials:'same-origin',
            headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
            body: new URLSearchParams({{ show: t.checked ? '1' : '0' }}),
          }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
          t.closest('label').title = t.checked ? 'On' : 'Off';
          setStatus('budgetVisibilityStatus', t.checked ? 'Shown on the Campaign Explorer.' : 'Hidden from the Campaign Explorer.');
        }} catch (err) {{
          t.checked = !t.checked;  // revert on failure
          setStatus('budgetVisibilityStatus', 'Save failed: ' + (err.message || err), true);
        }} finally {{ t.disabled = false; }}
      }});
    }})();
    // ---- Consent health: show-on-client-sidebar toggle ----
    (function(){{
      const t = document.getElementById('consentSidebarToggle');
      if (!t) return;
      t.addEventListener('change', async () => {{
        t.disabled = true;
        setStatus('consentSidebarStatus', 'Saving…');
        try {{
          const r = await fetch("{consent_visibility_url}", {{
            method:'POST', credentials:'same-origin',
            headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
            body: new URLSearchParams({{ show: t.checked ? '1' : '0' }}),
          }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
          t.closest('label').title = t.checked ? 'On' : 'Off';
          setStatus('consentSidebarStatus', t.checked ? 'Shown in the client sidebar. Reload to see it.' : 'Hidden from the client sidebar.');
        }} catch (err) {{
          t.checked = !t.checked;  // revert on failure
          setStatus('consentSidebarStatus', 'Save failed: ' + (err.message || err), true);
        }} finally {{ t.disabled = false; }}
      }});
    }})();
    // ---- Primary KPI: save the client's headline metric ----
    (function(){{
      const KPI_SAVE_URL = "{kpi_save_url}";
      const form = document.getElementById('kpiForm'); if (!form) return;
      form.addEventListener('submit', async (e) => {{
        e.preventDefault();
        const btn = form.querySelector('button[type=submit]');
        const type = document.getElementById('kpiType').value;
        const label = document.getElementById('kpiLabel').value.trim();
        const goal = document.getElementById('kpiGoal').value.trim();
        btn.disabled = true;
        setStatus('kpiStatus', 'Saving…');
        try {{
          const r = await fetch(KPI_SAVE_URL, {{
            method:'POST', credentials:'same-origin',
            headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
            body: new URLSearchParams({{ type, label, goal }}),
          }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
          setStatus('kpiStatus', type ? 'Saved ✓' : 'KPI cleared ✓');
        }} catch (err) {{
          setStatus('kpiStatus', 'Save failed: ' + (err.message || err), true);
        }} finally {{ btn.disabled = false; }}
      }});
    }})();
    // ---- Metric goals: save every card target in one post ----
    // Saving is a replace, not a merge: the form always submits the full set, so
    // clearing a field clears that metric's target rather than leaving a stale
    // one behind. Blank inputs are simply omitted and the server drops them.
    (function(){{
      const GOALS_SAVE_URL = "{goals_save_url}";
      const form = document.getElementById('goalsForm'); if (!form) return;
      form.addEventListener('submit', async (e) => {{
        e.preventDefault();
        const btn = form.querySelector('button[type=submit]');
        const goals = {{}};
        for (const input of form.querySelectorAll('[data-goal-key]')) {{
          const v = input.value.trim();
          if (v !== '') goals[input.dataset.goalKey] = v;
        }}
        btn.disabled = true;
        setStatus('goalsStatus', 'Saving…');
        try {{
          const r = await fetch(GOALS_SAVE_URL, {{
            method:'POST', credentials:'same-origin',
            headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
            body: new URLSearchParams({{ goals: JSON.stringify(goals) }}),
          }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
          const n = Object.keys(body.goals || {{}}).length;
          setStatus('goalsStatus', n ? `Saved ✓ ${{n}} target${{n === 1 ? '' : 's'}} active` : 'Cleared ✓ no targets set');
        }} catch (err) {{
          setStatus('goalsStatus', 'Save failed: ' + (err.message || err), true);
        }} finally {{ btn.disabled = false; }}
      }});
    }})();
    // ---- Segment filters: save the client's filter profile ----
    (function(){{
      const SEGMENT_SAVE_URL = "{segment_save_url}";
      const form = document.getElementById('segmentForm'); if (!form) return;
      form.addEventListener('submit', async (e) => {{
        e.preventDefault();
        const btn = form.querySelector('button[type=submit]');
        const profile = document.getElementById('segmentProfile').value;
        btn.disabled = true;
        setStatus('segmentStatus', 'Saving…');
        try {{
          const r = await fetch(SEGMENT_SAVE_URL, {{
            method:'POST', credentials:'same-origin',
            headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
            body: new URLSearchParams({{ profile }}),
          }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok || !body.ok) throw new Error(body.error || ('HTTP ' + r.status));
          setStatus('segmentStatus', 'Saved ✓ Reload to see the filters update.');
        }} catch (err) {{
          setStatus('segmentStatus', 'Save failed: ' + (err.message || err), true);
        }} finally {{ btn.disabled = false; }}
      }});
    }})();
  </script>
  {budget_scripts}
  <script>{dashboard_topbar_js()}</script>
</body>
</html>"""
