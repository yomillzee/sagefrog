"""BigQuery-mart client dashboard — paid media (Overview / Explorer) + Website Analytics tabs.

THIS IS THE SINGLE MASTER DASHBOARD TEMPLATE. Every client's dashboard — for every
slug — renders through render_bigquery_dashboard_page() below. There are no
client-specific dashboard renderers: edits here apply to all dashboards app-wide.
Per-client differences (segment filters, mart destination, theming, feature
flags) are driven by configuration (client_dashboard_config), never by branching
on a client's name or slug. Keep it that way.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlencode

from dashboard.assets import dashboard_css_url, dashboard_js_url

from dashboard.renderers.base_layout import (
    dashboard_topbar_js,
    favicon_head_html,
    dashboard_sidebar_view_nav_html,
    render_sidebar,
    platform_nav_flags,
    site_footer_html,
)
from dashboard.renderers import google_business_renderer, pagespeed_renderer
# Script-safe JSON for values embedded in the page's inline <script>: escapes
# < > & so a stored config value (keyword lists, watchlist, event names) can't
# close the script tag and run as markup.
from dashboard.utils.formatting import json_for_html_script as _json_script


# ── HubSpot MQL tracker formatting helpers ──────────────────────────────────
# Self-contained here so this renderer has no dependency on the (removed) Penn
# renderer. Used by hubspot_mql_section_html below.

def _mql_money_compact(n) -> str:
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


def _mql_mom_delta(mqls_by_month) -> str:
    """Month-over-month MQL change for the two most recent months with data."""
    parsed: list[tuple[str, int]] = []
    for r in mqls_by_month or []:
        month = str(r.get("month") or "")
        if not month:
            continue
        try:
            parsed.append((month, int(r.get("contacts") or 0)))
        except (TypeError, ValueError):
            continue
    if len(parsed) < 2:
        return ""
    parsed.sort()
    prev, curr = parsed[-2][1], parsed[-1][1]
    if prev == 0:
        return "" if curr == 0 else '<span class="mql-delta up">New this month</span>'
    change = (curr - prev) / prev * 100.0
    if abs(change) < 0.5:
        return '<span class="mql-delta flat">Flat MoM</span>'
    if change > 0:
        return f'<span class="mql-delta up">▲ {change:.0f}% MoM</span>'
    return f'<span class="mql-delta down">▼ {abs(change):.0f}% MoM</span>'


def _mql_sparkline(mqls_by_month, accent: str = "#ff7a59") -> str:
    """Tiny inline-SVG trend of monthly MQL volume (last 12 months).

    Returns "" when there's fewer than two months of data — nothing to plot.
    """
    series: list[int] = []
    for r in sorted(mqls_by_month or [], key=lambda x: str(x.get("month") or "")):
        if not str(r.get("month") or ""):
            continue
        try:
            series.append(int(r.get("contacts") or 0))
        except (TypeError, ValueError):
            continue
    series = series[-12:]
    if len(series) < 2:
        return ""

    w, h, pad = 132.0, 34.0, 3.0
    peak = max(series) or 1
    n = len(series)
    step = (w - 2 * pad) / (n - 1)
    pts = [
        (pad + i * step, h - pad - (v / peak) * (h - 2 * pad))
        for i, v in enumerate(series)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (
        f"M {pts[0][0]:.1f} {h - pad:.1f} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y in pts)
        + f" L {pts[-1][0]:.1f} {h - pad:.1f} Z"
    )
    lx, ly = pts[-1]
    return (
        f'<svg class="mql-spark" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
        f'role="img" aria-label="Monthly MQL trend">'
        f'<path d="{area}" fill="{accent}" fill-opacity="0.12"/>'
        f'<polyline points="{line}" fill="none" stroke="{accent}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" fill="{accent}"/>'
        f'</svg>'
    )


def _li_follower_sparkline(follower_series, accent: str = "#0A66C2") -> str:
    """Tiny inline-SVG follower-growth curve for the Overview card.

    Reconstructs the absolute follower count across the window from the daily
    ``total_follower_gain`` values, anchored to the most recent lifetime
    ``total_followers`` snapshot when one is present (otherwise it plots the
    running cumulative gain from zero). Unlike the MQL sparkline this scales
    between the series min and max — a follower count rarely starts near zero, so
    a 0-based axis would flatten the whole line against the top.

    Returns "" when there are fewer than two days to plot.
    """
    rows = [r for r in (follower_series or []) if str(r.get("metric_date") or "")]
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
        # Walk backward from the known current total using each day's gain.
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
        f'<svg class="mql-spark" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
        f'role="img" aria-label="Follower growth">'
        f'<path d="{area}" fill="{accent}" fill-opacity="0.12"/>'
        f'<polyline points="{line}" fill="none" stroke="{accent}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" fill="{accent}"/>'
        f'</svg>'
    )


# The LinkedIn wordmark glyph, used as the "LinkedIn Followers" panel's heading
# mark. It replaces the generic coloured dot the other Overview panels use: the
# dot only said "this panel has a brand colour", where the glyph says which
# network the numbers came from.
_LI_GLYPH_SVG = (
    '<svg class="li-glyph" viewBox="0 0 24 24" fill="#0A66C2" aria-hidden="true">'
    '<path d="M20.45 20.45h-3.55v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 '
    '2.94v5.67H9.35V9h3.42v1.56h.04c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 '
    '5.45v6.29zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zm1.78 '
    '13.02H3.55V9h3.57v11.45zM22.22 0H1.77C.79 0 0 .78 0 1.73v20.54C0 23.23.79 24 '
    '1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .78 23.2 0 22.22 0z"/></svg>'
)


def _li_weekly_follower_bars(follower_series, *, weeks: int = 12,
                             accent: str = "#0A66C2") -> str:
    """Weekly net-follower bars for the Overview "Net new followers" card.

    A single number left that card nearly empty, so the same daily series that
    feeds the sparkline is bucketed into ISO weeks and drawn as a small bar
    chart: it answers "is the gain steady or was it one good week?" without a
    second query. Bars hang off a zero baseline, so a losing week reads as a bar
    below the line rather than a shorter bar above it.

    Returns "" with fewer than two plottable weeks, so the caller keeps the
    plain number card.
    """
    from datetime import date as _date

    buckets: dict[tuple[int, int], int] = {}
    for r in (follower_series or []):
        day = str(r.get("metric_date") or "")
        try:
            iso = _date.fromisoformat(day[:10]).isocalendar()
        except ValueError:
            continue
        key = (iso[0], iso[1])
        buckets[key] = buckets.get(key, 0) + int(r.get("total_follower_gain") or 0)

    if len(buckets) < 2:
        return ""
    values = [buckets[k] for k in sorted(buckets)][-weeks:]
    if len(values) < 2:
        return ""

    w, h, pad = 132.0, 34.0, 2.0
    hi, lo = max(values + [0]), min(values + [0])
    span = (hi - lo) or 1
    zero = pad + (hi / span) * (h - 2 * pad)
    n = len(values)
    slot = (w - 2 * pad) / n
    bw = max(2.0, slot - 2.0)

    bars = []
    for i, v in enumerate(values):
        x = pad + i * slot + (slot - bw) / 2
        y = pad + ((hi - max(v, 0)) / span) * (h - 2 * pad)
        height = max(1.5, abs(v) / span * (h - 2 * pad))
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{height:.1f}" '
            f'rx="1" fill="{accent}" fill-opacity="{0.85 if v >= 0 else 0.35:.2f}"/>'
        )
    return (
        f'<svg class="mql-spark" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
        f'role="img" aria-label="Net new followers by week">'
        f'{"".join(bars)}'
        f'<line x1="{pad:.1f}" y1="{zero:.1f}" x2="{w - pad:.1f}" y2="{zero:.1f}" '
        f'stroke="{accent}" stroke-opacity="0.28" stroke-width="1"/>'
        f'</svg>'
    )


def _li_section_bars(sections, *, top: int = 4) -> str:
    """Ranked "views by page section" bars for the Overview LinkedIn card.

    ``sections`` is the report's ``page_sections`` -- ``[{label, views}]`` in
    fixed section order. Here they're ranked highest-first (it's a leaderboard)
    and capped at ``top`` rows so the card stays the height of the metric cards
    beside it. Bar widths are relative to the top section; the muted percentage
    is share of *all* section views, including any rows past the cut. Returns ""
    when nothing has views, so the caller can drop the column entirely.
    """
    from dashboard.utils.formatting import esc as _esc, fmt_int as _int

    rows = [
        (str(s.get("label") or "Other"), int(s.get("views") or 0))
        for s in (sections or [])
        if int(s.get("views") or 0) > 0
    ]
    if not rows:
        return ""
    total = sum(v for _, v in rows)
    rows.sort(key=lambda r: r[1], reverse=True)
    peak = rows[0][1] or 1

    bars = []
    for label, views in rows[:top]:
        width = max(3.0, views / peak * 100.0)
        share = views / total * 100.0
        bars.append(
            '<div class="li-bar-row">'
            f'<span class="li-bar-label">{_esc(label)}</span>'
            f'<span class="li-bar-val">{_int(views)}<span>{share:.0f}%</span></span>'
            '<span class="li-bar-track">'
            f'<span class="li-bar-fill" style="width:{width:.1f}%"></span></span>'
            '</div>'
        )
    return f'<div class="li-bars">{"".join(bars)}</div>'


def _api_url(path: str, *, access_key: str | None) -> str:
    if not access_key:
        return path
    return f"{path}?{urlencode({'key': access_key})}"


# Overview "home" cards an admin can reorder or hide in edit mode. Keys are
# stable and used both as the layout keys stored per-client in
# ``client_dashboard_config.card_layouts`` and as the card titles shown in the
# edit bar. A card only renders when its data/connector is present, so a stored
# key that isn't currently visible is simply ignored (the layout persists and
# re-applies once that card comes back).
OVERVIEW_PINNABLE_CARDS: dict[str, str] = {
    "mql": "HubSpot MQL Tracker",
    "paid": "Paid summary",
    "website": "Website analytics",
    "linkedin_organic": "LinkedIn Followers",
    "ai_traffic": "AI traffic",
    "gsc": "Search Console",
    "site_performance": "Site performance",
}


# Campaign Explorer panels, same deal: stable keys stored under the "explorer"
# entry of ``client_dashboard_config.card_layouts``, titles shown on each panel's
# edit bar. The budget tracker is listed here so it can be ordered and hidden
# with the rest, but its visibility has its own long-standing store (the
# per-client "Show on Explorer" setting, also on the settings page) — see
# ``show_budget`` below and the Hide/Show handling in the edit JS.
EXPLORER_LAYOUT_CARDS: dict[str, str] = {
    "explorer": "Campaign explorer",
    "keywords": "Keyword Performance",
    "gdemo": "Google Ads demographics",
    "lidemo": "LinkedIn audience",
    "budget": "Budget tracking",
}


# Search Console panels, stored under the "gsc" entry of
# ``client_dashboard_config.card_layouts``. Same rules as the two tabs above:
# stable keys, titles shown on each panel's edit bar, and a key that isn't
# rendered this time (the SEMrush panel without that connector) is simply
# ignored until it comes back.
GSC_LAYOUT_CARDS: dict[str, str] = {
    "semrush": "Organic Search Intelligence",
    "kpis": "Search Console summary",
    "tables": "Top queries & pages",
    "watchlist": "Keyword watchlist",
    "keywords": "Branded & Target Keywords",
}


# Drag-handle and hide/show glyphs for the panel edit-mode controls.
_OV_DRAG_ICON = (
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" '
    'aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/>'
    '<circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/>'
    '<circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>'
)


def _ov_unit_wrapper(card_key: str, title: str, panel_html: str, *, hidden: bool) -> str:
    """Wrap one editable panel (Overview or Campaign Explorer) with its
    admin-only edit-mode controls.

    The wrapper carries the stable ``data-ov-card`` key used for reorder/hide
    persistence. The edit bar (drag handle, card name, Hide/Show toggle) is
    display:none until the pane enters edit mode under ``.is-admin``; a hidden
    card is greyed rather than removed while editing so an admin can show it back
    (clients never receive hidden cards in their HTML at all — see the render
    loops). The panel HTML itself is untouched."""
    from dashboard.utils.formatting import esc as _esc

    cls = "ov-unit ov-unit--hidden" if hidden else "ov-unit"
    toggle_label = "Show" if hidden else "Hide"
    return (
        f'<div class="{cls}" data-ov-card="{_esc(card_key)}">'
        f'<div class="ov-edit-bar">'
        f'<span class="ov-drag" title="Drag to reorder" draggable="true">{_OV_DRAG_ICON}</span>'
        f'<span class="ov-card-name">{_esc(title)}</span>'
        f'<button type="button" class="ov-hide-toggle" data-ov-card="{_esc(card_key)}" '
        f'aria-pressed="{"true" if hidden else "false"}">{toggle_label}</button>'
        f'</div>{panel_html}</div>'
    )


def _render_editable_panels(
    units: list[tuple[str, str]],
    layout: dict[str, list[str]],
    *,
    titles: dict[str, str],
    is_admin: bool,
    forced_hidden: frozenset[str] = frozenset(),
) -> str:
    """Order, hide and wrap one tab's panels per its admin-authored layout.

    A stored ``order`` wins: panels are sorted by it, and any panel not named in
    it keeps its natural position after the ordered ones. Keys that aren't real
    panels this render are ignored — the layout persists and re-applies once that
    panel comes back. Panels named in ``hidden`` (plus any in ``forced_hidden``,
    which is how the Explorer's budget tracker keeps its own separate visibility
    setting) are not emitted for clients at all — nothing to inspect, no gap they
    could notice — while admins keep them in the DOM, greyed and only visible in
    edit mode, so they can be shown back."""
    present = {k for k, _ in units}
    stored_order = [k for k in layout.get("order", []) if k in present]
    if stored_order:
        rank = {k: i for i, k in enumerate(stored_order)}
        units = sorted(units, key=lambda kv: rank.get(kv[0], len(rank)))
    hidden = {k for k in layout.get("hidden", []) if k in present}
    hidden |= {k for k in forced_hidden if k in present}
    out: list[str] = []
    for key, panel_html in units:
        is_hidden = key in hidden
        if is_hidden and not is_admin:
            continue
        out.append(
            _ov_unit_wrapper(key, titles.get(key, key), panel_html, hidden=is_hidden)
        )
    return "".join(out)


def _edit_banner_html(*, prefix: str, layout_api: str) -> str:
    """The slim in-edit banner for one editable tab.

    Edit mode is entered from the sidebar kebab (⋮ on the tab's nav item), so
    there's no always-on toolbar cluttering the page. This banner shows only
    while editing: it explains the mode, reflects save state, and offers a Done
    button. It stays in the DOM (hidden via CSS until its pane is .is-editing)
    so the edit JS can read the layout endpoint off it any time."""
    from dashboard.utils.formatting import esc as _esc

    return (
        f'<div class="ov-editing-banner" data-ov-layout-api="{_esc(layout_api)}">'
        f'<span class="ov-editing-badge">Editing layout</span>'
        f'<span class="ov-edit-hint">Drag panels to reorder, or use Hide / Show. '
        f"Changes save automatically — only admins see this.</span>"
        f'<span class="ov-edit-status" id="{prefix}EditStatus" role="status"></span>'
        f'<button type="button" id="{prefix}EditDone" class="ov-edit-done">Done</button>'
        f"</div>"
    )


def _docs_enabled() -> bool:
    import client_insight_documents as docs
    return docs.enabled()


def parse_explorer_filters(text: str | None) -> list[dict]:
    """Parse a client's Campaign Explorer filter config into chip groups.

    Format (one rule per line):
        [Group Name]           optional section header; starts a new chip row
        Chip Label = phrase    a chip that keeps campaigns whose name contains
                               the phrase (case-insensitive substring)
        Chip Label = a, b, c   comma-separated phrases are OR'd together

    Lines before the first ``[Group]`` fall into an unnamed leading group.
    Blank lines and lines starting with ``#`` are ignored. Returns a list of
    ``{"id", "label", "chips": [{"label", "phrases": [...]}]}`` groups, empty
    if nothing valid was defined -- an unconfigured client gets no filter chips
    at all (the textarea's placeholder still shows Product/Region as an
    example of what to type, but that text is never treated as real config).
    """
    groups: list[dict] = []
    current: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {"id": f"g{len(groups)}", "label": line[1:-1].strip() or f"Group {len(groups) + 1}", "chips": []}
            groups.append(current)
            continue
        if "=" not in line:
            continue
        label, _, rhs = line.partition("=")
        label = label.strip()
        phrases = [p.strip().lower() for p in rhs.split(",") if p.strip()]
        if not label or not phrases:
            continue
        if current is None:
            current = {"id": "g0", "label": "Filter", "chips": []}
            groups.append(current)
        current["chips"].append({"label": label, "phrases": phrases})
    return [g for g in groups if g["chips"]]


def hubspot_mql_section_html(report, lead_tracking_url: str | None) -> str:
    """Server-rendered HubSpot MQL tracker panel for the BigQuery dashboard's
    Overview home. Mirrors the Overview tracker on the snapshot dashboard, but
    uses this template's card markup. Each card only renders when its data
    exists, and the whole panel is dropped when nothing has synced yet.
    """
    if report is None or not getattr(report, "configured", False):
        return ""

    _money, _mom, _spark = _mql_money_compact, _mql_mom_delta, _mql_sparkline
    from dashboard.utils.formatting import esc as _esc, fmt_int as _int, fmt_pct as _pct

    # Label follows the connector's configured lifecycle stage (MQL by default,
    # but "Leads", "SQLs", … when the client tracks a different stage).
    noun_plural = getattr(report, "stage_noun_plural", "MQLs") or "MQLs"

    cards: list[str] = []
    if report.mql_count:
        cards.append(
            f'<div class="card"><div class="card-title">{_esc(noun_plural)}</div>'
            f'<div class="card-value">{_int(report.mql_count)}</div>'
            f'{_spark(report.mqls_by_month, "#1d6fd0")}'
            '<div class="card-foot">'
            f'<span class="mql-sub">{_pct(report.mql_count, report.contact_count)} of contacts</span>'
            f'{_mom(report.mqls_by_month)}</div></div>'
        )
    if report.contact_count:
        cards.append(
            '<div class="card"><div class="card-title">Contacts tracked</div>'
            f'<div class="card-value">{_int(report.contact_count)}</div></div>'
        )
    if report.deal_count:
        cards.append(
            '<div class="card"><div class="card-title">Deals</div>'
            f'<div class="card-value">{_int(report.deal_count)}</div>'
            '<div class="card-foot">'
            f'<span class="mql-sub">{_pct(report.deal_count, report.mql_count)} of {_esc(noun_plural)}</span></div></div>'
        )
    if report.pipeline_amount:
        avg = (
            f'<span class="mql-sub">{_money(report.pipeline_amount / report.deal_count)} avg deal</span>'
            if report.deal_count else ""
        )
        cards.append(
            '<div class="card"><div class="card-title">Pipeline</div>'
            f'<div class="card-value">{_money(report.pipeline_amount)}</div>'
            f'<div class="card-foot">{avg}</div></div>'
        )
    if report.won_amount:
        cards.append(
            '<div class="card"><div class="card-title">Won revenue</div>'
            f'<div class="card-value">{_money(report.won_amount)}</div>'
            '<div class="card-foot">'
            f'<span class="mql-sub">{_pct(report.won_amount, report.pipeline_amount)} win rate</span></div></div>'
        )

    if not cards:
        return ""

    more = (
        f'<a class="ov-more" href="{_esc(lead_tracking_url)}" aria-label="Open lead tracking">'
        '<svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></a>'
        if lead_tracking_url else ""
    )
    return (
        '<section class="ov-panel mql-panel">'
        '<div class="sec-head"><h2><span class="mql-dot"></span>HubSpot MQL Tracker</h2>'
        f'<div class="ov-actions">{more}</div></div>'
        f'<div class="cards">{"".join(cards)}</div></section>'
    )


def linkedin_followers_section_html(
    report, more_url: str | None, *, window_days: int = 90
) -> str:
    """Server-rendered LinkedIn follower-growth panel for the Overview home.

    Reads the organic connector's follower series (see
    ``linkedin_organic_report_service.build_report``): a "Total followers" card
    with a growth sparkline, plus a "Net new followers" card with weekly bars.
    Mirrors the MQL tracker's card markup so it inherits the same Overview
    styling. Returns "" when nothing has synced yet so the whole card drops
    rather than showing an empty shell.
    """
    if report is None or not getattr(report, "configured", False):
        return ""

    from dashboard.utils.formatting import esc as _esc, fmt_int as _int

    total_followers = int(getattr(report, "total_followers", 0) or 0)
    net_gain = int(getattr(report, "follower_gain", 0) or 0)
    series = getattr(report, "follower_series", []) or []
    organic = sum(int(r.get("organic_follower_gain") or 0) for r in series)
    paid = sum(int(r.get("paid_follower_gain") or 0) for r in series)

    # Absent source (connected but never synced) → no card at all.
    if not total_followers and not series:
        return ""

    def _delta(n: int) -> str:
        """Window change as a signed count plus growth against the start count.

        The count alone ("32") reads as small or large depending on how big the
        page already is, so it is paired with the percentage it represents —
        computed against the follower count at the start of the window
        (``total - net``), which is what "growth" means here. The percentage
        drops out when that start count is unknown or zero.
        """
        start_count = total_followers - n
        pct = (abs(n) / start_count * 100.0) if start_count > 0 else None
        tail = ""
        if pct is not None:
            fmt = f"{pct:.0f}" if pct >= 10 else f"{pct:.1f}"
            tail = f' · {fmt}% {"growth" if n > 0 else "decline"}'
        window = f'title="Change over the last {window_days} days"'
        if n > 0:
            return f'<span class="mql-delta up" {window}>+{_int(n)} followers{tail}</span>'
        if n < 0:
            return (
                f'<span class="mql-delta down" {window}>'
                f'−{_int(abs(n))} followers{tail}</span>'
            )
        return (
            f'<span class="mql-delta flat" {window}>'
            f'No change in {window_days} days</span>'
        )

    cards: list[str] = []
    if total_followers:
        cards.append(
            '<div class="card"><div class="card-title">Total followers</div>'
            f'<div class="card-value">{_int(total_followers)}</div>'
            f'{_li_follower_sparkline(series)}'
            f'<div class="card-foot">{_delta(net_gain)}</div></div>'
        )

    # Net new followers. The organic/paid split is only worth a line when paid
    # has actually done something — a permanent "· 0 paid" on a page that has
    # never run a follower ad is noise, and it made the number look qualified.
    weekly = _li_weekly_follower_bars(series)
    if paid:
        foot = f'<span class="mql-sub">{_int(organic)} organic · {_int(paid)} paid</span>'
    elif weekly:
        foot = '<span class="mql-sub">By week</span>'
    else:
        foot = ""
    cards.append(
        '<div class="card"><div class="card-title">Net new followers</div>'
        f'<div class="card-value">{_int(net_gain)}</div>'
        f'{weekly}'
        f'<div class="card-foot">{foot}</div></div>'
    )

    # Third column: which tabs of the company page people actually landed on,
    # ranked. Drops out for pages whose section split hasn't synced (the column
    # set post-dates v1 of the page table) so the card falls back to two cards.
    sections = getattr(report, "page_sections", None) or []
    section_bars = _li_section_bars(sections)
    if section_bars:
        section_total = sum(int(s.get("views") or 0) for s in sections)
        page_views = int(getattr(report, "total_page_views", 0) or 0)
        # The bars only cover the tabs LinkedIn breaks out, so they sum to less
        # than the page's total views — the rest are the main page and posts.
        # Show both numbers rather than a total the rows visibly don't add up to.
        if page_views > section_total:
            foot = (
                '<span class="mql-sub" title="LinkedIn reports views for these '
                'page tabs only. The remaining views are the main page and '
                f'posts.">{_int(section_total)} of {_int(page_views)} page views '
                f'in {window_days} days</span>'
            )
        elif section_total:
            foot = (
                f'<span class="mql-sub">{_int(section_total)} page views '
                f'in {window_days} days</span>'
            )
        else:
            foot = ""
        cards.append(
            '<div class="card li-sections">'
            '<div class="card-title">Views by page section</div>'
            f'{section_bars}'
            f'<div class="card-foot">{foot}</div></div>'
        )

    more = (
        f'<a class="ov-more" href="{_esc(more_url)}" aria-label="Open LinkedIn Organic">'
        '<svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></a>'
        if more_url else ""
    )
    return (
        '<section class="ov-panel li-panel">'
        '<div class="sec-head"><h2>'
        f'{_LI_GLYPH_SVG}LinkedIn Followers</h2>'
        f'<div class="ov-actions">{more}</div></div>'
        f'<div class="cards">{"".join(cards)}</div></section>'
    )


def parse_gsc_watchlist(text: str | None) -> list[dict]:
    """Parse the stored keyword watchlist into [{"kw", "page"}] rows.

    One watched keyword per line, as ``keyword|page`` -- the page is optional and
    is the URL (or path) the keyword is being written for. A trailing ``*`` on the
    keyword is kept as-is: it is the marker the backend reads as "this keyword and
    its variants". Blank lines and duplicate keywords are dropped so a hand-edited
    value can't produce two rows for the same keyword.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        kw, _, page = raw.partition("|")
        kw = kw.strip()
        if not kw or not kw.strip("*"):
            continue
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"kw": kw, "page": page.strip()})
    return out


