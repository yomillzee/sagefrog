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


def _fmt_compact(v: int | float) -> str:
    """Short number: 5,090,000 → 5.09M, 57,400 → 57.4K."""
    v = float(v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(int(v))


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


def _fmt_pos(v: float) -> str:
    return f"{v:.1f}" if v else "—"


def _delta_badge(current: float, prior: float, *, lower_is_better: bool = False) -> str:
    """Render a ▲/▼ pct badge. Returns '' if no prior data."""
    if not prior or not current:
        return ""
    pct = (current - prior) / prior * 100
    is_up = pct >= 0
    good = (is_up and not lower_is_better) or (not is_up and lower_is_better)
    cls = "gsc-delta-good" if good else "gsc-delta-bad"
    arrow = "▲" if is_up else "▼"
    return f'<span class="gsc-delta {cls}">{arrow} {abs(pct):.1f}%</span>'


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
    errors = gsc.get("errors") or {}

    clicks = int(kpis.get("clicks") or 0)
    impressions = int(kpis.get("impressions") or 0)
    ctr = float(kpis.get("ctr") or 0)
    avg_position = float(kpis.get("avg_position") or 0)

    prior_clicks       = int(kpis.get("prior_clicks") or 0)
    prior_impressions  = int(kpis.get("prior_impressions") or 0)
    prior_ctr          = float(kpis.get("prior_ctr") or 0)
    prior_avg_position = float(kpis.get("prior_avg_position") or 0)
    prior_start        = kpis.get("prior_start") or ""
    prior_end          = kpis.get("prior_end") or ""

    has_prior = prior_impressions > 0

    badge_clicks      = _delta_badge(clicks, prior_clicks)
    badge_impressions = _delta_badge(impressions, prior_impressions)
    badge_ctr         = _delta_badge(ctr, prior_ctr)
    badge_position    = _delta_badge(avg_position, prior_avg_position, lower_is_better=True)

    prev_clicks_html = (
        f'<div class="gsc-kpi-prev">vs previous period ({_fmt_compact(prior_clicks)})</div>'
        if has_prior else ""
    )
    prev_impressions_html = (
        f'<div class="gsc-kpi-prev">vs previous period ({_fmt_compact(prior_impressions)})</div>'
        if has_prior else ""
    )
    prev_ctr_html = (
        f'<div class="gsc-kpi-prev">vs previous period ({_fmt_pct(prior_ctr)})</div>'
        if has_prior else ""
    )
    prev_position_html = (
        f'<div class="gsc-kpi-prev">vs previous period ({_fmt_pos(prior_avg_position)})</div>'
        if has_prior else ""
    )

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


    daily_json = _safe_json(daily)

    prior_ctr_rounded = round(prior_ctr, 2)
    prior_avg_position_rounded = round(prior_avg_position, 1)

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
      <div class="gsc-kpi-value">{_esc(_fmt_compact(clicks))}{badge_clicks}</div>
      {prev_clicks_html}
      <div class="gsc-sparkline-wrap"><canvas class="gsc-sparkline" data-metric="clicks"></canvas></div>
    </div>
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Impressions</div>
      <div class="gsc-kpi-value">{_esc(_fmt_compact(impressions))}{badge_impressions}</div>
      {prev_impressions_html}
      <div class="gsc-sparkline-wrap"><canvas class="gsc-sparkline" data-metric="impressions"></canvas></div>
    </div>
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Avg. CTR</div>
      <div class="gsc-kpi-value">{_esc(_fmt_pct(ctr))}{badge_ctr}</div>
      {prev_ctr_html}
      <div class="gsc-sparkline-wrap"><canvas class="gsc-sparkline" data-metric="ctr"></canvas></div>
    </div>
    <div class="gsc-kpi">
      <div class="gsc-kpi-label">Avg. Position</div>
      <div class="gsc-kpi-value">{_esc(_fmt_pos(avg_position))}{badge_position}</div>
      {prev_position_html}
      <div class="gsc-sparkline-wrap"><canvas class="gsc-sparkline" data-metric="avg_position"></canvas></div>
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
    <div class="gsc-chart-prior-label" id="gscPriorLabel"></div>
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

</section>

<script>
(function() {{
  var gscDaily = {daily_json};

  // ── Sparklines inside each KPI card ─────────────────────────────────
  var gscSparkColors = {{
    clicks:      '#16a34a',
    impressions: '#0a66c2',
    ctr:         '#c9a227',
    avg_position:'#7c3aed',
  }};
  if (typeof Chart !== 'undefined' && gscDaily.length > 1) {{
    document.querySelectorAll('.gsc-sparkline').forEach(function(canvas) {{
      var metric = canvas.dataset.metric;
      var color = gscSparkColors[metric] || '#0a66c2';
      var values = gscDaily.map(function(r) {{ return r[metric] || 0; }});
      new Chart(canvas.getContext('2d'), {{
        type: 'line',
        data: {{
          labels: gscDaily.map(function(r) {{ return r.date; }}),
          datasets: [{{
            data: values,
            borderColor: color,
            backgroundColor: color + '22',
            fill: true,
            tension: 0.35,
            pointRadius: 0,
            borderWidth: 1.5,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }},
          scales: {{
            x: {{ display: false }},
            y: {{ display: false, beginAtZero: metric !== 'avg_position' }},
          }},
        }},
      }});
    }});
  }}

  var gscPriorValues = {{
    clicks: {prior_clicks},
    impressions: {prior_impressions},
    ctr: {prior_ctr_rounded},
    avg_position: {prior_avg_position_rounded}
  }};
  var gscPriorRange = '{_esc(prior_start)} – {_esc(prior_end)}';
  var gscPriorMetricLabels = {{
    clicks: 'clicks',
    impressions: 'impressions',
    ctr: '% CTR',
    avg_position: ' avg. position'
  }};

  function gscUpdatePriorLabel(metric) {{
    var el = document.getElementById('gscPriorLabel');
    if (!el) return;
    var val = gscPriorValues[metric];
    if (!val && val !== 0) {{ el.textContent = ''; return; }}
    var formatted = metric === 'ctr' ? val.toFixed(2) + '%' :
                    metric === 'avg_position' ? val.toFixed(1) :
                    Math.round(val).toLocaleString();
    var label = gscPriorMetricLabels[metric] || metric;
    el.textContent = 'Previous period (' + gscPriorRange + '): ' + formatted + ' ' + label;
  }}

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
  gscUpdatePriorLabel(gscActiveMetric);

  document.querySelectorAll('.gsc-metric-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      document.querySelectorAll('.gsc-metric-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      gscActiveMetric = btn.dataset.gscMetric;
      if (gscChart) {{
        gscChart.data = gscChartData(gscActiveMetric);
        gscChart.update();
      }}
      gscUpdatePriorLabel(gscActiveMetric);
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
      padding: 14px 16px 0;
      display: flex;
      flex-direction: column;
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
      font-size: 1.65rem;
      font-weight: 900;
      color: var(--navy);
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
      display: flex;
      align-items: baseline;
      gap: 8px;
      flex-wrap: wrap;
      line-height: 1.1;
    }
    .gsc-delta { font-size: 0.72rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; }
    .gsc-delta-good { color: #16a34a; background: #f0fdf4; }
    .gsc-delta-bad  { color: #dc2626; background: #fef2f2; }
    .gsc-kpi-prev   { font-size: 0.68rem; color: var(--muted); margin-top: 4px; }
    .gsc-sparkline-wrap {
      margin-top: 10px;
      height: 52px;
      flex-shrink: 0;
    }
    .gsc-sparkline { display: block; width: 100%; height: 100%; }
    .gsc-chart-prior-label { font-size: 0.78rem; color: var(--muted); margin-top: 6px; text-align: right; }
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
