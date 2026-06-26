"""GTM live-tags audit page for nixon-bq-test."""

from __future__ import annotations

from dashboard.renderers.base_layout import favicon_head_html
from dashboard.renderers.nixon_analytics_renderer import _SIDEBAR_CSS
from dashboard.utils.formatting import esc as _esc

_ICON_TAGS = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9.293 2.293a1 1 0 011.414 0l7 7A1 1 0 0117 11H3a1 1 0 01-.707-1.707l7-7zM10 14v3m-3-3h6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
_ICON_ANALYTICS = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 17l4-7 3 4 3-6 4 9" stroke-linecap="round" stroke-linejoin="round"/></svg>'
_ICON_BACK = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 6l-4 4 4 4m5-8l-4 4 4 4" stroke-linecap="round" stroke-linejoin="round"/></svg>'
_ICON_SETTINGS = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="10" cy="10" r="3"/><path d="M10 2v2m0 12v2M2 10h2m12 0h2m-3.17-5.66-1.42 1.42M5.59 14.24l-1.42 1.42M14.83 14.24l-1.42-1.42M5.59 5.66l-1.42-1.42" stroke-linecap="round"/></svg>'
_ICON_MENU = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h14M3 10h14M3 14h14" stroke-linecap="round"/></svg>'
_ICON_REFRESH = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 4v5h5M16 16v-5h-5" stroke-linecap="round" stroke-linejoin="round"/><path d="M4.05 14.44A8 8 0 1015.95 5.56" stroke-linecap="round"/></svg>'


