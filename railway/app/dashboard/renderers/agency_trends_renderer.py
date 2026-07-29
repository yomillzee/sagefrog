"""Admin "Agency Trends" page: the HQ client view, reproduced on DuckDB.

Rendered inside the shared admin shell (navy sidebar + client switcher) so Agency
Trends sits alongside the other admin sections rather than as a standalone page.
It fetches /admin/agency-trends/data for the numbers, so the DuckDB rollup never
blocks first paint. The page reproduces exactly what HQ shows (every client's
spend vs budget and its trailing-30d web-traffic sparkline) and adds
week-over-week paid-media momentum and channel mix on top, all computed from one
concurrent set of BigQuery reads by dashboard.services.agency_trends_service
(build_agency_overview).
"""

from __future__ import annotations

import html

from dashboard.renderers.base_layout import render_admin_shell_page


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


_TRENDS_CSS = """
    :root {
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#0a7f3f; --amber:#b7791f; --danger:#b42318;
    }
    body { color:var(--ink);
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; }
    main { max-width:1180px; width:100%; min-width:0; margin:0 auto; padding:26px 24px 56px; }
    section { background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:22px 24px; margin-bottom:18px; box-shadow:0 6px 22px rgba(10,37,64,.06); }
    .page-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:4px; }
    .page-head h2 { margin:0; font-size:1.15rem; color:var(--navy); }
    .sub { color:var(--muted); font-size:.86rem; margin:2px 0 0; }
    /* Table */
    .table-wrap { overflow-x:auto; -webkit-overflow-scrolling:touch; }
    table { width:100%; border-collapse:collapse; font-size:.9rem; min-width:960px; }
    th, td { text-align:right; padding:11px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
    th:first-child, td:first-child { text-align:left; }
    th.col-icon, td.col-icon { text-align:center; }
    th { color:var(--muted); font-weight:700; font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }
    th.sortable { cursor:pointer; user-select:none; transition:color .12s; }
    th.sortable:hover { color:var(--navy); }
    th.sortable.active { color:var(--accent); }
    tbody tr:hover { background:#f6f9fd; }
    .cl-name { font-weight:700; color:var(--navy); }
    .cl-name a { color:inherit; text-decoration:none; }
    .cl-name a:hover { text-decoration:underline; }
    .cl-slug { color:var(--muted); font-size:.74rem; font-weight:500; margin-top:1px; }
    .num { font-variant-numeric:tabular-nums; }
    /* progress bar in % of budget cell */
    .pct-cell { display:flex; align-items:center; justify-content:flex-end; gap:9px; cursor:default; }
    .pct-cell .num { font-weight:800; }
    .pct-cell .num.over { color:var(--danger); }
    .pct-cell .num.under { color:var(--green); }
    .pct-cell .num.on_track { color:var(--navy); }
    .pbar { flex:0 0 84px; height:7px; border-radius:4px; background:#eef2f7; overflow:hidden; }
    .pbar > span { display:block; height:100%; border-radius:4px; background:var(--accent); }
    .pbar.over > span { background:var(--danger); }
    .pbar.under > span { background:var(--green); }
    /* diverging week-over-week bar, centered at zero */
    .wow-cell { display:flex; align-items:center; justify-content:flex-end; gap:10px; }
    .wow-track { position:relative; flex:0 0 96px; height:9px; background:#eef2f7; border-radius:5px; }
    .wow-mid { position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background:#cbd5e1; }
    .wow-fill { position:absolute; top:0; bottom:0; border-radius:5px; }
    .wow-fill.pos { left:50%; background:var(--green); }
    .wow-fill.neg { right:50%; background:var(--danger); }
    .wow-pct { min-width:56px; text-align:right; font-variant-numeric:tabular-nums; font-weight:700; }
    .wow-pct.pos { color:var(--green); } .wow-pct.neg { color:var(--danger); }
    .wow-pct.flat { color:var(--muted); }
    .sess-cell { display:flex; align-items:center; justify-content:flex-end; gap:9px; }
    .spark svg { display:block; }
    /* connector-health dot (icon-only; links to the client's full data-source
       status). No text — colour is the whole signal, tooltip carries the detail. */
    .hdot-link { display:inline-flex; align-items:center; justify-content:center; text-decoration:none; }
    .hdot { width:11px; height:11px; border-radius:50%; background:#94a3b8;
      box-shadow:0 0 0 3px rgba(148,163,184,.15); transition:transform .12s; }
    .hdot-link:hover .hdot { transform:scale(1.18); }
    .hdot.ok { background:var(--green); box-shadow:0 0 0 3px rgba(10,127,63,.16); }
    .hdot.warn { background:var(--amber); box-shadow:0 0 0 3px rgba(183,121,31,.18); }
    .hdot.bad { background:var(--danger); box-shadow:0 0 0 3px rgba(180,35,24,.16); }
    .hdot.idle { background:#cbd5e1; box-shadow:none; }
    /* Consent verdict as a colour-coded shield icon (no text). */
    .cshield { display:inline-flex; align-items:center; justify-content:center; text-decoration:none; color:#94a3b8; transition:transform .12s; }
    .cshield:hover { transform:scale(1.12); }
    .cshield svg { width:17px; height:17px; }
    .cshield.good { color:var(--green); }
    .cshield.warn { color:var(--amber); }
    .cshield.bad { color:var(--danger); }
    .cshield.idle { color:#cbd5e1; }
    .chan { display:flex; align-items:center; gap:12px; margin:10px 0; }
    .chan-name { width:92px; font-weight:700; color:var(--navy); text-transform:capitalize; font-size:.9rem; }
    .chan-bar { flex:1; height:14px; background:#eef2f7; border-radius:7px; overflow:hidden; }
    .chan-bar > span { display:block; height:100%; border-radius:7px; background:var(--accent); }
    .chan-val { width:150px; text-align:right; font-variant-numeric:tabular-nums; color:var(--muted); font-size:.85rem; }
    .muted { color:var(--muted); }
    .empty { text-align:center; color:var(--muted); padding:26px 8px; }
    .skel { display:inline-block; height:12px; border-radius:6px; background:linear-gradient(90deg,#eef2f7,#e2e8f0,#eef2f7); background-size:200% 100%; animation:sh 1.2s ease-in-out infinite; }
    @keyframes sh { 0%{background-position:200% 0;} 100%{background-position:-200% 0;} }
    tfoot td { font-weight:800; color:var(--navy); border-top:2px solid var(--line); border-bottom:0; }
    .status-note { font-size:.82rem; color:var(--muted); margin-top:12px; }
    .pill { display:inline-block; font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em;
      padding:2px 7px; border-radius:999px; background:#eef2f7; color:var(--muted); margin-left:7px; vertical-align:middle; }
    .pill.duck { background:#fff7ed; color:#b7791f; }
    /* Primary KPI cell: value + optional goal progress bar, colour-paced. */
    .kpi-cell { display:flex; flex-direction:column; align-items:flex-end; gap:3px; cursor:default; }
    .kpi-top { display:flex; align-items:baseline; gap:6px; }
    .kpi-val { font-variant-numeric:tabular-nums; font-weight:800; color:var(--navy); }
    .kpi-name { color:var(--muted); font-size:.7rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; }
    .kpi-bar { flex:0 0 auto; width:96px; height:6px; border-radius:4px; background:#eef2f7; overflow:hidden; }
    .kpi-bar > span { display:block; height:100%; border-radius:4px; background:var(--accent); }
    .kpi-bar.good > span { background:var(--green); }
    .kpi-bar.warn > span { background:var(--amber); }
    .kpi-bar.bad > span { background:var(--danger); }
    .kpi-goal { font-size:.68rem; color:var(--muted); font-variant-numeric:tabular-nums; }
    .kpi-unset { color:var(--muted); font-size:.78rem; }
    .kpi-unset a { color:var(--accent); text-decoration:none; }
    .kpi-unset a:hover { text-decoration:underline; }
    /* Narrow screens: tighten padding, let the wide data table scroll on its own,
       and stop the channel rows' fixed widths from overflowing. */
    @media (max-width: 720px) {
      main { padding:16px 13px 44px; }
      section { padding:16px 15px; border-radius:14px; margin-bottom:14px; }
      .page-head h2 { font-size:1.05rem; }
      .sub { font-size:.82rem; }
      th, td { padding:9px 10px; }
      .pbar { flex-basis:64px; }
      .wow-track { flex-basis:72px; }
      .chan { gap:10px; margin:9px 0; }
      .chan-name { width:64px; font-size:.82rem; }
      .chan-val { width:auto; min-width:92px; font-size:.78rem; }
      .status-note { font-size:.78rem; }
    }
    /* Mobile accordion: collapsed rows show the client name + a strip of small
       colour-coded status icons (budget / website / consent); tap to expand the
       full per-metric detail. Rendered from the same data as the table; only one
       of the two is shown at a time (see the media query below). */
    #atCards { display:none; }
    .acc { border:1px solid var(--line); border-radius:12px; background:#fff; margin-bottom:10px; }
    .acc-head { display:flex; align-items:center; gap:11px; padding:12px 14px; cursor:pointer; }
    .acc-title { display:flex; flex-direction:column; min-width:0; flex:1; }
    .acc-name { font-weight:700; color:var(--navy); text-decoration:none; font-size:.96rem;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .acc-name:hover { text-decoration:underline; }
    .acc-slug { color:var(--muted); font-size:.72rem; }
    .acc-icons { display:flex; align-items:center; gap:6px; flex:0 0 auto; }
    .acc-caret { color:var(--muted); flex:0 0 auto; display:flex; transition:transform .18s ease; }
    .acc-caret svg { width:18px; height:18px; }
    .acc.open .acc-caret { transform:rotate(180deg); }
    .sicon { display:inline-flex; align-items:center; justify-content:center; width:27px; height:27px; border-radius:8px; }
    .sicon svg { width:15px; height:15px; }
    .sicon.good { color:var(--green); background:rgba(10,127,63,.10); }
    .sicon.warn { color:var(--amber); background:rgba(183,121,31,.13); }
    .sicon.bad { color:var(--danger); background:rgba(180,35,24,.10); }
    .sicon.neutral { color:var(--accent); background:rgba(37,99,235,.10); }
    .sicon.idle { color:var(--muted); background:#eef2f7; }
    .acc-body { display:none; border-top:1px solid var(--line); padding:2px 14px 8px; }
    .acc.open .acc-body { display:block; }
    .acc-row { display:flex; align-items:center; justify-content:space-between; gap:14px;
      padding:9px 0; border-bottom:1px solid #f0f3f8; }
    .acc-row:last-child { border-bottom:0; }
    .acc-k { color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; flex:0 0 auto; }
    .acc-v { display:flex; align-items:center; justify-content:flex-end; min-width:0; }
    .acc-empty { text-align:center; color:var(--muted); padding:24px 8px; border:1px solid var(--line); border-radius:12px; background:#fff; }
    /* Phones: hide the wide scrolling table and show the accordion instead. */
    @media (max-width: 640px) {
      .table-wrap { display:none; }
      #atCards { display:block; }
    }"""


