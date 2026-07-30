"""HTML renderer for the LinkedIn Organic page (company-page analytics).

Uses the same visual language as the Overview / Campaign Explorer dashboards —
``section`` panels with a ``.sec-head``, compact ``.card`` KPI tiles with a
top-accent border and sparkline, and the shared modern table styling — rather
than the older bespoke tile layout. Charts stay on Chart.js and the Top posts
table stays client-sortable.
"""

from __future__ import annotations

import json
from typing import Any

from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc
from linkedin_organic_report_service import LinkedInOrganicReport

_LI_BLUE = "#0a66c2"
_PAGE_GREEN = "#16a34a"
_WINDOW_DAYS = 90

# Component styles mirror the BigQuery dashboard's card/section/table language so
# this page reads as part of the same product. A couple of design tokens the
# shell's :root doesn't define (--line-soft, --radius-sm) are inlined here.
_EXTRA_CSS = """
.lo-wrap { width:100%; }
.lo-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:18px; }
.lo-title { font-size:1.5rem; font-weight:800; color:var(--navy); margin:0; letter-spacing:-.02em; }
.lo-sub { font-size:.9rem; color:var(--muted); margin:5px 0 0; }
.lo-note { font-size:.88rem; color:var(--muted); background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin:0 0 16px; }

/* Section panels — matches the Overview/Explorer `section` chrome. */
.lo-wrap section { background:#fff; border:1px solid var(--border); border-radius:var(--radius); padding:18px 20px 20px; margin-bottom:16px; box-shadow:var(--shadow-sm); }
.lo-wrap .sec-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
.lo-wrap .sec-head h2 { margin:0; font-size:1rem; font-weight:750; color:var(--navy); display:flex; align-items:center; }
.lo-wrap .sec-head .status { margin:0; color:var(--muted); font-size:.78rem; text-align:right; flex-shrink:0; }
.lo-dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:#0a66c2; margin-right:8px; }

/* KPI cards — compact, top-accent border, sparkline + delta foot. */
.lo-wrap .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.lo-wrap .card { border:1px solid var(--line-soft,#eff3f8); border-top:3px solid var(--accent); border-radius:var(--radius-sm,9px); padding:14px 15px 15px; background:#fff; }
.lo-wrap .card-title { color:var(--muted); font-size:.66rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }
.lo-wrap .card-value { margin-top:7px; font-size:1.55rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.lo-wrap .card-foot { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:9px; min-height:20px; }
.lo-wrap .card-sub { color:var(--muted); font-size:.74rem; }
.lo-wrap .card-sub strong { color:var(--text); font-weight:700; }
.lo-spark { width:100%; height:30px; display:block; margin:8px 0 2px; }
.lo-delta { font-size:.74rem; font-weight:700; white-space:nowrap; }
.lo-delta.up { color:var(--ok); }
.lo-delta.down { color:var(--err); }
.lo-delta.flat { color:var(--muted); font-weight:600; }

/* Trend charts */
.lo-two { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.lo-two > * { min-width:0; }
.lo-chart-box { border:1px solid var(--line-soft,#eff3f8); border-radius:10px; padding:12px 14px; background:#fafcff; }
.lo-chart-lab { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-bottom:8px; }
.lo-chart-lab h3 { margin:0; font-size:.86rem; font-weight:700; color:var(--navy); }
.lo-chart-lab span { font-size:.72rem; color:var(--muted); }
.lo-chart { position:relative; height:180px; }
.lo-chart canvas { display:block; width:100% !important; }
.lo-empty { font-size:.85rem; color:var(--muted); padding:26px 0; text-align:center; }

/* Top posts table — shared modern table styling. */
.lo-table-wrap { overflow:auto; border:1px solid var(--line-soft,#eff3f8); border-radius:var(--radius-sm,9px); }
.lo-table { border-collapse:collapse; width:100%; min-width:640px; font-size:.85rem; }
.lo-table th, .lo-table td { padding:10px 13px; border-bottom:1px solid var(--line-soft,#eff3f8); text-align:right; white-space:nowrap; }
.lo-table tbody tr:last-child td { border-bottom:0; }
.lo-table tbody tr:hover td { background:#f7faff; }
.lo-table th { background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; }
.lo-table th.left, .lo-table td.left { text-align:left; }
.lo-table td.left { max-width:420px; }
.lo-table th[data-sort] { cursor:pointer; user-select:none; transition:background .12s,color .12s; }
.lo-table th[data-sort]:hover { background:#e9eef5; color:#33455e; }
.lo-table th[data-sort]::after { content:"\\2195"; opacity:.35; margin-left:5px; font-size:.85em; }
.lo-table th[data-dir="asc"] { color:var(--accent); }
.lo-table th[data-dir="asc"]::after { content:"\\2191"; opacity:.9; }
.lo-table th[data-dir="desc"] { color:var(--accent); }
.lo-table th[data-dir="desc"]::after { content:"\\2193"; opacity:.9; }
.lo-post-title { font-weight:650; color:var(--navy); display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:420px; }
.lo-post-meta { font-size:.72rem; color:var(--muted); }
.lo-chip { display:inline-block; font-size:.64rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:#0a66c2; background:#e9f1fb; border-radius:5px; padding:2px 6px; margin-left:6px; }

@media (max-width:820px){ .lo-two{ grid-template-columns:1fr; } }
"""


