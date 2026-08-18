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
# Standout pins sit on top of both lines, so they take a colour neither
# series uses rather than a third shade of blue.
_MARK_INK = "#f97316"
_WINDOW_DAYS = 90
# Titles listed in an engagement-chart tooltip for one publish day; the rest
# collapse into a "+ N more" line so a heavy posting day stays readable.
_MARKER_TITLE_CAP = 6
# Posts the engagement chart names on the plot itself. Small on purpose: the
# point is the handful worth explaining a spike with, and a daily poster would
# otherwise bury the lines under its own cadence.
_STANDOUT_CAP = 5
# Below this many posts there is no distribution to speak of, so every post is
# named rather than measured against a median of one or two.
_STANDOUT_MIN_POSTS = 6

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

# Component styles mirror the BigQuery dashboard's card/section/table language so
# this page reads as part of the same product. A couple of design tokens the
# shell's :root doesn't define (--line-soft, --radius-sm) are inlined here.
_EXTRA_CSS = """
.lo-wrap { width:100%; }
.lo-note { font-size:.88rem; color:var(--muted); background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin:0 0 16px; }

/* Sticky full-bleed filter bar — mirrors the Overview page's `.date-bar`.
   Negative margins break the bar out of the shell's .dash-content padding so it
   spans the content column edge-to-edge; its own inner padding re-aligns the
   title/filter with the cards below. On small screens it rests just under the
   fixed 52px mobile top bar (top:52px). */
.lo-bar { position:sticky; top:0; z-index:40; background:#fff; border-bottom:1px solid var(--border); box-shadow:0 1px 0 rgba(16,33,67,.04), 0 6px 16px -12px rgba(16,33,67,.28); margin:-28px -32px 22px; }
.lo-bar-inner { display:flex; align-items:center; justify-content:space-between; gap:10px 20px; flex-wrap:wrap; padding:12px 32px; }
.lo-bar-title { display:flex; flex-direction:column; gap:2px; min-width:0; }
.lo-bar-title h1 { margin:0; font-size:1.15rem; font-weight:800; color:var(--navy); letter-spacing:-.02em; }
.lo-bar-title p { margin:0; font-size:.8rem; color:var(--muted); overflow:hidden; text-overflow:ellipsis; }

/* Date-range filter — native select styled to match the dashboard chrome. */
.lo-range { display:flex; align-items:center; gap:9px; flex-shrink:0; }
.lo-range label { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }
.lo-range select { font:inherit; font-size:.82rem; font-weight:650; color:var(--navy); background-color:#fff; border:1px solid var(--border); border-radius:9px; padding:7px 30px 7px 12px; cursor:pointer; -webkit-appearance:none; appearance:none; background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%2364748b' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'><path d='M4 6l4 4 4-4'/></svg>"); background-repeat:no-repeat; background-position:right 10px center; background-size:12px; transition:border-color .12s; }
.lo-range select:hover { border-color:var(--accent); }
.lo-range select:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }

@media (max-width:900px){ .lo-bar { top:52px; } }
@media (max-width:720px){ .lo-bar { margin:-18px -16px 18px; } .lo-bar-inner { padding:11px 16px; } }

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

/* Standout-post key above the engagement chart: the numbers here are the
   numbers pinned on the plot, so the list doubles as the chart's legend. */
.lo-mk-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin:-6px 0 8px; }
.lo-mk-toggle { display:inline-flex; align-items:center; gap:7px; font-size:.76rem; color:var(--muted); font-weight:650; cursor:pointer; user-select:none; }
.lo-mk-toggle input { accent-color:#f97316; margin:0; cursor:pointer; }
.lo-mk-count { color:var(--muted); font-size:.74rem; font-weight:600; opacity:.85; }
.lo-mk-list { list-style:none; display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:2px 18px; margin:0 0 12px; padding:0; }
.lo-mk-list li { display:flex; align-items:center; gap:8px; min-width:0; font-size:.78rem; color:var(--text); padding:2px 0; }
.lo-mk-num { flex-shrink:0; width:16px; height:16px; border-radius:50%; background:#f97316; color:#fff; font-size:.62rem; font-weight:800; display:inline-flex; align-items:center; justify-content:center; }
.lo-mk-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
.lo-mk-imp { margin-left:auto; flex-shrink:0; color:var(--muted); font-size:.72rem; font-variant-numeric:tabular-nums; }

/* Top posts table — shared modern table styling. The Post column width is a
   CSS var so a drag-resizer can widen/narrow it (and its text truncation). */
.lo-table-wrap { overflow:auto; border:1px solid var(--line-soft,#eff3f8); border-radius:var(--radius-sm,9px); }
.lo-table { border-collapse:collapse; width:100%; min-width:640px; font-size:.85rem; --lo-post-w:420px; }
.lo-table th, .lo-table td { padding:10px 13px; border-bottom:1px solid var(--line-soft,#eff3f8); text-align:right; white-space:nowrap; }
.lo-table tbody tr:last-child td { border-bottom:0; }
.lo-table tbody tr:hover td { background:#f7faff; }
.lo-table th { background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; }
.lo-table th.left, .lo-table td.left { text-align:left; }
.lo-table th.left { position:relative; width:var(--lo-post-w,420px); }
.lo-table td.left { max-width:var(--lo-post-w,420px); }
.lo-table th[data-sort] { cursor:pointer; user-select:none; transition:background .12s,color .12s; }
.lo-table th[data-sort]:hover { background:#e9eef5; color:#33455e; }
.lo-table th[data-sort]::after { content:"\\2195"; opacity:.35; margin-left:5px; font-size:.85em; }
.lo-table th[data-dir="asc"] { color:var(--accent); }
.lo-table th[data-dir="asc"]::after { content:"\\2191"; opacity:.9; }
.lo-table th[data-dir="desc"] { color:var(--accent); }
.lo-table th[data-dir="desc"]::after { content:"\\2193"; opacity:.9; }
.lo-post-title { font-weight:650; color:var(--navy); display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:var(--lo-post-w,420px); cursor:help; }
.lo-post-meta { font-size:.72rem; color:var(--muted); }

/* Drag handle to resize the Post column. Sits on the header's right edge and
   doesn't trigger the column's click-to-sort (JS stops propagation). */
.lo-col-resizer { position:absolute; top:0; right:0; width:9px; height:100%; cursor:col-resize; user-select:none; touch-action:none; z-index:2; }
.lo-col-resizer::before { content:""; position:absolute; top:22%; right:3px; width:2px; height:56%; background:#c4cfdd; border-radius:2px; transition:background .12s; }
.lo-col-resizer:hover::before, .lo-table th.lo-resizing .lo-col-resizer::before { background:var(--accent); }

/* Desktop vs. mobile doughnut. */
.lo-pie { position:relative; height:170px; max-width:220px; margin:2px auto 12px; }
.lo-pie canvas { display:block; }
.lo-chip { display:inline-block; font-size:.64rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:#0a66c2; background:#e9f1fb; border-radius:5px; padding:2px 6px; margin-left:6px; }
.lo-react { color:var(--muted); font-size:.72rem; cursor:default; }

/* Ranked horizontal bars (demographics, sections). */
.lo-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px; }
.lo-bar-card h3 { margin:0 0 12px; font-size:.82rem; font-weight:750; color:var(--navy); }
.lo-bars { display:flex; flex-direction:column; gap:11px; }
.lo-bar-row { display:grid; grid-template-columns:1fr auto; gap:4px 12px; align-items:center; font-size:.8rem; }
.lo-bar-label { color:var(--text); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.lo-bar-val { text-align:right; color:var(--navy); font-variant-numeric:tabular-nums; font-weight:700; }
.lo-bar-val span { color:var(--muted); font-weight:600; font-size:.72rem; margin-left:5px; }
.lo-bar-track { grid-column:1 / -1; display:block; background:#e9eef5; border-radius:6px; height:8px; overflow:hidden; }
.lo-bar-fill { display:block; height:100%; border-radius:6px; min-width:3px; background:#0a66c2; }

/* Device split legend — accompanies the desktop/mobile doughnut. */
.lo-split-legend { display:flex; gap:20px; flex-wrap:wrap; font-size:.8rem; justify-content:center; }
.lo-split-legend div { display:flex; align-items:center; gap:7px; color:var(--text); }
.lo-split-legend .sw { width:10px; height:10px; border-radius:3px; }
.lo-split-legend b { color:var(--navy); font-variant-numeric:tabular-nums; }
.lo-split-legend span { color:var(--muted); }

@media (max-width:820px){ .lo-two{ grid-template-columns:1fr; } .lo-grid{ grid-template-columns:1fr; } }
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


def _range_picker(current_days: int) -> str:
    """Date-range dropdown. The reload-on-change handler is wired up by
    ``_range_script`` (an addEventListener block) rather than an inline
    ``onchange`` attribute, matching the page's other interactive controls."""
    options = []
    for value, days, label in _RANGE_PRESETS:
        sel = " selected" if days == current_days else ""
        options.append(f'<option value="{value}"{sel}>{_esc(label)}</option>')
    return (
        '<div class="lo-range">'
        '<label for="loRange">Date range</label>'
        f'<select id="loRange" aria-label="Date range">'
        f'{"".join(options)}</select>'
        '</div>'
    )


