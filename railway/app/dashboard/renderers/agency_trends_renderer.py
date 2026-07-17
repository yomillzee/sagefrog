"""Admin "Agency Trends" page: every client's week-over-week paid-media momentum.

Companion to the HQ budget page and built the same way — a self-contained shell
that mirrors the admin chrome and fetches /admin/agency-trends/data for the
numbers, so the DuckDB rollup never blocks first paint. The data itself is
computed by dashboard.services.agency_trends_service (one DuckDB scan over
metrics_daily joined to the connector→client map).
"""

from __future__ import annotations

import html


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def render_agency_trends_page(*, user_email: str) -> str:
    """Full HTML for GET /admin/agency-trends. Data loads from …/data."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agency Trends · Sagefrog Marketing Group</title>
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <style>
    :root {{
      --navy:#0a2540; --ink:#0f1c2e; --muted:#5a6578; --border:#e3e8f0; --line:#e3e8f0;
      --accent:#2563eb; --green:#0a7f3f; --amber:#b7791f; --danger:#b42318;
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
    .page-head h2 {{ margin:0; font-size:1.15rem; color:var(--navy); }}
    .sub {{ color:var(--muted); font-size:.86rem; margin:2px 0 0; }}
    .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:16px 0 4px; }}
    .tile {{ border:1px solid var(--line); border-top:3px solid var(--accent); border-radius:12px; padding:13px 15px; background:#fff; }}
    .tile-label {{ color:var(--muted); font-size:.66rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }}
    .tile-value {{ margin-top:7px; font-size:1.55rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; }}
    .tile-value.up {{ color:var(--green); }} .tile-value.down {{ color:var(--danger); }}
    .tile-sub {{ color:var(--muted); font-size:.75rem; margin-top:3px; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:.9rem; min-width:640px; }}
    th, td {{ text-align:right; padding:11px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
    th:first-child, td:first-child {{ text-align:left; }}
    th {{ color:var(--muted); font-weight:700; font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; }}
    tbody tr:hover {{ background:#f6f9fd; }}
    .cl-name {{ font-weight:700; color:var(--navy); }}
    .cl-name a {{ color:inherit; text-decoration:none; }}
    .cl-name a:hover {{ text-decoration:underline; }}
    .cl-slug {{ color:var(--muted); font-size:.74rem; font-weight:500; margin-top:1px; }}
    .num {{ font-variant-numeric:tabular-nums; }}
    /* diverging week-over-week bar, centered at zero */
    .wow-cell {{ display:flex; align-items:center; justify-content:flex-end; gap:10px; }}
    .wow-track {{ position:relative; flex:0 0 120px; height:9px; background:#eef2f7; border-radius:5px; }}
    .wow-mid {{ position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background:#cbd5e1; }}
    .wow-fill {{ position:absolute; top:0; bottom:0; border-radius:5px; }}
    .wow-fill.pos {{ left:50%; background:var(--green); }}
    .wow-fill.neg {{ right:50%; background:var(--danger); }}
    .wow-pct {{ min-width:60px; text-align:right; font-variant-numeric:tabular-nums; font-weight:700; }}
    .wow-pct.pos {{ color:var(--green); }} .wow-pct.neg {{ color:var(--danger); }}
    .wow-pct.flat {{ color:var(--muted); }}
    .chan {{ display:flex; align-items:center; gap:12px; margin:10px 0; }}
    .chan-name {{ width:92px; font-weight:700; color:var(--navy); text-transform:capitalize; font-size:.9rem; }}
    .chan-bar {{ flex:1; height:14px; background:#eef2f7; border-radius:7px; overflow:hidden; }}
    .chan-bar > span {{ display:block; height:100%; border-radius:7px; background:var(--accent); }}
    .chan-val {{ width:150px; text-align:right; font-variant-numeric:tabular-nums; color:var(--muted); font-size:.85rem; }}
    .muted {{ color:var(--muted); }}
    .empty {{ text-align:center; color:var(--muted); padding:26px 8px; }}
    .skel {{ display:inline-block; height:12px; border-radius:6px; background:linear-gradient(90deg,#eef2f7,#e2e8f0,#eef2f7); background-size:200% 100%; animation:sh 1.2s ease-in-out infinite; }}
    @keyframes sh {{ 0%{{background-position:200% 0;}} 100%{{background-position:-200% 0;}} }}
    .status-note {{ font-size:.82rem; color:var(--muted); margin-top:14px; }}
    .pill {{ display:inline-block; font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em;
      padding:2px 7px; border-radius:999px; background:#eef2f7; color:var(--muted); margin-left:7px; vertical-align:middle; }}
  </style>
</head>
<body>
  <header>
    <div class="brand-head">
      <div class="brand-mark">
        <img src="/static/apple-touch-icon.png" alt="Sagefrog" width="40" height="40">
      </div>
      <div>
        <h1>Sagefrog Marketing Group · Agency Trends</h1>
        <span class="who">Signed in as {_esc(user_email)}</span>
      </div>
    </div>
    <div class="head-actions">
      <a href="/admin/hq">HQ</a>
      <a href="/admin/docs">Docs</a>
      <a href="/admin">&larr; Admin</a>
      <form method="post" action="/logout" style="display:inline"><button type="submit" class="link">Sign out</button></form>
    </div>
  </header>
  <main>
    <section>
      <div class="page-head">
        <h2>Paid-media momentum, week over week</h2>
        <p class="sub" id="atSub">Loading…</p>
      </div>
      <div class="tiles" id="atTiles"></div>
    </section>
    <section>
      <div class="page-head"><h2>By client <span class="pill">steepest decline first</span></h2></div>
      <div class="table-wrap">
        <table id="atTable">
          <thead><tr>
            <th>Client</th>
            <th>Last 7 days</th>
            <th>Prior 7 days</th>
            <th>Week over week</th>
          </tr></thead>
          <tbody id="atBody"></tbody>
        </table>
      </div>
      <p class="status-note" id="atNote"></p>
    </section>
    <section>
      <div class="page-head"><h2>Where spend went (last 7 days)</h2></div>
      <div id="atChannels" style="margin-top:14px"></div>
    </section>
  </main>
  <script>
    const money = v => new Intl.NumberFormat('en-US',{{style:'currency',currency:'USD',maximumFractionDigits:0}}).format(Number(v||0));
    const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const pctTxt = p => (p>0?'+':'') + Number(p).toFixed(1) + '%';

    function tile(label, value, sub, cls) {{
      return `<div class="tile"><div class="tile-label">${{esc(label)}}</div>`
        + `<div class="tile-value ${{cls||''}}">${{esc(value)}}</div>`
        + `<div class="tile-sub">${{esc(sub)}}</div></div>`;
    }}
    function wowCell(r) {{
      if (r.pct_change == null) return '<div class="wow-cell"><span class="muted">new</span></div>';
      const pos = r.pct_change >= 0;
      const cls = r.pct_change > 0 ? 'pos' : (r.pct_change < 0 ? 'neg' : 'flat');
      const w = Math.min(50, Math.abs(r.pct_change) / 60 * 50); // 60% saturates half the track
      const fill = `<span class="wow-fill ${{pos?'pos':'neg'}}" style="width:${{w}}%"></span>`;
      return `<div class="wow-cell"><span class="wow-track"><span class="wow-mid"></span>${{fill}}</span>`
        + `<span class="wow-pct ${{cls}}">${{pctTxt(r.pct_change)}}</span></div>`;
    }}
    function render(d) {{
      const t = d.totals || {{}};
      const L = (d.window || {{}}).last || {{}};
      document.getElementById('atSub').textContent =
        `${{L.start}} → ${{L.end}} vs prior 7 days · ${{t.client_count}} clients with spend`;
      const dir = t.pct_change == null ? '' : (t.pct_change >= 0 ? 'up' : 'down');
      const topCh = (d.channels && d.channels[0]) ? d.channels[0] : null;
      document.getElementById('atTiles').innerHTML =
        tile('Spend · last 7 days', money(t.last_7), 'paid media (Google, LinkedIn, Meta)')
        + tile('Week over week', t.pct_change==null?'—':pctTxt(t.pct_change), `${{money(t.delta)}} vs prior 7 days`, dir)
        + tile('Clients declining', String(t.clients_declining||0), `of ${{t.client_count}} with spend`, (t.clients_declining>0?'down':''))
        + tile('Top channel', topCh?topCh.source:'—', topCh?`${{topCh.pct_of_total}}% of spend`:'', '');

      const body = document.getElementById('atBody');
      const rows = d.clients || [];
      if (!rows.length) {{ body.innerHTML = `<tr><td colspan="4" class="empty">No paid-media spend in the client marts for this window yet.</td></tr>`; }}
      else body.innerHTML = rows.map(r => {{
        const name = `<div class="cl-name"><a href="/dashboard/${{encodeURIComponent(r.client_slug)}}">${{esc(r.label)}}</a></div><div class="cl-slug">${{esc(r.client_slug)}}</div>`;
        return `<tr><td>${{name}}</td>`
          + `<td class="num">${{money(r.last_7)}}</td>`
          + `<td class="num">${{money(r.prior_7)}}</td>`
          + `<td>${{wowCell(r)}}</td></tr>`;
      }}).join('');

      const chans = d.channels || [];
      const maxSpend = chans.reduce((m,c) => Math.max(m, c.spend), 0) || 1;
      document.getElementById('atChannels').innerHTML = chans.length
        ? chans.map(c => `<div class="chan"><div class="chan-name">${{esc(c.source)}}</div>`
            + `<div class="chan-bar"><span style="width:${{Math.max(2, c.spend/maxSpend*100)}}%"></span></div>`
            + `<div class="chan-val">${{money(c.spend)}} · ${{c.pct_of_total}}%</div></div>`).join('')
        : `<p class="empty">No spend by channel in this window.</p>`;

      document.getElementById('atNote').textContent =
        `Paid media (Google, LinkedIn, Meta) from each client's BigQuery mart — the same source as HQ — `
        + `lined up in DuckDB to compare the last 7 days with the 7 before, as of ${{d.as_of}}. Cached ~15 min.`;
    }}
    function skeleton() {{
      document.getElementById('atTiles').innerHTML = Array.from({{length:4}}, () =>
        `<div class="tile"><div class="skel" style="height:10px;width:55%"></div>`
        + `<div class="skel" style="height:26px;width:70%;margin-top:9px"></div></div>`).join('');
      document.getElementById('atBody').innerHTML = Array.from({{length:5}}, () =>
        `<tr><td><span class="skel" style="width:150px"></span></td>`
        + Array.from({{length:3}}, () => `<td><span class="skel" style="width:70px"></span></td>`).join('') + `</tr>`).join('');
    }}
    async function load() {{
      skeleton();
      try {{
        const r = await fetch('/admin/agency-trends/data', {{ credentials:'same-origin' }});
        if (!r.ok) throw new Error('HTTP '+r.status);
        render(await r.json());
      }} catch (e) {{
        document.getElementById('atSub').textContent = 'Failed to load agency trends.';
        document.getElementById('atBody').innerHTML = `<tr><td colspan="4" class="empty">Could not load data (${{esc(e.message||'error')}}).</td></tr>`;
      }}
    }}
    load();
  </script>
</body>
</html>"""
