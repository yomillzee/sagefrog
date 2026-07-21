"""Admin "Client Hours" page: a Harvest burn-up chart per client.

Rendered inside the shared admin shell (navy sidebar + client switcher) so it
sits alongside the other admin overviews. It fetches /admin/client-hours/data
(live from the Harvest API) and draws, for the current calendar month, each
client's cumulative hours-logged line against a straight goal-pace line — the
same burn-up read the agency already uses for individual clients, now for every
client at once. Each card's monthly goal is editable inline and saved to
/admin/client-hours/goal.
"""

from __future__ import annotations

import html

from dashboard.renderers.base_layout import render_admin_shell_page


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


_HOURS_CSS = """
    :root {
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2f6df0; --accent-d:#1d4ed8; --grid:#eef2f7; --goal:#9aa7b8;
    }
    body { color:var(--ink);
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; }
    main { max-width:1180px; width:100%; min-width:0; margin:0 auto; padding:26px 24px 56px; }
    .page-head { margin-bottom:14px; display:flex; align-items:flex-start;
      justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .page-head h2 { margin:0; font-size:1.15rem; color:var(--navy); }
    .sub { color:var(--muted); font-size:.86rem; margin:3px 0 0; }
    .head-controls { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .seg { display:inline-flex; background:#eef2f7; border:1px solid var(--border);
      border-radius:999px; padding:2px; gap:2px; }
    .seg-btn { appearance:none; border:0; background:transparent; color:var(--muted);
      border-radius:999px; padding:6px 13px; font:inherit; font-size:.8rem; font-weight:700;
      cursor:pointer; white-space:nowrap; }
    .seg-btn:hover { color:var(--navy); }
    .seg-btn.is-active { background:#fff; color:var(--navy); box-shadow:0 1px 3px rgba(10,37,64,.12); }
    .head-select { appearance:none; border:1px solid var(--border); background:#fff
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%230a2540' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")
      no-repeat right 12px center; border-radius:999px; color:var(--navy); font:inherit; font-size:.82rem;
      font-weight:700; padding:8px 34px 8px 15px; cursor:pointer; }
    .head-select:hover { border-color:#94a3b8; }
    .refresh-btn { appearance:none; display:inline-flex; align-items:center; gap:7px;
      border:1px solid var(--border); background:#fff; color:var(--navy); border-radius:999px;
      padding:8px 14px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; flex:0 0 auto; }
    .refresh-btn:hover { border-color:#94a3b8; }
    .refresh-btn:disabled { opacity:.6; cursor:default; }
    .refresh-btn svg { width:14px; height:14px; }
    .refresh-btn.spin svg { animation:rot .8s linear infinite; }
    @keyframes rot { to { transform:rotate(360deg); } }
    .notice { border-radius:12px; padding:14px 16px; margin-bottom:16px; font-size:.9rem; }
    .notice.err { background:#fef2f2; border:1px solid #fecaca; color:#b42318; }
    .notice.warn { background:#fffbeb; border:1px solid #fde68a; color:#92400e; }
    .notice a { color:inherit; font-weight:700; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px; }
    .card { background:#fff; border:1px solid var(--line); border-radius:16px; padding:16px 16px 12px;
      box-shadow:0 6px 22px rgba(10,37,64,.06); }
    .card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; margin-bottom:2px; }
    .card-title { font-weight:750; color:var(--navy); font-size:.98rem; line-height:1.25;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .card-total { font-variant-numeric:tabular-nums; color:var(--muted); font-size:.78rem; margin-top:2px; }
    .card-total b { color:var(--navy); font-weight:800; }
    .card-total .track { font-weight:800; }
    .period { color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }
    .chart-wrap { position:relative; margin-top:8px; }
    .chart-wrap svg { display:block; width:100%; height:auto; }
    /* Goal editor */
    .goal { display:flex; align-items:center; gap:6px; flex:0 0 auto; }
    .goal-btn { appearance:none; border:1px solid var(--border); background:#f8fafc; color:var(--navy);
      border-radius:999px; padding:4px 11px; font:inherit; font-size:.76rem; font-weight:700; cursor:pointer;
      white-space:nowrap; }
    .goal-btn:hover { border-color:#94a3b8; background:#fff; }
    .goal-btn .g-set { color:var(--accent); }
    .goal-edit { display:none; align-items:center; gap:5px; }
    .goal-edit.on { display:flex; }
    .goal-edit input { width:118px; border:1px solid var(--border); border-radius:8px; padding:4px 8px;
      font:inherit; font-size:.8rem; text-align:left; }
    .goal-edit button { appearance:none; border:0; border-radius:8px; padding:5px 9px; font:inherit;
      font-size:.76rem; font-weight:700; cursor:pointer; }
    .goal-save { background:var(--accent); color:#fff; }
    .goal-cancel { background:#eef2f7; color:var(--muted); }
    .legend { display:flex; gap:14px; align-items:center; margin-top:6px; font-size:.72rem; color:var(--muted); }
    .legend span { display:inline-flex; align-items:center; gap:5px; }
    .legend i { width:14px; height:0; border-top-width:2px; border-top-style:solid; display:inline-block; }
    .legend i.actual { border-top-color:var(--accent); }
    .legend i.goal { border-top-color:var(--goal); border-top-style:dashed; }
    .empty { text-align:center; color:var(--muted); padding:40px 8px; background:#fff;
      border:1px solid var(--line); border-radius:16px; }
    .skel-card { height:230px; background:#fff; border:1px solid var(--line); border-radius:16px;
      position:relative; overflow:hidden; }
    .skel-card::after { content:""; position:absolute; inset:0;
      background:linear-gradient(90deg,transparent,rgba(148,163,184,.12),transparent);
      animation:sh 1.2s ease-in-out infinite; }
    @keyframes sh { 0%{transform:translateX(-100%);} 100%{transform:translateX(100%);} }
    /* Tag-projects modal */
    .modal-backdrop { position:fixed; inset:0; background:rgba(10,37,64,.42); z-index:120; }
    .tag-modal { position:fixed; top:0; right:0; height:100vh; width:min(460px, 94vw); background:#fff;
      z-index:121; display:flex; flex-direction:column; box-shadow:-12px 0 40px rgba(10,37,64,.22);
      animation:slideIn .18s ease; }
    /* display:flex/fixed above are author styles that would otherwise beat the
       browser's [hidden]{display:none}, leaving the panel stuck open. */
    .tag-modal[hidden], .modal-backdrop[hidden] { display:none; }
    @keyframes slideIn { from { transform:translateX(20px); opacity:.6; } to { transform:none; opacity:1; } }
    .tag-modal-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px;
      padding:18px 20px 12px; border-bottom:1px solid var(--line); }
    .tag-modal-head h2 { margin:0; font-size:1.05rem; color:var(--navy); }
    .tag-modal-sub { margin:4px 0 0; font-size:.78rem; color:var(--muted); line-height:1.4; }
    .tag-modal-close { appearance:none; border:0; background:#eef2f7; color:var(--muted); border-radius:8px;
      width:30px; height:30px; font-size:.95rem; cursor:pointer; flex:0 0 auto; }
    .tag-modal-close:hover { background:#e2e8f0; color:var(--navy); }
    .tag-modal-search { padding:12px 20px; border-bottom:1px solid var(--line); }
    .tag-modal-search input { width:100%; border:1px solid var(--border); border-radius:9px; padding:9px 12px;
      font:inherit; font-size:.85rem; }
    .tag-modal-body { overflow-y:auto; padding:8px 20px 24px; flex:1; }
    .tag-client { margin-top:14px; }
    .tag-client-name { font-weight:750; color:var(--navy); font-size:.82rem; text-transform:uppercase;
      letter-spacing:.03em; margin-bottom:6px; }
    .tag-row { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0;
      border-bottom:1px solid #f0f3f8; }
    .tag-row:last-child { border-bottom:0; }
    .tag-proj { min-width:0; }
    .tag-proj-name { font-size:.88rem; color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .tag-proj-hrs { font-size:.72rem; color:var(--muted); font-variant-numeric:tabular-nums; }
    .tag-seg { display:inline-flex; background:#eef2f7; border:1px solid var(--border); border-radius:999px;
      padding:2px; gap:2px; flex:0 0 auto; }
    .tag-seg button { appearance:none; border:0; background:transparent; color:var(--muted); border-radius:999px;
      padding:4px 10px; font:inherit; font-size:.74rem; font-weight:700; cursor:pointer; }
    .tag-seg button.on[data-tag=""] { background:#fff; color:var(--navy); box-shadow:0 1px 2px rgba(10,37,64,.12); }
    .tag-seg button.on[data-tag="retainer"] { background:#0a7f3f; color:#fff; }
    .tag-seg button.on[data-tag="project"] { background:#2f6df0; color:#fff; }
    .tag-empty { text-align:center; color:var(--muted); padding:30px 8px; }
    @media (max-width: 720px) {
      main { padding:16px 13px 44px; }
      .grid { grid-template-columns:1fr; }
    }"""


