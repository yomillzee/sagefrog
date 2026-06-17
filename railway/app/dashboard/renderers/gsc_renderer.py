"""HTML renderer for the Search Console section on the Penn BQ Test dashboard."""

from __future__ import annotations

import json
from typing import Any

from dashboard.utils.formatting import esc as _esc


def _safe_json(obj: Any) -> str:
    return (
        json.dumps(obj, default=str, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _fmt_int(v: int | float) -> str:
    return f"{int(v):,}"


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


def _fmt_pos(v: float) -> str:
    return f"{v:.1f}" if v else "—"


def _gsc_table(rows: list[dict], columns: list[tuple[str, str, str]]) -> str:
    """Render a sortable GSC data table.

    columns: list of (key, label, align) where align is 'left' or 'right'
    """
    if not rows:
        return (
            '<p class="gsc-empty">No data for this period.</p>'
        )
    head_cells = "".join(
        f'<th class="sortable{"" if align == "left" else " num"}" '
        f'data-col="{key}">{label}<span class="sort-icon"></span></th>'
        for key, label, align in columns
    )
    tbody_rows = ""
    for row in rows:
        cells = ""
        for key, _label, align in columns:
            val = row.get(key)
            if align == "right":
                if key == "ctr":
                    display = _fmt_pct(float(val) if val is not None else 0)
                elif key == "avg_position":
                    display = _fmt_pos(float(val) if val is not None else 0)
                else:
                    display = _fmt_int(float(val) if val is not None else 0)
                cells += f'<td class="num">{_esc(display)}</td>'
            else:
                cells += f'<td class="name gsc-name">{_esc(str(val or ""))}</td>'
        tbody_rows += f"<tr>{cells}</tr>"
    return f"""
    <div class="table-wrap">
      <table class="data-table gsc-table" data-rows='{_safe_json(rows)}'>
        <thead><tr>{head_cells}</tr></thead>
        <tbody>{tbody_rows}</tbody>
      </table>
    </div>"""


def gsc_section_html(gsc: dict[str, Any]) -> str:
    """Return the GSC section HTML for the GSC tab panel."""
    kpis = gsc.get("kpis") or {}
    daily = gsc.get("daily") or []
    top_queries = gsc.get("top_queries") or []
    top_pages = gsc.get("top_pages") or []
    top_query_page = gsc.get("top_query_page") or []
    errors = gsc.get("errors") or {}

    clicks = int(kpis.get("clicks") or 0)
    impressions = int(kpis.get("impressions") or 0)
    ctr = float(kpis.get("ctr") or 0)
    avg_position = float(kpis.get("avg_position") or 0)

    error_html = ""
    if errors:
        items = "".join(f"<li><strong>{_esc(k)}</strong>: {_esc(v)}</li>" for k, v in errors.items())
        error_html = f'<div class="gsc-errors"><strong>GSC query warnings</strong><ul>{items}</ul></div>'

    # Queries table
    query_table_html = _gsc_table(top_queries, [
        ("query", "Query", "left"),
        ("clicks", "Clicks", "right"),
        ("impressions", "Impressions", "right"),
        ("ctr", "CTR", "right"),
        ("avg_position", "Avg. Position", "right"),
    ])

    # Pages table
    page_table_html = _gsc_table(top_pages, [
        ("page_url", "Page URL", "left"),
        ("clicks", "Clicks", "right"),
        ("impressions", "Impressions", "right"),
        ("ctr", "CTR", "right"),
        ("avg_position", "Avg. Position", "right"),
    ])

    # Query + Page table
    qp_table_html = _gsc_table(top_query_page, [
        ("query", "Query", "left"),
        ("page_url", "Page URL", "left"),
        ("clicks", "Clicks", "right"),
        ("impressions", "Impressions", "right"),
        ("ctr", "CTR", "right"),
        ("avg_position", "Avg. Position", "right"),
    ])

    daily_json = _safe_json(daily)

    return f"""
<section class="panel gsc-panel" aria-label="Search Console — Organic Search">
  <div class="panel-head">
    <h2>Search Console — Organic Search</h2>
    <span class="badge">Google Search Console · BigQuery Export</span>
  </div>
  {error_html}

  <!-- GSC KPI strip -->
  <div class="gsc-kpis">
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Organic Clicks</div>
      <div class="gsc-kpi-value">{_esc(_fmt_int(clicks))}</div>
    </div>
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Impressions</div>
      <div class="gsc-kpi-value">{_esc(_fmt_int(impressions))}</div>
    </div>
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Avg. CTR</div>
      <div class="gsc-kpi-value">{_esc(_fmt_pct(ctr))}</div>
    </div>
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Avg. Position</div>
      <div class="gsc-kpi-value">{_esc(_fmt_pos(avg_position))}</div>
    </div>
  </div>

  <!-- Daily trend chart -->
  <div class="gsc-chart-section">
    <div class="gsc-chart-controls">
      <button class="gsc-metric-btn active" data-gsc-metric="clicks">Clicks</button>
      <button class="gsc-metric-btn" data-gsc-metric="impressions">Impressions</button>
      <button class="gsc-metric-btn" data-gsc-metric="ctr">CTR</button>
      <button class="gsc-metric-btn" data-gsc-metric="avg_position">Avg. Position</button>
    </div>
    <canvas id="gscTrendChart" style="max-height:220px;"></canvas>
  </div>

  <!-- Top Queries -->
  <details class="gsc-detail" open>
    <summary class="gsc-detail-summary">
      Top Queries <span class="badge">{len(top_queries)}</span>
    </summary>
    {query_table_html}
  </details>

  <!-- Top Pages -->
  <details class="gsc-detail" open>
    <summary class="gsc-detail-summary">
      Top Pages <span class="badge">{len(top_pages)}</span>
    </summary>
    {page_table_html}
  </details>

  <!-- Top Query + Page -->
  <details class="gsc-detail">
    <summary class="gsc-detail-summary">
      Top Query / Page Combinations <span class="badge">{len(top_query_page)}</span>
    </summary>
    {qp_table_html}
  </details>
</section>

<script>
(function() {{
  var gscDaily = {daily_json};

  // Chart
  var gscMetricColors = {{
    clicks: '#16a34a',
    impressions: '#0a66c2',
    ctr: '#c9a227',
    avg_position: '#7c3aed'
  }};
  var gscMetricLabels = {{
    clicks: 'Organic Clicks',
    impressions: 'Impressions',
    ctr: 'CTR (%)',
    avg_position: 'Avg. Position'
  }};

  var gscChartCanvas = document.getElementById('gscTrendChart');
  var gscActiveMetric = 'clicks';
  var gscChart = null;

  function gscChartData(metric) {{
    return {{
      labels: gscDaily.map(function(r) {{ return r.date; }}),
      datasets: [{{
        label: gscMetricLabels[metric],
        data: gscDaily.map(function(r) {{ return r[metric] || 0; }}),
        backgroundColor: gscMetricColors[metric] + '33',
        borderColor: gscMetricColors[metric],
        borderWidth: 1.5,
        borderRadius: 3,
      }}]
    }};
  }}

  if (gscChartCanvas && typeof Chart !== 'undefined') {{
    gscChart = new Chart(gscChartCanvas.getContext('2d'), {{
      type: 'bar',
      data: gscChartData(gscActiveMetric),
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10 }} }},
          y: {{ beginAtZero: true, grid: {{ color: '#dde3ed55' }} }}
        }}
      }}
    }});
  }}

  document.querySelectorAll('.gsc-metric-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      document.querySelectorAll('.gsc-metric-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      gscActiveMetric = btn.dataset.gscMetric;
      if (gscChart) {{
        gscChart.data = gscChartData(gscActiveMetric);
        gscChart.update();
      }}
    }});
  }});

  // Sortable GSC tables
  document.querySelectorAll('.gsc-table').forEach(function(table) {{
    var rawRows = JSON.parse(table.dataset.rows || '[]');
    var tbody = table.querySelector('tbody');
    var sortCol = null;
    var sortDir = -1;

    table.querySelectorAll('th.sortable').forEach(function(th) {{
      th.addEventListener('click', function() {{
        var col = th.dataset.col;
        if (sortCol === col) {{ sortDir *= -1; }} else {{ sortCol = col; sortDir = -1; }}
        table.querySelectorAll('th.sortable').forEach(function(h) {{
          h.classList.remove('sort-active');
          h.removeAttribute('data-sort-dir');
        }});
        th.classList.add('sort-active');
        th.setAttribute('data-sort-dir', sortDir === -1 ? 'desc' : 'asc');
        var sorted = rawRows.slice().sort(function(a, b) {{
          var av = a[col], bv = b[col];
          if (av == null) av = typeof bv === 'string' ? '' : 0;
          if (bv == null) bv = typeof av === 'string' ? '' : 0;
          if (av < bv) return sortDir;
          if (av > bv) return -sortDir;
          return 0;
        }});
        tbody.innerHTML = sorted.map(function(row) {{
          return '<tr>' + Object.keys(rawRows[0] || {{}}).map(function(k) {{
            var v = row[k];
            var isNum = typeof v === 'number';
            var display = v == null ? '' :
              k === 'ctr' ? (v).toFixed(2) + '%' :
              k === 'avg_position' ? (v > 0 ? v.toFixed(1) : '—') :
              isNum ? Math.round(v).toLocaleString() : String(v);
            return '<td class="' + (isNum ? 'num' : 'name gsc-name') + '">' + display + '</td>';
          }}).join('') + '</tr>';
        }}).join('');
      }});
    }});
  }});
}})();
</script>"""


GSC_CSS = """
    .gsc-panel {
      border-top: 3px solid #16a34a;
      margin-top: 24px;
    }
    .gsc-errors {
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 14px;
      font-size: 0.84rem;
    }
    .gsc-errors ul { margin: 6px 0 0; padding-left: 18px; }
    .gsc-kpis {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 20px;
    }
    @media (max-width: 800px) { .gsc-kpis { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 480px) { .gsc-kpis { grid-template-columns: 1fr 1fr; } }
    .gsc-kpi {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
    }
    .gsc-kpi-label {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
      margin-bottom: 4px;
    }
    .gsc-kpi-value {
      font-size: 1.55rem;
      font-weight: 800;
      color: var(--navy);
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
    }
    .gsc-chart-section {
      margin-bottom: 20px;
    }
    .gsc-chart-controls {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    .gsc-metric-btn {
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--muted);
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 500;
      cursor: pointer;
      transition: border-color .12s, color .12s, background .12s;
    }
    .gsc-metric-btn.active {
      border-color: #16a34a;
      background: #f0fdf4;
      color: #16a34a;
      font-weight: 700;
    }
    .gsc-detail {
      margin-bottom: 16px;
    }
    .gsc-detail-summary {
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
    .gsc-detail-summary:hover { color: var(--accent); }
    .gsc-name {
      max-width: 380px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 0.82rem;
      font-weight: 400;
    }
    .gsc-empty {
      color: var(--muted);
      font-size: 0.84rem;
      font-style: italic;
      padding: 12px 0;
    }
"""
