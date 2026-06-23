"""Nixon portal page backed directly by BigQuery JSON endpoints."""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlencode

import client_config
from dashboard.renderers.base_layout import favicon_head_html
from dashboard.utils.formatting import esc as _esc


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


def render_nixon_bigquery_portal(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    today = date.today()
    start = today - timedelta(days=29)
    label = client_config.client_label("nixon")
    account = ""
    if use_session and session_email:
        admin = '<a href="/admin">Admin</a>' if session_is_admin else ""
        account = f"""
        <div class="account">
          <span>{_esc(session_email)}</span>
          {admin}
          <form method="post" action="/logout"><button type="submit">Sign out</button></form>
        </div>
        """

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  __FAVICON__
  <style>
    :root { --navy:#0a2540; --blue:#1769aa; --bg:#f4f7fb; --card:#fff; --line:#dbe4ef; --muted:#617089; --bad:#b42318; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; }
    header { display:flex; justify-content:space-between; gap:16px; padding:20px 28px; background:var(--card); border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:1.25rem; color:var(--navy); }
    h2 { margin:0 0 6px; font-size:1rem; color:var(--navy); }
    .sub,.note,.status { color:var(--muted); font-size:.9rem; }
    .account { display:flex; align-items:center; gap:10px; color:var(--muted); font-size:.88rem; }
    .account a,.account button { border:0; background:transparent; color:var(--blue); cursor:pointer; font:inherit; padding:0; text-decoration:none; }
    main { max-width:1480px; margin:0 auto; padding:24px; }
    .toolbar { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin-bottom:18px; }
    label { display:grid; gap:5px; font-size:.78rem; font-weight:800; color:var(--muted); text-transform:uppercase; }
    input { border:1px solid var(--line); border-radius:9px; padding:9px 11px; font:inherit; background:#fff; }
    button.primary { border:0; border-radius:9px; padding:10px 14px; background:var(--blue); color:#fff; font-weight:800; cursor:pointer; }
    .status { margin-left:auto; min-height:22px; }
    .status.error { color:var(--bad); }
    .cards { display:grid; grid-template-columns:repeat(5,minmax(150px,1fr)); gap:14px; margin-bottom:18px; }
    .card,section { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; box-shadow:0 8px 24px rgba(10,37,64,.04); }
    .card-title { color:var(--muted); font-size:.78rem; font-weight:800; text-transform:uppercase; }
    .card-value { margin-top:8px; font-size:1.45rem; font-weight:850; color:var(--navy); }
    .grid { display:grid; grid-template-columns:1fr 1.35fr; gap:18px; align-items:start; }
    section.full { grid-column:1 / -1; }
    .table-wrap { overflow:auto; max-height:560px; border:1px solid var(--line); border-radius:10px; }
    table { border-collapse:collapse; width:100%; min-width:720px; font-size:.88rem; }
    th,td { padding:10px 11px; border-bottom:1px solid #edf1f6; text-align:right; white-space:nowrap; }
    th { position:sticky; top:0; background:#f8fafc; color:#40516b; font-size:.74rem; text-transform:uppercase; z-index:1; }
    th.left,td.left { text-align:left; }
    .empty { padding:28px; color:var(--muted); text-align:center; }
    .health { display:flex; flex-wrap:wrap; gap:8px; }
    .pill { border-radius:999px; padding:6px 10px; background:#eef4fb; color:#40516b; font-size:.8rem; }
    @media (max-width:960px) { .cards,.grid { grid-template-columns:1fr; } header { flex-direction:column; } .status { margin-left:0; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>__LABEL__ Paid Media</h1>
      <p class="sub">Live BigQuery view - no Postgres snapshots - marketing_marts only</p>
    </div>
    __ACCOUNT__
  </header>
  <main>
    <form class="toolbar" id="filters">
      <label>Start date<input id="startDate" name="start_date" type="date" value="__START__"></label>
      <label>End date<input id="endDate" name="end_date" type="date" value="__END__"></label>
      <button class="primary" type="submit">Update</button>
      <div class="status" id="status">Loading BigQuery data...</div>
    </form>
    <div class="cards" id="summaryCards"></div>
    <div class="grid">
      <section><h2>Totals by source</h2><p class="note">From marketing_marts.fact_marketing_daily.</p><div class="table-wrap"><table id="sourceTable"></table></div></section>
      <section><h2>Daily trend</h2><p class="note">Daily performance by source.</p><div class="table-wrap"><table id="trendTable"></table></div></section>
      <section><h2>Top campaigns by spend</h2><p class="note">Highest-spend campaigns in range.</p><div class="table-wrap"><table id="campaignTable"></table></div></section>
      <section><h2>Mart health</h2><p class="note">From marketing_marts.mart_health.</p><div class="health" id="healthPills"></div></section>
      <section class="full"><h2>Google Ads Explorer</h2><p class="note">Campaign to ad group to ad rows from marketing_marts.explorer_google_ads_daily.</p><div class="table-wrap"><table id="explorerTable"></table></div></section>
    </div>
  </main>
  <script>
    const MARKETING_API = __MARKETING_API__;
    const EXPLORER_API = __EXPLORER_API__;
    const HEALTH_API = __HEALTH_API__;
    const dollars = new Intl.NumberFormat('en-US', { style:'currency', currency:'USD', maximumFractionDigits:2 });
    const nums = new Intl.NumberFormat('en-US');
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
    const n = v => Number(v || 0);
    const money = v => dollars.format(n(v));
    const count = v => nums.format(Math.round(n(v)));
    const metric = (v, key) => key === 'spend' || key === 'conversion_value' ? money(v) : count(v);
    function withDates(base) {
      const sep = base.includes('?') ? '&' : '?';
      const params = new URLSearchParams({ start_date:startDate.value, end_date:endDate.value });
      return base + sep + params.toString();
    }
    async function getJson(url) {
      const resp = await fetch(url, { credentials:'same-origin' });
      const body = await resp.json().catch(() => ({ detail:resp.statusText }));
      if (!resp.ok) throw new Error(body.detail || resp.statusText || 'Request failed');
      return body;
    }
    function cards(summary) {
      summaryCards.innerHTML = [['spend','Spend'],['impressions','Impressions'],['clicks','Clicks'],['conversions','Conversions'],['conversion_value','Conversion value']]
        .map(([k,l]) => `<div class="card"><div class="card-title">${l}</div><div class="card-value">${metric(summary?.[k], k)}</div></div>`).join('');
    }
    function renderTable(id, cols, rows, empty) {
      const el = document.getElementById(id);
      if (!rows?.length) { el.innerHTML = `<tbody><tr><td class="empty">${esc(empty)}</td></tr></tbody>`; return; }
      el.innerHTML = `<thead><tr>${cols.map(c => `<th class="${c.left ? 'left' : ''}">${esc(c.label)}</th>`).join('')}</tr></thead>` +
        `<tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${c.left ? 'left' : ''}">${esc(c.format ? c.format(r[c.key], r) : r[c.key])}</td>`).join('')}</tr>`).join('')}</tbody>`;
    }
    function health(payload) {
      const rows = payload.rows || [];
      healthPills.innerHTML = rows.length ? rows.flatMap(row => Object.entries(row).map(([k,v]) => `<span class="pill">${esc(k)}: ${esc(v)}</span>`)).join('') : '<span class="pill">No health rows found</span>';
    }
    async function load() {
      status.className = 'status'; status.textContent = 'Loading BigQuery data...';
      try {
        const [m, e, h] = await Promise.all([getJson(withDates(MARKETING_API)), getJson(withDates(EXPLORER_API)), getJson(HEALTH_API)]);
        cards(m.summary || {});
        renderTable('sourceTable', [{key:'source',label:'Source',left:true},{key:'spend',label:'Spend',format:money},{key:'impressions',label:'Impr.',format:count},{key:'clicks',label:'Clicks',format:count},{key:'conversions',label:'Conv.',format:count},{key:'conversion_value',label:'Value',format:money}], m.by_source || [], 'No source totals found.');
        renderTable('trendTable', [{key:'date',label:'Date',left:true},{key:'source',label:'Source',left:true},{key:'spend',label:'Spend',format:money},{key:'impressions',label:'Impr.',format:count},{key:'clicks',label:'Clicks',format:count},{key:'conversions',label:'Conv.',format:count}], m.daily_trend || [], 'No daily rows found.');
        renderTable('campaignTable', [{key:'source',label:'Source',left:true},{key:'campaign_name',label:'Campaign',left:true},{key:'spend',label:'Spend',format:money},{key:'impressions',label:'Impr.',format:count},{key:'clicks',label:'Clicks',format:count},{key:'conversions',label:'Conv.',format:count}], m.top_campaigns_by_spend || [], 'No campaigns found.');
        renderTable('explorerTable', [{key:'campaign_name',label:'Campaign',left:true},{key:'ad_group_name',label:'Ad group',left:true},{key:'ad_label',label:'Ad',left:true},{key:'spend',label:'Spend',format:money},{key:'impressions',label:'Impr.',format:count},{key:'clicks',label:'Clicks',format:count},{key:'conversions',label:'Conv.',format:count},{key:'conversion_value',label:'Value',format:money}], e.rows || [], 'No Google Ads explorer rows found.');
        health(h);
        status.textContent = `Loaded ${m.date_range?.start_date || ''} to ${m.date_range?.end_date || ''} directly from BigQuery.`;
      } catch (err) {
        status.className = 'status error'; status.textContent = err.message || String(err);
      }
    }
    filters.addEventListener('submit', ev => { ev.preventDefault(); load(); });
    load();
  </script>
</body>
</html>"""
    return (
        html.replace("__TITLE__", f"{_esc(label)} - BigQuery Portal")
        .replace("__FAVICON__", favicon_head_html())
        .replace("__LABEL__", _esc(label))
        .replace("__ACCOUNT__", account)
        .replace("__START__", start.isoformat())
        .replace("__END__", today.isoformat())
        .replace("__MARKETING_API__", json.dumps(_api_url("/api/clients/nixon/marketing", access_key=access_key)))
        .replace("__EXPLORER_API__", json.dumps(_api_url("/api/clients/nixon/google-ads/explorer", access_key=access_key)))
        .replace("__HEALTH_API__", json.dumps(_api_url("/api/clients/nixon/marketing/health", access_key=access_key)))
    )
