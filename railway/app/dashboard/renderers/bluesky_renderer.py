"""HTML renderer for the Bluesky page (organic social analytics).

Same visual language as the LinkedIn Organic page — a sticky filter bar, compact
KPI tiles with a top accent and delta foot, Chart.js trends, and the shared
sortable table — so the two social pages read as one product.

What it deliberately does not show: impressions, reach, engagement *rate*. The
AT Protocol publishes none of them, and a rate with no denominator would be
invented. The page reports engagements per post instead, and says so in a note
rather than leaving a reader to wonder where the usual columns went.
"""

from __future__ import annotations

import json
from typing import Any

from bluesky_report_service import BlueskyReport
from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc

_BS_BLUE = "#0285ff"
_ENGAGE_INK = "#7c5cff"
_WINDOW_DAYS = 90

# Date-range presets for the page filter: (query value, days, label). The query
# value is what lands in ``?range=`` and is validated by ``sanitize_range_days``
# so a stale or hand-typed value can't reach the report service.
_RANGE_PRESETS: tuple[tuple[str, int, str], ...] = (
    ("7", 7, "Last 7 days"),
    ("30", 30, "Last 30 days"),
    ("90", 90, "Last 90 days"),
    ("180", 180, "Last 180 days"),
    ("365", 365, "Last 365 days"),
)
_DEFAULT_RANGE_DAYS = _WINDOW_DAYS

# Characters of post text shown in the table before ellipsis. The full text
# rides along as a hover title.
_TEXT_CAP = 140


def sanitize_range_days(raw: Any) -> int:
    """Map a raw ``?range=`` value to one of the preset day counts, falling back
    to the default window when it's missing or unrecognized."""
    if raw is None:
        return _DEFAULT_RANGE_DAYS
    token = str(raw).strip()
    for value, days, _label in _RANGE_PRESETS:
        if token == value:
            return days
    return _DEFAULT_RANGE_DAYS