_HOURS_CONTENT = """
  <main>
    <div class="page-head">
      <div>
        <h2>Client Hours</h2>
        <p class="sub" id="chSub">Loading Harvest hours…</p>
      </div>
      <div class="head-controls">
        <select id="chScope" class="head-select" aria-label="Work type">
          <option value="retainer" selected>Retainer</option>
          <option value="project">Project</option>
          <option value="all">All work</option>
        </select>
        <div class="seg" id="chMetric" role="group" aria-label="Hours type">
          <button type="button" class="seg-btn is-active" data-metric="billable">Billable</button>
          <button type="button" class="seg-btn" data-metric="nonbillable">Non-billable</button>
        </div>
        <div class="seg" id="chSort" role="group" aria-label="Sort order">
          <button type="button" class="seg-btn is-active" data-sort="hours">Highest</button>
          <button type="button" class="seg-btn" data-sort="alpha">A–Z</button>
        </div>
        <button type="button" class="refresh-btn" id="chTag" title="Tag projects as retainer or project">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>
            <line x1="7" y1="7" x2="7.01" y2="7"/></svg>
          <span>Tag projects</span>
        </button>
        <button type="button" class="refresh-btn" id="chRefresh" title="Pull fresh hours from Harvest">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          <span>Refresh</span>
        </button>
      </div>
    </div>
    <div id="chNotice"></div>
    <div class="grid" id="chGrid"></div>
  </main>
  <div class="modal-backdrop" id="tagBackdrop" hidden></div>
  <aside class="tag-modal" id="tagModal" role="dialog" aria-modal="true" aria-label="Tag projects" hidden>
    <header class="tag-modal-head">
      <div>
        <h2>Tag projects</h2>
        <p class="tag-modal-sub">Mark each Harvest project as retainer or one-off project. Untagged projects show only under “All work”.</p>
      </div>
      <button type="button" class="tag-modal-close" id="tagClose" aria-label="Close">✕</button>
    </header>
    <div class="tag-modal-search">
      <input type="text" id="tagSearch" placeholder="Search projects or clients…" autocomplete="off" spellcheck="false">
    </div>
    <div class="tag-modal-body" id="tagBody"></div>
  </aside>"""