_TRENDS_CONTENT = """
  <main>
    <section>
      <div class="page-head">
        <div>
          <h2>By client <span class="pill duck">DuckDB</span></h2>
          <p class="sub" id="atSub">Loading…</p>
        </div>
      </div>
      <div class="table-wrap">
        <table id="atTable">
          <thead><tr>
            <th class="sortable" data-key="label">Client</th>
            <th class="sortable col-icon" data-key="health_rank">Data</th>
            <th class="sortable" data-key="kpi_value">Primary KPI</th>
            <th class="sortable active" data-key="pct_budget">% of budget</th>
            <th class="sortable" data-key="pct_change">Week over week</th>
            <th class="sortable" data-key="sessions_total">Sessions · 30d</th>
            <th class="sortable col-icon" data-key="consent_rank">Consent</th>
          </tr></thead>
          <tbody id="atBody"></tbody>
          <tfoot id="atFoot"></tfoot>
        </table>
      </div>
      <div id="atCards"></div>
      <p class="status-note" id="atNote"></p>
    </section>
    <section>
      <div class="page-head"><h2>Where spend went (last 7 days)</h2></div>
      <div id="atChannels" style="margin-top:14px"></div>
    </section>
  </main>"""


def render_agency_trends_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/agency-trends. Data loads from …/data."""
    body_end = """<script>
    const money = v => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(Number(v||0));
    const money2 = v => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(v||0));
    const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const pctTxt = p => (p>0?'+':'') + Number(p).toFixed(1) + '%';
    const STATUS_LABEL = { over:'Over pace', under:'Under pace', on_track:'On track', no_budget:'No budget' };
    let atData = null;
    let sort = { key:'pct_budget', dir:'desc' };

    const CONSENT_META = { pass:{c:'#0a7f3f',l:'Healthy',rank:1}, attention:{c:'#b7791f',l:'Attention',rank:2}, fail:{c:'#b42318',l:'Critical',rank:3} };
    function sortVal(r, key) {
      if (key === 'label') return (r.label||'').toLowerCase();
      if (key === 'consent_rank') { const m = r.consent && CONSENT_META[r.consent.health]; return m ? m.rank : null; }
      if (key === 'kpi_value') return (r.kpi && r.kpi.value != null) ? Number(r.kpi.value) : null;
      const v = r[key];
      return (v == null) ? null : Number(v);
    }
    const CONSENT_SHIELD = { pass:['good','Healthy'], attention:['warn','Attention'], fail:['bad','Critical'] };
    function consentCell(r) {
      const c = r.consent;
      if (!c || !c.health || c.health === 'unknown') return '<span class="muted">—</span>';
      const [cls, label] = CONSENT_SHIELD[c.health] || ['idle', c.health];
      const v = c.violation_count || 0;
      const tip = `Consent — ${label} · ${v} issue${v===1?'':'s'} before/after opt-out`
        + (c.scanned_at ? ` · scanned ${String(c.scanned_at).slice(0,10)}` : '');
      const url = `/dashboard/${encodeURIComponent(r.client_slug)}/consent`;
      return `<a class="cshield ${cls}" href="${url}" title="${esc(tip)}" role="img" aria-label="${esc(tip)}">${ICON.shield}</a>`;
    }
    // Each client's KPI is a different metric (MQLs, Google Ads conversions,
    // ROAS, …); the value carries its own format + label so one column reads for
    // all of them. Goal-bearing KPIs get a pace-coloured progress bar.
    function fmtKpiVal(v, format) {
      if (v == null) return '—';
      v = Number(v);
      if (format === 'multiplier') return v.toFixed(1) + '×';
      if (format === 'currency') return money(v);
      return new Intl.NumberFormat('en-US', { maximumFractionDigits: Number.isInteger(v) ? 0 : 1 }).format(v);
    }
    function kpiCell(r) {
      const k = r.kpi;
      if (!k) return '<span class="kpi-unset">Not set</span>';
      const valTxt = fmtKpiVal(k.value, k.format);
      const name = k.label ? `<span class="kpi-name">${esc(k.label)}</span>` : '';
      let bar = '', goalTxt = '';
      if (k.goal != null && k.goal > 0) {
        const cls = ['good','warn','bad'].includes(k.status) ? k.status : '';
        const w = Math.max(2, Math.min(100, k.pct_to_goal == null ? 0 : k.pct_to_goal));
        bar = `<span class="kpi-bar ${cls}"><span style="width:${w}%"></span></span>`;
        const pctStr = k.pct_to_goal == null ? '' : Math.round(k.pct_to_goal) + '% of ';
        goalTxt = `<span class="kpi-goal">${pctStr}${fmtKpiVal(k.goal, k.format)} goal</span>`;
      }
      return `<div class="kpi-cell" title="${esc(k.tooltip || '')}"><div class="kpi-top">`
        + `<span class="kpi-val">${valTxt}</span>${name}</div>${bar}${goalTxt}</div>`;
    }
    function sortedRows() {
      const rows = (atData.clients||[]).slice();
      const {key, dir} = sort, mul = dir==='asc'?1:-1;
      rows.sort((a,b) => {
        const x = sortVal(a,key), y = sortVal(b,key);
        const xb = (x==null), yb = (y==null);
        if (xb && yb) return 0; if (xb) return 1; if (yb) return -1;
        if (typeof x === 'string') return mul*x.localeCompare(y);
        return mul*(x-y);
      });
      return rows;
    }
    function pctCell(r) {
      if (r.pct_budget == null) return '<span class="muted">—</span>';
      const cls = r.status==='over'?'over':(r.status==='under'?'under':'on_track');
      const bar = cls==='on_track' ? '' : cls;
      const w = Math.min(100, r.pct_budget);
      const budgetTxt = r.has_budget ? money2(r.monthly_budget) : 'no budget set';
      const tip = r.spend_available
        ? `${money2(r.mtd_spend)} spent of ${budgetTxt} · ${STATUS_LABEL[r.status]||r.status}`
        : 'No spend data';
      return `<div class="pct-cell" title="${esc(tip)}"><span class="num ${cls}">${r.pct_budget.toFixed(0)}%</span>`
        + `<span class="pbar ${bar}"><span style="width:${w}%"></span></span></div>`;
    }
    function wowCell(r) {
      if (r.pct_change == null) return '<div class="wow-cell"><span class="muted">new</span></div>';
      const pos = r.pct_change >= 0;
      const cls = r.pct_change > 0 ? 'pos' : (r.pct_change < 0 ? 'neg' : 'flat');
      const w = Math.min(50, Math.abs(r.pct_change) / 60 * 50); // 60% saturates half the track
      const fill = `<span class="wow-fill ${pos?'pos':'neg'}" style="width:${w}%"></span>`;
      const tip = `${money(r.last_7)} last 7 days vs ${money(r.prior_7)} prior 7 days`;
      return `<div class="wow-cell" title="${esc(tip)}"><span class="wow-track"><span class="wow-mid"></span>${fill}</span>`
        + `<span class="wow-pct ${cls}">${pctTxt(r.pct_change)}</span></div>`;
    }
    // Inline SVG sparkline of daily sessions. Colour tints up (green) / down
    // (red) / flat (blue) by comparing the last third of the window to the first.
    function sparkline(series) {
      if (!series || series.length < 2) return '';
      const w = 96, h = 26, pad = 3, n = series.length;
      const max = Math.max(...series), min = Math.min(...series), span = (max - min) || 1;
      const x = i => pad + (w - 2*pad) * (i / (n - 1));
      const y = v => pad + (h - 2*pad) * (1 - (v - min) / span);
      const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
      const k = Math.max(1, Math.floor(n / 3));
      const avg = a => a.reduce((s, v) => s + v, 0) / a.length;
      const first = avg(series.slice(0, k)), last = avg(series.slice(-k));
      const color = last >= first * 1.02 ? '#0a7f3f' : (last <= first * 0.98 ? '#b42318' : '#2563eb');
      const lx = x(n - 1), ly = y(series[n - 1]);
      return `<span class="spark"><svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">`
        + `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>`
        + `<circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="1.9" fill="${color}"/></svg></span>`;
    }
    function sessCell(r) {
      if (!r.sessions_available) return '<span class="muted">—</span>';
      return `<div class="sess-cell">${sparkline(r.sessions_series)}</div>`;
    }
    // Connector-health chip. Colour by staleness (green current / amber lagging
    // / red stale-or-failed / grey not-set-up); tooltip shows each channel's
    // through-date and any reasons. Links to the client's settings page, which
    // runs the full per-source status check on demand — so we never pay that
    // cost for every client up front.
    const HEALTH = {
      current:['ok','Current'], lagging:['warn','Lagging'], stale:['bad','Stale'],
      error:['bad','Read failed'], no_data:['idle','No data'], not_configured:['idle','Not set up'],
    };
    function healthCell(r) {
      const h = r.health || {status:'no_data'};
      const [cls, label] = HEALTH[h.status] || HEALTH.no_data;
      const lines = [`Data — ${label}`];
      (h.reasons || []).forEach(x => lines.push(x));
      (h.channels || []).forEach(c => lines.push(`${c.source} → ${c.through || 'no data'}`));
      lines.push('Click for full connector status');
      const href = `/dashboard/${encodeURIComponent(r.client_slug)}/settings`;
      return `<a class="hdot-link" href="${href}" target="_blank" rel="noopener" title="${esc(lines.join('\\n'))}" `
        + `role="img" aria-label="${esc('Data — ' + label)}"><span class="hdot ${cls}"></span></a>`;
    }
    // Fold each client's week-over-week momentum onto its budget row so one
    // table shows the HQ view (spend vs budget + sessions) with a WoW column.
    function mergeMomentum() {
      const m = atData.momentum || {};
      const bySlug = {};
      (m.clients || []).forEach(c => { bySlug[c.client_slug] = c; });
      (atData.clients || []).forEach(r => {
        const mom = bySlug[r.client_slug];
        r.last_7 = mom ? mom.last_7 : null;
        r.prior_7 = mom ? mom.prior_7 : null;
        r.pct_change = mom ? mom.pct_change : null;
      });
    }
    // ---- Mobile accordion -------------------------------------------------
    // Collapsed rows carry a strip of small colour-coded status icons so the
    // whole roster reads at a glance; expanding shows the same per-metric detail
    // the desktop table columns do.
    const ICON = {
      dollar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="1.5" x2="12" y2="22.5"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
      chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
      shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
      target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg>',
      caret: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>',
    };
    function trendDir(series) {
      if (!series || series.length < 2) return null;
      const n = series.length, k = Math.max(1, Math.floor(n / 3));
      const avg = a => a.reduce((s, v) => s + v, 0) / a.length;
      const first = avg(series.slice(0, k)), last = avg(series.slice(-k));
      return last >= first * 1.02 ? 'up' : (last <= first * 0.98 ? 'down' : 'flat');
    }
    function statIcon(cls, svg, title) {
      return `<span class="sicon ${cls}" title="${esc(title)}" role="img" aria-label="${esc(title)}">${svg}</span>`;
    }
    function budgetIcon(r) {
      if (r.pct_budget == null) return statIcon('idle', ICON.dollar, 'Budget — no budget set');
      const cls = r.status==='over'?'bad':(r.status==='under'?'good':'neutral');
      return statIcon(cls, ICON.dollar, `Budget — ${r.pct_budget.toFixed(0)}% of budget · ${STATUS_LABEL[r.status]||r.status}`);
    }
    function webIcon(r) {
      const d = r.sessions_available ? trendDir(r.sessions_series) : null;
      const cls = d==='up'?'good':(d==='down'?'bad':(d==='flat'?'neutral':'idle'));
      const txt = d ? `Website — GA4 sessions trending ${d}` : 'Website — no session data';
      return statIcon(cls, ICON.chart, txt);
    }
    function consentIcon(r) {
      const c = r.consent, h = c && c.health;
      const map = { pass:['good','Healthy'], attention:['warn','Attention'], fail:['bad','Critical'] };
      const [cls, label] = map[h] || ['idle','No data'];
      return statIcon(cls, ICON.shield, `Consent — ${label}`);
    }
    function kpiIcon(r) {
      const k = r.kpi;
      if (!k) return statIcon('idle', ICON.target, 'Primary KPI — not set');
      const cls = ['good','warn','bad'].includes(k.status) ? k.status : 'neutral';
      const goal = (k.goal != null && k.goal > 0) ? ` of ${fmtKpiVal(k.goal, k.format)} goal` : '';
      return statIcon(cls, ICON.target, `${k.label || 'Primary KPI'} — ${fmtKpiVal(k.value, k.format)}${goal}`);
    }
    function accRow(label, valueHtml) {
      return `<div class="acc-row"><span class="acc-k">${esc(label)}</span><span class="acc-v">${valueHtml}</span></div>`;
    }
    function renderCards(rows) {
      const el = document.getElementById('atCards');
      if (!rows.length) { el.innerHTML = `<div class="acc-empty">No clients configured yet.</div>`; return; }
      el.innerHTML = rows.map(r => {
        const dash = `/dashboard/${encodeURIComponent(r.client_slug)}`;
        const detail = accRow('Data', healthCell(r))
          + accRow('Primary KPI', kpiCell(r))
          + accRow('% of budget', pctCell(r))
          + accRow('Week over week', wowCell(r))
          + accRow('Sessions · 30d', sessCell(r))
          + accRow('Consent', consentCell(r));
        return `<div class="acc">`
          + `<div class="acc-head" role="button" tabindex="0" aria-expanded="false">`
            + `<span class="acc-title"><a class="acc-name" href="${dash}">${esc(r.label)}</a>`
            + `<span class="acc-slug">${esc(r.client_slug)}</span></span>`
            + `<span class="acc-icons">${budgetIcon(r)}${kpiIcon(r)}${webIcon(r)}${consentIcon(r)}</span>`
            + `<span class="acc-caret">${ICON.caret}</span>`
          + `</div>`
          + `<div class="acc-body">${detail}</div>`
        + `</div>`;
      }).join('');
    }
    function toggleAcc(head) {
      const acc = head.closest('.acc'); if (!acc) return;
      const open = acc.classList.toggle('open');
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    // ----------------------------------------------------------------------
    function render() {
      const t = atData.totals || {};
      document.getElementById('atSub').textContent =
        `${atData.month_label} · day ${atData.days_elapsed} of ${atData.days_in_month} (${atData.pct_month_elapsed}% elapsed) · ${t.client_count} clients`;

      const rows = sortedRows();
      const body = document.getElementById('atBody');
      if (!rows.length) { body.innerHTML = `<tr><td colspan="7" class="empty">No clients configured yet.</td></tr>`; }
      else body.innerHTML = rows.map(r => {
        const dash = `/dashboard/${encodeURIComponent(r.client_slug)}`;
        return `<tr>`
          + `<td><div class="cl-name"><a href="${dash}">${esc(r.label)}</a></div><div class="cl-slug">${esc(r.client_slug)}</div></td>`
          + `<td class="col-icon">${healthCell(r)}</td>`
          + `<td>${kpiCell(r)}</td>`
          + `<td>${pctCell(r)}</td>`
          + `<td>${wowCell(r)}</td>`
          + `<td>${sessCell(r)}</td>`
          + `<td class="col-icon">${consentCell(r)}</td>`
        + `</tr>`;
      }).join('');
      renderCards(rows);

      const totPct = t.monthly_budget ? Math.round(t.mtd_spend / t.monthly_budget * 100) : null;
      const totTip = `${money2(t.mtd_spend)} spent of ${money2(t.monthly_budget)} budget`;
      document.getElementById('atFoot').innerHTML = rows.length
        ? `<tr><td>All clients</td><td></td><td></td><td><div class="pct-cell" title="${esc(totTip)}"><span class="num">${totPct==null?'—':totPct+'%'}</span></div></td><td></td><td></td><td></td></tr>`
        : '';

      document.querySelectorAll('#atTable th.sortable').forEach(th =>
        th.classList.toggle('active', th.dataset.key === sort.key));

      const m = atData.momentum || {};
      const chans = m.channels || [];
      const maxSpend = chans.reduce((mx,c) => Math.max(mx, c.spend), 0) || 1;
      document.getElementById('atChannels').innerHTML = chans.length
        ? chans.map(c => `<div class="chan"><div class="chan-name">${esc(c.source)}</div>`
            + `<div class="chan-bar"><span style="width:${Math.max(2, c.spend/maxSpend*100)}%"></span></div>`
            + `<div class="chan-val">${money(c.spend)} · ${c.pct_of_total}%</div></div>`).join('')
        : `<p class="empty">No spend by channel in this window.</p>`;

      const sw = atData.sessions_window || {};
      const L = (m.window || {}).last || {};
      const issues = t.clients_with_data_issues || 0;
      document.getElementById('atNote').textContent =
        `Spend vs budget is month-to-date paid media (Google, LinkedIn, Meta) from each client's BigQuery mart, as of ${atData.as_of}. `
        + `Week over week compares the last 7 days (${L.start||''} → ${L.end||''}) with the 7 before. `
        + `Sessions sparkline is GA4 sessions per day over the trailing ${sw.days||30} days; green trending up, red down. `
        + `The Data dot flags connector freshness inferred from these same reads — green current, amber lagging, red stale or failed`
        + `${issues ? ` (${issues} client${issues===1?'':'s'} need attention)` : ''} — and links to that client's full connector status. `
        + `All rollups are computed in DuckDB from one set of reads. "—" / "new" means no data or no prior baseline yet.`;
    }
    function skeleton() {
      document.getElementById('atBody').innerHTML = Array.from({length:6}, () =>
        `<tr><td><span class="skel" style="width:150px"></span></td>`
        + Array.from({length:6}, () => `<td><span class="skel" style="width:70px"></span></td>`).join('') + `</tr>`).join('');
      document.getElementById('atCards').innerHTML = Array.from({length:6}, () =>
        `<div class="acc"><div class="acc-head"><span class="acc-title"><span class="skel" style="width:120px;height:13px"></span></span>`
        + `<span class="skel" style="width:96px;height:22px;border-radius:8px"></span></div></div>`).join('');
    }
    document.getElementById('atTable').querySelector('thead').addEventListener('click', ev => {
      const th = ev.target.closest('th.sortable'); if (!th || !atData) return;
      const key = th.dataset.key;
      if (sort.key === key) sort.dir = sort.dir==='asc'?'desc':'asc';
      else sort = { key, dir: key==='label'?'asc':'desc' };
      render();
    });
    // Accordion: tap/click (or Enter/Space) anywhere on a head except the client
    // name link toggles its detail panel.
    const cardsEl = document.getElementById('atCards');
    cardsEl.addEventListener('click', ev => {
      if (ev.target.closest('.acc-name')) return;
      const head = ev.target.closest('.acc-head'); if (head) toggleAcc(head);
    });
    cardsEl.addEventListener('keydown', ev => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      const head = ev.target.closest('.acc-head'); if (!head) return;
      ev.preventDefault(); toggleAcc(head);
    });
    async function load() {
      skeleton();
      try {
        const r = await fetch('/admin/agency-trends/data', { credentials:'same-origin' });
        if (!r.ok) throw new Error('HTTP '+r.status);
        atData = await r.json();
        mergeMomentum();
        render();
      } catch (e) {
        document.getElementById('atSub').textContent = 'Failed to load agency trends.';
        const msg = `Could not load data (${esc(e.message||'error')}). Try refreshing.`;
        document.getElementById('atBody').innerHTML = `<tr><td colspan="7" class="empty">${msg}</td></tr>`;
        document.getElementById('atCards').innerHTML = `<div class="acc-empty">${msg}</div>`;
      }
    }
    load();
  </script>"""

    return render_admin_shell_page(
        active_nav="trends",
        page_title="Health",
        content_html=_TRENDS_CONTENT,
        session_email=user_email,
        extra_css=_TRENDS_CSS,
        body_end_html=body_end,
    )