def render_bigquery_dashboard_page(
    *,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    client_slug: str = "nixon-bq-test",
    api_client_key: str = "nixon",
    label: str = "Nixon Medical",
    session_can_switch_clients: bool = False,
    view_as_users: list[dict] | None = None,
    show_budget_tracker: bool = True,
    show_benchmarks: bool = False,
) -> str:
    """Render this BigQuery-mart dashboard for any client.

    client_slug drives dashboard-facing URLs (sidebar, GTM, lead tracking) and
    label is the display name. api_client_key drives the /api/clients/*
    endpoints -- kept separate from client_slug because Nixon's own routes
    still split these (marketing reads under "nixon", GSC/SEMrush/BQ routing
    under "nixon-bq-test"); any client onboarded through the generic
    /api/clients/{client_key}/* routes uses the same value for both.
    """
    today = date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=30)

    # First-run onboarding: a client with no connectors yet would otherwise see
    # a bare "no data" Overview. Show a clear call-to-action instead. Fail open
    # (assume connected) on any error so an established client never sees it.
    has_connectors = True
    # Paid-ad platforms specifically -- distinct from has_connectors, which is
    # true for GA4/GSC-only clients too. Gates the paid Summary/Trends cards
    # below so a client with no google/linkedin/meta connector (e.g. GA4+GSC
    # only) doesn't see a broken paid-media panel that has no BQ mart to read.
    has_paid_ads = True
    try:
        import connector_config_store
        configs = connector_config_store.list_configs(client_slug)
        has_connectors = bool(configs)
        has_paid_ads = any(
            c.connector_type in ("google_ads", "linkedin_ads", "meta_ads", "microsoft_ads")
            and c.status not in ("not_connected", "disconnected")
            for c in configs
        )
    except Exception:
        has_connectors = True
        has_paid_ads = True
    # The demo client has no real connectors (its data is synthetic), so the
    # connector probe above would flag it as un-onboarded and show the "set up
    # connectors" card while hiding the paid panels. Treat it as fully connected
    # so the demo Overview renders complete, with no setup card.
    try:
        import demo_client
        if demo_client.is_demo(client_slug) or demo_client.is_demo(api_client_key):
            has_connectors = True
            has_paid_ads = True
    except Exception:
        pass
    # Search Console branded roots + target keywords (client-configurable), used
    # by the "Branded & Target Keywords" section. Stored one per line.
    gsc_branded_roots = ""
    gsc_target_keywords = ""
    gsc_branded_exclude = ""
    gsc_target_exclude = ""
    # Keyword watchlist ("keyword|page" per line) -- the curated benchmark list
    # in the Search Console tab, separate from the broad branded/target roots.
    gsc_watch_keywords = ""
    ga4_key_events = ""
    explorer_filters_cfg = ""
    analytics_page_path_filter_cfg = ""
    default_date_preset: str = ""
    explorer_campaign_allowlist: list[str] = []
    monthly_budget_val: float | None = None
    pagespeed_targets_stored: dict | None = None
    overview_pinned_card: str | None = None
    overview_layout: dict[str, list[str]] = {"order": [], "hidden": []}
    explorer_layout: dict[str, list[str]] = {"order": [], "hidden": []}
    gsc_layout: dict[str, list[str]] = {"order": [], "hidden": []}
    try:
        import client_dashboard_config as _cdc
        _kwcfg = _cdc.get_config(api_client_key) or _cdc.get_config(client_slug)
        if _kwcfg:
            gsc_branded_roots = _kwcfg.gsc_branded_roots or ""
            gsc_target_keywords = _kwcfg.gsc_target_keywords or ""
            gsc_branded_exclude = getattr(_kwcfg, "gsc_branded_exclude", None) or ""
            gsc_target_exclude = getattr(_kwcfg, "gsc_target_exclude", None) or ""
            gsc_watch_keywords = getattr(_kwcfg, "gsc_watch_keywords", None) or ""
            ga4_key_events = _kwcfg.ga4_key_events or ""
            explorer_filters_cfg = _kwcfg.explorer_filters or ""
            analytics_page_path_filter_cfg = getattr(_kwcfg, "analytics_page_path_filter", None) or ""
            default_date_preset = getattr(_kwcfg, "default_date_preset", None) or ""
            explorer_campaign_allowlist = list(
                getattr(_kwcfg, "explorer_campaign_allowlist", None) or ()
            )
            monthly_budget_val = getattr(_kwcfg, "monthly_budget_usd", None)
            overview_pinned_card = getattr(_kwcfg, "overview_pinned_card", None)
            _layouts = getattr(_kwcfg, "card_layouts", None) or {}
            if not isinstance(_layouts, dict):
                _layouts = {}

            def _stored_layout(tab: str) -> dict[str, list[str]]:
                entry = _layouts.get(tab)
                if not isinstance(entry, dict):
                    return {"order": [], "hidden": []}
                return {
                    "order": [str(k) for k in (entry.get("order") or [])],
                    "hidden": [str(k) for k in (entry.get("hidden") or [])],
                }

            overview_layout = _stored_layout("overview")
            explorer_layout = _stored_layout("explorer")
            gsc_layout = _stored_layout("gsc")
        # Every write goes to the URL slug — the inline goal POST
        # (/dashboard/<client_slug>/budget), the settings page save and the
        # active-days POST all key on client_slug — but the read above prefers
        # the API client key. The two differ only for Nixon (nixon-bq-test vs
        # nixon), whose dashboard therefore re-rendered an empty goal after
        # every save while the value sat safely in the slug's row. Read that
        # row back for the fields those endpoints own. (Same fix the Nixon
        # settings page already carries; the dashboard never got it.)
        if client_slug and (_kwcfg is None or _kwcfg.client_slug != client_slug):
            _slugcfg = _cdc.get_config(client_slug)
            if _slugcfg is not None and _slugcfg.monthly_budget_usd is not None:
                monthly_budget_val = _slugcfg.monthly_budget_usd
        pagespeed_targets_stored = (
            _cdc.get_pagespeed_targets(api_client_key)
            or _cdc.get_pagespeed_targets(client_slug)
        )
    except Exception:
        pass
    # Effective per-KPI PageSpeed target bands (client overrides merged over
    # defaults), injected for the Site Performance tab's traffic-light coloring.
    pagespeed_targets_json = json.dumps(
        pagespeed_renderer.effective_targets(pagespeed_targets_stored)
    ).replace("<", "\\u003c")
    # Which device strategies are synced (PAGESPEED_STRATEGIES, default desktop)
    # — the Site Performance toggle renders exactly these, hiding itself when
    # there's only one.
    try:
        import pagespeed_service as _ps
        pagespeed_strategies_json = json.dumps(_ps.synced_strategies())
    except Exception:
        pagespeed_strategies_json = '["desktop"]'
    # Whether the Overview home shows the Site Performance scorecard — gated on
    # the pagespeed connector being connected, same as the sidebar nav button.
    try:
        _pf = platform_nav_flags(client_slug)
        show_pagespeed = bool(_pf.get("show_pagespeed"))
        show_semrush = bool(_pf.get("show_semrush"))
        show_lead_tracking = bool(_pf.get("show_lead_tracking"))
        show_linkedin_organic = bool(_pf.get("show_linkedin_organic"))
        # Search Console: gates both the Overview panel and the sidebar tab (see
        # dashboard_sidebar_view_nav_html). Fails open on a failed connector read
        # so a transient blip doesn't drop the panel for a client that has GSC.
        show_gsc = bool(_pf.get("show_gsc")) or not _pf.get("_connector_read_ok", True)
    except Exception:
        show_pagespeed = False
        show_semrush = False
        show_lead_tracking = False
        show_linkedin_organic = False
        show_gsc = True

    # HubSpot MQL tracker for the Overview home — only fetched when HubSpot is
    # connected. Failures never break the dashboard; the helper drops any card
    # (and the whole panel) that has no data behind it.
    mql_section_html = ""
    if show_lead_tracking:
        try:
            import hubspot_reports_service
            from dashboard.utils.urls import lead_tracking_page_url
            _mql_report = hubspot_reports_service.build_report(client_slug)
            mql_section_html = hubspot_mql_section_html(
                _mql_report,
                lead_tracking_page_url(
                    client_slug=client_slug,
                    access_key=access_key,
                    use_session=use_session,
                ),
            )
        except Exception:
            mql_section_html = ""
    # LinkedIn follower growth for the Overview home — only when the organic
    # connector is connected. Reads the follower series the connector syncs into
    # BigQuery; failures never break the dashboard, and the helper drops the card
    # entirely when nothing has synced yet.
    linkedin_organic_section_html = ""
    if show_linkedin_organic:
        try:
            import linkedin_organic_report_service
            from dashboard.utils.urls import linkedin_organic_page_url
            _lo_window = 90
            _lo_report = linkedin_organic_report_service.build_report(
                client_slug, days=_lo_window
            )
            linkedin_organic_section_html = linkedin_followers_section_html(
                _lo_report,
                linkedin_organic_page_url(
                    client_slug=client_slug,
                    access_key=access_key,
                    use_session=use_session,
                ),
                window_days=_lo_window,
            )
        except Exception:
            linkedin_organic_section_html = ""
    # Organic Search Intelligence (SEMrush) — only when the SEMrush connector is
    # connected; otherwise the whole section is dropped from the GSC tab.
    semrush_section_html = """
      <section id="sec-semrush">
        <div class="sec-head"><h2>Organic Search Intelligence</h2><span class="status" id="semrushStatus"></span></div>
        <div class="cards" id="semrushKpis"></div>
      </section>""" if show_semrush else ""
    # Campaign Explorer filter chips: parsed from the client's own config only.
    # Injected as JSON for the page JS to build the chip rows + match campaigns.
    # An unconfigured client gets no chips -- the "Product"/"Region" example in
    # the Edit filters textarea's placeholder is just an example, not a default
    # that quietly populates the dropdowns until someone configures their own.
    # Escape "<" so a chip label can't break out of the <script> block.
    explorer_filter_groups_json = json.dumps(
        parse_explorer_filters(explorer_filters_cfg)
    ).replace("<", "\\u003c")

    # Landing date-range preset: the admin-chosen client default, else last_30.
    # Validated against the known presets so a stale value can't wedge the picker.
    _DATE_PRESETS = (
        "last_7", "last_30", "last_90", "last_365",
        "this_week", "last_week", "this_month", "last_month",
        "this_quarter", "last_quarter", "this_year",
    )
    effective_default_preset = (
        default_date_preset if default_date_preset in _DATE_PRESETS else "last_30"
    )
    default_date_preset_json = json.dumps(effective_default_preset)
    _DATE_PRESET_LABELS = [
        ("last_7", "Last 7 days"), ("last_30", "Last 30 days"),
        ("last_90", "Last 90 days"), ("last_365", "Last 365 days"),
        ("this_week", "This week"), ("last_week", "Last week"),
        ("this_month", "This month"), ("last_month", "Last month"),
        ("this_quarter", "This quarter"), ("last_quarter", "Last quarter"),
        ("this_year", "This year"),
    ]
    date_preset_labels_json = json.dumps(dict(_DATE_PRESET_LABELS))
    # One picker next to the Range control, listing every comparison the page
    # can draw. "No comparison" leads the list and is the default: a delta under
    # every number is only worth the ink when someone came to the page asking
    # "versus what?", so nobody pays for it until they ask. The other two are the
    # windows -- the equivalent range immediately before the selected one, and
    # the same range 12 months back.
    #
    # These labels are what the picker, the backfill notice and every delta
    # tooltip say, so they cannot drift apart.
    _COMPARE_DEFAULT_MODE = "none"
    _COMPARE_MODES = [
        ("none", "No comparison"),
        ("prev_period", "Previous period"),
        ("prev_year", "Previous year"),
    ]
    compare_mode_labels_json = json.dumps(dict(_COMPARE_MODES))
    compare_default_mode_json = json.dumps(_COMPARE_DEFAULT_MODE)
    compare_option_rows_html = "".join(
        f'<button type="button" class="range-opt{" active" if v == _COMPARE_DEFAULT_MODE else ""}" role="option" data-cmp="{v}">{lbl}</button>'
        for v, lbl in _COMPARE_MODES
    )
    # The raw stored default ('' when the client has none) — distinct from the
    # effective preset above, which falls back to last_30. The dropdown JS uses it
    # to decide whether "Make default" starts ticked and whether Apply clears.
    stored_default_preset_json = json.dumps(
        default_date_preset if default_date_preset in _DATE_PRESETS else ""
    )
    # The Range picker is a custom dropdown (so the admin "Make default" + Apply
    # controls live inside the panel, under the preset list). Each preset is a
    # selectable row; the built-in default row starts active.
    range_option_rows_html = "".join(
        f'<button type="button" class="range-opt{" active" if v == effective_default_preset else ""}" role="option" data-preset="{v}">{lbl}</button>'
        for v, lbl in _DATE_PRESET_LABELS
    )
    effective_default_label = next(
        (lbl for v, lbl in _DATE_PRESET_LABELS if v == effective_default_preset),
        "Last 30 days",
    )
    # Admin-only footer inside the Range dropdown: a "Make default" checkbox +
    # Apply button that saves the applied preset as this client's landing range
    # (or clears it). Non-admins see only the preset list. The checkbox's ticked
    # state and Apply's save/clear behavior are driven by the dropdown JS.
    range_default_html = "" if not session_is_admin else """<div class="range-dd-foot">
              <label class="range-default" title="Land on the applied range for this client"><input type="checkbox" id="rangeMakeDefault"><span>Make default</span></label>
              <button type="button" class="range-apply" id="rangeApply">Apply</button>
              <span class="range-default-status" id="rangeDefaultStatus"></span>
            </div>"""
    # Admin-only footer inside the Events dropdown: saves the ticked event names
    # as this client's stored key-event set, so every visitor lands on the same
    # definition of "key events" instead of GA4's own flags.
    key_event_default_html = "" if not session_is_admin else """<div class="range-dd-foot">
                <button type="button" class="range-apply" id="keyEventSaveDefault">Save as default</button>
                <span class="range-default-status" id="keyEventSaveStatus"></span>
              </div>"""
    # Campaign Explorer allowlist (campaign names the client may see). Empty list
    # = no restriction. Escape "<" so a campaign name can't break the <script>.
    explorer_campaign_allowlist_json = json.dumps(
        explorer_campaign_allowlist
    ).replace("<", "\\u003c")

    # Admin-only "Edit filters" affordance in the Campaign explorer header. The
    # editor (textarea of chip rules) moved here from the Insights page so it
    # lives with the thing it configures; it POSTs the same explorer/filters API.
    from dashboard.utils.formatting import esc as _esc
    explorer_filters_edit_html = "" if not session_is_admin else f"""<div class="ef-edit">
          <button type="button" class="ef-edit-btn" id="efEditBtn" aria-haspopup="dialog" aria-expanded="false" title="Edit filter chips">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>
            <span>Edit filters</span>
          </button>
          <div class="ef-pop" id="efPop" role="dialog" aria-label="Edit campaign filters" hidden>
            <div class="ef-pop-head"><span>Campaign explorer filters</span><button type="button" class="ef-pop-x" aria-label="Close">&times;</button></div>
            <div class="ef-pop-body">
              <p class="ef-pop-desc">One rule per line as <code>Chip label = phrase</code> (matches campaign names, case-insensitive). Comma-separate phrases to match any of them, and group rows under a <code>[Group name]</code> header. Leave blank for the defaults.</p>
              <textarea id="efText" spellcheck="false" placeholder="[Product]&#10;Apparel = apparel&#10;Scrubs = scrub&#10;&#10;[Region]&#10;TX = tx&#10;FL = fl">{_esc(explorer_filters_cfg)}</textarea>
              <div class="ef-pop-actions">
                <button type="button" class="ef-pop-btn primary" id="efSave">Save filters</button>
                <span class="ef-status" id="efStatus"></span>
              </div>
            </div>
          </div>
        </div>"""

    # Website Analytics page-path scope. Parsed to a list of substring patterns
    # (one per non-blank, non-comment line) injected for the page JS, which hides
    # the site-wide panels when a scope is active and shows a scope indicator.
    analytics_path_patterns = [
        s.strip() for s in analytics_page_path_filter_cfg.splitlines()
        if s.strip() and not s.strip().startswith("#")
    ]
    analytics_path_filter_json = json.dumps(analytics_path_patterns).replace("<", "\\u003c")
    # Admin-only "Edit page filter" editor for the Website Analytics view. Same
    # popover chrome as the campaign explorer filters editor; POSTs the scope to
    # the analytics/page-path-filter API. Reuses the ef-* styles.
    analytics_path_filter_edit_html = "" if not session_is_admin else f"""<div class="ef-edit">
          <button type="button" class="ef-edit-btn" id="pfEditBtn" aria-haspopup="dialog" aria-expanded="false" title="Limit this view to certain page paths">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>
            <span>Edit page filter</span>
          </button>
          <div class="ef-pop" id="pfPop" role="dialog" aria-label="Edit analytics page filter" hidden>
            <div class="ef-pop-head"><span>Website Analytics page filter</span><button type="button" class="ef-pop-x" aria-label="Close">&times;</button></div>
            <div class="ef-pop-body">
              <p class="ef-pop-desc">One path per line (case-insensitive substring, e.g. <code>/careers</code>). Sessions, Pages, Landing Pages and Geography are limited to matching paths; the panels with no page to scope by (Acquisition — both tabs — plus Audience and Age &amp; gender) are hidden while a filter is set. Leave blank to show the whole site.</p>
              <textarea id="pfText" spellcheck="false" placeholder="/careers&#10;/jobs&#10;/apply">{_esc(analytics_page_path_filter_cfg)}</textarea>
              <div class="ef-pop-actions">
                <button type="button" class="ef-pop-btn primary" id="pfSave">Save filter</button>
                <span class="ef-status" id="pfStatus"></span>
              </div>
            </div>
          </div>
        </div>"""

    # Admin-only "Campaigns" picker in the Campaign explorer header. Restricts the
    # Explorer to a chosen subset of campaigns — for portals whose account pulls
    # more campaigns than the client should see. The checklist is filled by JS
    # from the campaigns actually loaded (plus any already-saved names), and it
    # POSTs the selected set to the explorer/campaigns API. The `1` badge count is
    # only shown when a restriction is active (JS updates it after load).
    explorer_campaigns_edit_html = "" if not session_is_admin else """<div class="ef-edit">
          <button type="button" class="ef-edit-btn" id="ecEditBtn" aria-haspopup="dialog" aria-expanded="false" title="Limit which campaigns this portal shows">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h16"/><path d="M7 12h10"/><path d="M10 18h4"/></svg>
            <span>Campaigns</span><span class="ec-badge" id="ecBadge" hidden></span>
          </button>
          <div class="ef-pop ec-pop" id="ecPop" role="dialog" aria-label="Choose visible campaigns" hidden>
            <div class="ef-pop-head"><span>Visible campaigns</span><button type="button" class="ef-pop-x" aria-label="Close">&times;</button></div>
            <div class="ef-pop-body">
              <p class="ef-pop-desc">Tick the campaigns this portal should show. Leave everything unticked to show <strong>all</strong> campaigns. Only campaigns seen in the current date range (plus ones already chosen) are listed.</p>
              <input type="text" class="ec-search" id="ecSearch" placeholder="Search campaigns…" autocomplete="off">
              <div class="ec-tools"><button type="button" class="ec-tool" id="ecSelectAll">Select all</button><button type="button" class="ec-tool" id="ecClear">Clear</button><span class="ec-count" id="ecCount"></span></div>
              <div class="ec-list" id="ecList"></div>
              <div class="ef-pop-actions">
                <button type="button" class="ef-pop-btn primary" id="ecSave">Save campaigns</button>
                <span class="ef-status" id="ecStatus"></span>
              </div>
            </div>
          </div>
        </div>"""

    # Everything that configures the Campaign explorer without being part of
    # reading it lives behind one kebab in the section head: the two admin
    # editors above plus the GA4-verified conversions switch (every viewer gets
    # that one — it only decides whether the GA4 column and card are drawn).
    # The head itself keeps just the Platform chips and the status line.
    explorer_adv_items = (explorer_campaigns_edit_html + explorer_filters_edit_html)
    explorer_adv_menu_html = f"""<div class="adv-menu" id="explorerAdv">
          <button type="button" class="adv-btn" id="explorerAdvBtn" aria-haspopup="true" aria-expanded="false" title="More options" aria-label="More options">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><circle cx="12" cy="5" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="12" cy="19" r="1.9"/></svg>
          </button>
          <div class="adv-pop" id="explorerAdvPop" hidden>
            <div class="adv-pop-head">Explorer options</div>
            <label class="adv-switch-row">
              <span class="adv-switch-txt">GA4-verified conv.<small>Show the GA4-matched conversion column and card</small></span>
              <input type="checkbox" class="adv-switch" id="explorerVerifiedToggle">
            </label>
            {'<div class="adv-sep"></div>' + explorer_adv_items if explorer_adv_items else ''}
          </div>
        </div>"""

    connectors_url = _api_url(f"/dashboard/{client_slug}/connectors", access_key=access_key)
    onboarding_html = "" if has_connectors else f"""
      <section class="onboarding-card onboarding-steps">
        <div class="onboarding-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 3a3 3 0 00-3 3v1H9V6a3 3 0 10-3 3v1H3v2h3v1a3 3 0 103 3v-1h6v1a3 3 0 103-3v-1h3v-2h-3V9a3 3 0 000-6z"/></svg></div>
        <h2>Get your dashboard connected</h2>
        <p>Three steps to start pulling data in. Steps&nbsp;1–2 happen once in Google&nbsp;Cloud (signed in as the agency account); step&nbsp;3 is here in the portal.</p>
        <ol class="ob-steps">
          <li class="ob-step">
            <div class="ob-step-num">1</div>
            <div class="ob-step-body">
              <div class="ob-step-title">Create a BigQuery project &amp; link billing</div>
              <div class="ob-step-desc">In Google Cloud — signed in as <strong>sagefrogmarketinggroup@gmail.com</strong> — create a new GCP project for this client (or reuse an existing one) and <strong>link a billing account</strong> to it (BigQuery won't run queries without one). Note the <strong>project ID</strong> for step&nbsp;3.</div>
              <a class="ob-step-link" href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">Create project →</a>
              <a class="ob-step-link" href="https://console.cloud.google.com/billing/linkedaccount" target="_blank" rel="noopener" style="margin-left:14px">Link billing →</a>
            </div>
          </li>
          <li class="ob-step">
            <div class="ob-step-num">2</div>
            <div class="ob-step-body">
              <div class="ob-step-title">Grant access in IAM</div>
              <div class="ob-step-desc">On that project, open <strong>IAM &amp; Admin → Grant access</strong>. Add the principal below and give it <strong>both</strong> the <strong>BigQuery Data Editor</strong> and <strong>BigQuery Job User</strong> roles (both are required — the connector step verifies them).</div>
              <div class="ob-copy">
                <code>marketing-data-reader@sagefrog.iam.gserviceaccount.com</code>
                <button type="button" class="ob-copy-btn" onclick="obCopy('marketing-data-reader@sagefrog.iam.gserviceaccount.com', this)">Copy</button>
              </div>
              <a class="ob-step-link" href="https://console.cloud.google.com/iam-admin/iam" target="_blank" rel="noopener">Open IAM →</a>
            </div>
          </li>
          <li class="ob-step">
            <div class="ob-step-num">3</div>
            <div class="ob-step-body">
              <div class="ob-step-title">Set up connectors</div>
              <div class="ob-step-desc">Back here, connect a marketing platform and enter your new <strong>project ID</strong> as the destination. We'll create the datasets and verify access — data appears after the first sync.</div>
              <a class="onboarding-cta" href="{connectors_url}">Set up connectors →</a>
            </div>
          </li>
        </ol>
        <script>
        function obCopy(t, btn){{
          navigator.clipboard.writeText(t).then(function(){{
            var o = btn.textContent; btn.textContent = 'Copied ✓'; btn.classList.add('ok');
            setTimeout(function(){{ btn.textContent = o; btn.classList.remove('ok'); }}, 1400);
          }});
        }}
        </script>
      </section>
    """

    # Section nav (Overview/Explorer/Website Analytics/Search Console as JS tabs
    # driven by switchTab() below, + connected Lead/Event Tracking as links).
    # Shared with the settings/connectors/files pages via dashboard_sidebar_view_nav_html
    # so the sidebar is identical on every page of a Nixon-style dashboard.
    view_nav_html = dashboard_sidebar_view_nav_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
        as_tabs=True,
    )

    sidebar_html = render_sidebar(
        client_slug=client_slug,
        label=label,
        active_nav="overview",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=_docs_enabled(),
        # Always expose the connectors nav on connector-driven dashboards so a
        # brand-new client can reach the setup wizards before any connector is
        # connected (pflags only turns it on once one already exists).
        show_connectors=True,
        view_nav_html=view_nav_html,
        session_can_switch_clients=session_can_switch_clients,
    )

    admin_class = "is-admin" if session_is_admin else ""
    # The "View as user" tool used to live in a floating admin bubble here; it
    # now has its own card on the /admin page (see web_auth.render_admin_page),
    # so it no longer overlays the client dashboard during a live presentation.
    # ``view_as_users`` is still accepted for call-site compatibility.
    del view_as_users

    # Presenter notes: floating, client-specific notepad for agency users to keep
    # talking points on hand while sharing this dashboard live. Agency-only
    # (admins + agency-wide "standard" users, i.e. whoever can switch clients);
    # client-role users never see it.
    notes_widget_html = ""
    if session_can_switch_clients:
        from dashboard.renderers import notes_widget as _notes_widget
        notes_widget_html = _notes_widget.widget_html(client_slug=client_slug, label=label)

    def _aurl(path: str) -> str:
        return _api_url(path, access_key=access_key)

    # Site Performance (PageSpeed Insights) tab — the markup goes in below; its
    # CSS and JS are composed into the cached assets (see dashboard/assets.py).
    # The pane is always emitted (like Search Console); the sidebar nav button is
    # what gates on the pagespeed connector (see base_layout.platform_nav_flags).
    pagespeed_pane_html = pagespeed_renderer.pane_html()

    # Google Business Profile tab — same deal: the pane is always emitted and the
    # sidebar nav button gates on the google_business connector.
    google_business_pane_html = google_business_renderer.pane_html()

    # No paid-ad connector (google/linkedin/meta) -- the paid Summary/Trends
    # cards have no BQ mart to read and would otherwise render a zeroed-out
    # panel with a table-not-found error (see e.g. Andesa: GA4 + GSC only).
    # Show a lightweight traffic/search snapshot in their place instead.
    # The Platform chips only ever filtered the two paid views (Paid summary and
    # Campaign explorer), so they sit in those card heads rather than in the
    # sticky bar next to Range/Compare, which apply page-wide. Both rows drive
    # the one platformFilter Set -- see buildPlatformChips() below.
    def _platform_chip_row(dom_id: str) -> str:
        if not has_paid_ads:
            return ""
        # No visible "Platform" caption: the chips (All / Google / LinkedIn /
        # …) say what they filter, and on a phone the caption was what pushed
        # the last chip off the card. The group keeps its aria-label.
        return (
            '<div class="card-filter">'
            f'<div class="chips platform-chips" id="{dom_id}" role="group" '
            'aria-label="Filter by platform"></div>'
            "</div>"
        )

    platform_chips_summary_html = _platform_chip_row("platformChips")
    platform_chips_explorer_html = _platform_chip_row("explorerPlatformChips")

    # Budget tracking (Campaign Explorer): shared module, also on the settings
    # page. Only when the client runs paid ads (no spend mart otherwise) AND the
    # per-client "show on explorer" toggle is on. An admin keeps the section in
    # the DOM even when that toggle is off, greyed and only visible in Explorer
    # edit mode, so it can be shown back from there (same rule as a hidden
    # Overview card — see budget_hidden below). Its scripts lazy-load behind an
    # IntersectionObserver, so a hidden section costs no queries. The inline goal
    # editor is admin-only (session_is_admin is the effective user, so a view-as
    # client correctly hides it). Lazy import avoids a circular dependency.
    from dashboard.renderers import budget_tracker as _budget_tracker
    budget_hidden = not show_budget_tracker
    show_budget = bool(has_paid_ads and (show_budget_tracker or session_is_admin))
    budget_section_html = (
        _budget_tracker.section_html(can_edit=session_is_admin) if show_budget else ""
    )
    budget_scripts = (
        _budget_tracker.scripts(
            api_client_key=api_client_key,
            access_key=access_key,
            monthly_budget=monthly_budget_val,
            can_edit=session_is_admin,
            client_slug=client_slug,
        )
        if show_budget
        else ""
    )

    # Caption + hover tooltip shared by the four branded/target keyword trend
    # charts (Search Console tab + Overview), explaining how the avg-position
    # line is built. Native title tooltip, matching the .cmp-warn help pattern.
    _kw_trend_help = (
        "Each point is the impression-weighted average Google position of the "
        "matching keywords (your include roots, minus any exclude terms) for "
        "that week. Lower is better, so the axis is flipped — higher on the "
        "chart means a better ranking. The line always covers the last ~13 "
        "weeks, regardless of the date range selected above."
    )
    _kw_trend_ico = (
        '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" '
        'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" '
        'aria-hidden="true"><circle cx="8" cy="8" r="6.5"/>'
        '<path d="M8 7.3v3.4"/>'
        '<circle cx="8" cy="4.9" r=".85" fill="currentColor" stroke="none"/></svg>'
    )
    _kw_trend_cap = (
        '<div class="chart-cap">Avg. position over time'
        '<span class="info-tip" tabindex="0" role="img" '
        f'aria-label="How this chart works. {_kw_trend_help}" '
        f'title="{_kw_trend_help}">{_kw_trend_ico}</span></div>'
    )

    # Overview is a "home": the top widget from each section, each with a
    # "See more" that jumps to that tab. Panels below are shared by all clients;
    # the paid panel is prepended only when the client runs paid ads. Card order
    # and visibility are managed by admins in edit mode (entered from the sidebar
    # kebab); see the layout handling and _ov_unit_wrapper below.

    panel_website = """
      <section class="ov-panel">
        <div class="sec-head"><h2>Website analytics</h2><div class="ov-actions"><span class="status" id="ovSessionsStatus"></span><div class="chips seg" id="ovSeGranChips"><button type="button" class="chip" data-gran="daily">Daily</button><button type="button" class="chip active" data-gran="weekly">Weekly</button></div><button type="button" class="ov-more" aria-label="See more" data-goto="analytics"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="cards metric-cards" id="ovSeCards" style="margin-bottom:12px"></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:220px"><canvas id="ovSessionsTrend"></canvas></div></div>
        <div class="cmp-legend" id="ovSessionsLegend"></div>
      </section>"""

    panel_ai = """
      <section class="ov-panel">
        <div class="sec-head"><h2>AI traffic</h2><div class="ov-actions"><span class="status" id="ovAiStatus"></span><div class="chips seg" id="ovAiGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><button type="button" class="ov-more" aria-label="See more" data-goto="ai_traffic"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:220px"><canvas id="ovAiTrend"></canvas></div></div>
        <div class="cmp-legend" id="ovAiLegend"></div>
      </section>"""

    panel_gsc = f"""
      <section class="ov-panel">
        <div class="sec-head"><h2>Search Console</h2><div class="ov-actions"><span class="status" id="ovGscStatus"></span><button type="button" class="ov-more" aria-label="See more" data-goto="gsc"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="two-col" style="margin-top:0">
          <div class="col-panel">
            <h3>Branded queries</h3>
            <div class="table-wrap"><table id="ovGscBrandedLeaders" class="compact gsc-leaderboard"></table></div>
            {_kw_trend_cap}
            <div class="chart-canvas-host" style="height:180px"><canvas id="ovGscBrandedTrend"></canvas></div>
            <div class="muted" id="ovGscBrandedNote" style="font-size:.74rem;margin-top:6px"></div>
          </div>
          <div class="col-panel">
            <h3>Target keywords</h3>
            <div class="table-wrap"><table id="ovGscTargetLeaders" class="compact gsc-leaderboard"></table></div>
            {_kw_trend_cap}
            <div class="chart-canvas-host" style="height:180px"><canvas id="ovGscTargetTrend"></canvas></div>
            <div class="muted" id="ovGscTargetNote" style="font-size:.74rem;margin-top:6px"></div>
          </div>
        </div>
      </section>"""

    panel_pagespeed = """
      <section class="ov-panel">
        <div class="sec-head"><h2>Site performance</h2><div class="ov-actions"><span class="status" id="ovPsStatus"></span><button type="button" class="ov-more" aria-label="See more" data-goto="site_performance"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="cards" id="ovPsScores"></div>
      </section>""" if show_pagespeed else ""

    paid_panel = f"""
      <section id="sec-overview">
        <div class="sec-head"><h2>Paid summary</h2><div class="ov-actions">{platform_chips_summary_html}<span class="status" id="summaryStatus"></span><button type="button" class="ov-more" aria-label="See more" data-goto="explorer"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="cards" id="summaryCards"></div>
      </section>""" if has_paid_ads else ""

    # Overview cards in natural order, each tagged with its stable pin key. The
    # MQL tracker leads when it has data, then paid, then the shared panels.
    ov_units: list[tuple[str, str]] = []
    if mql_section_html:
        ov_units.append(("mql", mql_section_html))
    if paid_panel:
        ov_units.append(("paid", paid_panel))
    ov_units.append(("website", panel_website))
    if linkedin_organic_section_html:
        ov_units.append(("linkedin_organic", linkedin_organic_section_html))
    ov_units.append(("ai_traffic", panel_ai))
    if show_gsc:
        ov_units.append(("gsc", panel_gsc))
    if panel_pagespeed:
        ov_units.append(("site_performance", panel_pagespeed))

    # Legacy single-card pin, only when this client has no stored order: move the
    # pinned card to the top if it's present this render. The stored layout (which
    # _render_editable_panels applies next) supersedes it.
    if overview_pinned_card and not [
        k for k in overview_layout.get("order", []) if k in {k for k, _ in ov_units}
    ]:
        _pin_idx = next(
            (i for i, (k, _) in enumerate(ov_units) if k == overview_pinned_card),
            None,
        )
        if _pin_idx is not None:
            ov_units.insert(0, ov_units.pop(_pin_idx))

    overview_summary_html = _render_editable_panels(
        ov_units,
        overview_layout,
        titles=OVERVIEW_PINNABLE_CARDS,
        is_admin=session_is_admin,
    )
    overview_edit_banner_html = _edit_banner_html(
        prefix="ov",
        layout_api=_api_url(
            f"/api/clients/{api_client_key}/tabs/overview/card-layout",
            access_key=access_key,
        ),
    )

    # ---- Campaign Explorer panels ----
    # Same editable-panel treatment as Overview, stored under the "explorer" entry
    # of card_layouts: an admin can drag these to reorder or hide any of them. The
    # keyword table hides itself (inline display) until the client actually has
    # Google Ads search-keyword data — the page JS toggles the wrapper, so an
    # empty panel never shows an edit bar either.
    panel_explorer_main = f"""
      <section id="sec-explorer">
        <div class="sec-head"><h2>Campaign explorer</h2><div class="sec-head-actions">{platform_chips_explorer_html}<span class="status" id="explorerStatus"></span>{explorer_adv_menu_html}</div></div>
        <div class="cards" id="explorerSummaryCards" style="margin-bottom:14px" role="group" aria-label="Metrics on the chart below (pick one or more)"></div>
        <!-- Filter groups (Product / Region / Business line …) live in the sticky
             top bar (#explorerFilterBar) as dropdowns; built by buildExplorerFilters()
             from the client-configured chip rules; see EXPLORER_FILTER_GROUPS.
             The trend chart below reads the same campaign-name / platform filters
             and the metric a summary card above has selected -- see
             renderExplorerTrend(). -->
        <div class="expl-trend" id="explorerTrendSec">
          <div class="expl-trend-head">
            <span class="expl-trend-title" id="explorerTrendTitle">Spend over time</span>
            <span class="status" id="explorerTrendStatus"></span>
          </div>
          <div id="explorerTrendBody">
            <div class="chart-wrap"><div class="chart-canvas-host" style="height:200px"><canvas id="explorerTrendChart"></canvas></div></div>
            <div class="cmp-legend" id="explorerTrendLegend"></div>
          </div>
        </div>
        <div class="table-wrap"><table id="explorerTable"></table></div>
      </section>"""

    panel_keywords = """
      <section id="sec-keywords" style="display:none">
        <div class="sec-head"><h2>Keyword Performance</h2><div class="sec-head-actions"><span class="kw-window" id="keywordWindow"></span><span class="status" id="keywordStatus"></span></div></div>
        <div class="kw-toolbar">
          <input type="search" id="keywordSearch" class="kw-search" placeholder="Search keywords…" autocomplete="off">
          <div class="chips" id="keywordMatchChips"></div>
        </div>
        <div class="table-wrap"><table id="keywordTable" class="compact"></table></div>
        <div class="pager" id="keywordPager"></div>
      </section>"""

    # LinkedIn member demographics — who actually saw and clicked the ads.
    # A sibling of the campaign tree rather than a tier inside it: LinkedIn
    # reports demographics per window with no date dimension and no per-creative
    # grain, so these rows can neither hang off a creative nor be re-cut to the
    # page's date range. The panel says which window it is showing (see
    # marketing_service.fetch_linkedin_demographics) instead of silently
    # implying it follows the date picker. Hidden until the client actually has
    # demographics data, same inline-display trick as the keyword panel.
    panel_lidemo = """
      <section id="sec-lidemo" style="display:none">
        <div class="sec-head">
          <h2>LinkedIn audience</h2>
          <div class="sec-head-actions">
            <span class="lid-window" id="lidemoWindow"></span>
            <button type="button" class="lid-export" id="lidemoExport" title="Download every breakdown in this window as a CSV">Export CSV</button>
            <span class="status" id="lidemoStatus"></span>
          </div>
        </div>
        <div class="pnl-tabs lid-tabs" role="tablist" aria-label="Demographic breakdown" id="lidemoTabs"></div>
        <div class="table-wrap"><table id="lidemoTable" class="compact"></table></div>
        <div class="tbl-more" id="lidemoMore"></div>
        <p class="lid-note" id="lidemoNote"></p>
      </section>"""

    # Google Ads age / gender segments — where the money goes by demographic and
    # which segments are burning it. Unlike the LinkedIn panel this one does
    # follow the date picker (Google reports demographics per day), and it makes
    # explicit recommendations, so it also has to be explicit about coverage:
    # Performance Max reports no demographics at all and Search only reports
    # what Google can infer, so the note carries the share of spend Google could
    # not classify. Hidden until the client has data, same as the keyword panel.
    panel_gdemo = """
      <section id="sec-gdemo" style="display:none">
        <div class="sec-head">
          <h2>Google Ads demographics</h2>
          <div class="sec-head-actions">
            <span class="gd-coverage" id="gdemoCoverage" hidden></span>
            <span class="status" id="gdemoStatus"></span>
          </div>
        </div>
        <div class="pnl-tabs gd-tabs" role="tablist" aria-label="Demographic dimension" id="gdemoTabs"></div>
        <div class="gd-recs" id="gdemoRecs" hidden></div>
        <div class="table-wrap"><table id="gdemoTable" class="compact"></table></div>
        <div class="tbl-more" id="gdemoMore"></div>
        <p class="gd-note" id="gdemoNote"></p>
      </section>"""

    # The stand-alone "Paid trends" panel that used to sit here is gone: the
    # campaign explorer's own chart, driven by the summary cards above the tree,
    # plots the same metrics over the same window against the same filters, and
    # two charts answering one question is how they end up disagreeing.

    ex_units: list[tuple[str, str]] = [
        ("explorer", panel_explorer_main),
        ("keywords", panel_keywords),
        ("gdemo", panel_gdemo),
        ("lidemo", panel_lidemo),
    ]
    if budget_section_html:
        ex_units.append(("budget", budget_section_html))

    explorer_sections_html = _render_editable_panels(
        ex_units,
        # The budget tracker orders with the rest but never takes its visibility
        # from the layout's hidden set (see forced_hidden below), so drop any
        # stale entry for it rather than letting the two stores disagree.
        {
            "order": explorer_layout.get("order", []),
            "hidden": [k for k in explorer_layout.get("hidden", []) if k != "budget"],
        },
        titles=EXPLORER_LAYOUT_CARDS,
        is_admin=session_is_admin,
        # The budget tracker's own "Show on Explorer" setting is its visibility,
        # so the layout's hidden set never speaks for it (the edit JS posts that
        # panel's Hide/Show to the budget-visibility endpoint instead).
        forced_hidden=frozenset({"budget"}) if budget_hidden else frozenset(),
    )
    explorer_edit_banner_html = _edit_banner_html(
        prefix="ex",
        layout_api=_api_url(
            f"/api/clients/{api_client_key}/tabs/explorer/card-layout",
            access_key=access_key,
        ),
    )

    # ---- Search Console panels ----
    # Same editable-panel treatment as Overview and the Explorer, stored under
    # the "gsc" entry of card_layouts. The SEMrush panel is only in the list when
    # that connector is on; the watchlist hides its own wrapper when there is
    # nothing on the list and nobody who could add one (see loadGscWatchlist).
    panel_gsc_kpis = """
      <section id="sec-gsc-overview">
        <div class="sec-head"><h2>Search Console</h2><span class="status" id="gscStatus"></span></div>
        <div class="cards" id="gscKpis"></div>
      </section>"""

    panel_gsc_tables = """
      <section id="sec-gsc-tables">
        <div class="two-col">
          <div class="col-panel">
            <h3>Top queries</h3>
            <div class="table-wrap"><table id="gscQueriesTable" class="compact"></table></div>
            <div class="pager" id="gscQueriesPager"></div>
            <div class="gsc-ctr-legend">
              <span>CTR vs. typical for its position:</span>
              <span class="gsc-ctr gsc-ctr-above"><span class="gsc-ctr-dot"></span>ahead</span>
              <span class="gsc-ctr"><span class="gsc-ctr-dot"></span>in line</span>
              <span class="gsc-ctr gsc-ctr-below"><span class="gsc-ctr-dot"></span>behind</span>
            </div>
          </div>
          <div class="col-panel">
            <h3>Top pages</h3>
            <div class="table-wrap"><table id="gscPagesTable" class="compact"></table></div>
            <div class="pager" id="gscPagesPager"></div>
          </div>
        </div>
      </section>"""

    panel_gsc_watchlist = """
      <section id="sec-gsc-watchlist">
        <div class="sec-head"><h2>Keyword watchlist</h2><div class="sec-head-actions"><span class="status" id="gscWatchStatus"></span><button type="button" class="watch-btn watch-btn-primary debug-only" id="gscWatchAdd">+ Add keyword</button><span class="watch-bulk-wrap debug-only"><button type="button" class="watch-btn" id="gscWatchBulkBtn" aria-expanded="false" aria-controls="gscWatchBulk">Bulk add</button>
          <div class="watch-pop" id="gscWatchBulk" role="dialog" aria-label="Bulk add keywords" hidden>
            <textarea id="gscWatchBulkText" rows="4" spellcheck="false" placeholder="hvac software&#10;manufacturing seo, industrial marketing, plant maintenance seo"></textarea>
            <p class="watch-hint">One keyword per line, or several separated by commas. End a keyword with * to count its variants too.</p>
            <div class="watch-pop-foot">
              <button type="button" class="watch-btn watch-btn-primary" id="gscWatchBulkAdd">Add rows</button>
              <button type="button" class="watch-btn" id="gscWatchBulkCancel">Cancel</button>
            </div>
          </div></span></div></div>
        <div class="table-wrap"><table id="gscWatchTable" class="compact watch-table"></table></div>
      </section>"""

    # Branded and Target queries used to sit side by side, which left each one
    # half a page wide and its trend chart unreadable. They are one tabbed card
    # now — the same .pnl-head/.pnl-tabs card the Website Analytics tab uses for
    # Pages / Landing Pages — so whichever group you are reading gets the full
    # width. The per-group Edit button (admin) rides in the card head and swaps
    # with the tab via data-pnl-for.
    panel_gsc_keywords = f"""
      <section id="card-gsc-kw">
        <div class="pnl-head">
          <div class="pnl-tabs" role="tablist" aria-label="Keyword group">
            <button type="button" class="pnl-tab" role="tab" id="tab-sec-gsc-branded" aria-selected="true" aria-controls="sec-gsc-branded" data-pnl="gsckw" data-pane="sec-gsc-branded">Branded queries <span class="muted" id="gscBrandedCount"></span></button>
            <button type="button" class="pnl-tab" role="tab" id="tab-sec-gsc-target" aria-selected="false" aria-controls="sec-gsc-target" data-pnl="gsckw" data-pane="sec-gsc-target">Target queries <span class="muted" id="gscTargetCount"></span></button>
          </div>
          <div class="pnl-head-actions">
            <span class="status" id="gscKwStatus"></span>
            <button type="button" class="kw-edit-btn debug-only" data-pnl-for="sec-gsc-branded" data-kw-edit="gscBrandedEditors" aria-expanded="false">Edit</button>
            <button type="button" class="kw-edit-btn debug-only" data-pnl-for="sec-gsc-target" data-kw-edit="gscTargetEditors" aria-expanded="false" hidden>Edit</button>
          </div>
        </div>
        <div class="pnl-pane" id="sec-gsc-branded" role="tabpanel" aria-labelledby="tab-sec-gsc-branded">
          <div class="kw-editors" id="gscBrandedEditors" hidden>
            <div class="tag-editor" id="gscBrandedTags"></div>
            <div class="tag-editor tag-editor-exclude" id="gscBrandedExcludeTags"></div>
          </div>
          <div class="table-wrap"><table id="gscBrandedTable" class="compact"></table></div>
          <div class="pager" id="gscBrandedPager"></div>
          <div class="chart-wrap" style="margin-top:12px">
            {_kw_trend_cap}
            <div class="chart-canvas-host" style="height:190px"><canvas id="gscBrandedTrendChart"></canvas></div>
          </div>
        </div>
        <div class="pnl-pane" id="sec-gsc-target" role="tabpanel" aria-labelledby="tab-sec-gsc-target" hidden>
          <div class="kw-editors" id="gscTargetEditors" hidden>
            <div class="tag-editor" id="gscTargetTags"></div>
            <div class="tag-editor tag-editor-exclude" id="gscTargetExcludeTags"></div>
          </div>
          <div class="table-wrap"><table id="gscTargetTable" class="compact"></table></div>
          <div class="pager" id="gscTargetPager"></div>
          <div class="chart-wrap" style="margin-top:12px">
            {_kw_trend_cap}
            <div class="chart-canvas-host" style="height:190px"><canvas id="gscTargetTrendChart"></canvas></div>
          </div>
        </div>
      </section>"""

    gsc_units: list[tuple[str, str]] = []
    if semrush_section_html:
        gsc_units.append(("semrush", semrush_section_html))
    gsc_units += [
        ("kpis", panel_gsc_kpis),
        ("tables", panel_gsc_tables),
        ("watchlist", panel_gsc_watchlist),
        ("keywords", panel_gsc_keywords),
    ]
    gsc_sections_html = _render_editable_panels(
        gsc_units,
        gsc_layout,
        titles=GSC_LAYOUT_CARDS,
        is_admin=session_is_admin,
    )
    gsc_edit_banner_html = _edit_banner_html(
        prefix="gsc",
        layout_api=_api_url(
            f"/api/clients/{api_client_key}/tabs/gsc/card-layout",
            access_key=access_key,
        ),
    )


    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{label}</title>
  {favicon_head_html()}
  <!-- Charts: Chart.js, vendored locally (served from /static/vendor) -->
  <script src="/static/vendor/chart.umd.min.js"></script>
  <!-- World country outline paths for the Demographics "Users by country" map -->
  <script src="/static/vendor/world-country-outlines.js"></script>
  <!-- Dashboard styles: composed from static/css/dashboard-*.css plus the CSS
       shared with other renderers, and served with a content digest so it can
       be cached immutably. See dashboard/assets.py. -->
  <link rel="stylesheet" href="{dashboard_css_url()}">
