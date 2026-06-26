"""Focused settings page for the Nixon BQ-test client.

Only the controls needed to set up the account and push/pull data from
BigQuery: the BigQuery connection (routing + service-account credential),
account mapping (account IDs), and data controls (refresh / backfill / mart
freshness). Reuses the test page's sidebar + helpers so it stays import-light
and previewable offline via .preview/gen.py.
"""

from __future__ import annotations

from dashboard.renderers.base_layout import favicon_head_html
from dashboard.renderers.nixon_bq_test_renderer import _SIDEBAR_CSS, _api_url
from dashboard.utils.formatting import esc as _esc

_ICON_MENU = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>'
_ICON_OVERVIEW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
_ICON_EXPLORER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>'
_ICON_WEBSITE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>'
_ICON_SETTINGS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'


def render_nixon_bq_settings_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    routing: dict | None = None,
    account_ids: dict | None = None,
    sa_email: str | None = None,
    flash: str | None = None,
    flash_error: str | None = None,
) -> str:
    routing = routing or {}
    account_ids = account_ids or {}
    project = routing.get("project") or "—"
    ga4_dataset = routing.get("ga4_dataset") or "—"
    marts_dataset = routing.get("marts_dataset") or "marketing_marts"
    creds_env = routing.get("creds_env") or "GCP_SERVICE_ACCOUNT_JSON"
    railway_ready = bool(routing.get("railway_ready"))

    admin_class = "is-admin" if session_is_admin else ""
    dash_url = _api_url("/dashboard/nixon-bq-test", access_key=access_key)
    this_url = _api_url("/dashboard/nixon-bq-test/settings", access_key=access_key)
    cred_action = _api_url("/dashboard/nixon/gcp-credentials", access_key=access_key)
    settings_action = _api_url("/dashboard/nixon/settings", access_key=access_key)

    account_html = ""
    if use_session and session_email:
        admin_link = (
            '<a class="dash-sidebar-account-link" href="/admin">Admin</a>'
            '<span class="dash-sidebar-account-sep">·</span>' if session_is_admin else ""
        )
        account_html = f"""
        <div class="dash-sidebar-account">
          <span class="dash-sidebar-account-email">{_esc(session_email)}</span>
          <div class="dash-sidebar-account-actions">
            {admin_link}
            <form class="dash-sidebar-logout-form" method="post" action="/logout"><button type="submit" class="dash-sidebar-account-link">Sign out</button></form>
          </div>
        </div>"""

    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{_esc(flash)}</div>'
    elif flash_error:
        flash_html = f'<div class="flash err">{_esc(flash_error)}</div>'

    railway_note = "" if railway_ready else (
        '<p class="hint err-hint">Railway API not configured — credential upload is disabled until the '
        'RAILWAY_* variables are set on this service.</p>'
    )
    _disabled = "" if railway_ready else " disabled"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nixon Medical — Settings</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    {_SIDEBAR_CSS}
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
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    <button type="button" class="dash-sidebar-toggle" id="sidebarToggle" aria-label="Open navigation" aria-expanded="false" aria-controls="dashSidebar">{_ICON_MENU}</button>
    <div class="dash-sidebar-backdrop" id="sidebarBackdrop" hidden></div>
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Primary navigation">
      <div class="dash-sidebar-head">
        <a href="{dash_url}" class="dash-sidebar-logo" aria-label="Sagefrog home">
          <img class="dash-sidebar-logo-icon" src="/static/apple-touch-icon.png" alt="" width="34" height="34" onerror="this.remove()" />
          <span class="dash-sidebar-wordmark">Sagefrog</span>
        </a>
        <span class="dash-sidebar-beta">Beta</span>
      </div>
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <a class="dash-view-btn" href="{dash_url}">{_ICON_OVERVIEW}<span>Overview</span></a>
        <a class="dash-view-btn" href="{dash_url}">{_ICON_EXPLORER}<span>Explorer</span></a>
        <a class="dash-view-btn" href="{dash_url}">{_ICON_WEBSITE}<span>Website Analytics</span></a>
      </nav>
      <div class="dash-sidebar-footer">
        <div class="dash-sidebar-client"><span class="topbar-client-label">Nixon — BQ Test</span></div>
        <nav class="dash-sidebar-links" aria-label="Account navigation">
          <a href="{this_url}" class="dash-sidebar-link active">{_ICON_SETTINGS}<span>Settings</span></a>
        </nav>
        {account_html}
      </div>
    </aside>
    <div class="dash-main">
  <main>
    <div class="page-head">
      <h1>Nixon Medical — Settings</h1>
      <p class="debug-only">Configure the BigQuery account connection and push / pull data.</p>
    </div>
    {flash_html}

    <section>
      <h2>BigQuery connection</h2>
      <div class="kv-grid">
        <div><span class="kv-label">Project</span><span class="kv-val mono">{_esc(project)}</span></div>
        <div><span class="kv-label">GA4 dataset</span><span class="kv-val mono">{_esc(ga4_dataset)}</span></div>
        <div><span class="kv-label">Marts dataset</span><span class="kv-val mono">{_esc(marts_dataset)}</span></div>
        <div><span class="kv-label">Credentials env</span><span class="kv-val mono">{_esc(creds_env)}</span></div>
        <div><span class="kv-label">Service account</span><span class="kv-val mono">{_esc(sa_email or '—')}</span></div>
      </div>
      <h3 class="sub">Service account key</h3>
      <p class="hint">Upload the GCP service-account JSON; it's validated, base64-encoded, and written to <code>{_esc(creds_env)}</code> on Railway (triggers a redeploy).</p>
      {railway_note}
      <form class="stack" method="post" action="{cred_action}" enctype="multipart/form-data" onsubmit="return confirm('Set {_esc(creds_env)} on Railway? The service will redeploy.');">
        <input type="hidden" name="env_var" value="{_esc(creds_env)}">
        <input type="hidden" name="next" value="/dashboard/nixon-bq-test/settings">
        <input type="file" name="credentials_file" accept="application/json,.json" required{_disabled}>
        <button type="submit" class="primary"{_disabled}>Upload &amp; set credential</button>
      </form>
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
              <td class="left mono">/api/clients/nixon/summary</td>
              <td class="left mono">vw_paid_media_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Campaign Explorer — Google</div><div class="module-sub">Ad-level creative drill-down</div></td>
              <td class="left mono">/api/clients/nixon/google-ads/explorer</td>
              <td class="left mono">explorer_google_ads_daily<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Campaign Explorer — LinkedIn</div><div class="module-sub">Creative thumbnails + spend</div></td>
              <td class="left mono">/api/clients/nixon/linkedin/explorer</td>
              <td class="left mono">fact_linkedin_ads_creative_daily<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Top Pages</div><div class="module-sub">Page views, users, sessions</div></td>
              <td class="left mono">/api/clients/nixon/pages/top</td>
              <td class="left mono">vw_page_path_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — By Source</div><div class="module-sub">Source / AI referral breakdown</div></td>
              <td class="left mono">/api/clients/nixon/pages/sources</td>
              <td class="left mono">vw_page_path_source_daily<span class="badge badge-view">VIEW</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Traffic</div><div class="module-sub">Sessions by channel + daily trend</div></td>
              <td class="left mono">/api/clients/nixon/pages/traffic-acquisition</td>
              <td class="left mono">ga4_TrafficAcquisition_*<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Audience</div><div class="module-sub">Device type split</div></td>
              <td class="left mono">/api/clients/nixon/pages/device-split</td>
              <td class="left mono">ga4_TechDetails_*<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Landing Pages</div><div class="module-sub">Session + key event rate by first URL</div></td>
              <td class="left mono">/api/clients/nixon/pages/landing</td>
              <td class="left mono">ga4_LandingPage_*<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Conversions</div><div class="module-sub">Custom events + form funnel</div></td>
              <td class="left mono">/api/clients/nixon/analytics/conversions</td>
              <td class="left mono">ga4_Events_*<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — User Acquisition</div><div class="module-sub">First-touch channel + source/medium</div></td>
              <td class="left mono">/api/clients/nixon/analytics/user-acquisition</td>
              <td class="left mono">ga4_UserAcquisition_*<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Website Analytics — Demographics</div><div class="module-sub">City/region, age bracket, gender</div></td>
              <td class="left mono">/api/clients/nixon/analytics/demographics</td>
              <td class="left mono">ga4_DemographicDetails_*<span class="badge badge-tbl">TABLE</span></td>
            </tr>
            <tr>
              <td class="left"><div class="module-label">Mart Health</div><div class="module-sub">Row counts + date freshness per source</div></td>
              <td class="left mono">/api/clients/nixon/marketing/health</td>
              <td class="left mono">mart_health<span class="badge badge-tbl">TABLE</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Website Analytics modules</h2>
      <p class="hint">Toggle which sections appear on the Website Analytics tab. Saved in your browser only (no server state).</p>
      <div id="moduleToggles" style="margin-top:14px; display:flex; flex-direction:column; gap:12px;"></div>
      <div class="btn-row" style="margin-top:16px;">
        <button type="button" class="primary ghost" id="resetModulesBtn">Reset to defaults (all on)</button>
      </div>
    </section>

    <section>
      <h2>Account mapping</h2>
      <p class="hint">Account IDs that control what gets ingested into BigQuery for Nixon.</p>
      <form class="form-grid" method="post" action="{settings_action}">
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="next" value="/dashboard/nixon-bq-test/settings">
        <label>LinkedIn account ID<input name="linkedin_account_id" value="{_esc(account_ids.get('linkedin_account_id') or '')}" placeholder="503285948"></label>
        <label>Google customer ID<input name="google_customer_id" value="{_esc(account_ids.get('google_customer_id') or '')}" placeholder="8032778786"></label>
        <label>Meta account ID<input name="meta_account_id" value="{_esc(account_ids.get('meta_account_id') or '')}"></label>
        <label>GA4 client key<input name="ga4_client_key" value="{_esc(account_ids.get('ga4_client_key') or '')}" placeholder="nixon"></label>
        <label>GTM account ID<input name="gtm_account_id" value="{_esc(account_ids.get('gtm_account_id') or '')}" placeholder="123456789"><span class="hint">GTM Admin URL → accounts/<strong>ID</strong>/containers/…</span></label>
        <label>GTM container ID<input name="gtm_container_id" value="{_esc(account_ids.get('gtm_container_id') or '')}" placeholder="987654321"><span class="hint">GTM Admin URL → …/containers/<strong>ID</strong></span></label>
        <div class="form-actions"><button type="submit" class="primary">Save account IDs</button></div>
      </form>
    </section>

    <section>
      <h2>Data</h2>
      <p class="hint">Pull recent data, or backfill history, into BigQuery for Nixon.</p>
      <div class="btn-row">
        <button type="button" class="primary" id="refreshBtn">Refresh — last 30 days</button>
        <button type="button" class="primary ghost" id="backfillBtn">Backfill LinkedIn — 180 days</button>
        <span class="status" id="dataStatus"></span>
      </div>
      <h3 class="sub">Freshness — mart health</h3>
      <div class="status" id="healthStatus">Loading…</div>
      <div class="table-wrap"><table id="healthTable"></table></div>
    </section>
  </main>
    </div>
  </div>
  <script>
    const REFRESH_API = "{_api_url('/api/clients/nixon/refresh', access_key=access_key)}";
    const BACKFILL_API = "{_api_url('/api/clients/nixon/backfill-linkedin', access_key=access_key)}";
    const HEALTH_API = "{_api_url('/api/clients/nixon/marketing/health', access_key=access_key)}";
    const nums = new Intl.NumberFormat('en-US');
    const dollars = new Intl.NumberFormat('en-US', {{ style:'currency', currency:'USD', maximumFractionDigits:2 }});
    const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({{ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }}[c]));
    const count = v => nums.format(Math.round(Number(v || 0)));
    const money = v => dollars.format(Number(v || 0));
    const SOURCE_LABELS = {{ google:'Google Ads', linkedin:'LinkedIn', google_analytics:'Google Analytics' }};
    const srcLabel = v => SOURCE_LABELS[String(v || '').toLowerCase()] || v;
    const moneyD = v => (v == null ? '\\u2014' : money(v));
    function setStatus(id, text, isErr) {{ const el = document.getElementById(id); el.textContent = text; el.className = isErr ? 'status error' : 'status'; }}
    async function runJob(url, confirmMsg, label) {{
      if (confirmMsg && !confirm(confirmMsg)) return;
      const buttons = [document.getElementById('refreshBtn'), document.getElementById('backfillBtn')];
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
    document.getElementById('backfillBtn').addEventListener('click', () => runJob(BACKFILL_API, 'Backfill ~180 days of LinkedIn into BigQuery for Nixon?', 'Backfill'));
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

    // ---- Website Analytics module toggles ----
    const ALL_MODULES = ['top_pages','traffic','audience','landing','conversions','user_acquisition','demographics'];
    const MODULE_LABELS = {{
      top_pages:'Top Pages', traffic:'Traffic', audience:'Audience', landing:'Landing Pages',
      conversions:'Conversions', user_acquisition:'User Acquisition', demographics:'Demographics',
    }};
    const MODULE_DESCS = {{
      top_pages:'Page views, users, sessions and engagement time per URL.',
      traffic:'Sessions by channel group + daily trend + source/medium table.',
      audience:'Device type split (desktop, mobile, tablet).',
      landing:'Landing page sessions, new users, key event rate and engagement.',
      conversions:'Custom GA4 event counts and form funnel (form_start → generate_lead).',
      user_acquisition:'First-touch channel, source and medium for new users.',
      demographics:'Top cities, age bracket bars, and gender split.',
    }};
    const LS_KEY = 'nixon_analytics_modules';
    function getModules() {{
      try {{ const s = localStorage.getItem(LS_KEY); const saved = s ? JSON.parse(s) : {{}}; return ALL_MODULES.reduce((o,k) => ({{...o,[k]:k in saved?saved[k]:true}}),{{}}); }} catch {{ return ALL_MODULES.reduce((o,k)=>({{...o,[k]:true}}),{{}}); }}
    }}
    function saveModules(m) {{ try {{ localStorage.setItem(LS_KEY, JSON.stringify(m)); }} catch {{}} }}
    function renderModuleToggles() {{
      const m = getModules();
      const container = document.getElementById('moduleToggles');
      container.innerHTML = ALL_MODULES.map(key => {{
        const checked = m[key] ? ' checked' : '';
        return `<div class="module-toggle-row"><div class="module-toggle-info"><span class="module-toggle-label">${{esc(MODULE_LABELS[key])}}</span><span class="module-toggle-desc">${{esc(MODULE_DESCS[key])}}</span></div><label class="toggle-switch" title="${{m[key]?'On':'Off'}}"><input type="checkbox" data-module="${{key}}"${{checked}}><span class="toggle-track"></span></label></div>`;
      }}).join('');
      container.querySelectorAll('input[data-module]').forEach(inp => inp.addEventListener('change', () => {{
        const cur = getModules(); cur[inp.dataset.module] = inp.checked; saveModules(cur);
        inp.closest('label').title = inp.checked ? 'On' : 'Off';
      }}));
    }}
    document.getElementById('resetModulesBtn').addEventListener('click', () => {{
      const all = ALL_MODULES.reduce((o,k)=>({{...o,[k]:true}}),{{}}); saveModules(all); renderModuleToggles();
    }});
    renderModuleToggles();

    (function() {{
      const shell = document.querySelector('.app-shell');
      const toggle = document.getElementById('sidebarToggle');
      const backdrop = document.getElementById('sidebarBackdrop');
      if (!shell || !toggle) return;
      const setOpen = open => {{ shell.classList.toggle('sidebar-open', open); toggle.setAttribute('aria-expanded', open ? 'true' : 'false'); if (backdrop) backdrop.hidden = !open; }};
      toggle.addEventListener('click', () => setOpen(!shell.classList.contains('sidebar-open')));
      if (backdrop) backdrop.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', e => {{ if (e.key === 'Escape') setOpen(false); }});
    }})();
  </script>
</body>
</html>"""
