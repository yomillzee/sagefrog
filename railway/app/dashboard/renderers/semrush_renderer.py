"""HTML renderer for the SEMrush tab on the Penn BQ Test dashboard."""

from __future__ import annotations

import json
from typing import Any

from dashboard.utils.formatting import esc as _esc
from dashboard.utils.formatting import json_for_html_attr as _attr_json


def _safe_json(obj: Any) -> str:
    """JSON for embedding inside a ``<script>`` tag.

    Escapes only what could close the tag; quotes are left alone. For an HTML
    *attribute* value use ``_attr_json`` instead — quotes in the data (an
    apostrophe in a search query is enough) truncate the attribute otherwise.
    """
    return (
        json.dumps(obj, default=str, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _fmt_int(v: int | float) -> str:
    return f"{int(v):,}" if v else "0"


def _fmt_float(v: float, decimals: int = 2) -> str:
    return f"{float(v):.{decimals}f}" if v else "0"


def _aio_cell(kw: dict[str, Any]) -> str:
    """AI Overview status pill for a keyword row.

    Cited (domain appears in the AI Overview) > Present (keyword triggers one) > —.
    """
    if kw.get("ai_overview_cited"):
        return '<span class="smr-aio-pill cited">Cited</span>'
    if kw.get("ai_overview"):
        return '<span class="smr-aio-pill present">Present</span>'
    return '<span class="smr-aio-pill none">—</span>'


def _kpi_card(
    label: str,
    value: str,
    subtitle: str = "",
    spark_id: str = "",
    delta_html: str = "",
) -> str:
    sub = f'<div class="smr-kpi-sub">{_esc(subtitle)}</div>' if subtitle else ""
    delta = f'<div class="smr-kpi-delta">{delta_html}</div>' if delta_html else ""
    spark = f'<canvas class="smr-spark" id="{spark_id}"></canvas>' if spark_id else ""
    return f"""
    <div class="smr-kpi">
      <div class="smr-kpi-label">{_esc(label)}</div>
      <div class="smr-kpi-value">{value}</div>
      {delta}{sub}{spark}
    </div>"""


def _delta_badge(series: list[dict[str, Any]], key: str) -> str:
    """First-vs-last delta over the series for a KPI card. '' when <2 points."""
    vals = [s.get(key) for s in series if s.get(key) is not None]
    if len(vals) < 2:
        return ""
    first, last = vals[0], vals[-1]
    diff = last - first
    if diff == 0:
        return '<span class="smr-delta flat">±0</span>'
    pct = (diff / first * 100) if first else 0.0
    cls = "up" if diff > 0 else "down"
    arrow = "▲" if diff > 0 else "▼"
    sign = "+" if diff > 0 else "−"
    return (
        f'<span class="smr-delta {cls}">{arrow} {sign}{abs(diff):,} '
        f'({sign}{abs(pct):.1f}%)</span> <span class="smr-delta-note">since {_esc(series[0].get("date") or "")}</span>'
    )


def semrush_section_html(data: dict[str, Any]) -> str:
    """Return the full SEMrush tab HTML."""
    overview  = data.get("overview") or {}
    backlinks = data.get("backlinks") or {}
    keywords  = data.get("keywords") or []
    ai_ov     = data.get("ai_overview") or {}
    series    = data.get("series") or []
    pos_dist  = data.get("position_distribution") or []
    errors    = data.get("errors") or {}
    domain    = data.get("domain") or ""
    database  = data.get("database") or "us"

    # ── Error banner ───────────────────────────────────────────────────────
    error_html = ""
    if data.get("error"):
        error_html = f'<div class="smr-error">{_esc(str(data["error"]))}</div>'
    elif errors:
        items = "".join(f"<li><strong>{_esc(k)}</strong>: {_esc(v)}</li>" for k, v in errors.items())
        error_html = f'<div class="smr-errors"><strong>SEMrush warnings</strong><ul>{items}</ul></div>'

    # ── KPI strip ──────────────────────────────────────────────────────────
    bl_error      = bool(backlinks.get("error"))
    auth_score    = int(backlinks.get("authority_score") or 0)
    auth_color    = "#16a34a" if auth_score >= 40 else "#c9a227" if auth_score >= 20 else "#dc2626"
    auth_value    = f'<span style="color:{auth_color};font-weight:900">{auth_score}</span><span style="font-size:0.9rem;color:var(--muted)">/100</span>'

    def _bl(key: str) -> str:
        """Format a backlink metric — show '—' when backlinks API failed."""
        if bl_error:
            return '<span style="color:var(--muted)">—</span>'
        return _fmt_int(backlinks.get(key) or 0)

    ref_domains_subtitle = (
        f"{_fmt_int(backlinks.get('total_backlinks') or 0)} total backlinks"
        if not bl_error else "backlinks API unavailable"
    )

    kpi_html = f"""
    <div class="smr-kpis">
      {_kpi_card("Organic Traffic (est.)", _fmt_int(overview.get("organic_traffic") or 0), f"{database.upper()} database · monthly", "smrSparkTraffic", _delta_badge(series, "organic_traffic"))}
      {_kpi_card("Organic Keywords", _fmt_int(overview.get("organic_keywords") or 0), "ranking in top 100", "smrSparkKeywords", _delta_badge(series, "organic_keywords"))}
      {_kpi_card("Authority Score", auth_value, "0–100 domain authority", "smrSparkAuthority", _delta_badge(series, "authority_score"))}
      {_kpi_card("Referring Domains", _bl("referring_domains"), ref_domains_subtitle, "smrSparkRefDomains", _delta_badge(series, "referring_domains"))}
    </div>"""

    # ── Secondary KPIs ─────────────────────────────────────────────────────
    secondary_html = f"""
    <div class="smr-secondary">
      <div class="smr-secondary-item">
        <span class="smr-secondary-label">SEMrush Rank</span>
        <span class="smr-secondary-value">#{_fmt_int(overview.get("semrush_rank") or 0)}</span>
      </div>
      <div class="smr-secondary-item">
        <span class="smr-secondary-label">Organic Cost (est.)</span>
        <span class="smr-secondary-value">${_fmt_float(overview.get("organic_cost") or 0, 0)}</span>
      </div>
      <div class="smr-secondary-item">
        <span class="smr-secondary-label">Total Backlinks</span>
        <span class="smr-secondary-value">{_bl("total_backlinks")}</span>
      </div>
      <div class="smr-secondary-item">
        <span class="smr-secondary-label">Dofollow Links</span>
        <span class="smr-secondary-value">{_bl("dofollow")}</span>
      </div>
      <div class="smr-secondary-item">
        <span class="smr-secondary-label">Nofollow Links</span>
        <span class="smr-secondary-value">{_bl("nofollow")}</span>
      </div>
      <div class="smr-secondary-item">
        <span class="smr-secondary-label">Paid Keywords</span>
        <span class="smr-secondary-value">{_fmt_int(overview.get("paid_keywords") or 0)}</span>
      </div>
    </div>"""

    # ── AI Overview (Google AI Overviews visibility) ───────────────────────
    aio_kw    = int(ai_ov.get("keywords_with_aio") or 0)
    aio_cited = int(ai_ov.get("keywords_cited") or 0)
    aio_share = (aio_cited / aio_kw * 100) if aio_kw else 0.0
    aio_html = f"""
    <div class="smr-aio">
      <div class="smr-aio-head">
        <span class="smr-aio-badge">AI</span>
        <span class="smr-aio-title">Google AI Overview Visibility</span>
      </div>
      <div class="smr-aio-metrics">
        <div class="smr-aio-metric">
          <span class="smr-aio-value">{_fmt_int(aio_kw)}</span>
          <span class="smr-aio-label">ranked keywords trigger an AI Overview</span>
        </div>
        <div class="smr-aio-metric">
          <span class="smr-aio-value">{_fmt_int(aio_cited)}</span>
          <span class="smr-aio-label">of those cite {_esc(domain) or "this domain"}</span>
        </div>
        <div class="smr-aio-metric">
          <span class="smr-aio-value">{_fmt_float(aio_share, 1)}%</span>
          <span class="smr-aio-label">AI Overview citation rate</span>
        </div>
      </div>
    </div>"""

    # ── Charts ────────────────────────────────────────────────────────────
    pos_json = _safe_json(pos_dist)
    series_json = _safe_json(series)
    charts_html = """
    <div class="smr-charts-row">
      <div class="smr-chart-card">
        <div class="smr-chart-title">Keyword Position Distribution</div>
        <canvas id="smrPositionChart" style="max-height:200px;"></canvas>
      </div>
    </div>"""

    # ── Keywords table ────────────────────────────────────────────────────
    kw_count = len(keywords)
    if keywords:
        thead = """<tr>
          <th class="smr-th sortable" data-col="keyword">Keyword</th>
          <th class="smr-th sortable num" data-col="position">Position</th>
          <th class="smr-th sortable num" data-col="search_volume">Search Vol.</th>
          <th class="smr-th sortable num" data-col="traffic_pct">Traffic %</th>
          <th class="smr-th sortable num" data-col="cpc">CPC ($)</th>
          <th class="smr-th sortable" data-col="ai_overview">AI Overview</th>
          <th class="smr-th">URL</th>
        </tr>"""
        tbody_rows = ""
        for kw in keywords[:200]:
            pos = int(kw.get("position") or 0)
            pos_cls = "smr-pos-top3" if pos <= 3 else "smr-pos-top10" if pos <= 10 else ""
            url = (kw.get("url") or "").strip()
            url_html = f'<a href="{_esc(url)}" target="_blank" rel="noopener" class="smr-url">{_esc(url[:60] + ("…" if len(url) > 60 else ""))}</a>' if url else ""
            aio_cell = _aio_cell(kw)
            tbody_rows += f"""<tr>
              <td class="smr-td smr-kw">{_esc(kw.get("keyword") or "")}</td>
              <td class="smr-td num"><span class="smr-pos {pos_cls}">{pos}</span></td>
              <td class="smr-td num">{_fmt_int(kw.get("search_volume") or 0)}</td>
              <td class="smr-td num">{_fmt_float(kw.get("traffic_pct") or 0)}%</td>
              <td class="smr-td num">${_fmt_float(kw.get("cpc") or 0)}</td>
              <td class="smr-td">{aio_cell}</td>
              <td class="smr-td">{url_html}</td>
            </tr>"""
        kw_table_html = f"""
        <div class="table-wrap">
          <table class="data-table smr-table" id="smrKeywordsTable" data-rows="{_attr_json(keywords[:200])}">
            <thead>{thead}</thead>
            <tbody>{tbody_rows}</tbody>
          </table>
        </div>"""
    else:
        kw_table_html = '<p class="smr-empty">No keyword data available.</p>'

    kw_json = _safe_json(keywords[:200])

    return f"""
<section class="panel smr-panel" aria-label="SEMrush — Organic Search Intelligence">
  <div class="panel-head">
    <h2>SEMrush — <span class="smr-domain">{_esc(domain)}</span></h2>
    <span class="badge">SEMrush API · {database.upper()} database</span>
  </div>

  {error_html}
  {kpi_html}
  {secondary_html}
  {aio_html}
  {charts_html}

  <details class="smr-detail" open>
    <summary class="smr-detail-summary">
      Top Organic Keywords
      <span class="badge">{kw_count}</span>
      <span class="smr-table-hint">sorted by traffic share</span>
    </summary>
    {kw_table_html}
  </details>
</section>

<script>
(function() {{
  // ── KPI sparklines ───────────────────────────────────────────────────
  var smrSeries = {series_json};
  function drawSpark(id, key, color) {{
    var el = document.getElementById(id);
    if (!el || typeof Chart === 'undefined' || !smrSeries || smrSeries.length < 2) return;
    new Chart(el.getContext('2d'), {{
      type: 'line',
      data: {{
        labels: smrSeries.map(function(r) {{ return r.date; }}),
        datasets: [{{
          data: smrSeries.map(function(r) {{ return r[key] || 0; }}),
          borderColor: color,
          backgroundColor: color + '22',
          borderWidth: 1.5,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 3,
          tension: 0.35,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            enabled: true, intersect: false, mode: 'index',
            displayColors: false,
            callbacks: {{
              title: function(items) {{ return items[0].label; }},
              label: function(ctx) {{ return (ctx.parsed.y || 0).toLocaleString(); }}
            }}
          }}
        }},
        scales: {{ x: {{ display: false }}, y: {{ display: false }} }},
        animation: false,
      }}
    }});
  }}
  drawSpark('smrSparkTraffic',    'organic_traffic',   '#16a34a');
  drawSpark('smrSparkKeywords',   'organic_keywords',  '#2563eb');
  drawSpark('smrSparkAuthority',  'authority_score',   '#7c3aed');
  drawSpark('smrSparkRefDomains', 'referring_domains', '#ff642d');

  // ── Position distribution chart ──────────────────────────────────────
  var posData = {pos_json};
  var posCanvas = document.getElementById('smrPositionChart');
  if (posCanvas && typeof Chart !== 'undefined' && posData.length) {{
    new Chart(posCanvas.getContext('2d'), {{
      type: 'bar',
      data: {{
        labels: posData.map(function(r) {{ return r.bucket; }}),
        datasets: [{{
          label: 'Keywords',
          data: posData.map(function(r) {{ return r.count; }}),
          backgroundColor: ['#16a34a88','#22c55e88','#86efac88','#fde04788','#fca5a588'],
          borderColor:     ['#16a34a',  '#22c55e',  '#86efac',  '#fde047',  '#fca5a5'],
          borderWidth: 1.5,
          borderRadius: 4,
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ display: false }} }},
          y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }}, grid: {{ color: '#dde3ed55' }} }}
        }}
      }}
    }});
  }}

  // ── Sortable keywords table ──────────────────────────────────────────
  var smrRows = {kw_json};
  var smrTable = document.getElementById('smrKeywordsTable');
  if (smrTable) {{
    var tbody = smrTable.querySelector('tbody');
    var sortCol = null;
    var sortDir = -1;

    function renderRows(rows) {{
      tbody.innerHTML = rows.map(function(kw) {{
        var pos = kw.position || 0;
        var posCls = pos <= 3 ? 'smr-pos-top3' : pos <= 10 ? 'smr-pos-top10' : '';
        var url = (kw.url || '').trim();
        var urlHtml = url
          ? '<a href="' + url + '" target="_blank" rel="noopener" class="smr-url">' +
            (url.length > 60 ? url.substring(0, 60) + '…' : url) + '</a>'
          : '';
        var aioHtml = kw.ai_overview_cited
          ? '<span class="smr-aio-pill cited">Cited</span>'
          : kw.ai_overview
          ? '<span class="smr-aio-pill present">Present</span>'
          : '<span class="smr-aio-pill none">—</span>';
        return '<tr>' +
          '<td class="smr-td smr-kw">' + (kw.keyword || '') + '</td>' +
          '<td class="smr-td num"><span class="smr-pos ' + posCls + '">' + pos + '</span></td>' +
          '<td class="smr-td num">' + (kw.search_volume || 0).toLocaleString() + '</td>' +
          '<td class="smr-td num">' + ((kw.traffic_pct || 0).toFixed(2)) + '%</td>' +
          '<td class="smr-td num">$' + ((kw.cpc || 0).toFixed(2)) + '</td>' +
          '<td class="smr-td">' + aioHtml + '</td>' +
          '<td class="smr-td">' + urlHtml + '</td>' +
          '</tr>';
      }}).join('');
    }}

    smrTable.querySelectorAll('th.sortable').forEach(function(th) {{
      th.style.cursor = 'pointer';
      th.addEventListener('click', function() {{
        var col = th.dataset.col;
        if (sortCol === col) {{ sortDir *= -1; }} else {{ sortCol = col; sortDir = -1; }}
        smrTable.querySelectorAll('th.sortable').forEach(function(h) {{
          h.classList.remove('sort-active');
          h.removeAttribute('data-sort-dir');
        }});
        th.classList.add('sort-active');
        th.setAttribute('data-sort-dir', sortDir === -1 ? 'desc' : 'asc');
        var sorted = smrRows.slice().sort(function(a, b) {{
          var av = a[col], bv = b[col];
          if (av == null) av = typeof bv === 'string' ? '' : 0;
          if (bv == null) bv = typeof av === 'string' ? '' : 0;
          if (av < bv) return sortDir;
          if (av > bv) return -sortDir;
          return 0;
        }});
        renderRows(sorted);
      }});
    }});
  }}
}})();
</script>"""


SEMRUSH_CSS = """
    .smr-panel {
      border-top: 3px solid #ff642d;
      margin-top: 24px;
    }
    .smr-domain {
      color: #ff642d;
      font-weight: 800;
    }
    .smr-error {
      background: #fef2f2;
      border: 1px solid #fca5a5;
      border-radius: 8px;
      padding: 12px 16px;
      color: #dc2626;
      font-size: 0.84rem;
      margin-bottom: 16px;
    }
    .smr-errors {
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 14px;
      font-size: 0.84rem;
    }
    .smr-errors ul { margin: 6px 0 0; padding-left: 18px; }
    /* KPI strip */
    .smr-kpis {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 12px;
    }
    @media (max-width: 800px) { .smr-kpis { grid-template-columns: repeat(2, 1fr); } }
    .smr-kpi {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
    }
    .smr-kpi-label {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
      margin-bottom: 4px;
    }
    .smr-kpi-value {
      font-size: 1.55rem;
      font-weight: 800;
      color: var(--navy);
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
      display: flex;
      align-items: baseline;
      gap: 4px;
    }
    .smr-kpi-sub {
      font-size: 0.68rem;
      color: var(--muted);
      margin-top: 3px;
    }
    .smr-kpi-delta {
      font-size: 0.7rem;
      margin-top: 3px;
      display: flex;
      align-items: baseline;
      gap: 5px;
      flex-wrap: wrap;
    }
    .smr-delta { font-weight: 700; font-variant-numeric: tabular-nums; }
    .smr-delta.up   { color: #16a34a; }
    .smr-delta.down { color: #dc2626; }
    .smr-delta.flat { color: var(--muted); }
    .smr-delta-note { color: var(--muted); font-weight: 400; }
    .smr-spark {
      width: 100% !important;
      height: 34px !important;
      margin-top: 10px;
      display: block;
    }
    /* Secondary metrics bar */
    .smr-secondary {
      display: flex;
      flex-wrap: wrap;
      gap: 0;
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      margin-bottom: 20px;
    }
    .smr-secondary-item {
      flex: 1;
      min-width: 120px;
      padding: 10px 16px;
      border-right: 1px solid var(--border);
    }
    .smr-secondary-item:last-child { border-right: none; }
    .smr-secondary-label {
      display: block;
      font-size: 0.68rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
    }
    .smr-secondary-value {
      display: block;
      font-size: 1.1rem;
      font-weight: 800;
      color: var(--navy);
      font-variant-numeric: tabular-nums;
    }
    /* AI Overview callout */
    .smr-aio {
      background: linear-gradient(135deg, #faf5ff 0%, #f5f3ff 100%);
      border: 1px solid #d8b4fe;
      border-radius: 10px;
      padding: 14px 18px;
      margin-bottom: 20px;
    }
    .smr-aio-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
    }
    .smr-aio-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 0.62rem;
      font-weight: 800;
      letter-spacing: .04em;
      color: #fff;
      background: #7c3aed;
      border-radius: 5px;
      padding: 2px 6px;
    }
    .smr-aio-title {
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: #6b21a8;
    }
    .smr-aio-metrics {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    @media (max-width: 640px) { .smr-aio-metrics { grid-template-columns: 1fr; } }
    .smr-aio-metric { display: flex; flex-direction: column; gap: 2px; }
    .smr-aio-value {
      font-size: 1.5rem;
      font-weight: 800;
      color: #6b21a8;
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
    }
    .smr-aio-label { font-size: 0.72rem; color: var(--muted); }
    /* AI Overview keyword pills */
    .smr-aio-pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
    }
    .smr-aio-pill.cited   { background: #f3e8ff; color: #7c3aed; }
    .smr-aio-pill.present { background: #eef2ff; color: #4f46e5; }
    .smr-aio-pill.none    { background: transparent; color: var(--muted); font-weight: 400; }
    /* Charts */
    .smr-charts-row {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      margin-bottom: 20px;
    }
    .smr-chart-card {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
    }
    .smr-chart-title {
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
      margin-bottom: 12px;
    }
    /* Details accordion */
    .smr-detail { margin-bottom: 16px; }
    .smr-detail-summary {
      cursor: pointer;
      font-size: 0.9rem;
      font-weight: 650;
      color: var(--navy);
      padding: 8px 0;
      display: flex;
      align-items: center;
      gap: 8px;
      user-select: none;
      border-top: 1px solid var(--border);
    }
    .smr-detail-summary:hover { color: #ff642d; }
    .smr-table-hint {
      font-size: 0.72rem;
      font-weight: 400;
      color: var(--muted);
      margin-left: auto;
    }
    /* Keywords table */
    .smr-table { width: 100%; }
    .smr-th {
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .05em;
      color: var(--muted);
      padding: 8px 10px;
      text-align: left;
      border-bottom: 2px solid var(--border);
      white-space: nowrap;
    }
    .smr-th.num { text-align: right; }
    .smr-th.sort-active { color: var(--navy); }
    .smr-th.sort-active[data-sort-dir="asc"]::after  { content: " ↑"; }
    .smr-th.sort-active[data-sort-dir="desc"]::after { content: " ↓"; }
    .smr-td {
      padding: 7px 10px;
      font-size: 0.82rem;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }
    .smr-td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .smr-kw {
      max-width: 260px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .smr-pos {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 0.78rem;
      font-weight: 700;
      background: #f3f4f6;
      color: var(--navy);
    }
    .smr-pos-top3  { background: #f0fdf4; color: #16a34a; }
    .smr-pos-top10 { background: #eff6ff; color: #2563eb; }
    .smr-url {
      font-size: 0.78rem;
      color: var(--accent);
      text-decoration: none;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      display: block;
      max-width: 340px;
    }
    .smr-url:hover { text-decoration: underline; }
    .smr-empty {
      color: var(--muted);
      font-size: 0.84rem;
      font-style: italic;
      padding: 12px 0;
    }
"""
