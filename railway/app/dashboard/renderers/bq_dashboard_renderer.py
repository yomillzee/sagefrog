"""Renderer for the Penn BQ Test BigQuery-backed dashboard."""

from __future__ import annotations

import json
from typing import Any

from dashboard.renderers.base_layout import favicon_head_html as _favicon_head_html
from dashboard.utils.formatting import esc as _esc


def _fmt_money(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:,.2f}"


def _fmt_int(val: int | float) -> str:
    return f"{int(val):,}"


def _fmt_pct(val: float) -> str:
    return f"{val:.2f}%"


def _safe_json(obj: Any) -> str:
    return (
        json.dumps(obj, default=str, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_bq_dashboard_html(payload: dict[str, Any]) -> str:
    totals = payload.get("totals") or {}
    by_date = payload.get("by_date") or {}
    campaigns = payload.get("by_campaign") or []
    dr = payload.get("date_range") or {}
    error = payload.get("error")
    row_count = payload.get("row_count") or 0

    spend = float(totals.get("spend") or 0)
    impressions = int(totals.get("impressions") or 0)
    clicks = int(totals.get("clicks") or 0)
    conversions = float(totals.get("conversions") or 0)
    ctr = (clicks / impressions * 100) if impressions else 0.0
    cpc = (spend / clicks) if clicks else 0.0

    date_range_label = f"{dr.get('start', '')} → {dr.get('end', '')}"

    # Chart data — sorted dates
    sorted_dates = sorted(by_date.keys())
    chart_labels = sorted_dates
    chart_spend = [round(by_date[d]["spend"], 2) for d in sorted_dates]
    chart_clicks = [int(by_date[d]["clicks"]) for d in sorted_dates]
    chart_impressions = [int(by_date[d]["impressions"]) for d in sorted_dates]
    chart_conversions = [round(float(by_date[d]["conversions"]), 2) for d in sorted_dates]

    # Campaign table rows HTML
    campaign_rows_html = ""
    for c in campaigns:
        c_spend = float(c.get("spend") or 0)
        c_imp = int(c.get("impressions") or 0)
        c_clicks = int(c.get("clicks") or 0)
        c_conv = float(c.get("conversions") or 0)
        c_ctr = (c_clicks / c_imp * 100) if c_imp else 0.0
        c_cpc = (c_spend / c_clicks) if c_clicks else 0.0
        name = _esc(str(c.get("campaign_name") or c.get("campaign_id") or "—"))
        campaign_rows_html += f"""
        <tr>
          <td class="name">{name}</td>
          <td class="num">{_fmt_money(c_spend)}</td>
          <td class="num">{_fmt_int(c_imp)}</td>
          <td class="num">{_fmt_int(c_clicks)}</td>
          <td class="num">{_fmt_pct(c_ctr)}</td>
          <td class="num">{_fmt_money(c_cpc)}</td>
          <td class="num">{_fmt_int(c_conv)}</td>
        </tr>"""

    if not campaign_rows_html:
        campaign_rows_html = '<tr><td colspan="7" style="text-align:center;color:#94a3b8;padding:24px;">No campaign data found.</td></tr>'

    error_html = ""
    if error:
        error_html = f'<div class="bq-error"><strong>BigQuery error:</strong> {_esc(error)}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Penn BQ Test — Ads Dashboard</title>{_favicon_head_html()}
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #f0f4f8;
      --panel: #ffffff;
      --border: #dde3ed;
      --text: #1e293b;
      --muted: #64748b;
      --navy: #0a2540;
      --accent: #0b5cab;
      --gold: #c9a227;
      --radius: 14px;
      --shadow-sm: 0 1px 3px rgba(10,37,64,.06);
      --shadow: 0 4px 16px rgba(10,37,64,.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    .page-header {{
      background: var(--navy);
      color: #fff;
      padding: 18px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .page-header h1 {{
      margin: 0;
      font-size: 1.1rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }}
    .page-header .header-meta {{
      font-size: 0.8rem;
      opacity: 0.7;
    }}
    .page-content {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 28px 32px 64px;
    }}
    .bq-error {{
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: var(--radius);
      padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 0.88rem;
      word-break: break-word;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }}
    @media (max-width: 1100px) {{ .kpi-grid {{ grid-template-columns: repeat(3, 1fr); }} }}
    @media (max-width: 700px) {{ .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
    .kpi-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px;
      box-shadow: var(--shadow-sm);
    }}
    .kpi-label {{
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .kpi-value {{
      font-size: 1.65rem;
      font-weight: 800;
      color: var(--navy);
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 22px 24px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-sm);
    }}
    .panel-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 16px;
    }}
    .panel-head h2 {{
      margin: 0;
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .badge {{
      font-size: 0.72rem;
      font-weight: 600;
      background: #eef3f9;
      color: var(--accent);
      padding: 3px 10px;
      border-radius: 999px;
      white-space: nowrap;
    }}
    .chart-controls {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }}
    .metric-btn {{
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--muted);
      padding: 6px 14px;
      border-radius: 999px;
      font-size: 0.83rem;
      font-weight: 500;
      cursor: pointer;
      transition: border-color .12s, color .12s, background .12s;
    }}
    .metric-btn.active {{
      border-color: var(--accent);
      background: #eef3f9;
      color: var(--accent);
      font-weight: 700;
    }}
    canvas {{ max-height: 280px; }}
    .table-wrap {{ overflow-x: auto; }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }}
    .data-table th, .data-table td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }}
    .data-table th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      background: #f8fafc;
      position: sticky;
      top: 0;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    .data-table th:hover {{ color: var(--accent); }}
    .data-table tbody tr:last-child td {{ border-bottom: none; }}
    .data-table tbody tr:hover {{ background: #f5f8fc; }}
    td.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    td.name {{ max-width: 360px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .source-note {{
      font-size: 0.76rem;
      color: var(--muted);
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid var(--border);
    }}
  </style>
</head>
<body>
  <header class="page-header">
    <h1>Penn BQ Test · Google Ads (BigQuery mart)</h1>
    <span class="header-meta">{_esc(date_range_label)} · {_fmt_int(row_count)} rows</span>
  </header>
  <main class="page-content">
    {error_html}

    <!-- KPI Cards -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Spend</div>
        <div class="kpi-value">{_fmt_money(spend)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Impressions</div>
        <div class="kpi-value">{_fmt_int(impressions)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Clicks</div>
        <div class="kpi-value">{_fmt_int(clicks)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">CTR</div>
        <div class="kpi-value">{_fmt_pct(ctr)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">CPC</div>
        <div class="kpi-value">{_fmt_money(cpc)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Conversions</div>
        <div class="kpi-value">{_fmt_int(conversions)}</div>
      </div>
    </div>

    <!-- Daily Trend Chart -->
    <div class="panel">
      <div class="panel-head">
        <h2>Daily Performance Trend</h2>
      </div>
      <div class="chart-controls">
        <button class="metric-btn active" data-metric="spend">Spend</button>
        <button class="metric-btn" data-metric="clicks">Clicks</button>
        <button class="metric-btn" data-metric="impressions">Impressions</button>
        <button class="metric-btn" data-metric="conversions">Conversions</button>
      </div>
      <canvas id="trendChart"></canvas>
    </div>

    <!-- Campaign Table -->
    <div class="panel">
      <div class="panel-head">
        <h2>Campaign Performance</h2>
        <span class="badge">{len(campaigns)} campaigns</span>
      </div>
      <div class="table-wrap">
        <table class="data-table" id="campaignTable">
          <thead>
            <tr>
              <th data-col="campaign_name">Campaign</th>
              <th data-col="spend" class="num">Spend</th>
              <th data-col="impressions" class="num">Impressions</th>
              <th data-col="clicks" class="num">Clicks</th>
              <th data-col="ctr" class="num">CTR</th>
              <th data-col="cpc" class="num">CPC</th>
              <th data-col="conversions" class="num">Conv.</th>
            </tr>
          </thead>
          <tbody id="campaignTableBody">
            {campaign_rows_html}
          </tbody>
        </table>
      </div>
      <p class="source-note">
        Source: <code>{_esc(_full_table_display())}</code> · Last 30 days · refreshed on page load
      </p>
    </div>
  </main>

  <script>
    (function() {{
      var chartData = {{
        labels: {_safe_json(chart_labels)},
        spend: {_safe_json(chart_spend)},
        clicks: {_safe_json(chart_clicks)},
        impressions: {_safe_json(chart_impressions)},
        conversions: {_safe_json(chart_conversions)}
      }};

      var metricLabels = {{
        spend: 'Spend ($)',
        clicks: 'Clicks',
        impressions: 'Impressions',
        conversions: 'Conversions'
      }};
      var metricColors = {{
        spend: '#0a2540',
        clicks: '#4285f4',
        impressions: '#0a66c2',
        conversions: '#16a34a'
      }};

      var ctx = document.getElementById('trendChart').getContext('2d');
      var activeMetric = 'spend';
      var chart = new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: chartData.labels,
          datasets: [{{
            label: metricLabels[activeMetric],
            data: chartData[activeMetric],
            backgroundColor: metricColors[activeMetric] + '33',
            borderColor: metricColors[activeMetric],
            borderWidth: 1.5,
            borderRadius: 3,
          }}]
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{
            x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10 }} }},
            y: {{ beginAtZero: true, grid: {{ color: '#dde3ed66' }} }}
          }}
        }}
      }});

      document.querySelectorAll('.metric-btn').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
          document.querySelectorAll('.metric-btn').forEach(function(b) {{ b.classList.remove('active'); }});
          btn.classList.add('active');
          activeMetric = btn.dataset.metric;
          chart.data.datasets[0].data = chartData[activeMetric];
          chart.data.datasets[0].label = metricLabels[activeMetric];
          chart.data.datasets[0].backgroundColor = metricColors[activeMetric] + '33';
          chart.data.datasets[0].borderColor = metricColors[activeMetric];
          chart.update();
        }});
      }});

      // Sortable table
      var campaigns = {_safe_json(campaigns)};
      var sortCol = 'spend';
      var sortDir = -1;

      function renderCampaigns() {{
        var sorted = campaigns.slice().sort(function(a, b) {{
          var av = sortCol === 'campaign_name' ? (a.campaign_name || '') : (Number(a[sortCol]) || 0);
          var bv = sortCol === 'campaign_name' ? (b.campaign_name || '') : (Number(b[sortCol]) || 0);
          if (av < bv) return sortDir;
          if (av > bv) return -sortDir;
          return 0;
        }});
        var tbody = document.getElementById('campaignTableBody');
        tbody.innerHTML = sorted.map(function(c) {{
          var imp = c.impressions || 0;
          var clk = c.clicks || 0;
          var spd = c.spend || 0;
          var conv = c.conversions || 0;
          var ctr = imp ? (clk / imp * 100) : 0;
          var cpc = clk ? (spd / clk) : 0;
          function fmtMoney(v) {{
            if (v >= 1e6) return '$' + (v/1e6).toFixed(2) + 'M';
            if (v >= 1e3) return '$' + (v/1e3).toFixed(1) + 'K';
            return '$' + v.toFixed(2);
          }}
          function fmtInt(v) {{ return Math.round(v).toLocaleString(); }}
          return '<tr>' +
            '<td class="name">' + (c.campaign_name || c.campaign_id || '—') + '</td>' +
            '<td class="num">' + fmtMoney(spd) + '</td>' +
            '<td class="num">' + fmtInt(imp) + '</td>' +
            '<td class="num">' + fmtInt(clk) + '</td>' +
            '<td class="num">' + ctr.toFixed(2) + '%</td>' +
            '<td class="num">' + fmtMoney(cpc) + '</td>' +
            '<td class="num">' + fmtInt(conv) + '</td>' +
          '</tr>';
        }}).join('');
      }}

      document.querySelectorAll('#campaignTable thead th').forEach(function(th) {{
        th.addEventListener('click', function() {{
          var col = th.dataset.col;
          if (sortCol === col) {{ sortDir *= -1; }} else {{ sortCol = col; sortDir = -1; }}
          renderCampaigns();
        }});
      }});

      renderCampaigns();
    }})();
  </script>
</body>
</html>"""


def _full_table_display() -> str:
    import os
    project = (os.getenv("BQ_MART_PROJECT_ID") or "penn-community-b-1699391543298").strip()
    dataset = (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()
    table = (os.getenv("BQ_MART_TABLE") or "fact_google_ads_campaign_daily").strip()
    return f"{project}.{dataset}.{table}"
