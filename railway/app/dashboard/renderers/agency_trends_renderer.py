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
    main { max-width:1180px; margin:0 auto; padding:26px 24px 56px; }
    section { background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:22px 24px; margin-bottom:18px; box-shadow:0 6px 22px rgba(10,37,64,.06); }
    .page-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:4px; }
    .page-head h2 { margin:0; font-size:1.15rem; color:var(--navy); }
    .sub { color:var(--muted); font-size:.86rem; margin:2px 0 0; }
    /* Summary tiles */
    .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:16px 0 4px; }
    .tile { border:1px solid var(--line); border-top:3px solid var(--accent); border-radius:12px; padding:13px 15px; background:#fff; }
    .tile-label { color:var(--muted); font-size:.66rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }
    .tile-value { margin-top:7px; font-size:1.55rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; }
    .tile-value.up { color:var(--green); } .tile-value.down { color:var(--danger); }
    .tile-sub { color:var(--muted); font-size:.75rem; margin-top:3px; }
    /* Table */
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; font-size:.9rem; min-width:960px; }
    th, td { text-align:right; padding:11px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
    th:first-child, td:first-child { text-align:left; }
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
    /* connector-health chip (links to the client's full data-source status) */
    .chip { display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:999px;
      font-size:.72rem; font-weight:700; text-decoration:none; border:1px solid var(--line);
      background:#fff; color:var(--ink); white-space:nowrap; transition:border-color .12s, background .12s; }
    .chip:hover { border-color:#cbd5e1; background:#f6f9fd; }
    .chip .dot { width:8px; height:8px; border-radius:50%; background:#94a3b8; flex:0 0 auto; }
    .chip.ok .dot { background:var(--green); }
    .chip.warn .dot { background:var(--amber); }
    .chip.bad .dot { background:var(--danger); }
    .chip.idle { color:var(--muted); }
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
    .pill.duck { background:#fff7ed; color:#b7791f; }"""


_TRENDS_CONTENT = """
  <main>
    <section>
      <div class="page-head">
        <div>
          <h2>Monthly budget pacing <span class="pill duck">DuckDB</span></h2>
          <p class="sub" id="atSub">Loading…</p>
        </div>
      </div>
      <div class="tiles" id="atTiles"></div>
    </section>
    <section>
      <div class="page-head"><h2>By client</h2></div>
      <div class="table-wrap">
        <table id="atTable">
          <thead><tr>
            <th class="sortable" data-key="label">Client</th>
            <th class="sortable" data-key="health_rank">Data</th>
            <th class="sortable active" data-key="pct_budget">% of budget</th>
            <th class="sortable" data-key="pct_change">Week over week</th>
            <th class="sortable" data-key="sessions_total">Sessions · 30d</th>
          </tr></thead>
          <tbody id="atBody"></tbody>
          <tfoot id="atFoot"></tfoot>
        </table>
      </div>
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

    function sortVal(r, key) {
      if (key === 'label') return (r.label||'').toLowerCase();
      const v = r[key];
      return (v == null) ? null : Number(v);
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
      const lines = (h.reasons || []).slice();
      (h.channels || []).forEach(c => lines.push(`${c.source} → ${c.through || 'no data'}`));
      lines.push('Click for full connector status');
      const href = `/dashboard/${encodeURIComponent(r.client_slug)}/settings`;
      return `<a class="chip ${cls}" href="${href}" target="_blank" rel="noopener" title="${esc(lines.join('\\n'))}">`
        + `<span class="dot"></span>${esc(label)}</a>`;
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
    function render() {
      const t = atData.totals || {};
      document.getElementById('atSub').textContent =
        `${atData.month_label} · day ${atData.days_elapsed} of ${atData.days_in_month} (${atData.pct_month_elapsed}% elapsed) · ${t.client_count} clients`;
      const tiles = [
        ['Total monthly budget', money(t.monthly_budget), `${atData.days_remaining} days left`],
        ['Spend month-to-date', money(t.mtd_spend), `${atData.pct_month_elapsed}% of month elapsed`],
        ['Projected month-end', money(t.projected_month_end), 'at current run-rate'],
        ['Clients over pace', String(t.clients_over_pace), `of ${t.client_count} clients`],
      ];
      document.getElementById('atTiles').innerHTML = tiles.map(([l,v,s]) =>
        `<div class="tile"><div class="tile-label">${esc(l)}</div><div class="tile-value">${esc(v)}</div><div class="tile-sub">${esc(s)}</div></div>`).join('');

      const rows = sortedRows();
      const body = document.getElementById('atBody');
      if (!rows.length) { body.innerHTML = `<tr><td colspan="5" class="empty">No clients configured yet.</td></tr>`; }
      else body.innerHTML = rows.map(r => {
        const dash = `/dashboard/${encodeURIComponent(r.client_slug)}`;
        return `<tr>`
          + `<td><div class="cl-name"><a href="${dash}">${esc(r.label)}</a></div><div class="cl-slug">${esc(r.client_slug)}</div></td>`
          + `<td>${healthCell(r)}</td>`
          + `<td>${pctCell(r)}</td>`
          + `<td>${wowCell(r)}</td>`
          + `<td>${sessCell(r)}</td>`
        + `</tr>`;
      }).join('');

      const totPct = t.monthly_budget ? Math.round(t.mtd_spend / t.monthly_budget * 100) : null;
      const totTip = `${money2(t.mtd_spend)} spent of ${money2(t.monthly_budget)} budget`;
      document.getElementById('atFoot').innerHTML = rows.length
        ? `<tr><td>All clients</td><td></td><td><div class="pct-cell" title="${esc(totTip)}"><span class="num">${totPct==null?'—':totPct+'%'}</span></div></td><td></td><td></td></tr>`
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
        + `The Data chip flags connector freshness inferred from these same reads — green current, amber lagging, red stale or failed`
        + `${issues ? ` (${issues} client${issues===1?'':'s'} need attention)` : ''} — and links to that client's full connector status. `
        + `All rollups are computed in DuckDB from one set of reads. "—" / "new" means no data or no prior baseline yet.`;
    }
    function skeleton() {
      document.getElementById('atTiles').innerHTML = Array.from({length:4}, () =>
        `<div class="tile"><div class="skel" style="height:10px;width:55%"></div>`
        + `<div class="skel" style="height:26px;width:70%;margin-top:9px"></div>`
        + `<div class="skel" style="height:9px;width:40%;margin-top:7px"></div></div>`).join('');
      document.getElementById('atBody').innerHTML = Array.from({length:6}, () =>
        `<tr><td><span class="skel" style="width:150px"></span></td>`
        + Array.from({length:4}, () => `<td><span class="skel" style="width:70px"></span></td>`).join('') + `</tr>`).join('');
    }
    document.getElementById('atTable').querySelector('thead').addEventListener('click', ev => {
      const th = ev.target.closest('th.sortable'); if (!th || !atData) return;
      const key = th.dataset.key;
      if (sort.key === key) sort.dir = sort.dir==='asc'?'desc':'asc';
      else sort = { key, dir: key==='label'?'asc':'desc' };
      render();
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
        document.getElementById('atBody').innerHTML = `<tr><td colspan="5" class="empty">Could not load data (${esc(e.message||'error')}). Try refreshing.</td></tr>`;
      }
    }
    load();
  </script>"""

    return render_admin_shell_page(
        active_nav="trends",
        page_title="Agency Trends",
        content_html=_TRENDS_CONTENT,
        session_email=user_email,
        extra_css=_TRENDS_CSS,
        body_end_html=body_end,
    )
