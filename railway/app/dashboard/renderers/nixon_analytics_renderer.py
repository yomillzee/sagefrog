"""Nixon website analytics page — GA4 data from analytics_test."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    dashboard_topbar_js,
    favicon_head_html,
    platform_nav_flags,
    render_sidebar,
)


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()


def render_nixon_analytics_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=30)

    _ICON_PAGES    = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>'
    _ICON_TRAFFIC  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>'
    _ICON_AUDIENCE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="7" r="4"/><path d="M3 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/><circle cx="17" cy="7" r="3" opacity=".6"/><path d="M21 21v-2a3 3 0 0 0-2-2.83" opacity=".6"/></svg>'
    _ICON_LANDING  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 19V5m-7 7 7-7 7 7"/></svg>'
    _ICON_BACK     = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>'
    _ICON_TAGS     = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>'
    _ICON_LEADS    = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>'

    main_url = _api_url("/dashboard/nixon-bq-test", access_key=access_key)

    pflags = platform_nav_flags("nixon-bq-test")
    lead_tracking_link = ""
    if pflags.get("show_lead_tracking"):
        lead_tracking_url = _api_url("/dashboard/nixon-bq-test/lead-tracking", access_key=access_key)
        lead_tracking_link = (
            f'<a class="dash-view-btn" href="{lead_tracking_url}">'
            f'{_ICON_LEADS}<span>Lead Tracking</span></a>'
        )

    # The "Paid Media" back-link and the section anchors below are page-specific
    # navigation content passed straight through to the shared sidebar via
    # view_nav_html — render_sidebar() inserts this raw, so it must supply its
    # own <nav class="dash-sidebar-nav"> wrapper (matching the CSS/scrollspy
    # selector `.dash-sidebar-nav .dash-view-btn`).
    view_nav_html = f"""
      <nav class="dash-sidebar-nav" aria-label="Sections">
        <a class="dash-view-btn" href="{main_url}">{_ICON_BACK}<span>Paid Media</span></a>
        <a class="dash-view-btn active" href="#sec-pages"   data-nav="sec-pages">{_ICON_PAGES}<span>Top Pages</span></a>
        <a class="dash-view-btn"        href="#sec-traffic"  data-nav="sec-traffic">{_ICON_TRAFFIC}<span>Traffic</span></a>
        <a class="dash-view-btn"        href="#sec-audience" data-nav="sec-audience">{_ICON_AUDIENCE}<span>Audience</span></a>
        <a class="dash-view-btn"        href="#sec-landing"  data-nav="sec-landing">{_ICON_LANDING}<span>Landing Pages</span></a>
        {lead_tracking_link}
        <a class="dash-view-btn"        href="/dashboard/nixon-bq-test/gtm{('?key=' + access_key) if access_key else ''}" style="margin-top:8px;border-top:1px solid rgba(255,255,255,.1);padding-top:14px">{_ICON_TAGS}<span>Event Tracking</span></a>
      </nav>
    """

    sidebar_html = render_sidebar(
        client_slug="nixon-bq-test",
        label="Nixon Medical",
        active_nav="website-analytics",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=_docs_enabled(),
        show_connectors=pflags["show_connectors"],
        view_nav_html=view_nav_html,
    )

    admin_class = "is-admin" if session_is_admin else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nixon — Website Analytics</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    {SIDEBAR_CSS}
    .debug-only, .src-note, .raw-json {{ display:none; }}
    .is-admin .debug-only, .is-admin .src-note, .is-admin .raw-json {{ display:block; }}
    .page-head {{ margin-bottom:24px; }}
    .src-note {{ margin:0 0 14px; font-size:.72rem; color:var(--muted); }}
    .src-note code {{ background:#eef4fb; padding:1px 6px; border-radius:5px; font-size:.7rem; color:#33506f; }}
    .src-note .arrow {{ color:#9aa7bd; margin:0 4px; }}
    main {{ max-width:1320px; margin:0 auto; padding:30px 28px 56px; }}
    h1 {{ margin:0; color:var(--navy); font-size:1.5rem; font-weight:800; letter-spacing:-.01em; }}
    h2 {{ margin:0; color:var(--navy); font-size:1.1rem; font-weight:750; letter-spacing:-.005em; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    .page-head p {{ font-size:.9rem; }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:14px; align-items:end; margin-bottom:14px; }}
    label {{ display:grid; gap:6px; color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; }}
    input {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:9px 12px; font:inherit; background:#fff; color:#102033; }}
    input:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:10px 16px; background:var(--accent); color:#fff; font-weight:700; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover {{ background:#1a62b8; }}
    button.primary:disabled {{ opacity:.55; cursor:default; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow); }}
    section > h2 {{ margin-bottom:14px; }}
    .status {{ color:var(--muted); font-size:.84rem; margin:0 0 14px; }}
    .status.error {{ color:var(--bad); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line-soft); border-radius:var(--radius-sm); }}
    table {{ border-collapse:collapse; width:100%; min-width:600px; font-size:.86rem; }}
    th,td {{ padding:11px 13px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    tbody tr:hover td {{ background:#f7faff; }}
    th {{ background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.68rem; letter-spacing:.04em; font-weight:800; position:sticky; top:0; }}
    th.left,td.left {{ text-align:left; }}
    .empty {{ color:var(--muted); padding:26px; text-align:center; }}
    details.raw-json {{ margin-top:14px; }}
    summary {{ color:var(--accent); cursor:pointer; font-weight:700; font-size:.82rem; }}
    pre {{ max-height:360px; overflow:auto; background:#0b1020; color:#d7e3ff; border-radius:var(--radius-sm); padding:12px; font-size:.78rem; margin-top:10px; }}
    code {{ background:#eef4fb; padding:2px 5px; border-radius:4px; }}
    .muted {{ color:var(--muted); font-size:.78rem; margin-left:6px; }}
    .page-path {{ font-weight:600; color:#1f2d40; word-break:break-all; }}
    table.compact {{ min-width:0; font-size:.84rem; }}
    table.compact th, table.compact td {{ padding:8px 12px; }}
    .pager {{ display:flex; align-items:center; justify-content:flex-end; gap:12px; margin-top:12px; }}
    .pager-btn {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:var(--radius-sm); padding:6px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s; }}
    .pager-btn:hover:not(:disabled) {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .pager-btn:disabled {{ opacity:.45; cursor:default; }}
    .pager-info {{ font-size:.8rem; color:var(--muted); font-weight:600; }}
    .filter-row {{ display:flex; flex-wrap:wrap; gap:20px; margin-bottom:12px; align-items:center; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    .filter-label {{ color:var(--muted); font-size:.74rem; font-weight:800; text-transform:uppercase; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:5px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s, color .12s; }}
    .chip:hover {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
    .chip.active:hover {{ background:#0d2c4d; }}
    .bar-row {{ display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid var(--line-soft); }}
    .bar-row:last-child {{ border-bottom:0; }}
    .bar-label {{ flex:0 0 160px; font-size:.84rem; color:var(--navy); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .bar-track {{ flex:1 1 auto; height:8px; background:var(--line-soft); border-radius:4px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:var(--accent); border-radius:4px; transition:width .3s; }}
    .bar-count {{ flex:0 0 100px; font-size:.82rem; color:var(--navy); text-align:right; font-variant-numeric:tabular-nums; }}
    .bar-pct {{ color:var(--muted); font-size:.76rem; margin-left:4px; }}
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:14px; }}
    .col-panel {{ border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:14px 16px; }}
    .col-panel h3 {{ margin:0 0 12px; font-size:.88rem; font-weight:750; color:var(--navy); }}
    .subsec-h3 {{ margin:16px 0 10px; font-size:.88rem; font-weight:750; color:var(--navy); }}
    .trend-sm-svg {{ width:100%; height:130px; display:block; }}
    @media (max-width:900px) {{ .two-col {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    {sidebar_html}
    <div class="dash-main">
  <main>
    <div class="page-head">
      <h1>Nixon Medical — Website Analytics</h1>
    </div>
    <form class="toolbar" id="filters">
      <label>Start date<input id="startDate" type="date" value="{start.isoformat()}"></label>
      <label>End date<input id="endDate" type="date" value="{end.isoformat()}"></label>
      <button class="primary" type="submit">Refetch</button>
    </form>
    <div class="filter-row" id="datePresetsRow">
      <div class="filter-group">
        <span class="filter-label">Quick range</span>
        <div class="chips" id="datePresets">
          <button type="button" class="chip" data-preset="this_month">This month</button>
          <button type="button" class="chip" data-preset="last_month">Last month</button>
          <button type="button" class="chip" data-preset="last_7">Last 7 days</button>
          <button type="button" class="chip" data-preset="last_30">Last 30 days</button>
          <button type="button" class="chip" data-preset="last_90">Last 90 days</button>
        </div>
      </div>
    </div>

    <section id="sec-pages">
      <h2>Top pages</h2>
      <p class="src-note"><code>GET .../pages/top</code><span class="arrow">→</span><code>vw_page_path_daily</code> · <code>GET .../pages/sources</code><span class="arrow">→</span><code>vw_page_path_source_daily</code></p>
      <div class="filter-row" id="pageFilters">
        <div class="filter-group">
          <span class="filter-label">AI platform</span>
          <div class="chips" id="aiChips"></div>
        </div>
        <div class="filter-group">
          <span class="filter-label">Paid source</span>
          <div class="chips" id="sourceChips"></div>
        </div>
      </div>
      <div class="status" id="pagesStatus">Waiting…</div>
      <div class="table-wrap"><table id="pagesTable" class="compact"></table></div>
      <div class="pager" id="pagesPager"></div>
      <details class="raw-json"><summary>Raw page JSON</summary><pre id="pagesJson">{{}}</pre></details>
    </section>

    <section id="sec-traffic">
      <h2>Traffic overview</h2>
      <p class="src-note"><code>GET .../pages/traffic-acquisition</code><span class="arrow">→</span><code>analytics_test.ga4_TrafficAcquisition_*</code></p>
      <div class="status" id="trafficAcqStatus">Waiting…</div>
      <div class="two-col">
        <div class="col-panel">
          <h3>Sessions over time</h3>
          <svg id="sessionsTrendChart" class="trend-sm-svg" preserveAspectRatio="none"></svg>
        </div>
        <div class="col-panel">
          <h3>By channel</h3>
          <div id="channelBars"></div>
        </div>
      </div>
      <h3 class="subsec-h3">Top sources / medium</h3>
      <div class="table-wrap"><table id="sourcesTable" class="compact"></table></div>
      <details class="raw-json"><summary>Raw traffic JSON</summary><pre id="trafficAcqJson">{{}}</pre></details>
    </section>

    <section id="sec-audience">
      <h2>Audience</h2>
      <p class="src-note"><code>GET .../pages/device-split</code><span class="arrow">→</span><code>analytics_test.ga4_TechDetails_*</code></p>
      <div class="status" id="deviceStatus">Waiting…</div>
      <div class="col-panel" style="max-width:440px">
        <h3>Device type</h3>
        <div id="deviceBars"></div>
      </div>
      <details class="raw-json"><summary>Raw device JSON</summary><pre id="deviceJson">{{}}</pre></details>
    </section>

    <section id="sec-landing">
      <h2>Landing pages</h2>
      <p class="src-note"><code>GET .../pages/landing</code><span class="arrow">→</span><code>analytics_test.ga4_LandingPage_*</code></p>
      <div class="status" id="landingStatus">Waiting…</div>
      <div class="table-wrap"><table id="landingTable" class="compact"></table></div>
      <div class="pager" id="landingPager"></div>
      <details class="raw-json"><summary>Raw landing JSON</summary><pre id="landingJson">{{}}</pre></details>
    </section>
  </main>
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
  <script>
    const PAGES_TOP_API      = "{_api_url('/api/clients/nixon/pages/top', access_key=access_key)}";
    const PAGES_SOURCES_API  = "{_api_url('/api/clients/nixon/pages/sources', access_key=access_key)}";
    const TRAFFIC_ACQ_API    = "{_api_url('/api/clients/nixon/pages/traffic-acquisition', access_key=access_key)}";
    const DEVICE_SPLIT_API   = "{_api_url('/api/clients/nixon/pages/device-split', access_key=access_key)}";
    const LANDING_PAGES_API  = "{_api_url('/api/clients/nixon/pages/landing', access_key=access_key)}";
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const num = v => Number(v || 0);
    const nums = new Intl.NumberFormat('en-US');
    const count = v => v == null ? '—' : nums.format(Math.round(num(v)));
    function setStatus(id, msg, isErr) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = msg;
      el.className = 'status' + (isErr ? ' error' : '');
      el.style.display = msg ? '' : 'none';
    }}
    function setRaw(id, data) {{
      const el = document.getElementById(id);
      if (el) el.textContent = JSON.stringify(data, null, 2);
    }}
    async function getJson(url) {{
      const r = await fetch(url, {{ credentials: 'same-origin' }});
      if (!r.ok) {{ const b = await r.json().catch(() => ({{}})); throw new Error((b && (b.detail && b.detail.error || b.detail)) || r.statusText); }}
      return r.json();
    }}
    function withDates(base) {{
      const s = document.getElementById('startDate').value;
      const e = document.getElementById('endDate').value;
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + s + '&end_date=' + e;
    }}
    function renderTable(tableId, columns, rows, emptyText) {{
      const el = document.getElementById(tableId);
      if (!rows || !rows.length) {{
        el.innerHTML = `<tbody><tr><td class="empty">${{esc(emptyText)}}</td></tr></tbody>`;
        return;
      }}
      el.innerHTML = `<thead><tr>${{columns.map(c => `<th class="${{c.left ? 'left' : ''}}">${{esc(c.label)}}</th>`).join('')}}</tr></thead>` +
        `<tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td class="${{c.left ? 'left' : ''}}">${{esc(c.format ? c.format(row[c.key], row) : row[c.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    function fmtDuration(secs) {{
      secs = Math.round(num(secs));
      if (secs < 60) return secs + 's';
      const m = Math.floor(secs / 60), s = secs % 60;
      return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
    }}
    // ---- Top pages ----
    let pagesTopRows = [];
    let pagesSourceRows = [];
    const paidSourceFilter = new Set();
    const aiPlatformFilter = new Set();
    const PAGES_PER_PAGE = 10;
    let pagesPageNum = 1;
    const PAID_SOURCE_LABELS = {{ paid_google:'Google', paid_bing:'Bing', paid_linkedin:'LinkedIn', paid_meta:'Meta', paid_facebook:'Facebook' }};
    function paidLabel(src) {{
      return PAID_SOURCE_LABELS[src] || String(src).replace(/^paid_/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }}
    function pageFiltersActive() {{ return paidSourceFilter.size > 0 || aiPlatformFilter.size > 0; }}
    function pageSourceRowMatches(r) {{
      if (aiPlatformFilter.size && !aiPlatformFilter.has(r.ai_platform)) return false;
      if (paidSourceFilter.size && !paidSourceFilter.has(r.source_platform)) return false;
      return true;
    }}
    function aggregatePages(rows) {{
      const map = new Map();
      for (const r of rows) {{
        let g = map.get(r.page_path);
        if (!g) {{ g = {{ page_path:r.page_path, page_group:r.page_group, page_topic:r.page_topic, page_views:0, users:0, sessions:0, engagement_seconds:0, key_events:0 }}; map.set(r.page_path, g); }}
        g.page_views += num(r.page_views); g.users += num(r.users); g.sessions += num(r.sessions); g.engagement_seconds += num(r.engagement_seconds); g.key_events += num(r.key_events);
      }}
      return [...map.values()].sort((a, b) => b.page_views - a.page_views);
    }}
    function renderPages() {{
      const base = pageFiltersActive() ? aggregatePages(pagesSourceRows.filter(pageSourceRowMatches)) : pagesTopRows;
      const el = document.getElementById('pagesTable');
      if (!base.length) {{
        el.innerHTML = `<tbody><tr><td class="empty">No pages for this range / filter.</td></tr></tbody>`;
        setStatus('pagesStatus', 'No pages for this range / filter.');
        document.getElementById('pagesPager').innerHTML = '';
        return;
      }}
      const totalPages = Math.max(1, Math.ceil(base.length / PAGES_PER_PAGE));
      if (pagesPageNum > totalPages) pagesPageNum = totalPages;
      const startIdx = (pagesPageNum - 1) * PAGES_PER_PAGE;
      const pageRows = base.slice(startIdx, startIdx + PAGES_PER_PAGE);
      const head = `<thead><tr><th class="left">Page</th><th>Views</th><th>Users</th><th>Key events</th><th>Avg engt</th></tr></thead>`;
      const body = pageRows.map(p => {{
        const sub = p.page_group ? ` <span class="muted">${{esc(p.page_group)}}</span>` : '';
        const engt = p.users ? p.engagement_seconds / p.users : 0;
        return `<tr><td class="left"><span class="page-path">${{esc(p.page_path)}}</span>${{sub}}</td><td>${{count(p.page_views)}}</td><td>${{count(p.users)}}</td><td>${{count(p.key_events)}}</td><td>${{fmtDuration(engt)}}</td></tr>`;
      }}).join('');
      el.innerHTML = head + `<tbody>${{body}}</tbody>`;
      setStatus('pagesStatus', `${{startIdx + 1}}–${{startIdx + pageRows.length}} of ${{base.length}} page(s)` + (pageFiltersActive() ? ' (filtered)' : '') + '.');
      renderPagesPager(totalPages);
    }}
    function renderPagesPager(totalPages) {{
      const pager = document.getElementById('pagesPager');
      if (totalPages <= 1) {{ pager.innerHTML = ''; return; }}
      pager.innerHTML =
        `<button type="button" class="pager-btn" id="pagesPrev"${{pagesPageNum <= 1 ? ' disabled' : ''}}>‹ Prev</button>` +
        `<span class="pager-info">Page ${{pagesPageNum}} of ${{totalPages}}</span>` +
        `<button type="button" class="pager-btn" id="pagesNext"${{pagesPageNum >= totalPages ? ' disabled' : ''}}>Next ›</button>`;
      const prev = document.getElementById('pagesPrev');
      const next = document.getElementById('pagesNext');
      if (prev) prev.onclick = () => {{ if (pagesPageNum > 1) {{ pagesPageNum--; renderPages(); }} }};
      if (next) next.onclick = () => {{ if (pagesPageNum < totalPages) {{ pagesPageNum++; renderPages(); }} }};
    }}
    function buildMultiChips(containerId, entries, stateSet) {{
      const el = document.getElementById(containerId);
      el.innerHTML = [['__all__', 'All'], ...entries].map(([v, l]) => `<button type="button" class="chip" data-key="${{esc(v)}}">${{esc(l)}}</button>`).join('');
      const sync = () => el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key === '__all__' ? stateSet.size === 0 : stateSet.has(b.dataset.key)));
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key = btn.dataset.key;
        if (key === '__all__') stateSet.clear();
        else if (stateSet.has(key)) stateSet.delete(key);
        else stateSet.add(key);
        sync(); pagesPageNum = 1; renderPages();
      }}));
      sync();
    }}
    function buildPageFilters() {{
      const aiPlatforms = [...new Set(pagesSourceRows.map(r => r.ai_platform).filter(Boolean))].sort();
      buildMultiChips('aiChips', aiPlatforms.map(p => [p, p]), aiPlatformFilter);
      const paidSources = [...new Set(pagesSourceRows.map(r => r.source_platform).filter(s => s && s.startsWith('paid_')))].sort();
      buildMultiChips('sourceChips', paidSources.map(s => [s, paidLabel(s)]), paidSourceFilter);
    }}
    async function loadPages() {{
      setStatus('pagesStatus', 'Loading page performance...');
      const [top, src] = await Promise.all([
        getJson(withDates(PAGES_TOP_API)).catch(() => ({{ rows: [] }})),
        getJson(withDates(PAGES_SOURCES_API)).catch(() => ({{ rows: [] }})),
      ]);
      pagesTopRows = top.rows || [];
      pagesSourceRows = src.rows || [];
      pagesPageNum = 1;
      buildPageFilters();
      renderPages();
      setRaw('pagesJson', {{ top, sources: src }});
    }}
    // ---- Traffic acquisition ----
    function pctBar(pct) {{
      return `<div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100, pct).toFixed(1)}}%"></div></div>`;
    }}
    function drawSessionsTrend(daily) {{
      const svg = document.getElementById('sessionsTrendChart');
      const W = 800, H = 130, padL = 10, padR = 10, padT = 8, padB = 24;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const n = daily.length;
      svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
      if (!n) {{ svg.innerHTML = ''; return; }}
      const vals = daily.map(d => num(d.sessions));
      const mn = Math.min(...vals), mx = Math.max(...vals), span = (mx - mn) || 1;
      const xAt = i => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
      const yAt = v => padT + (1 - (v - mn) / span) * plotH;
      const pts = vals.map((v, i) => `${{xAt(i).toFixed(1)}},${{yAt(v).toFixed(1)}}`).join(' ');
      const fillPts = `${{padL}},${{padT + plotH}} ${{pts}} ${{(padL + plotW).toFixed(1)}},${{padT + plotH}}`;
      const lblIdx = n === 1 ? [0] : [0, Math.floor((n - 1) / 2), n - 1];
      svg.innerHTML = [
        `<line x1="${{padL}}" y1="${{padT}}" x2="${{padL}}" y2="${{padT + plotH}}" stroke="#eef2f7"/>`,
        `<line x1="${{padL}}" y1="${{padT + plotH}}" x2="${{padL + plotW}}" y2="${{padT + plotH}}" stroke="#e3e9f1"/>`,
        `<polygon fill="rgba(29,111,208,.1)" points="${{fillPts}}"/>`,
        `<polyline fill="none" stroke="#1d6fd0" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${{pts}}"/>`,
        ...lblIdx.map(i => {{
          const anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
          return `<text x="${{xAt(i).toFixed(1)}}" y="${{H - 5}}" font-size="10" fill="#66758f" text-anchor="${{anchor}}">${{esc(String(daily[i].date).slice(5))}}</text>`;
        }}),
      ].join('');
    }}
    function renderChannels(rows) {{
      const el = document.getElementById('channelBars');
      if (!rows || !rows.length) {{ el.innerHTML = '<div class="empty">No data.</div>'; return; }}
      const total = rows.reduce((s, r) => s + num(r.sessions), 0);
      el.innerHTML = rows.map(r => {{
        const pct = total ? num(r.sessions) / total * 100 : 0;
        return `<div class="bar-row"><div class="bar-label">${{esc(r.channel)}}</div>${{pctBar(pct)}}<div class="bar-count">${{count(r.sessions)}}<span class="bar-pct">${{pct.toFixed(0)}}%</span></div></div>`;
      }}).join('');
    }}
    async function loadTrafficAcq() {{
      setStatus('trafficAcqStatus', 'Loading traffic data...');
      try {{
        const payload = await getJson(withDates(TRAFFIC_ACQ_API));
        renderChannels(payload.by_channel || []);
        drawSessionsTrend(payload.daily || []);
        renderTable('sourcesTable', [
          {{ key:'source', label:'Source', left:true }},
          {{ key:'medium', label:'Medium', left:true }},
          {{ key:'sessions', label:'Sessions', format:count }},
          {{ key:'engaged_sessions', label:'Engaged', format:count }},
          {{ key:'engagement_rate', label:'Eng. rate', format:v => v != null ? v + '%' : '—' }},
          {{ key:'key_events', label:'Key events', format:count }},
        ], payload.by_source || [], 'No source data.');
        setStatus('trafficAcqStatus', '');
        setRaw('trafficAcqJson', payload);
      }} catch (err) {{
        setStatus('trafficAcqStatus', err.message || String(err), true);
      }}
    }}
    // ---- Device split ----
    function renderDeviceSplit(rows) {{
      const el = document.getElementById('deviceBars');
      if (!rows || !rows.length) {{ el.innerHTML = '<div class="empty">No data.</div>'; return; }}
      const total = rows.reduce((s, r) => s + num(r.users), 0);
      el.innerHTML = rows.map(r => {{
        const pct = total ? num(r.users) / total * 100 : 0;
        return `<div class="bar-row"><div class="bar-label">${{esc(r.device)}}</div>${{pctBar(pct)}}<div class="bar-count">${{count(r.users)}}<span class="bar-pct">${{pct.toFixed(0)}}%</span></div></div>`;
      }}).join('');
    }}
    async function loadDeviceSplit() {{
      setStatus('deviceStatus', 'Loading device data...');
      try {{
        const payload = await getJson(withDates(DEVICE_SPLIT_API));
        renderDeviceSplit(payload.rows || []);
        setStatus('deviceStatus', '');
        setRaw('deviceJson', payload);
      }} catch (err) {{
        setStatus('deviceStatus', err.message || String(err), true);
      }}
    }}
    // ---- Landing pages ----
    const LANDING_PER_PAGE = 15;
    let landingPageNum = 1;
    let landingRows = [];
    function renderLanding() {{
      const totalPages = Math.max(1, Math.ceil(landingRows.length / LANDING_PER_PAGE));
      if (landingPageNum > totalPages) landingPageNum = totalPages;
      const startIdx = (landingPageNum - 1) * LANDING_PER_PAGE;
      const rows = landingRows.slice(startIdx, startIdx + LANDING_PER_PAGE);
      const el = document.getElementById('landingTable');
      if (!rows.length) {{
        el.innerHTML = `<tbody><tr><td class="empty">No landing page data for this range.</td></tr></tbody>`;
        document.getElementById('landingPager').innerHTML = '';
        return;
      }}
      const head = `<thead><tr><th class="left">Landing page</th><th>Sessions</th><th>Users</th><th>New users</th><th>Key events</th><th>KE rate</th><th>Avg engt</th></tr></thead>`;
      const body = rows.map(r =>
        `<tr><td class="left"><span class="page-path">${{esc(r.page_path)}}</span></td><td>${{count(r.sessions)}}</td><td>${{count(r.users)}}</td><td>${{count(r.new_users)}}</td><td>${{count(r.key_events)}}</td><td>${{r.key_event_rate != null ? r.key_event_rate + '%' : '—'}}</td><td>${{fmtDuration(r.avg_engagement_seconds)}}</td></tr>`
      ).join('');
      el.innerHTML = head + `<tbody>${{body}}</tbody>`;
      setStatus('landingStatus', `${{startIdx + 1}}–${{startIdx + rows.length}} of ${{landingRows.length}} page(s).`);
      renderLandingPager(totalPages);
    }}
    function renderLandingPager(totalPages) {{
      const pager = document.getElementById('landingPager');
      if (totalPages <= 1) {{ pager.innerHTML = ''; return; }}
      pager.innerHTML =
        `<button type="button" class="pager-btn" id="landingPrev"${{landingPageNum <= 1 ? ' disabled' : ''}}>‹ Prev</button>` +
        `<span class="pager-info">Page ${{landingPageNum}} of ${{totalPages}}</span>` +
        `<button type="button" class="pager-btn" id="landingNext"${{landingPageNum >= totalPages ? ' disabled' : ''}}>Next ›</button>`;
      const prev = document.getElementById('landingPrev');
      const next = document.getElementById('landingNext');
      if (prev) prev.onclick = () => {{ if (landingPageNum > 1) {{ landingPageNum--; renderLanding(); }} }};
      if (next) next.onclick = () => {{ if (landingPageNum < totalPages) {{ landingPageNum++; renderLanding(); }} }};
    }}
    async function loadLandingPages() {{
      setStatus('landingStatus', 'Loading landing pages...');
      try {{
        const payload = await getJson(withDates(LANDING_PAGES_API));
        landingRows = payload.rows || [];
        landingPageNum = 1;
        renderLanding();
        setRaw('landingJson', payload);
      }} catch (err) {{
        setStatus('landingStatus', err.message || String(err), true);
      }}
    }}
    function loadAll() {{
      loadPages();
      loadTrafficAcq();
      loadDeviceSplit();
      loadLandingPages();
    }}
    // Date presets
    const fmtDate = d => `${{d.getFullYear()}}-${{String(d.getMonth() + 1).padStart(2, '0')}}-${{String(d.getDate()).padStart(2, '0')}}`;
    function presetRange(name) {{
      const today = new Date();
      let s, e = today;
      const lastNDays = n => {{ e = new Date(today); e.setDate(today.getDate() - 1); s = new Date(today); s.setDate(today.getDate() - n); }};
      if (name === 'this_month') s = new Date(today.getFullYear(), today.getMonth(), 1);
      else if (name === 'last_month') {{ s = new Date(today.getFullYear(), today.getMonth() - 1, 1); e = new Date(today.getFullYear(), today.getMonth(), 0); }}
      else if (name === 'last_7') lastNDays(7);
      else if (name === 'last_30') lastNDays(30);
      else if (name === 'last_90') lastNDays(90);
      else return null;
      return {{ start: fmtDate(s), end: fmtDate(e) }};
    }}
    function highlightPreset(name) {{
      document.querySelectorAll('#datePresets .chip').forEach(b => b.classList.toggle('active', b.dataset.preset === name));
    }}
    const startDate = document.getElementById('startDate');
    const endDate = document.getElementById('endDate');
    const filters = document.getElementById('filters');
    document.getElementById('datePresets').addEventListener('click', event => {{
      const btn = event.target.closest('[data-preset]');
      if (!btn) return;
      const range = presetRange(btn.dataset.preset);
      if (!range) return;
      startDate.value = range.start;
      endDate.value = range.end;
      highlightPreset(btn.dataset.preset);
      loadAll();
    }});
    [startDate, endDate].forEach(inp => inp.addEventListener('change', () => highlightPreset(null)));
    filters.addEventListener('submit', event => {{ event.preventDefault(); highlightPreset(null); loadAll(); }});
    loadAll();
    // Scrollspy
    (function() {{
      const links = [...document.querySelectorAll('.dash-sidebar-nav .dash-view-btn')];
      const map = new Map(links.map(a => [a.dataset.nav, a]));
      const obs = new IntersectionObserver(entries => {{
        entries.forEach(en => {{ if (en.isIntersecting) {{ links.forEach(l => l.classList.remove('active')); const a = map.get(en.target.id); if (a) a.classList.add('active'); }} }});
      }}, {{ rootMargin: '-45% 0px -50% 0px' }});
      links.forEach(a => {{ const el = document.getElementById(a.dataset.nav); if (el) obs.observe(el); }});
    }})();
  </script>
</body>
</html>"""
