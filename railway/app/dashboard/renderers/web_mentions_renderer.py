"""HTML renderer for the Web Mentions page (Google Alerts monitoring).

Same visual language as the other standalone report pages (Bluesky, LinkedIn
Organic): a sticky filter bar, compact KPI tiles with a top accent and a delta
foot, a Chart.js trend, and a plain sortable-looking table. Filters submit the
bar as a GET form, so every view of this page is a shareable URL and the back
button works — there is no client-side state to lose.

Two things the page says out loud rather than leaving a reader to assume:

*   **Share of mentions is not share of voice.** It is the split across the
    names this account happens to monitor. Add a competitor alert tomorrow and
    the percentages move. The panel says so, every time.
*   **Some dates are discovery dates.** Feeds routinely omit a publication date;
    those rows are marked rather than being quietly dated to the day we saw them.

The alert admin panel is rendered only for admins, and even for them the feed
URL appears only in its masked form — the full URL never reaches the browser.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import web_mentions_store as store
from dashboard.renderers.base_layout import render_client_shell_page
from dashboard.utils.formatting import esc as _esc
from web_mentions_service import RANGE_PRESETS, WebMentionsReport

_INK = "#2563eb"
_CATEGORY_COLORS: dict[str, str] = {
    "brand": "#2563eb",
    "competitor": "#f97316",
    "executive": "#7c5cff",
    "industry": "#0ea5e9",
    "other": "#94a3b8",
}

_EXTRA_CSS = """
.wm-wrap { width:100%; }
.wm-note { font-size:.88rem; color:var(--muted); background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin:0 0 16px; }
.wm-note strong { color:var(--text); }
.wm-note.wm-warn { border-color:#fcd9b6; background:#fff8f1; color:#8a5220; }
.wm-note.wm-ok { border-color:#c7e9d4; background:#f3fbf6; color:#25603f; }

/* Sticky full-bleed filter bar — mirrors the Bluesky `.bs-bar`. */
.wm-bar { position:sticky; top:0; z-index:40; background:#fff; border-bottom:1px solid var(--border); box-shadow:0 1px 0 rgba(16,33,67,.04), 0 6px 16px -12px rgba(16,33,67,.28); margin:-28px -32px 22px; }
.wm-bar-inner { display:flex; align-items:flex-end; justify-content:space-between; gap:10px 20px; flex-wrap:wrap; padding:12px 32px; }
.wm-bar-title { display:flex; flex-direction:column; gap:2px; min-width:0; }
.wm-bar-title h1 { margin:0; font-size:1.15rem; font-weight:800; color:var(--navy); letter-spacing:-.02em; }
.wm-bar-title p { margin:0; font-size:.8rem; color:var(--muted); overflow:hidden; text-overflow:ellipsis; }
.wm-filters { display:flex; align-items:flex-end; gap:10px; flex-wrap:wrap; }
.wm-field { display:flex; flex-direction:column; gap:4px; }
.wm-field label { font-size:.66rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }
.wm-field select { font:inherit; font-size:.82rem; font-weight:650; color:var(--navy); background-color:#fff; border:1px solid var(--border); border-radius:9px; padding:7px 30px 7px 12px; cursor:pointer; max-width:220px; -webkit-appearance:none; appearance:none; background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%2364748b' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'><path d='M4 6l4 4 4-4'/></svg>"); background-repeat:no-repeat; background-position:right 10px center; background-size:12px; transition:border-color .12s; }
.wm-field select:hover { border-color:var(--accent); }
.wm-field select:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.wm-clear { font-size:.78rem; font-weight:700; color:var(--accent); text-decoration:none; padding-bottom:9px; }
.wm-clear:hover { text-decoration:underline; }

@media (max-width:900px){ .wm-bar { top:52px; } }
@media (max-width:720px){ .wm-bar { margin:-18px -16px 18px; } .wm-bar-inner { padding:11px 16px; } }

/* Section panels */
.wm-wrap section { background:#fff; border:1px solid var(--border); border-radius:var(--radius); padding:18px 20px 20px; margin-bottom:16px; box-shadow:var(--shadow-sm); }
.wm-wrap .sec-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
.wm-wrap .sec-head h2 { margin:0; font-size:1rem; font-weight:750; color:var(--navy); display:flex; align-items:center; }
.wm-wrap .sec-head .status { margin:0; color:var(--muted); font-size:.78rem; text-align:right; flex-shrink:0; }
.wm-wrap .sec-head-actions { display:flex; align-items:center; gap:12px; flex-shrink:0; }
.wm-dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:#2563eb; margin-right:8px; }

/* KPI cards */
.wm-wrap .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }
.wm-wrap .card { border:1px solid var(--line-soft,#eff3f8); border-top:3px solid var(--accent); border-radius:var(--radius-sm,9px); padding:14px 15px 15px; background:#fff; }
.wm-wrap .card-title { color:var(--muted); font-size:.66rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }
.wm-wrap .card-value { margin-top:7px; font-size:1.55rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.wm-wrap .card-foot { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:2px 8px; margin-top:9px; min-height:20px; }
.wm-sub { color:var(--muted); font-size:.74rem; }
.wm-delta { font-size:.74rem; font-weight:700; white-space:nowrap; }
.wm-delta.up { color:var(--ok); }
.wm-delta.down { color:var(--err); }
.wm-delta.flat { color:var(--muted); font-weight:600; }

/* Trend */
.wm-chart { position:relative; height:210px; }
.wm-chart canvas { display:block; width:100% !important; }
.wm-empty { font-size:.85rem; color:var(--muted); padding:26px 0; text-align:center; }

/* Share of mentions */
.wm-share-row { display:grid; grid-template-columns:minmax(120px,190px) 1fr 92px; align-items:center; gap:12px; margin-bottom:9px; }
.wm-share-name { font-size:.85rem; font-weight:650; color:var(--navy); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.wm-share-track { background:#eef2f8; border-radius:999px; height:9px; overflow:hidden; }
.wm-share-fill { display:block; height:100%; border-radius:999px; }
.wm-share-val { font-size:.82rem; font-weight:750; color:var(--navy); text-align:right; font-variant-numeric:tabular-nums; }
.wm-share-val span { color:var(--muted); font-weight:600; font-size:.74rem; }

/* Mentions table */
.wm-table-wrap { overflow:auto; border:1px solid var(--line-soft,#eff3f8); border-radius:var(--radius-sm,9px); }
.wm-table { border-collapse:collapse; width:100%; min-width:720px; font-size:.85rem; }
.wm-table th, .wm-table td { padding:10px 13px; border-bottom:1px solid var(--line-soft,#eff3f8); text-align:left; vertical-align:top; }
.wm-table tbody tr:last-child td { border-bottom:0; }
.wm-table tbody tr:hover td { background:#f7faff; }
.wm-table th { background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; white-space:nowrap; }
.wm-td-date { white-space:nowrap; color:var(--muted); font-variant-numeric:tabular-nums; }
.wm-headline { color:var(--navy); font-weight:650; text-decoration:none; }
.wm-headline:hover { color:var(--accent); text-decoration:underline; }
.wm-snippet { display:block; margin-top:3px; color:var(--muted); font-size:.78rem; font-weight:400; }
.wm-tag { display:inline-block; padding:2px 8px; border-radius:999px; font-size:.68rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; color:#fff; }
.wm-est { font-size:.66rem; color:var(--muted); display:block; }

/* Admin: alert management */
.wm-admin { border-color:#dbe6f5 !important; }
.wm-admin-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px 12px; align-items:end; }
.wm-admin-grid label { display:flex; flex-direction:column; gap:4px; font-size:.66rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }
.wm-admin-grid input, .wm-admin-grid select { font:inherit; font-size:.85rem; color:var(--navy); background:#fff; border:1px solid var(--border); border-radius:9px; padding:8px 11px; }
.wm-btn { font:inherit; font-size:.82rem; font-weight:750; border-radius:9px; padding:9px 15px; border:1px solid var(--accent); background:var(--accent); color:#fff; cursor:pointer; }
.wm-btn:hover { filter:brightness(1.06); }
.wm-btn.ghost { background:#fff; color:var(--navy); border-color:var(--border); }
.wm-btn.ghost:hover { border-color:var(--accent); color:var(--accent); filter:none; }
.wm-btn.link { background:none; border:0; color:var(--accent); padding:0; font-size:.78rem; font-weight:700; cursor:pointer; }
.wm-btn.link:hover { text-decoration:underline; }
.wm-btn.link.danger { color:var(--err,#c0392b); }
.wm-feed { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.72rem; color:var(--muted); }
.wm-status-ok { color:var(--ok,#1f8b4c); font-weight:700; font-size:.76rem; }
.wm-status-err { color:var(--err,#c0392b); font-weight:700; font-size:.76rem; }
.wm-status-idle { color:var(--muted); font-size:.76rem; }
.wm-inactive td { opacity:.6; }
.wm-actions { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
"""


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_day(value: date | None) -> str:
    if not isinstance(value, date):
        return "—"
    return f"{value:%b} {value.day}"


def _fmt_stamp(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return "never"
    return value.strftime("%b %d, %Y %H:%M UTC")


def _delta(current: int, prior: int, *, window_days: int) -> str:
    """The vs-previous-period badge. Says nothing when there is nothing to compare."""
    if not prior:
        if not current:
            return ""
        return f'<span class="wm-delta flat">no prior {window_days}-day data</span>'
    change = 100.0 * (current - prior) / prior
    if abs(change) < 0.5:
        return '<span class="wm-delta flat">flat vs prev.</span>'
    cls = "up" if change > 0 else "down"
    arrow = "▲" if change > 0 else "▼"
    return f'<span class="wm-delta {cls}">{arrow} {abs(change):.0f}% vs prev.</span>'


def _card(title: str, value: str, *, delta: str = "", sub: str = "") -> str:
    sub_html = f'<span class="wm-sub">{sub}</span>' if sub else ""
    return (
        '<div class="card">'
        f'<div class="card-title">{_esc(title)}</div>'
        f'<div class="card-value">{value}</div>'
        f'<div class="card-foot">{delta}{sub_html}</div>'
        "</div>"
    )


def _category_tag(category: str) -> str:
    color = _CATEGORY_COLORS.get(category, _CATEGORY_COLORS["other"])
    label = store.CATEGORY_LABELS.get(category, category.title())
    return f'<span class="wm-tag" style="background:{color}">{_esc(label)}</span>'


def _page_url(client_slug: str, **params: Any) -> str:
    clean = {k: str(v) for k, v in params.items() if v not in (None, "", 0)}
    base = f"/dashboard/{client_slug}/web-mentions"
    return f"{base}?{urlencode(clean)}" if clean else base


# ──────────────────────────────────────────────────────────────────────────────
# Sections
# ──────────────────────────────────────────────────────────────────────────────

def _filter_bar(report: WebMentionsReport) -> str:
    """The one filter bar for the page: range, alert, category, source.

    A GET form, so the selected view is the URL. It doubles as the page header
    rather than adding a second row of chrome above it.
    """
    range_opts = "".join(
        f'<option value="{value}"{" selected" if days == report.range_days else ""}>'
        f"{_esc(label)}</option>"
        for value, days, label in RANGE_PRESETS
    )
    alert_opts = ['<option value="">All alerts</option>']
    for alert in report.alerts:
        selected = " selected" if report.alert_id == alert.id else ""
        suffix = "" if alert.active else " (inactive)"
        alert_opts.append(
            f'<option value="{alert.id}"{selected}>{_esc(alert.name)}{suffix}</option>'
        )
    cat_opts = ['<option value="">All categories</option>']
    for key in store.CATEGORIES:
        selected = " selected" if report.category == key else ""
        cat_opts.append(
            f'<option value="{key}"{selected}>{_esc(store.CATEGORY_LABELS[key])}</option>'
        )
    source_opts = ['<option value="">All sources</option>']
    for name in report.source_options:
        selected = " selected" if report.source == name else ""
        source_opts.append(f'<option value="{_esc(name)}"{selected}>{_esc(name)}</option>')

    filtered = bool(report.alert_id or report.category or report.source)
    clear = (
        f'<a class="wm-clear" href="{_esc(_page_url(report.client_slug, range=report.range_days))}">'
        "Clear filters</a>"
        if filtered
        else ""
    )
    window = ""
    if report.start and report.end:
        window = f"{_fmt_day(report.start)} – {_fmt_day(report.end)}"
    return f"""
      <div class="wm-bar">
        <div class="wm-bar-inner">
          <div class="wm-bar-title">
            <h1>Web Mentions</h1>
            <p>{_esc(report.label)}{" · " + _esc(window) if window else ""}</p>
          </div>
          <form class="wm-filters" method="get" action="/dashboard/{_esc(report.client_slug)}/web-mentions">
            <div class="wm-field"><label for="wmRange">Date range</label>
              <select id="wmRange" name="range" onchange="this.form.submit()">{range_opts}</select></div>
            <div class="wm-field"><label for="wmAlert">Alert</label>
              <select id="wmAlert" name="alert" onchange="this.form.submit()">{"".join(alert_opts)}</select></div>
            <div class="wm-field"><label for="wmCategory">Category</label>
              <select id="wmCategory" name="category" onchange="this.form.submit()">{"".join(cat_opts)}</select></div>
            <div class="wm-field"><label for="wmSource">Source</label>
              <select id="wmSource" name="source" onchange="this.form.submit()">{"".join(source_opts)}</select></div>
            <noscript><button type="submit" class="wm-btn">Apply</button></noscript>
            {clear}
          </form>
        </div>
      </div>
    """


def _summary_section(report: WebMentionsReport) -> str:
    days = report.range_days
    cards = "".join([
        _card("Total mentions", _fmt_int(report.total),
              delta=_delta(report.total, report.prev_total, window_days=days),
              sub=f"prev. {_fmt_int(report.prev_total)}"),
        _card("Brand mentions", _fmt_int(report.brand),
              sub="alerts tagged Brand"),
        _card("Competitor mentions", _fmt_int(report.competitor),
              sub="alerts tagged Competitor"),
        _card("Unique sources", _fmt_int(report.sources),
              sub="publications in range"),
    ])
    return (
        '<section><div class="sec-head">'
        '<h2><span class="wm-dot"></span>Coverage</h2>'
        f'<span class="status">last {days} days &middot; vs previous {days} days</span></div>'
        f'<div class="cards">{cards}</div></section>'
    )


def _daily_series(report: WebMentionsReport) -> tuple[list[str], list[int]]:
    """Continuous day-by-day series — a gap day is a zero, not a missing point."""
    if not (report.start and report.end):
        return [], []
    counts = {row["date"]: int(row["count"]) for row in report.daily}
    labels: list[str] = []
    values: list[int] = []
    day = report.start
    while day <= report.end:
        labels.append(day.isoformat())
        values.append(counts.get(day, 0))
        day += timedelta(days=1)
    return labels, values


def _trend_section(report: WebMentionsReport) -> str:
    labels, values = _daily_series(report)
    if not any(values):
        body = '<div class="wm-empty">No mentions in this range yet.</div>'
    else:
        body = '<div class="wm-chart"><canvas id="wmTrendChart"></canvas></div>'
    return (
        '<section><div class="sec-head"><h2>Mentions over time</h2>'
        f'<span class="status">by publication date</span></div>{body}</section>'
    )


def _trend_script(report: WebMentionsReport) -> str:
    labels, values = _daily_series(report)
    if not any(values):
        return ""
    payload = json.dumps({"labels": labels, "values": values, "color": _INK}).replace("<", "\\u003c")
    return (
        '<script src="/static/vendor/chart.umd.min.js"></script>'
        "<script>(function(){\n"
        f"  var WM = {payload};\n"
        "  var el=document.getElementById('wmTrendChart');\n"
        "  if(!el || !window.Chart || !WM.labels.length) return;\n"
        "  new Chart(el, {type:'bar',\n"
        "    data:{labels:WM.labels, datasets:[{data:WM.values, backgroundColor:WM.color,\n"
        "      borderRadius:3, maxBarThickness:22}]},\n"
        "    options:{responsive:true, maintainAspectRatio:false, animation:false,\n"
        "      plugins:{legend:{display:false},\n"
        "        tooltip:{displayColors:false, padding:8,\n"
        "          callbacks:{label:function(c){return c.formattedValue + ' mention' + (c.parsed.y===1?'':'s');}}}},\n"
        "      scales:{x:{grid:{display:false}, ticks:{maxRotation:0, autoSkip:true,\n"
        "          maxTicksLimit:10, font:{size:10}, color:'#94a3b8'}},\n"
        "        y:{beginAtZero:true, grid:{color:'rgba(148,163,184,.15)'},\n"
        "          border:{display:false},\n"
        "          ticks:{precision:0, maxTicksLimit:5, font:{size:10}, color:'#94a3b8'}}}}\n"
        "  });\n"
        "})();</script>"
    )


def _share_section(report: WebMentionsReport) -> str:
    """Share of *monitored* mentions — never presented as market share."""
    if len(report.share) < 2:
        return ""
    rows = []
    for row in report.share:
        color = _CATEGORY_COLORS.get(row.category, _CATEGORY_COLORS["other"])
        rows.append(
            '<div class="wm-share-row">'
            f'<div class="wm-share-name" title="{_esc(row.subject)}">{_esc(row.subject)}</div>'
            '<div class="wm-share-track">'
            f'<span class="wm-share-fill" style="width:{row.pct:.1f}%;background:{color}"></span></div>'
            f'<div class="wm-share-val">{row.pct:.0f}% <span>({_fmt_int(row.count)})</span></div>'
            "</div>"
        )
    return (
        '<section><div class="sec-head"><h2>Share of mentions</h2>'
        f'<span class="status">{_fmt_int(report.share_total)} monitored mentions '
        f"&middot; last {report.range_days} days</span></div>"
        f'{"".join(rows)}'
        '<p class="wm-note" style="margin:14px 0 0">This is the split of the web mentions '
        '<strong>this account monitors</strong> — the brand and competitor alerts configured '
        'below, over the selected range. It is not market share, and not a comprehensive '
        'media share of voice: adding or removing an alert changes these percentages, and '
        'Google Alerts sees only part of the web.</p>'
        "</section>"
    )


def _mentions_section(report: WebMentionsReport) -> str:
    if not report.mentions:
        body = (
            '<div class="wm-empty">No mentions match these filters. '
            "Try a wider date range.</div>"
        )
    else:
        rows = []
        for m in report.mentions:
            est = '<span class="wm-est">discovered</span>' if m.published_estimated else ""
            snippet = (
                f'<span class="wm-snippet">{_esc(m.snippet[:220])}</span>' if m.snippet else ""
            )
            headline = _esc(m.title or m.url or "(untitled)")
            link = (
                f'<a class="wm-headline" href="{_esc(m.url)}" target="_blank" '
                f'rel="noopener noreferrer nofollow">{headline}</a>'
                if m.url
                else f'<span class="wm-headline">{headline}</span>'
            )
            rows.append(
                "<tr>"
                f'<td class="wm-td-date">{_esc(m.mention_date.isoformat() if m.mention_date else "—")}{est}</td>'
                f"<td>{link}{snippet}</td>"
                f"<td>{_esc(m.source or '—')}</td>"
                f"<td>{_esc(m.alert_name)}<br>{_category_tag(m.category)}</td>"
                "</tr>"
            )
        more = (
            f'<p class="wm-sub" style="margin:10px 0 0">Showing the most recent '
            f"{len(report.mentions)} mentions. Narrow the date range or filters to see more.</p>"
            if report.truncated
            else ""
        )
        body = (
            '<div class="wm-table-wrap"><table class="wm-table">'
            "<thead><tr><th>Date</th><th>Headline</th><th>Publication</th>"
            "<th>Alert</th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table></div>{more}'
        )
    return (
        '<section><div class="sec-head"><h2>Recent mentions</h2>'
        f'<span class="status">{_fmt_int(report.total)} in range</span></div>{body}</section>'
    )


# ──────────────────────────────────────────────────────────────────────────────
# Admin panel
# ──────────────────────────────────────────────────────────────────────────────

def _alert_status_html(alert: store.Alert) -> str:
    if alert.last_error_message:
        return (
            f'<span class="wm-status-err">Failing</span>'
            f'<br><span class="wm-sub">{_esc(alert.last_error_message[:160])}</span>'
        )
    if alert.last_success_at:
        return (
            f'<span class="wm-status-ok">Synced</span>'
            f'<br><span class="wm-sub">{_esc(_fmt_stamp(alert.last_success_at))}</span>'
        )
    return '<span class="wm-status-idle">Not synced yet</span>'


def _admin_section(report: WebMentionsReport) -> str:
    action = f"/dashboard/{_esc(report.client_slug)}/web-mentions"
    cat_opts = "".join(
        f'<option value="{key}">{_esc(store.CATEGORY_LABELS[key])}</option>'
        for key in store.CATEGORIES
    )

    rows = []
    for alert in report.alerts:
        count = report.alert_counts.get(alert.id, 0)
        row_cat_opts = "".join(
            f'<option value="{key}"{" selected" if key == alert.category else ""}>'
            f"{_esc(store.CATEGORY_LABELS[key])}</option>"
            for key in store.CATEGORIES
        )
        toggle_label = "Deactivate" if alert.active else "Activate"
        delete_btn = (
            ""
            if count
            else (
                f'<form method="post" action="{action}/alerts/{alert.id}/delete" '
                'onsubmit="return confirm(\'Delete this alert? It has not collected '
                'any mentions.\')" style="display:inline">'
                '<button type="submit" class="wm-btn link danger">Delete</button></form>'
            )
        )
        row_open = "<tr>" if alert.active else '<tr class="wm-inactive">'
        rows.append(
            row_open +
            f'<td><form method="post" action="{action}/alerts/{alert.id}" class="wm-actions">'
            f'<input type="text" name="name" value="{_esc(alert.name)}" '
            'style="font:inherit;font-size:.85rem;padding:6px 9px;border:1px solid var(--border);'
            'border-radius:8px;max-width:190px" required>'
            f'<select name="category" style="font:inherit;font-size:.8rem;padding:6px 9px;'
            f'border:1px solid var(--border);border-radius:8px">{row_cat_opts}</select>'
            '<button type="submit" class="wm-btn link">Save</button></form></td>'
            f'<td><span class="wm-feed">{_esc(alert.masked_feed_url)}</span></td>'
            f"<td>{_alert_status_html(alert)}</td>"
            f'<td style="text-align:right">{_fmt_int(count)}</td>'
            '<td><div class="wm-actions">'
            f'<form method="post" action="{action}/alerts/{alert.id}">'
            f'<input type="hidden" name="active" value="{"0" if alert.active else "1"}">'
            f'<button type="submit" class="wm-btn link">{toggle_label}</button></form>'
            f"{delete_btn}</div></td>"
            "</tr>"
        )

    table = (
        '<div class="wm-table-wrap" style="margin-bottom:16px"><table class="wm-table">'
        "<thead><tr><th>Alert</th><th>Feed</th><th>Last successful sync</th>"
        '<th style="text-align:right">Collected</th><th>Actions</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        if rows
        else '<p class="wm-empty">No alerts yet — add the first one below.</p>'
    )

    # Its own form, and it must stay OUT of the add-alert form below: nested
    # forms are invalid HTML, and a browser drops the inner tag rather than
    # nesting them — which silently turns this into a second submit button for
    # whichever form encloses it.
    sync_button = (
        f'<form method="post" action="{action}/sync">'
        '<button type="submit" class="wm-btn ghost">Sync now</button></form>'
        if report.active_alerts
        else ""
    )

    return f"""
      <section class="wm-admin">
        <div class="sec-head"><h2>Monitored alerts</h2>
          <div class="sec-head-actions">
            <span class="status">admins only &middot; {_fmt_int(len(report.alerts))} configured</span>
            {sync_button}
          </div>
        </div>
        {table}
        <form method="post" action="{action}/alerts" class="wm-admin-grid">
          <label>Alert name
            <input type="text" name="name" placeholder="EOS Worldwide — brand" required maxlength="200"></label>
          <label>Category
            <select name="category">{cat_opts}</select></label>
          <label>Share-of-mentions name <span style="text-transform:none;font-weight:600">(optional)</span>
            <input type="text" name="subject" placeholder="defaults to the alert name" maxlength="200"></label>
          <label style="grid-column:1/-1">Google Alerts RSS feed URL
            <input type="url" name="feed_url" placeholder="https://www.google.com/alerts/feeds/…" required></label>
          <div class="wm-actions">
            <button type="submit" class="wm-btn">Add alert</button>
          </div>
        </form>
        <p class="wm-note" style="margin:16px 0 0">In Google Alerts, open an alert's pencil
          icon, set <strong>Deliver to</strong> to <strong>RSS feed</strong>, save, then copy the
          feed link from the RSS icon. Feeds are polled on the daily schedule; the URL is stored
          encrypted and never sent to a browser. Deactivating an alert stops polling and
          <strong>keeps every mention it already found</strong>.</p>
      </section>
    """


# ──────────────────────────────────────────────────────────────────────────────
# Page
# ──────────────────────────────────────────────────────────────────────────────

def render_web_mentions_page(
    *,
    client_slug: str,
    label: str,
    report: WebMentionsReport,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash: str = "",
    flash_error: str = "",
) -> str:
    notices: list[str] = []
    if flash_error:
        notices.append(f'<div class="wm-note wm-warn">{_esc(flash_error)}</div>')
    elif flash:
        notices.append(f'<div class="wm-note wm-ok">{_esc(flash)}</div>')

    if not report.configured:
        notices.append(
            '<div class="wm-note">No Google Alerts are being monitored for this account yet.'
            + (
                " Add one below to start collecting web mentions."
                if session_is_admin
                else " Ask your Sagefrog team to set one up."
            )
            + "</div>"
        )
    elif report.never_synced:
        notices.append(
            '<div class="wm-note">Alerts are configured but have not been polled yet. '
            "The first results land on the next scheduled sync"
            + (' — or use "Sync now" below.' if session_is_admin else ".")
            + "</div>"
        )

    failing = report.failing_alerts
    if failing and session_is_admin:
        names = ", ".join(_esc(a.name) for a in failing[:4])
        notices.append(
            f'<div class="wm-note wm-warn"><strong>{len(failing)} feed'
            f'{"" if len(failing) == 1 else "s"} failing:</strong> {names}. '
            "Mentions already collected are unaffected — see the alert table for the error.</div>"
        )

    body: list[str] = [_filter_bar(report), "".join(notices)]
    if report.configured:
        body.append(_summary_section(report))
        body.append(_trend_section(report))
        body.append(_share_section(report))
        body.append(_mentions_section(report))
    if session_is_admin:
        body.append(_admin_section(report))

    synced = report.last_checked_at
    if report.configured:
        body.append(
            '<div class="wm-note">Mentions come from the Google Alerts RSS feeds configured for '
            "this account, so coverage is whatever Google Alerts indexes — not every mention on "
            "the web. Dates are the publication date when the feed provides one, and the date we "
            "discovered the result when it does not (marked <em>discovered</em>). Last successful "
            f"feed sync: {_esc(_fmt_stamp(synced))}.</div>"
        )

    content = (
        f'<div class="wm-wrap">{"".join(body)}</div>{_trend_script(report)}'
        if report.configured
        else f'<div class="wm-wrap">{"".join(body)}</div>'
    )
    return render_client_shell_page(
        client_slug=client_slug,
        label=label,
        active_nav="web_mentions",
        page_title="Web Mentions",
        page_subtitle="",
        content_html=content,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        extra_css=_EXTRA_CSS,
        # Renders the Admin tab strip for admins only, so the page is reachable
        # (to add the first alert) before any alert exists to light up the sidebar.
        admin_tab="web-mentions",
    )
