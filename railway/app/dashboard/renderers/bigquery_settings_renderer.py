"""Focused settings page for a BigQuery-mart client.

Only the controls needed to set up the account and push/pull data from
BigQuery: the BigQuery connection (routing + service-account credential),
account mapping (account IDs), and data controls (refresh / backfill / mart
freshness). Reuses the test page's sidebar + helpers so it stays import-light
and previewable offline via .preview/gen.py.
"""

from __future__ import annotations

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    dashboard_topbar_js,
    favicon_head_html,
    dashboard_sidebar_view_nav_html,
    render_sidebar,
)
from dashboard.renderers.bigquery_dashboard_renderer import _api_url
from dashboard.renderers import budget_tracker
from dashboard.utils.formatting import esc as _esc


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()


def render_bigquery_settings_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    routing: dict | None = None,
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
) -> str:
    """Settings page for any BigQuery-mart (Nixon-style) client.

    Defaults preserve Nixon's exact page. client_slug drives dashboard-facing
    URLs + the connectors nav; api_client_key drives the /api/clients/* refresh
    + health endpoints (see render_bigquery_dashboard_page for why these are
    separate for Nixon itself). show_linkedin_backfill hides the LinkedIn-only
    onboarding button for generic clients, whose full refresh already syncs
    every connected platform.
    """
    routing = routing or {}
    account_ids = account_ids or {}
    project = routing.get("project") or "—"
    ga4_dataset = routing.get("ga4_dataset") or "—"
    marts_dataset = routing.get("marts_dataset") or "marketing_marts"

    admin_class = "is-admin" if session_is_admin else ""

    # The "Verify BigQuery access" control uses /api/clients/{api_client_key}/bq-verify,
    # which resolves the project from client_dashboard_config. That only holds for
    # generic bigquery_nixon clients (where api_client_key == client_slug); Nixon
    # itself uses a hardcoded project under a different slug, so hide it there.
    show_bq_verify = client_slug == api_client_key
    verify_html = (
        """
      <div class="btn-row" style="margin-top:14px">
        <button type="button" class="primary ghost" id="bqVerifyBtn">Verify BigQuery access</button>
        <span class="status" id="bqVerifyStatus"></span>
      </div>"""
        if show_bq_verify else ""
    )

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

    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{_esc(flash)}</div>'
    elif flash_error:
        flash_html = f'<div class="flash err">{_esc(flash_error)}</div>'

    # Budget tracker — the shared module, always shown here (admins get the goal
    # editor). Admins also get a toggle for whether it appears on the client-facing
    # Campaign Explorer (server-persisted per client).
    budget_css = budget_tracker.css()
    budget_module_html = budget_tracker.section_html(can_edit=session_is_admin)
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
    budget_visibility_html = "" if not session_is_admin else f"""
    <section>
      <h2>Budget tracker visibility</h2>
      <p class="hint">This tracker always shows on this page. Toggle whether it also appears at the bottom of the client-facing Campaign Explorer.</p>
      <div class="module-toggle-row" style="margin-top:14px">
        <div class="module-toggle-info">
          <span class="module-toggle-label">Show on Campaign Explorer</span>
          <span class="module-toggle-desc">When on, clients see the budget tracker on their Campaign Explorer.</span>
        </div>
        <label class="toggle-switch" title="{'On' if budget_tracker_enabled else 'Off'}"><input type="checkbox" id="budgetVisibilityToggle"{budget_toggle_checked}><span class="toggle-track"></span></label>
      </div>
      <span class="status" id="budgetVisibilityStatus" style="display:block;margin-top:8px"></span>
    </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — Settings</title>
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
    .badge {{ display:inline-block; font-size:.6rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; padding:2px 6px; border-radius:4px; vertical-align:middle; margin-left:5px; }}
    .badge-view {{ background:#eef4fb; color:#1d6fd0; }}
    .badge-tbl {{ background:#eef7f2; color:#0a7f3f; }}
    .badge-opt {{ background:#fff8e7; color:#8a6500; }}
    .pipe-table td.left {{ vertical-align:top; }}
    .pipe-table .module-label {{ font-weight:600; color:var(--navy); font-size:.88rem; }}
    .pipe-table .module-sub {{ font-size:.76rem; color:var(--muted); margin-top:1px; }}
    .module-toggle-row {{ display:flex; align-items:center; justify-content:space-between; padding:10px 14px; border:1px solid var(--line-soft); border-radius:var(--radius-sm); background:#fafcff; }}
    .module-toggle-info {{ display:flex; flex-direction:column; gap:2px; }}
    .module-toggle-label {{ font-size:.9rem; font-weight:650; color:var(--navy); }}
    .module-toggle-desc {{ font-size:.76rem; color:var(--muted); }}
    .toggle-switch {{ position:relative; display:inline-block; width:42px; height:24px; flex-shrink:0; }}
    .toggle-switch input {{ opacity:0; width:0; height:0; position:absolute; }}
    .toggle-track {{ position:absolute; cursor:pointer; inset:0; background:#c5cdd9; border-radius:24px; transition:background .2s; }}
    .toggle-track:before {{ content:''; position:absolute; left:3px; top:3px; width:18px; height:18px; background:#fff; border-radius:50%; transition:transform .2s; box-shadow:0 1px 3px rgba(0,0,0,.2); }}
    .toggle-switch input:checked + .toggle-track {{ background:var(--accent); }}
    .toggle-switch input:checked + .toggle-track:before {{ transform:translateX(18px); }}
    .toggle-switch input:focus-visible + .toggle-track {{ outline:2px solid #bcd4f0; outline-offset:2px; }}
    {budget_css}
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    {sidebar_html}
    <div class="dash-main">
  <main>
    <div class="page-head">
      <h1>{_esc(label)} — Settings</h1>
      <p class="debug-only">Configure the BigQuery account connection and push / pull data.</p>
    </div>
    {flash_html}

    <section>
      <h2>BigQuery connection</h2>
      <div class="kv-grid">
        <div><span class="kv-label">Project</span><span class="kv-val mono">{_esc(project)}</span></div>
        <div><span class="kv-label">GA4 dataset</span><span class="kv-val mono">{_esc(ga4_dataset)}</span></div>
        <div><span class="kv-label">Marts dataset</span><span class="kv-val mono">{_esc(marts_dataset)}</span></div>
      </div>
      <p class="hint">Reads use the shared agency service account (managed once in <a href="/admin">Admin</a>), which needs the <strong>BigQuery Data Editor</strong> and <strong>BigQuery Job User</strong> roles on this project in GCP.</p>
      {verify_html}
    </section>

    <section>
      <h2>Data pipeline map</h2>
      <p class="hint">Each dashboard module calls one API endpoint that reads one mart table or view in <code>{_esc(marts_dataset)}</code> on <code>{_esc(project)}</code>.</p>
      <div class="table-wrap" style="margin-top:14px">
        <table class="pipe-table">
          <thead>
            <tr>
              <th class="left">Module</th>
              <th class="left">API endpoint</th>
              <th class="left">BQ mart</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="left"><div class="module-label">Overview</div><div class="module-sub">Paid media summary + daily trend</div></td>
              <td class="left mono">/api/clients/{api_client_key}/summary</td>
              <td class="left mono">vw_paid_media_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Campaign Explorer — Google</div><div class="module-sub">Ad-level creative drill-down</div></td>
              <td class="left mono">/api/clients/{api_client_key}/google-ads/explorer</td>
              <td class="left mono">explorer_google_ads_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Campaign Explorer — Keywords</div><div class="module-sub">Cost by search keyword</div></td>
              <td class="left mono">/api/clients/{api_client_key}/google-ads/keywords</td>
              <td class="left mono">explorer_google_ads_keyword_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Campaign Explorer — LinkedIn</div><div class="module-sub">Creative thumbnails + spend</div></td>
              <td class="left mono">/api/clients/{api_client_key}/linkedin/explorer</td>
              <td class="left mono">fact_linkedin_ads_creative_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Top Pages</div><div class="module-sub">Page views, users, sessions</div></td>
              <td class="left mono">/api/clients/{api_client_key}/pages/top</td>
              <td class="left mono">vw_page_path_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">AI Traffic + Campaign Explorer paid source</div><div class="module-sub">Source / AI referral breakdown</div></td>
              <td class="left mono">/api/clients/{api_client_key}/pages/sources</td>
              <td class="left mono">vw_page_path_source_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Traffic</div><div class="module-sub">Sessions by channel + daily trend</div></td>
              <td class="left mono">/api/clients/{api_client_key}/pages/traffic-acquisition</td>
              <td class="left mono">vw_ga4_traffic_acq_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Audience</div><div class="module-sub">Device type split</div></td>
              <td class="left mono">/api/clients/{api_client_key}/pages/device-split</td>
              <td class="left mono">vw_ga4_tech_daily<span class="badge badge-view">VIEW</span><span class="badge badge-opt">optional</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Landing Pages</div><div class="module-sub">Session + key event rate by first URL</div></td>
              <td class="left mono">/api/clients/{api_client_key}/pages/landing</td>
              <td class="left mono">vw_ga4_landing_pages_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Conversions</div><div class="module-sub">Custom events + form funnel</div></td>
              <td class="left mono">/api/clients/{api_client_key}/analytics/conversions</td>
              <td class="left mono">vw_ga4_events_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — User Acquisition</div><div class="module-sub">First-touch channel + source/medium</div></td>
              <td class="left mono">/api/clients/{api_client_key}/analytics/user-acquisition</td>
              <td class="left mono">vw_ga4_user_acq_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Demographics</div><div class="module-sub">City/region, age bracket, gender</div></td>
              <td class="left mono">/api/clients/{api_client_key}/analytics/demographics</td>
              <td class="left mono">vw_ga4_demographics_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Mart Health</div><div class="module-sub">Row counts + date freshness per source</div></td>
              <td class="left mono">/api/clients/{api_client_key}/marketing/health</td>
              <td class="left mono">mart_health<span class="badge badge-tbl">TABLE</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Sidebar pages</h2>
      <p class="hint">Toggle which pages appear in the sidebar. Saved in your browser only (no server state).</p>
      <div id="pageToggles" style="margin-top:14px; display:flex; flex-direction:column; gap:12px;"></div>
      <div class="btn-row" style="margin-top:16px;">
        <button type="button" class="primary ghost" id="resetPagesBtn">Reset to defaults (all on)</button>
      </div>
    </section>

    <section>
      <h2>Campaign explorer filters</h2>
      <p class="hint">Define the filter chips on the Campaign Explorer. One rule per line as
        <code>Chip label = phrase</code>; a chip keeps campaigns whose name contains that phrase
        (case-insensitive). Separate phrases with commas to match any of them, and group chips into
        rows with a <code>[Group name]</code> header. Leave blank to use the built-in defaults.</p>
      <label for="explorerFilters" style="margin-top:14px">Filter rules</label>
      <textarea id="explorerFilters" spellcheck="false" placeholder="[Product]&#10;Apparel = apparel&#10;Scrubs = scrub&#10;Linens = linen&#10;&#10;[Region]&#10;TX = tx&#10;FL = fl&#10;MA = ma">{_esc(explorer_filters)}</textarea>
      <div class="btn-row" style="margin-top:12px">
        <button type="button" class="primary" id="explorerFiltersSaveBtn">Save filters</button>
        <span class="status" id="explorerFiltersStatus"></span>
      </div>
    </section>

    <section>
      <h2>Data</h2>
      <p class="hint">Pull recent data, or backfill history, into BigQuery for {_esc(label)}.</p>
      <div class="btn-row">
        <button type="button" class="primary" id="refreshBtn">Refresh — last 30 days</button>
        {'<button type="button" class="primary ghost" id="backfillBtn">Backfill LinkedIn — 180 days</button>' if show_linkedin_backfill else ''}
        <span class="status" id="dataStatus"></span>
      </div>
      <h3 class="sub">Freshness — mart health</h3>
      <div class="status" id="healthStatus">Loading…</div>
      <div class="table-wrap"><table id="healthTable"></table></div>
    </section>

    {budget_visibility_html}
    {budget_module_html}
  </main>
    </div>
  </div>
  <script>
    const REFRESH_API = "{_api_url(f'/api/clients/{api_client_key}/refresh', access_key=access_key)}";
    const BACKFILL_API = "{_api_url(f'/api/clients/{api_client_key}/backfill-linkedin', access_key=access_key)}";
    const HEALTH_API = "{_api_url(f'/api/clients/{api_client_key}/marketing/health', access_key=access_key)}";
    const BQ_VERIFY_API = "{_api_url(f'/api/clients/{api_client_key}/bq-verify', access_key=access_key)}";
    const EXPLORER_FILTERS_API = "{_api_url(f'/api/clients/{api_client_key}/explorer/filters', access_key=access_key)}";
    const nums = new Intl.NumberFormat('en-US');
    const dollars = new Intl.NumberFormat('en-US', {{ style:'currency', currency:'USD', maximumFractionDigits:2 }});
    const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({{ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }}[c]));
    const count = v => nums.format(Math.round(Number(v || 0)));
    const money = v => dollars.format(Number(v || 0));
    const SOURCE_LABELS = {{ google:'Google Ads', linkedin:'LinkedIn', meta:'Meta', google_analytics:'Google Analytics' }};
    const srcLabel = v => SOURCE_LABELS[String(v || '').toLowerCase()] || v;
    const moneyD = v => (v == null ? '\\u2014' : money(v));
    function setStatus(id, text, isErr) {{ const el = document.getElementById(id); el.textContent = text; el.className = isErr ? 'status error' : 'status'; }}
    async function runJob(url, confirmMsg, label) {{
      if (confirmMsg && !confirm(confirmMsg)) return;
      const buttons = [document.getElementById('refreshBtn'), document.getElementById('backfillBtn')].filter(Boolean);
      buttons.forEach(b => b.disabled = true);
      setStatus('dataStatus', label + '… this can take 1–2 minutes. Leave this tab open.');
      const t0 = Date.now();
      try {{
        const r = await fetch(url, {{ method:'POST', credentials:'same-origin' }});
        const body = await r.json().catch(() => ({{}}));
        if (!r.ok) throw new Error((body && (body.detail && (body.detail.error || body.detail) || body.detail)) || r.statusText);
        const li = body.linkedin || {{}};
        const secs = Math.round((Date.now() - t0) / 1000);
        const extra = li.rows_fetched != null ? ` · LinkedIn ${{li.rows_fetched}} rows through ${{li.data_through || '—'}}` : '';
        setStatus('dataStatus', `${{label}} done in ${{secs}}s${{extra}}. Refreshing freshness…`);
        loadHealth();
      }} catch (err) {{
        setStatus('dataStatus', label + ' failed: ' + (err.message || err), true);
      }} finally {{
        buttons.forEach(b => b.disabled = false);
      }}
    }}
    document.getElementById('refreshBtn').addEventListener('click', () => runJob(REFRESH_API, null, 'Refresh'));
    (function(){{ const b = document.getElementById('backfillBtn'); if (b) b.addEventListener('click', () => runJob(BACKFILL_API, 'Backfill ~180 days of LinkedIn into BigQuery?', 'Backfill')); }})();
    (function(){{
      const b = document.getElementById('bqVerifyBtn'); if (!b) return;
      b.addEventListener('click', async () => {{
        b.disabled = true; setStatus('bqVerifyStatus', 'Checking…');
        try {{
          const r = await fetch(BQ_VERIFY_API, {{ method:'POST', credentials:'same-origin' }});
          const body = await r.json().catch(() => ({{}}));
          if (body.ok) setStatus('bqVerifyStatus', body.message || 'Access verified.');
          else setStatus('bqVerifyStatus', body.error || 'Verification failed.', true);
        }} catch (err) {{
          setStatus('bqVerifyStatus', 'Verification failed: ' + (err.message || err), true);
        }} finally {{ b.disabled = false; }}
      }});
    }})();
    (function(){{
      const b = document.getElementById('explorerFiltersSaveBtn'); if (!b) return;
      b.addEventListener('click', async () => {{
        b.disabled = true; setStatus('explorerFiltersStatus', 'Saving…');
        try {{
          const filters = document.getElementById('explorerFilters').value;
          const r = await fetch(EXPLORER_FILTERS_API, {{
            method:'POST', credentials:'same-origin',
            headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{ filters }}),
          }});
          const body = await r.json().catch(() => ({{}}));
          if (!r.ok) throw new Error((body && (body.detail && (body.detail.error || body.detail) || body.detail)) || r.statusText);
          setStatus('explorerFiltersStatus', 'Saved. Reload the dashboard to see the updated chips.');
        }} catch (err) {{
          setStatus('explorerFiltersStatus', 'Save failed: ' + (err.message || err), true);
        }} finally {{ b.disabled = false; }}
      }});
    }})();
    async function loadHealth() {{
      setStatus('healthStatus', 'Loading mart health…');
      try {{
        const r = await fetch(HEALTH_API, {{ credentials:'same-origin' }});
        const body = await r.json();
        const rows = (body && body.rows) || [];
        const el = document.getElementById('healthTable');
        if (!rows.length) {{ el.innerHTML = '<tbody><tr><td class="empty">No mart health rows.</td></tr></tbody>'; setStatus('healthStatus', 'No data.'); return; }}
        const head = '<thead><tr><th class="left">Source</th><th>Rows</th><th class="left">Earliest</th><th class="left">Latest</th><th>Spend</th></tr></thead>';
        const tb = rows.map(s => `<tr><td class="left">${{esc(srcLabel(s.source))}}</td><td>${{count(s.row_count)}}</td><td class="left">${{esc(s.earliest_date || '—')}}</td><td class="left">${{esc(s.latest_date || '—')}}</td><td>${{moneyD(s.spend)}}</td></tr>`).join('');
        el.innerHTML = head + `<tbody>${{tb}}</tbody>`;
        setStatus('healthStatus', `${{rows.length}} source(s).`);
      }} catch (err) {{
        setStatus('healthStatus', err.message || String(err), true);
      }}
    }}
    loadHealth();

    // ---- Sidebar page toggles ----
    // Mirrors the tab keys in bigquery_dashboard_renderer's TABS + the data-tab
    // attributes in base_layout.dashboard_sidebar_view_nav_html. Stored in
    // localStorage (client-side only); the sidebar nav on every page reads
    // this to hide turned-off pages, and the dashboard falls back off a
    // hidden active tab.
    const ALL_PAGES = ['overview','explorer','analytics','ai_traffic','gsc'];
    const PAGE_LABELS = {{
      overview:'Overview', explorer:'Campaign Explorer',
      analytics:'Website Analytics', ai_traffic:'AI Traffic', gsc:'Search Console',
    }};
    const PAGE_DESCS = {{
      overview:'Paid media / web traffic summary and daily trend.',
      explorer:'Campaign → ad group → ad drill-down across platforms.',
      analytics:'GA4 pages, traffic, audience, landing, conversions and more.',
      ai_traffic:'Website sessions referred by AI assistants, by source and landing page.',
      gsc:'Search Console clicks, queries, pages and keyword tracking.',
    }};
    const LS_KEY = 'nixon_sidebar_pages:{client_slug}';  // scoped per client — a global key leaked toggles across portals
    function getPages() {{
      try {{ const s = localStorage.getItem(LS_KEY); const saved = s ? JSON.parse(s) : {{}}; return ALL_PAGES.reduce((o,k) => ({{...o,[k]:k in saved?saved[k]:true}}),{{}}); }} catch {{ return ALL_PAGES.reduce((o,k)=>({{...o,[k]:true}}),{{}}); }}
    }}
    function savePages(m) {{ try {{ localStorage.setItem(LS_KEY, JSON.stringify(m)); }} catch {{}} }}
    function renderPageToggles() {{
      const m = getPages();
      const container = document.getElementById('pageToggles');
      container.innerHTML = ALL_PAGES.map(key => {{
        const checked = m[key] ? ' checked' : '';
        return `<div class="module-toggle-row"><div class="module-toggle-info"><span class="module-toggle-label">${{esc(PAGE_LABELS[key])}}</span><span class="module-toggle-desc">${{esc(PAGE_DESCS[key])}}</span></div><label class="toggle-switch" title="${{m[key]?'On':'Off'}}"><input type="checkbox" data-page="${{key}}"${{checked}}><span class="toggle-track"></span></label></div>`;
      }}).join('');
      container.querySelectorAll('input[data-page]').forEach(inp => inp.addEventListener('change', () => {{
        const cur = getPages(); cur[inp.dataset.page] = inp.checked; savePages(cur);
        inp.closest('label').title = inp.checked ? 'On' : 'Off';
      }}));
    }}
    document.getElementById('resetPagesBtn').addEventListener('click', () => {{
      const all = ALL_PAGES.reduce((o,k)=>({{...o,[k]:true}}),{{}}); savePages(all); renderPageToggles();
    }});
    renderPageToggles();
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
  </script>
  {budget_scripts}
  <script>{dashboard_topbar_js()}</script>
</body>
</html>"""
