"""HTML renderer for the Lead Tracking page (HubSpot reports)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import dashboard_theme
from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc
from dashboard.utils.formatting import json_for_html_attr as _json_attr
from hubspot_reports_service import LeadTrackingReport

# Categorical palette for source breakdowns, drawn from the client theme so the
# page inherits any brand overrides. Ordered for good adjacent contrast.
_SOURCE_PALETTE_KEYS = ("sidebar_from", "google", "meta", "organic", "linkedin", "business_line")

_EXTRA_CSS = """
/* Match Overview: a centered content column instead of hugging the sidebar. */
.lt-wrap { max-width:1320px; margin:0 auto; }
.lt-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:4px; }
.lt-title { font-size:1.6rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }
.lt-sub { font-size:.9rem; color:var(--muted); margin:6px 0 0; }

/* KPI tiles */
.lt-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:16px; margin:22px 0 24px; }
.lt-tile { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:18px 20px; box-shadow:var(--shadow-sm); position:relative; overflow:hidden; }
.lt-tile::before { content:""; position:absolute; inset:0 auto 0 0; width:4px; background:var(--accent); opacity:.85; }
.lt-tile.t-mql::before { background:var(--accent); }
.lt-tile.t-contacts::before { background:var(--navy); }
.lt-tile.t-deals::before { background:var(--organic); }
.lt-tile.t-pipeline::before { background:var(--business-line); }
.lt-tile.t-won::before { background:var(--ok); }
.lt-tile-label { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); font-weight:700; margin-bottom:8px; }
.lt-tile-value { font-size:1.9rem; font-weight:750; color:var(--navy); line-height:1.05; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
.lt-tile-sub { font-size:.78rem; color:var(--muted); margin-top:6px; }
.lt-tile-sub strong { color:var(--text); font-weight:700; }

/* Cards */
.lt-card { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow-sm); }
.lt-card-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:0 0 16px; flex-wrap:wrap; }
.lt-card h2 { font-size:1rem; font-weight:700; color:var(--navy); margin:0; }
.lt-card-note { font-size:.78rem; color:var(--muted); }
.lt-cols { display:grid; grid-template-columns:1.15fr 1fr; gap:20px; align-items:start; }
.lt-cols .lt-card { margin-bottom:0; height:100%; }

/* Delta badge */
.lt-delta { display:inline-flex; align-items:center; gap:4px; padding:4px 10px; border-radius:999px; font-size:.76rem; font-weight:700; font-variant-numeric:tabular-nums; }
.lt-delta.up { background:var(--ok-bg); color:var(--ok); }
.lt-delta.down { background:var(--err-bg); color:var(--err); }
.lt-delta.flat { background:var(--surface); color:var(--muted); }
.lt-delta svg { width:13px; height:13px; }

/* Time-series chart — Chart.js canvas, sized like Overview's trend charts. */
.lt-chart-host { position:relative; width:100%; height:260px; }

/* Funnel */
.lt-funnel { display:flex; flex-direction:column; gap:2px; }
.lt-funnel-stage { padding:2px 0; }
.lt-funnel-top { display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin-bottom:5px; }
.lt-funnel-name { font-size:.82rem; font-weight:650; color:var(--text); }
.lt-funnel-val { font-size:.92rem; font-weight:750; color:var(--navy); font-variant-numeric:tabular-nums; }
.lt-funnel-bar { height:26px; border-radius:8px; min-width:8px; transition:width .4s ease; }
.lt-funnel-conv { display:flex; align-items:center; gap:7px; padding:6px 0 6px 4px; color:var(--muted); font-size:.75rem; }
.lt-funnel-conv svg { width:14px; height:14px; opacity:.7; }
.lt-funnel-conv strong { color:var(--text); font-weight:700; }