def render_client_hours_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/client-hours. Data loads from …/data."""
    body_end = """<script>
    const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const hrs = v => new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(Number(v||0));
    let chData = null;
    // View state (client-side only — each client carries its projects with both
    // total + billable series, so scope/metric/sort toggles never re-hit
    // Harvest). scope: all|retainer|project · metric: billable|nonbillable ·
    // sort: hours|alpha. Defaults: retainer work, billable hours.
    const view = { scope: 'retainer', metric: 'billable', sort: 'hours' };
    // Projects the current scope selects: everything under "all"; only projects
    // carrying the matching tag under "retainer"/"project" (untagged excluded).
    function projectsInScope(c) {
      const ps = c.projects || [];
      return view.scope === 'all' ? ps : ps.filter(p => p.tag === view.scope);
    }
    // Cumulative series are additive across disjoint projects, so summing them
    // elementwise gives the scope's cumulative series.
    function sumSeries(ps, key) {
      const out = [];
      ps.forEach(p => { const s = p[key] || []; for (let i = 0; i < s.length; i++) out[i] = (out[i] || 0) + s[i]; });
      return out;
    }
    // The selected metric's cumulative series. Non-billable is derived as the
    // total minus the billable series (both carried per project).
    function metricSeries(ps) {
      if (view.metric === 'nonbillable') {
        const all = sumSeries(ps, 'series'), bil = sumSeries(ps, 'series_billable');
        const out = [];
        for (let i = 0; i < all.length; i++) out[i] = Math.max(0, (all[i] || 0) - (bil[i] || 0));
        return out;
      }
      return sumSeries(ps, 'series_billable');
    }
    const seriesOf = c => metricSeries(projectsInScope(c));
    const totalOf  = c => { const s = seriesOf(c); return s.length ? s[s.length - 1] : 0; };
    const metricLabel = () => (view.metric === 'nonbillable' ? 'non-billable' : 'billable');

    // On/off-track status by projecting the current run-rate to month end and
    // comparing it to the goal (range, floor, or single value):
    //   behind = projected under the floor · over = projected past the ceiling ·
    //   on = landing inside the goal · none = no goal set (neutral blue).
    const STATUS = {
      on:     { color:'#0a7f3f', fill:'rgba(10,127,63,.10)',  label:'On track' },
      over:   { color:'#b7791f', fill:'rgba(183,121,31,.11)', label:'Over pace' },
      behind: { color:'#b42318', fill:'rgba(180,35,24,.09)',  label:'Behind' },
      none:   { color:'#2f6df0', fill:'rgba(47,109,240,.10)', label:'' },
    };
    function trackStatus(c, meta) {
      const lo = c.goal_min, hi = c.goal_max;
      if (lo == null && hi == null) return 'none';
      const total = totalOf(c);
      const frac = meta.days_in_month ? meta.days_elapsed / meta.days_in_month : 0;
      const projected = frac > 0 ? total / frac : total;
      if (lo != null && projected < lo * 0.95) return 'behind';
      if (hi != null && projected > hi * 1.03) return 'over';
      return 'on';
    }

    // Burn-up SVG: cumulative actual hours (solid, colored by track status) vs the
    // goal pace — a straight line for a single goal, a dashed floor for an open
    // "N+" goal, or a shaded corridor between the min and max pace for a range.
    function chart(c, meta) {
      const W = 320, H = 180, padL = 34, padR = 12, padT = 12, padB = 22;
      const days = meta.days_in_month, elapsed = meta.days_elapsed;
      const lo = (c.goal_min != null && c.goal_min > 0) ? Number(c.goal_min) : null;
      const hi = (c.goal_max != null && c.goal_max > 0) ? Number(c.goal_max) : null;
      const gTop = (hi != null ? hi : lo);
      const series = seriesOf(c);
      const st = STATUS[trackStatus(c, meta)] || STATUS.none;
      const actualMax = series.length ? Math.max.apply(null, series) : 0;
      const yMax = Math.max(actualMax, gTop || 0, 1) * 1.08;
      const x = d => padL + (W - padL - padR) * ((d - 1) / Math.max(1, days - 1));
      const y = v => H - padB - (H - padT - padB) * (v / yMax);

      // Horizontal gridlines + y labels (0, mid, max-ish).
      const ticks = [0, yMax/2, yMax];
      let grid = '';
      ticks.forEach(t => {
        const yy = y(t).toFixed(1);
        grid += `<line x1="${padL}" y1="${yy}" x2="${W-padR}" y2="${yy}" stroke="var(--grid)" stroke-width="1"/>`;
        grid += `<text x="${padL-6}" y="${(y(t)+3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8">${Math.round(t)}</text>`;
      });
      // X axis day labels (1, mid, last).
      let xlab = '';
      [1, Math.round(days/2), days].forEach(d => {
        xlab += `<text x="${x(d).toFixed(1)}" y="${H-6}" text-anchor="middle" font-size="9" fill="#94a3b8">${d}</text>`;
      });

      // Goal pace element(s): all pace lines start at (day 1, 0).
      let goalEl = '';
      const gx1 = x(1).toFixed(1), gy0 = y(0).toFixed(1), gxN = x(days).toFixed(1);
      if (lo != null && hi != null && hi !== lo) {
        // Shaded corridor between the min-pace and max-pace lines + dashed edges.
        const yLo = y(lo).toFixed(1), yHi = y(hi).toFixed(1);
        goalEl = `<polygon points="${gx1},${gy0} ${gxN},${yLo} ${gxN},${yHi}" fill="rgba(154,167,184,.16)"/>`
          + `<line x1="${gx1}" y1="${gy0}" x2="${gxN}" y2="${yLo}" stroke="var(--goal)" stroke-width="1.3" stroke-dasharray="4 3"/>`
          + `<line x1="${gx1}" y1="${gy0}" x2="${gxN}" y2="${yHi}" stroke="var(--goal)" stroke-width="1.3" stroke-dasharray="4 3"/>`;
      } else if (gTop != null) {
        // Single goal or open-ended floor: one dashed pace line.
        goalEl = `<line x1="${gx1}" y1="${gy0}" x2="${gxN}" y2="${y(gTop).toFixed(1)}" stroke="var(--goal)" stroke-width="1.5" stroke-dasharray="4 3"/>`;
      }

      // Actual cumulative line + soft fill under it, colored by track status.
      let actualEl = '';
      if (series.length) {
        const pts = series.map((v, i) => `${x(i+1).toFixed(1)},${y(v).toFixed(1)}`);
        const line = pts.join(' ');
        const area = `${x(1).toFixed(1)},${gy0} ${line} ${x(elapsed).toFixed(1)},${gy0}`;
        actualEl = `<polygon points="${area}" fill="${st.fill}"/>`
          + `<polyline points="${line}" fill="none" stroke="${st.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
        const last = series[series.length-1];
        actualEl += `<circle cx="${x(elapsed).toFixed(1)}" cy="${y(last).toFixed(1)}" r="2.6" fill="${st.color}"/>`;
      }
      return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(c.name)} hours burn-up">`
        + grid + xlab + goalEl + actualEl + `</svg>`;
    }

    function goalEditor(c) {
      const label = c.goal_label ? `<span class="g-set">${esc(c.goal_label)} goal</span>` : 'Set goal';
      const cur = c.goal_label ? esc(c.goal_label.replace(/h/g, '').replace('–', '-')) : '';
      return `<div class="goal" data-cid="${esc(c.harvest_client_id)}" data-name="${esc(c.name)}">`
        + `<button type="button" class="goal-btn">${label}</button>`
        + `<span class="goal-edit">`
          + `<input type="text" value="${cur}" placeholder="80 or 80-100 or 80+" aria-label="Monthly hours goal" title="A number (80), a range (80-100), or an open floor (80+)">`
          + `<button type="button" class="goal-save">Save</button>`
          + `<button type="button" class="goal-cancel">✕</button>`
        + `</span></div>`;
    }

    function card(c, meta) {
      const stKey = trackStatus(c, meta);
      const st = STATUS[stKey] || STATUS.none;
      const statusTxt = st.label
        ? ` · <span class="track" style="color:${st.color}">${st.label}</span>` : '';
      const kind = metricLabel();
      return `<div class="card">`
        + `<div class="card-head">`
          + `<div style="min-width:0"><div class="card-title" title="${esc(c.name)}">${esc(c.name)}</div>`
          + `<div class="card-total"><b>${hrs(totalOf(c))}h</b> ${kind}${statusTxt}</div></div>`
          + goalEditor(c)
        + `</div>`
        + `<div class="chart-wrap">${chart(c, meta)}</div>`
        + `<div class="legend"><span><i class="actual" style="border-top-color:${st.color}"></i>Hours logged</span>`
          + `<span><i class="goal"></i>${(c.goal_min != null && c.goal_max != null && c.goal_max !== c.goal_min) ? 'Goal range' : 'Goal pace'}</span></div>`
      + `</div>`;
    }

    // "updated 3h ago" from an ISO timestamp — the burn-up data is cached
    // server-side (default 6h) so refreshes don't hammer Harvest; this shows how
    // fresh the currently-displayed pull is.
    function agoTxt(iso) {
      if (!iso) return '';
      const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
      if (secs < 60) return 'just now';
      const m = Math.round(secs / 60);
      if (m < 60) return `${m}m ago`;
      const h = Math.round(m / 60);
      return `${h}h ago`;
    }

    function sortedClients(meta) {
      let clients = (meta.clients || []).slice();
      // In a split scope, drop clients with no hours of that type this month.
      if (view.scope !== 'all') clients = clients.filter(c => totalOf(c) > 0);
      if (view.sort === 'alpha') {
        clients.sort((a, b) => String(a.name||'').localeCompare(String(b.name||''),
          undefined, { sensitivity: 'base' }));
      } else {
        clients.sort((a, b) => totalOf(b) - totalOf(a));
      }
      return clients;
    }

    function render() {
      const meta = chData;
      const sub = document.getElementById('chSub');
      const fresh = meta.refreshed_at ? ` · updated ${agoTxt(meta.refreshed_at)}` : '';
      const shown = sortedClients(meta);
      const totalShown = shown.reduce((s, c) => s + totalOf(c), 0);
      const scopeLabel = view.scope === 'retainer' ? 'retainer ' : (view.scope === 'project' ? 'project ' : '');
      const kind = metricLabel();
      sub.textContent = `${meta.month_label} · day ${meta.days_elapsed} of ${meta.days_in_month}`
        + ` · ${shown.length} clients · ${hrs(totalShown)}h ${scopeLabel}${kind}`
        + (meta.account_name ? ` · ${meta.account_name}` : '') + fresh;

      const notice = document.getElementById('chNotice');
      if (meta.error) {
        const connect = !meta.connected
          ? ' <a href="/admin">Connect Harvest →</a>' : '';
        notice.innerHTML = `<div class="notice ${meta.connected?'warn':'err'}">${esc(meta.error)}${connect}</div>`;
      } else notice.innerHTML = '';

      const grid = document.getElementById('chGrid');
      if (!shown.length) {
        const msg = meta.error ? '' : (view.scope === 'all'
          ? `<div class="empty">No hours logged yet this month.</div>`
          : `<div class="empty">No ${view.scope} hours this month. Tag projects to populate this view.</div>`);
        grid.innerHTML = msg;
        return;
      }
      grid.innerHTML = shown.map(c => card(c, meta)).join('');
    }

    // Inline goal editing: toggle the input, POST on save, then re-render just
    // that client's number (cheap full re-render off the cached data).
    function wireGoals() {
      const grid = document.getElementById('chGrid');
      grid.addEventListener('click', async (ev) => {
        const g = ev.target.closest('.goal'); if (!g) return;
        const editor = g.querySelector('.goal-edit');
        const btn = g.querySelector('.goal-btn');
        if (ev.target.closest('.goal-btn')) {
          editor.classList.add('on'); btn.style.display='none';
          const inp = editor.querySelector('input'); inp.focus(); inp.select(); return;
        }
        if (ev.target.closest('.goal-cancel')) {
          editor.classList.remove('on'); btn.style.display=''; return;
        }
        if (ev.target.closest('.goal-save')) {
          const cid = g.dataset.cid;
          const raw = editor.querySelector('input').value.trim();
          const saveBtn = g.querySelector('.goal-save'); saveBtn.disabled = true;
          try {
            // The server parses "80" / "80-100" / "80+" and returns the stored
            // min/max + label; apply those so the chart re-colors immediately
            // (no Harvest round-trip — the hours series is unchanged).
            const body = new URLSearchParams({ harvest_client_id: cid, client_name: g.dataset.name, goal: raw });
            const r = await fetch('/admin/client-hours/goal', { method:'POST', credentials:'same-origin',
              headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
            const b = await r.json().catch(()=>({}));
            if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
            const client = (chData.clients||[]).find(c => String(c.harvest_client_id) === String(cid));
            if (client) {
              client.goal_min = (b.goal_min == null ? null : Number(b.goal_min));
              client.goal_max = (b.goal_max == null ? null : Number(b.goal_max));
              client.goal_label = b.goal_label || '';
            }
            render();
          } catch (e) {
            saveBtn.disabled = false;
            alert('Could not save goal: ' + (e.message||e));
          }
        }
      });
    }

    async function load(force) {
      const btn = document.getElementById('chRefresh');
      if (force) { btn.disabled = true; btn.classList.add('spin'); }
      else document.getElementById('chGrid').innerHTML =
        Array.from({length:6}, () => `<div class="skel-card"></div>`).join('');
      try {
        const r = await fetch('/admin/client-hours/data' + (force ? '?refresh=1' : ''),
          { credentials:'same-origin' });
        if (!r.ok) throw new Error('HTTP '+r.status);
        chData = await r.json();
        render();
      } catch (e) {
        document.getElementById('chSub').textContent = 'Failed to load Harvest hours.';
        if (!force) document.getElementById('chGrid').innerHTML =
          `<div class="empty">Could not load hours (${esc(e.message||'error')}). Try refreshing.</div>`;
      } finally {
        btn.disabled = false; btn.classList.remove('spin');
      }
    }
    document.getElementById('chRefresh').addEventListener('click', () => load(true));

    // Segmented controls (All/Billable hours, Highest/A–Z sort): update view
    // state and re-render off the cached data — no Harvest round-trip.
    function wireSeg(id, key, attr) {
      const seg = document.getElementById(id);
      seg.addEventListener('click', (ev) => {
        const b = ev.target.closest('.seg-btn'); if (!b) return;
        view[key] = b.dataset[attr];
        seg.querySelectorAll('.seg-btn').forEach(x => x.classList.toggle('is-active', x === b));
        if (chData) render();
      });
    }
    document.getElementById('chScope').addEventListener('change', (ev) => {
      view.scope = ev.target.value;
      if (chData) render();
    });
    wireSeg('chMetric', 'metric', 'metric');
    wireSeg('chSort', 'sort', 'sort');
    wireGoals();

    // ── Tag-projects modal ────────────────────────────────────────────────
    // Lists every project seen this month (grouped by client) with a
    // Untagged / Retainer / Project selector. Changing one persists via
    // /project-tag and updates the in-memory data so the charts + scope views
    // re-color immediately (no Harvest round-trip).
    const tagModal = document.getElementById('tagModal');
    const tagBackdrop = document.getElementById('tagBackdrop');
    const tagBody = document.getElementById('tagBody');
    const tagSearch = document.getElementById('tagSearch');

    function tagSegHtml(p) {
      const opts = [['', 'Untagged'], ['retainer', 'Retainer'], ['project', 'Project']];
      const cur = p.tag || '';
      return `<div class="tag-seg" data-pid="${esc(p.project_id)}">`
        + opts.map(([val, lbl]) =>
            `<button type="button" data-tag="${val}" class="${cur === val ? 'on' : ''}">${lbl}</button>`).join('')
        + `</div>`;
    }
    function renderTagBody() {
      const q = (tagSearch.value || '').trim().toLowerCase();
      const clients = (chData && chData.clients || []).slice()
        .sort((a, b) => String(a.name||'').localeCompare(String(b.name||''), undefined, { sensitivity:'base' }));
      let html = '';
      clients.forEach(c => {
        const projs = (c.projects || []).filter(p => {
          if (!q) return true;
          return (p.name||'').toLowerCase().includes(q) || (c.name||'').toLowerCase().includes(q);
        }).sort((a, b) => (b.total_hours||0) - (a.total_hours||0));
        if (!projs.length) return;
        html += `<div class="tag-client"><div class="tag-client-name">${esc(c.name)}</div>`;
        projs.forEach(p => {
          html += `<div class="tag-row" data-cid="${esc(c.harvest_client_id)}" data-cname="${esc(c.name)}" data-pname="${esc(p.name)}">`
            + `<div class="tag-proj"><div class="tag-proj-name" title="${esc(p.name)}">${esc(p.name)}</div>`
            + `<div class="tag-proj-hrs">${hrs(p.total_hours||0)}h this month</div></div>`
            + tagSegHtml(p) + `</div>`;
        });
        html += `</div>`;
      });
      tagBody.innerHTML = html || `<div class="tag-empty">No projects match.</div>`;
    }
    function openTags() {
      if (!chData || !chData.connected) { alert('Connect Harvest first.'); return; }
      tagSearch.value = '';
      renderTagBody();
      tagBackdrop.hidden = false; tagModal.hidden = false;
    }
    function closeTags() { tagBackdrop.hidden = true; tagModal.hidden = true; }
    document.getElementById('chTag').addEventListener('click', openTags);
    document.getElementById('tagClose').addEventListener('click', closeTags);
    tagBackdrop.addEventListener('click', closeTags);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !tagModal.hidden) closeTags(); });
    tagSearch.addEventListener('input', renderTagBody);

    tagBody.addEventListener('click', async (ev) => {
      const btn = ev.target.closest('.tag-seg button'); if (!btn) return;
      const seg = btn.closest('.tag-seg'); const row = btn.closest('.tag-row');
      const pid = seg.dataset.pid; const newTag = btn.dataset.tag;
      const prev = seg.querySelector('button.on');
      // Optimistic: reflect the choice immediately.
      seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
      try {
        const body = new URLSearchParams({ harvest_project_id: pid, tag: newTag,
          project_name: row.dataset.pname, client_name: row.dataset.cname });
        const r = await fetch('/admin/client-hours/project-tag', { method:'POST', credentials:'same-origin',
          headers:{'Content-Type':'application/x-www-form-urlencoded'}, body });
        const b = await r.json().catch(()=>({}));
        if (!r.ok || !b.ok) throw new Error(b.error || ('HTTP '+r.status));
        // Update in-memory project tag across the dataset, then re-render charts.
        (chData.clients || []).forEach(c => (c.projects || []).forEach(p => {
          if (String(p.project_id) === String(pid)) p.tag = (b.tag || null);
        }));
        render();
      } catch (e) {
        if (prev) seg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === prev));
        alert('Could not save tag: ' + (e.message||e));
      }
    });

    load(false);
  </script>"""

    return render_admin_shell_page(
        active_nav="hours",
        page_title="Client Hours",
        content_html=_HOURS_CONTENT,
        session_email=user_email,
        extra_css=_HOURS_CSS,
        body_end_html=body_end,
    )
