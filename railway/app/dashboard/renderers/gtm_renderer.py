"""GTM live-tags audit page for any bigquery_nixon client (client_slug-scoped)."""

from __future__ import annotations

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    dashboard_topbar_js,
    favicon_head_html,
    platform_nav_flags,
    render_sidebar,
)

_ICON_TAGS     = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>'
_ICON_OVERVIEW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>'
_ICON_EXPLORER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg>'
_ICON_WEBSITE  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>'
_ICON_SEARCH   = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
_ICON_LEADS    = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>'
_ICON_SHIELD   = '<svg viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="1.8" width="15" height="15"><path d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.955 11.955 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" stroke-linecap="round" stroke-linejoin="round"/></svg>'
_ICON_REFRESH  = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 4v5h5M16 16v-5h-5" stroke-linecap="round" stroke-linejoin="round"/><path d="M4.05 14.44A8 8 0 1015.95 5.56" stroke-linecap="round"/></svg>'


def render_gtm_page(
    *,
    client_slug: str = "nixon-bq-test",
    label: str = "Nixon Medical",
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    import html
    from urllib.parse import urlencode

    def _url(path: str) -> str:
        if not access_key:
            return path
        return f"{path}?{urlencode({'key': access_key})}"

    title_label = html.escape(label or client_slug)
    main_url = _url(f"/dashboard/{client_slug}")

    pflags = platform_nav_flags(client_slug)
    lead_tracking_link = ""
    if pflags.get("show_lead_tracking"):
        lead_tracking_url = _url(f"/dashboard/{client_slug}/lead-tracking")
        lead_tracking_link = (
            f'<a class="dash-view-btn" href="{lead_tracking_url}">'
            f'{_ICON_LEADS}<span>Lead Tracking</span></a>'
        )

    view_nav_html = f"""
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <a class="dash-view-btn" href="{main_url}">{_ICON_OVERVIEW}<span>Overview</span></a>
        <a class="dash-view-btn" href="{main_url}">{_ICON_EXPLORER}<span>Explorer</span></a>
        <a class="dash-view-btn" href="{main_url}">{_ICON_WEBSITE}<span>Website Analytics</span></a>
        <a class="dash-view-btn" href="{main_url}">{_ICON_SEARCH}<span>Search Console</span></a>
        {lead_tracking_link}
        <a class="dash-view-btn active" href="#" style="margin-top:6px;border-top:1px solid rgba(255,255,255,.1);padding-top:14px">{_ICON_TAGS}<span>Event Tracking</span></a>
      </nav>
    """

    sidebar_html = render_sidebar(
        client_slug=client_slug,
        label=label,
        active_nav="event-tracking",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=_docs_enabled(),
        show_connectors=pflags["show_connectors"],
        view_nav_html=view_nav_html,
    )

    api_url       = f"/api/clients/{client_slug}/gtm/live-tags"
    api_refresh   = f"{api_url}?refresh=true"
    if access_key:
        api_url     = f"{api_url}?key={access_key}"
        api_refresh = f"{api_refresh}&key={access_key}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title_label} — Event Tracking</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04),0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    /* SIDEBAR_CSS (shared) covers .dash-sidebar* only — the flex-container
       rules that lay the sidebar and content side by side normally live in
       render_client_shell_page's own style block, which this standalone page
       doesn't use, so they're declared explicitly here instead. */
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1 1 auto; min-width: 0; }}
    {SIDEBAR_CSS}
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
    .tag-table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:.875rem; }}
    th {{ text-align:left; padding:10px 14px; font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); border-bottom:2px solid var(--line); white-space:nowrap; }}
    td {{ padding:10px 14px; border-bottom:1px solid var(--line); vertical-align:middle; }}
    td.td-top {{ vertical-align:top; }}
    tr:last-child td {{ border-bottom:0; }}
    tr:hover td {{ background:#f7fafc; }}
    .col-dot {{ width:28px; padding:0 8px 0 14px; text-align:center; }}
    .col-consent {{ width:32px; padding:0 6px; text-align:center; }}
    .tag-name {{ font-weight:600; font-size:.88rem; color:#0f172a; }}
    .badge {{ display:inline-flex; align-items:center; padding:3px 9px; border-radius:6px; font-size:.72rem; font-weight:700; white-space:nowrap; }}
    .badge-ga4    {{ background:#e8f0fe; color:#1a56db; }}
    .badge-gads   {{ background:#e6f4ea; color:#137333; }}
    .badge-gtag   {{ background:#e6f4ea; color:#137333; }}
    .badge-fb     {{ background:#ede9fe; color:#5b21b6; }}
    .badge-li     {{ background:#dbeafe; color:#1d4ed8; }}
    .badge-html   {{ background:#f3f4f6; color:#374151; }}
    .badge-img    {{ background:#fef3c7; color:#92400e; }}
    .badge-default {{ background:#f1f5f9; color:#475569; }}
    .status-dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; }}
    .status-dot.active {{ background:#16a34a; box-shadow:0 0 0 2px #dcfce7; }}
    .status-dot.paused {{ background:#94a3b8; box-shadow:0 0 0 2px #f1f5f9; }}
    .consent-icon {{ display:inline-flex; align-items:center; opacity:.9; }}
    .trigger-chip {{ display:inline-flex; align-items:center; padding:2px 8px; background:#f1f5f9; border-radius:5px; font-size:.74rem; color:#334155; margin:2px 3px 2px 0; white-space:nowrap; }}
    .criteria-list {{ display:flex; flex-direction:column; gap:4px; }}
    .criteria-row {{ display:flex; align-items:center; gap:5px; font-size:.77rem; flex-wrap:wrap; margin-bottom:3px; }}
    .criteria-row:last-child {{ margin-bottom:0; }}
    .criteria-var {{ font-family:monospace; font-size:.75rem; color:#1d4ed8; background:#eff6ff; padding:2px 7px; border-radius:4px; white-space:nowrap; }}
    .criteria-op {{ color:#64748b; font-size:.71rem; font-style:italic; white-space:nowrap; }}
    .criteria-val {{ font-family:monospace; font-size:.75rem; color:#166534; background:#f0fdf4; padding:2px 7px; border-radius:4px; word-break:break-all; max-width:200px; overflow:hidden; text-overflow:ellipsis; }}
    .loading-msg {{ padding:40px; text-align:center; color:var(--muted); }}
    .error-msg {{ padding:20px; color:var(--bad); background:#fef2f2; border-radius:9px; border:1px solid #fecaca; }}
    .empty-state {{ padding:48px 20px; text-align:center; color:var(--muted); }}
  </style>
</head>
<body>
  <div class="app-shell" id="appShell">
    {sidebar_html}

    <div class="dash-main">
      <main>
        <div class="page-head">
          <div>
            <h1 class="page-title">Event Tracking</h1>
            <p class="page-meta" id="pageMeta">Loading container…</p>
          </div>
          <button class="btn btn-secondary" id="refreshBtn" onclick="loadTags(true)">
            {_ICON_REFRESH}Refresh
          </button>
        </div>

        <div class="card">
          <div id="tableContainer" class="tag-table-wrap">
            <div class="loading-msg">Loading tags…</div>
          </div>
        </div>
      </main>
    </div>
  </div>

<script>{dashboard_topbar_js()}</script>
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

const SHIELD_SVG = {repr(_ICON_SHIELD)};

function renderCriteria(criteria) {{
  if (!criteria || !criteria.length) return '';
  return '<div class="criteria-list">' + criteria.map(c => {{
    const raw = c.variable || '';
    const v   = esc(raw.startsWith('{{{{') && raw.endsWith('}}}}') ? raw.slice(2, -2) : raw);
    const op  = esc(c.operator_label || c.operator || '');
    const val = esc(c.value || '');
    return `<div class="criteria-row"><span class="criteria-var">${{v}}</span><span class="criteria-op">${{op}}</span>${{val ? `<span class="criteria-val">${{val}}</span>` : ''}}</div>`;
  }}).join('') + '</div>';
}}

function renderTable(rows) {{
  if (!rows.length) {{
    return '<div class="empty-state">No tags found in this container.</div>';
  }}
  const ths = ['', 'Tag', '', 'Type', 'Triggers', 'Logic'];
  const head = '<tr>'
    + '<th class="col-dot"></th>'
    + '<th>Tag</th>'
    + '<th class="col-consent"></th>'
    + '<th>Type</th>'
    + '<th>Triggers</th>'
    + '<th>Logic</th>'
    + '</tr>';
  const body = rows.map(r => {{
    const dot = r.paused
      ? '<span class="status-dot paused" title="Paused"></span>'
      : '<span class="status-dot active" title="Active"></span>';
    const name = `<span class="tag-name">${{esc(r.tag_name || '')}}</span>`;
    const shield = r.consent_status === 'required'
      ? `<span class="consent-icon" title="Consent required before firing">${{SHIELD_SVG}}</span>`
      : '';
    const bc  = badgeClass(r.friendly_type || '');
    const typ = `<span class="badge ${{bc}}">${{esc(r.friendly_type || r.raw_type || 'Unknown')}}</span>`;
    const triggers = r.triggers || [];
    const chips = triggers.length
      ? triggers.map(t => `<span class="trigger-chip">${{esc(t.name)}}</span>`).join('')
      : '<span style="color:var(--muted);font-size:.8rem">—</span>';
    const allCriteria = triggers.flatMap(t => t.criteria || []);
    const logic = allCriteria.length
      ? renderCriteria(allCriteria)
      : '<span style="color:var(--muted);font-size:.8rem">—</span>';
    return `<tr>
      <td class="col-dot">${{dot}}</td>
      <td>${{name}}</td>
      <td class="col-consent">${{shield}}</td>
      <td>${{typ}}</td>
      <td class="td-top">${{chips}}</td>
      <td class="td-top">${{logic}}</td>
    </tr>`;
  }}).join('');
  return `<table><thead>${{head}}</thead><tbody>${{body}}</tbody></table>`;
}}

function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

async function loadTags(forceRefresh = false) {{
  const container = document.getElementById('tableContainer');
  const pageMeta  = document.getElementById('pageMeta');
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  container.innerHTML = '<div class="loading-msg">Loading tags…</div>';
  pageMeta.textContent = 'Loading…';
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
    const ver   = data.container_version ? `v${{data.container_version}}` : '';
    const fetched = data.fetched_at ? new Date(data.fetched_at).toLocaleString() : '';
    const parts = [
      `${{rows.length}} tags`,
      `${{active}} active`,
      ver,
      fetched ? `updated ${{fetched}}` : '',
    ].filter(Boolean);
    pageMeta.textContent = parts.join('  ·  ');
    container.innerHTML = renderTable(rows);
  }} catch(e) {{
    container.innerHTML = `<div class="error-msg">Network error: ${{esc(e.message)}}</div>`;
    pageMeta.textContent = 'Failed to load';
  }} finally {{
    btn.disabled = false;
  }}
}}

loadTags();
</script>
</body>
</html>"""


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()
