"""HTML renderer for the LinkedIn Organic page (company-page analytics)."""

from __future__ import annotations

import json
from typing import Any

from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc
from linkedin_organic_report_service import LinkedInOrganicReport

_LI_BLUE = "#0a66c2"
_PAGE_GREEN = "#16a34a"

_EXTRA_CSS = """
.lo-wrap { max-width: 1200px; }
.lo-head { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:4px; }
.lo-title { font-size:1.6rem; font-weight:750; color:var(--navy); margin:0; letter-spacing:-.01em; }
.lo-sub { font-size:.9rem; color:var(--muted); margin:6px 0 0; }
.lo-note { font-size:.88rem; color:var(--muted); background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin:16px 0; }

.lo-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:16px; margin:22px 0 24px; }
.lo-tile { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:18px 20px; box-shadow:var(--shadow-sm); position:relative; overflow:hidden; }
.lo-tile::before { content:""; position:absolute; inset:0 auto 0 0; width:4px; background:#0a66c2; opacity:.85; }
.lo-tile-label { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); font-weight:700; margin-bottom:8px; }
.lo-tile-value { font-size:1.9rem; font-weight:750; color:var(--navy); line-height:1.05; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
.lo-tile-sub { font-size:.78rem; color:var(--muted); margin-top:6px; }

.lo-card { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:20px 22px; margin-bottom:20px; box-shadow:var(--shadow-sm); }
.lo-card-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:0 0 16px; flex-wrap:wrap; }
.lo-card h2 { font-size:1rem; font-weight:700; color:var(--navy); margin:0; }
.lo-card-note { font-size:.78rem; color:var(--muted); }
.lo-cols { display:grid; grid-template-columns:1fr 1fr; gap:20px; align-items:start; }
.lo-cols .lo-card { margin-bottom:0; height:100%; }

.lo-chart { position:relative; height:180px; }
.lo-chart canvas { display:block; width:100% !important; }
.lo-empty { font-size:.86rem; color:var(--muted); padding:22px 0; text-align:center; }

.lo-table-wrap { overflow-x:auto; }
.lo-table { width:100%; border-collapse:collapse; font-size:.85rem; }
.lo-table th { text-align:right; font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:700; padding:8px 10px; border-bottom:1px solid var(--border); }
.lo-table th:first-child { text-align:left; }
.lo-table th[data-sort] { cursor:pointer; user-select:none; white-space:nowrap; }
.lo-table th[data-sort]:hover { color:var(--navy); }
.lo-table th[data-sort]::after { content:"\\2195"; opacity:.3; margin-left:5px; font-size:.85em; }
.lo-table th[data-dir="asc"]::after { content:"\\2191"; opacity:.9; }
.lo-table th[data-dir="desc"]::after { content:"\\2193"; opacity:.9; }
.lo-table td { padding:10px; border-bottom:1px solid var(--line,#eef1f4); text-align:right; font-variant-numeric:tabular-nums; }
.lo-table td:first-child { text-align:left; color:var(--text); max-width:420px; }
.lo-post-title { font-weight:600; color:var(--navy); display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:420px; }
.lo-post-meta { font-size:.72rem; color:var(--muted); }
.lo-chip { display:inline-block; font-size:.68rem; font-weight:650; text-transform:uppercase; letter-spacing:.03em; color:#0a66c2; background:#e6f0f8; border-radius:6px; padding:2px 7px; margin-left:6px; }
"""


def _fmt_int(n: int | float) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _tile(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="lo-tile-sub">{sub}</div>' if sub else ""
    return (
        '<div class="lo-tile">'
        f'<div class="lo-tile-label">{_esc(label)}</div>'
        f'<div class="lo-tile-value">{_esc(value)}</div>'
        f'{sub_html}</div>'
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

    tiles = (
        '<div class="lo-tiles">'
        + _tile("Followers", _fmt_int(report.total_followers),
                (f'<strong>+{_fmt_int(report.follower_gain)}</strong> in period'
                 if report.follower_gain else "lifetime total"))
        + _tile("Posts", _fmt_int(report.post_count), "in selected period")
        + _tile("Impressions", _fmt_int(report.total_impressions), "across posts")
        + _tile("Reactions", _fmt_int(report.total_likes), "likes on posts")
        + _tile("Comments", _fmt_int(report.total_comments))
        + _tile("Avg. engagement", _fmt_pct(avg_engagement), "per post")
        + _tile("Page views", _fmt_int(report.total_page_views),
                f'<strong>{_fmt_int(report.total_unique_visitors)}</strong> unique visitors')
        + '</div>'
    )

    follower_chart = _chart_block("loFollowerChart", report.follower_series)
    page_chart = _chart_block("loPageChart", report.page_series)
    cols = (
        '<div class="lo-cols">'
        '<div class="lo-card"><div class="lo-card-head"><h2>Follower gains</h2>'
        '<span class="lo-card-note">net new followers/day</span></div>'
        f'{follower_chart}</div>'
        '<div class="lo-card"><div class="lo-card-head"><h2>Page views</h2>'
        '<span class="lo-card-note">organization page/day</span></div>'
        f'{page_chart}</div>'
        '</div>'
    )

    if report.top_posts:
        rows = []
        for p in report.top_posts:
            meta = p["published_at"] or ""
            chip = f'<span class="lo-chip">{_esc(p["post_type"])}</span>' if p["post_type"] else ""
            rows.append(
                '<tr>'
                f'<td data-val="{_esc(p["title"])}"><span class="lo-post-title">{_esc(p["title"])}{chip}</span>'
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
            '<th data-sort="text">Post</th><th data-sort="num">Impressions</th>'
            '<th data-sort="num">Reactions</th><th data-sort="num">Comments</th>'
            '<th data-sort="num">Shares</th><th data-sort="num">Clicks</th>'
            '<th data-sort="num">Engagement</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>'
        )
    else:
        posts_html = '<div class="lo-empty">No posts synced for this period yet.</div>'
    posts_card = (
        '<div class="lo-card"><div class="lo-card-head"><h2>Top posts</h2>'
        '<span class="lo-card-note">by impressions</span></div>'
        f'{posts_html}</div>'
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
        {tiles}
        {cols}
        {posts_card}
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