_EXTRA_CSS = """
.bs-wrap { width:100%; }
.bs-note { font-size:.88rem; color:var(--muted); background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin:0 0 16px; }

/* Sticky full-bleed filter bar — mirrors the LinkedIn Organic `.lo-bar`. */
.bs-bar { position:sticky; top:0; z-index:40; background:#fff; border-bottom:1px solid var(--border); box-shadow:0 1px 0 rgba(16,33,67,.04), 0 6px 16px -12px rgba(16,33,67,.28); margin:-28px -32px 22px; }
.bs-bar-inner { display:flex; align-items:center; justify-content:space-between; gap:10px 20px; flex-wrap:wrap; padding:12px 32px; }
.bs-bar-title { display:flex; flex-direction:column; gap:2px; min-width:0; }
.bs-bar-title h1 { margin:0; font-size:1.15rem; font-weight:800; color:var(--navy); letter-spacing:-.02em; }
.bs-bar-title p { margin:0; font-size:.8rem; color:var(--muted); overflow:hidden; text-overflow:ellipsis; }
.bs-handle { color:var(--accent); font-weight:700; text-decoration:none; }
.bs-handle:hover { text-decoration:underline; }

.bs-range { display:flex; align-items:center; gap:9px; flex-shrink:0; }
.bs-range label { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }
.bs-range select { font:inherit; font-size:.82rem; font-weight:650; color:var(--navy); background-color:#fff; border:1px solid var(--border); border-radius:9px; padding:7px 30px 7px 12px; cursor:pointer; -webkit-appearance:none; appearance:none; background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%2364748b' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'><path d='M4 6l4 4 4-4'/></svg>"); background-repeat:no-repeat; background-position:right 10px center; background-size:12px; transition:border-color .12s; }
.bs-range select:hover { border-color:var(--accent); }
.bs-range select:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }

@media (max-width:900px){ .bs-bar { top:52px; } }
@media (max-width:720px){ .bs-bar { margin:-18px -16px 18px; } .bs-bar-inner { padding:11px 16px; } }

/* Section panels */
.bs-wrap section { background:#fff; border:1px solid var(--border); border-radius:var(--radius); padding:18px 20px 20px; margin-bottom:16px; box-shadow:var(--shadow-sm); }
.bs-wrap .sec-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
.bs-wrap .sec-head h2 { margin:0; font-size:1rem; font-weight:750; color:var(--navy); display:flex; align-items:center; }
.bs-wrap .sec-head .status { margin:0; color:var(--muted); font-size:.78rem; text-align:right; flex-shrink:0; }
.bs-dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:#0285ff; margin-right:8px; }

/* KPI cards */
.bs-wrap .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.bs-wrap .card { border:1px solid var(--line-soft,#eff3f8); border-top:3px solid var(--accent); border-radius:var(--radius-sm,9px); padding:14px 15px 15px; background:#fff; }
.bs-wrap .card-title { color:var(--muted); font-size:.66rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }
.bs-wrap .card-value { margin-top:7px; font-size:1.55rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.bs-wrap .card-foot { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:2px 8px; margin-top:9px; min-height:20px; }
.bs-wrap .card-sub { color:var(--muted); font-size:.74rem; }
.bs-wrap .card-sub strong { color:var(--text); font-weight:700; }
.bs-spark { width:100%; height:30px; display:block; margin:8px 0 2px; }
.bs-delta { font-size:.74rem; font-weight:700; white-space:nowrap; }
.bs-delta.up { color:var(--ok); }
.bs-delta.down { color:var(--err); }
.bs-delta.flat { color:var(--muted); font-weight:600; }

/* Trend charts */
.bs-two { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.bs-two > * { min-width:0; }
.bs-chart-box { border:1px solid var(--line-soft,#eff3f8); border-radius:10px; padding:12px 14px; background:#fafcff; }
.bs-chart-lab { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-bottom:8px; }
.bs-chart-lab h3 { margin:0; font-size:.86rem; font-weight:700; color:var(--navy); }
.bs-chart-lab span { font-size:.72rem; color:var(--muted); }
.bs-chart { position:relative; height:180px; }
.bs-chart canvas { display:block; width:100% !important; }
.bs-empty { font-size:.85rem; color:var(--muted); padding:26px 0; text-align:center; }

/* Top posts table */
.bs-table-wrap { overflow:auto; border:1px solid var(--line-soft,#eff3f8); border-radius:var(--radius-sm,9px); }
.bs-table { border-collapse:collapse; width:100%; min-width:640px; font-size:.85rem; }
.bs-table th, .bs-table td { padding:10px 13px; border-bottom:1px solid var(--line-soft,#eff3f8); text-align:right; white-space:nowrap; }
.bs-table tbody tr:last-child td { border-bottom:0; }
.bs-table tbody tr:hover td { background:#f7faff; }
.bs-table th { background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; }
.bs-table th.left, .bs-table td.left { text-align:left; }
.bs-table th.left { width:440px; }
.bs-table td.left { max-width:440px; }
.bs-table th[data-sort] { cursor:pointer; user-select:none; transition:background .12s,color .12s; }
.bs-table th[data-sort]:hover { background:#e9eef5; color:#33455e; }
.bs-table th[data-sort]::after { content:"\\2195"; opacity:.35; margin-left:5px; font-size:.85em; }
.bs-table th[data-dir="asc"] { color:var(--accent); }
.bs-table th[data-dir="asc"]::after { content:"\\2191"; opacity:.9; }
.bs-table th[data-dir="desc"] { color:var(--accent); }
.bs-table th[data-dir="desc"]::after { content:"\\2193"; opacity:.9; }
.bs-post-text { font-weight:600; color:var(--navy); display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:440px; text-decoration:none; }
.bs-post-text:hover { color:var(--accent); text-decoration:underline; }
.bs-post-meta { font-size:.72rem; color:var(--muted); }
.bs-chip { display:inline-block; font-size:.64rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:#0285ff; background:#e6f2ff; border-radius:5px; padding:2px 6px; margin-left:6px; }

@media (max-width:820px){ .bs-two{ grid-template-columns:1fr; } }
"""


# ──────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_int(n: int | float) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_avg(x: float) -> str:
    return f"{float(x):.1f}" if x else "0"


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


def _foot(delta: str = "", sub_html: str = "") -> str:
    return f'{delta}{_sub(sub_html) if sub_html else ""}'


def _delta(current: float, prior: float, *, window_days: int) -> str:
    """Period-over-period change badge, as a percentage of the comparison
    window's value.

    Returns "" when the prior window has nothing to compare against: a client
    whose Bluesky sync only reaches back one window would otherwise see every
    metric flagged as a record.
    """
    if not prior:
        return ""
    pct = (current - prior) / abs(prior) * 100
    title = _esc(f"{_fmt_int(prior)} in the previous {window_days} days")
    if round(pct, 1) == 0:
        return f'<span class="bs-delta flat" title="{title}">No change</span>'
    arrow, cls = ("▲", "up") if pct > 0 else ("▼", "down")
    shown = f"{abs(pct):,.0f}%" if abs(pct) >= 100 else f"{abs(pct):.1f}%"
    return f'<span class="bs-delta {cls}" title="{title}">{arrow} {shown}</span>'


