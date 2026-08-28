"""GTM live-tags audit page for any bigquery_nixon client (client_slug-scoped)."""

from __future__ import annotations

from dashboard.renderers.base_layout import render_client_shell_page

_ICON_SHIELD   = '<svg viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="1.8" width="15" height="15"><path d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.955 11.955 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" stroke-linecap="round" stroke-linejoin="round"/></svg>'
_ICON_REFRESH  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v5h-5"/></svg>'

# Everything is scoped under .evt-page so these generic inner names (.card, th,
# td, .badge, .tab…) can't leak into the shared chrome the shell provides.
_EXTRA_CSS = """
.evt-page { max-width:1320px; margin:0 auto; }
.evt-page .page-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:22px; }
.evt-page .page-title { font-size:1.6rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }
.evt-page .page-meta { font-size:.8rem; color:var(--muted); margin-top:4px; }
.evt-page .btn { display:inline-flex; align-items:center; gap:7px; padding:8px 14px; border-radius:10px; font-size:.85rem; font-weight:600; cursor:pointer; border:1px solid var(--border); background:var(--panel); color:var(--navy); transition:background .15s,border-color .15s,box-shadow .15s; text-decoration:none; }
.evt-page .btn:hover { background:#f4f8fd; border-color:#cdd9e8; box-shadow:0 1px 2px rgba(16,33,67,.06); }
.evt-page .btn:disabled { opacity:.6; cursor:default; }
.evt-page .btn svg { width:16px; height:16px; }
.evt-page .btn:disabled svg { animation:evt-spin .8s linear infinite; }
@keyframes evt-spin { to { transform:rotate(360deg); } }
.evt-page .card { background:var(--panel); border-radius:16px; border:1px solid var(--border); box-shadow:0 1px 2px rgba(16,33,67,.04),0 10px 30px -16px rgba(16,33,67,.16); overflow:hidden; }
.evt-page .tag-table-wrap { overflow-x:auto; }
.evt-page table { width:100%; border-collapse:collapse; font-size:.86rem; }
.evt-page th { text-align:left; padding:11px 16px; font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); background:var(--surface); border-bottom:1px solid var(--border); white-space:nowrap; }
.evt-page th.sortable { cursor:pointer; user-select:none; }
.evt-page th.sortable:hover { color:var(--accent); }
.evt-page th.sorted { color:var(--accent); }
.evt-page th .sort-arrow { font-size:.62rem; }
.evt-page td { padding:12px 16px; border-bottom:1px solid #eef2f7; vertical-align:middle; }
.evt-page td.td-top { vertical-align:top; }
.evt-page tr:last-child td { border-bottom:0; }
.evt-page tbody tr { transition:background .1s; }
.evt-page tr:hover td { background:#f7fafc; }
.evt-page .col-dot { width:30px; padding:0 6px 0 16px; text-align:center; }
.evt-page .col-consent { width:32px; padding:0 6px; text-align:center; }
.evt-page .tag-name { font-weight:600; font-size:.88rem; color:var(--text); }
.evt-page .badge { display:inline-flex; align-items:center; padding:3px 9px; border-radius:6px; font-size:.72rem; font-weight:700; white-space:nowrap; }
.evt-page .badge-ga4    { background:#e8f0fe; color:#1a56db; }
.evt-page .badge-gads   { background:#e6f4ea; color:#137333; }
.evt-page .badge-gtag   { background:#e6f4ea; color:#137333; }
.evt-page .badge-fb     { background:#ede9fe; color:#5b21b6; }
.evt-page .badge-li     { background:#dbeafe; color:#1d4ed8; }
.evt-page .badge-html   { background:#f3f4f6; color:#374151; }
.evt-page .badge-img    { background:#fef3c7; color:#92400e; }
.evt-page .badge-default { background:#f1f5f9; color:#475569; }
.evt-page .status-dot { display:inline-block; width:9px; height:9px; border-radius:50%; }
.evt-page .status-dot.active { background:#16a34a; box-shadow:0 0 0 2px #dcfce7; }
.evt-page .status-dot.paused { background:#94a3b8; box-shadow:0 0 0 2px #f1f5f9; }
.evt-page .consent-icon { display:inline-flex; align-items:center; opacity:.9; }
.evt-page .trigger-chip { display:inline-flex; align-items:center; padding:2px 8px; background:#f1f5f9; border-radius:5px; font-size:.74rem; color:#334155; margin:2px 3px 2px 0; white-space:nowrap; }
.evt-page .criteria-list { display:flex; flex-direction:column; gap:4px; }
.evt-page .criteria-row { display:flex; align-items:center; gap:5px; font-size:.77rem; flex-wrap:wrap; margin-bottom:3px; }
.evt-page .criteria-row:last-child { margin-bottom:0; }
.evt-page .criteria-var { font-family:monospace; font-size:.75rem; color:#1d4ed8; background:#eff6ff; padding:2px 7px; border-radius:4px; white-space:nowrap; }
.evt-page .criteria-op { color:var(--muted); font-size:.71rem; font-style:italic; white-space:nowrap; }
.evt-page .criteria-val { font-family:monospace; font-size:.75rem; color:#166534; background:#f0fdf4; padding:2px 7px; border-radius:4px; word-break:break-all; max-width:200px; overflow:hidden; text-overflow:ellipsis; }
.evt-page .loading-msg { padding:44px; text-align:center; color:var(--muted); font-size:.88rem; }
.evt-page .error-msg { margin:16px; padding:16px 18px; color:var(--err); background:var(--err-bg); border-radius:10px; border:1px solid #fecaca; font-size:.86rem; }
.evt-page .empty-state { padding:56px 24px; text-align:center; color:var(--muted); font-size:.86rem; line-height:1.5; }
.evt-page .empty-state .es-title { font-size:.98rem; font-weight:650; color:var(--navy); margin-bottom:5px; }
.evt-page .empty-state strong { color:var(--navy); font-weight:650; }
.evt-page .tabbar { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:14px 16px; border-bottom:1px solid var(--border); }
.evt-page .tabs { display:inline-flex; background:#eef2f7; border-radius:11px; padding:3px; gap:2px; }
.evt-page .tab { display:inline-flex; align-items:center; gap:8px; border:0; background:transparent; padding:7px 15px; border-radius:8px; font:inherit; font-size:.85rem; font-weight:600; color:var(--muted); cursor:pointer; transition:background .12s,color .12s,box-shadow .12s; }
.evt-page .tab:hover { color:var(--navy); }
.evt-page .tab.active { background:var(--panel); color:var(--navy); box-shadow:0 1px 2px rgba(16,33,67,.10); }
.evt-page .tab-count { font-size:.72rem; font-weight:700; min-width:20px; text-align:center; background:rgba(10,37,64,.08); color:var(--muted); padding:1px 7px; border-radius:999px; }
.evt-page .tab.active .tab-count { background:var(--accent); color:#fff; }
.evt-page .toolbar-note { font-size:.78rem; color:var(--muted); }
.evt-page .badge-ke { background:#dcfce7; color:#166534; }
.evt-page .event-name { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.8rem; color:var(--text); }
.evt-page .event-none { color:var(--muted); font-size:.8rem; }
.evt-page .col-ke { width:84px; text-align:center; white-space:nowrap; }
.evt-page .col-ke input { width:16px; height:16px; cursor:pointer; accent-color:var(--accent); }
.evt-page tr.is-key-event td { background:#f0fdf4; }
.evt-page tr.is-key-event:hover td { background:#e7f9ee; }
"""


def render_gtm_page(
    *,
    client_slug: str = "nixon-bq-test",
    label: str = "Nixon Medical",
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    api_url       = f"/api/clients/{client_slug}/gtm/live-tags"
    api_refresh   = f"{api_url}?refresh=true"
    if access_key:
        api_url       = f"{api_url}?key={access_key}"
        api_refresh   = f"{api_refresh}&key={access_key}"

    content = f"""
      <div class="evt-page">
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
      </div>

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
    """

    # Same shell as every other standalone client page (Lead Tracking, Email
    # Performance, Accessibility…), so this page gets the ONE canonical sidebar
    # instead of a hand-rolled nav of its own.
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="event-tracking",
        page_title="Event Tracking",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
    )