def _fmt_int(n: int | float) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _card(title: str, value: str, *, spark: str = "", foot: str = "") -> str:
    foot_html = f'<div class="card-foot">{foot}</div>' if foot else ""
    return (
        '<div class="card">'
        f'<div class="card-title">{_esc(title)}</div>'
        f'<div class="card-value">{_esc(value)}</div>'
        f'{spark}{foot_html}</div>'
    )


def _sub(text_html: str) -> str:
    return f'<span class="card-sub">{text_html}</span>'


def _period_delta(gain: int) -> str:
    """▲/▼ follower change over the reporting window, styled like the Overview
    card's delta badge."""
    if gain > 0:
        return f'<span class="lo-delta up">▲ {_fmt_int(gain)} in {_WINDOW_DAYS} days</span>'
    if gain < 0:
        return f'<span class="lo-delta down">▼ {_fmt_int(abs(gain))} in {_WINDOW_DAYS} days</span>'
    return f'<span class="lo-delta flat">No change in {_WINDOW_DAYS} days</span>'


def _follower_sparkline(series: list[dict[str, Any]]) -> str:
    """Tiny inline-SVG follower-growth curve, reconstructing absolute follower
    counts from the daily gains anchored to the latest lifetime total. Returns
    "" with fewer than two days to plot (nothing to draw)."""
    rows = [r for r in (series or []) if str(r.get("metric_date") or "")]
    rows.sort(key=lambda r: str(r.get("metric_date") or ""))
    if len(rows) < 2:
        return ""

    gains = [int(r.get("total_follower_gain") or 0) for r in rows]
    total = 0
    for r in reversed(rows):
        snapshot = int(r.get("total_followers") or 0)
        if snapshot:
            total = snapshot
            break

    if total:
        counts = [0] * len(rows)
        counts[-1] = total
        for i in range(len(rows) - 1, 0, -1):
            counts[i - 1] = counts[i] - gains[i]
        values = counts
    else:
        running = 0
        values = []
        for g in gains:
            running += g
            values.append(running)

    w, h, pad = 132.0, 34.0, 3.0
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)
    step = (w - 2 * pad) / (n - 1)
    pts = [
        (pad + i * step, h - pad - ((v - lo) / span) * (h - 2 * pad))
        for i, v in enumerate(values)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (
        f"M {pts[0][0]:.1f} {h - pad:.1f} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y in pts)
        + f" L {pts[-1][0]:.1f} {h - pad:.1f} Z"
    )
    lx, ly = pts[-1]
    return (
        f'<svg class="lo-spark" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
        f'role="img" aria-label="Follower growth">'
        f'<path d="{area}" fill="{_LI_BLUE}" fill-opacity="0.12"/>'
        f'<polyline points="{line}" fill="none" stroke="{_LI_BLUE}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" fill="{_LI_BLUE}"/>'
        f'</svg>'
    )


def _chart_block(canvas_id: str, series: list[dict[str, Any]]) -> str:
    """Canvas host for a Chart.js daily bar chart (empty state when no data)."""
    if not series:
        return '<div class="lo-empty">No data for this period yet.</div>'
    return f'<div class="lo-chart"><canvas id="{_esc(canvas_id)}"></canvas></div>'


def _chart_series(series: list[dict[str, Any]], value_key: str) -> dict[str, list]:
    return {
        "labels": [str(r.get("metric_date") or "") for r in series],
        "values": [float(r.get(value_key) or 0) for r in series],
    }


def _charts_script(
    follower_series: list[dict[str, Any]],
    page_series: list[dict[str, Any]],
) -> str:
    """Chart.js loader + init for the follower-gain and page-view bar charts."""
    payload = json.dumps({
        "follower": {**_chart_series(follower_series, "total_follower_gain"),
                     "color": _LI_BLUE, "canvas": "loFollowerChart"},
        "page": {**_chart_series(page_series, "page_views"),
                 "color": _PAGE_GREEN, "canvas": "loPageChart"},
    }).replace("<", "\\u003c")
    return (
        '<script src="/static/vendor/chart.umd.min.js"></script>'
        "<script>(function(){\n"
        f"  var LO_CHARTS = {payload};\n"
        "  function draw(spec){\n"
        "    var el = document.getElementById(spec.canvas);\n"
        "    if(!el || !window.Chart || !spec.labels.length) return;\n"
        "    new Chart(el, {\n"
        "      type:'bar',\n"
        "      data:{labels:spec.labels, datasets:[{data:spec.values,\n"
        "        backgroundColor:spec.color, hoverBackgroundColor:spec.color,\n"
        "        borderRadius:3, borderSkipped:false, maxBarThickness:22}]},\n"
        "      options:{responsive:true, maintainAspectRatio:false, animation:false,\n"
        "        plugins:{legend:{display:false},\n"
        "          tooltip:{displayColors:false, padding:8,\n"
        "            callbacks:{label:function(c){return c.formattedValue;}}}},\n"
        "        scales:{\n"
        "          x:{grid:{display:false}, ticks:{maxRotation:0, autoSkip:true,\n"
        "             maxTicksLimit:8, font:{size:10}, color:'#94a3b8'}},\n"
        "          y:{beginAtZero:true, grid:{color:'rgba(148,163,184,.15)'},\n"
        "             ticks:{precision:0, font:{size:10}, color:'#94a3b8', maxTicksLimit:5},\n"
        "             border:{display:false}}}}\n"
        "    });\n"
        "  }\n"
        "  function init(){ draw(LO_CHARTS.follower); draw(LO_CHARTS.page); }\n"
        "  if(document.readyState!=='loading') init();\n"
        "  else document.addEventListener('DOMContentLoaded', init);\n"
        "})();</script>"
    )


def _sort_script() -> str:
    """Click-to-sort for the Top posts table (numeric + text columns)."""
    return (
        "<script>(function(){\n"
        "  function initSort(table){\n"
        "    var ths = Array.prototype.slice.call(table.querySelectorAll('th[data-sort]'));\n"
        "    ths.forEach(function(th){\n"
        "      var idx = Array.prototype.indexOf.call(th.parentNode.children, th);\n"
        "      th.addEventListener('click', function(){\n"
        "        var numeric = th.getAttribute('data-sort')==='num';\n"
        "        var asc = th.getAttribute('data-dir')!=='asc';\n"
        "        ths.forEach(function(o){ if(o!==th) o.removeAttribute('data-dir'); });\n"
        "        th.setAttribute('data-dir', asc?'asc':'desc');\n"
        "        var tbody = table.querySelector('tbody');\n"
        "        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));\n"
        "        rows.sort(function(a,b){\n"
        "          var av=a.children[idx].getAttribute('data-val')||'';\n"
        "          var bv=b.children[idx].getAttribute('data-val')||'';\n"
        "          if(numeric){ return asc?(parseFloat(av)||0)-(parseFloat(bv)||0):(parseFloat(bv)||0)-(parseFloat(av)||0); }\n"
        "          return asc?av.toLowerCase().localeCompare(bv.toLowerCase()):bv.toLowerCase().localeCompare(av.toLowerCase());\n"
        "        });\n"
        "        rows.forEach(function(r){ tbody.appendChild(r); });\n"
        "      });\n"
        "    });\n"
        "  }\n"
        "  function init(){ var t=document.getElementById('loPostsTable'); if(t) initSort(t); }\n"
        "  if(document.readyState!=='loading') init();\n"
        "  else document.addEventListener('DOMContentLoaded', init);\n"
        "})();</script>"
    )


def render_linkedin_organic(
    *,
    client_slug: str,
    label: str,
    report: LinkedInOrganicReport,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    if not report.configured:
        content = (
            '<div class="lo-wrap">'
            '<div class="lo-head"><div><h1 class="lo-title">LinkedIn Organic</h1></div></div>'
            f'<div class="lo-note">{_esc(report.error or "LinkedIn Organic is not configured for this client.")} '
            'Connect the LinkedIn Organic connector and run a sync to enable these reports.</div>'
            '</div>'
        )
        return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)

    has_any = bool(report.top_posts or report.follower_series or report.page_series)
    note = ""
    if not has_any:
        note = ('<div class="lo-note">No organic data has synced yet. Run a sync from the '
                'LinkedIn Organic connector, then refresh this page.</div>')

    avg_engagement = (
        sum(p["engagement_rate"] for p in report.top_posts) / report.post_count
        if report.post_count else 0.0
    )

    follower_foot = (
        _period_delta(report.follower_gain)
        if report.follower_series
        else _sub("lifetime total")
    )
    cards = (
        '<div class="cards">'
        + _card("Followers", _fmt_int(report.total_followers),
                spark=_follower_sparkline(report.follower_series), foot=follower_foot)
        + _card("Posts", _fmt_int(report.post_count), foot=_sub("in selected period"))
        + _card("Impressions", _fmt_int(report.total_impressions), foot=_sub("across posts"))
        + _card("Reactions", _fmt_int(report.total_likes), foot=_sub("likes on posts"))
        + _card("Comments", _fmt_int(report.total_comments))
        + _card("Avg. engagement", _fmt_pct(avg_engagement), foot=_sub("per post"))
        + _card("Page views", _fmt_int(report.total_page_views),
                foot=_sub(f'<strong>{_fmt_int(report.total_unique_visitors)}</strong> unique visitors'))
        + '</div>'
    )
    kpi_section = (
        '<section>'
        '<div class="sec-head"><h2><span class="lo-dot"></span>Performance</h2>'
        f'<span class="status">last {_WINDOW_DAYS} days</span></div>'
        f'{cards}</section>'
    )

    follower_chart = _chart_block("loFollowerChart", report.follower_series)
    page_chart = _chart_block("loPageChart", report.page_series)
    trends_section = (
        '<section>'
        '<div class="sec-head"><h2>Trends</h2>'
        f'<span class="status">daily, last {_WINDOW_DAYS} days</span></div>'
        '<div class="lo-two">'
        '<div class="lo-chart-box"><div class="lo-chart-lab"><h3>Follower gains</h3>'
        '<span>net new / day</span></div>'
        f'{follower_chart}</div>'
        '<div class="lo-chart-box"><div class="lo-chart-lab"><h3>Page views</h3>'
        '<span>organization page / day</span></div>'
        f'{page_chart}</div>'
        '</div></section>'
    )

    if report.top_posts:
        rows = []
        for p in report.top_posts:
            meta = p["published_at"] or ""
            chip = f'<span class="lo-chip">{_esc(p["post_type"])}</span>' if p["post_type"] else ""
            rows.append(
                '<tr>'
                f'<td class="left" data-val="{_esc(p["title"])}"><span class="lo-post-title">{_esc(p["title"])}{chip}</span>'
                f'<span class="lo-post-meta">{_esc(meta)}</span></td>'
                f'<td data-val="{p["impressions"]}">{_fmt_int(p["impressions"])}</td>'
                f'<td data-val="{p["likes"]}">{_fmt_int(p["likes"])}</td>'
                f'<td data-val="{p["comments"]}">{_fmt_int(p["comments"])}</td>'
                f'<td data-val="{p["shares"]}">{_fmt_int(p["shares"])}</td>'
                f'<td data-val="{p["clicks"]}">{_fmt_int(p["clicks"])}</td>'
                f'<td data-val="{p["engagement_rate"]}">{_fmt_pct(p["engagement_rate"])}</td>'
                '</tr>'
            )
        posts_html = (
            '<div class="lo-table-wrap"><table id="loPostsTable" class="lo-table"><thead><tr>'
            '<th class="left" data-sort="text">Post</th><th data-sort="num">Impressions</th>'
            '<th data-sort="num">Reactions</th><th data-sort="num">Comments</th>'
            '<th data-sort="num">Shares</th><th data-sort="num">Clicks</th>'
            '<th data-sort="num">Engagement</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>'
        )
    else:
        posts_html = '<div class="lo-empty">No posts synced for this period yet.</div>'
    posts_section = (
        '<section><div class="sec-head"><h2>Top posts</h2>'
        '<span class="status">by impressions</span></div>'
        f'{posts_html}</section>'
    )

    charts_script = _charts_script(report.follower_series, report.page_series)
    sort_script = _sort_script() if report.top_posts else ""
    content = f"""
      <div class="lo-wrap">
        <div class="lo-head">
          <div>
            <h1 class="lo-title">LinkedIn Organic</h1>
            <p class="lo-sub">Company-page posts, followers &amp; page analytics for {_esc(label)}.</p>
          </div>
        </div>
        {note}
        {kpi_section}
        {trends_section}
        {posts_section}
      </div>
      {charts_script}
      {sort_script}
    """
    return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)


def _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin) -> str:
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="linkedin-organic",
        page_title="LinkedIn Organic",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
    )