def _avg_delta(current: float, prior: float, *, window_days: int) -> str:
    """Like :func:`_delta` but for engagements-per-post, where the readable unit
    is the difference itself rather than a percentage of a small number."""
    if not prior:
        return ""
    diff = current - prior
    title = _esc(f"{_fmt_avg(prior)} per post in the previous {window_days} days")
    if round(diff, 1) == 0:
        return f'<span class="bs-delta flat" title="{title}">No change</span>'
    arrow, cls = ("▲", "up") if diff > 0 else ("▼", "down")
    return f'<span class="bs-delta {cls}" title="{title}">{arrow} {_fmt_avg(abs(diff))}</span>'


def _follower_delta(gain: int, window_days: int) -> str:
    """Follower change over the reporting window."""
    if gain > 0:
        return f'<span class="bs-delta up">▲ {_fmt_int(gain)} in {window_days} days</span>'
    if gain < 0:
        return f'<span class="bs-delta down">▼ {_fmt_int(abs(gain))} in {window_days} days</span>'
    return f'<span class="bs-delta flat">No change in {window_days} days</span>'


def _follower_sparkline(series: list[dict[str, Any]]) -> str:
    """Tiny inline-SVG follower curve. "" with fewer than two days to plot."""
    rows = [r for r in (series or []) if str(r.get("metric_date") or "")]
    if len(rows) < 2:
        return ""
    values = [float(r.get("followers") or 0) for r in rows]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    width, height = 100.0, 30.0
    step = width / (len(values) - 1)
    points = " ".join(
        f"{i * step:.2f},{height - ((v - lo) / span) * (height - 4) - 2:.2f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg class="bs-spark" viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" '
        f'aria-hidden="true"><polyline points="{points}" fill="none" stroke="{_BS_BLUE}" '
        'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


# ──────────────────────────────────────────────────────────────────────────────
# Page chrome
# ──────────────────────────────────────────────────────────────────────────────

def _range_picker(current_days: int) -> str:
    options = []
    for value, days, label in _RANGE_PRESETS:
        sel = " selected" if days == current_days else ""
        options.append(f'<option value="{value}"{sel}>{_esc(label)}</option>')
    return (
        '<div class="bs-range">'
        '<label for="bsRange">Date range</label>'
        '<select id="bsRange" aria-label="Date range">'
        f'{"".join(options)}</select>'
        '</div>'
    )


def _filter_bar(label: str, window_days: int, *, handle: str | None = None,
                subtitle: str | None = None) -> str:
    if subtitle is not None:
        sub = _esc(subtitle)
    elif handle:
        link = f"https://bsky.app/profile/{handle}"
        sub = (f'Posts, followers &amp; engagement for '
               f'<a class="bs-handle" href="{_esc(link)}" target="_blank" rel="noopener">'
               f'@{_esc(handle)}</a>.')
    else:
        sub = f'Posts, followers &amp; engagement for {_esc(label)}.'
    sub_html = f'<p>{sub}</p>' if sub else ""
    return (
        '<div class="bs-bar"><div class="bs-bar-inner">'
        f'<div class="bs-bar-title"><h1>Bluesky</h1>{sub_html}</div>'
        f'{_range_picker(window_days)}'
        '</div></div>'
    )


def _range_script() -> str:
    """Reload with the chosen ``?range=``, preserving other query params."""
    return (
        "<script>(function(){\n"
        "  function init(){\n"
        "    var sel = document.getElementById('bsRange');\n"
        "    if(!sel) return;\n"
        "    sel.addEventListener('change', function(){\n"
        "      var u = new URL(window.location.href);\n"
        "      u.searchParams.set('range', sel.value);\n"
        "      window.location.href = u.toString();\n"
        "    });\n"
        "  }\n"
        "  if(document.readyState!=='loading') init();\n"
        "  else document.addEventListener('DOMContentLoaded', init);\n"
        "})();</script>"
    )


def _charts_script(follower_series: list[dict[str, Any]],
                   engagement_series: list[dict[str, Any]]) -> str:
    """Chart.js loader + init for the follower line and engagement bars."""
    payload = json.dumps({
        "followers": {
            "canvas": "bsFollowerChart",
            "labels": [str(r.get("metric_date") or "") for r in follower_series],
            "values": [float(r.get("followers") or 0) for r in follower_series],
            "color": _BS_BLUE,
        },
        "engagement": {
            "canvas": "bsEngagementChart",
            "labels": [str(r.get("metric_date") or "") for r in engagement_series],
            "values": [float(r.get("engagements") or 0) for r in engagement_series],
            "posts": [int(r.get("posts") or 0) for r in engagement_series],
            "color": _ENGAGE_INK,
        },
    }).replace("<", "\\u003c")
    return (
        '<script src="/static/vendor/chart.umd.min.js"></script>'
        "<script>(function(){\n"
        f"  var BS = {payload};\n"
        "  var GRID={color:'rgba(148,163,184,.15)'};\n"
        "  var XT={maxRotation:0, autoSkip:true, maxTicksLimit:8, font:{size:10}, color:'#94a3b8'};\n"
        "  var YT={precision:0, font:{size:10}, color:'#94a3b8', maxTicksLimit:5};\n"
        "  function line(spec){\n"
        "    var el=document.getElementById(spec.canvas);\n"
        "    if(!el || !window.Chart || !spec.labels.length) return;\n"
        "    new Chart(el, {type:'line',\n"
        "      data:{labels:spec.labels, datasets:[{data:spec.values, borderColor:spec.color,\n"
        "        backgroundColor:'rgba(2,133,255,.10)', borderWidth:2, fill:true,\n"
        "        pointRadius:0, pointHoverRadius:4, tension:.3}]},\n"
        "      options:{responsive:true, maintainAspectRatio:false, animation:false,\n"
        "        plugins:{legend:{display:false},\n"
        "          tooltip:{displayColors:false, padding:8,\n"
        "            callbacks:{label:function(c){return c.formattedValue + ' followers';}}}},\n"
        # Followers barely move day to day, so a zero-based axis would flatten
        # the line into a straight rule. Let Chart.js frame the actual range.
        "        scales:{x:{grid:{display:false}, ticks:XT},\n"
        "          y:{grid:GRID, ticks:YT, border:{display:false}}}}\n"
        "    });\n"
        "  }\n"
        "  function bars(spec){\n"
        "    var el=document.getElementById(spec.canvas);\n"
        "    if(!el || !window.Chart || !spec.labels.length) return;\n"
        "    new Chart(el, {type:'bar',\n"
        "      data:{labels:spec.labels, datasets:[{data:spec.values,\n"
        "        backgroundColor:spec.color, hoverBackgroundColor:spec.color,\n"
        "        borderRadius:3, borderSkipped:false, maxBarThickness:22}]},\n"
        "      options:{responsive:true, maintainAspectRatio:false, animation:false,\n"
        "        plugins:{legend:{display:false},\n"
        "          tooltip:{displayColors:false, padding:8, callbacks:{\n"
        "            label:function(c){\n"
        "              var n=(spec.posts && spec.posts[c.dataIndex]) || 0;\n"
        "              return c.formattedValue + ' engagements from ' + n + (n===1?' post':' posts');\n"
        "            }}}},\n"
        "        scales:{x:{grid:{display:false}, ticks:XT},\n"
        "          y:{beginAtZero:true, grid:GRID, ticks:YT, border:{display:false}}}}\n"
        "    });\n"
        "  }\n"
        "  function init(){ line(BS.followers); bars(BS.engagement); }\n"
        "  if(document.readyState!=='loading') init();\n"
        "  else document.addEventListener('DOMContentLoaded', init);\n"
        "})();</script>"
    )


def _sort_script() -> str:
    """Click-to-sort on the Top posts table, numeric or text by ``data-sort``."""
    return (
        "<script>(function(){\n"
        "  function init(){\n"
        "    var table=document.getElementById('bsPostsTable');\n"
        "    if(!table) return;\n"
        "    var body=table.tBodies[0];\n"
        "    table.querySelectorAll('th[data-sort]').forEach(function(th, idx){\n"
        "      th.addEventListener('click', function(){\n"
        "        var dir = th.getAttribute('data-dir')==='desc' ? 'asc' : 'desc';\n"
        "        table.querySelectorAll('th[data-sort]').forEach(function(o){ o.removeAttribute('data-dir'); });\n"
        "        th.setAttribute('data-dir', dir);\n"
        "        var num = th.getAttribute('data-sort')==='num';\n"
        "        var rows=Array.prototype.slice.call(body.rows);\n"
        "        rows.sort(function(a,b){\n"
        "          var av=a.cells[idx].getAttribute('data-val')||'';\n"
        "          var bv=b.cells[idx].getAttribute('data-val')||'';\n"
        "          if(num){ av=parseFloat(av)||0; bv=parseFloat(bv)||0; return dir==='asc'? av-bv : bv-av; }\n"
        "          return dir==='asc' ? av.localeCompare(bv) : bv.localeCompare(av);\n"
        "        });\n"
        "        rows.forEach(function(r){ body.appendChild(r); });\n"
        "      });\n"
        "    });\n"
        "  }\n"
        "  if(document.readyState!=='loading') init();\n"
        "  else document.addEventListener('DOMContentLoaded', init);\n"
        "})();</script>"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Sections
# ──────────────────────────────────────────────────────────────────────────────

def _excerpt(text: str) -> str:
    body = " ".join((text or "").split())
    if not body:
        return "(no text)"
    return body if len(body) <= _TEXT_CAP else body[: _TEXT_CAP - 1].rstrip() + "…"


def _posts_table(posts: list[dict[str, Any]]) -> str:
    if not posts:
        return '<div class="bs-empty">No posts synced for this period yet.</div>'
    rows = []
    for p in posts:
        text = _excerpt(p.get("text") or "")
        chips = ""
        if p.get("is_reply"):
            chips += '<span class="bs-chip">Reply</span>'
        embed = str(p.get("embed_type") or "")
        if embed and embed != "text":
            chips += f'<span class="bs-chip">{_esc(embed)}</span>'
        url = str(p.get("url") or "")
        title_html = (
            f'<a class="bs-post-text" href="{_esc(url)}" target="_blank" rel="noopener" '
            f'title="{_esc(p.get("text") or "")}">{_esc(text)}</a>'
            if url else
            f'<span class="bs-post-text" title="{_esc(p.get("text") or "")}">{_esc(text)}</span>'
        )
        rows.append(
            '<tr>'
            f'<td class="left" data-val="{_esc(text)}">{title_html}'
            f'<span class="bs-post-meta">{_esc(p.get("post_date") or "")}{chips}</span></td>'
            f'<td data-val="{p.get("likes", 0)}">{_fmt_int(p.get("likes", 0))}</td>'
            f'<td data-val="{p.get("reposts", 0)}">{_fmt_int(p.get("reposts", 0))}</td>'
            f'<td data-val="{p.get("replies", 0)}">{_fmt_int(p.get("replies", 0))}</td>'
            f'<td data-val="{p.get("quotes", 0)}">{_fmt_int(p.get("quotes", 0))}</td>'
            f'<td data-val="{p.get("engagements", 0)}">{_fmt_int(p.get("engagements", 0))}</td>'
            '</tr>'
        )
    return (
        '<div class="bs-table-wrap"><table id="bsPostsTable" class="bs-table"><thead><tr>'
        '<th class="left" data-sort="text">Post</th>'
        '<th data-sort="num">Likes</th><th data-sort="num">Reposts</th>'
        '<th data-sort="num">Replies</th><th data-sort="num">Quotes</th>'
        '<th data-sort="num">Engagements</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table></div>'
    )


def _chart_block(canvas_id: str, series: list[dict[str, Any]]) -> str:
    if not series:
        return '<div class="bs-empty">No data for this period yet.</div>'
    return f'<div class="bs-chart"><canvas id="{_esc(canvas_id)}"></canvas></div>'


# ──────────────────────────────────────────────────────────────────────────────
# Page
# ──────────────────────────────────────────────────────────────────────────────

def render_bluesky(
    *,
    client_slug: str,
    label: str,
    report: BlueskyReport,
    range_days: int = _DEFAULT_RANGE_DAYS,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
) -> str:
    window_days = sanitize_range_days(range_days)
    range_script = _range_script()

    if not report.configured:
        content = (
            '<div class="bs-wrap">'
            f'{_filter_bar(label, window_days, subtitle="")}'
            f'<div class="bs-note">{_esc(report.error or "Bluesky is not configured for this client.")} '
            'Connect the Bluesky connector and run a sync to enable this report.</div>'
            '</div>'
            f'{range_script}'
        )
        return _shell(client_slug, label, content, access_key, use_session,
                      session_email, session_is_admin)

    has_any = bool(report.top_posts or report.follower_series)
    note = ""
    if not has_any:
        note = ('<div class="bs-note">No Bluesky data has synced yet. Run a sync from the '
                'Bluesky connector, then refresh this page.</div>')

    def _d(current: float, prior: float) -> str:
        return _delta(current, prior, window_days=window_days)

    follower_foot = (
        _follower_delta(report.follower_gain, window_days)
        if report.follower_series else _sub("current total")
    )
    cards = "".join([
        _card("Followers", _fmt_int(report.followers),
              spark=_follower_sparkline(report.follower_series), foot=follower_foot),
        _card("Posts", _fmt_int(report.post_count),
              foot=_foot(_d(report.post_count, report.prev_post_count),
                         "in selected period")),
        _card("Engagements", _fmt_int(report.total_engagements),
              foot=_foot(_d(report.total_engagements, report.prev_total_engagements),
                         "likes, reposts, replies &amp; quotes")),
        _card("Likes", _fmt_int(report.total_likes),
              foot=_foot(_d(report.total_likes, report.prev_total_likes))),
        _card("Reposts", _fmt_int(report.total_reposts),
              foot=_foot(_d(report.total_reposts, report.prev_total_reposts))),
        _card("Replies", _fmt_int(report.total_replies),
              foot=_foot(_d(report.total_replies, report.prev_total_replies))),
        _card("Avg. per post", _fmt_avg(report.avg_engagements),
              foot=_foot(_avg_delta(report.avg_engagements, report.prev_avg_engagements,
                                    window_days=window_days),
                         "engagements")),
    ])
    compared = any((
        report.prev_post_count, report.prev_total_engagements, report.prev_total_likes,
        report.prev_total_reposts, report.prev_total_replies,
    ))
    kpi_status = (f"last {window_days} days &middot; vs previous {window_days} days"
                  if compared else f"last {window_days} days")
    kpi_section = (
        '<section>'
        '<div class="sec-head"><h2><span class="bs-dot"></span>Performance</h2>'
        f'<span class="status">{kpi_status}</span></div>'
        f'<div class="cards">{cards}</div></section>'
    )

    trends_section = (
        '<section>'
        '<div class="sec-head"><h2>Trends</h2>'
        f'<span class="status">last {window_days} days</span></div>'
        '<div class="bs-two">'
        '<div class="bs-chart-box"><div class="bs-chart-lab"><h3>Followers</h3>'
        '<span>total, per sync</span></div>'
        f'{_chart_block("bsFollowerChart", report.follower_series)}</div>'
        '<div class="bs-chart-box"><div class="bs-chart-lab"><h3>Engagement</h3>'
        '<span>by day posted</span></div>'
        f'{_chart_block("bsEngagementChart", report.engagement_series)}</div>'
        '</div></section>'
    )

    posts_section = (
        '<section><div class="sec-head"><h2>Top posts</h2>'
        '<span class="status">by engagements</span></div>'
        f'{_posts_table(report.top_posts)}</section>'
    )

    # Said once, plainly, so nobody hunts for the columns the other social pages
    # have. Bluesky's API publishes no impression, reach or click data at all.
    synced = (f' Last synced {_esc(report.last_synced)}.' if report.last_synced else "")
    footnote = (
        '<div class="bs-note">Bluesky publishes likes, reposts, replies, quotes and '
        'follower counts — it has no impressions, reach or click data, so those '
        'figures can’t be reported here. Engagement totals keep rising after a post '
        f'goes out; each is shown as of the most recent sync.{synced}</div>'
    )

    charts_script = _charts_script(report.follower_series, report.engagement_series)
    sort_script = _sort_script() if report.top_posts else ""
    content = f"""
      <div class="bs-wrap">
        {_filter_bar(label, window_days, handle=report.handle)}
        {note}
        {kpi_section}
        {trends_section}
        {posts_section}
        {footnote}
      </div>
      {charts_script}
      {sort_script}
      {range_script}
    """
    return _shell(client_slug, label, content, access_key, use_session,
                  session_email, session_is_admin)


def _shell(client_slug, label, content, access_key, use_session,
           session_email, session_is_admin) -> str:
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="bluesky",
        page_title="Bluesky",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
    )