def _filter_bar(label: str, window_days: int, *, subtitle: str | None = None) -> str:
    """Sticky top filter bar: page identity on the left, date-range control on
    the right — the LinkedIn Organic equivalent of the Overview `.date-bar`."""
    sub = (
        f'Company-page posts, followers &amp; page analytics for {_esc(label)}.'
        if subtitle is None else _esc(subtitle)
    )
    sub_html = f'<p>{sub}</p>' if sub else ""
    return (
        '<div class="lo-bar"><div class="lo-bar-inner">'
        f'<div class="lo-bar-title"><h1>LinkedIn Organic</h1>{sub_html}</div>'
        f'{_range_picker(window_days)}'
        '</div></div>'
    )


def _range_script() -> str:
    """Reload the page with the chosen ``?range=`` when the date-range select
    changes, preserving any other query params on the current URL."""
    return (
        "<script>(function(){\n"
        "  function init(){\n"
        "    var sel = document.getElementById('loRange');\n"
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


# Reaction-type codes -> label, matching linkedin_organic_service._REACTION_LABELS.
_REACTION_LABELS = {
    "LIKE": "Like", "PRAISE": "Celebrate", "APPRECIATION": "Love",
    "EMPATHY": "Support", "INTEREST": "Insightful", "ENTERTAINMENT": "Funny",
    "MAYBE": "Curious",
}


def _reactions_note(reactions: dict[str, int] | None) -> str:
    """A hover note listing the reaction-type split for a post, when available."""
    if not reactions:
        return ""
    items = sorted(reactions.items(), key=lambda kv: kv[1], reverse=True)
    title = " · ".join(
        f"{_REACTION_LABELS.get(t, t.title())} {_fmt_int(v)}" for t, v in items
    )
    return f' <span class="lo-react" title="{_esc(title)}">▾</span>'


def _period_delta(gain: int, window_days: int = _WINDOW_DAYS) -> str:
    """▲/▼ follower change over the reporting window, styled like the Overview
    card's delta badge."""
    if gain > 0:
        return f'<span class="lo-delta up">▲ {_fmt_int(gain)} in {window_days} days</span>'
    if gain < 0:
        return f'<span class="lo-delta down">▼ {_fmt_int(abs(gain))} in {window_days} days</span>'
    return f'<span class="lo-delta flat">No change in {window_days} days</span>'


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


_DEMO_PANELS = (
    ("seniority", "By seniority"),
    ("function", "By job function"),
    ("industry", "By industry"),
    ("company_size", "By company size"),
    ("region", "By region"),
)


def _ranked_bars(rows: list[dict[str, Any]], *, label_key: str, value_key: str,
                 top: int = 6) -> str:
    """Ranked horizontal bars with a share-of-total note."""
    ranked = [r for r in rows if int(r.get(value_key) or 0) > 0][:top]
    if not ranked:
        return '<div class="lo-empty">No data yet.</div>'
    total = sum(int(r.get(value_key) or 0) for r in rows) or 1
    peak = max(int(r.get(value_key) or 0) for r in ranked) or 1
    out = ['<div class="lo-bars">']
    for r in ranked:
        v = int(r.get(value_key) or 0)
        width = max(3.0, v / peak * 100.0)
        share = v / total * 100.0
        out.append(
            '<div class="lo-bar-row">'
            f'<span class="lo-bar-label">{_esc(str(r.get(label_key) or "Unknown"))}</span>'
            f'<span class="lo-bar-val">{_fmt_int(v)}<span>{share:.0f}%</span></span>'
            f'<span class="lo-bar-track"><span class="lo-bar-fill" style="width:{width:.1f}%"></span></span>'
            '</div>'
        )
    out.append('</div>')
    return "".join(out)


def _demographics_section(report: LinkedInOrganicReport) -> str:
    """Follower demographics — one ranked-bar card per available dimension."""
    demo = report.follower_demographics or {}
    cards = []
    for dim, title in _DEMO_PANELS:
        rows = demo.get(dim) or []
        if not rows:
            continue
        cards.append(
            '<div class="lo-bar-card">'
            f'<h3>{_esc(title)}</h3>'
            f'{_ranked_bars(rows, label_key="category", value_key="total_followers")}'
            '</div>'
        )
    if not cards:
        return ""
    return (
        '<section><div class="sec-head"><h2>Follower demographics</h2>'
        '<span class="status">who follows this page · lifetime</span></div>'
        f'<div class="lo-grid">{"".join(cards)}</div></section>'
    )


def _visitors_section(report: LinkedInOrganicReport, window_days: int = _WINDOW_DAYS) -> str:
    """Page-visitor breakdown: desktop vs mobile split + views by page section."""
    desktop = int(report.page_desktop_views or 0)
    mobile = int(report.page_mobile_views or 0)
    sections = report.page_sections or []
    if not (desktop or mobile or sections):
        return ""

    device_html = ""
    if desktop or mobile:
        total = desktop + mobile or 1
        d_pct = desktop / total * 100.0
        m_pct = mobile / total * 100.0
        device_html = (
            '<div class="lo-bar-card"><h3>Desktop vs. mobile</h3>'
            '<div class="lo-pie"><canvas id="loDeviceChart"></canvas></div>'
            '<div class="lo-split-legend">'
            f'<div><span class="sw" style="background:#0a66c2"></span>Desktop&nbsp;<b>{_fmt_int(desktop)}</b>&nbsp;<span>{d_pct:.0f}%</span></div>'
            f'<div><span class="sw" style="background:#38bdf8"></span>Mobile&nbsp;<b>{_fmt_int(mobile)}</b>&nbsp;<span>{m_pct:.0f}%</span></div>'
            '</div></div>'
        )

    section_html = ""
    if sections:
        section_html = (
            '<div class="lo-bar-card"><h3>Views by page section</h3>'
            f'{_ranked_bars(sections, label_key="label", value_key="views", top=8)}'
            '</div>'
        )

    return (
        '<section><div class="sec-head"><h2>Page visitors</h2>'
        f'<span class="status">last {window_days} days</span></div>'
        f'<div class="lo-grid">{device_html}{section_html}</div></section>'
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


def _engagement_chart_series(series: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "labels": [str(r.get("metric_date") or "") for r in series],
        "impressions": [float(r.get("impressions") or 0) for r in series],
        "reach": [float(r.get("unique_impressions") or 0) for r in series],
    }


def _post_markers(
    markers: list[dict[str, Any]] | None,
    engagement_series: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Shape the window's posts into what the engagement chart plots.

    Two things, because a publish flag alone stops meaning anything for a client
    who posts every day — on a daily poster it is a constant, and the chart just
    fills up with identical marks:

    * ``volume`` — posts per chart column, drawn as bars behind the lines. This
      is the part that varies for a daily poster (three posts vs seven), so it
      is what a reader can actually correlate against impressions.
    * ``standouts`` — the few best-performing posts by impressions, pinned to
      their publish day and named. A spike worth explaining is nearly always one
      of these, and capping the count keeps them legible at any cadence.

    Posts published on a day the engagement series doesn't cover are dropped —
    there is no column to pin them to.
    """
    labels = [str(r.get("metric_date") or "") for r in (engagement_series or [])]
    index = {day: i for i, day in enumerate(labels)}
    in_window = [
        m for m in (markers or [])
        if str(m.get("published_at") or "") in index
    ]

    by_day: dict[str, list[dict[str, Any]]] = {}
    for m in in_window:
        by_day.setdefault(str(m["published_at"]), []).append(m)

    def _label(m: dict[str, Any]) -> str:
        kind = str(m.get("post_type") or "")
        title = str(m.get("title") or "Untitled post")
        return f"{title} ({kind})" if kind else title

    # One bar per chart column; days without a post stay null so Chart.js draws
    # nothing rather than a zero-height stub.
    volume = [len(by_day.get(day, [])) or None for day in labels]

    # Standouts are the best posts in the window, not merely good ones: a client
    # who posts twice a month shouldn't get both of them flagged as remarkable.
    ranked = sorted(in_window, key=lambda m: int(m.get("impressions") or 0), reverse=True)
    floor = _standout_floor(ranked)
    standouts = [
        {"i": index[str(m["published_at"])],
         "label": _label(m),
         "impressions": int(m.get("impressions") or 0)}
        for m in ranked[:_STANDOUT_CAP]
        if int(m.get("impressions") or 0) >= floor
    ]

    by_label = {
        day: [_label(m) for m in sorted(
            posts, key=lambda m: int(m.get("impressions") or 0), reverse=True)][:_MARKER_TITLE_CAP]
        for day, posts in by_day.items()
    }
    return {"on": True, "volume": volume, "standouts": standouts,
            "byLabel": by_label,
            "counts": {day: len(posts) for day, posts in by_day.items()},
            "postDays": len(by_day), "posts": len(in_window)}


def _standout_floor(ranked: list[dict[str, Any]]) -> int:
    """Impression floor a post must clear to be called out by name.

    Twice the median post in the window: enough to mean "this one did something
    the others didn't," and it lets a quiet window flag nothing at all rather
    than dressing up its least-bad post as a hit.
    """
    values = [int(m.get("impressions") or 0) for m in ranked]
    if len(values) < _STANDOUT_MIN_POSTS or not any(values):
        return 0 if len(values) < _STANDOUT_MIN_POSTS else 1
    mid = sorted(values)[len(values) // 2]
    return max(2 * mid, 1)


def _charts_script(
    follower_series: list[dict[str, Any]],
    page_series: list[dict[str, Any]],
    engagement_series: list[dict[str, Any]] | None = None,
    *,
    desktop_views: int = 0,
    mobile_views: int = 0,
    post_markers: list[dict[str, Any]] | None = None,
) -> str:
    """Chart.js loader + init for the follower/page bar charts, the
    organization-wide engagement line chart, and the device-split pie."""
    payload = json.dumps({
        "follower": {**_chart_series(follower_series, "total_follower_gain"),
                     "color": _LI_BLUE, "canvas": "loFollowerChart"},
        "page": {**_chart_series(page_series, "page_views"),
                 "color": _PAGE_GREEN, "canvas": "loPageChart"},
        "engagement": {**_engagement_chart_series(engagement_series or []),
                       "canvas": "loEngagementChart",
                       "markers": _post_markers(post_markers, engagement_series)},
        "device": {"labels": ["Desktop", "Mobile"],
                   "values": [int(desktop_views or 0), int(mobile_views or 0)],
                   "colors": [_LI_BLUE, "#38bdf8"], "canvas": "loDeviceChart"},
    }).replace("<", "\\u003c")
    return (
        '<script src="/static/vendor/chart.umd.min.js"></script>'
        "<script>(function(){\n"
        f"  var LO_CHARTS = {payload};\n"
        "  var GRID={color:'rgba(148,163,184,.15)'};\n"
        "  var XT={maxRotation:0, autoSkip:true, maxTicksLimit:8, font:{size:10}, color:'#94a3b8'};\n"
        "  var YT={precision:0, font:{size:10}, color:'#94a3b8', maxTicksLimit:5};\n"
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
        "        scales:{x:{grid:{display:false}, ticks:XT},\n"
        "          y:{beginAtZero:true, grid:GRID, ticks:YT, border:{display:false}}}}\n"
        "    });\n"
        "  }\n"
        # Publish-day annotations: a dashed rule plus a dot at the top of the
        # plot for each day a post went out, so cadence reads against the lines.
        # Standout pins: a rule down to the plot floor and a numbered dot for
        # the few posts big enough to explain a spike with. Deliberately not one
        # per publish day — a daily poster turns that into a picket fence.
        "  var MARKERS={id:'loPostMarkers', afterDatasetsDraw:function(chart){\n"
        "    var m=chart.$loMarkers;\n"
        "    if(!m || !m.on || !m.standouts.length) return;\n"
        "    var xs=chart.scales.x, area=chart.chartArea, ctx=chart.ctx;\n"
        "    if(!xs || !area) return;\n"
        "    ctx.save();\n"
        "    m.standouts.forEach(function(d, n){\n"
        "      var px = xs.getPixelForValue(d.i);\n"
        "      if(!isFinite(px) || px < area.left-1 || px > area.right+1) return;\n"
        "      ctx.beginPath(); ctx.setLineDash([3,3]); ctx.lineWidth=1;\n"
        "      ctx.strokeStyle='rgba(15,23,42,.30)';\n"
        "      ctx.moveTo(px, area.top+12); ctx.lineTo(px, area.bottom); ctx.stroke();\n"
        "      ctx.setLineDash([]);\n"
        "      ctx.beginPath(); ctx.arc(px, area.top+5, 7.5, 0, Math.PI*2);\n"
        "      ctx.fillStyle='" + _MARK_INK + "'; ctx.fill();\n"
        "      ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.stroke();\n"
        "      ctx.fillStyle='#fff'; ctx.font='700 9px system-ui, sans-serif';\n"
        "      ctx.textAlign='center'; ctx.textBaseline='middle';\n"
        "      ctx.fillText(String(n+1), px, area.top+5);\n"
        "    });\n"
        "    ctx.restore();\n"
        "  }};\n"
        "  function drawLine(spec){\n"
        "    var el = document.getElementById(spec.canvas);\n"
        "    if(!el || !window.Chart || !spec.labels.length) return;\n"
        "    function ds(label,data,color){return {label:label, data:data, borderColor:color,\n"
        "      backgroundColor:color, fill:false, tension:.3, borderWidth:2, pointRadius:0,\n"
        "      pointHoverRadius:4, order:0};}\n"
        "    var mk = spec.markers || {on:false, volume:[], standouts:[], byLabel:{}, counts:{}};\n"
        "    var sets = [ds('Impressions', spec.impressions, '" + _LI_BLUE + "'),\n"
        "                ds('Reach', spec.reach, '#38bdf8')];\n"
        # Posts/day rides a hidden right-hand axis padded well above the busiest
        # day, so the bars read as a band along the floor rather than a second
        # chart competing with the lines.
        "    var vol = mk.volume || [];\n"
        "    var peak = vol.reduce(function(a,b){return Math.max(a, b||0);}, 1);\n"
        "    if(vol.length){ sets.push({type:'bar', label:'Posts / day', data:vol,\n"
        "      yAxisID:'y1', backgroundColor:'rgba(100,116,139,.28)',\n"
        "      hoverBackgroundColor:'rgba(100,116,139,.55)', borderRadius:2,\n"
        "      borderSkipped:false, maxBarThickness:14, order:9}); }\n"
        "    var chart = new Chart(el, {\n"
        "      type:'line',\n"
        "      plugins:[MARKERS],\n"
        "      data:{labels:spec.labels, datasets:sets},\n"
        "      options:{responsive:true, maintainAspectRatio:false, animation:false,\n"
        "        interaction:{mode:'index', intersect:false},\n"
        "        layout:{padding:{top:8}},\n"
        "        plugins:{legend:{display:true, position:'bottom',\n"
        "            labels:{boxWidth:10, boxHeight:10, font:{size:11}, color:'#64748b'}},\n"
        "          tooltip:{padding:8, callbacks:{afterBody:function(items){\n"
        "            var m = chart && chart.$loMarkers;\n"
        "            if(!m) return '';\n"
        "            var day = (items && items.length) ? items[0].label : '';\n"
        "            var titles = m.byLabel[day];\n"
        "            if(!titles || !titles.length) return '';\n"
        "            var total = m.counts[day] || titles.length;\n"
        "            var out = ['', total + (total===1?' post published:':' posts published:')];\n"
        "            titles.forEach(function(t){\n"
        "              out.push('\\u2022 ' + (t.length>64 ? t.slice(0,63)+'\\u2026' : t)); });\n"
        "            if(total > titles.length) out.push('+ '+(total-titles.length)+' more');\n"
        "            return out;\n"
        "          }}}},\n"
        "        scales:{x:{grid:{display:false}, ticks:XT},\n"
        "          y:{beginAtZero:true, grid:GRID, ticks:YT, border:{display:false}},\n"
        "          y1:{display:false, position:'right', beginAtZero:true,\n"
        "            suggestedMax: peak*3.2, grid:{display:false}}}}\n"
        "    });\n"
        "    chart.$loMarkers = mk;\n"
        "    var tgl = document.getElementById('loMarkerToggle');\n"
        "    if(tgl){ tgl.addEventListener('change', function(){\n"
        "      chart.$loMarkers.on = !!tgl.checked; chart.update('none'); }); }\n"
        "  }\n"
        "  function drawPie(spec){\n"
        "    var el = document.getElementById(spec.canvas);\n"
        "    var tot = (spec.values||[]).reduce(function(a,b){return a+b;},0);\n"
        "    if(!el || !window.Chart || !tot) return;\n"
        "    new Chart(el, {\n"
        "      type:'doughnut',\n"
        "      data:{labels:spec.labels, datasets:[{data:spec.values,\n"
        "        backgroundColor:spec.colors, borderColor:'#fff', borderWidth:2}]},\n"
        "      options:{responsive:true, maintainAspectRatio:false, animation:false,\n"
        "        cutout:'58%',\n"
        "        plugins:{legend:{display:false},\n"
        "          tooltip:{displayColors:false, padding:8,\n"
        "            callbacks:{label:function(c){\n"
        "              var p = tot ? Math.round(c.parsed/tot*100) : 0;\n"
        "              return c.label+': '+c.formattedValue+' ('+p+'%)';}}}}}\n"
        "    });\n"
        "  }\n"
        "  function init(){ draw(LO_CHARTS.follower); draw(LO_CHARTS.page);\n"
        "    drawLine(LO_CHARTS.engagement); drawPie(LO_CHARTS.device); }\n"
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


def _resize_script() -> str:
    """Drag-to-resize the Top posts 'Post' column via a --lo-post-w CSS var."""
    return (
        "<script>(function(){\n"
        "  var MIN=140, MAX=900;\n"
        "  function initResize(table){\n"
        "    var grip = table.querySelector('.lo-col-resizer');\n"
        "    if(!grip) return;\n"
        "    var th = grip.closest('th');\n"
        "    var startX=0, startW=0, dragging=false;\n"
        "    function onMove(e){\n"
        "      if(!dragging) return;\n"
        "      if(e.cancelable) e.preventDefault();\n"
        "      var pt = e.touches ? e.touches[0] : e;\n"
        "      var w = Math.max(MIN, Math.min(MAX, startW + (pt.clientX - startX)));\n"
        "      table.style.setProperty('--lo-post-w', w + 'px');\n"
        "    }\n"
        "    function onUp(){\n"
        "      dragging=false; th.classList.remove('lo-resizing');\n"
        "      document.removeEventListener('mousemove', onMove);\n"
        "      document.removeEventListener('mouseup', onUp);\n"
        "      document.removeEventListener('touchmove', onMove);\n"
        "      document.removeEventListener('touchend', onUp);\n"
        "    }\n"
        "    function onDown(e){\n"
        "      e.preventDefault(); e.stopPropagation();\n"
        "      var pt = e.touches ? e.touches[0] : e;\n"
        "      dragging=true; startX=pt.clientX; startW=th.getBoundingClientRect().width;\n"
        "      th.classList.add('lo-resizing');\n"
        "      document.addEventListener('mousemove', onMove);\n"
        "      document.addEventListener('mouseup', onUp);\n"
        "      document.addEventListener('touchmove', onMove, {passive:false});\n"
        "      document.addEventListener('touchend', onUp);\n"
        "    }\n"
        "    grip.addEventListener('mousedown', onDown);\n"
        "    grip.addEventListener('touchstart', onDown, {passive:false});\n"
        "    grip.addEventListener('click', function(e){ e.stopPropagation(); });\n"
        "  }\n"
        "  function init(){ var t=document.getElementById('loPostsTable'); if(t) initResize(t); }\n"
        "  if(document.readyState!=='loading') init();\n"
        "  else document.addEventListener('DOMContentLoaded', init);\n"
        "})();</script>"
    )


def render_linkedin_organic(
    *,
    client_slug: str,
    label: str,
    report: LinkedInOrganicReport,
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
            '<div class="lo-wrap">'
            f'{_filter_bar(label, window_days, subtitle="")}'
            f'<div class="lo-note">{_esc(report.error or "LinkedIn Organic is not configured for this client.")} '
            'Connect the LinkedIn Organic connector and run a sync to enable these reports.</div>'
            '</div>'
            f'{range_script}'
        )
        return _shell(client_slug, label, content, access_key, use_session, session_email, session_is_admin)

    has_any = bool(report.top_posts or report.follower_series or report.page_series)
    note = ""
    if not has_any:
        note = ('<div class="lo-note">No organic data has synced yet. Run a sync from the '
                'LinkedIn Organic connector, then refresh this page.</div>')

    # Averaged over every post in the window by the report service — summing the
    # Top-posts listing here would divide a 50-post sum by the full post count.
    avg_engagement = report.avg_engagement_rate

    follower_foot = (
        _period_delta(report.follower_gain, window_days)
        if report.follower_series
        else _sub("lifetime total")
    )
    card_list = [
        _card("Followers", _fmt_int(report.total_followers),
              spark=_follower_sparkline(report.follower_series), foot=follower_foot),
        _card("Posts", _fmt_int(report.post_count), foot=_sub("in selected period")),
        _card("Impressions", _fmt_int(report.total_impressions), foot=_sub("across posts")),
    ]
    # Reach only renders once a sync has captured unique-impression data.
    if report.total_unique_impressions:
        card_list.append(
            _card("Reach", _fmt_int(report.total_unique_impressions),
                  foot=_sub("unique impressions"))
        )
    card_list += [
        _card("Reactions", _fmt_int(report.total_likes), foot=_sub("likes on posts")),
        _card("Comments", _fmt_int(report.total_comments)),
        _card("Avg. engagement", _fmt_pct(avg_engagement), foot=_sub("per post")),
        _card("Page views", _fmt_int(report.total_page_views),
              foot=_sub(f'<strong>{_fmt_int(report.total_unique_visitors)}</strong> unique visitors')),
    ]
    cards = '<div class="cards">' + "".join(card_list) + '</div>'
    kpi_section = (
        '<section>'
        '<div class="sec-head"><h2><span class="lo-dot"></span>Performance</h2>'
        f'<span class="status">last {window_days} days</span></div>'
        f'{cards}</section>'
    )

    follower_chart = _chart_block("loFollowerChart", report.follower_series)
    page_chart = _chart_block("loPageChart", report.page_series)
    trends_section = (
        '<section>'
        '<div class="sec-head"><h2>Trends</h2>'
        f'<span class="status">daily, last {window_days} days</span></div>'
        '<div class="lo-two">'
        '<div class="lo-chart-box"><div class="lo-chart-lab"><h3>Follower gains</h3>'
        '<span>net new / day</span></div>'
        f'{follower_chart}</div>'
        '<div class="lo-chart-box"><div class="lo-chart-lab"><h3>Page views</h3>'
        '<span>organization page / day</span></div>'
        f'{page_chart}</div>'
        '</div></section>'
    )

    # Organization-wide engagement over time (only once the series has synced).
    engagement_section = ""
    if report.engagement_series:
        marks = _post_markers(report.post_markers, report.engagement_series)
        # The named posts are listed under the chart as well as pinned on it:
        # the pin says *when*, and at three posts a day the reader still needs to
        # be told *which one* without hunting for the right hover target.
        standout_html = ""
        if marks["standouts"]:
            items = "".join(
                f'<li><span class="lo-mk-num">{n}</span>'
                f'<span class="lo-mk-name" title="{_esc(p["label"])}">{_esc(p["label"])}</span>'
                f'<span class="lo-mk-imp">{_fmt_int(p["impressions"])} impressions</span></li>'
                for n, p in enumerate(marks["standouts"], start=1)
            )
            standout_html = (
                '<div class="lo-mk-head">'
                '<label class="lo-mk-toggle"><input type="checkbox" id="loMarkerToggle" checked> '
                'Show top posts on the chart</label>'
                f'<span class="lo-mk-count">{marks["posts"]} posts over '
                f'{marks["postDays"]} days</span></div>'
                f'<ol class="lo-mk-list">{items}</ol>'
            )
        engagement_section = (
            '<section><div class="sec-head"><h2>Engagement over time</h2>'
            f'<span class="status">impressions &amp; reach / day, last {window_days} days</span></div>'
            f'{standout_html}'
            '<div class="lo-chart" style="height:240px"><canvas id="loEngagementChart"></canvas></div>'
            '</section>'
        )

    demographics_section = _demographics_section(report)
    visitors_section = _visitors_section(report, window_days)

    if report.top_posts:
        show_reach = any(p.get("unique_impressions") for p in report.top_posts)
        rows = []
        for p in report.top_posts:
            meta = p["published_at"] or ""
            chip = f'<span class="lo-chip">{_esc(p["post_type"])}</span>' if p["post_type"] else ""
            reach_cell = (
                f'<td data-val="{p.get("unique_impressions", 0)}">{_fmt_int(p.get("unique_impressions", 0))}</td>'
                if show_reach else ""
            )
            rows.append(
                '<tr>'
                f'<td class="left" data-val="{_esc(p["title"])}"><span class="lo-post-title" title="{_esc(p["title"])}">{_esc(p["title"])}{chip}</span>'
                f'<span class="lo-post-meta">{_esc(meta)}</span></td>'
                f'<td data-val="{p["impressions"]}">{_fmt_int(p["impressions"])}</td>'
                f'{reach_cell}'
                f'<td data-val="{p["likes"]}">{_fmt_int(p["likes"])}{_reactions_note(p.get("reactions"))}</td>'
                f'<td data-val="{p["comments"]}">{_fmt_int(p["comments"])}</td>'
                f'<td data-val="{p["shares"]}">{_fmt_int(p["shares"])}</td>'
                f'<td data-val="{p["clicks"]}">{_fmt_int(p["clicks"])}</td>'
                f'<td data-val="{p["engagement_rate"]}">{_fmt_pct(p["engagement_rate"])}</td>'
                '</tr>'
            )
        reach_th = '<th data-sort="num">Reach</th>' if show_reach else ""
        posts_html = (
            '<div class="lo-table-wrap"><table id="loPostsTable" class="lo-table"><thead><tr>'
            '<th class="left" data-sort="text">Post<span class="lo-col-resizer" aria-hidden="true"></span></th>'
            '<th data-sort="num">Impressions</th>'
            f'{reach_th}'
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

    charts_script = _charts_script(
        report.follower_series, report.page_series, report.engagement_series,
        desktop_views=report.page_desktop_views, mobile_views=report.page_mobile_views,
        post_markers=report.post_markers,
    )
    sort_script = (_sort_script() + _resize_script()) if report.top_posts else ""
    content = f"""
      <div class="lo-wrap">
        {_filter_bar(label, window_days)}
        {note}
        {kpi_section}
        {trends_section}
        {engagement_section}
        {demographics_section}
        {visitors_section}
        {posts_section}
      </div>
      {charts_script}
      {sort_script}
      {range_script}
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
