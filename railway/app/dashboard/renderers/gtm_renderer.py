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
_ICON_REFRESH  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v5h-5"/></svg>'


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
        api_url       = f"{api_url}?key={access_key}"
        api_refresh   = f"{api_refresh}&key={access_key}"

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
    .page-head {{ display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:22px; }}
    .page-title {{ font-size:1.5rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }}
    .page-meta {{ font-size:.8rem; color:var(--muted); margin-top:4px; }}
    .btn {{ display:inline-flex; align-items:center; gap:7px; padding:8px 14px; border-radius:10px; font-size:.85rem; font-weight:600; cursor:pointer; border:1px solid var(--line); background:#fff; color:var(--navy); transition:background .15s,border-color .15s,box-shadow .15s; text-decoration:none; }}
    .btn:hover {{ background:#f4f8fd; border-color:#cdd9e8; box-shadow:0 1px 2px rgba(16,33,67,.06); }}
    .btn:disabled {{ opacity:.6; cursor:default; }}
    .btn svg {{ width:16px; height:16px; }}
    .btn:disabled svg {{ animation:spin .8s linear infinite; }}
    @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
    .card {{ background:var(--card); border-radius:16px; border:1px solid var(--line); box-shadow:0 1px 2px rgba(16,33,67,.04),0 10px 30px -16px rgba(16,33,67,.16); overflow:hidden; }}
    .tag-table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:.86rem; }}
    th {{ text-align:left; padding:11px 16px; font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); background:#f8fafc; border-bottom:1px solid var(--line); white-space:nowrap; }}
    th.sortable {{ cursor:pointer; user-select:none; }}
    th.sortable:hover {{ color:var(--accent); }}
    th.sorted {{ color:var(--accent); }}
    th .sort-arrow {{ font-size:.62rem; }}
    td {{ padding:12px 16px; border-bottom:1px solid #eef2f7; vertical-align:middle; }}
    td.td-top {{ vertical-align:top; }}
    tr:last-child td {{ border-bottom:0; }}
    tbody tr {{ transition:background .1s; }}
    tr:hover td {{ background:#f7fafc; }}
    .col-dot {{ width:30px; padding:0 6px 0 16px; text-align:center; }}
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
    .loading-msg {{ padding:44px; text-align:center; color:var(--muted); font-size:.88rem; }}
    .error-msg {{ margin:16px; padding:16px 18px; color:var(--bad); background:#fef2f2; border-radius:10px; border:1px solid #fecaca; font-size:.86rem; }}
    .empty-state {{ padding:56px 24px; text-align:center; color:var(--muted); font-size:.86rem; line-height:1.5; }}
    .empty-state .es-title {{ font-size:.98rem; font-weight:650; color:var(--navy); margin-bottom:5px; }}
    .empty-state strong {{ color:var(--navy); font-weight:650; }}
    .tabbar {{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:14px 16px; border-bottom:1px solid var(--line); }}
    .tabs {{ display:inline-flex; background:#eef2f7; border-radius:11px; padding:3px; gap:2px; }}
    .tab {{ display:inline-flex; align-items:center; gap:8px; border:0; background:transparent; padding:7px 15px; border-radius:8px; font:inherit; font-size:.85rem; font-weight:600; color:var(--muted); cursor:pointer; transition:background .12s,color .12s,box-shadow .12s; }}
    .tab:hover {{ color:var(--navy); }}
    .tab.active {{ background:#fff; color:var(--navy); box-shadow:0 1px 2px rgba(16,33,67,.10); }}
    .tab-count {{ font-size:.72rem; font-weight:700; min-width:20px; text-align:center; background:rgba(10,37,64,.08); color:var(--muted); padding:1px 7px; border-radius:999px; }}
    .tab.active .tab-count {{ background:var(--accent); color:#fff; }}
    .toolbar-note {{ font-size:.78rem; color:var(--muted); }}
    .badge-ke {{ background:#dcfce7; color:#166534; }}
    .event-name {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.8rem; color:#0f172a; }}
    .event-none {{ color:var(--muted); font-size:.8rem; }}
    .col-ke {{ width:84px; text-align:center; white-space:nowrap; }}
    .col-ke input {{ width:16px; height:16px; cursor:pointer; accent-color:var(--accent); }}
    tr.is-key-event td {{ background:#f0fdf4; }}
    tr.is-key-event:hover td {{ background:#e7f9ee; }}
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
          <button class="btn" id="refreshBtn" onclick="loadTags(true)">
            {_ICON_REFRESH}Refresh
          </button>
        </div>

        <div class="card">
          <div class="tabbar">
            <div class="tabs" id="viewTabs" role="tablist">
              <button type="button" class="tab active" data-view="key" role="tab">Key events<span class="tab-count" id="keyCount">0</span></button>
              <button type="button" class="tab" data-view="all" role="tab">All tags<span class="tab-count" id="allCount">0</span></button>
            </div>
            <span class="toolbar-note" id="filterNote"></span>
          </div>
          <div id="tableContainer" class="tag-table-wrap">
            <div class="loading-msg">Loading tags…</div>
          </div>
        </div>
      </main>
    </div>
  </div>

<script>{dashboard_topbar_js()}</script>
<script>
const API_URL      = {repr(api_url)};
const API_REFRESH  = {repr(api_refresh)};
// localStorage key is client-scoped so key-event marks don't leak across portals.
const KE_STORAGE_KEY = {repr(f"gtm_key_events_{client_slug}")};

// Shared state: all normalised tags, the current view, and the set of tag names
// the user has marked as key events (persisted locally in this browser). Land on
// the Key events tab if any are already marked, otherwise on All tags to curate.
let allRows = [];
let keyEventTags = loadKeyEventTags();
let viewMode = keyEventTags.size ? 'key' : 'all';
// Sort the table by the GA4 event column — on by default ('asc'). Click the
// "GA4 event" header to flip direction. Non-GA4 tags (no event) always sort last.
let eventSortDir = 'asc';
function sortRows(rows) {{
  if (!eventSortDir) return rows;
  const dir = eventSortDir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {{
    const ga = a.is_ga4_event ? 0 : 1, gb = b.is_ga4_event ? 0 : 1;
    if (ga !== gb) return ga - gb;                      // non-GA4 tags always last
    const ka = (a.event_name || '').toLowerCase(), kb = (b.event_name || '').toLowerCase();
    if (ka !== kb) return (ka < kb ? -1 : 1) * dir;
    return String(a.tag_name || '').localeCompare(String(b.tag_name || ''));
  }});
}}

function loadKeyEventTags() {{
  try {{ const s = localStorage.getItem(KE_STORAGE_KEY); return new Set(s ? JSON.parse(s) : []); }}
  catch (e) {{ return new Set(); }}
}}
function saveKeyEventTags() {{
  try {{ localStorage.setItem(KE_STORAGE_KEY, JSON.stringify([...keyEventTags])); }} catch (e) {{}}
}}

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
    return '<div class="empty-state">No tags match this view.</div>';
  }}
  const head = '<tr>'
    + '<th class="col-dot"></th>'
    + '<th class="col-ke">Key event</th>'
    + '<th>Tag</th>'
    + '<th class="col-consent"></th>'
    + '<th>Type</th>'
    + `<th class="col-event sortable${{eventSortDir ? ' sorted' : ''}}" data-sort="event" title="Sort by GA4 event">GA4 event${{eventSortDir === 'asc' ? ' <span class="sort-arrow">▲</span>' : eventSortDir === 'desc' ? ' <span class="sort-arrow">▼</span>' : ''}}</th>`
    + '<th>Triggers</th>'
    + '<th>Logic</th>'
    + '</tr>';
  const body = rows.map(r => {{
    const dot = r.paused
      ? '<span class="status-dot paused" title="Paused"></span>'
      : '<span class="status-dot active" title="Active"></span>';
    const isKey = r.is_ga4_event && keyEventTags.has(r.tag_name);
    // Only GA4 event tags can be marked as key events.
    const keCell = r.is_ga4_event
      ? `<input type="checkbox" data-ke-tag="${{esc(r.tag_name || '')}}"${{isKey ? ' checked' : ''}} aria-label="Mark as key event">`
      : '';
    const name = `<span class="tag-name">${{esc(r.tag_name || '')}}</span>`;
    const shield = r.consent_status === 'required'
      ? `<span class="consent-icon" title="Consent required before firing">${{SHIELD_SVG}}</span>`
      : '';
    const bc  = badgeClass(r.friendly_type || '');
    const typ = `<span class="badge ${{bc}}">${{esc(r.friendly_type || r.raw_type || 'Unknown')}}</span>`;
    let evt = '<span class="event-none">—</span>';
    if (r.is_ga4_event) {{
      const en = r.event_name || '';
      const keBadge = isKey ? ' <span class="badge badge-ke" title="Marked as a key event">key event</span>' : '';
      evt = en ? `<span class="event-name">${{esc(en)}}</span>${{keBadge}}` : '<span class="event-none">(no event name)</span>';
    }}
    const triggers = r.triggers || [];
    const chips = triggers.length
      ? triggers.map(t => `<span class="trigger-chip">${{esc(t.name)}}</span>`).join('')
      : '<span style="color:var(--muted);font-size:.8rem">—</span>';
    const allCriteria = triggers.flatMap(t => t.criteria || []);
    const logic = allCriteria.length
      ? renderCriteria(allCriteria)
      : '<span style="color:var(--muted);font-size:.8rem">—</span>';
    return `<tr class="${{isKey ? 'is-key-event' : ''}}">
      <td class="col-dot">${{dot}}</td>
      <td class="col-ke">${{keCell}}</td>
      <td>${{name}}</td>
      <td class="col-consent">${{shield}}</td>
      <td>${{typ}}</td>
      <td class="td-top">${{evt}}</td>
      <td class="td-top">${{chips}}</td>
      <td class="td-top">${{logic}}</td>
    </tr>`;
  }}).join('');
  return `<table><thead>${{head}}</thead><tbody>${{body}}</tbody></table>`;
}}

// Key events tab = the GA4 event tags the user has checked; All tags = everything.
function currentRows() {{
  return viewMode === 'key'
    ? allRows.filter(r => r.is_ga4_event && keyEventTags.has(r.tag_name))
    : allRows;
}}
function emptyState() {{
  if (viewMode === 'key') {{
    return `<div class="empty-state"><div class="es-title">No key events yet</div>Open <strong>All tags</strong> and check the GA4 event tags that count as key events — they'll collect here.</div>`;
  }}
  return `<div class="empty-state"><div class="es-title">No tags found</div>This GTM container has no tags.</div>`;
}}
function applyView() {{
  const rows = sortRows(currentRows());
  document.getElementById('tableContainer').innerHTML = rows.length ? renderTable(rows) : emptyState();
  syncTabs();
}}

// Click the "GA4 event" header to flip sort direction (delegated so it survives
// table re-renders).
document.getElementById('tableContainer').addEventListener('click', ev => {{
  const th = ev.target.closest('th[data-sort="event"]');
  if (!th) return;
  eventSortDir = eventSortDir === 'asc' ? 'desc' : 'asc';
  applyView();
}});
function syncTabs() {{
  const keyCount = allRows.filter(r => r.is_ga4_event && keyEventTags.has(r.tag_name)).length;
  const kc = document.getElementById('keyCount'); if (kc) kc.textContent = keyCount;
  const ac = document.getElementById('allCount'); if (ac) ac.textContent = allRows.length;
  document.querySelectorAll('#viewTabs .tab').forEach(b => b.classList.toggle('active', b.dataset.view === viewMode));
  const note = document.getElementById('filterNote');
  if (note) note.textContent = viewMode === 'key'
    ? (keyCount ? `${{keyCount}} key event${{keyCount === 1 ? '' : 's'}}` : '')
    : 'Check the GA4 event tags that count as key events';
}}

document.getElementById('viewTabs').addEventListener('click', ev => {{
  const btn = ev.target.closest('[data-view]');
  if (!btn) return;
  viewMode = btn.dataset.view;
  applyView();
}});

// Delegated so it survives table re-renders: toggle a tag's key-event mark.
document.getElementById('tableContainer').addEventListener('change', ev => {{
  const cb = ev.target.closest('input[data-ke-tag]');
  if (!cb) return;
  const name = cb.dataset.keTag;
  if (cb.checked) keyEventTags.add(name); else keyEventTags.delete(name);
  saveKeyEventTags();
  // On the Key events tab, an unchecked tag should drop out of the list.
  if (viewMode === 'key' && !cb.checked) {{ applyView(); return; }}
  const row = cb.closest('tr');
  if (row) {{
    row.classList.toggle('is-key-event', cb.checked);
    const evtCell = row.children[5];  // "GA4 event" column
    if (evtCell) {{
      const existing = evtCell.querySelector('.badge-ke');
      if (cb.checked && !existing) {{
        evtCell.insertAdjacentHTML('beforeend', ' <span class="badge badge-ke" title="Marked as a key event">key event</span>');
      }} else if (!cb.checked && existing) {{
        existing.remove();
      }}
    }}
  }}
  syncTabs();
}});

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
    allRows = data.rows || [];
    const active = allRows.filter(r => !r.paused).length;
    const ver   = data.container_version ? `v${{data.container_version}}` : '';
    const fetched = data.fetched_at ? new Date(data.fetched_at).toLocaleString() : '';
    const parts = [
      `${{allRows.length}} tags`,
      `${{active}} active`,
      ver,
      fetched ? `updated ${{fetched}}` : '',
      // Tag Manager's per-project quota is 0.25 req/s, so a refresh can be
      // throttled. We serve the last audit rather than an error — say so, so
      // "updated <time>" isn't read as "this is the container right now".
      data.stale ? 'cached (Tag Manager rate limit — retry shortly)' : '',
    ].filter(Boolean);
    pageMeta.textContent = parts.join('  ·  ');
    applyView();
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
