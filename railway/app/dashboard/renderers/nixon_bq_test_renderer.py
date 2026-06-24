"""Internal Nixon BigQuery lab page.

This renderer is intentionally plain: it validates server-side BigQuery API
responses without touching Postgres snapshots or the main dashboard UI.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from dashboard.renderers.base_layout import favicon_head_html
from dashboard.utils.formatting import esc as _esc


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


def render_nixon_bigquery_test_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    # Default to the "Last 30 days" preset: 30 complete days ending yesterday
    # (today is partial, excluded).
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=30)
    account_html = ""
    if use_session and session_email:
        admin_link = '<a href="/admin">Admin</a>' if session_is_admin else ""
        account_html = f"""
        <div class="account">
          <span>{_esc(session_email)}</span>
          {admin_link}
          <form method="post" action="/logout"><button type="submit">Sign out</button></form>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nixon BigQuery Test</title>
  {favicon_head_html()}
  <style>
    :root {{ --bg:#f5f7fb; --card:#fff; --line:#d8e1ee; --navy:#0a2540; --blue:#1769aa; --muted:#66758f; --bad:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; }}
    header {{ display:flex; justify-content:space-between; gap:16px; padding:20px 28px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1400px; margin:0 auto; padding:24px; }}
    h1 {{ margin:0; color:var(--navy); font-size:1.35rem; }}
    h2 {{ margin:0 0 8px; color:var(--navy); font-size:1.05rem; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    .account {{ display:flex; align-items:center; gap:10px; font-size:.88rem; color:var(--muted); }}
    .account a,.account button {{ border:0; background:transparent; color:var(--blue); cursor:pointer; padding:0; font:inherit; }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin-bottom:18px; }}
    label {{ display:grid; gap:5px; color:var(--muted); font-size:.78rem; font-weight:800; text-transform:uppercase; }}
    input {{ border:1px solid var(--line); border-radius:8px; padding:9px 11px; font:inherit; background:#fff; }}
    button.primary {{ border:0; border-radius:8px; padding:10px 14px; background:var(--blue); color:#fff; font-weight:800; cursor:pointer; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:18px; }}
    .cards {{ display:grid; grid-template-columns:repeat(7,minmax(120px,1fr)); gap:10px; }}
    .card {{ border:1px solid var(--line); border-radius:10px; padding:12px; background:#fbfdff; }}
    .card-title {{ color:var(--muted); font-size:.74rem; text-transform:uppercase; font-weight:800; }}
    .card-value {{ margin-top:6px; font-size:1.2rem; color:var(--navy); font-weight:850; }}
    .status {{ color:var(--muted); font-size:.9rem; margin-bottom:10px; }}
    .status.error {{ color:var(--bad); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:10px; }}
    table {{ border-collapse:collapse; width:100%; min-width:780px; font-size:.88rem; }}
    th,td {{ padding:9px 10px; border-bottom:1px solid #edf1f6; text-align:right; white-space:nowrap; }}
    th {{ background:#f8fafc; color:#40516b; text-transform:uppercase; font-size:.72rem; position:sticky; top:0; }}
    th.left,td.left {{ text-align:left; }}
    .empty {{ color:var(--muted); padding:20px; text-align:center; }}
    details {{ margin-top:12px; }}
    summary {{ color:var(--blue); cursor:pointer; font-weight:700; }}
    pre {{ max-height:360px; overflow:auto; background:#0b1020; color:#d7e3ff; border-radius:10px; padding:12px; font-size:.78rem; }}
    code {{ background:#eef4fb; padding:2px 4px; border-radius:4px; }}
    .page-path {{ font-weight:600; color:#1f2d40; word-break:break-all; }}
    .chart-wrap {{ position:relative; border:1px solid var(--line); border-radius:10px; padding:10px 12px; background:#fff; }}
    .trend-svg {{ width:100%; height:260px; display:block; }}
    .chart-note {{ font-size:.76rem; color:var(--muted); margin-top:8px; }}
    .chart-tip {{ position:absolute; pointer-events:none; background:#0b1020; color:#e8eefc; font-size:.74rem; line-height:1.5; padding:7px 9px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,.25); transform:translate(-50%,-112%); white-space:nowrap; z-index:5; }}
    .metric-swatch {{ width:10px; height:10px; border-radius:2px; display:inline-block; vertical-align:middle; margin-right:4px; }}
    .filter-row {{ display:flex; flex-wrap:wrap; gap:20px; margin-bottom:12px; align-items:center; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    .filter-label {{ color:var(--muted); font-size:.74rem; font-weight:800; text-transform:uppercase; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:5px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
    .tree-row[data-expandable] {{ cursor:pointer; }}
    .tree-row[data-expandable]:hover {{ background:#f3f8ff; }}
    .caret {{ display:inline-block; width:14px; color:var(--muted); font-size:.8rem; }}
    .tree-row[data-expandable] .caret::before {{ content:'\\25B8'; }}
    .tree-row[data-expandable].open .caret::before {{ content:'\\25BE'; }}
    .indent1 {{ display:inline-block; width:18px; }}
    .indent2 {{ display:inline-block; width:36px; }}
    .lvl-campaign .tree-name {{ font-weight:800; color:var(--navy); }}
    .lvl-group .tree-name {{ font-weight:600; }}
    .lvl-ad td.left {{ color:var(--muted); }}
    .pill {{ display:inline-block; padding:1px 7px; border-radius:999px; font-size:.66rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; vertical-align:middle; margin-right:7px; }}
    .pill-google {{ background:#e8f0fe; color:#1a73e8; }}
    .pill-linkedin {{ background:#e6f0f8; color:#0a66c2; }}
    .ad-cell {{ display:inline-flex; align-items:center; gap:9px; vertical-align:middle; }}
    .ad-thumb {{ width:34px; height:34px; border-radius:5px; object-fit:cover; border:1px solid var(--line); background:#f0f3f8; flex:0 0 auto; }}
    .ad-meta {{ display:flex; flex-direction:column; line-height:1.25; }}
    .ad-label {{ font-weight:600; color:#1f2d40; }}
    .ad-type {{ font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
    .ad-copy {{ display:flex; flex-direction:column; gap:1px; margin-top:3px; }}
    .ad-copy-line {{ font-size:.78rem; color:var(--muted); white-space:normal; }}
    .ad-copy-tag {{ display:inline-block; min-width:34px; color:#9aa7bd; font-weight:700; font-size:.66rem; text-transform:uppercase; margin-right:5px; }}
    @media (max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(120px,1fr)); }} header {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Nixon BigQuery Test</h1>
      <p>Internal validation page. Browser fetches Railway API endpoints only; Railway queries <code>marketing_marts</code>.</p>
    </div>
    {account_html}
  </header>
  <main>
    <form class="toolbar" id="filters">
      <label>Start date<input id="startDate" type="date" value="{start.isoformat()}"></label>
      <label>End date<input id="endDate" type="date" value="{end.isoformat()}"></label>
      <button class="primary" type="submit">Refetch all modules</button>
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
      <div class="filter-group">
        <span class="filter-label">Platform</span>
        <div class="chips" id="platformChips"></div>
      </div>
    </div>

    <section>
      <h2>1. Summary card</h2>
      <div class="status" id="summaryStatus">Waiting…</div>
      <div class="cards" id="summaryCards"></div>
      <details><summary>Raw summary JSON</summary><pre id="summaryJson">{{}}</pre></details>
    </section>

    <section>
      <h2>2. Trend chart</h2>
      <div class="filter-row">
        <div class="filter-group">
          <span class="filter-label">Metrics</span>
          <div class="chips" id="metricChips"></div>
        </div>
      </div>
      <div class="status" id="chartStatus">Waiting…</div>
      <div class="chart-wrap" id="trendChartWrap">
        <svg id="trendChart" class="trend-svg" preserveAspectRatio="none"></svg>
        <div id="chartTip" class="chart-tip" hidden></div>
      </div>
      <p class="chart-note">Each line is normalized to its own min–max, so the overlay compares trend shapes, not absolute scale. Hover for actual values.</p>
    </section>

    <section>
      <h2>3. Mart health table</h2>
      <div class="status" id="healthStatus">Waiting…</div>
      <div class="table-wrap"><table id="healthTable"></table></div>
      <details><summary>Raw health JSON</summary><pre id="healthJson">{{}}</pre></details>
    </section>

    <section>
      <h2>4. Campaign explorer (Google + LinkedIn)</h2>
      <div class="filter-row" id="explorerFilters">
        <div class="filter-group">
          <span class="filter-label">Product</span>
          <div class="chips" id="productChips"></div>
        </div>
        <div class="filter-group">
          <span class="filter-label">Region</span>
          <div class="chips" id="regionChips"></div>
        </div>
      </div>
      <div class="status" id="explorerStatus">Waiting…</div>
      <div class="table-wrap"><table id="explorerTable"></table></div>
      <details><summary>Raw explorer JSON</summary><pre id="explorerJson">{{}}</pre></details>
    </section>

    <section>
      <h2>5. Page performance</h2>
      <div class="filter-row" id="pageFilters">
        <div class="filter-group">
          <span class="filter-label">AI referral</span>
          <div class="chips" id="aiChips"></div>
        </div>
        <div class="filter-group">
          <span class="filter-label">Source</span>
          <div class="chips" id="sourceChips"></div>
        </div>
      </div>
      <div class="status" id="pagesStatus">Waiting…</div>
      <div class="table-wrap"><table id="pagesTable"></table></div>
      <details><summary>Raw page JSON</summary><pre id="pagesJson">{{}}</pre></details>
    </section>
  </main>
  <script>
    const SUMMARY_API = "{_api_url('/api/clients/nixon/summary', access_key=access_key)}";
    const HEALTH_API = "{_api_url('/api/clients/nixon/marketing/health', access_key=access_key)}";
    const EXPLORER_API = "{_api_url('/api/clients/nixon/google-ads/explorer', access_key=access_key)}";
    const LINKEDIN_EXPLORER_API = "{_api_url('/api/clients/nixon/linkedin/explorer', access_key=access_key)}";
    const PAGES_TOP_API = "{_api_url('/api/clients/nixon/pages/top', access_key=access_key)}";
    const PAGES_SOURCES_API = "{_api_url('/api/clients/nixon/pages/sources', access_key=access_key)}";
    const dollars = new Intl.NumberFormat('en-US', {{ style:'currency', currency:'USD', maximumFractionDigits:2 }});
    const nums = new Intl.NumberFormat('en-US');
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }}[c]));
    const num = v => Number(v || 0);
    const money = v => dollars.format(num(v));
    const count = v => nums.format(Math.round(num(v)));
    const pct = v => `${{num(v).toFixed(2)}}%`;
    function withDates(base) {{
      const sep = base.includes('?') ? '&' : '?';
      const params = new URLSearchParams({{ start_date:startDate.value, end_date:endDate.value }});
      return base + sep + params.toString();
    }}
    async function getJson(url) {{
      const resp = await fetch(url, {{ credentials:'same-origin' }});
      const body = await resp.json().catch(() => ({{ detail:resp.statusText }}));
      if (!resp.ok) throw new Error(body.detail || resp.statusText || 'Request failed');
      return body;
    }}
    function setStatus(id, text, isError=false) {{
      const el = document.getElementById(id);
      el.textContent = text;
      el.className = isError ? 'status error' : 'status';
    }}
    function setRaw(id, payload) {{
      document.getElementById(id).textContent = JSON.stringify(payload, null, 2);
    }}
    function renderTable(id, columns, rows, emptyText) {{
      const el = document.getElementById(id);
      if (!rows || !rows.length) {{
        el.innerHTML = `<tbody><tr><td class="empty">${{esc(emptyText)}}</td></tr></tbody>`;
        return;
      }}
      el.innerHTML = `<thead><tr>${{columns.map(c => `<th class="${{c.left ? 'left' : ''}}">${{esc(c.label)}}</th>`).join('')}}</tr></thead>` +
        `<tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td class="${{c.left ? 'left' : ''}}">${{esc(c.format ? c.format(row[c.key], row) : row[c.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    const SUMMARY_CARDS = [
      ['spend', 'Spend', money],
      ['impressions', 'Impressions', count],
      ['clicks', 'Clicks', count],
      ['conversions', 'Conversions', count],
      ['cpc', 'CPC', money],
      ['cpa', 'CPA', money],
      ['ctr', 'CTR', pct],
    ];
    // Platform filter is shared by the summary cards and the explorer. Stored as
    // display labels ('Google'/'LinkedIn'); lowercased to match source keys.
    const platformFilter = new Set();
    let summaryPayload = null;
    function selectedSummary() {{
      if (!summaryPayload) return {{}};
      const by = summaryPayload.by_source || null;
      // No per-source breakdown (live endpoint) or no platform selected → combined.
      if (!by || platformFilter.size === 0) return summaryPayload.summary || {{}};
      const acc = {{ spend:0, impressions:0, clicks:0, conversions:0 }};
      for (const p of platformFilter) {{
        const src = by[p.toLowerCase()];
        if (!src) continue;
        acc.spend += num(src.spend); acc.impressions += num(src.impressions);
        acc.clicks += num(src.clicks); acc.conversions += num(src.conversions);
      }}
      return {{ ...acc,
        cpc: acc.clicks ? acc.spend / acc.clicks : 0,
        cpa: acc.conversions ? acc.spend / acc.conversions : 0,
        ctr: acc.impressions ? acc.clicks / acc.impressions * 100 : 0 }};
    }}
    function renderSummary() {{
      const s = selectedSummary();
      summaryCards.innerHTML = SUMMARY_CARDS.map(([key, label, format]) => `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{format(s[key])}}</div></div>`).join('');
    }}
    // ---- Trend chart (overlay normalized daily series) ----
    const CHART_METRICS = [
      {{ key:'spend', label:'Spend', color:'#1769aa', fmt:money }},
      {{ key:'impressions', label:'Impressions', color:'#7c3aed', fmt:count }},
      {{ key:'clicks', label:'Clicks', color:'#0a7f3f', fmt:count }},
      {{ key:'cpc', label:'CPC', color:'#d97706', fmt:money }},
      {{ key:'cpa', label:'CPA', color:'#dc2626', fmt:money }},
      {{ key:'ctr', label:'CTR', color:'#0891b2', fmt:pct }},
    ];
    const chartMetrics = new Set(['spend', 'clicks']);
    let chartDaily = [];
    function buildChartDaily() {{
      // Per-date-per-source rows from the summary endpoint → filter by platform,
      // sum per day, derive cpc/cpa/ctr. Mirrors the summary-card platform logic.
      const daily = (summaryPayload && summaryPayload.daily) ? summaryPayload.daily : [];
      const sel = platformFilter.size ? new Set([...platformFilter].map(p => p.toLowerCase())) : null;
      const byDate = new Map();
      for (const r of daily) {{
        if (sel && !sel.has(String(r.source || '').toLowerCase())) continue;
        let d = byDate.get(r.date);
        if (!d) {{ d = {{ date:r.date, spend:0, impressions:0, clicks:0, conversions:0 }}; byDate.set(r.date, d); }}
        d.spend += num(r.spend); d.impressions += num(r.impressions); d.clicks += num(r.clicks); d.conversions += num(r.conversions);
      }}
      const out = [...byDate.values()].sort((a, b) => a.date < b.date ? -1 : 1);
      for (const d of out) {{
        d.cpc = d.clicks ? d.spend / d.clicks : 0;
        d.cpa = d.conversions ? d.spend / d.conversions : 0;
        d.ctr = d.impressions ? d.clicks / d.impressions * 100 : 0;
      }}
      return out;
    }}
    function renderChart() {{
      chartDaily = buildChartDaily();
      const svg = document.getElementById('trendChart');
      const W = 800, H = 260, padL = 12, padR = 12, padT = 14, padB = 26;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const n = chartDaily.length;
      svg.setAttribute('viewBox', `0 0 ${{W}} ${{H}}`);
      if (!n) {{ svg.innerHTML = ''; setStatus('chartStatus', 'No data for this range.'); return; }}
      const active = CHART_METRICS.filter(m => chartMetrics.has(m.key));
      const xAt = i => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
      const parts = [
        `<line x1="${{padL}}" y1="${{padT}}" x2="${{padL}}" y2="${{padT + plotH}}" stroke="#eef2f7"/>`,
        `<line x1="${{padL}}" y1="${{padT + plotH}}" x2="${{padL + plotW}}" y2="${{padT + plotH}}" stroke="#e3e9f1"/>`,
      ];
      for (const m of active) {{
        const vals = chartDaily.map(d => num(d[m.key]));
        const mn = Math.min(...vals), mx = Math.max(...vals), span = (mx - mn) || 1;
        const pts = vals.map((v, i) => `${{xAt(i).toFixed(1)}},${{(padT + (1 - (v - mn) / span) * plotH).toFixed(1)}}`).join(' ');
        parts.push(`<polyline fill="none" stroke="${{m.color}}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${{pts}}"/>`);
      }}
      const lblIdx = n === 1 ? [0] : [0, Math.floor((n - 1) / 2), n - 1];
      for (const i of lblIdx) {{
        const anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
        parts.push(`<text x="${{xAt(i).toFixed(1)}}" y="${{H - 8}}" font-size="10" fill="#66758f" text-anchor="${{anchor}}">${{esc(String(chartDaily[i].date).slice(5))}}</text>`);
      }}
      svg.innerHTML = parts.join('');
      setStatus('chartStatus', `${{n}} day(s) · ${{active.length}} metric(s) overlaid.`);
    }}
    function buildMetricChips() {{
      const el = document.getElementById('metricChips');
      el.innerHTML = CHART_METRICS.map(m => `<button type="button" class="chip" data-key="${{m.key}}"><span class="metric-swatch" style="background:${{m.color}}"></span>${{esc(m.label)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const k = btn.dataset.key;
        if (chartMetrics.has(k)) chartMetrics.delete(k); else chartMetrics.add(k);
        syncMetricChips();
        renderChart();
      }}));
      syncMetricChips();
    }}
    function syncMetricChips() {{
      document.querySelectorAll('#metricChips .chip').forEach(btn => btn.classList.toggle('active', chartMetrics.has(btn.dataset.key)));
    }}
    function setupChartHover() {{
      const wrap = document.getElementById('trendChartWrap');
      const svg = document.getElementById('trendChart');
      const tip = document.getElementById('chartTip');
      svg.addEventListener('mousemove', ev => {{
        if (!chartDaily.length) {{ tip.hidden = true; return; }}
        const rect = svg.getBoundingClientRect();
        const W = 800, padL = 12, padR = 12;
        const frac = ((ev.clientX - rect.left) / rect.width * W - padL) / (W - padL - padR);
        let i = Math.round(frac * (chartDaily.length - 1));
        i = Math.max(0, Math.min(chartDaily.length - 1, i));
        const d = chartDaily[i];
        const active = CHART_METRICS.filter(m => chartMetrics.has(m.key));
        if (!active.length) {{ tip.hidden = true; return; }}
        tip.innerHTML = `<strong>${{esc(d.date)}}</strong>` + active.map(m => `<br><span class="metric-swatch" style="background:${{m.color}}"></span>${{esc(m.label)}}: ${{m.fmt(d[m.key])}}`).join('');
        tip.hidden = false;
        const wrapRect = wrap.getBoundingClientRect();
        tip.style.left = (ev.clientX - wrapRect.left) + 'px';
        tip.style.top = (ev.clientY - wrapRect.top) + 'px';
      }});
      svg.addEventListener('mouseleave', () => {{ tip.hidden = true; }});
    }}
    async function loadSummary() {{
      setStatus('summaryStatus', 'Loading summary...');
      try {{
        summaryPayload = await getJson(withDates(SUMMARY_API));
        renderSummary();
        renderChart();
        setRaw('summaryJson', summaryPayload);
        const note = summaryPayload.by_source ? '' : ' (no per-platform breakdown — showing combined)';
        setStatus('summaryStatus', `Loaded ${{summaryPayload.start_date}} to ${{summaryPayload.end_date}} from fact_marketing_daily.${{note}}`);
      }} catch (err) {{
        summaryPayload = null;
        setStatus('summaryStatus', err.message || String(err), true);
        setRaw('summaryJson', {{ error: err.message || String(err) }});
      }}
    }}
    async function loadHealth() {{
      setStatus('healthStatus', 'Loading mart health...');
      try {{
        const payload = await getJson(withDates(HEALTH_API));
        const rows = payload.rows || [];
        renderTable('healthTable', [
          {{ key:'source', label:'Source', left:true }},
          {{ key:'row_count', label:'Rows', format:count }},
          {{ key:'earliest_date', label:'Earliest', left:true }},
          {{ key:'latest_date', label:'Latest', left:true }},
          {{ key:'spend', label:'Spend', format:money }},
          {{ key:'impressions', label:'Impr.', format:count }},
          {{ key:'clicks', label:'Clicks', format:count }},
          {{ key:'conversions', label:'Conv.', format:count }},
        ], rows, 'No mart health rows found.');
        setRaw('healthJson', payload);
        setStatus('healthStatus', rows.length ? `Loaded ${{rows.length}} health row(s).` : 'No health rows found.');
      }} catch (err) {{
        setStatus('healthStatus', err.message || String(err), true);
        setRaw('healthJson', {{ error: err.message || String(err) }});
      }}
    }}
    const METRIC_COLS = [
      {{ key:'spend', label:'Spend', format:money }},
      {{ key:'impressions', label:'Impr.', format:count }},
      {{ key:'clicks', label:'Clicks', format:count }},
      {{ key:'conversions', label:'Conv.', format:count }},
      {{ key:'ctr', label:'CTR', format:pct }},
    ];
    // Fuzzy campaign-name match. Region tokens use word boundaries so "Patient
    // Apparel - TX & FL" matches both TX and FL without false hits inside words.
    const PRODUCT_RULES = {{ Apparel:/apparel/i, Scrubs:/scrub/i, Linens:/linen/i }};
    const REGION_RULES = {{ TX:/\\bTX\\b/i, FL:/\\bFL\\b/i, MA:/\\bMA\\b/i }};
    const productFilter = new Set();
    const regionFilter = new Set();
    let explorerRows = [];
    function buildChips(containerId, keys, stateSet, onChange) {{
      const el = document.getElementById(containerId);
      el.innerHTML = ['All', ...keys].map(k => `<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key = btn.dataset.key;
        if (key === 'All') stateSet.clear();
        else if (stateSet.has(key)) stateSet.delete(key);
        else stateSet.add(key);
        syncChips(el, stateSet);
        (onChange || renderExplorer)();
      }}));
      syncChips(el, stateSet);
    }}
    function syncChips(el, stateSet) {{
      el.querySelectorAll('.chip').forEach(btn => {{
        const key = btn.dataset.key;
        const active = key === 'All' ? stateSet.size === 0 : stateSet.has(key);
        btn.classList.toggle('active', active);
      }});
    }}
    function explorerRowMatches(row) {{
      const name = String(row.campaign_name || '');
      const prodOk = !productFilter.size || [...productFilter].some(k => PRODUCT_RULES[k].test(name));
      const regOk = !regionFilter.size || [...regionFilter].some(k => REGION_RULES[k].test(name));
      const platOk = !platformFilter.size || [...platformFilter].some(k => k.toLowerCase() === (row.platform || ''));
      return prodOk && regOk && platOk;
    }}
    // Group flat ad rows into campaign > ad group > ad, summing metrics at each level.
    function zeroMetrics() {{ return {{ spend:0, impressions:0, clicks:0, conversions:0 }}; }}
    function addMetrics(acc, r) {{
      acc.spend += num(r.spend); acc.impressions += num(r.impressions);
      acc.clicks += num(r.clicks); acc.conversions += num(r.conversions);
    }}
    function withCtr(m) {{ return {{ ...m, ctr: m.impressions ? (num(m.clicks) / num(m.impressions) * 100) : 0 }}; }}
    function buildExplorerTree(rows) {{
      const campaigns = new Map();
      for (const r of rows) {{
        const cName = r.campaign_name || '\\u2014';
        const platform = (r.platform || 'google').toLowerCase();
        const cKey = platform + '|' + cName;
        if (!campaigns.has(cKey)) campaigns.set(cKey, {{ name:cName, platform, metrics:zeroMetrics(), groups:new Map() }});
        const camp = campaigns.get(cKey);
        addMetrics(camp.metrics, r);
        const gName = r.ad_group_name || '\\u2014';
        if (!camp.groups.has(gName)) camp.groups.set(gName, {{ name:gName, metrics:zeroMetrics(), ads:[] }});
        const grp = camp.groups.get(gName);
        addMetrics(grp.metrics, r);
        grp.ads.push(r);
      }}
      // Highest-spend campaigns first, platforms intermixed.
      return new Map([...campaigns.entries()].sort((a, b) => b[1].metrics.spend - a[1].metrics.spend));
    }}
    function metricCells(m) {{
      const withc = withCtr(m);
      return METRIC_COLS.map(c => `<td>${{c.format(withc[c.key])}}</td>`).join('');
    }}
    function platformPill(p) {{
      const key = (p || 'google').toLowerCase() === 'linkedin' ? 'linkedin' : 'google';
      return `<span class="pill pill-${{key}}">${{key === 'linkedin' ? 'LinkedIn' : 'Google'}}</span>`;
    }}
    function adCell(ad) {{
      const label = esc(ad.ad_label || ad.ad_name || '\\u2014');
      const type = ad.media_type ? `<span class="ad-type">${{esc(ad.media_type)}}</span>` : '';
      const thumb = ad.thumbnail_url
        ? `<img class="ad-thumb" src="${{esc(ad.thumbnail_url)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`
        : '';
      // Responsive search ad copy beneath the ad: headlines + first description.
      const copyLines = [
        ['H1', ad.headline_1], ['H2', ad.headline_2], ['H3', ad.headline_3], ['Desc', ad.description_1],
      ].filter(([, v]) => v).map(
        ([tag, v]) => `<span class="ad-copy-line"><span class="ad-copy-tag">${{tag}}</span>${{esc(v)}}</span>`
      ).join('');
      const copy = copyLines ? `<div class="ad-copy">${{copyLines}}</div>` : '';
      return `<div class="ad-cell">${{thumb}}<span class="ad-meta"><span class="ad-label">${{label}}</span>${{type}}${{copy}}</span></div>`;
    }}
    function renderExplorer() {{
      const filtered = explorerRows.filter(explorerRowMatches);
      const el = document.getElementById('explorerTable');
      const tree = buildExplorerTree(filtered);
      if (!tree.size) {{
        el.innerHTML = `<tbody><tr><td class="empty">No campaigns match these filters.</td></tr></tbody>`;
      }} else {{
        const head = `<thead><tr><th class="left">Campaign / Ad group / Ad</th>${{METRIC_COLS.map(c => `<th>${{esc(c.label)}}</th>`).join('')}}</tr></thead>`;
        let body = '';
        let cIdx = 0;
        for (const camp of tree.values()) {{
          const cId = 'c' + (cIdx++);
          const gCount = camp.groups.size;
          body += `<tr class="tree-row lvl-campaign" data-id="${{cId}}" data-expandable="1"><td class="left"><span class="caret"></span>${{platformPill(camp.platform)}}<span class="tree-name">${{esc(camp.name)}}</span> <span class="muted">(${{gCount}} ad group${{gCount === 1 ? '' : 's'}})</span></td>${{metricCells(camp.metrics)}}</tr>`;
          let gIdx = 0;
          for (const grp of camp.groups.values()) {{
            const gId = cId + 'g' + (gIdx++);
            const aCount = grp.ads.length;
            body += `<tr class="tree-row lvl-group" data-id="${{gId}}" data-parent="${{cId}}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${{esc(grp.name)}}</span> <span class="muted">(${{aCount}} ad${{aCount === 1 ? '' : 's'}})</span></td>${{metricCells(grp.metrics)}}</tr>`;
            for (const ad of grp.ads) {{
              body += `<tr class="tree-row lvl-ad" data-parent="${{gId}}" hidden><td class="left"><span class="indent2"></span>${{adCell(ad)}}</td>${{metricCells(ad)}}</tr>`;
            }}
          }}
        }}
        el.innerHTML = head + `<tbody>${{body}}</tbody>`;
      }}
      const filterActive = productFilter.size || regionFilter.size;
      const totalCampaigns = new Set(explorerRows.map(r => r.campaign_name || '\\u2014')).size;
      setStatus('explorerStatus', explorerRows.length
        ? (filterActive
            ? `Showing ${{tree.size}} of ${{totalCampaigns}} campaign(s).`
            : `Loaded ${{tree.size}} campaign(s) across ${{explorerRows.length}} ads.`)
        : 'No explorer rows found.');
    }}
    // Expand/collapse a node; collapsing also hides its whole descendant subtree.
    function toggleExplorerRow(row) {{
      const id = row.dataset.id;
      const table = row.closest('table');
      const expanded = row.classList.toggle('open');
      if (expanded) {{
        table.querySelectorAll(`tr[data-parent="${{id}}"]`).forEach(c => {{ c.hidden = false; }});
      }} else {{
        const stack = [id];
        while (stack.length) {{
          const pid = stack.pop();
          table.querySelectorAll(`tr[data-parent="${{pid}}"]`).forEach(c => {{
            c.hidden = true;
            c.classList.remove('open');
            if (c.dataset.id) stack.push(c.dataset.id);
          }});
        }}
      }}
    }}
    // Normalize Google + LinkedIn rows to a common shape. LinkedIn's hierarchy
    // (campaign group > campaign/ad set > creative) maps onto the Google tree
    // levels (campaign > ad group > ad); creatives carry a thumbnail.
    function normalizeExplorerRows(google, linkedin) {{
      const out = [];
      for (const r of (google && google.rows ? google.rows : [])) {{
        out.push({{ platform:'google', campaign_name:r.campaign_name, ad_group_name:r.ad_group_name, ad_label:r.ad_label,
          headline_1:r.headline_1, headline_2:r.headline_2, headline_3:r.headline_3,
          description_1:r.description_1, description_2:r.description_2,
          ad_name:r.ad_name, final_url:r.final_url, ad_type:r.ad_type,
          thumbnail_url:'', media_type:r.ad_type || '', spend:num(r.spend), impressions:num(r.impressions), clicks:num(r.clicks), conversions:num(r.conversions) }});
      }}
      for (const r of (linkedin && linkedin.rows ? linkedin.rows : [])) {{
        out.push({{ platform:'linkedin', campaign_name:r.campaign_group_name || r.campaign_name, ad_group_name:r.campaign_name, ad_label:r.creative_name, thumbnail_url:r.thumbnail_url || r.image_url || '', media_type:r.media_type || '', spend:num(r.spend), impressions:num(r.impressions), clicks:num(r.clicks), conversions:num(r.conversions) }});
      }}
      return out;
    }}
    async function loadExplorer() {{
      setStatus('explorerStatus', 'Loading campaign explorer...');
      // LinkedIn endpoint is optional — if it 404s (not yet built), show Google only.
      const [g, l] = await Promise.all([
        getJson(withDates(EXPLORER_API)).catch(() => ({{ rows: [] }})),
        getJson(withDates(LINKEDIN_EXPLORER_API)).catch(() => ({{ rows: [] }})),
      ]);
      explorerRows = normalizeExplorerRows(g, l);
      renderExplorer();
      setRaw('explorerJson', {{ google: g, linkedin: l }});
    }}
    // ---- Page performance ----
    let pagesTopRows = [];      // /pages/top — all traffic, per page
    let pagesSourceRows = [];   // /pages/sources — per page x source x AI
    const pageSourceFilter = new Set();  // selected source_platform values
    let pageAiFilter = 'all';            // 'all' | 'ai' | 'non'
    function fmtDuration(secs) {{
      secs = Math.round(num(secs));
      if (secs < 60) return secs + 's';
      const m = Math.floor(secs / 60), s = secs % 60;
      return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
    }}
    function pageFiltersActive() {{ return pageSourceFilter.size > 0 || pageAiFilter !== 'all'; }}
    function pageSourceRowMatches(r) {{
      if (pageAiFilter === 'ai' && !r.is_ai_referral) return false;
      if (pageAiFilter === 'non' && r.is_ai_referral) return false;
      if (pageSourceFilter.size && !pageSourceFilter.has(r.source_platform)) return false;
      return true;
    }}
    function aggregatePages(rows) {{
      const map = new Map();
      for (const r of rows) {{
        let g = map.get(r.page_path);
        if (!g) {{ g = {{ page_path:r.page_path, page_group:r.page_group, page_topic:r.page_topic, page_views:0, users:0, sessions:0, engagement_seconds:0 }}; map.set(r.page_path, g); }}
        g.page_views += num(r.page_views); g.users += num(r.users); g.sessions += num(r.sessions); g.engagement_seconds += num(r.engagement_seconds);
      }}
      return [...map.values()].sort((a, b) => b.page_views - a.page_views);
    }}
    function renderPages() {{
      // No filter → the all-traffic top-pages view; any filter → recompute from
      // the per-source rows so the list reflects only that source / AI traffic.
      const base = pageFiltersActive() ? aggregatePages(pagesSourceRows.filter(pageSourceRowMatches)) : pagesTopRows;
      const el = document.getElementById('pagesTable');
      if (!base.length) {{
        el.innerHTML = `<tbody><tr><td class="empty">No pages for this range / filter.</td></tr></tbody>`;
        setStatus('pagesStatus', 'No pages for this range / filter.');
        return;
      }}
      const head = `<thead><tr><th class="left">Page</th><th>Views</th><th>Users</th><th>Sessions</th><th>Avg engt</th></tr></thead>`;
      const body = base.slice(0, 100).map(p => {{
        const sub = p.page_group ? ` <span class="muted">${{esc(p.page_group)}}${{p.page_topic ? ' \\u00b7 ' + esc(p.page_topic) : ''}}</span>` : '';
        const engt = p.sessions ? p.engagement_seconds / p.sessions : 0;
        return `<tr><td class="left"><span class="page-path">${{esc(p.page_path)}}</span>${{sub}}</td><td>${{count(p.page_views)}}</td><td>${{count(p.users)}}</td><td>${{count(p.sessions)}}</td><td>${{fmtDuration(engt)}}</td></tr>`;
      }}).join('');
      el.innerHTML = head + `<tbody>${{body}}</tbody>`;
      setStatus('pagesStatus', `${{Math.min(base.length, 100)}} of ${{base.length}} page(s)` + (pageFiltersActive() ? ' (filtered)' : '') + '.');
    }}
    function buildPageFilters() {{
      const aiEl = document.getElementById('aiChips');
      aiEl.innerHTML = [['all', 'All'], ['ai', 'AI only'], ['non', 'Non-AI']].map(([k, l]) => `<button type="button" class="chip" data-ai="${{k}}">${{esc(l)}}</button>`).join('');
      const syncAi = () => aiEl.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.ai === pageAiFilter));
      aiEl.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{ pageAiFilter = btn.dataset.ai; syncAi(); renderPages(); }}));
      syncAi();
      const sources = [...new Set(pagesSourceRows.map(r => r.source_platform).filter(Boolean))].sort();
      const srcEl = document.getElementById('sourceChips');
      srcEl.innerHTML = ['All', ...sources].map(k => `<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      const syncSrc = () => srcEl.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key === 'All' ? pageSourceFilter.size === 0 : pageSourceFilter.has(b.dataset.key)));
      srcEl.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key = btn.dataset.key;
        if (key === 'All') pageSourceFilter.clear();
        else if (pageSourceFilter.has(key)) pageSourceFilter.delete(key);
        else pageSourceFilter.add(key);
        syncSrc(); renderPages();
      }}));
      syncSrc();
    }}
    async function loadPages() {{
      setStatus('pagesStatus', 'Loading page performance...');
      const [top, src] = await Promise.all([
        getJson(withDates(PAGES_TOP_API)).catch(() => ({{ rows: [] }})),
        getJson(withDates(PAGES_SOURCES_API)).catch(() => ({{ rows: [] }})),
      ]);
      pagesTopRows = top.rows || [];
      pagesSourceRows = src.rows || [];
      buildPageFilters();
      renderPages();
      setRaw('pagesJson', {{ top, sources: src }});
    }}
    function loadAll() {{
      loadSummary();
      loadHealth();
      loadExplorer();
      loadPages();
    }}
    buildChips('productChips', ['Apparel', 'Scrubs', 'Linens'], productFilter);
    buildChips('regionChips', ['TX', 'FL', 'MA'], regionFilter);
    buildChips('platformChips', ['Google', 'LinkedIn'], platformFilter, () => {{ renderSummary(); renderChart(); renderExplorer(); }});
    buildMetricChips();
    setupChartHover();
    document.getElementById('explorerTable').addEventListener('click', event => {{
      const row = event.target.closest('tr[data-expandable]');
      if (row) toggleExplorerRow(row);
    }});
    // Date presets — compute [start,end] locally, set the inputs, then refetch.
    const fmtDate = d => `${{d.getFullYear()}}-${{String(d.getMonth() + 1).padStart(2, '0')}}-${{String(d.getDate()).padStart(2, '0')}}`;
    function presetRange(name) {{
      const today = new Date();
      let s, e = today;
      // "Last N days" = N complete days ending yesterday (today is partial, excluded).
      // "This month" is month-to-date and "Last month" is the full prior month.
      const lastNDays = n => {{
        e = new Date(today); e.setDate(today.getDate() - 1);
        s = new Date(today); s.setDate(today.getDate() - n);
      }};
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
    // Manually editing a date clears the active preset highlight.
    [startDate, endDate].forEach(inp => inp.addEventListener('change', () => highlightPreset(null)));
    filters.addEventListener('submit', event => {{
      event.preventDefault();
      highlightPreset(null);
      loadAll();
    }});
    loadAll();
  </script>
</body>
</html>"""