def render_gtm_page(
    *,
    client_slug: str = "nixon-bq-test",
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    from urllib.parse import urlencode

    def _url(path: str) -> str:
        if not access_key:
            return path
        return f"{path}?{urlencode({'key': access_key})}"

    analytics_url = _url("/dashboard/nixon-bq-test/analytics")
    settings_url  = _url("/dashboard/nixon-bq-test/settings")
    main_url      = _url("/dashboard/nixon-bq-test")
    api_url       = f"/api/clients/{client_slug}/gtm/live-tags"
    api_refresh   = f"{api_url}?refresh=true"
    if access_key:
        api_url     = f"{api_url}?key={access_key}"
        api_refresh = f"{api_refresh}&key={access_key}"

    account_html = ""
    if use_session and session_email:
        account_html = f"""
        <div class="dash-sidebar-account">
          <span class="dash-sidebar-account-email">{_esc(session_email)}</span>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nixon — GTM Tags</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04),0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    {_SIDEBAR_CSS}
    main {{ padding: 28px 32px; max-width: 1200px; }}
    @media (max-width:900px) {{ main {{ padding: 24px 16px; }} }}
    .page-head {{ display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:24px; }}
    .page-title {{ font-size:1.45rem; font-weight:700; color:var(--navy); margin:0; }}
    .page-meta {{ font-size:.82rem; color:var(--muted); margin-top:3px; }}
    .btn {{ display:inline-flex; align-items:center; gap:6px; padding:8px 16px; border-radius:9px; font-size:.88rem; font-weight:600; cursor:pointer; border:0; transition:background .15s; text-decoration:none; }}
    .btn-secondary {{ background:var(--card); color:var(--navy); border:1px solid var(--line); }}
    .btn-secondary:hover {{ background:#f0f4fa; }}
    .btn svg {{ width:16px; height:16px; }}
    .card {{ background:var(--card); border-radius:var(--radius); border:1px solid var(--line); box-shadow:var(--shadow); }}
    .summary-bar {{ display:flex; gap:20px; flex-wrap:wrap; margin-bottom:20px; }}
    .stat-chip {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 20px; }}
    .stat-chip-label {{ font-size:.75rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
    .stat-chip-value {{ font-size:1.5rem; font-weight:700; color:var(--navy); line-height:1.2; margin-top:2px; }}
    .tag-table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:.875rem; }}
    th {{ text-align:left; padding:10px 14px; font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); border-bottom:2px solid var(--line); white-space:nowrap; }}
    td {{ padding:11px 14px; border-bottom:1px solid var(--line); vertical-align:middle; }}
    tr:last-child td {{ border-bottom:0; }}
    tr:hover td {{ background:#f7fafc; }}
    .badge {{ display:inline-flex; align-items:center; padding:3px 9px; border-radius:6px; font-size:.72rem; font-weight:700; white-space:nowrap; }}
    .badge-ga4    {{ background:#e8f0fe; color:#1a56db; }}
    .badge-gads   {{ background:#e6f4ea; color:#137333; }}
    .badge-gtag   {{ background:#e6f4ea; color:#137333; }}
    .badge-fb     {{ background:#ede9fe; color:#5b21b6; }}
    .badge-li     {{ background:#dbeafe; color:#1d4ed8; }}
    .badge-html   {{ background:#f3f4f6; color:#374151; }}
    .badge-img    {{ background:#fef3c7; color:#92400e; }}
    .badge-default {{ background:#f1f5f9; color:#475569; }}
    .status-paused {{ color:var(--bad); font-size:.8rem; font-weight:600; }}
    .status-active {{ color:var(--ok); font-size:.8rem; font-weight:600; }}
    .trigger-list {{ display:flex; flex-wrap:wrap; gap:4px; }}
    .trigger-chip {{ display:inline-flex; align-items:center; padding:2px 7px; background:#f0f4fa; border-radius:5px; font-size:.72rem; color:#374151; }}
    .loading-msg {{ padding:40px; text-align:center; color:var(--muted); }}
    .error-msg {{ padding:20px; color:var(--bad); background:#fef2f2; border-radius:9px; border:1px solid #fecaca; }}
    .empty-state {{ padding:48px 20px; text-align:center; color:var(--muted); }}
  </style>
</head>
<body>
  <div class="app-shell" id="appShell">
    <button type="button" class="dash-sidebar-toggle" id="sidebarToggle" aria-label="Open navigation">{_ICON_MENU}</button>
    <div class="dash-sidebar-backdrop" id="sidebarBackdrop" hidden></div>
    <aside class="dash-sidebar" id="dashSidebar">
      <div class="dash-sidebar-head">
        <a href="{analytics_url}" class="dash-sidebar-logo">
          <img class="dash-sidebar-logo-icon" src="/static/apple-touch-icon.png" alt="" width="34" height="34" onerror="this.remove()" />
          <span class="dash-sidebar-wordmark">Sagefrog</span>
        </a>
        <span class="dash-sidebar-beta">Beta</span>
      </div>
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <a class="dash-view-btn" href="{analytics_url}">{_ICON_ANALYTICS}<span>Analytics</span></a>
        <a class="dash-view-btn active" href="#">{_ICON_TAGS}<span>GTM Tags</span></a>
      </nav>
      <div class="dash-sidebar-footer">
        <div class="dash-sidebar-client"><span class="topbar-client-label">Nixon — GTM Audit</span></div>
        <nav class="dash-sidebar-links">
          <a href="{main_url}" class="dash-sidebar-link">{_ICON_BACK}<span>Paid Media</span></a>
          <a href="{settings_url}" class="dash-sidebar-link">{_ICON_SETTINGS}<span>Settings</span></a>
        </nav>
        {account_html}
      </div>
    </aside>

    <div class="dash-main">
      <main>
        <div class="page-head">
          <div>
            <h1 class="page-title">GTM Live Tags</h1>
            <p class="page-meta" id="pageMeta">Loading container…</p>
          </div>
          <button class="btn btn-secondary" id="refreshBtn" onclick="loadTags(true)">
            {_ICON_REFRESH}Refresh
          </button>
        </div>

        <div class="summary-bar" id="summaryBar" style="display:none">
          <div class="stat-chip">
            <div class="stat-chip-label">Total Tags</div>
            <div class="stat-chip-value" id="statTotal">—</div>
          </div>
          <div class="stat-chip">
            <div class="stat-chip-label">Active</div>
            <div class="stat-chip-value" id="statActive" style="color:var(--ok)">—</div>
          </div>
          <div class="stat-chip">
            <div class="stat-chip-label">Paused</div>
            <div class="stat-chip-value" id="statPaused" style="color:var(--bad)">—</div>
          </div>
          <div class="stat-chip">
            <div class="stat-chip-label">Container Version</div>
            <div class="stat-chip-value" id="statVersion">—</div>
          </div>
        </div>

        <div class="card">
          <div id="tableContainer" class="tag-table-wrap">
            <div class="loading-msg">Loading tags…</div>
          </div>
        </div>
      </main>
    </div>
  </div>

<script>
const API_URL     = {repr(api_url)};
const API_REFRESH = {repr(api_refresh)};

const TAG_BADGE = {{
  'Google Analytics': 'ga4',
  'GA4 Configuration': 'ga4',
  'GA4 Event': 'ga4',
  'Google Ads Remarketing': 'gads',
  'Google Ads Conversion Tracking': 'gads',
  'Google Tag': 'gtag',
  'Facebook Pixel': 'fb',
  'Meta Pixel': 'fb',
  'LinkedIn Insight Tag': 'li',
  'Custom HTML': 'html',
  'Custom Image': 'img',
}};

function badgeClass(type) {{
  for (const [k, v] of Object.entries(TAG_BADGE)) {{
    if (type.includes(k)) return 'badge-' + v;
  }}
  return 'badge-default';
}}

function renderTable(rows) {{
  if (!rows.length) {{
    return '<div class="empty-state">No tags found in this container.</div>';
  }}
  const ths = ['Tag Name','Type','Status','Firing Triggers'];
  const head = '<tr>' + ths.map(h => `<th>${{h}}</th>`).join('') + '</tr>';
  const body = rows.map(r => {{
    const bc  = badgeClass(r.friendly_type || '');
    const typ = `<span class="badge ${{bc}}">${{esc(r.friendly_type || r.raw_type || 'Unknown')}}</span>`;
    const st  = r.paused
      ? '<span class="status-paused">⏸ Paused</span>'
      : '<span class="status-active">● Active</span>';
    const triggers = (r.firing_trigger_names || []);
    const trig = triggers.length
      ? '<div class="trigger-list">' + triggers.map(t => `<span class="trigger-chip">${{esc(t)}}</span>`).join('') + '</div>'
      : '<span style="color:var(--muted);font-size:.8rem">—</span>';
    return `<tr><td><strong>${{esc(r.tag_name || '')}}</strong></td><td>${{typ}}</td><td>${{st}}</td><td>${{trig}}</td></tr>`;
  }}).join('');
  return `<table><thead>${{head}}</thead><tbody>${{body}}</tbody></table>`;
}}

function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

async function loadTags(forceRefresh = false) {{
  const container = document.getElementById('tableContainer');
  const summaryBar = document.getElementById('summaryBar');
  const pageMeta = document.getElementById('pageMeta');
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  container.innerHTML = '<div class="loading-msg">Loading tags…</div>';
  summaryBar.style.display = 'none';
  try {{
    const url = forceRefresh ? API_REFRESH : API_URL;
    const resp = await fetch(url);
    if (!resp.ok) {{
      const err = await resp.json().catch(() => ({{detail: resp.statusText}}));
      container.innerHTML = `<div class="error-msg">Error ${{resp.status}}: ${{esc(err.detail || err.message || resp.statusText)}}</div>`;
      pageMeta.textContent = 'Failed to load';
      return;
    }}
    const data = await resp.json();
    const rows = data.rows || [];
    const active = rows.filter(r => !r.paused).length;
    const paused = rows.filter(r => r.paused).length;
    document.getElementById('statTotal').textContent  = rows.length;
    document.getElementById('statActive').textContent = active;
    document.getElementById('statPaused').textContent = paused;
    document.getElementById('statVersion').textContent = data.container_version || '—';
    summaryBar.style.display = 'flex';
    const fetched = data.fetched_at ? new Date(data.fetched_at).toLocaleString() : '';
    pageMeta.textContent = fetched ? `Container data as of ${{fetched}}` : '';
    container.innerHTML = renderTable(rows);
  }} catch(e) {{
    container.innerHTML = `<div class="error-msg">Network error: ${{esc(e.message)}}</div>`;
    pageMeta.textContent = 'Failed to load';
  }} finally {{
    btn.disabled = false;
  }}
}}

// Sidebar toggle
document.getElementById('sidebarToggle').addEventListener('click', () => {{
  const shell = document.getElementById('appShell');
  const open = shell.classList.toggle('sidebar-open');
  document.getElementById('sidebarToggle').setAttribute('aria-expanded', open);
  const backdrop = document.getElementById('sidebarBackdrop');
  backdrop.hidden = !open;
}});
document.getElementById('sidebarBackdrop').addEventListener('click', () => {{
  document.getElementById('appShell').classList.remove('sidebar-open');
  document.getElementById('sidebarToggle').setAttribute('aria-expanded', false);
  document.getElementById('sidebarBackdrop').hidden = true;
}});

loadTags();
</script>
</body>
</html>"""
