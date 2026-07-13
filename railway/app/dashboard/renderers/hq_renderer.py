"""Admin "Budget HQ" page: every client's monthly spend vs budget, one view.

Self-contained HTML that mirrors the /admin page's chrome (same colour tokens,
header, card/table styling). The page shell paints instantly and fetches
/admin/hq/data for the numbers, so a cold BigQuery read per client never blocks
first paint.
"""

from __future__ import annotations

import html


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def render_hq_budget_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/hq. Data is loaded client-side from /admin/hq/data."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HQ · Sagefrog Marketing Group</title>
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <style>
    :root {{
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --accent-d:#1d4ed8; --green:#0a7f3f; --amber:#b7791f; --danger:#b42318;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
      background:linear-gradient(180deg,#eef2f7 0%,#e6edf5 100%); background-attachment:fixed; min-height:100vh; }}
    header {{ color:#fff; padding:16px 24px; display:flex; align-items:center; justify-content:space-between;
      gap:16px; flex-wrap:wrap; background:linear-gradient(120deg,#0a2540 0%,#0d2f57 100%);
      box-shadow:0 2px 14px rgba(5,18,31,.28); }}
    .brand-head {{ display:flex; align-items:center; gap:12px; }}
    .brand-mark {{ width:40px; height:40px; border-radius:11px; display:grid; place-items:center; overflow:hidden;
      background:#fff; box-shadow:0 6px 16px rgba(5,18,31,.4); flex-shrink:0; }}
    .brand-mark img {{ width:100%; height:100%; object-fit:cover; }}
    header h1 {{ margin:0; font-size:1.05rem; letter-spacing:.2px; }}
    header .who {{ font-size:.82rem; opacity:.7; }}
    header a, header button.link {{ color:#fff; text-decoration:none; background:none; border:0;
      cursor:pointer; font:inherit; opacity:.9; }}
    header a:hover {{ opacity:1; text-decoration:underline; }}
    .head-actions {{ display:flex; align-items:center; gap:16px; }}
    main {{ max-width:1180px; margin:0 auto; padding:26px 20px 56px; }}
    section {{ background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:22px 24px; margin-bottom:18px; box-shadow:0 6px 22px rgba(10,37,64,.06); }}
    .page-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:4px; }}
    .page-head h2 {{ margin:0; font-size:1.15rem; color:var(--navy); }}
    .sub {{ color:var(--muted); font-size:.86rem; margin:2px 0 0; }}
    /* Summary tiles */
    .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:16px 0 4px; }}
    .tile {{ border:1px solid var(--line); border-top:3px solid var(--accent); border-radius:12px; padding:13px 15px; background:#fff; }}
    .tile-label {{ color:var(--muted); font-size:.66rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }}
    .tile-value {{ margin-top:7px; font-size:1.55rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; }}
    .tile-sub {{ color:var(--muted); font-size:.75rem; margin-top:3px; }}
    /* Table */
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:.9rem; min-width:820px; }}
    th, td {{ text-align:right; padding:11px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
    th:first-child, td:first-child {{ text-align:left; }}
    th {{ color:var(--muted); font-weight:700; font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }}
    th.sortable {{ cursor:pointer; user-select:none; transition:color .12s; }}
    th.sortable:hover {{ color:var(--navy); }}
    th.sortable.active {{ color:var(--accent); }}
    tbody tr:hover {{ background:#f6f9fd; }}
    .cl-name {{ font-weight:700; color:var(--navy); }}
    .cl-name a {{ color:inherit; text-decoration:none; }}
    .cl-name a:hover {{ text-decoration:underline; }}
    .cl-slug {{ color:var(--muted); font-size:.74rem; font-weight:500; margin-top:1px; }}
    .num {{ font-variant-numeric:tabular-nums; }}
    /* progress bar in % of budget cell */
    .pct-cell {{ display:flex; align-items:center; justify-content:flex-end; gap:9px; cursor:default; }}
    .pct-cell .num {{ font-weight:800; }}
    .pct-cell .num.over {{ color:var(--danger); }}
    .pct-cell .num.under {{ color:var(--green); }}
    .pct-cell .num.on_track {{ color:var(--navy); }}
    .pbar {{ flex:0 0 84px; height:7px; border-radius:4px; background:#eef2f7; overflow:hidden; }}
    .pbar > span {{ display:block; height:100%; border-radius:4px; background:var(--accent); }}
    .pbar.over > span {{ background:var(--danger); }}
    .pbar.under > span {{ background:var(--green); }}
    .sess-cell {{ display:flex; align-items:center; justify-content:flex-end; gap:9px; }}
    .spark svg {{ display:block; }}
    .muted {{ color:var(--muted); }}
    .empty {{ text-align:center; color:var(--muted); padding:26px 8px; }}
    .skel {{ display:inline-block; height:12px; border-radius:6px; background:linear-gradient(90deg,#eef2f7,#e2e8f0,#eef2f7); background-size:200% 100%; animation:sh 1.2s ease-in-out infinite; }}
    @keyframes sh {{ 0%{{background-position:200% 0;}} 100%{{background-position:-200% 0;}} }}
    tfoot td {{ font-weight:800; color:var(--navy); border-top:2px solid var(--line); border-bottom:0; }}
    .status-note {{ font-size:.82rem; color:var(--muted); margin-top:12px; }}
  </style>
</head>
<body>
  <header>
    <div class="brand-head">
      <div class="brand-mark">
        <img src="/static/apple-touch-icon.png" alt="Sagefrog" width="40" height="40">
      </div>
      <div>
        <h1>Sagefrog Marketing Group · HQ</h1>
        <span class="who">Signed in as {_esc(user_email)}</span>
      </div>
    </div>
    <div class="head-actions">
      <a href="/admin/agency-trends">Trends</a>
      <a href="/admin">&larr; Admin</a>
      <form method="post" action="/logout" style="display:inline"><button type="submit" class="link">Sign out</button></form>
    </div>
  </header>
  <main>
    <section>
      <div class="page-head">
        <div>
          <h2>Monthly budget pacing</h2>
          <p class="sub" id="hqSub">Loading…</p>
        </div>
      </div>
      <div class="tiles" id="hqTiles"></div>
    </section>
    <section>
      <div class="table-wrap">
        <table id="hqTable">
          <thead><tr>
            <th class="sortable" data-key="label">Client</th>
            <th class="sortable active" data-key="pct_budget">% of budget</th>
            <th class="sortable" data-key="sessions_total">Sessions · 30d</th>
          </tr></thead>
          <tbody id="hqBody"></tbody>
          <tfoot id="hqFoot"></tfoot>
        </table>
      </div>
      <p class="status-note" id="hqNote"></p>
    </section>
  </main>
  <script>
    const money = v => new Intl.NumberFormat('en-US',{{style:'currency',currency:'USD',maximumFractionDigits:0}}).format(Number(v||0));
    const money2 = v => new Intl.NumberFormat('en-US',{{style:'currency',currency:'USD',maximumFractionDigits:2}}).format(Number(v||0));
    const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const STATUS_LABEL = {{ over:'Over pace', under:'Under pace', on_track:'On track', no_budget:'No budget' }};
    let hqData = null;
    let sort = {{ key:'pct_budget', dir:'desc' }};

    function sortVal(r, key) {{
      if (key === 'label') return (r.label||'').toLowerCase();
      const v = r[key];
      return (v == null) ? null : Number(v);
    }}
    function sortedRows() {{
      const rows = (hqData.clients||[]).slice();
      const {{key, dir}} = sort, mul = dir==='asc'?1:-1;
      rows.sort((a,b) => {{
        const x = sortVal(a,key), y = sortVal(b,key);
        const xb = (x==null), yb = (y==null);
        if (xb && yb) return 0; if (xb) return 1; if (yb) return -1;
        if (typeof x === 'string') return mul*x.localeCompare(y);
        return mul*(x-y);
      }});
      return rows;
    }}
    function pctCell(r) {{
      if (r.pct_budget == null) return '<span class="muted">—</span>';
      const cls = r.status==='over'?'over':(r.status==='under'?'under':'on_track');
      const bar = cls==='on_track' ? '' : cls;
      const w = Math.min(100, r.pct_budget);
      const budgetTxt = r.has_budget ? money2(r.monthly_budget) : 'no budget set';
      const tip = r.spend_available
        ? `${{money2(r.mtd_spend)}} spent of ${{budgetTxt}} · ${{STATUS_LABEL[r.status]||r.status}}`
        : 'No spend data';
      return `<div class="pct-cell" title="${{esc(tip)}}"><span class="num ${{cls}}">${{r.pct_budget.toFixed(0)}}%</span>`
        + `<span class="pbar ${{bar}}"><span style="width:${{w}}%"></span></span></div>`;
    }}
    // Inline SVG sparkline of daily sessions. Colour tints up (green) / down
    // (red) / flat (blue) by comparing the last third of the window to the first.
    function sparkline(series) {{
      if (!series || series.length < 2) return '';
      const w = 96, h = 26, pad = 3, n = series.length;
      const max = Math.max(...series), min = Math.min(...series), span = (max - min) || 1;
      const x = i => pad + (w - 2*pad) * (i / (n - 1));
      const y = v => pad + (h - 2*pad) * (1 - (v - min) / span);
      const pts = series.map((v, i) => `${{x(i).toFixed(1)}},${{y(v).toFixed(1)}}`).join(' ');
      const k = Math.max(1, Math.floor(n / 3));
      const avg = a => a.reduce((s, v) => s + v, 0) / a.length;
      const first = avg(series.slice(0, k)), last = avg(series.slice(-k));
      const color = last >= first * 1.02 ? '#0a7f3f' : (last <= first * 0.98 ? '#b42318' : '#2563eb');
      const lx = x(n - 1), ly = y(series[n - 1]);
      return `<span class="spark"><svg width="${{w}}" height="${{h}}" viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none" aria-hidden="true">`
        + `<polyline points="${{pts}}" fill="none" stroke="${{color}}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>`
        + `<circle cx="${{lx.toFixed(1)}}" cy="${{ly.toFixed(1)}}" r="1.9" fill="${{color}}"/></svg></span>`;
    }}
    function sessCell(r) {{
      if (!r.sessions_available) return '<span class="muted">—</span>';
      return `<div class="sess-cell">${{sparkline(r.sessions_series)}}</div>`;
    }}
    function render() {{
      const t = hqData.totals || {{}};
      document.getElementById('hqSub').textContent =
        `${{hqData.month_label}} · day ${{hqData.days_elapsed}} of ${{hqData.days_in_month}} (${{hqData.pct_month_elapsed}}% elapsed) · ${{t.client_count}} clients`;
      const tiles = [
        ['Total monthly budget', money(t.monthly_budget), `${{hqData.days_remaining}} days left`],
        ['Spend month-to-date', money(t.mtd_spend), `${{hqData.pct_month_elapsed}}% of month elapsed`],
        ['Projected month-end', money(t.projected_month_end), 'at current run-rate'],
        ['Clients over pace', String(t.clients_over_pace), `of ${{t.client_count}} clients`],
      ];
      document.getElementById('hqTiles').innerHTML = tiles.map(([l,v,s]) =>
        `<div class="tile"><div class="tile-label">${{esc(l)}}</div><div class="tile-value">${{esc(v)}}</div><div class="tile-sub">${{esc(s)}}</div></div>`).join('');

      const rows = sortedRows();
      const body = document.getElementById('hqBody');
      if (!rows.length) {{ body.innerHTML = `<tr><td colspan="3" class="empty">No clients configured yet.</td></tr>`; }}
      else body.innerHTML = rows.map(r => {{
        const dash = `/dashboard/${{encodeURIComponent(r.client_slug)}}`;
        return `<tr>`
          + `<td><div class="cl-name"><a href="${{dash}}">${{esc(r.label)}}</a></div><div class="cl-slug">${{esc(r.client_slug)}}</div></td>`
          + `<td>${{pctCell(r)}}</td>`
          + `<td>${{sessCell(r)}}</td>`
        + `</tr>`;
      }}).join('');

      const totPct = t.monthly_budget ? Math.round(t.mtd_spend / t.monthly_budget * 100) : null;
      const totTip = `${{money2(t.mtd_spend)}} spent of ${{money2(t.monthly_budget)}} budget`;
      document.getElementById('hqFoot').innerHTML = rows.length
        ? `<tr><td>All clients</td><td><div class="pct-cell" title="${{esc(totTip)}}"><span class="num">${{totPct==null?'—':totPct+'%'}}</span></div></td><td></td></tr>`
        : '';

      document.querySelectorAll('#hqTable th.sortable').forEach(th =>
        th.classList.toggle('active', th.dataset.key === sort.key));
      const sw = hqData.sessions_window || {{}};
      document.getElementById('hqNote').textContent =
        `Spend is paid media (Google, LinkedIn, Meta) from each client's BigQuery mart, as of ${{hqData.as_of}}. `
        + `Sessions sparkline is total GA4 sessions per day over the trailing ${{sw.days||30}} days (${{sw.start||''}} to ${{sw.end||''}}); green trending up, red down. `
        + `"—" means no data / no BigQuery project configured for that client yet.`;
    }}
    function skeleton() {{
      document.getElementById('hqTiles').innerHTML = Array.from({{length:4}}, () =>
        `<div class="tile"><div class="skel" style="height:10px;width:55%"></div>`
        + `<div class="skel" style="height:26px;width:70%;margin-top:9px"></div>`
        + `<div class="skel" style="height:9px;width:40%;margin-top:7px"></div></div>`).join('');
      document.getElementById('hqBody').innerHTML = Array.from({{length:6}}, () =>
        `<tr><td><span class="skel" style="width:140px"></span></td>`
        + Array.from({{length:2}}, () => `<td><span class="skel" style="width:60px"></span></td>`).join('') + `</tr>`).join('');
    }}
    document.getElementById('hqTable').querySelector('thead').addEventListener('click', ev => {{
      const th = ev.target.closest('th.sortable'); if (!th || !hqData) return;
      const key = th.dataset.key;
      if (sort.key === key) sort.dir = sort.dir==='asc'?'desc':'asc';
      else sort = {{ key, dir: key==='label'?'asc':'desc' }};
      render();
    }});
    async function load() {{
      skeleton();
      try {{
        const r = await fetch('/admin/hq/data', {{ credentials:'same-origin' }});
        if (!r.ok) throw new Error('HTTP '+r.status);
        hqData = await r.json();
        render();
      }} catch (e) {{
        document.getElementById('hqSub').textContent = 'Failed to load budget data.';
        document.getElementById('hqBody').innerHTML = `<tr><td colspan="3" class="empty">Could not load budget data (${{esc(e.message||'error')}}). Try refreshing.</td></tr>`;
      }}
    }}
    load();
  </script>
</body>
</html>"""
