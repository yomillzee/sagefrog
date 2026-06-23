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
    today = date.today()
    start = today - timedelta(days=29)
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
    .filter-row {{ display:flex; flex-wrap:wrap; gap:20px; margin-bottom:12px; align-items:center; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    .filter-label {{ color:var(--muted); font-size:.74rem; font-weight:800; text-transform:uppercase; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:5px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
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
      <label>End date<input id="endDate" type="date" value="{today.isoformat()}"></label>
      <button class="primary" type="submit">Refetch all modules</button>
    </form>

    <section>
      <h2>1. Summary card</h2>
      <div class="status" id="summaryStatus">Waiting...</div>
      <div class="cards" id="summaryCards"></div>
      <details><summary>Raw summary JSON</summary><pre id="summaryJson">{{}}</pre></details>
    </section>

    <section>
      <h2>2. Mart health table</h2>
      <div class="status" id="healthStatus">Waiting...</div>
      <div class="table-wrap"><table id="healthTable"></table></div>
      <details><summary>Raw health JSON</summary><pre id="healthJson">{{}}</pre></details>
    </section>

    <section>
      <h2>3. Google Ads explorer table</h2>
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
      <div class="status" id="explorerStatus">Waiting...</div>
      <div class="table-wrap"><table id="explorerTable"></table></div>
      <details><summary>Raw explorer JSON</summary><pre id="explorerJson">{{}}</pre></details>
    </section>
  </main>
  <script>
    const SUMMARY_API = "{_api_url('/api/clients/nixon/summary', access_key=access_key)}";
    const HEALTH_API = "{_api_url('/api/clients/nixon/marketing/health', access_key=access_key)}";
    const EXPLORER_API = "{_api_url('/api/clients/nixon/google-ads/explorer', access_key=access_key)}";
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
    async function loadSummary() {{
      setStatus('summaryStatus', 'Loading summary...');
      summaryCards.innerHTML = '';
      try {{
        const payload = await getJson(withDates(SUMMARY_API));
        const s = payload.summary || {{}};
        const cards = [
          ['spend', 'Spend', money],
          ['impressions', 'Impressions', count],
          ['clicks', 'Clicks', count],
          ['conversions', 'Conversions', count],
          ['cpc', 'CPC', money],
          ['cpa', 'CPA', money],
          ['ctr', 'CTR', pct],
        ];
        summaryCards.innerHTML = cards.map(([key, label, format]) => `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{format(s[key])}}</div></div>`).join('');
        setRaw('summaryJson', payload);
        setStatus('summaryStatus', `Loaded ${{payload.start_date}} to ${{payload.end_date}} from fact_marketing_daily.`);
      }} catch (err) {{
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
    const EXPLORER_COLUMNS = [
      {{ key:'campaign_name', label:'Campaign', left:true }},
      {{ key:'ad_group_name', label:'Ad group', left:true }},
      {{ key:'ad_label', label:'Ad / label', left:true }},
      {{ key:'spend', label:'Spend', format:money }},
      {{ key:'impressions', label:'Impr.', format:count }},
      {{ key:'clicks', label:'Clicks', format:count }},
      {{ key:'conversions', label:'Conv.', format:count }},
    ];
    // Fuzzy campaign-name match. Region tokens use word boundaries so "Patient
    // Apparel - TX & FL" matches both TX and FL without false hits inside words.
    const PRODUCT_RULES = {{ Apparel:/apparel/i, Scrubs:/scrub/i, Linens:/linen/i }};
    const REGION_RULES = {{ TX:/\\bTX\\b/i, FL:/\\bFL\\b/i, MA:/\\bMA\\b/i }};
    const productFilter = new Set();
    const regionFilter = new Set();
    let explorerRows = [];
    function buildChips(containerId, keys, stateSet) {{
      const el = document.getElementById(containerId);
      el.innerHTML = ['All', ...keys].map(k => `<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key = btn.dataset.key;
        if (key === 'All') stateSet.clear();
        else if (stateSet.has(key)) stateSet.delete(key);
        else stateSet.add(key);
        syncChips(el, stateSet);
        renderExplorer();
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
      return prodOk && regOk;
    }}
    function renderExplorer() {{
      const filtered = explorerRows.filter(explorerRowMatches);
      renderTable('explorerTable', EXPLORER_COLUMNS, filtered, 'No campaigns match these filters.');
      const filterActive = productFilter.size || regionFilter.size;
      setStatus('explorerStatus', explorerRows.length
        ? (filterActive ? `Showing ${{filtered.length}} of ${{explorerRows.length}} row(s).` : `Loaded ${{explorerRows.length}} explorer row(s).`)
        : 'No explorer rows found.');
    }}
    async function loadExplorer() {{
      setStatus('explorerStatus', 'Loading Google Ads explorer...');
      try {{
        const payload = await getJson(withDates(EXPLORER_API));
        explorerRows = payload.rows || [];
        renderExplorer();
        setRaw('explorerJson', payload);
      }} catch (err) {{
        explorerRows = [];
        setStatus('explorerStatus', err.message || String(err), true);
        setRaw('explorerJson', {{ error: err.message || String(err) }});
      }}
    }}
    function loadAll() {{
      loadSummary();
      loadHealth();
      loadExplorer();
    }}
    buildChips('productChips', ['Apparel', 'Scrubs', 'Linens'], productFilter);
    buildChips('regionChips', ['TX', 'FL', 'MA'], regionFilter);
    filters.addEventListener('submit', event => {{
      event.preventDefault();
      loadAll();
    }});
    loadAll();
  </script>
</body>
</html>"""