</head>
<body>
  <div class="app-shell {admin_class}" id="appShell">
    {sidebar_html}
    <div class="dash-main">

    <!-- Sticky filter header (full-bleed, flush to top) -->
    <div class="date-bar">
      <div class="date-bar-inner">
      <div class="date-bar-bottom">
        <div class="filter-group">
          <div class="ke-dropdown range-dd" id="rangeDropdown">
            <button type="button" class="ke-dd-toggle range-dd-toggle" id="rangeToggle" aria-haspopup="listbox" aria-expanded="false">
              <span class="dd-lead">
                <svg class="dd-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4"/><path d="M16 3v4"/><path d="M3 10h18"/></svg>
                <span class="sr-only">Date range:</span>
                <span id="rangeToggleLabel">{effective_default_label}</span>
              </span>
              <span class="ke-dd-caret">▾</span>
            </button>
            <div class="ke-dd-panel range-dd-panel" id="rangePanel" hidden>
              <div class="ke-dd-list range-dd-list" id="rangeList" role="listbox">
                {range_option_rows_html}
                <button type="button" class="range-opt range-custom-open" id="rangeCustomOpen" data-custom="1" aria-expanded="false" aria-controls="rangeCustom">Custom range…</button>
              </div>
              <div class="range-custom" id="rangeCustom" hidden>
                <label class="range-custom-field"><span>Start</span><input type="date" id="rangeCustomStart"></label>
                <label class="range-custom-field"><span>End</span><input type="date" id="rangeCustomEnd"></label>
                <button type="button" class="range-apply range-custom-apply" id="rangeCustomApply">Apply range</button>
                <span class="range-custom-err" id="rangeCustomErr" role="alert"></span>
              </div>
              {range_default_html}
            </div>
          </div>
        </div>
        <div class="filter-group" id="compareFilterGroup">
          <!-- Comparison picker: one control listing No comparison (the
               default) plus the two windows. The "vs" lead only makes sense
               once a window is chosen, so it hides itself on No comparison
               rather than reading "vs No comparison". -->
          <div class="ke-dropdown range-dd" id="compareDropdown">
            <button type="button" class="ke-dd-toggle range-dd-toggle" id="compareToggle" aria-haspopup="listbox" aria-expanded="false">
              <span class="dd-lead">
                <span class="dd-vs" id="compareVs" aria-hidden="true" hidden>vs</span>
                <span class="sr-only">Compare to:</span>
                <span id="compareToggleLabel">No comparison</span>
              </span>
              <span class="ke-dd-caret">▾</span>
            </button>
            <div class="ke-dd-panel range-dd-panel" id="comparePanel" hidden>
              <div class="ke-dd-list range-dd-list" id="compareList" role="listbox">
                {compare_option_rows_html}
              </div>
              <div class="range-dd-foot cmp-range-foot"><span class="range-default-status" id="compareRangeLabel"></span></div>
            </div>
          </div>
          <!-- Filled + unhidden by syncCompareNotice() when the selected
               comparison window starts before a connector's synced history
               (most often a previous-year comparison against a recently
               connected source) -- the cue that the warehouse needs a deeper
               backfill. It qualifies this one picker, so it rides next to it
               as a hover/focus tooltip rather than a page-wide banner. -->
          <button type="button" class="cmp-notice" id="compareNotice" aria-label="Comparison window warning" aria-describedby="compareNoticeTip" hidden>
            <span class="cmp-notice-icon" aria-hidden="true">&#9888;</span>
            <span class="cmp-notice-tip" id="compareNoticeTip" role="tooltip"></span>
          </button>
        </div>
        <div class="filter-group" id="keyEventFilterGroup" hidden>
          <span class="filter-label">Events</span>
          <div class="ke-dropdown" id="keyEventDropdown">
            <button type="button" class="ke-dd-toggle" id="keyEventToggle" aria-haspopup="listbox" aria-expanded="false">
              <span id="keyEventToggleLabel">All key events</span>
              <span class="ke-dd-caret">▾</span>
            </button>
            <div class="ke-dd-panel" id="keyEventPanel" hidden>
              <input type="text" class="ke-dd-search" id="keyEventSearch" placeholder="Search events…" autocomplete="off">
              <div class="ke-dd-list" id="keyEventList"></div>
              {key_event_default_html}
            </div>
          </div>
        </div>
        <div class="filter-group" id="explorerFilterBar" hidden></div>
      </div>
      </div>
    </div>

  <main>

    <!-- ===== OVERVIEW TAB ===== -->
    <div id="pane-overview" class="ov-editable" data-edit-pane="overview">
      {overview_edit_banner_html}
      {onboarding_html}
      {overview_summary_html}
    </div>

    <!-- ===== EXPLORER TAB ===== -->
    <div id="pane-explorer" class="ov-editable" data-edit-pane="explorer" hidden>
      {explorer_edit_banner_html}
      {explorer_sections_html}
    </div>

    <!-- ===== WEBSITE ANALYTICS TAB ===== -->
    <div id="pane-analytics" hidden>

      <div class="apf-bar" id="analyticsFilterBar" hidden>
        <div class="apf-scope" id="analyticsScopeNote" hidden></div>
        {analytics_path_filter_edit_html}
      </div>

      <section id="sec-avgduration">
        <div class="sec-head"><h2>Sessions &amp; engagement <span class="cmp-warn" id="avgDurCmpWarn" title="" hidden>&#9888;</span></h2><div class="sec-head-actions"><div class="chips seg" id="avgDurGranChips"><button type="button" class="chip" data-gran="daily">Daily</button><button type="button" class="chip active" data-gran="weekly">Weekly</button></div><span class="status" id="avgDurStatus"></span></div></div>
        <div class="cards metric-cards" id="avgDurCards" style="margin-bottom:12px"></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:200px"><canvas id="avgDurTrendChart"></canvas></div></div>
        <div class="cmp-legend" id="avgDurTrendLegend"></div>
      </section>

      <section id="card-pages">
        <div class="pnl-head">
          <div class="pnl-tabs" role="tablist" aria-label="Page breakdown">
            <button type="button" class="pnl-tab" role="tab" id="tab-sec-pages" aria-selected="true" aria-controls="sec-pages" data-pnl="pages" data-pane="sec-pages">Pages</button>
            <button type="button" class="pnl-tab" role="tab" id="tab-sec-landing" aria-selected="false" aria-controls="sec-landing" data-pnl="pages" data-pane="sec-landing">Landing Pages</button>
          </div>
          <span class="status" id="pagesStatus"></span>
          <span class="status" id="landingStatus" hidden></span>
        </div>
        <div class="pnl-pane" id="sec-pages" role="tabpanel" aria-labelledby="tab-sec-pages">
          <input class="page-search" id="pagesSearch" type="search" placeholder="Filter by path…" autocomplete="off">
          <div class="table-wrap"><table id="pagesTable" class="compact resizable"></table></div>
          <div class="pager" id="pagesPager"></div>
        </div>
        <div class="pnl-pane" id="sec-landing" role="tabpanel" aria-labelledby="tab-sec-landing" hidden>
          <input class="page-search" id="landingSearch" type="search" placeholder="Filter by path…" autocomplete="off">
          <div class="table-wrap"><table id="landingTable" class="compact resizable"></table></div>
          <div class="pager" id="landingPager"></div>
        </div>
      </section>

      <section id="card-acq">
        <div class="pnl-head">
          <div class="pnl-tabs" role="tablist" aria-label="Acquisition breakdown">
            <button type="button" class="pnl-tab" role="tab" id="tab-sec-traffic" aria-selected="true" aria-controls="sec-traffic" data-pnl="acq" data-pane="sec-traffic">Traffic acquisition</button>
            <button type="button" class="pnl-tab" role="tab" id="tab-sec-useracq" aria-selected="false" aria-controls="sec-useracq" data-pnl="acq" data-pane="sec-useracq">New user acquisition</button>
          </div>
          <span class="status" id="trafficAcqStatus"></span>
          <span class="status" id="userAcqStatus" hidden></span>
        </div>
        <div class="pnl-pane" id="sec-traffic" role="tabpanel" aria-labelledby="tab-sec-traffic">
          <div class="col-panel"><h3>By channel</h3><div id="channelBars"></div></div>
          <h3 class="subsec-h3">Top sources / medium</h3>
          <div class="table-wrap"><table id="sourcesTable" class="compact"></table></div>
          <div class="pager" id="sourcesPager"></div>
        </div>
        <div class="pnl-pane" id="sec-useracq" role="tabpanel" aria-labelledby="tab-sec-useracq" hidden>
          <div id="newVsReturning"></div>
          <div class="two-col" style="align-items:start">
            <div class="col-panel"><h3>By first channel</h3><div id="userAcqChannelBars"></div><div class="pager" id="userAcqChannelPager"></div></div>
            <div class="col-panel">
              <h3>By first source / medium</h3>
              <div class="table-wrap"><table id="userAcqSourceTable" class="compact"></table></div>
              <div class="pager" id="userAcqSourcePager"></div>
            </div>
          </div>
        </div>
      </section>

      <section id="sec-audience">
        <div class="sec-head"><h2>Audience</h2><span class="status" id="deviceStatus"></span></div>
        <div class="col-panel" style="max-width:420px"><h3>Device type</h3><div id="deviceBars"></div></div>
      </section>

      <section id="sec-demographics">
        <div class="sec-head"><h2 id="demoSectionTitle">Demographics</h2><span class="status" id="demoStatus"></span></div>
        <p class="chart-note" id="demoScopeNote" style="margin-top:0" hidden></p>
        <div class="two-col" style="align-items:start">
          <div class="col-panel">
            <div class="col-panel-head">
              <h3 id="stateMapTitle">Users by state</h3>
              <div class="chips seg" id="geoMapViewChips">
                <button type="button" class="chip active" data-view="state">State</button>
                <button type="button" class="chip" data-view="country">Country</button>
              </div>
            </div>
            <div id="stateMap" class="geo-map"></div>
          </div>
          <div class="col-panel">
            <div class="col-panel-head">
              <h3 id="citiesTableTitle">Top cities</h3>
              <div class="chips seg" id="geoTableViewChips">
                <button type="button" class="chip active" data-view="cities">Cities</button>
                <button type="button" class="chip" data-view="countries">Countries</button>
              </div>
            </div>
            <div class="table-wrap"><table id="citiesTable" class="compact"></table></div>
            <div class="geo-map-note" id="citiesTableNote" hidden></div>
          </div>
        </div>
        <div class="two-col" id="demoUserPanels" style="align-items:start; margin-top:14px">
          <div class="col-panel">
            <div class="col-panel-head"><h3>Age bracket</h3><label class="age-toggle"><input type="checkbox" id="ageUnknownToggle"> Show “unknown”</label></div>
            <div id="ageBars"></div>
          </div>
          <div class="col-panel"><h3>Gender</h3><div id="genderBars"></div></div>
        </div>
      </section>

    </div><!-- /pane-analytics -->

    <!-- ===== AI TRAFFIC TAB ===== -->
    <div id="pane-ai_traffic" hidden>
      <section id="sec-ai-trend">
        <div class="sec-head"><h2>AI traffic over time</h2><div class="sec-head-actions"><div class="chips seg" id="aiTrendGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><span class="status" id="aiTrendStatus"></span></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:260px"><canvas id="aiTrendChart"></canvas></div></div>
        <p class="chart-note">Sessions per day, stacked by referring AI assistant.</p>
      </section>
      <section id="sec-ai-sources">
        <div class="sec-head"><h2>AI traffic by source</h2><span class="status" id="aiTrafficStatus"></span></div>
        <p class="chart-note" style="margin-top:0">Website sessions referred by AI assistants (ChatGPT, Perplexity, Gemini, etc.) in this range.</p>
        <div class="table-wrap"><table id="aiSourcesTable" class="compact"></table></div>
      </section>
      <section id="sec-ai-pages">
        <div class="sec-head"><h2>Top landing pages from AI</h2><span class="status" id="aiPagesStatus"></span></div>
        <div class="filter-group" style="margin-bottom:10px"><span class="filter-label">AI source</span><div class="chips" id="aiPageSourceChips"></div></div>
        <input class="page-search" id="aiPagesSearch" type="search" placeholder="Filter by path…" autocomplete="off">
        <div class="table-wrap"><table id="aiPagesTable" class="compact"></table></div>
      </section>
    </div>

    <div id="pane-gsc" class="ov-editable" data-edit-pane="gsc" hidden>
      {gsc_edit_banner_html}
      {gsc_sections_html}
    </div><!-- /pane-gsc -->
    {pagespeed_pane_html}
    {google_business_pane_html}
  </main>
  {site_footer_html()}
    </div>
  </div>
  <div id="creativePreview" class="creative-preview" hidden>
    <div class="creative-preview-backdrop" data-close-preview></div>
    <div class="creative-preview-dialog" role="dialog" aria-modal="true" aria-label="Creative preview">
      <button type="button" class="creative-preview-close" data-close-preview aria-label="Close">&times;</button>
      <div class="creative-preview-body" id="creativePreviewBody"></div>
    </div>
  </div>
  <script>{dashboard_topbar_js()}</script>
  <script>
    // ---- Skeleton helpers ----
    const _SW=['50%','76%','38%','90%','62%','44%','70%'];
    function skelCards(n){{return Array.from({{length:n}},()=>`<div class="card" style="display:flex;flex-direction:column;gap:10px"><div class="skel" style="height:10px;width:52%"></div><div class="skel" style="height:24px;width:66%"></div></div>`).join('');}}
    function skelBars(n){{return Array.from({{length:n}},(_,i)=>`<div class="bar-row"><div class="skel" style="height:12px;width:${{_SW[i%_SW.length]}};flex:0 0 auto;min-width:60px"></div><div class="skel bar-track" style="height:7px"></div><div class="skel" style="height:12px;width:40px;flex-shrink:0"></div></div>`).join('');}}
    function skelTable(cols,rows){{const ths=Array.from({{length:cols}},()=>`<th></th>`).join('');const ws=['55%','80%','40%','92%','65%'];const trs=Array.from({{length:rows}},(_,i)=>`<tr>${{Array.from({{length:cols}},(_,j)=>`<td><div class="skel" style="height:12px;width:${{ws[(i*cols+j)%ws.length]}}"></div></td>`).join('')}}</tr>`).join('');return`<thead><tr>${{ths}}</tr></thead><tbody>${{trs}}</tbody>`;}}
    function skelChart(svgId,cssClass){{const svg=document.getElementById(svgId);if(!svg)return;const d=document.createElement('div');d.id='sk_'+svgId;d.className='skel skel-chart '+cssClass;svg.parentElement.insertBefore(d,svg);svg.style.visibility='hidden';}}
    function clearSkelChart(svgId){{const s=document.getElementById('sk_'+svgId);if(s)s.remove();const svg=document.getElementById(svgId);if(svg)svg.style.visibility='';}}

    const HAS_PAID_ADS = {'true' if has_paid_ads else 'false'};
    // The effective user is an admin (an admin using "view as" is NOT — the
    // whole point is that they see what the target user sees). Gates the three
    // interpretation features that are still in preview: metric goals, peer
    // benchmarks, and the data-freshness chip. The matching API routes enforce
    // this server-side too; this flag only avoids requests that would 403.
    const IS_ADMIN = {'true' if session_is_admin else 'false'};
    // Peer benchmarks are opt-in per client (Settings → Peer benchmarks) and off
    // by default, because the comparison only means something once the account's
    // industry tags are right. Off means the cards never ask for it; the API
    // enforces the same setting, so this only avoids a request that would 404.
    const SHOW_BENCHMARKS = {'true' if show_benchmarks else 'false'};

    // ---- API constants ----
    const SUMMARY_API          = "{_aurl(f'/api/clients/{api_client_key}/summary')}";
    const HEALTH_API           = "{_aurl(f'/api/clients/{api_client_key}/marketing/health')}";
    const GOALS_API            = "{_aurl(f'/api/clients/{api_client_key}/goals')}";
    const BENCHMARKS_API       = "{_aurl(f'/api/clients/{api_client_key}/benchmarks')}";
    const ANNOTATIONS_API      = "{_aurl(f'/dashboard/{client_slug}/annotations')}";
    const EXPLORER_API         = "{_aurl(f'/api/clients/{api_client_key}/google-ads/explorer')}";
    const GOOGLE_ADS_KEYWORDS_API = "{_aurl(f'/api/clients/{api_client_key}/google-ads/keywords')}";
    const GOOGLE_ADS_DEMOGRAPHICS_API = "{_aurl(f'/api/clients/{api_client_key}/google-ads/demographics')}";
    const LINKEDIN_EXPLORER_API= "{_aurl(f'/api/clients/{api_client_key}/linkedin/explorer')}";
    const LINKEDIN_DEMOGRAPHICS_API = "{_aurl(f'/api/clients/{api_client_key}/linkedin/demographics')}";
    const META_EXPLORER_API    = "{_aurl(f'/api/clients/{api_client_key}/meta/explorer')}";
    const MICROSOFT_EXPLORER_API= "{_aurl(f'/api/clients/{api_client_key}/microsoft-ads/explorer')}";
    // Platform conversion actions -- the split behind the Conv. column's selector.
    const GOOGLE_CONV_ACTIONS_API   = "{_aurl(f'/api/clients/{api_client_key}/google-ads/conversion-actions')}";
    const META_CONV_ACTIONS_API     = "{_aurl(f'/api/clients/{api_client_key}/meta/conversion-actions')}";
    const MICROSOFT_CONV_ACTIONS_API= "{_aurl(f'/api/clients/{api_client_key}/microsoft-ads/conversion-actions')}";
    const META_VERIFIED_API    = "{_aurl(f'/api/clients/{api_client_key}/meta/verified-conversions')}";
    const GOOGLE_VERIFIED_API  = "{_aurl(f'/api/clients/{api_client_key}/google-ads/verified-conversions')}";
    const LINKEDIN_VERIFIED_API= "{_aurl(f'/api/clients/{api_client_key}/linkedin/verified-conversions')}";
    const MICROSOFT_VERIFIED_API= "{_aurl(f'/api/clients/{api_client_key}/microsoft/verified-conversions')}";
    // Campaign explorer "metrics over time" chart: daily campaign-grain totals
    // (base metrics) and daily verified-conversion counts, one endpoint per
    // platform for each. See renderExplorerTrend().
    const EXPLORER_TREND_API           = "{_aurl(f'/api/clients/{api_client_key}/google-ads/explorer-trend')}";
    const MICROSOFT_EXPLORER_TREND_API = "{_aurl(f'/api/clients/{api_client_key}/microsoft-ads/explorer-trend')}";
    const LINKEDIN_EXPLORER_TREND_API  = "{_aurl(f'/api/clients/{api_client_key}/linkedin/explorer-trend')}";
    const META_EXPLORER_TREND_API      = "{_aurl(f'/api/clients/{api_client_key}/meta/explorer-trend')}";
    const GOOGLE_VERIFIED_TREND_API    = "{_aurl(f'/api/clients/{api_client_key}/google-ads/verified-conversions-trend')}";
    const LINKEDIN_VERIFIED_TREND_API  = "{_aurl(f'/api/clients/{api_client_key}/linkedin/verified-conversions-trend')}";
    const MICROSOFT_VERIFIED_TREND_API = "{_aurl(f'/api/clients/{api_client_key}/microsoft/verified-conversions-trend')}";
    const META_VERIFIED_TREND_API      = "{_aurl(f'/api/clients/{api_client_key}/meta/verified-conversions-trend')}";
    const BACKFILL_API         = "{_aurl(f'/api/clients/{api_client_key}/backfill-linkedin')}";
    const PAGES_TOP_API        = "{_aurl(f'/api/clients/{api_client_key}/pages/top')}";
    const PAGES_SOURCES_API    = "{_aurl(f'/api/clients/{api_client_key}/pages/sources')}";
    const AI_TRAFFIC_DAILY_API = "{_aurl(f'/api/clients/{api_client_key}/ai-traffic/daily')}";
    const TOP_PAGES_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/top-key-events')}";
    const TRAFFIC_ACQ_API      = "{_aurl(f'/api/clients/{api_client_key}/pages/traffic-acquisition')}";
    const DEVICE_SPLIT_API     = "{_aurl(f'/api/clients/{api_client_key}/pages/device-split')}";
    const LANDING_PAGES_API    = "{_aurl(f'/api/clients/{api_client_key}/pages/landing')}";
    const USER_ACQ_API         = "{_aurl(f'/api/clients/{api_client_key}/analytics/user-acquisition')}";
    const DEMOGRAPHICS_API     = "{_aurl(f'/api/clients/{api_client_key}/analytics/demographics')}";
    const SESSION_DURATION_API = "{_aurl(f'/api/clients/{api_client_key}/analytics/session-duration')}";
    const GSC_API              = "{_aurl(f'/api/clients/{api_client_key}/gsc/summary')}";
    const SEMRUSH_API          = "{_aurl(f'/api/clients/{api_client_key}/semrush/summary')}";
    const PAGESPEED_API        = "{_aurl(f'/api/clients/{api_client_key}/pagespeed/summary')}";
    const PAGESPEED_TARGETS_API= "{_aurl(f'/api/clients/{api_client_key}/pagespeed/targets')}";
    const PAGESPEED_TARGETS    = {pagespeed_targets_json};
    const PAGESPEED_STRATEGIES = {pagespeed_strategies_json};
    const GOOGLE_BUSINESS_API  = "{_aurl(f'/api/clients/{api_client_key}/google-business/summary')}";
    const GSC_KEYWORD_CONFIG_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/keyword-config')}";
    const GSC_KEYWORD_MATCHES_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/keyword-matches')}";
    const GSC_BRANDED_ROOTS = {_json_script([s.strip() for s in gsc_branded_roots.splitlines() if s.strip()])};
    const GSC_TARGET_KEYWORDS = {_json_script([s.strip() for s in gsc_target_keywords.splitlines() if s.strip()])};
    const GSC_BRANDED_EXCLUDE = {_json_script([s.strip() for s in gsc_branded_exclude.splitlines() if s.strip()])};
    const GSC_TARGET_EXCLUDE = {_json_script([s.strip() for s in gsc_target_exclude.splitlines() if s.strip()])};
    const GSC_WATCHLIST_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/watchlist')}";
    const GSC_WATCHLIST_CONFIG_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/watchlist-config')}";
    // [{{kw, page}}] -- the watched keyword and the page it was written for.
    const GSC_WATCH_ITEMS = {_json_script(parse_gsc_watchlist(gsc_watch_keywords))};
    const GSC_BRANDED_RAW = {_json_script(gsc_branded_roots)};
    const GSC_TARGET_RAW = {_json_script(gsc_target_keywords)};
    const LANDING_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/landing-events')}";
    const TRAFFIC_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/traffic-key-events')}";
    const USER_ACQ_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/analytics/user-acq-key-events')}";
    const GA4_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/ga4/key-events')}";
    const GA4_KEY_EVENTS_SAVED = {_json_script([s.strip() for s in ga4_key_events.splitlines() if s.strip()])};
    // Website Analytics page-path scope: patterns the admin set (empty = whole
    // site). When non-empty, the page-path panels come back pre-scoped from the
    // server; the JS hides the site-wide panels and shows a scope indicator.
    const ANALYTICS_PATH_FILTER = {analytics_path_filter_json};
    const ANALYTICS_PATH_FILTER_API = "{_aurl(f'/api/clients/{api_client_key}/analytics/page-path-filter')}";
    // Landing date-range preset (admin-chosen client default, else last_30) and
    // the admin-only save endpoint for the "Make default" control.
    const DEFAULT_DATE_PRESET = {default_date_preset_json};
    // The raw stored default ('' when none) and preset→label map, used by the
    // Range dropdown to seed the "Make default" checkbox and the toggle label.
    let STORED_DEFAULT_PRESET = {stored_default_preset_json};
    const DATE_PRESET_LABELS = {date_preset_labels_json};
    const DEFAULT_DATE_RANGE_API = "{_aurl(f'/api/clients/{api_client_key}/default-date-range')}";
    // Comparisons the picker offers, its default, and the localStorage key the
    // viewer's choice is remembered under (per client, per browser -- a reading
    // preference, unlike the admin-set client default range).
    const COMPARE_MODE_LABELS = {compare_mode_labels_json};
    const COMPARE_DEFAULT_MODE = {compare_default_mode_json};
    const COMPARE_MODE_STORAGE_KEY = 'sf.compareMode.{client_slug}';
    // Written by the switch this picker replaced. Read once, so someone who had
    // comparison switched on comes back to the window they were reading.
    const COMPARE_ON_LEGACY_KEY = 'sf.compareOn.{client_slug}';
    // A move smaller than this reads as steady: it still shows its arrow and
    // percentage, but in muted grey rather than red or green. Colour is the
    // page's loudest signal, so it is spent only on movement worth acting on.
    const MEANINGFUL_DELTA_PCT = 10;
    // Campaign Explorer allowlist (campaign names the client may see; empty = all)
    // and the admin-only endpoint that saves it from the "Campaigns" picker.
    const EXPLORER_CAMPAIGN_ALLOWLIST = {explorer_campaign_allowlist_json};
    const EXPLORER_CAMPAIGNS_API = "{_aurl(f'/api/clients/{api_client_key}/explorer/campaigns')}";
    // Admin endpoint behind the Explorer's Budget tracking panel Hide/Show (form
    // POST show=1/0). Uses client_slug (a /dashboard route), not api_client_key.
    const BUDGET_VISIBILITY_API = "{_aurl(f'/dashboard/{client_slug}/budget-visibility')}";
    // Per-request values the cached script reads. Everything else lives in
    // /assets/dashboard-<digest>.js — see dashboard/assets.py.
    const EXPLORER_FILTER_GROUPS = {explorer_filter_groups_json};
    const EXPLORER_FILTERS_API = "{_aurl(f'/api/clients/{api_client_key}/explorer/filters')}";
    const KE_STORAGE_KEY = 'ce_verified_ke_{api_client_key}';
    const CONV_STORAGE_KEY = 'ce_conv_action_{api_client_key}';
    let currentStart='{start.isoformat()}', currentEnd='{end.isoformat()}';
  </script>
  <script src="{dashboard_js_url()}"></script>
  {budget_scripts}
  {notes_widget_html}
</body>
</html>"""