/* Ranked source bars */
.lt-bars { display:flex; flex-direction:column; gap:12px; }
.lt-bar-row { display:grid; grid-template-columns:1fr auto; gap:4px 12px; align-items:center; font-size:.82rem; }
.lt-bar-head { display:flex; align-items:center; gap:8px; min-width:0; }
.lt-bar-swatch { width:9px; height:9px; border-radius:3px; flex-shrink:0; }
.lt-bar-label { color:var(--text); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.lt-bar-val { text-align:right; color:var(--navy); font-variant-numeric:tabular-nums; font-weight:700; }
.lt-bar-val span { color:var(--muted); font-weight:600; font-size:.74rem; margin-left:5px; }
.lt-bar-track { grid-column:1 / -1; background:var(--surface); border-radius:6px; height:9px; overflow:hidden; }
.lt-bar-fill { height:100%; border-radius:6px; min-width:3px; transition:width .4s ease; }

/* Tables */
.lt-table-wrap { overflow-x:auto; }
.lt-table { width:100%; border-collapse:collapse; font-size:.85rem; }
.lt-table th { text-align:left; color:var(--muted); font-weight:700; font-size:.68rem; text-transform:uppercase; letter-spacing:.05em; padding:9px 12px; border-bottom:1px solid var(--border); white-space:nowrap; }
.lt-table th.num, .lt-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
.lt-table td { padding:11px 12px; border-bottom:1px solid #f1f4f8; color:var(--text); }
.lt-table tbody tr:last-child td { border-bottom:0; }
.lt-table tbody tr:hover td { background:var(--surface); }
.lt-src-cell { display:flex; align-items:center; gap:9px; }
.lt-conv-pill { display:inline-flex; align-items:center; padding:2px 9px; border-radius:999px; background:#eef4ff; color:var(--accent); font-size:.74rem; font-weight:700; }
.lt-email { font-weight:600; color:var(--text); }
.lt-muted { color:var(--muted); }

.lt-empty { color:var(--muted); font-size:.85rem; padding:20px 4px; text-align:center; }
.lt-note { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:12px; padding:13px 16px; font-size:.85rem; margin-bottom:20px; }

@media (max-width:820px){ .lt-cols{ grid-template-columns:1fr; } .lt-cols .lt-card{ height:auto; } }
"""

_MONTHS_ABBR = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_money(n: Any) -> str:
    try:
        return f"${float(n):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def _fmt_money_compact(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "$0"
    a = abs(v)
    if a >= 1_000_000:
        return f"${v / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 10_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"


def _pct_str(num: float, den: float) -> str:
    if not den:
        return "—"
    return f"{100.0 * num / den:.0f}%"


def _fmt_dt(v: Any) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%b %d, %Y")
    return _esc(str(v)) if v else "—"


def _source_label(src: str) -> str:
    return (src or "Unknown").replace("_", " ").title()


def _fill_months(rows: list[dict[str, Any]]) -> list[tuple[int, int, int]]:
    """Turn sparse [{month:'YYYY-MM', contacts:N}] into a continuous monthly
    series (missing months filled with 0), capped to the most recent 24."""
    parsed: list[tuple[int, int, int]] = []
    for r in rows:
        m = str(r.get("month") or "")
        try:
            y_s, mo_s = m.split("-")
            parsed.append((int(y_s), int(mo_s), int(r.get("contacts") or 0)))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return []
    parsed.sort()
    valmap = {(y, mo): v for y, mo, v in parsed}
    (y0, m0, _), (y1, m1, _) = parsed[0], parsed[-1]
    out: list[tuple[int, int, int]] = []
    y, mo = y0, m0
    while (y, mo) <= (y1, m1):
        out.append((y, mo, valmap.get((y, mo), 0)))
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
    return out[-24:]


def _delta_badge(series: list[tuple[int, int, int]]) -> str:
    """Month-over-month change badge for the two most recent months."""
    if len(series) < 2:
        return ""
    prev, curr = series[-2][2], series[-1][2]
    if prev == 0:
        if curr == 0:
            return '<span class="lt-delta flat">No change</span>'
        return f'<span class="lt-delta up">{_up_svg()} New this month</span>'
    change = (curr - prev) / prev * 100.0
    if abs(change) < 0.5:
        return '<span class="lt-delta flat">Flat vs. last month</span>'
    if change > 0:
        return f'<span class="lt-delta up">{_up_svg()} {change:.0f}% vs. last month</span>'
    return f'<span class="lt-delta down">{_down_svg()} {abs(change):.0f}% vs. last month</span>'


def _up_svg() -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M4 17l6-6 4 4 6-7"/>'
            '<path d="M20 8h-4V4" transform="translate(0 0)"/><path d="M16 8h4v4"/></svg>')


def _down_svg() -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M4 7l6 6 4-4 6 7"/>'
            '<path d="M16 16h4v-4"/></svg>')


def _month_label(y: int, mo: int, *, with_year: bool) -> str:
    base = _MONTHS_ABBR[mo] if 1 <= mo <= 12 else str(mo)
    return f"{base} '{y % 100:02d}" if with_year else base


def _area_chart(series: list[tuple[int, int, int]], accent: str,
                noun: str = "MQL", noun_plural: str = "MQLs") -> str:
    """Hero trend chart, rendered with Chart.js to match the Overview dashboard.

    Emits a sized ``<canvas>`` carrying its labels/values (and styling hints) as
    data attributes; ``_CHART_JS`` reads them and builds the chart. Overview uses
    the same vendored Chart.js, so the two trends now read as one family.
    """
    del noun  # aria label reads the plural noun below
    if len(series) < 2:
        return '<div class="lt-empty">Not enough monthly history yet to plot a trend.</div>'

    labels = [
        _month_label(y, mo, with_year=(mo == 1 or i == 0))
        for i, (y, mo, _v) in enumerate(series)
    ]
    values = [v for _, _, v in series]

    return (
        '<div class="lt-chart-host">'
        f'<canvas id="ltTrend" role="img" aria-label="Monthly {_esc(noun_plural)} trend" '
        f'data-labels="{_json_attr(labels)}" data-values="{_json_attr(values)}" '
        f'data-accent="{_esc(accent)}" data-noun="{_esc(noun_plural)}"></canvas>'
        '</div>'
    )


# Chart.js init for the hero trend. Plain (non-f) string, so literal braces need
# no doubling. Mirrors the Overview dashboard's line-chart styling (area fill,
# tension, tick limits, dark tooltip) so both trends look identical.
_CHART_JS = """
<script>
(function () {
  if (!window.Chart) return;
  var canvas = document.getElementById('ltTrend');
  if (!canvas) return;
  var labels, values;
  try {
    labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
    values = JSON.parse(canvas.getAttribute('data-values') || '[]');
  } catch (e) { return; }
  if (!values.length) return;
  var accent = canvas.getAttribute('data-accent') || '#0b5cab';
  var noun   = canvas.getAttribute('data-noun') || 'MQLs';

  Chart.defaults.font.family = 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.color = '#6b7a90';
  Chart.defaults.maintainAspectRatio = false;

  function areaFill(ctx) {
    var c = ctx.chart.ctx, a = ctx.chart.chartArea;
    if (!a) return accent + '00';
    var g = c.createLinearGradient(0, a.top, 0, a.bottom);
    g.addColorStop(0, accent + '33'); g.addColorStop(1, accent + '00');
    return g;
  }
  function fmtInt(v) {
    try { return Number(v).toLocaleString('en-US'); } catch (e) { return v; }
  }

  new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: noun, data: values, borderColor: accent,
        backgroundColor: areaFill, fill: true, borderWidth: 2.25, tension: 0.35,
        pointRadius: 0, pointHoverRadius: 4, pointBackgroundColor: accent,
      }],
    },
    options: {
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { display: false }, border: { display: false },
             ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        y: { beginAtZero: true, grid: { color: '#f1f4f9' }, border: { display: false },
             ticks: { maxTicksLimit: 4, callback: function (v) { return fmtInt(v); } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0b1020', titleColor: '#e8eefc', bodyColor: '#e8eefc',
          padding: 9, cornerRadius: 8, boxPadding: 4, usePointStyle: true,
          callbacks: { label: function (c) { return fmtInt(c.parsed.y) + ' ' + noun; } },
        },
      },
    },
  });
})();
</script>
"""


def _tile(kind: str, label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="lt-tile-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="lt-tile t-{kind}">'
        f'<div class="lt-tile-label">{_esc(label)}</div>'
        f'<div class="lt-tile-value">{value}</div>'
        f'{sub_html}</div>'
    )


def _funnel(stages: list[tuple[str, int, str]]) -> str:
    """Vertical, tapering funnel. stages = [(name, count, color)]."""
    counts = [c for _, c, _ in stages]
    peak = max(counts, default=0) or 1
    parts = ['<div class="lt-funnel">']
    for i, (name, count, color) in enumerate(stages):
        width = max(8.0, count / peak * 100.0)
        parts.append(
            f'<div class="lt-funnel-stage">'
            f'<div class="lt-funnel-top"><span class="lt-funnel-name">{_esc(name)}</span>'
            f'<span class="lt-funnel-val">{_fmt_int(count)}</span></div>'
            f'<div class="lt-funnel-bar" style="width:{width:.1f}%;background:{color}"></div>'
            f'</div>'
        )
        if i < len(stages) - 1:
            nxt = stages[i + 1][1]
            parts.append(
                f'<div class="lt-funnel-conv">{_down_arrow()}'
                f'<span><strong>{_pct_str(nxt, count)}</strong> '
                f'{_esc(name.split()[0].lower())} → {_esc(stages[i + 1][0].split()[0].lower())}</span></div>'
            )
    parts.append("</div>")
    return "".join(parts)


def _down_arrow() -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/>'
            '<path d="M6 13l6 6 6-6"/></svg>')


def _source_bars(rows: list[dict[str, Any]], colors: list[str]) -> str:
    if not rows:
        return '<div class="lt-empty">No lead sources yet.</div>'
    top = rows[:8]
    total = sum(int(r.get("contacts") or 0) for r in rows) or 1
    peak = max((int(r.get("contacts") or 0) for r in top), default=0) or 1
    out = ['<div class="lt-bars">']
    for i, r in enumerate(top):
        c = int(r.get("contacts") or 0)
        color = colors[i % len(colors)]
        width = max(3.0, c / peak * 100.0)
        share = c / total * 100.0
        out.append(
            f'<div class="lt-bar-row">'
            f'<span class="lt-bar-head"><span class="lt-bar-swatch" style="background:{color}"></span>'
            f'<span class="lt-bar-label">{_esc(_source_label(r.get("source")))}</span></span>'
            f'<span class="lt-bar-val">{_fmt_int(c)}<span>{share:.0f}%</span></span>'
            f'<span class="lt-bar-track"><span class="lt-bar-fill" '
            f'style="width:{width:.1f}%;background:{color}"></span></span>'
            f'</div>'
        )
    out.append("</div>")
    return "".join(out)


def _source_performance(
    leads: list[dict[str, Any]], deals: list[dict[str, Any]],
    noun: str = "MQL", noun_plural: str = "MQLs",
) -> str:
    """Unified per-source table joining lead-stage volume with deal/revenue
    outcomes."""
    merged: dict[str, dict[str, Any]] = {}
    for r in leads:
        key = str(r.get("source") or "Unknown")
        merged.setdefault(key, {"mqls": 0, "deals": 0, "amount": 0.0, "won": 0.0})
        merged[key]["mqls"] = int(r.get("contacts") or 0)
    for r in deals:
        key = str(r.get("source") or "Unknown")
        m = merged.setdefault(key, {"mqls": 0, "deals": 0, "amount": 0.0, "won": 0.0})
        m["deals"] = int(r.get("deals") or 0)
        m["amount"] = float(r.get("amount") or 0.0)
        m["won"] = float(r.get("won_amount") or 0.0)

    if not merged:
        return '<div class="lt-empty">No source data synced yet.</div>'

    rows = sorted(
        merged.items(), key=lambda kv: (kv[1]["mqls"], kv[1]["deals"]), reverse=True
    )[:12]

    body = []
    for key, m in rows:
        conv = (
            f'<span class="lt-conv-pill">{_pct_str(m["deals"], m["mqls"])}</span>'
            if m["mqls"] else '<span class="lt-muted">—</span>'
        )
        body.append(
            f'<tr><td>{_esc(_source_label(key))}</td>'
            f'<td class="num">{_fmt_int(m["mqls"])}</td>'
            f'<td class="num">{_fmt_int(m["deals"])}</td>'
            f'<td class="num">{_fmt_money(m["amount"])}</td>'
            f'<td class="num">{_fmt_money(m["won"])}</td>'
            f'<td class="num">{conv}</td></tr>'
        )
    return (
        '<div class="lt-table-wrap"><table class="lt-table"><thead><tr>'
        f'<th>Source</th><th class="num">{_esc(noun_plural)}</th><th class="num">Deals</th>'
        '<th class="num">Pipeline</th><th class="num">Won</th>'
        f'<th class="num">{_esc(noun)}→Deal</th></tr></thead><tbody>'
        + "".join(body) + '</tbody></table></div>'
    )


def render_lead_tracking(
    *,
    client_slug: str,
    label: str,
    report: LeadTrackingReport,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    if not report.configured:
        content = (
            '<div class="lt-wrap">'
            '<div class="lt-head"><div><h1 class="lt-title">Lead Tracking</h1></div></div>'
            f'<div class="lt-note">{_esc(report.error or "HubSpot is not configured for this client.")} '
            'Connect HubSpot from the Connectors page to enable these reports.</div>'
            '</div>'
        )
        return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)

    theme = dashboard_theme.load_client_theme(client_slug)
    accent = "#0b5cab"
    source_colors = [theme.get(k, "#0b5cab") for k in _SOURCE_PALETTE_KEYS]

    # The page reports on whichever lifecycle stage the connector is configured
    # to import (MQL by default, but "Lead", "SQL", etc. are all valid), so the
    # labels follow that stage rather than being hard-wired to "MQL".
    noun = getattr(report, "stage_noun", "MQL") or "MQL"
    noun_plural = getattr(report, "stage_noun_plural", "MQLs") or "MQLs"

    note = ""
    if not report.contacts_available and not report.deals_available:
        note = ('<div class="lt-note">No HubSpot data has synced yet. Run a sync from the '
                'HubSpot connector, then refresh this page.</div>')

    # KPI tiles with derived context.
    tiles = (
        '<div class="lt-tiles">'
        + _tile("mql", noun_plural, _fmt_int(report.mql_count),
                f'<strong>{_pct_str(report.mql_count, report.contact_count)}</strong> of contacts')
        + _tile("contacts", "Contacts tracked", _fmt_int(report.contact_count))
        + _tile("deals", "Deals", _fmt_int(report.deal_count),
                f'<strong>{_pct_str(report.deal_count, report.mql_count)}</strong> of {noun_plural}')
        + _tile("pipeline", "Pipeline", _fmt_money_compact(report.pipeline_amount),
                (f'<strong>{_fmt_money_compact(report.pipeline_amount / report.deal_count)}</strong> avg deal'
                 if report.deal_count else ""))
        + _tile("won", "Won revenue", _fmt_money_compact(report.won_amount),
                f'<strong>{_pct_str(report.won_amount, report.pipeline_amount)}</strong> win rate')
        + '</div>'
    )

    # Hero: primary-stage trend over time.
    series = _fill_months(report.mqls_by_month)
    chart_html = _area_chart(series, accent, noun, noun_plural)
    delta = _delta_badge(series)
    chart_card = (
        '<div class="lt-card">'
        f'<div class="lt-card-head"><h2>{_esc(noun_plural)} over time</h2>'
        f'{delta}</div>{chart_html}</div>'
    )

    # Funnel + top sources.
    funnel_html = _funnel([
        ("Contacts tracked", report.contact_count, theme.get("sidebar_from", "#0a2540")),
        (noun_plural, report.mql_count, accent),
        ("Deals", report.deal_count, theme.get("organic", "#16a34a")),
    ])
    sources_html = _source_bars(report.leads_by_source, source_colors)
    cols = (
        '<div class="lt-cols">'
        f'<div class="lt-card"><div class="lt-card-head"><h2>Lead-to-deal funnel</h2></div>{funnel_html}</div>'
        f'<div class="lt-card"><div class="lt-card-head"><h2>Top lead sources</h2>'
        f'<span class="lt-card-note">by {_esc(noun)} volume</span></div>'
        f'{sources_html}</div>'
        '</div>'
    )

    # Unified source performance.
    perf_html = _source_performance(report.leads_by_source, report.deals_by_source, noun, noun_plural)
    perf_card = (
        '<div class="lt-card"><div class="lt-card-head"><h2>Source performance</h2>'
        f'<span class="lt-card-note">{_esc(noun_plural)}, pipeline &amp; conversion by channel</span></div>'
        f'{perf_html}</div>'
    )


    # Recent MQLs.
    if report.recent_mqls:
        recent_rows = "".join(
            f'<tr><td class="lt-email">{_esc(r.get("email") or "—")}</td>'
            f'<td>{_esc(r.get("company") or "—")}</td>'
            f'<td>{_esc(_source_label(r.get("source")))}</td>'
            f'<td class="lt-muted">{_fmt_dt(r.get("became_stage_date"))}</td></tr>'
            for r in report.recent_mqls
        )
        recent_html = (
            '<div class="lt-table-wrap"><table class="lt-table"><thead><tr><th>Email</th><th>Company</th>'
            f'<th>Original source</th><th>Became {_esc(noun)}</th></tr></thead><tbody>'
            + recent_rows + '</tbody></table></div>'
        )
    else:
        recent_html = f'<div class="lt-empty">No {_esc(noun_plural)} synced yet.</div>'
    recent_card = (
        f'<div class="lt-card"><div class="lt-card-head"><h2>Recent {_esc(noun_plural)}</h2>'
        '<span class="lt-card-note">latest 20</span></div>'
        f'{recent_html}</div>'
    )

    content = f"""
      <div class="lt-wrap">
        <div class="lt-head">
          <div>
            <h1 class="lt-title">Lead Tracking</h1>
            <p class="lt-sub">HubSpot lead &amp; pipeline reporting for {_esc(label)}.</p>
          </div>
        </div>
        {note}
        {tiles}
        {chart_card}
        {cols}
        {perf_card}
        {recent_card}
      </div>
      {_CHART_JS}
    """
    return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)


def _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin) -> str:
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="lead-tracking",
        page_title="Lead Tracking",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
        include_chartjs=True,
    )
