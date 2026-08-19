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

from dashboard.renderers.base_layout import (
    SIDEBAR_CSS,
    dashboard_topbar_js,
    favicon_head_html,
    dashboard_sidebar_view_nav_html,
    render_sidebar,
    platform_nav_flags,
    site_footer_html,
)
from dashboard.renderers import pagespeed_renderer


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


# Campaign Explorer filter chips, shown when a client hasn't configured their
# own. These reproduce Nixon's long-standing chips; every other client should
# override them from Settings → Campaign explorer filters. Match semantics are
# case-insensitive substring (see explorerRowMatches in the page JS), so the
# region phrases here are slightly broader than the old \bTX\b word-boundary
# regex — acceptable for an opt-in fallback.
DEFAULT_EXPLORER_FILTERS: list[dict] = [
    {
        "id": "g0",
        "label": "Product",
        "chips": [
            {"label": "Apparel", "phrases": ["apparel"]},
            {"label": "Scrubs", "phrases": ["scrub"]},
            {"label": "Linens", "phrases": ["linen"]},
        ],
    },
    {
        "id": "g1",
        "label": "Region",
        "chips": [
            {"label": "TX", "phrases": ["tx"]},
            {"label": "FL", "phrases": ["fl"]},
            {"label": "MA", "phrases": ["ma"]},
        ],
    },
]


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
    if nothing valid was defined (caller falls back to DEFAULT_EXPLORER_FILTERS).
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


def resolve_explorer_filters(text: str | None) -> list[dict]:
    """Client config if present, otherwise the built-in default chips."""
    parsed = parse_explorer_filters(text)
    return parsed or DEFAULT_EXPLORER_FILTERS


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
    with a growth sparkline, plus a "Net new followers" card with the
    organic/paid split over the window. Mirrors the MQL tracker's card markup so
    it inherits the same Overview styling. Returns "" when nothing has synced yet
    so the whole card drops rather than showing an empty shell.
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
        if n > 0:
            return f'<span class="mql-delta up">▲ {_int(n)} in {window_days} days</span>'
        if n < 0:
            return f'<span class="mql-delta down">▼ {_int(abs(n))} in {window_days} days</span>'
        return f'<span class="mql-delta flat">No change in {window_days} days</span>'

    cards: list[str] = []
    if total_followers:
        cards.append(
            '<div class="card"><div class="card-title">Total followers</div>'
            f'<div class="card-value">{_int(total_followers)}</div>'
            f'{_li_follower_sparkline(series)}'
            f'<div class="card-foot">{_delta(net_gain)}</div></div>'
        )
    split = (
        f'<span class="mql-sub">{_int(organic)} organic · {_int(paid)} paid</span>'
        if (organic or paid)
        else ""
    )
    cards.append(
        '<div class="card"><div class="card-title">Net new followers</div>'
        f'<div class="card-value">{_int(net_gain)}</div>'
        f'<div class="card-foot">{split}</div></div>'
    )

    # Third column: which tabs of the company page people actually landed on,
    # ranked. Drops out for pages whose section split hasn't synced (the column
    # set post-dates v1 of the page table) so the card falls back to two cards.
    section_bars = _li_section_bars(getattr(report, "page_sections", None))
    if section_bars:
        page_views = int(getattr(report, "total_page_views", 0) or 0)
        foot = (
            f'<span class="mql-sub">{_int(page_views)} page views '
            f'in {window_days} days</span>'
            if page_views else ""
        )
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
        '<section class="ov-panel">'
        '<div class="sec-head"><h2>'
        '<span class="mql-dot" style="background:#0A66C2"></span>LinkedIn Followers</h2>'
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
    # Campaign Explorer filter chips: client config if set, else Nixon defaults.
    # Injected as JSON for the page JS to build the chip rows + match campaigns.
    # Escape "<" so a chip label can't break out of the <script> block.
    explorer_filter_groups_json = json.dumps(
        resolve_explorer_filters(explorer_filters_cfg)
    ).replace("<", "\\u003c")

    # Landing date-range preset: the admin-chosen client default, else last_30.
    # Validated against the known presets so a stale value can't wedge the picker.
    _DATE_PRESETS = (
        "last_7", "last_30", "last_90", "last_365",
        "this_week", "last_week", "this_month", "last_month",
        "this_quarter", "last_quarter",
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
    ]
    date_preset_labels_json = json.dumps(dict(_DATE_PRESET_LABELS))
    # Comparison-period modes offered next to the Range picker. "Previous
    # period" (the equivalent window immediately before the selected range) is
    # the long-standing behaviour and stays the default; "Previous year" shifts
    # the selected range back 12 months.
    _COMPARE_MODES = [
        ("prev_period", "Previous period"),
        ("prev_year", "Previous year"),
    ]
    compare_mode_labels_json = json.dumps(dict(_COMPARE_MODES))
    compare_option_rows_html = "".join(
        f'<button type="button" class="range-opt{" active" if v == "prev_period" else ""}" role="option" data-cmp="{v}">{lbl}</button>'
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

    # Site Performance (PageSpeed Insights) tab — HTML/CSS/JS injected below. The
    # pane is always emitted (like Search Console); the sidebar nav button is what
    # gates on the pagespeed connector (see base_layout.platform_nav_flags).
    pagespeed_pane_html = pagespeed_renderer.pane_html()
    pagespeed_pane_css = pagespeed_renderer.pane_css()
    pagespeed_pane_js = pagespeed_renderer.pane_js()

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
    budget_css = _budget_tracker.css() if show_budget else ""
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

    panel_website = f"""
      <section class="ov-panel">
        <div class="sec-head"><h2>Website analytics</h2><div class="ov-actions"><span class="status" id="ovSessionsStatus"></span><div class="chips seg" id="ovSessionsGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><button type="button" class="ov-more" aria-label="See more" data-goto="analytics"><svg class="ov-more-arrow" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15"/><path d="M13 5.5 19.5 12 13 18.5"/></svg></button></div></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:220px"><canvas id="ovSessionsTrend"></canvas></div></div>
        <div class="cmp-legend" id="ovSessionsLegend"></div>
      </section>"""

    panel_ai = f"""
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

    panel_pagespeed = f"""
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
        <div class="sec-head"><h2>Campaign explorer</h2><div class="sec-head-actions">{platform_chips_explorer_html}{explorer_campaigns_edit_html}{explorer_filters_edit_html}<span class="status" id="explorerStatus"></span></div></div>
        <div class="cards" id="explorerSummaryCards" style="margin-bottom:14px"></div>
        <!-- Filter groups (Product / Region / Business line …) live in the sticky
             top bar (#explorerFilterBar) as dropdowns; built by buildExplorerFilters()
             from the client-configured chip rules; see EXPLORER_FILTER_GROUPS. -->
        <div class="table-wrap"><table id="explorerTable"></table></div>
      </section>"""

    panel_keywords = """
      <section id="sec-keywords" style="display:none">
        <div class="sec-head"><h2>Keyword Performance</h2><span class="status" id="keywordStatus"></span></div>
        <div class="kw-insight" id="keywordInsight" hidden></div>
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
            <span class="status" id="lidemoStatus"></span>
          </div>
        </div>
        <div class="pnl-tabs lid-tabs" role="tablist" aria-label="Demographic breakdown" id="lidemoTabs"></div>
        <div class="table-wrap"><table id="lidemoTable" class="compact"></table></div>
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
        <p class="gd-note" id="gdemoNote"></p>
      </section>"""

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
  <style>
    :root {{ --bg:#eef2f7; --card:#fff; --line:#e2e8f0; --line-soft:#eff3f8; --navy:#0a2540; --blue:#1769aa; --accent:#1d6fd0; --muted:#6b7a90; --bad:#b42318; --ok:#0a7f3f; --sidebar-from:#0a2540; --sidebar-to:#123456; --radius:14px; --radius-sm:9px; --shadow:0 1px 2px rgba(16,33,67,.04), 0 4px 16px rgba(16,33,67,.05); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:#102033; -webkit-font-smoothing:antialiased; }}
    /* SIDEBAR_CSS (shared) covers .dash-sidebar* only — the flex-container
       rules that lay the sidebar and content side by side normally live in
       render_client_shell_page's own style block, which this standalone page
       doesn't use, so they're declared explicitly here instead. */
    .app-shell {{ display: flex; flex-direction: row; min-height: 100vh; }}
    .dash-main {{ flex: 1 1 auto; min-width: 0; }}
    {SIDEBAR_CSS}
    main {{ max-width:1320px; margin:0 auto; padding:20px 28px 56px; }}
    h2 {{ margin:0; color:var(--navy); font-size:1.05rem; font-weight:750; letter-spacing:-.005em; }}
    p {{ margin:6px 0 0; color:var(--muted); }}
    /* ---- Date bar (sticky) ---- */
    /* Full-bleed sticky filter header — flush to the very top of the content
       area (no gap above), spanning the full width of the main column, with an
       inner wrapper that keeps the chips aligned to the same 1320px/28px grid
       as the cards below. A hairline border + soft shadow separate it from the
       scrolling content. */
    .date-bar {{ position:sticky; top:0; z-index:50; background:var(--card); border-bottom:1px solid var(--line); box-shadow:0 1px 0 rgba(16,33,67,.04), 0 6px 16px -12px rgba(16,33,67,.28); }}
    .date-bar[hidden] {{ display:none; }}
    .date-bar-inner {{ max-width:1320px; margin:0 auto; padding:13px 28px; display:flex; flex-direction:column; gap:10px; }}
    .date-bar-top {{ display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }}
    .date-bar-bottom {{ display:flex; flex-wrap:wrap; gap:20px; align-items:center; }}
    label {{ display:grid; gap:5px; color:var(--muted); font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; }}
    input[type=date] {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:8px 11px; font:inherit; font-size:.88rem; background:#fff; color:#102033; }}
    input[type=date]:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    button.primary {{ border:0; border-radius:var(--radius-sm); padding:9px 16px; background:var(--accent); color:#fff; font-weight:700; font-size:.88rem; cursor:pointer; box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    button.primary:hover {{ background:#1a62b8; }}
    button.primary:disabled {{ opacity:.55; cursor:default; }}
    .filter-group {{ display:flex; align-items:center; gap:8px; }}
    /* `hidden` attr must win over the display:flex above — used to scope groups
       to the tabs they apply to (Events, the Explorer filter bar). */
    .filter-group[hidden] {{ display:none; }}
    .filter-label {{ color:var(--muted); font-size:.7rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }}
    .sr-only {{ position:absolute; width:1px; height:1px; margin:-1px; padding:0; border:0; overflow:hidden; clip:rect(0 0 0 0); clip-path:inset(50%); white-space:nowrap; }}
    /* The Range and Compare pickers carry their own caption inside the field —
       a calendar glyph on Range, a "vs" on Compare — instead of an uppercase
       label beside it. That halves the width each control needs, which is what
       keeps both on one line on a phone. Screen readers get the caption from
       the .sr-only spans. */
    .dd-lead {{ display:inline-flex; align-items:center; gap:7px; min-width:0; overflow:hidden; }}
    .dd-lead > span:not(.sr-only) {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .dd-icon {{ width:15px; height:15px; flex:none; color:var(--muted); }}
    .dd-vs {{ flex:none; color:var(--muted); font-weight:700; }}
    /* Range picker: a custom dropdown so the admin "Make default" + Apply
       controls live inside the panel, under the preset list. Reuses the .ke-dd-*
       base styling (toggle/panel/caret); these tune sizing + the option rows. */
    .range-dd-toggle {{ min-width:150px; }}
    .range-dd-panel {{ width:220px; }}
    /* Two classes, so this beats .ke-dd-list's 260px cap regardless of source
       order -- the preset list is short and should never scroll internally. */
    .range-dd-panel .range-dd-list {{ max-height:none; }}
    .range-opt {{ display:block; width:100%; text-align:left; border:0; background:none; border-radius:6px; padding:7px 9px; font:inherit; font-size:.82rem; font-weight:600; color:var(--navy); cursor:pointer; }}
    .range-opt:hover {{ background:#f4f8fd; }}
    .range-opt.active {{ background:#eaf2fd; color:var(--accent); }}
    /* "Custom range…" row + the start/end form it reveals inside the panel.
       The generic label rule (grid, uppercase caption) is exactly the stacked
       caption-over-input layout wanted here, so only the input is retuned. */
    .range-custom-open {{ border-top:1px solid var(--line-soft); border-radius:0 0 6px 6px; margin-top:2px; padding-top:8px; }}
    .range-custom {{ display:grid; gap:7px; margin-top:8px; padding-top:8px; border-top:1px solid var(--line-soft); }}
    .range-custom[hidden] {{ display:none; }}
    .range-custom .range-custom-field {{ gap:3px; font-size:.66rem; }}
    .range-custom input[type=date] {{ width:100%; padding:5px 7px; font-size:.78rem; }}
    .range-custom-apply {{ justify-self:start; }}
    .range-custom-err {{ font-size:.72rem; font-weight:600; color:var(--bad); }}
    .range-custom-err:empty {{ display:none; }}
    /* Admin footer inside the Range dropdown (checkbox + Apply). The generic
       label rule above forces grid/uppercase, so reset to an inline checkbox. */
    .range-dd-foot {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px; padding-top:8px; border-top:1px solid var(--line-soft); }}
    .range-default {{ display:inline-flex; align-items:center; gap:5px; margin:0; text-transform:none; letter-spacing:0; font-size:.74rem; font-weight:700; color:var(--muted); cursor:pointer; white-space:nowrap; }}
    .range-default input {{ margin:0; cursor:pointer; accent-color:var(--accent); }}
    .range-apply {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:8px; padding:5px 12px; font:inherit; font-size:.76rem; font-weight:700; cursor:pointer; transition:background .15s,border-color .15s; }}
    .range-apply:hover:not(:disabled) {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .range-apply:disabled {{ opacity:.55; cursor:default; }}
    .range-default-status {{ flex-basis:100%; font-size:.72rem; font-weight:600; text-transform:none; letter-spacing:0; color:var(--muted); }}
    .range-default-status.err {{ color:var(--bad); }}
    .range-default-status.ok {{ color:#178a4c; }}
    /* Compare picker: same dropdown shell as Range, with a footer that spells
       out the resolved comparison dates instead of the admin default controls. */
    .cmp-range-foot {{ margin-top:6px; }}
    .cmp-range-foot .range-default-status {{ color:var(--muted); }}
    /* Backfill warning: the comparison window reaches back past a source's
       synced history, so its deltas are incomplete. A badge beside the Compare
       picker — the control it qualifies — with the detail in a hover/focus
       tooltip, so the filter bar keeps its one-line height. */
    .cmp-notice {{ position:relative; display:inline-flex; align-items:center; justify-content:center; flex:none; width:22px; height:22px; padding:0; border:1px solid #f0d9a0; border-radius:50%; background:#fdf7e8; color:#a9760a; cursor:help; transition:background .12s, border-color .12s; }}
    .cmp-notice[hidden] {{ display:none; }}
    .cmp-notice:hover, .cmp-notice:focus-visible {{ background:#fbeeca; border-color:#e0bf6d; }}
    .cmp-notice-icon {{ font-size:.82rem; line-height:1; }}
    .cmp-notice-tip {{ position:absolute; z-index:60; top:calc(100% + 8px); left:50%; transform:translateX(-50%); width:min(340px, 72vw); border:1px solid #f0d9a0; background:#fdf7e8; color:#7a5806; border-radius:var(--radius-sm); box-shadow:var(--shadow); padding:9px 11px; font-size:.78rem; font-weight:600; line-height:1.45; text-align:left; text-transform:none; letter-spacing:0; white-space:normal; visibility:hidden; opacity:0; transition:opacity .12s; pointer-events:none; }}
    .cmp-notice:hover .cmp-notice-tip, .cmp-notice:focus-visible .cmp-notice-tip {{ visibility:visible; opacity:1; }}
    .cmp-notice-tip strong {{ font-weight:800; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:5px; }}
    .chip {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:999px; padding:4px 12px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s, color .12s; }}
    .chip:hover {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .chip.active {{ background:var(--navy); color:#fff; border-color:var(--navy); }}
    .chip.active:hover {{ background:#0d2c4d; }}
    /* Compact segmented toggle (e.g. Daily/Weekly) that sits on the card title line. */
    .chips.seg {{ gap:0; flex-wrap:nowrap; padding:2px; background:#f1f4f8; border:1px solid var(--line); border-radius:8px; }}
    .chips.seg .chip {{ border:0; border-radius:6px; background:none; padding:3px 12px; font-size:.76rem; color:var(--muted); }}
    .chips.seg .chip:hover {{ background:#fff; color:var(--navy); }}
    .chips.seg .chip.active {{ background:#fff; color:var(--navy); border-color:transparent; box-shadow:0 1px 2px rgba(16,33,67,.14); }}
    .chips.seg .chip.active:hover {{ background:#fff; }}
    /* A filter that belongs to one card rather than the page sits in that
       card's head (Platform on Paid summary / Campaign explorer), sized down a
       notch so it reads as part of the heading row. */
    .card-filter {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
    .card-filter .filter-label {{ font-size:.64rem; }}
    .chips.platform-chips .chip {{ padding:3px 10px; font-size:.75rem; }}
    /* ---- Key-event searchable dropdown ---- */
    .ke-dropdown {{ position:relative; display:inline-block; }}
    .ke-dd-toggle {{ display:inline-flex; align-items:center; gap:8px; min-width:190px; justify-content:space-between; border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:var(--radius-sm); padding:6px 12px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:border-color .12s; }}
    .ke-dd-toggle:hover {{ border-color:#b9c8dc; }}
    .ke-dropdown.open .ke-dd-toggle {{ border-color:var(--accent); }}
    .ke-dd-caret {{ color:var(--muted); font-size:.7rem; transition:transform .12s; }}
    .ke-dropdown.open .ke-dd-caret {{ transform:rotate(180deg); }}
    .ke-dd-panel {{ position:absolute; z-index:30; top:calc(100% + 4px); left:0; width:280px; background:#fff; border:1px solid var(--line); border-radius:var(--radius-sm); box-shadow:var(--shadow); padding:8px; }}
    .ke-dd-search {{ width:100%; box-sizing:border-box; border:1px solid var(--line); border-radius:6px; padding:7px 10px; font:inherit; font-size:.82rem; color:var(--navy); margin-bottom:6px; }}
    .ke-dd-search:focus {{ outline:none; border-color:var(--accent); }}
    .ke-dd-list {{ max-height:260px; overflow-y:auto; display:flex; flex-direction:column; gap:1px; }}
    .ke-dd-option {{ display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:6px; cursor:pointer; font-size:.82rem; font-weight:600; color:var(--navy); text-transform:none; letter-spacing:0; }}
    .ke-dd-option:hover {{ background:#f4f8fd; }}
    .ke-dd-option.active {{ background:#eaf2fd; }}
    .ke-dd-option input {{ margin:0; accent-color:var(--accent); cursor:pointer; }}
    .ke-dd-name {{ flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .ke-dd-count {{ color:var(--muted); font-size:.74rem; font-weight:600; }}
    .ke-dd-empty {{ color:var(--muted); font-size:.8rem; padding:14px 8px; text-align:center; }}
    /* ---- Timeline annotation markers ----
       The pin sits on the plot's top edge, small enough to read as a tick rather
       than as data. An internal-only marker is hollow so an agency user can tell
       at a glance which of these the client is also seeing. */
    .anno-host {{ position:relative; }}
    .anno-layer {{ position:absolute; inset:0; pointer-events:none; }}
    .anno-pin {{ position:absolute; top:0; width:11px; height:11px; margin-left:-5.5px; padding:0; border:1.5px solid var(--anno-color,#64748b); border-radius:50%; background:var(--anno-color,#64748b); box-shadow:0 0 0 2px var(--card); cursor:default; pointer-events:auto; }}
    .anno-pin.internal {{ background:var(--card); }}
    .anno-pin:hover, .anno-pin:focus-visible {{ transform:scale(1.28); outline:none; }}
    .anno-pin:focus-visible {{ box-shadow:0 0 0 2px var(--card), 0 0 0 4px #bcd4f0; }}

    /* ---- Explorer filter dropdowns (sticky bar) ---- */
    #explorerFilterBar {{ gap:8px; flex-wrap:wrap; }}
    .expl-dd .ke-dd-toggle {{ min-width:0; }}
    .ke-dd-toggle.has-active {{ border-color:var(--accent); color:var(--accent); }}
    /* ---- Sections ---- */
    section {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:18px 20px 20px; margin-bottom:16px; box-shadow:var(--shadow); }}
    /* ---- First-run onboarding card ---- */
    .onboarding-card {{ text-align:center; padding:40px 24px; }}
    .onboarding-icon {{ width:52px; height:52px; margin:0 auto 14px; border-radius:14px; background:#eaf2fd; color:var(--accent); display:flex; align-items:center; justify-content:center; }}
    .onboarding-icon svg {{ width:26px; height:26px; }}
    .onboarding-card h2 {{ font-size:1.15rem; margin:0 0 6px; }}
    .onboarding-card p {{ max-width:440px; margin:0 auto 18px; color:var(--muted); font-size:.9rem; line-height:1.5; }}
    .onboarding-cta {{ display:inline-block; background:var(--accent); color:#fff; text-decoration:none; font-weight:700; font-size:.9rem; padding:10px 20px; border-radius:var(--radius-sm); box-shadow:0 1px 2px rgba(16,33,67,.12); transition:background .15s; }}
    .onboarding-cta:hover {{ background:#1a62b8; }}
    /* ---- Step-by-step onboarding checklist ---- */
    .onboarding-steps {{ text-align:left; padding:28px 26px 26px; }}
    .onboarding-steps .onboarding-icon {{ margin:0 0 14px; }}
    .onboarding-steps h2 {{ text-align:left; }}
    .onboarding-steps > p {{ text-align:left; max-width:600px; margin:0 0 22px; }}
    .ob-steps {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:20px; }}
    .ob-step {{ display:flex; gap:14px; }}
    .ob-step-num {{ flex:0 0 28px; width:28px; height:28px; border-radius:50%; background:#eaf2fd; color:var(--accent); font-weight:700; font-size:.86rem; display:flex; align-items:center; justify-content:center; }}
    .ob-step-body {{ flex:1; min-width:0; }}
    .ob-step-title {{ font-weight:700; font-size:.96rem; margin-bottom:3px; }}
    .ob-step-desc {{ color:var(--muted); font-size:.86rem; line-height:1.5; margin-bottom:9px; }}
    .ob-step-link {{ display:inline-block; color:var(--accent); font-weight:600; font-size:.84rem; text-decoration:none; }}
    .ob-step-link:hover {{ text-decoration:underline; }}
    .ob-copy {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:0 0 10px; }}
    .ob-copy code {{ background:#f6f8fb; border:1px solid var(--line); border-radius:6px; padding:5px 9px; font-size:.8rem; word-break:break-all; }}
    .ob-copy-btn {{ border:1px solid var(--line); background:var(--card); color:var(--accent); font-weight:600; font-size:.78rem; padding:5px 11px; border-radius:6px; cursor:pointer; white-space:nowrap; transition:background .15s; }}
    .ob-copy-btn:hover {{ background:#eaf2fd; }}
    .ob-copy-btn.ok {{ color:#178a4c; border-color:#178a4c; }}
    /* Wraps so a card head carrying its own filter (e.g. Platform chips on Paid
       summary) drops the controls to a second line instead of squeezing them. */
    .sec-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; flex-wrap:wrap; }}
    .sec-head h2 {{ margin:0; }}
    .sec-head .status {{ margin:0; font-size:.76rem; text-align:right; flex-shrink:0; }}
    .sec-head-actions {{ display:flex; align-items:center; gap:12px; flex-shrink:0; }}
    /* ---- Tabbed panel card ----
       Two related panels share one full-width card and swap on a tab, instead of
       sitting side by side at half width where the tables were too narrow to read
       (Pages / Landing Pages, Traffic / New user acquisition). The tabs *are* the
       card's heading — same size and weight as an h2 — with the active one
       underlined on the head's rule. Only the active pane's status shows, so the
       right of the head stays a single line. */
    .pnl-head {{ display:flex; align-items:flex-end; justify-content:space-between; gap:12px; flex-wrap:wrap; border-bottom:1px solid var(--line); margin-bottom:16px; }}
    /* On a phone the two tab labels are wider than the card: let the tablist
       scroll sideways rather than push the whole page wide. The 1px of padding
       (cancelled by the negative margin) keeps the active underline from being
       clipped by the scroller. */
    .pnl-tabs {{ display:flex; gap:24px; min-width:0; max-width:100%; overflow-x:auto; padding-bottom:1px; margin-bottom:-1px; scrollbar-width:none; }}
    .pnl-tabs::-webkit-scrollbar {{ display:none; }}
    .pnl-tab {{ position:relative; border:0; background:none; padding:0 0 10px; font:inherit; font-size:1.05rem; font-weight:750; letter-spacing:-.005em; color:var(--muted); cursor:pointer; white-space:nowrap; }}
    .pnl-tab[hidden] {{ display:none; }}
    .pnl-tab:hover {{ color:var(--navy); }}
    .pnl-tab[aria-selected="true"] {{ color:var(--navy); }}
    /* The active underline sits on top of .pnl-head's border, not below it. */
    .pnl-tab[aria-selected="true"]::after {{ content:""; position:absolute; left:0; right:0; bottom:-1px; height:2px; background:var(--accent); border-radius:2px 2px 0 0; }}
    .pnl-tab:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:3px; border-radius:4px; }}
    /* One tab left (the other panel is switched off) — nothing to choose, so it
       reads as a plain heading. */
    .pnl-tabs.one-tab .pnl-tab[aria-selected="true"]::after {{ display:none; }}
    .pnl-head .status {{ margin:0 0 10px; font-size:.76rem; text-align:right; flex-shrink:0; }}
    .pnl-head-actions {{ display:flex; align-items:center; gap:10px; margin:0 0 8px; flex-shrink:0; }}
    .pnl-head-actions .status {{ margin:0; }}
    .pnl-head .status[hidden] {{ display:none; }}
    .pnl-pane[hidden] {{ display:none; }}
    /* Campaign explorer: admin "Edit filters" button + popover */
    .ef-edit {{ position:relative; }}
    .ef-edit-btn {{ display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--navy); font:inherit; font-size:.8rem; font-weight:700; padding:6px 11px; cursor:pointer; transition:background .15s,border-color .15s; }}
    .ef-edit-btn:hover {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .ef-edit-btn[aria-expanded="true"] {{ border-color:#9bbfe6; background:#eef5fd; }}
    .ef-edit-btn svg {{ opacity:.8; }}
    .ef-pop {{ position:absolute; top:calc(100% + 8px); right:0; width:340px; max-width:82vw; background:var(--card); border:1px solid var(--line); border-radius:12px; box-shadow:0 14px 38px rgba(16,33,67,.22); z-index:50; text-align:left; }}
    .ef-pop-head {{ display:flex; align-items:center; justify-content:space-between; padding:11px 14px; border-bottom:1px solid var(--line-soft); font-size:.85rem; font-weight:750; color:var(--navy); }}
    .ef-pop-x {{ appearance:none; border:0; background:none; font-size:1.2rem; line-height:1; color:#8a97a8; cursor:pointer; padding:0 2px; }}
    .ef-pop-x:hover {{ color:var(--navy); }}
    .ef-pop-body {{ padding:13px 14px; }}
    .ef-pop-desc {{ margin:0 0 10px; font-size:.76rem; line-height:1.5; color:var(--muted); }}
    .ef-pop-desc code {{ background:#eef4fb; padding:1px 5px; border-radius:4px; }}
    .ef-pop textarea {{ width:100%; min-height:150px; border:1px solid var(--line); border-radius:9px; padding:10px 12px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.82rem; resize:vertical; color:#102033; background:#fff; }}
    .ef-pop textarea:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    .ef-pop-actions {{ display:flex; align-items:center; gap:12px; margin-top:12px; }}
    .ef-pop-btn {{ appearance:none; border:0; border-radius:8px; padding:8px 14px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; }}
    .ef-pop-btn.primary {{ background:var(--accent); color:#fff; }}
    .ef-pop-btn.primary:hover:not(:disabled) {{ background:#1a62b8; }}
    .ef-pop-btn:disabled {{ opacity:.55; cursor:default; }}
    .ef-status {{ font-size:.78rem; color:var(--muted); }}
    .ef-status.err {{ color:var(--bad); }}
    .ef-status.ok {{ color:#178a4c; }}
    /* Website Analytics page-path scope: top-of-pane bar (scope indicator +
       admin "Edit page filter" editor). Editor reuses the .ef-* styles above. */
    .apf-bar {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin:0 0 16px; }}
    .apf-bar[hidden] {{ display:none; }}
    .apf-bar .ef-edit {{ margin-left:auto; }}
    .apf-scope {{ display:inline-flex; align-items:center; gap:6px; font-size:.82rem; color:var(--navy); background:#eef5fd; border:1px solid var(--line); border-radius:999px; padding:6px 13px; font-weight:600; }}
    .apf-scope[hidden] {{ display:none; }}
    .apf-scope svg {{ opacity:.75; flex-shrink:0; }}
    .apf-scope code {{ background:rgba(29,111,208,.12); padding:1px 6px; border-radius:5px; font-size:.78rem; }}
    /* Small clarifying line under a section heading (e.g. what the scoped
       sessions series counts). Hidden unless the page sets its text. */
    .sec-note {{ margin:-4px 0 12px; font-size:.78rem; line-height:1.5; color:var(--muted); }}
    .sec-note[hidden] {{ display:none; }}
    /* Campaign explorer: admin "Campaigns" allowlist picker (checklist popover) */
    .ec-badge {{ display:inline-flex; align-items:center; justify-content:center; min-width:16px; height:16px; padding:0 4px; border-radius:999px; background:var(--accent); color:#fff; font-size:.66rem; font-weight:800; }}
    .ec-badge[hidden] {{ display:none; }}
    .ec-search {{ width:100%; border:1px solid var(--line); border-radius:8px; padding:7px 10px; font:inherit; font-size:.82rem; color:#102033; background:#fff; margin-bottom:8px; }}
    .ec-search:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    .ec-tools {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }}
    .ec-tool {{ appearance:none; border:0; background:none; color:var(--accent); font:inherit; font-size:.76rem; font-weight:700; cursor:pointer; padding:0; }}
    .ec-tool:hover {{ text-decoration:underline; }}
    .ec-count {{ margin-left:auto; font-size:.72rem; color:var(--muted); }}
    .ec-list {{ max-height:240px; overflow-y:auto; border:1px solid var(--line); border-radius:9px; padding:4px; }}
    .ec-list .ec-empty {{ padding:14px 10px; font-size:.78rem; color:var(--muted); text-align:center; }}
    .ec-option {{ display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:7px; font-size:.82rem; color:var(--navy); cursor:pointer; text-transform:none; letter-spacing:0; font-weight:500; }}
    .ec-option:hover {{ background:#f4f8fd; }}
    .ec-option input {{ margin:0; cursor:pointer; accent-color:var(--accent); flex-shrink:0; }}
    .ec-option .ec-name {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .ec-option[hidden] {{ display:none; }}
    /* Paid-trends metric picker: chips on desktop, a compact dropdown on phones
       (see the max-width:720px block) where the chip row would overflow. */
    .ov-actions {{ display:flex; align-items:center; gap:12px; flex-shrink:0; }}
    .ov-more {{ display:inline-flex; align-items:center; justify-content:center; width:30px; height:30px; border:1px solid var(--line); border-radius:999px; background:var(--card); color:var(--muted); padding:0; font:inherit; cursor:pointer; transition:color .14s, border-color .14s, background .14s, box-shadow .14s; }}
    .ov-more:hover {{ color:var(--accent); border-color:var(--accent); background:var(--card); box-shadow:0 1px 4px rgba(29,111,208,.18); }}
    .ov-more-arrow {{ width:16px; height:16px; display:block; transition:transform .14s; }}
    .ov-more:hover .ov-more-arrow {{ transform:translateX(2px); }}
    /* ---- Panel edit mode (admin-only; Overview + Campaign Explorer) ---- */
    /* Entered from the sidebar kebab; no always-on chrome. This banner and the
       per-panel edit bars appear only while an .ov-editable pane is .is-editing. */
    .ov-editing-banner {{ display:none; }}
    .ov-editable.is-editing .ov-editing-banner {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0 0 14px; padding:9px 13px; border:1px solid var(--accent); border-radius:var(--radius-sm); background:rgba(29,111,208,.06); position:sticky; top:8px; z-index:20; }}
    .ov-editing-badge {{ display:inline-flex; align-items:center; gap:6px; font-size:.76rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; color:var(--accent); }}
    .ov-editing-badge::before {{ content:""; width:8px; height:8px; border-radius:999px; background:var(--accent); box-shadow:0 0 0 3px rgba(29,111,208,.18); }}
    .ov-edit-hint {{ color:var(--muted); font-size:.78rem; }}
    .ov-edit-status {{ color:var(--muted); font-size:.76rem; margin-left:auto; }}
    .ov-edit-status.is-error {{ color:var(--bad); }}
    .ov-edit-done {{ border:1px solid var(--accent); border-radius:999px; background:var(--accent); color:#fff; padding:6px 16px; font:inherit; font-size:.8rem; font-weight:700; cursor:pointer; transition:background .14s, border-color .14s; }}
    .ov-edit-done:hover {{ background:var(--blue); border-color:var(--blue); }}

    /* Per-panel edit bar: revealed only while the admin is editing. */
    .ov-edit-bar {{ display:none; }}
    .is-admin .ov-editable.is-editing .ov-edit-bar {{ display:flex; align-items:center; gap:9px; margin:0 0 8px; padding:6px 10px; border:1px dashed var(--line); border-radius:var(--radius-sm); background:rgba(29,111,208,.05); }}
    .ov-drag {{ display:inline-flex; align-items:center; justify-content:center; color:var(--muted); cursor:grab; }}
    .ov-drag:active {{ cursor:grabbing; }}
    .ov-drag svg {{ display:block; }}
    .ov-card-name {{ font-size:.8rem; font-weight:600; color:var(--navy); }}
    .ov-hide-toggle {{ margin-left:auto; border:1px solid var(--line); border-radius:999px; background:#fff; color:var(--muted); padding:4px 12px; font:inherit; font-size:.76rem; font-weight:600; cursor:pointer; transition:color .14s, border-color .14s, background .14s; }}
    .ov-hide-toggle:hover {{ color:var(--accent); border-color:var(--accent); }}
    .ov-unit.ov-unit--hidden .ov-hide-toggle {{ color:#fff; background:var(--accent); border-color:var(--accent); }}
    /* A hidden panel is gone for clients (never emitted) and, for an admin, only
       reappears — greyed — while editing so it can be shown back. */
    .ov-unit.ov-unit--hidden {{ display:none; }}
    .is-admin .ov-editable.is-editing .ov-unit.ov-unit--hidden {{ display:block; opacity:.5; }}
    .ov-editable.is-editing .ov-unit {{ border:1px solid transparent; border-radius:var(--radius-sm); padding:4px; transition:border-color .12s, background .12s; }}
    .ov-editable.is-editing .ov-unit.is-drag-over {{ border-color:var(--accent); background:rgba(29,111,208,.04); }}
    .ov-editable.is-editing .ov-unit.is-dragging {{ opacity:.4; }}

    .status {{ color:var(--muted); font-size:.82rem; margin:0 0 12px; }}
    .status.error {{ color:var(--bad); }}
    /* ---- Metric cards ---- */
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(128px,1fr)); gap:10px; }}
    .card {{ border:1px solid var(--line-soft); border-top:3px solid var(--accent); border-radius:var(--radius-sm); padding:13px 14px 14px; background:#fff; }}
    .card-title {{ color:var(--muted); font-size:.65rem; text-transform:uppercase; font-weight:800; letter-spacing:.06em; }}
    .card-value {{ margin-top:7px; font-size:1.5rem; line-height:1.1; color:var(--navy); font-weight:800; letter-spacing:-.02em; }}
    .card-foot {{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:9px; min-height:22px; }}
    .card-foot .cmp-delta {{ font-size:.74rem; white-space:nowrap; }}
    .cmp-delta.flat {{ color:var(--muted); }}
    .spark {{ width:66px; height:22px; flex:0 0 auto; opacity:.85; }}
    .spark-empty {{ width:66px; height:22px; flex:0 0 auto; }}
    .card-delta {{ margin-top:4px; font-size:.76rem; font-weight:700; }}
    .card-delta.up {{ color:var(--ok); }}
    .card-delta.down {{ color:var(--bad); }}
    .card-delta.flat {{ color:var(--muted); font-weight:600; }}
    /* The bar across the top of a metric card takes the colour of that card's
       vs-previous delta, so a wall of cards reads as good/bad at arm's length
       before any number is. It follows the delta's *meaning*, not its arrow:
       the delta classes are already assigned by whether the move is good for
       that metric (CPA falling is green), so a rising CPA colours the bar red.
       Cards with no comparison, a flat move, or a direction-less metric
       (Spend) keep the default blue. :has() is the whole mechanism — every
       card is built by a different JS branch, and none of them needs to know
       about this. Browsers without :has() just keep blue bars. */
    .card:has(.cmp-delta.up), .card:has(.card-delta.up) {{ border-top-color:var(--ok); }}
    .card:has(.cmp-delta.down), .card:has(.card-delta.down) {{ border-top-color:var(--bad); }}
    /* ---- Goal + peer benchmark rows (admin preview) ----
       Both sit under the delta and answer the question the delta cannot: not
       "which way did this move" but "is this where it should be". They are
       deliberately quiet — small type, a hairline rule above — because on a card
       the value is still the headline and these are the footnote that qualifies
       it. Neither row renders at all when there is nothing to say, so a card
       without a target or without peers looks exactly as it did before. */
    .card-goal {{ margin-top:8px; padding-top:8px; border-top:1px solid var(--line-soft); }}
    .cg-bar {{ height:4px; border-radius:999px; background:#eef2f7; overflow:hidden; }}
    .cg-bar > span {{ display:block; height:100%; border-radius:999px; background:var(--muted); transition:width .3s ease; }}
    .cg-text {{ margin-top:5px; font-size:.7rem; font-weight:700; color:var(--muted); line-height:1.3; }}
    .card-goal.good .cg-bar > span {{ background:var(--ok); }}
    .card-goal.good .cg-text {{ color:var(--ok); }}
    .card-goal.warn .cg-bar > span {{ background:#b7791f; }}
    .card-goal.warn .cg-text {{ color:#b7791f; }}
    .card-goal.bad .cg-bar > span {{ background:var(--bad); }}
    .card-goal.bad .cg-text {{ color:var(--bad); }}
    .card-bench {{ margin-top:7px; font-size:.7rem; color:var(--muted); font-weight:600; line-height:1.35; display:flex; align-items:center; gap:5px; flex-wrap:wrap; }}
    .card-goal + .card-bench {{ margin-top:6px; }}
    .card-bench b {{ color:var(--navy); font-weight:800; }}
    .cb-verdict {{ font-weight:800; }}
    .cb-verdict.ahead {{ color:var(--ok); }}
    .cb-verdict.behind {{ color:var(--bad); }}
    .cb-verdict.level {{ color:var(--muted); }}
    /* A bucket at or below the thin-sample cutoff is directional, not a
       benchmark — say so on the row rather than in a tooltip nobody opens. */
    .cb-thin {{ padding:0 5px; border-radius:999px; background:#fdf6e3; border:1px solid #f0e0b6; color:#8a6d1f; font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; }}
    /* Preview marker: these two rows are admin-only until the framing is agreed,
       and an admin needs to see at a glance that a client would not see this. */
    .adm-preview {{ display:inline-block; padding:0 5px; border-radius:999px; background:#eef4fb; border:1px solid #cfe0f3; color:var(--accent); font-size:.6rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; }}
    /* HubSpot MQL tracker (Overview home) */
    .mql-panel .card {{ border-top-color:#ff7a59; }}
    .mql-dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; background:#ff7a59; margin-right:8px; vertical-align:middle; }}
    .mql-sub {{ color:var(--muted); font-size:.74rem; }}
    .mql-delta {{ font-size:.74rem; font-weight:700; white-space:nowrap; }}
    .mql-delta.up {{ color:var(--ok); }}
    .mql-delta.down {{ color:var(--bad); }}
    .mql-delta.flat {{ color:var(--muted); font-weight:600; }}
    .mql-spark {{ width:100%; height:30px; display:block; margin:7px 0 2px; }}
    /* LinkedIn Overview card — ranked "views by page section" column. Wants more
       room than a number card, so it claims two grid tracks where they exist. */
    .li-sections {{ grid-column:span 2; min-width:0; }}
    .li-bars {{ display:flex; flex-direction:column; gap:8px; margin-top:9px; }}
    .li-bar-row {{ display:grid; grid-template-columns:1fr auto; gap:3px 10px; align-items:center; font-size:.78rem; }}
    .li-bar-label {{ color:var(--text); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .li-bar-val {{ text-align:right; color:var(--navy); font-weight:700; font-variant-numeric:tabular-nums; }}
    .li-bar-val span {{ color:var(--muted); font-weight:600; font-size:.7rem; margin-left:5px; }}
    .li-bar-track {{ grid-column:1 / -1; display:block; background:#e9eef5; border-radius:5px; height:6px; overflow:hidden; }}
    .li-bar-fill {{ display:block; height:100%; border-radius:5px; min-width:3px; background:#0A66C2; }}
    .cmp-warn {{ display:inline-block; margin-left:6px; font-size:.85rem; cursor:help; color:#b78103; }}
    .cmp-warn[hidden] {{ display:none; }}
    /* ---- Tables ----
       The slim scrollbar treatment for .table-wrap (and the dropdown lists
       above) lives in base_layout's LIGHT_SCROLLBAR_CSS, shared with every
       other page. */
    .table-wrap {{ overflow:auto; border:1px solid var(--line-soft); border-radius:var(--radius-sm); }}
    table {{ border-collapse:collapse; width:100%; min-width:600px; font-size:.86rem; }}
    th,td {{ padding:10px 13px; border-bottom:1px solid var(--line-soft); text-align:right; white-space:nowrap; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    tbody tr:hover td {{ background:#f7faff; }}
    th {{ background:#f4f7fb; color:#5a6b82; text-transform:uppercase; font-size:.67rem; letter-spacing:.05em; font-weight:800; position:sticky; top:0; }}
    th.left,td.left {{ text-align:left; }}
    th.gsc-sort {{ cursor:pointer; user-select:none; white-space:nowrap; transition:background .12s,color .12s; }}
    th.gsc-sort:hover {{ background:#e9eef5; color:#33455e; }}
    th.gsc-sort.active {{ color:var(--accent); }}
    /* Δ Position movement badges (queries table) */
    .gsc-mv {{ display:inline-block; font-size:.74rem; font-weight:800; padding:2px 7px; border-radius:999px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .gsc-mv-up {{ color:#0a7f3f; background:#e7f6ee; }}
    .gsc-mv-down {{ color:#c02626; background:#fdecec; }}
    .gsc-mv-flat {{ color:#8a97a8; background:transparent; font-weight:600; }}
    .gsc-mv-new {{ color:#1769aa; background:#e9f1fb; }}
    /* CTR read-out: a dot saying whether the click-through rate is ahead of,
       in line with, or behind what a query's average rank normally earns. */
    .gsc-ctr {{ display:inline-flex; align-items:center; justify-content:flex-end; gap:5px; }}
    .gsc-ctr-dot {{ width:6px; height:6px; border-radius:50%; flex:none; background:#c9d3e0; }}
    .gsc-ctr-above .gsc-ctr-dot {{ background:#0a7f3f; }}
    .gsc-ctr-below .gsc-ctr-dot {{ background:#d97706; }}
    /* Keyword watchlist: one row per watched keyword, with a rank spark line. */
    #gscWatchTable td.left {{ max-width:0; }}
    #gscWatchTable td.left > * {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .watch-kw {{ font-weight:700; }}
    .watch-variants {{ font-size:.64rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; color:#7d8ba0; margin-left:5px; }}
    .watch-spark {{ display:inline-flex; align-items:center; gap:8px; justify-content:flex-end; }}
    .watch-pos {{ font-variant-numeric:tabular-nums; font-weight:800; min-width:2.4em; text-align:right; }}
    .watch-pos-none {{ color:#9aa7b8; font-weight:600; }}
    /* Editing happens in the list itself (admin only): the keyword and page
       cells are click-to-edit, so the table stays the one place the watchlist
       lives instead of a form that shadows it. */
    .watch-btn {{ border:1px solid var(--line); background:#fff; color:var(--muted); border-radius:var(--radius-sm); padding:4px 11px; font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; cursor:pointer; transition:border-color .12s, color .12s; }}
    .watch-btn:hover {{ border-color:#9bbfe6; color:var(--accent); }}
    .watch-btn-primary {{ border-color:var(--accent); color:#fff; background:var(--accent); }}
    .watch-btn-primary:hover {{ color:#fff; filter:brightness(1.06); }}
    .watch-hint {{ font-size:.7rem; color:#7d8ba0; margin:7px 0 0; }}
    /* Bulk add popover, anchored under its button. Deliberately sets no display
       property: the `hidden` attribute is what opens and closes it, and an
       author `display` rule would beat it (which is exactly how the first
       version ended up permanently open). */
    .watch-bulk-wrap {{ position:relative; }}
    .watch-pop {{ position:absolute; right:0; top:calc(100% + 7px); z-index:60; width:min(420px, 78vw); padding:11px; text-align:left; border:1px solid var(--line); border-radius:var(--radius-sm); background:#fff; box-shadow:0 10px 26px rgba(24,39,58,.16); }}
    .watch-pop textarea {{ width:100%; box-sizing:border-box; font:inherit; font-size:.8rem; line-height:1.5; padding:7px 9px; border:1px solid var(--line); border-radius:var(--radius-sm); background:#fff; color:inherit; resize:vertical; }}
    .watch-pop textarea:focus {{ outline:none; border-color:#9bbfe6; box-shadow:0 0 0 2px #e2eefb; }}
    .watch-pop-foot {{ display:flex; align-items:center; gap:8px; margin-top:9px; }}
    /* On a narrow screen the anchored box would hang off the edge, so it drops
       to the full width of the panel instead. */
    @media (max-width: 560px) {{
      .watch-pop {{ position:fixed; left:12px; right:12px; top:auto; width:auto; }}
    }}
    /* Click-to-edit affordance: only an admin gets the cursor and the hover
       underline, so a client's table reads as plain text. */
    .is-admin .watch-editable {{ cursor:text; }}
    .is-admin .watch-editable:hover {{ box-shadow:inset 0 -1px 0 #c9d3e0; }}
    .watch-cell-in {{ width:100%; box-sizing:border-box; font:inherit; font-size:.82rem; padding:3px 6px; border:1px solid #9bbfe6; border-radius:var(--radius-sm); background:#fff; color:inherit; }}
    .watch-cell-in:focus {{ outline:none; box-shadow:0 0 0 2px #e2eefb; }}
    /* A row added but not yet named -- tinted so it is obvious the list is
       waiting on you, and it stays at the top until it has a keyword. */
    .watch-draft td {{ background:#fbfdff; }}
    /* Drag handle. Only meaningful in list order, where the row's position is
       the thing being stored -- under a metric sort the cell is a button back to
       list order instead, since dragging could not mean anything there. */
    .watch-grip {{ display:inline-block; color:#c9d3e0; cursor:grab; line-height:1; padding:2px 3px; border-radius:3px; font-size:.9rem; letter-spacing:-1px; }}
    tr:hover .watch-grip {{ color:#8a97a8; }}
    .watch-grip:hover, .watch-grip:focus-visible {{ color:var(--accent); background:#eef4fb; outline:none; }}
    .watch-grip:active {{ cursor:grabbing; }}
    td.watch-grip-cell {{ width:22px; padding-left:6px; padding-right:0; text-align:center; }}
    th.watch-grip-cell {{ width:22px; padding-left:6px; padding-right:0; text-align:center; }}
    tr.watch-dragging td {{ opacity:.45; }}
    /* Where the row will land, drawn on the row you are hovering over. */
    tr.watch-drop-above td {{ box-shadow:inset 0 2px 0 var(--accent); }}
    tr.watch-drop-below td {{ box-shadow:inset 0 -2px 0 var(--accent); }}
    .watch-rm {{ border:1px solid transparent; background:transparent; color:#c9d3e0; border-radius:var(--radius-sm); width:22px; height:22px; line-height:1; cursor:pointer; }}
    tr:hover .watch-rm {{ color:#8a97a8; border-color:var(--line); }}
    .watch-rm:hover {{ border-color:#e6a6a6; color:#8a2020; }}
    td.watch-rm-cell {{ width:28px; padding-left:0; padding-right:6px; text-align:right; }}
    th.watch-sort {{ cursor:pointer; user-select:none; white-space:nowrap; transition:background .12s,color .12s; }}
    th.watch-sort:hover {{ background:#e9eef5; color:#33455e; }}
    th.watch-sort.active {{ color:var(--accent); }}
    .gsc-ctr-legend {{ margin-top:7px; font-size:.7rem; color:#7d8ba0; display:flex; flex-wrap:wrap; align-items:center; gap:4px 12px; }}
    .gsc-ctr-legend .gsc-ctr {{ gap:4px; color:#7d8ba0; }}
    th.expl-sort {{ cursor:pointer; user-select:none; white-space:nowrap; transition:background .12s,color .12s; }}
    th.expl-sort:hover {{ background:#e9eef5; color:#33455e; }}
    th.expl-sort.active {{ color:var(--accent); }}
    /* GSC/keyword tables: truncate the long query/URL label instead of letting
       the table overflow its column and push off the page. */
    #gscQueriesTable td.left, #gscPagesTable td.left,
    #gscBrandedTable td.left, #gscTargetTable td.left,
    .gsc-leaderboard td.left {{ max-width:0; }}
    #gscQueriesTable td.left > *, #gscPagesTable td.left > *,
    #gscBrandedTable td.left > *, #gscTargetTable td.left > *,
    .gsc-leaderboard td.left > * {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; word-break:normal; }}
    /* Mobile: the compact overview keyword leaderboards must fit the phone
       viewport. The shared 600px table min-width (fine for the wide GSC-tab
       tables) otherwise scrolls the Movement column off-screen and crushes the
       keyword label down to a few characters. Drop the floor, switch to a fixed
       layout, and pin the three numeric columns so the keyword gets the
       remaining width and every column stays readable. */
    @media (max-width:640px) {{
      .gsc-leaderboard {{ min-width:0; table-layout:fixed; }}
      .gsc-leaderboard th, .gsc-leaderboard td {{ padding:8px 6px; }}
      .gsc-leaderboard th {{ font-size:.58rem; }}
      .gsc-leaderboard td {{ font-size:.8rem; }}
      .gsc-leaderboard td.left {{ max-width:none; }}
      .gsc-leaderboard th:nth-child(2), .gsc-leaderboard td:nth-child(2) {{ width:17%; }}
      .gsc-leaderboard th:nth-child(3), .gsc-leaderboard td:nth-child(3) {{ width:19%; }}
      .gsc-leaderboard th:nth-child(4), .gsc-leaderboard td:nth-child(4) {{ width:26%; }}
      .gsc-leaderboard .gsc-mv {{ font-size:.66rem; padding:2px 5px; }}
    }}
    /* Drag-to-resize handle on the label column of the GSC/keyword tables. */
    th.col-resizable {{ position:relative; }}
    .col-resizer {{ position:absolute; top:0; right:0; width:9px; height:100%; cursor:col-resize; user-select:none; touch-action:none; }}
    .col-resizer::after {{ content:''; position:absolute; top:18%; right:3px; height:64%; width:2px; border-radius:1px; background:transparent; transition:background .12s; }}
    .col-resizer:hover::after {{ background:#b9c8dc; }}
    body.col-resizing {{ cursor:col-resize; user-select:none; }}
    body.col-resizing .col-resizer::after {{ background:var(--accent); }}
    /* Admin-only controls (shown when the app-shell has .is-admin) */
    .debug-only {{ display:none !important; }}
    .is-admin .debug-only {{ display:inline-block !important; }}
    /* …but an admin-only control that is also tab-scoped (the Branded / Target
       Edit buttons) still obeys [hidden] when its tab isn't the open one. */
    .is-admin .debug-only[hidden] {{ display:none !important; }}
    /* Inline branded/target keyword tag editor (admin only) */
    .tag-editor {{ display:none; }}
    .is-admin .tag-editor {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:7px 9px; margin-bottom:10px; border:1px solid var(--line); border-radius:var(--radius-sm); background:#fff; }}
    .tag-editor:focus-within {{ border-color:#9bbfe6; box-shadow:0 0 0 2px #e2eefb; }}
    .tag-editor-label {{ width:100%; font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
    .tag-chip {{ display:inline-flex; align-items:center; gap:6px; background:#eef4fb; color:var(--navy); border-radius:999px; padding:3px 5px 3px 11px; font-size:.8rem; font-weight:650; }}
    .tag-chip button {{ border:0; background:rgba(10,37,64,.14); color:var(--navy); width:16px; height:16px; border-radius:50%; cursor:pointer; font-size:.72rem; line-height:1; display:flex; align-items:center; justify-content:center; padding:0; }}
    .tag-chip button:hover {{ background:var(--bad); color:#fff; }}
    .tag-input {{ border:0; outline:none; font:inherit; font-size:.84rem; padding:4px 2px; min-width:110px; flex:1; background:transparent; color:#102033; }}
    .tag-input::placeholder {{ color:#9aa7bd; }}
    /* Exclude editor: same layout, red-tinted so it reads as a "subtract"
       filter distinct from the blue include chips above it. */
    .is-admin .tag-editor-exclude {{ margin-top:-4px; }}
    .tag-editor-exclude .tag-chip {{ background:#fdecec; color:#8a2020; }}
    .tag-editor-exclude .tag-chip button {{ background:rgba(138,32,32,.16); color:#8a2020; }}
    .tag-editor-exclude:focus-within {{ border-color:#e6a6a6; box-shadow:0 0 0 2px #fbe2e2; }}
    /* "Edit" toggle in the branded/target headline row: reveals the include /
       exclude tag editors (admin only, via .debug-only) so they stay tucked
       away until needed. */
    .kw-edit-btn {{ flex-shrink:0; border:1px solid var(--line); background:#fff; color:var(--muted); border-radius:var(--radius-sm); padding:3px 11px; font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; cursor:pointer; transition:border-color .12s, color .12s; }}
    .kw-edit-btn:hover {{ border-color:#9bbfe6; color:var(--navy); }}
    .kw-edit-btn[aria-expanded="true"] {{ border-color:#9bbfe6; color:var(--navy); background:#eef4fb; }}
    .empty {{ color:var(--muted); padding:26px; text-align:center; }}
    code {{ background:#eef4fb; padding:2px 5px; border-radius:4px; font-size:.85em; }}
    .muted {{ color:var(--muted); font-size:.78rem; margin-left:6px; }}
    /* Cap the path label so a long URL can't balloon the column and shove the
       metric columns off the horizontal scroller (worst on mobile). Truncates
       with an ellipsis; the full path stays available via the title tooltip. */
    .page-path {{ display:inline-block; max-width:min(52vw,460px); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom; font-weight:600; color:#1f2d40; cursor:help; }}
    /* Drag-to-resize columns (Pages / Landing Pages tables). */
    table.resizable {{ table-layout:fixed; }}
    table.resizable th, table.resizable td {{ overflow:hidden; text-overflow:ellipsis; }}
    .col-resizer {{ position:absolute; top:0; right:0; width:9px; height:100%; cursor:col-resize; user-select:none; touch-action:none; }}
    .col-resizer::after {{ content:""; position:absolute; top:25%; right:4px; width:1px; height:50%; background:transparent; transition:background .12s; }}
    .col-resizer:hover::after {{ background:var(--accent); }}
    body.col-resizing, body.col-resizing * {{ cursor:col-resize !important; user-select:none !important; }}
    table.compact {{ min-width:0; font-size:.84rem; }}
    table.compact th, table.compact td {{ padding:8px 12px; }}
    /* ---- Pager ---- */
    .pager {{ display:flex; align-items:center; justify-content:flex-end; gap:12px; margin-top:12px; }}
    .pager-btn {{ border:1px solid var(--line); background:#fff; color:var(--navy); border-radius:var(--radius-sm); padding:6px 13px; font:inherit; font-size:.82rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s; }}
    .pager-btn:hover:not(:disabled) {{ border-color:#b9c8dc; background:#f4f8fd; }}
    .pager-btn:disabled {{ opacity:.45; cursor:default; }}
    .pager-info {{ font-size:.8rem; color:var(--muted); font-weight:600; }}
    /* ---- Trend chart ---- */
    .chart-wrap {{ position:relative; border:1px solid var(--line-soft); border-radius:10px; padding:10px 12px; background:#fafcff; }}
    .chart-note {{ font-size:.74rem; color:var(--muted); margin-top:8px; }}
    /* Small labelled caption above the keyword avg-position trend charts, with a
       hover/focus info glyph carrying the "how this chart works" tooltip. */
    .chart-cap {{ display:flex; align-items:center; gap:5px; margin:2px 0 8px; font-size:.72rem; font-weight:800; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
    .ov-panel .table-wrap + .chart-cap {{ margin-top:18px; }}
    .info-tip {{ display:inline-flex; align-items:center; cursor:help; color:#9aa7bd; line-height:0; text-transform:none; }}
    .info-tip svg {{ display:block; }}
    .info-tip:hover, .info-tip:focus {{ color:var(--accent); outline:none; }}
    {budget_css}
    .chart-tip {{ position:absolute; pointer-events:none; background:#0b1020; color:#e8eefc; font-size:.74rem; line-height:1.5; padding:7px 9px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,.25); transform:translate(-50%,-112%); white-space:nowrap; z-index:5; }}
    /* ---- Bar lists ---- */
    .bar-row {{ display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid var(--line-soft); }}
    .bar-row:last-child {{ border-bottom:0; }}
    .bar-label {{ flex:0 0 160px; font-size:.84rem; color:var(--navy); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .bar-track {{ flex:1 1 auto; height:7px; background:var(--line-soft); border-radius:4px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:var(--accent); border-radius:4px; transition:width .3s; }}
    .bar-count {{ flex:0 0 100px; font-size:.82rem; color:var(--navy); text-align:right; font-variant-numeric:tabular-nums; }}
    .bar-pct {{ color:var(--muted); font-size:.74rem; margin-left:4px; }}
    /* Keyword Performance: insight banner, toolbar, match badges and in-cell bars. */
    .kw-insight {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 22px; padding:11px 15px; margin-bottom:14px; border:1px solid var(--line-soft); border-radius:var(--radius-sm); background:#f6f9fd; font-size:.82rem; color:var(--navy); }}
    .kw-insight[hidden] {{ display:none; }}
    .kw-insight .kw-ins {{ display:inline-flex; align-items:center; gap:8px; }}
    .kw-insight strong {{ font-weight:800; }}
    .kw-ins-icon {{ flex:0 0 auto; width:15px; height:15px; }}
    .kw-ins.kw-ins-warn {{ color:#8a5a00; }}
    .kw-toolbar {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px 14px; margin-bottom:14px; }}
    .kw-search {{ border:1px solid var(--line); border-radius:var(--radius-sm); padding:7px 12px; font:inherit; font-size:.84rem; color:#102033; min-width:230px; background:#fff; }}
    .kw-search:focus {{ outline:none; border-color:var(--accent); }}
    .kw-search::placeholder {{ color:#9aa7bd; }}
    .kw-name {{ font-weight:650; color:var(--navy); }}
    .kw-sub {{ color:var(--muted); font-size:.73rem; margin-top:1px; }}
    .badge-match {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.66rem; font-weight:800; letter-spacing:.02em; white-space:nowrap; }}
    .badge-exact {{ background:#e4f4ea; color:#0a7f3f; }}
    .badge-phrase {{ background:#e8f0fe; color:#1a73e8; }}
    .badge-broad {{ background:#eef2f7; color:#54637a; }}
    .cell-bar {{ display:flex; align-items:center; justify-content:flex-end; gap:9px; }}
    .cell-bar .bar-track {{ flex:0 0 auto; width:74px; height:6px; }}
    .cell-bar-val {{ font-variant-numeric:tabular-nums; }}
    .num-good {{ color:var(--ok); font-weight:650; }}
    .num-bad {{ color:var(--bad); }}
    .cell-flag {{ color:#d9a400; margin-left:6px; }}
    /* ---- Layout cols ---- */
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
    /* Grid items must be allowed to shrink below their content, or a wide table
       (long GSC page URLs) forces the column past the viewport. */
    .two-col > * {{ min-width:0; }}
    .three-col {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; margin-top:14px; }}
    .col-panel {{ border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:14px 15px; background:#fafcff; }}
    .col-panel h3 {{ margin:0 0 12px; font-size:.86rem; font-weight:750; color:var(--navy); }}
    .subsec-h3 {{ margin:16px 0 10px; font-size:.86rem; font-weight:750; color:var(--navy); }}
    .trend-sm-svg {{ width:100%; height:130px; display:block; }}
    .trend-md-svg {{ width:100%; height:200px; display:block; }}
    /* Chart.js canvas host: a relatively-positioned box of fixed height that
       the canvas fills (charts run with maintainAspectRatio:false). */
    .chart-canvas-host {{ position:relative; width:100%; }}
    .chart-canvas-host canvas {{ display:block; }}
    {pagespeed_pane_css}
    /* Sessions-over-time current-vs-previous legend */
    .cmp-legend {{ display:flex; flex-wrap:wrap; gap:18px; margin-top:10px; font-size:.78rem; color:var(--muted); }}
    .cmp-item {{ display:inline-flex; align-items:center; gap:7px; }}
    .cmp-swatch {{ width:16px; height:0; border-top-width:3px; border-top-style:solid; border-radius:2px; }}
    .cmp-swatch.cur {{ border-top-color:#1d6fd0; }}
    .cmp-swatch.prev {{ border-top-color:#9aa7bd; border-top-style:dashed; }}
    .cmp-delta {{ font-weight:800; }}
    .cmp-delta.up {{ color:var(--ok); }}
    .cmp-delta.down {{ color:var(--bad); }}
    /* ---- Funnel ---- */
    .funnel-bar {{ display:flex; flex-direction:column; gap:8px; }}
    .funnel-step {{ display:flex; align-items:center; gap:12px; }}
    .funnel-step-label {{ flex:0 0 110px; font-size:.82rem; color:var(--navy); font-weight:600; }}
    .funnel-step-track {{ flex:1 1 auto; height:22px; background:var(--line-soft); border-radius:6px; overflow:hidden; }}
    .funnel-step-fill {{ height:100%; background:var(--accent); border-radius:6px; display:flex; align-items:center; padding-left:10px; font-size:.76rem; color:#fff; font-weight:700; min-width:2px; }}
    .funnel-step-count {{ flex:0 0 56px; font-size:.82rem; color:var(--navy); text-align:right; font-weight:700; }}
    /* ---- Explorer tree ---- */
    .indent1 {{ display:inline-block; width:18px; }}
    .indent2 {{ display:inline-block; width:36px; }}
    .tree-row[data-expandable] {{ cursor:pointer; }}
    .tree-row[data-expandable]:hover {{ background:#f3f8ff; }}
    /* Modern chevron caret: a rounded hover target whose chevron rotates on open. */
    .caret {{ display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; border-radius:6px; color:var(--muted); vertical-align:middle; transition:transform .18s ease, background .12s, color .12s; }}
    .tree-row[data-expandable] .caret::before {{ content:''; width:5px; height:5px; border-top:1.7px solid currentColor; border-right:1.7px solid currentColor; transform:translateX(-1px) rotate(45deg); }}
    .tree-row[data-expandable].open .caret {{ transform:rotate(90deg); }}
    .tree-row[data-expandable]:hover .caret {{ background:rgba(29,111,208,.12); color:var(--accent); }}
    .lvl-campaign .tree-name {{ font-weight:800; color:var(--navy); }}
    .lvl-group .tree-name {{ font-weight:600; }}
    .lvl-ad td.left {{ color:var(--muted); }}
    /* Sleek count indicator: dots representing the child count + a numeric badge. */
    .tree-count {{ display:inline-flex; align-items:center; gap:6px; margin-left:11px; vertical-align:middle; }}
    .tree-count .tc-dots {{ display:inline-flex; align-items:center; gap:3px; }}
    .tree-count .tc-dot {{ width:5px; height:5px; border-radius:50%; background:rgba(29,111,208,.55); }}
    .tree-count .tc-dot.tc-more {{ background:none; width:auto; height:auto; font-size:.62rem; font-weight:800; line-height:1; color:rgba(29,111,208,.65); letter-spacing:-.02em; }}
    .lvl-group .tree-count .tc-dot {{ background:rgba(107,122,144,.5); }}
    .lvl-group .tree-count .tc-dot.tc-more {{ color:rgba(107,122,144,.7); }}
    /* Grand-total footer row — reads as a summary rail under the tree, not as
       another campaign: heavier type, a rule above it and no hover state. */
    #explorerTable tfoot td {{ background:#eef3fa; border-top:2px solid var(--line); border-bottom:0; font-weight:800; color:var(--navy); font-variant-numeric:tabular-nums; }}
    #explorerTable tfoot td.ga4-col {{ background:rgba(184,146,46,0.13); }}
    #explorerTable tfoot .tree-name {{ font-weight:800; text-transform:uppercase; font-size:.7rem; letter-spacing:.06em; color:#5a6b82; }}
    #explorerTable tfoot .tot-sub {{ margin-left:9px; font-weight:600; font-size:.72rem; color:var(--muted); }}
    /* "vs previous" delta under each total, shown once the Compare picker has
       a window selected. Kept out-of-flow of the bold total figure above it. */
    #explorerTable tfoot .expl-total-delta {{ margin-top:2px; font-weight:600; }}
    #explorerTable tfoot .expl-total-delta .cmp-delta {{ font-size:.68rem; }}
    /* GA4-verified conversions column — set off from the platform-reported metrics with a subtle gold accent. */
    #explorerTable th.ga4-col, #explorerTable td.ga4-col {{ background:rgba(184,146,46,0.06); border-left:1px solid rgba(184,146,46,0.3); }}
    #explorerTable td.ga4-col {{ font-variant-numeric:tabular-nums; }}
    #explorerTable th.ga4-col {{ color:#8a6d1f; vertical-align:top; }}
    /* Two-line header: GA4 badge + label + sort arrow on top, filter select below. */
    .ga4-head {{ display:inline-flex; flex-direction:column; align-items:flex-end; gap:6px; }}
    .ga4-head-top {{ display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }}
    .ga4-badge {{ font-size:.55rem; font-weight:800; letter-spacing:.05em; color:#8a6d1f; background:rgba(184,146,46,0.16); border-radius:4px; padding:1px 5px; line-height:1.45; }}
    .ga4-head-label {{ text-transform:uppercase; letter-spacing:.05em; }}
    /* Modernized key-event filter control (pill select with leading funnel icon + custom caret). */
    .ke-select-wrap {{ position:relative; display:inline-flex; align-items:center; }}
    .ke-select-ico {{ position:absolute; left:8px; width:11px; height:11px; color:#a98b3a; pointer-events:none; }}
    .ke-select-caret {{ position:absolute; right:8px; font-size:.62rem; color:#a98b3a; pointer-events:none; }}
    #explorerTable th.ga4-col .ke-select {{ -webkit-appearance:none; appearance:none; max-width:158px; font-size:.68rem; font-weight:600; text-transform:none; letter-spacing:0; color:var(--navy); background:#fff; border:1px solid rgba(184,146,46,0.42); border-radius:999px; padding:3px 22px 3px 23px; cursor:pointer; text-overflow:ellipsis; transition:border-color .12s, background .12s, box-shadow .12s; }}
    #explorerTable th.ga4-col .ke-select:hover {{ border-color:#c79a2e; }}
    #explorerTable th.ga4-col .ke-select:focus-visible {{ outline:none; border-color:#b8922e; box-shadow:0 0 0 2px rgba(184,146,46,0.2); }}
    /* Active state: a specific key event is isolated, so the control reads as a live filter. */
    .ke-select-wrap.active .ke-select {{ background:rgba(184,146,46,0.13); border-color:#b8922e; color:#755a11; font-weight:700; }}
    .ke-select-wrap.active .ke-select-ico, .ke-select-wrap.active .ke-select-caret {{ color:#8a6d1f; }}
    .pill {{ display:inline-block; padding:1px 7px; border-radius:999px; font-size:.64rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; vertical-align:middle; margin-right:7px; }}
    .pill-google {{ background:#e8f0fe; color:#1a73e8; }}
    .pill-linkedin {{ background:#e6f0f8; color:#0a66c2; }}
    /* LinkedIn audience panel: tab strip, window badge, share-of-reach bars. */
    .lid-tabs {{ margin:2px 0 14px; border-bottom:1px solid var(--line); }}
    .lid-window {{ display:inline-flex; align-items:center; gap:6px; font-size:.72rem; font-weight:700; color:#0a66c2; background:#e6f0f8; border-radius:999px; padding:4px 11px; white-space:nowrap; }}
    .lid-note {{ margin:12px 2px 0; font-size:.72rem; line-height:1.5; color:var(--muted); }}
    #lidemoTable td.lid-cat {{ max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .lid-bar {{ position:relative; display:block; height:6px; min-width:120px; border-radius:3px; background:rgba(10,102,194,.13); }}
    .lid-bar span {{ position:absolute; inset:0 auto 0 0; border-radius:3px; background:#0a66c2; }}
    #lidemoTable td.lid-share {{ width:150px; }}
    /* Google Ads demographics: dimension tabs, recommendation cards, the
       spend-vs-conversion share bars and the already-excluded badge. */
    .gd-tabs {{ margin:2px 0 14px; border-bottom:1px solid var(--line); }}
    .gd-coverage {{ display:inline-flex; align-items:center; gap:6px; font-size:.72rem; font-weight:700; color:#8a5a00; background:#fdf3e2; border-radius:999px; padding:4px 11px; white-space:nowrap; }}
    .gd-coverage[hidden] {{ display:none; }}
    .gd-note {{ margin:12px 2px 0; font-size:.72rem; line-height:1.5; color:var(--muted); }}
    .gd-recs {{ display:grid; gap:8px; margin:0 0 14px; }}
    .gd-recs[hidden] {{ display:none; }}
    .gd-rec {{ display:flex; gap:10px; align-items:flex-start; border:1px solid var(--line); border-left-width:3px; border-radius:8px; padding:10px 13px; background:var(--card); }}
    .gd-rec-high {{ border-left-color:#c0392b; }}
    .gd-rec-medium {{ border-left-color:#b8922e; }}
    .gd-rec-body {{ min-width:0; }}
    .gd-rec-head {{ font-size:.82rem; font-weight:750; color:var(--navy); }}
    .gd-rec-detail {{ margin-top:2px; font-size:.74rem; line-height:1.5; color:var(--muted); }}
    #gdemoTable td.gd-seg {{ max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    /* Two bars on one baseline: spend share above, conversion share below. A
       segment taking more spend than it returns reads as a top-heavy pair. */
    .gd-split {{ display:block; min-width:120px; }}
    .gd-split-row {{ position:relative; display:block; height:5px; border-radius:3px; background:rgba(26,115,232,.12); }}
    .gd-split-row + .gd-split-row {{ margin-top:3px; background:rgba(31,138,86,.12); }}
    .gd-split-row span {{ position:absolute; inset:0 auto 0 0; border-radius:3px; background:#1a73e8; }}
    .gd-split-row + .gd-split-row span {{ background:#1f8a56; }}
    #gdemoTable td.gd-shares {{ width:150px; }}
    .gd-flag {{ display:inline-block; margin-left:7px; padding:1px 7px; border-radius:999px; font-size:.62rem; font-weight:800; letter-spacing:.03em; text-transform:uppercase; vertical-align:middle; }}
    .gd-flag-excluded {{ background:var(--line-soft); color:var(--muted); }}
    .gd-flag-waste {{ background:#fdecea; color:#c0392b; }}
    .gd-flag-cpa {{ background:#fdf3e2; color:#8a5a00; }}
    .pill-meta {{ background:#f0e8fe; color:#7b2ff7; }}
    /* Platform brand icons (campaign rows) */
    .plat-ico {{ display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; margin-right:9px; vertical-align:middle; flex:0 0 auto; }}
    .plat-ico svg {{ width:18px; height:18px; display:block; }}
    .ad-cell {{ display:inline-flex; align-items:center; gap:9px; vertical-align:middle; }}
    .ad-thumb {{ width:34px; height:34px; border-radius:5px; object-fit:cover; border:1px solid var(--line); background:#f0f3f8; flex:0 0 auto; cursor:zoom-in; }}
    .creative-preview {{ position:fixed; inset:0; z-index:1000; display:flex; align-items:center; justify-content:center; }}
    .creative-preview[hidden] {{ display:none; }}
    .creative-preview-backdrop {{ position:absolute; inset:0; background:rgba(10,20,35,.72); }}
    .creative-preview-dialog {{ position:relative; max-width:min(90vw,760px); max-height:88vh; background:#fff; border-radius:12px; padding:14px; box-shadow:0 20px 60px rgba(0,0,0,.35); display:flex; }}
    .creative-preview-body {{ display:flex; align-items:center; justify-content:center; max-width:100%; max-height:calc(88vh - 28px); }}
    .creative-preview-body img, .creative-preview-body video {{ max-width:100%; max-height:calc(88vh - 28px); border-radius:6px; display:block; }}
    .creative-preview-close {{ position:absolute; top:-14px; right:-14px; width:32px; height:32px; border-radius:50%; border:0; background:#0a2540; color:#fff; font-size:1.1rem; line-height:1; cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.25); }}
    .ad-meta {{ display:flex; flex-direction:column; line-height:1.25; }}
    .ad-label {{ font-weight:600; color:#1f2d40; }}
    .ad-id {{ margin-left:6px; font-family:monospace; font-size:.68rem; font-weight:400; color:var(--muted); }}
    .ad-type {{ font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
    /* Ad-row asset list — each headline/description on its own line with a small
       tinted chip tag (blue for headlines, slate for descriptions). */
    .ad-copy {{ display:flex; flex-direction:column; gap:3px; margin-top:5px; }}
    .ad-copy-line {{ display:flex; align-items:baseline; gap:7px; font-size:.78rem; color:#43536b; line-height:1.4; white-space:normal; }}
    .ad-copy-tag {{ flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; min-width:24px; padding:1px 6px; border-radius:5px; background:#eef2f8; color:#7688a2; font-weight:800; font-size:.6rem; letter-spacing:.03em; text-transform:uppercase; }}
    .ad-copy-tag.ad-copy-tag--h {{ background:rgba(29,111,208,.1); color:#1d6fd0; }}
    .ad-copy-tag.ad-copy-tag--d {{ background:rgba(107,122,144,.12); color:#5a6b82; }}
    .ad-copy-more {{ align-self:flex-start; margin:4px 0 1px; padding:2px 10px; border:1px solid var(--line); border-radius:999px; background:#fff; color:var(--accent, #2563eb); font-size:.7rem; font-weight:700; cursor:pointer; transition:background .12s, border-color .12s; }}
    .ad-copy-more:hover {{ background:#f3f8ff; border-color:var(--accent); }}
    .ad-copy-extra {{ display:flex; flex-direction:column; gap:3px; margin-top:3px; }}
    .ad-copy-extra[hidden] {{ display:none; }}
    /* Google search-ad preview — mimics a real SERP ad so the row reads like the
       live creative. White card + Google colours regardless of dashboard theme. */
    .ad-cell.gads {{ align-items:flex-start; }}
    .gads-preview {{ max-width:540px; padding:9px 12px; border:1px solid #e6e9ef; border-radius:9px; background:#fff; box-shadow:0 1px 2px rgba(16,33,67,.05); }}
    .gads-top {{ display:flex; align-items:center; gap:8px; margin-bottom:3px; }}
    .gads-badge {{ font-size:.62rem; font-weight:800; color:#202124; border:1px solid #202124; border-radius:4px; padding:0 4px; line-height:1.35; }}
    .gads-url {{ font-size:.74rem; color:#202124; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .gads-shuffle {{ margin-left:auto; display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; border:1px solid #dadce0; border-radius:999px; background:#fff; color:#1a73e8; cursor:pointer; flex:0 0 auto; transition:background .12s, transform .12s; }}
    .gads-shuffle svg {{ width:13px; height:13px; }}
    .gads-shuffle:hover {{ background:#f1f6ff; border-color:#a9c7f5; }}
    .gads-shuffle:active {{ transform:rotate(-90deg); }}
    /* white-space:normal overrides the table's `th,td{{white-space:nowrap}}` — without
       it the headline/description text runs on one line and overflows the card
       across the metric columns. */
    .gads-title {{ color:#1a0dab; font-size:1rem; font-weight:400; line-height:1.3; white-space:normal; overflow-wrap:anywhere; }}
    .gads-desc {{ color:#4d5156; font-size:.8rem; line-height:1.35; margin-top:2px; white-space:normal; overflow-wrap:anywhere; }}
    .ad-label-sub {{ margin-top:4px; }}
    .ad-cell.gads .ad-copy-more {{ margin-left:2px; margin-top:5px; }}
    .ad-cell.gads .ad-copy-extra {{ margin-left:2px; margin-top:3px; }}
    /* ---- Pages search ---- */
    .page-search {{ width:100%; border:1px solid var(--line); border-radius:var(--radius-sm); padding:8px 12px; font:inherit; font-size:.88rem; background:#fff; color:#102033; margin-bottom:10px; }}
    .page-search:focus-visible {{ outline:2px solid #bcd4f0; outline-offset:1px; border-color:#9bbfe6; }}
    /* ---- New vs returning ---- */
    .nr-wrap {{ display:flex; align-items:center; gap:20px; padding:12px 14px; border:1px solid var(--line-soft); border-radius:var(--radius-sm); background:#f9fbff; margin-bottom:14px; flex-wrap:wrap; }}
    .nr-stat {{ display:flex; flex-direction:column; gap:2px; min-width:80px; }}
    .nr-stat-label {{ font-size:.67rem; text-transform:uppercase; letter-spacing:.05em; font-weight:800; color:var(--muted); }}
    .nr-stat-value {{ font-size:1.2rem; font-weight:800; color:var(--navy); line-height:1.1; }}
    .nr-stat-pct {{ font-size:.74rem; color:var(--muted); }}
    .nr-bar-wrap {{ flex:1 1 180px; }}
    .nr-bar {{ height:10px; border-radius:5px; overflow:hidden; display:flex; }}
    .nr-bar-new {{ background:#1d6fd0; height:100%; transition:width .3s; }}
    .nr-bar-ret {{ background:#c3d9f5; height:100%; transition:width .3s; }}
    .nr-legend {{ display:flex; gap:14px; margin-top:5px; }}
    .nr-legend-item {{ display:flex; align-items:center; gap:5px; font-size:.74rem; color:var(--muted); }}
    .nr-legend-swatch {{ width:10px; height:10px; border-radius:2px; }}
    /* ---- Traffic: single 100% channel bar (hover for per-segment label) ---- */
    .stack-wrap {{ position:relative; }}
    .stack-bar {{ display:flex; width:100%; height:26px; border-radius:6px; overflow:hidden; background:var(--line-soft); }}
    .stack-seg {{ height:100%; min-width:2px; cursor:default; transition:filter .12s; }}
    .stack-seg:hover {{ filter:brightness(1.08); }}
    .stack-tip {{ position:absolute; bottom:calc(100% + 7px); transform:translateX(-50%); background:var(--navy); color:#fff; font-size:.74rem; font-weight:700; padding:5px 9px; border-radius:6px; white-space:nowrap; pointer-events:none; box-shadow:var(--shadow); z-index:5; }}
    .stack-tip[hidden] {{ display:none; }}
    /* ---- Demographics: age toggle, gender split, state tile map ---- */
    .col-panel-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin-bottom:12px; }}
    .col-panel-head h3 {{ margin:0; }}
    .age-toggle {{ display:inline-flex; align-items:center; gap:5px; font-size:.74rem; font-weight:600; color:var(--muted); cursor:pointer; text-transform:none; letter-spacing:0; }}
    .age-toggle input {{ accent-color:var(--accent); cursor:pointer; }}
    .gender-wrap {{ display:flex; flex-direction:column; gap:14px; }}
    .gender-bar {{ display:flex; width:100%; height:30px; border-radius:8px; overflow:hidden; background:var(--line-soft); }}
    .gender-seg {{ height:100%; display:flex; align-items:center; justify-content:center; color:#fff; font-size:.8rem; font-weight:800; min-width:2px; }}
    .gender-stats {{ display:flex; gap:12px; }}
    .gender-stat {{ flex:1 1 0; border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:10px 12px; display:flex; align-items:center; gap:10px; }}
    .gender-dot {{ width:12px; height:12px; border-radius:50%; flex:0 0 auto; }}
    .gender-stat-label {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; font-weight:800; color:var(--muted); }}
    .gender-stat-value {{ font-size:1.1rem; font-weight:800; color:var(--navy); line-height:1.1; }}
    .gender-stat-pct {{ font-size:.74rem; color:var(--muted); }}
    .state-map {{ width:100%; }}
    .state-map svg {{ width:100%; height:auto; display:block; }}
    .state-tile {{ transition:filter .12s; }}
    .state-tile:hover {{ filter:brightness(.92); }}
    .state-tile-label {{ font-size:8px; font-weight:700; fill:#5a6b82; pointer-events:none; }}
    .state-map-scale {{ display:flex; align-items:center; gap:8px; margin-top:10px; font-size:.72rem; color:var(--muted); }}
    .state-map-scale-bar {{ flex:1 1 auto; height:8px; border-radius:4px; background:linear-gradient(90deg,#eaf1fb,#1d6fd0); }}
    /* Sticky date bar clears the fixed 52px mobile top bar (see SIDEBAR_CSS). */
    @media (max-width:900px) {{ .cards {{ grid-template-columns:repeat(2,minmax(120px,1fr)); }} .two-col,.three-col {{ grid-template-columns:1fr; }} .date-bar {{ top:52px; }} }}
    /* On phones give the metric columns more room: tighten the path label cap. */
    @media (max-width:640px) {{ .page-path {{ max-width:44vw; }} }}
    /* ---- Phone layout for the filters ----
       Range and Compare share one line: each picker gives up its desktop
       min-width, splits the row evenly and ellipsises its own label rather
       than pushing the row wide. A third picker (Events on Website analytics,
       the Explorer bar) wraps to a second line instead of squeezing these two.
       The Compare panel is right-anchored so it can't hang off the screen. */
    @media (max-width:640px) {{
      /* Bar and content share one padding so the pickers line up with the
         cards below them. */
      .date-bar-inner {{ padding:11px 16px; }}
      main {{ padding:16px 16px 44px; }}
      /* A long money value ($17,428.47) overflowed a half-width card at this
         size; scale the figure with the viewport instead of clipping it. */
      .card-value {{ font-size:clamp(1.05rem, 4.6vw, 1.5rem); }}
      .date-bar-bottom {{ gap:8px; }}
      .date-bar-bottom > .filter-group {{ flex:1 1 auto; min-width:0; }}
      .date-bar-bottom > #explorerFilterBar {{ flex:1 1 100%; }}
      .date-bar-bottom .range-dd {{ display:block; min-width:0; }}
      .date-bar-bottom .range-dd-toggle {{ width:100%; min-width:0; padding:6px 10px; font-size:.79rem; }}
      .range-dd-panel {{ width:min(220px, calc(100vw - 32px)); }}
      #compareDropdown .ke-dd-panel {{ left:auto; right:0; }}
      /* Card-head filters (the Platform chips) are the widest thing in a
         section head on a phone. The head already drops its actions to their
         own line; they just couldn't shrink or wrap once there, so the last
         chip ran off the card. Let them do both. */
      .ov-actions, .sec-head-actions {{ flex-shrink:1; flex-wrap:wrap; min-width:0; }}
    }}
    /* ---- Skeleton loaders ---- */
    @keyframes shimmer {{ 0%{{background-position:-200% 0}} 100%{{background-position:200% 0}} }}
    .skel {{ display:block; background:linear-gradient(90deg,#eef2f7 25%,#e4eaf2 50%,#eef2f7 75%); background-size:200% 100%; animation:shimmer 1.4s ease-in-out infinite; border-radius:5px; }}
    .skel-chart {{ border-radius:10px; }}
  </style>
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
          <div class="ke-dropdown range-dd" id="compareDropdown">
            <button type="button" class="ke-dd-toggle range-dd-toggle" id="compareToggle" aria-haspopup="listbox" aria-expanded="false">
              <span class="dd-lead">
                <span class="dd-vs" aria-hidden="true">vs</span>
                <span class="sr-only">Compare to:</span>
                <span id="compareToggleLabel">Previous period</span>
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
               backfill. It qualifies this one picker, so it rides next to it as
               a hover/focus tooltip rather than a page-wide banner. -->
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

      <section id="sec-sessions">
        <div class="sec-head"><h2><span id="sessionsTrendTitle">Sessions over time</span> <span class="cmp-warn" id="sessionsCmpWarn" title="" hidden>&#9888;</span></h2><div class="sec-head-actions"><div class="chips seg" id="sessionsGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><span class="status" id="sessionsTrendStatus"></span></div></div>
        <p class="sec-note" id="sessionsScopeNote" hidden></p>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:200px"><canvas id="sessionsTrendChart"></canvas></div></div>
        <div class="cmp-legend" id="sessionsTrendLegend"></div>
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

      <section id="sec-avgduration">
        <div class="sec-head"><h2>Average session duration <span class="cmp-warn" id="avgDurCmpWarn" title="" hidden>&#9888;</span></h2><div class="sec-head-actions"><div class="chips seg" id="avgDurGranChips"><button type="button" class="chip active" data-gran="daily">Daily</button><button type="button" class="chip" data-gran="weekly">Weekly</button></div><span class="status" id="avgDurStatus"></span></div></div>
        <p class="sec-note" id="avgDurNote">How long a session lasted on average, weighted by how many sessions each landing page brought in. Each bar is that day’s own average, so a quiet day with one long visit reads high.</p>
        <div class="cards" id="avgDurCards" style="max-width:220px; margin-bottom:12px"></div>
        <div class="chart-wrap"><div class="chart-canvas-host" style="height:200px"><canvas id="avgDurTrendChart"></canvas></div></div>
        <div class="cmp-legend" id="avgDurTrendLegend"></div>
      </section>

      <section id="sec-demographics">
        <div class="sec-head"><h2 id="demoSectionTitle">Demographics</h2><span class="status" id="demoStatus"></span></div>
        <p class="chart-note" id="demoScopeNote" style="margin-top:0" hidden></p>
        <div class="two-col" style="align-items:start">
          <div class="col-panel">
            <h3 id="stateMapTitle">Users by state</h3>
            <div id="stateMap" class="state-map"></div>
          </div>
          <div class="col-panel">
            <h3>Top cities</h3>
            <div class="table-wrap"><table id="citiesTable" class="compact"></table></div>
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
    const META_VERIFIED_API    = "{_aurl(f'/api/clients/{api_client_key}/meta/verified-conversions')}";
    const GOOGLE_VERIFIED_API  = "{_aurl(f'/api/clients/{api_client_key}/google-ads/verified-conversions')}";
    const LINKEDIN_VERIFIED_API= "{_aurl(f'/api/clients/{api_client_key}/linkedin/verified-conversions')}";
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
    const GSC_KEYWORD_CONFIG_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/keyword-config')}";
    const GSC_KEYWORD_MATCHES_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/keyword-matches')}";
    const GSC_BRANDED_ROOTS = {json.dumps([s.strip() for s in gsc_branded_roots.splitlines() if s.strip()])};
    const GSC_TARGET_KEYWORDS = {json.dumps([s.strip() for s in gsc_target_keywords.splitlines() if s.strip()])};
    const GSC_BRANDED_EXCLUDE = {json.dumps([s.strip() for s in gsc_branded_exclude.splitlines() if s.strip()])};
    const GSC_TARGET_EXCLUDE = {json.dumps([s.strip() for s in gsc_target_exclude.splitlines() if s.strip()])};
    const GSC_WATCHLIST_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/watchlist')}";
    const GSC_WATCHLIST_CONFIG_API = "{_aurl(f'/api/clients/{api_client_key}/gsc/watchlist-config')}";
    // [{{kw, page}}] -- the watched keyword and the page it was written for.
    const GSC_WATCH_ITEMS = {json.dumps(parse_gsc_watchlist(gsc_watch_keywords))};
    const GSC_BRANDED_RAW = {json.dumps(gsc_branded_roots)};
    const GSC_TARGET_RAW = {json.dumps(gsc_target_keywords)};
    const LANDING_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/landing-events')}";
    const TRAFFIC_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/pages/traffic-key-events')}";
    const USER_ACQ_KEY_EVENTS_API = "{_aurl(f'/api/clients/{api_client_key}/analytics/user-acq-key-events')}";
    const GA4_KEY_EVENTS_SAVED = {json.dumps([s.strip() for s in ga4_key_events.splitlines() if s.strip()])};
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
    // Comparison-period modes for the Compare picker, and the localStorage key
    // the viewer's choice is remembered under (per client, per browser -- it's a
    // reading preference, unlike the admin-set client default range).
    const COMPARE_MODE_LABELS = {compare_mode_labels_json};
    const COMPARE_MODE_STORAGE_KEY = 'sf.compareMode.{client_slug}';
    // Campaign Explorer allowlist (campaign names the client may see; empty = all)
    // and the admin-only endpoint that saves it from the "Campaigns" picker.
    const EXPLORER_CAMPAIGN_ALLOWLIST = {explorer_campaign_allowlist_json};
    const EXPLORER_CAMPAIGNS_API = "{_aurl(f'/api/clients/{api_client_key}/explorer/campaigns')}";
    // Admin endpoint behind the Explorer's Budget tracking panel Hide/Show (form
    // POST show=1/0). Uses client_slug (a /dashboard route), not api_client_key.
    const BUDGET_VISIBILITY_API = "{_aurl(f'/dashboard/{client_slug}/budget-visibility')}";

    // ---- Panel edit mode: hide / show / reorder (admin only) ----
    // Same editor on Overview and Campaign Explorer. Admins enter edit mode for
    // a pane from that tab's sidebar kebab, then hide a panel (it greys out but
    // stays, so it can be shown back), show a hidden one, or drag panels to
    // reorder. Every change persists the full {{order, hidden}} for that tab to
    // its card-layout endpoint; the server applies it on the next render (and
    // omits hidden panels from clients' HTML entirely). Optimistic: the DOM is
    // already correct after the edit, so we don't reload on success.
    //
    // The one exception is the Explorer's Budget tracking panel: its visibility
    // has its own per-client setting (the same one the settings page's "Show on
    // Explorer" toggle writes), so its Hide/Show posts there instead and the
    // layout's hidden set stays quiet about it.
    (function () {{
      const shell = document.getElementById('appShell');
      if (!shell || !shell.classList.contains('is-admin')) return;

      // One editor per editable pane, keyed by its tab so the sidebar kebab can
      // find it. A pane with no banner isn't editable (nothing to persist to).
      const editors = {{}};

      function makeEditor(pane) {{
        const tab = pane.getAttribute('data-edit-pane') || '';
        const banner = pane.querySelector('.ov-editing-banner');
        if (!tab || !banner) return null;
        const LAYOUT_API = banner.getAttribute('data-ov-layout-api') || '';
        const statusEl = banner.querySelector('.ov-edit-status');
        const doneBtn = banner.querySelector('.ov-edit-done');

        function setStatus(msg, isError) {{
          if (!statusEl) return;
          statusEl.textContent = msg || '';
          statusEl.classList.toggle('is-error', !!isError);
        }}

        function setEditing(on) {{
          pane.classList.toggle('is-editing', !!on);
          if (!on) setStatus('');
          else {{
            // Bring the pane into view when entering from the sidebar.
            try {{ pane.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); }} catch (e) {{}}
          }}
        }}

        // Exit via the banner's Done button.
        if (doneBtn) doneBtn.addEventListener('click', function () {{ setEditing(false); }});

        // Collect the current layout from the live DOM: order = every panel in
        // document order; hidden = the ones flagged with the hidden class. The
        // budget panel is left out of `hidden` on purpose — see the note above.
        function currentLayout() {{
          const units = Array.prototype.slice.call(pane.querySelectorAll('.ov-unit'));
          const order = [], hidden = [];
          units.forEach(function (u) {{
            const key = u.getAttribute('data-ov-card');
            if (!key) return;
            order.push(key);
            if (key !== 'budget' && u.classList.contains('ov-unit--hidden')) hidden.push(key);
          }});
          return {{ order: order, hidden: hidden }};
        }}

        let saveSeq = 0;
        function persist() {{
          const payload = currentLayout();
          const seq = ++saveSeq;
          setStatus('Saving…', false);
          fetch(LAYOUT_API, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            credentials: 'same-origin',
            body: JSON.stringify(payload),
          }}).then(function (r) {{
            return r.json().catch(function () {{ return {{}}; }}).then(function (b) {{
              if (!r.ok || !b.ok) throw new Error((b && b.detail && (b.detail.error || b.detail)) || r.statusText);
              if (seq === saveSeq) setStatus('Saved', false);
            }});
          }}).catch(function (err) {{
            if (seq === saveSeq) setStatus('Could not save: ' + (err.message || err), true);
          }});
        }}

        // The budget panel's visibility is a portal-wide client setting, not part
        // of the layout: post it to its own endpoint and reflect the result in
        // the same status line.
        function persistBudget(nowHidden, unit, btn) {{
          setStatus('Saving…', false);
          fetch(BUDGET_VISIBILITY_API, {{
            method: 'POST', credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
            body: 'show=' + (nowHidden ? '0' : '1'),
          }}).then(function (r) {{ return r.json().catch(function () {{ return {{}}; }}); }})
            .then(function (b) {{
              if (b && b.ok) {{ setStatus('Saved', false); return; }}
              throw new Error((b && b.error) || 'unknown error');
            }}).catch(function (err) {{
              // Put the panel back the way it was so the page keeps telling the truth.
              unit.classList.toggle('ov-unit--hidden', !nowHidden);
              btn.setAttribute('aria-pressed', nowHidden ? 'false' : 'true');
              btn.textContent = nowHidden ? 'Hide' : 'Show';
              setStatus('Could not save: ' + (err.message || err), true);
            }});
        }}

        // Hide / show a panel.
        pane.addEventListener('click', function (ev) {{
          const btn = ev.target.closest && ev.target.closest('.ov-hide-toggle');
          if (!btn || !pane.contains(btn)) return;
          ev.preventDefault();
          const unit = btn.closest('.ov-unit');
          if (!unit) return;
          const nowHidden = unit.classList.toggle('ov-unit--hidden');
          btn.setAttribute('aria-pressed', nowHidden ? 'true' : 'false');
          btn.textContent = nowHidden ? 'Show' : 'Hide';
          if (unit.getAttribute('data-ov-card') === 'budget') persistBudget(nowHidden, unit, btn);
          else persist();
        }});

        // Drag to reorder. The handle is the drag source; we move its parent unit.
        let dragUnit = null;
        pane.addEventListener('dragstart', function (ev) {{
          const handle = ev.target.closest && ev.target.closest('.ov-drag');
          if (!handle || !pane.classList.contains('is-editing')) return;
          dragUnit = handle.closest('.ov-unit');
          if (!dragUnit) return;
          dragUnit.classList.add('is-dragging');
          if (ev.dataTransfer) {{
            ev.dataTransfer.effectAllowed = 'move';
            try {{ ev.dataTransfer.setData('text/plain', dragUnit.getAttribute('data-ov-card') || ''); }} catch (e) {{}}
          }}
        }});
        pane.addEventListener('dragover', function (ev) {{
          if (!dragUnit) return;
          const over = ev.target.closest && ev.target.closest('.ov-unit');
          if (!over || over === dragUnit || !pane.contains(over)) return;
          ev.preventDefault();
          if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
          const rect = over.getBoundingClientRect();
          const after = (ev.clientY - rect.top) > rect.height / 2;
          pane.querySelectorAll('.ov-unit.is-drag-over').forEach(function (u) {{ u.classList.remove('is-drag-over'); }});
          over.classList.add('is-drag-over');
          if (after) over.parentNode.insertBefore(dragUnit, over.nextSibling);
          else over.parentNode.insertBefore(dragUnit, over);
        }});
        function endDrag(persistIt) {{
          pane.querySelectorAll('.ov-unit.is-drag-over').forEach(function (u) {{ u.classList.remove('is-drag-over'); }});
          if (dragUnit) {{
            dragUnit.classList.remove('is-dragging');
            dragUnit = null;
            if (persistIt) persist();
          }}
        }}
        pane.addEventListener('drop', function (ev) {{ if (dragUnit) {{ ev.preventDefault(); endDrag(true); }} }});
        pane.addEventListener('dragend', function () {{ endDrag(true); }});

        return {{ tab: tab, pane: pane, setEditing: setEditing }};
      }}

      document.querySelectorAll('.ov-editable').forEach(function (pane) {{
        const ed = makeEditor(pane);
        if (ed) editors[ed.tab] = ed;
      }});
      if (!Object.keys(editors).length) return;

      // Only one pane edits at a time — entering edit mode on one leaves the other.
      function editOnly(tab) {{
        Object.keys(editors).forEach(function (k) {{ editors[k].setEditing(k === tab); }});
      }}
      function exitAll() {{
        Object.keys(editors).forEach(function (k) {{ editors[k].setEditing(false); }});
      }}
      function anyEditing() {{
        return Object.keys(editors).some(function (k) {{
          return editors[k].pane.classList.contains('is-editing');
        }});
      }}

      // ---- Entry point: the sidebar kebab (⋮) on an editable nav item ----
      // A small popover menu whose "Edit layout" item switches to that tab and
      // drops into edit mode. Menu open/close is handled here so the sidebar
      // renderer stays presentational.
      function closeMenus() {{
        document.querySelectorAll('.dash-view-item.menu-open').forEach(function (it) {{
          it.classList.remove('menu-open');
          const k = it.querySelector('.dash-view-kebab');
          const m = it.querySelector('.dash-view-menu');
          if (k) k.setAttribute('aria-expanded', 'false');
          if (m) m.hidden = true;
        }});
      }}
      document.addEventListener('click', function (ev) {{
        const kebab = ev.target.closest && ev.target.closest('.dash-view-kebab');
        if (kebab) {{
          ev.preventDefault();
          ev.stopPropagation();
          const item = kebab.closest('.dash-view-item');
          const menu = item && item.querySelector('.dash-view-menu');
          const willOpen = !(item && item.classList.contains('menu-open'));
          closeMenus();
          if (willOpen && item) {{
            item.classList.add('menu-open');
            kebab.setAttribute('aria-expanded', 'true');
            if (menu) menu.hidden = false;
          }}
          return;
        }}
        const action = ev.target.closest && ev.target.closest('.dash-view-menu-item');
        if (action) {{
          ev.preventDefault();
          ev.stopPropagation();
          closeMenus();
          if (action.getAttribute('data-action') === 'edit-layout') {{
            const item = action.closest('.dash-view-item');
            const tab = item && item.getAttribute('data-view-item');
            if (!tab || !editors[tab]) return;
            // Make sure that tab is the active one, then enter edit mode.
            const navBtn = document.querySelector('.dash-view-btn[data-tab="' + tab + '"]');
            if (navBtn && !navBtn.classList.contains('active')) navBtn.click();
            editOnly(tab);
          }}
          return;
        }}
        // A click anywhere else dismisses any open kebab menu.
        closeMenus();
      }});
      document.addEventListener('keydown', function (ev) {{
        if (ev.key === 'Escape') {{
          if (anyEditing()) exitAll();
          closeMenus();
        }}
      }});
    }})();

    // ---- Formatters ----
    const dollars = new Intl.NumberFormat('en-US', {{ style:'currency', currency:'USD', maximumFractionDigits:2 }});
    const nums    = new Intl.NumberFormat('en-US');
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    // Shorten long URL paths for display; the full value stays in the cell
    // tooltip. Middle ellipsis keeps the leading segment and the distinguishing
    // tail both visible (paths often share a long common prefix).
    const PATH_MAX = 24;
    function truncPath(path, max) {{
      max = max || PATH_MAX;
      const s = String(path == null ? '' : path);
      if (s.length <= max) return s;
      const keep = max - 1;
      const head = Math.ceil(keep / 2);
      const tail = keep - head;
      return s.slice(0, head) + '\\u2026' + s.slice(s.length - tail);
    }}
    // Drag-to-resize table columns. Widths persist per table across re-renders
    // and pagination. On first render we freeze the auto-computed widths, then
    // switch to a fixed layout so columns can be widened *or* narrowed by
    // dragging their right edge.
    const colWidths = {{}};
    function enableColResize(tableId) {{
      const table = document.getElementById(tableId);
      if (!table) return;
      const ths = [...table.querySelectorAll('thead th')];
      if (!ths.length) return;
      const store = colWidths[tableId] || (colWidths[tableId] = {{}});
      // Safe to call again on the same thead (a tab switch re-runs it) — drop any
      // grips from a previous pass instead of stacking a second set.
      ths.forEach(th => th.querySelectorAll(':scope > .col-resizer').forEach(g => g.remove()));
      table.style.tableLayout = 'auto';
      // A table rendered inside a hidden tab has no layout to freeze — every
      // column would measure 0 and collapse. Leave it auto; showPanelTab calls
      // back once the pane is on screen.
      if (!table.getBoundingClientRect().width) return;
      ths.forEach((th, idx) => {{ if (store[idx] == null) store[idx] = Math.round(th.getBoundingClientRect().width); }});
      ths.forEach((th, idx) => {{ th.style.width = store[idx] + 'px'; }});
      table.style.tableLayout = 'fixed';
      ths.forEach((th, idx) => {{
        const grip = document.createElement('span');
        grip.className = 'col-resizer';
        grip.title = 'Drag to resize';
        // Keep the grip from triggering the header's (delegated) sort handler.
        grip.addEventListener('click', e => e.stopPropagation());
        grip.addEventListener('mousedown', e => {{
          e.preventDefault();
          e.stopPropagation();
          const startX = e.pageX;
          const startW = th.getBoundingClientRect().width;
          document.body.classList.add('col-resizing');
          const onMove = ev => {{
            const w = Math.max(48, Math.round(startW + (ev.pageX - startX)));
            th.style.width = w + 'px';
            store[idx] = w;
          }};
          const onUp = () => {{
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.body.classList.remove('col-resizing');
          }};
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        }});
        th.appendChild(grip);
      }});
    }}
    const num  = v => Number(v || 0);
    const money  = v => dollars.format(num(v));
    const count  = v => nums.format(Math.round(num(v)));
    const pct    = v => `${{num(v).toFixed(2)}}%`;
    function fmtDuration(secs) {{
      secs = Math.round(num(secs));
      if (secs < 60) return secs + 's';
      const m = Math.floor(secs / 60), s = secs % 60;
      return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
    }}

    // ---- Chart.js foundation (lib vendored under /static/vendor) ----
    // Shared theme + small factories keep every chart consistent. Instances are
    // tracked per canvas id and destroyed before re-creation (Chart.js will not
    // reuse a canvas that still has a live chart on it).
    const __charts = {{}};
    function __chart(id, config) {{
      const el = document.getElementById(id);
      if (!el || !window.Chart) return null;
      if (__charts[id]) __charts[id].destroy();
      __charts[id] = new Chart(el.getContext('2d'), config);
      return __charts[id];
    }}
    function __destroyChart(id) {{ if (__charts[id]) {{ __charts[id].destroy(); delete __charts[id]; }} }}
    if (window.Chart) {{
      Chart.defaults.font.family = 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
      Chart.defaults.font.size = 11;
      Chart.defaults.color = '#6b7a90';
      Chart.defaults.maintainAspectRatio = false;
      Chart.defaults.animation.duration = 300;
      Chart.defaults.plugins.legend.display = false;
      const _tt = Chart.defaults.plugins.tooltip;
      _tt.backgroundColor = '#0b1020'; _tt.titleColor = '#e8eefc'; _tt.bodyColor = '#e8eefc';
      _tt.padding = 9; _tt.cornerRadius = 8; _tt.boxPadding = 4; _tt.usePointStyle = true;
    }}
    // Vertical gradient fill under an area line, built from the plot geometry.
    function __areaFill(context, color) {{
      const c = context.chart.ctx, a = context.chart.chartArea;
      if (!a) return color + '00';
      const g = c.createLinearGradient(0, a.top, 0, a.bottom);
      g.addColorStop(0, color + '33'); g.addColorStop(1, color + '00');
      return g;
    }}
    // Line chart. series: [{{label, data, color, fill?, dashed?, raw?, fmt?}}].
    // `data` is what's plotted (may be normalized); `raw`/`fmt` drive tooltips.
    function lineChart(id, labels, series, opts) {{
      opts = opts || {{}};
      const datasets = series.map(s => ({{
        label: s.label, data: s.data, borderColor: s.color,
        backgroundColor: s.fill ? (ctx => __areaFill(ctx, s.color)) : 'transparent',
        fill: !!s.fill, borderWidth: 2.25, tension: 0.35,
        borderDash: s.dashed ? [5, 4] : [],
        pointRadius: opts.points ? 2.5 : 0, pointHoverRadius: 4, pointBackgroundColor: s.color,
        _raw: s.raw || s.data, _fmt: s.fmt || count,
      }}));
      const chart = __chart(id, {{
        type: 'line',
        data: {{ labels, datasets }},
        options: {{
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            x: {{ grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: opts.xTicks || 6 }} }},
            y: {{ display: opts.yDisplay !== false, reverse: !!opts.yReverse, beginAtZero: opts.beginAtZero !== false,
                 grid: {{ color: '#f1f4f9' }}, border: {{ display: false }},
                 ticks: {{ maxTicksLimit: 4, callback: opts.yFmt || (v => v) }} }},
          }},
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: opts.tooltip || {{
              label: c => `${{c.dataset.label}}: ${{c.dataset._fmt(c.dataset._raw[c.dataIndex])}}`,
            }} }},
          }},
        }},
        // `dates` is the ISO date behind each label. Charts that pass it get
        // timeline annotations drawn over them; charts that don't are untouched.
        plugins: opts.dates ? [annoLinePlugin(opts.dates)] : [],
      }});
      if (opts.dates) syncAnnoPins(id, opts.dates);
      return chart;
    }}

    // Bar chart, same option surface as lineChart (yFmt / tooltip / dates), for
    // series where each point is its own reading rather than a flow -- a daily
    // average reads as a row of bars, not a slope. A series tagged
    // kind:'line' rides the same axes, which is how a comparison period is
    // overlaid without doubling the bars.
    function barChart(id, labels, series, opts) {{
      opts = opts || {{}};
      const datasets = series.map(s => s.kind === 'line'
        ? ({{
            type: 'line', label: s.label, data: s.data, borderColor: s.color,
            backgroundColor: 'transparent', fill: false, borderWidth: 2,
            borderDash: s.dashed ? [5, 4] : [], tension: 0.35,
            pointRadius: 0, pointHoverRadius: 4, pointBackgroundColor: s.color,
            order: 0, _raw: s.raw || s.data, _fmt: s.fmt || count,
          }})
        : ({{
            label: s.label, data: s.data, backgroundColor: s.color,
            hoverBackgroundColor: s.hoverColor || s.color,
            borderRadius: 3, borderSkipped: false,
            maxBarThickness: opts.maxBarThickness || 22,
            order: 1, _raw: s.raw || s.data, _fmt: s.fmt || count,
          }}));
      const chart = __chart(id, {{
        type: 'bar',
        data: {{ labels, datasets }},
        options: {{
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            x: {{ grid: {{ display: false }}, border: {{ display: false }}, ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: opts.xTicks || 6 }} }},
            y: {{ display: opts.yDisplay !== false, beginAtZero: opts.beginAtZero !== false,
                 grid: {{ color: '#f1f4f9' }}, border: {{ display: false }},
                 ticks: {{ maxTicksLimit: 4, callback: opts.yFmt || (v => v) }} }},
          }},
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: opts.tooltip || {{
              label: c => `${{c.dataset.label}}: ${{c.dataset._fmt(c.dataset._raw[c.dataIndex])}}`,
            }} }},
          }},
        }},
        plugins: opts.dates ? [annoLinePlugin(opts.dates)] : [],
      }});
      if (opts.dates) syncAnnoPins(id, opts.dates);
      return chart;
    }}

    // ---- Timeline annotations ----
    // A chart can show that traffic fell; only an annotation can say the site was
    // migrated that week. Markers are drawn in two halves for a reason: the
    // vertical rule belongs on the canvas (it has to sit inside the plot area and
    // survive resizes), while the flag at the top is a real DOM element so it
    // gets native hover text and keyboard focus. Nothing here runs for a
    // client-role user beyond drawing the shared markers they are allowed to see.
    // Adding/editing events happens on the Settings → Insights page, not here.
    let annoCache = [];
    let annoCanEdit = false;
    let annoCategories = [];
    let annoLoaded = false;

    // The index of the plotted point an annotation belongs to: the last date at
    // or before it. Weekly-aggregated charts label each bucket with its Monday,
    // so a Thursday event correctly lands on that week's point.
    function annoIndexFor(dates, iso) {{
      let found = -1;
      for (let i = 0; i < dates.length; i++) {{
        if (String(dates[i]) <= iso) found = i; else break;
      }}
      // An annotation before the first plotted day still marks the chart's start
      // when its range overlaps the window; one after the last is off-chart.
      if (found < 0) return (annoOverlapsStart(iso, dates)) ? 0 : -1;
      return found;
    }}
    function annoOverlapsStart(iso, dates) {{ return dates.length > 0 && iso < String(dates[0]); }}

    // Annotations that fall inside (or overlap) the dates a chart is showing.
    function annoVisibleFor(dates) {{
      if (!annoCache.length || !dates || !dates.length) return [];
      const first = String(dates[0]), last = String(dates[dates.length - 1]);
      return annoCache
        .filter(a => {{
          const start = String(a.event_date || '');
          const end = String(a.end_date || a.event_date || '');
          return start <= last && end >= first;   // any overlap with the window
        }})
        .map(a => ({{ anno: a, idx: annoIndexFor(dates, String(a.event_date)) }}))
        .filter(m => m.idx >= 0);
    }}

    function annoLinePlugin(dates) {{
      return {{
        id: 'sfAnnotations',
        afterDatasetsDraw(chart) {{
          const markers = annoVisibleFor(dates);
          if (!markers.length) return;
          const area = chart.chartArea, xs = chart.scales.x;
          if (!area || !xs) return;
          const ctx = chart.ctx;
          ctx.save();
          for (const m of markers) {{
            const x = xs.getPixelForValue(m.idx);
            if (x < area.left - 1 || x > area.right + 1) continue;
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = m.anno.color || '#64748b';
            ctx.globalAlpha = 0.75;
            ctx.moveTo(x, area.top);
            ctx.lineTo(x, area.bottom);
            ctx.stroke();
          }}
          ctx.restore();
        }},
      }};
    }}

    // The DOM half: one focusable pin per marker, positioned over the canvas.
    function syncAnnoPins(chartId, dates) {{
      const canvas = document.getElementById(chartId);
      if (!canvas) return;
      const host = canvas.parentElement;
      if (!host) return;
      host.classList.add('anno-host');
      let layer = host.querySelector('.anno-layer');
      if (!layer) {{
        layer = document.createElement('div');
        layer.className = 'anno-layer';
        host.appendChild(layer);
      }}
      const chart = __charts[chartId];
      const markers = annoVisibleFor(dates);
      // chartArea is only populated once the chart has laid out; bail quietly
      // rather than positioning pins against undefined geometry.
      if (!chart || !chart.scales || !chart.scales.x || !chart.chartArea || !markers.length) {{
        layer.innerHTML = ''; return;
      }}
      const xs = chart.scales.x, area = chart.chartArea;
      layer.innerHTML = markers.map(m => {{
        const x = xs.getPixelForValue(m.idx);
        if (x < area.left - 1 || x > area.right + 1) return '';
        const a = m.anno;
        const when = a.end_date ? `${{a.event_date}} → ${{a.end_date}}` : a.event_date;
        const shared = a.visibility === 'shared' ? 'Shared with the client' : 'Internal — the client does not see this';
        const tip = `${{when}} · ${{a.category_label}}\\n${{a.title}}`
          + (a.body ? `\\n\\n${{a.body}}` : '')
          + `\\n\\n${{shared}}${{annoCanEdit ? ' · edit under Settings → Insights' : ''}}`;
        return `<button type="button" class="anno-pin${{a.visibility === 'internal' ? ' internal' : ''}}"`
          + ` style="left:${{x.toFixed(1)}}px;--anno-color:${{esc(a.color)}}" data-anno-id="${{a.id}}"`
          + ` title="${{esc(tip)}}" aria-label="${{esc(when + ': ' + a.title)}}"></button>`;
      }}).join('');
    }}

    // Redraw every chart that carries annotations — used after an edit so the
    // markers update without a page reload.
    const ANNOTATED_CHARTS = [];
    function registerAnnotatedChart(fn) {{ if (ANNOTATED_CHARTS.indexOf(fn) < 0) ANNOTATED_CHARTS.push(fn); }}
    function refreshAnnotatedCharts() {{ for (const fn of ANNOTATED_CHARTS) {{ try {{ fn(); }} catch (e) {{}} }} }}

    async function loadAnnotations(force) {{
      if (annoLoaded && !force) return;
      try {{
        const payload = await getJson(ANNOTATIONS_API);
        annoCache = payload.annotations || [];
        annoCanEdit = !!payload.can_edit;
        annoCategories = payload.categories || [];
        annoLoaded = true;
        refreshAnnotatedCharts();
      }} catch (err) {{
        // Markers are additive context; a chart without them is still correct.
        annoCache = []; annoCanEdit = false;
      }}
    }}

    function withDates(base) {{
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + currentStart + '&end_date=' + currentEnd;
    }}
    function withDatesRange(base, s, e) {{
      const sep = base.includes('?') ? '&' : '?';
      return base + sep + 'start_date=' + s + '&end_date=' + e;
    }}
    // Endpoints that compute their own vs-previous figures server-side (the
    // Search Console ones) need the comparison window spelled out, or they fall
    // back to the preceding period and contradict the Compare picker.
    function withCompare(url) {{
      if (!compareStart || !compareEnd) return url;
      const sep = url.includes('?') ? '&' : '?';
      return url + sep + 'compare_start_date=' + compareStart + '&compare_end_date=' + compareEnd;
    }}
    // ---- Period-over-period comparison helpers ----
    function deltaHtml(curr, prev) {{
      curr = num(curr);
      if (prev == null) return '';
      prev = num(prev);
      const vs = cmpNoun();
      if (!prev) {{
        if (!curr) return `<div class="card-delta flat">No change vs ${{vs}}</div>`;
        return `<div class="card-delta up">New vs ${{vs}}</div>`;
      }}
      const change = ((curr - prev) / Math.abs(prev)) * 100;
      const dir = change > 0.05 ? 'up' : (change < -0.05 ? 'down' : 'flat');
      const arrow = dir==='up' ? '\\u25B2' : (dir==='down' ? '\\u25BC' : '\\u2014');
      return `<div class="card-delta ${{dir}}">${{arrow}} ${{Math.abs(change).toFixed(1)}}% vs ${{vs}}</div>`;
    }}
    // True if the comparison window (compareStart/compareEnd) reaches back
    // before any of the given sources' synced history -- earliestDates is
    // populated from the /marketing/health payload by loadHealth().
    function cmpBlockedBy(sourceKeys) {{
      for (const k of sourceKeys) {{
        const earliest = earliestDates[k];
        if (earliest && compareStart && compareStart < earliest) return earliest;
      }}
      return null;
    }}
    function setCmpWarn(elId, sourceKeys) {{
      const el = document.getElementById(elId);
      if (!el) return;
      const blockedSince = cmpBlockedBy(sourceKeys);
      if (blockedSince) {{
        el.hidden = false;
        el.title = `Comparison period (${{compareStart}} to ${{compareEnd}}) starts before synced data begins (${{blockedSince}}). The "vs ${{cmpNoun()}}" figures above may be incomplete.`;
      }} else {{
        el.hidden = true;
        el.title = '';
      }}
    }}
    // Human names for the /marketing/health source keys, for the notice below.
    const CMP_SOURCE_LABELS = {{
      google:'Google Ads', linkedin:'LinkedIn Ads', meta:'Meta Ads',
      microsoft:'Microsoft Ads', google_analytics:'GA4', gsc:'Search Console',
    }};
    // Page-level version of the per-section warning icons: name every source
    // whose synced history starts after the comparison window does. Usually
    // silent on a previous-period comparison and loud on a previous-year one,
    // which is the signal that the warehouse needs a deeper backfill.
    function syncCompareNotice() {{
      const el = document.getElementById('compareNotice');
      const tip = document.getElementById('compareNoticeTip');
      if (!el || !tip) return;
      const gaps = [];
      if (compareStart) {{
        for (const k of Object.keys(earliestDates)) {{
          const earliest = earliestDates[k];
          if (earliest && compareStart < earliest) {{
            gaps.push(`${{CMP_SOURCE_LABELS[k] || k}} (from ${{earliest}})`);
          }}
        }}
      }}
      if (!gaps.length) {{ el.hidden = true; tip.innerHTML = ''; return; }}
      gaps.sort();
      const mode = COMPARE_MODE_LABELS[compareMode] || 'Previous period';
      el.hidden = false;
      tip.innerHTML = `No synced data covers all of the <strong>${{esc(mode.toLowerCase())}}</strong> comparison window `
        + `(${{esc(compareStart)}} – ${{esc(compareEnd)}}): ${{esc(gaps.join(', '))}}. `
        + 'Every "vs ' + esc(cmpNoun()) + '" figure below is measured against a partial window until those sources are backfilled.';
    }}
    async function getJson(url, _attempt) {{
      _attempt = _attempt || 0;
      const MAX_RETRIES = 2;
      // Growing backoff with jitter: ~0.4-0.8s, then ~0.8-1.2s.
      const backoff = () => new Promise(r => setTimeout(r, 400 * (_attempt + 1) + Math.random()*400));
      let resp;
      try {{
        resp = await fetch(url, {{ credentials:'same-origin' }});
      }} catch (netErr) {{
        // fetch() rejects (no HTTP status) on network drops, cold-start
        // connection resets and platform proxy blips -- treat like a 5xx and
        // retry before surfacing the failure.
        if (_attempt < MAX_RETRIES) {{ await backoff(); return getJson(url, _attempt + 1); }}
        throw netErr;
      }}
      if (!resp.ok && resp.status >= 500 && _attempt < MAX_RETRIES) {{
        // 5xx here has mostly been transient BQ concurrency pressure / cold
        // starts, not a real failure -- retry with growing backoff.
        await backoff();
        return getJson(url, _attempt + 1);
      }}
      const body = await resp.json().catch(() => ({{ detail:resp.statusText }}));
      if (!resp.ok) {{
        // FastAPI error bodies here are {{detail: {{error, type}}}} -- surface the
        // actual message instead of stringifying the whole object.
        const d = body && body.detail;
        const msg = (d && typeof d === 'object') ? (d.error || JSON.stringify(d)) : d;
        throw new Error(msg || resp.statusText || 'Request failed');
      }}
      return body;
    }}
    function setStatus(id, text, isError) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.className = isError ? 'status error' : 'status';
      el.style.display = text ? '' : 'none';
    }}
    function renderTable(id, columns, rows, emptyText) {{
      const el = document.getElementById(id);
      if (!rows || !rows.length) {{ el.innerHTML = `<tbody><tr><td class="empty">${{esc(emptyText)}}</td></tr></tbody>`; return; }}
      el.innerHTML =
        `<thead><tr>${{columns.map(c => `<th class="${{c.left ? 'left' : ''}}">${{esc(c.label)}}</th>`).join('')}}</tr></thead>` +
        `<tbody>${{rows.map(row => `<tr>${{columns.map(c => `<td class="${{c.left ? 'left' : ''}}">${{esc(c.format ? c.format(row[c.key], row) : row[c.key])}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
    }}
    function pctBar(pct) {{
      return `<div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100, pct).toFixed(1)}}%"></div></div>`;
    }}

    // ---- Tab system ----
    const TABS = ['overview', 'explorer', 'analytics', 'ai_traffic', 'gsc', 'site_performance'];
    let currentTab = 'overview';
    let analyticsLoaded = false;
    let explorerLoaded = false;
    let gscLoaded = false;
    let aiTrafficLoaded = false;

    function switchTab(tab) {{
      TABS.forEach(t => {{ document.getElementById('pane-' + t).hidden = t !== tab; }});
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      const efb = document.getElementById('explorerFilterBar');
      if (efb) efb.hidden = !(tab === 'explorer' && EXPLORER_FILTER_GROUPS.length);
      // Site Performance shows one latest PageSpeed snapshot plus its own trend,
      // so the sticky Range/Compare filters have nothing to act on — hide the
      // whole filter bar on that tab rather than leaving dead controls.
      const dateBar = document.querySelector('.date-bar');
      if (dateBar) dateBar.hidden = tab === 'site_performance';
      currentTab = tab;
      // Keep the tab in the URL so a refresh reopens the same page (not
      // Overview), and so switching clients can carry the tab across. Overview
      // is the canonical home, so it drops ?view= to keep that URL clean.
      try {{
        const u = new URL(location.href);
        if (tab === 'overview') u.searchParams.delete('view');
        else u.searchParams.set('view', tab);
        history.replaceState(null, '', u);
      }} catch (e) {{ /* ignore */ }}
      updateKeyEventBar();
      if (tab === 'explorer' && !explorerLoaded) {{
        explorerLoaded = true;
        loadExplorer();
      }}
      // The budget module lazy-loads via IntersectionObserver, which is
      // unreliable when the pane is revealed below the fold (blank until a
      // scroll/refresh/toggle). Trigger it directly on tab open; it's a no-op
      // once already loaded. (Absent when the client has no paid-ads budget.)
      if (tab === 'explorer' && typeof window.ensureBudgetLoaded === 'function') {{
        window.ensureBudgetLoaded();
      }}
      if (tab === 'ai_traffic' && !aiTrafficLoaded) {{
        aiTrafficLoaded = true;
        loadAiTraffic();
      }}
      if (tab === 'analytics' && !analyticsLoaded) {{
        analyticsLoaded = true;
        applyModules();
        loadAllAnalytics();
      }}
      if (tab === 'gsc' && !gscLoaded) {{
        gscLoaded = true;
        loadGsc();
        loadSemrush();
      }}
      if (tab === 'site_performance' && !sitePerfLoaded) {{
        sitePerfLoaded = true;
        loadSitePerformance();
      }}
    }}

    document.querySelectorAll('.tab-btn').forEach(btn =>
      btn.addEventListener('click', () => switchTab(btn.dataset.tab))
    );

    // ---- Module system (localStorage) ----
    const ALL_MODULES = ['sessions','top_pages','traffic','audience','landing','user_acquisition','avg_duration','demographics'];
    // Element a module owns. For the four modules that share a tabbed card
    // (top_pages/landing, traffic/user_acquisition) this is the tab's pane, and
    // applyPanelCards — not applyModules — decides when it shows.
    const MODULE_SECTIONS = {{
      sessions:'sec-sessions', top_pages:'sec-pages', traffic:'sec-traffic', audience:'sec-audience',
      landing:'sec-landing',
      user_acquisition:'sec-useracq', avg_duration:'sec-avgduration', demographics:'sec-demographics'
    }};

    // Modules hidden while a page-path scope is active: they aggregate whole-site
    // GA4 sessions/users and have no page_path to scope by, so showing them next
    // to careers-only Pages/Landing would be misleading. Sessions-over-time is NOT
    // in this list — its daily series is served page-scoped from vw_page_path_daily
    // (see fetch_traffic_acquisition), as are Top pages and Landing pages.
    // Demographics is likewise not hidden: its geography half reads the page-scoped
    // vw_ga4_geo_page_daily under a scope (see fetch_demographics), and only its
    // user-scoped age/gender panels hide — that's demoUserPanels below.
    const PATH_FILTER_HIDDEN_MODULES = ['traffic','audience','user_acquisition'];
    function pathFilterActive() {{ return Array.isArray(ANALYTICS_PATH_FILTER) && ANALYTICS_PATH_FILTER.length > 0; }}
    function getModules() {{
      let modules;
      try {{
        const s = localStorage.getItem('nixon_analytics_modules');
        const saved = s ? JSON.parse(s) : {{}};
        modules = ALL_MODULES.reduce((o, k) => ({{...o, [k]: k in saved ? saved[k] : true}}), {{}});
      }} catch {{ modules = ALL_MODULES.reduce((o, k) => ({{...o, [k]: true}}), {{}}); }}
      if (pathFilterActive()) PATH_FILTER_HIDDEN_MODULES.forEach(k => {{ modules[k] = false; }});
      return modules;
    }}

    // ---- Tabbed panel cards ----
    // Two modules share one full-width card and swap on a tab. The module system
    // still owns whether each half exists at all, so a card follows its tabs:
    // one module off hides that tab (and falls back to the other if it was the
    // one on screen), both off hides the card.
    const PANEL_CARDS = {{
      pages: {{ card:'card-pages', tabs:[
        {{module:'top_pages', pane:'sec-pages',    status:'pagesStatus'}},
        {{module:'landing',   pane:'sec-landing',  status:'landingStatus'}},
      ]}},
      acq: {{ card:'card-acq', tabs:[
        {{module:'traffic',          pane:'sec-traffic', status:'trafficAcqStatus'}},
        {{module:'user_acquisition', pane:'sec-useracq', status:'userAcqStatus'}},
      ]}},
      // Search Console's Branded / Target queries. No module gates these two —
      // they are the same Search Console data cut two ways — so they carry no
      // `module` and are always live.
      gsckw: {{ card:'card-gsc-kw', tabs:[
        {{pane:'sec-gsc-branded', status:null}},
        {{pane:'sec-gsc-target',  status:null}},
      ]}},
    }};
    // Pane on screen per card. Defaults match the markup.
    const panelOpen = {{ pages:'sec-pages', acq:'sec-traffic', gsckw:'sec-gsc-branded' }};

    function showPanelTab(grp, pane) {{
      const cfg = PANEL_CARDS[grp];
      if (!cfg || !cfg.tabs.some(t => t.pane === pane)) return;
      panelOpen[grp] = pane;
      cfg.tabs.forEach(t => {{
        const on = t.pane === pane;
        const el = document.getElementById(t.pane);
        const btn = document.getElementById('tab-' + t.pane);
        const st = document.getElementById(t.status);
        if (el) el.hidden = !on;
        if (btn) btn.setAttribute('aria-selected', on ? 'true' : 'false');
        if (st) st.hidden = !on;
      }});
      // Controls that live in the card head but belong to one pane (the
      // Branded / Target keyword editors' Edit button) follow the tab too.
      const cardEl = document.getElementById(cfg.card);
      if (cardEl) cardEl.querySelectorAll('[data-pnl-for]').forEach(el => {{
        el.hidden = el.getAttribute('data-pnl-for') !== pane;
      }});
      // Column widths are frozen from a laid-out table, and a table first
      // rendered inside a hidden pane measures nothing — so re-run the freeze
      // now that the pane is on screen (enableColResize is idempotent).
      const shown = document.getElementById(pane);
      if (shown) shown.querySelectorAll('table.resizable').forEach(t => enableColResize(t.id));
      // Same story for a chart drawn behind a closed tab: it measured a 0-high
      // host. Chart.js re-measures on its own resize observer, but nudge it so
      // the chart is right on the first frame rather than the second.
      if (shown) shown.querySelectorAll('canvas[id]').forEach(c => {{
        const ch = __charts[c.id];
        if (ch) {{ try {{ ch.resize(); }} catch (e) {{}} }}
      }});
    }}

    document.querySelectorAll('.pnl-tab').forEach(btn =>
      btn.addEventListener('click', () => showPanelTab(btn.dataset.pnl, btn.dataset.pane))
    );

    function applyPanelCards(modules) {{
      Object.entries(PANEL_CARDS).forEach(([grp, cfg]) => {{
        const card = document.getElementById(cfg.card);
        const live = cfg.tabs.filter(t => !t.module || modules[t.module]);
        cfg.tabs.forEach(t => {{
          const btn = document.getElementById('tab-' + t.pane);
          if (btn) btn.hidden = !!t.module && !modules[t.module];
        }});
        if (card) card.hidden = !live.length;
        if (!live.length) return;
        const tablist = card && card.querySelector('.pnl-tabs');
        if (tablist) tablist.classList.toggle('one-tab', live.length === 1);
        // If the pane on screen just lost its module, fall back to what's left.
        showPanelTab(grp, live.some(t => t.pane === panelOpen[grp]) ? panelOpen[grp] : live[0].pane);
      }});
    }}

    function applyModules() {{
      const modules = getModules();
      const tabbed = new Set(Object.values(PANEL_CARDS).flatMap(c => c.tabs.map(t => t.module)).filter(Boolean));
      ALL_MODULES.forEach(key => {{
        if (tabbed.has(key)) return;              // handled by applyPanelCards
        const sec = document.getElementById(MODULE_SECTIONS[key]);
        if (sec) sec.hidden = !modules[key];
      }});
      applyPanelCards(modules);
      updateKeyEventBar();
    }}
    // The Events dropdown now lives in the shared sticky bar, so it must only
    // show on the Website Analytics tab — and only when a panel it drives
    // (Top pages / Traffic / Landing / User acquisition) is enabled.
    function updateKeyEventBar() {{
      const keBar = document.getElementById('keyEventFilterGroup');
      if (!keBar) return;
      const m = getModules();
      const anyPanel = m.top_pages || m.traffic || m.landing || m.user_acquisition;
      keBar.hidden = !(currentTab === 'analytics' && anyPanel);
    }}

    // ---- Paid media: Summary ----
    // 4th field = which direction is "good" for coloring the vs-previous delta:
    // 'up' (more is better), 'down' (less is better), 'neutral' (just report it).
    const SUMMARY_CARDS = [
      ['spend','Spend',money,'neutral'],['impressions','Impressions',count,'up'],['clicks','Clicks',count,'up'],
      ['conversions','Conversions',count,'up'],['cpc','CPC',money,'down'],['cpa','CPA',money,'down'],['ctr','CTR',pct,'up'],
    ];
    const SPARK_COLORS = {{ spend:'#1769aa', impressions:'#7c3aed', clicks:'#0a7f3f', conversions:'#0891b2', cpc:'#d97706', cpa:'#dc2626', ctr:'#0891b2' }};
    const platformFilter = new Set();
    let summaryPayload = null;
    let compareSummaryPayload = null;
    const summaryCards = document.getElementById('summaryCards');

    function selectedSummaryFrom(payload) {{
      if (!payload) return {{}};
      const by = payload.by_source || null;
      if (!by || platformFilter.size === 0) return payload.summary || {{}};
      const acc = {{ spend:0, impressions:0, clicks:0, conversions:0 }};
      const needles = [...platformFilter].map(p => p.toLowerCase());
      for (const k of Object.keys(by)) {{
        if (!needles.some(nd => k.toLowerCase().includes(nd))) continue;
        const src = by[k];
        acc.spend += num(src.spend); acc.impressions += num(src.impressions);
        acc.clicks += num(src.clicks); acc.conversions += num(src.conversions);
      }}
      return {{ ...acc, cpc: acc.clicks ? acc.spend/acc.clicks : 0, cpa: acc.conversions ? acc.spend/acc.conversions : 0, ctr: acc.impressions ? acc.clicks/acc.impressions*100 : 0 }};
    }}
    function selectedSummary() {{ return selectedSummaryFrom(summaryPayload); }}
    // Tiny inline-SVG sparkline of the metric's current-period daily trend.
    function sparkSvg(vals, color) {{
      const clean=vals.filter(v=>v!=null&&isFinite(v));
      if (clean.length<2) return '<span class="spark-empty"></span>';
      const n=vals.length, w=66, h=22, mn=Math.min(...clean), mx=Math.max(...clean), span=(mx-mn)||1;
      const pts=vals.map((v,i)=>`${{(n===1?w/2:i/(n-1)*w).toFixed(1)}},${{(h-1-((num(v)-mn)/span)*(h-2)).toFixed(1)}}`).join(' ');
      return `<svg class="spark" viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${{pts}}" fill="none" stroke="${{color}}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
    }}
    // % change vs the comparison period, colored by whether the move is good for
    // that metric (dir from SUMMARY_CARDS). Arrow always shows the raw direction.
    // (Distinct from the snapshot-card deltaHtml above — this one is a compact
    // inline chip for the paid summary cards.)
    function summaryDeltaHtml(cur, prev, dir) {{
      if (prev==null || num(prev)===0 || cur==null) return '<span class="cmp-delta flat">—</span>';
      const ch=(num(cur)-num(prev))/num(prev)*100;
      const tip=`vs ${{cmpNoun()}} (${{compareStart}} – ${{compareEnd}})`;
      if (Math.abs(ch)<0.5) return `<span class="cmp-delta flat" title="${{tip}}">0%</span>`;
      const up=ch>0, arrow=up?'▲':'▼';
      let cls='flat';
      if (dir==='up') cls=up?'up':'down'; else if (dir==='down') cls=up?'down':'up';
      return `<span class="cmp-delta ${{cls}}" title="${{tip}}">${{arrow}} ${{Math.abs(ch).toFixed(0)}}%</span>`;
    }}
    // ---- Goals + peer benchmarks (admin preview) ----
    // Both hang off the summary cards and both are additive: if either payload
    // is missing, failed, or has nothing for a metric, the card renders exactly
    // as it always did. Neither is ever fetched for a non-admin.
    let goalsPayload = null;
    let benchPayload = null;

    // Format a number the way its card does, so a target reads like the value it
    // sits under (a $ target under a $ value) rather than as a bare number.
    const GOAL_FORMATTERS = {{ currency:money, currency2:v=>'$'+num(v).toFixed(2), percent:pct, number:count }};
    function goalFmt(fmt, v) {{ return (GOAL_FORMATTERS[fmt] || count)(v); }}

    // Percent-of-target → good|warn|bad, using the thresholds the server sent.
    // Mirrors metric_goals.grade() in Python; the server owns the numbers so the
    // two cannot drift apart without the payload changing.
    function goalStatus(pct, direction, thresholds) {{
      const t = (thresholds||{{}})[direction];
      if (!t || pct==null || !isFinite(pct)) return '';
      if (direction === 'up')   return pct >= t.good ? 'good' : (pct >= t.warn ? 'warn' : 'bad');
      if (direction === 'down') return pct <= t.good ? 'good' : (pct <= t.warn ? 'warn' : 'bad');
      if (pct >= t.good_low && pct <= t.good_high) return 'good';
      if (pct >= t.warn_low && pct <= t.warn_high) return 'warn';
      return 'bad';
    }}

    function goalRowHtml(key, value) {{
      if (!goalsPayload) return '';
      const g = (goalsPayload.goals||{{}})[key];
      if (!g || !g.target || value==null) return '';
      const pct = num(value) / num(g.target) * 100;
      if (!isFinite(pct)) return '';
      const status = goalStatus(pct, g.direction, goalsPayload.thresholds);
      // The bar caps at 100% so an overshoot doesn't render off the card; the
      // caption still states the true percentage.
      const width = Math.max(0, Math.min(100, pct));
      const verb = g.direction === 'down' ? 'of' : 'of';
      const tip = `Target ${{goalFmt(g.format, g.target)}} for this range · stored goal ${{goalFmt(g.format, g.goal)}}${{g.cumulative ? '/mo' : ''}}. ${{g.note}}`;
      return `<div class="card-goal ${{status}}" title="${{esc(tip)}}">`
        + `<div class="cg-bar"><span style="width:${{width.toFixed(1)}}%"></span></div>`
        + `<div class="cg-text">${{pct.toFixed(0)}}% ${{verb}} ${{goalFmt(g.format, g.target)}} target</div></div>`;
    }}

    function benchRowHtml(key, value) {{
      if (!benchPayload || !benchPayload.available) return '';
      const b = (benchPayload.metrics||{{}})[key];
      if (!b || !b.peer || !b.peer.n) return '';
      const median = num(b.peer.median);
      // "Ahead"/"behind" follows the metric's meaning, not its arithmetic: a CPA
      // below the peer median is ahead, a CTR below it is behind.
      let verdict = 'level', word = 'in line with';
      if (value != null && median) {{
        const higher = num(value) > median * 1.05;
        const lower  = num(value) < median * 0.95;
        if (b.direction === 'up' && higher)   {{ verdict='ahead';  word='ahead of'; }}
        else if (b.direction === 'up' && lower) {{ verdict='behind'; word='behind'; }}
        else if (b.direction === 'down' && lower) {{ verdict='ahead';  word='better than'; }}
        else if (b.direction === 'down' && higher) {{ verdict='behind'; word='worse than'; }}
        else if (b.direction === 'none' && (higher||lower)) {{ word = higher ? 'above' : 'below'; }}
      }}
      const thin = b.peer.thin ? ` <span class="cb-thin" title="Only ${{b.peer.n}} comparable client${{b.peer.n===1?'':'s'}} — directional, not a benchmark.">thin</span>` : '';
      const scope = b.scope === 'industry' ? b.peer_label : 'all clients';
      const tip = `${{b.label}} across ${{b.peer.n}} other ${{esc(String(scope))}} account${{b.peer.n===1?'':'s'}}: `
        + `median ${{goalFmt(b.format, b.peer.median)}}, mean ${{goalFmt(b.format, b.peer.mean)}}, `
        + `range ${{goalFmt(b.format, b.peer.min)}}–${{goalFmt(b.format, b.peer.max)}}. `
        + `The client's own value is excluded from its peer group.`;
      return `<div class="card-bench" title="${{esc(tip)}}">`
        + `<span class="cb-verdict ${{verdict}}">${{esc(word)}}</span>`
        + `<span>${{esc(String(scope))}} · <b>${{goalFmt(b.format, b.peer.median)}}</b> median (n=${{b.peer.n}})</span>${{thin}}</div>`;
    }}

    function renderSummary() {{
      const s = selectedSummary();
      const prev = selectedSummaryFrom(compareSummaryPayload);
      const daily = buildChartDaily();
      summaryCards.innerHTML = SUMMARY_CARDS.map(([key,label,format,dir]) => {{
        const delta = summaryDeltaHtml(s[key], (prev && prev[key]!=null) ? prev[key] : null, dir);
        const spark = sparkSvg(daily.map(d=>num(d[key])), SPARK_COLORS[key]||'#1769aa');
        const extra = goalRowHtml(key, s[key]) + benchRowHtml(key, s[key]);
        return `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{format(s[key])}}</div><div class="card-foot">${{delta}}${{spark}}</div>${{extra}}</div>`;
      }}).join('');
    }}

    // Both loaders are fire-and-forget: they re-render the cards when they land
    // and stay silent when they don't, so a slow benchmark pass (it walks every
    // client's cached marts) never holds up the numbers the page exists to show.
    async function loadGoals() {{
      if (!IS_ADMIN) return;
      try {{
        goalsPayload = await getJson(withDates(GOALS_API));
        if (summaryPayload) renderSummary();
      }} catch (err) {{ goalsPayload = null; }}
    }}
    // Not window-scoped — the benchmark always describes the agency's own two
    // warm windows — so one successful fetch serves the whole page session and
    // changing the date range does not re-request it.
    let benchLoaded = false;
    async function loadBenchmarks() {{
      if (!IS_ADMIN || !SHOW_BENCHMARKS || benchLoaded) return;
      try {{
        benchPayload = await getJson(BENCHMARKS_API);
        benchLoaded = true;
        if (summaryPayload) renderSummary();
      }} catch (err) {{ benchPayload = null; }}
    }}

    // ---- Summary sparkline data (feeds the Paid summary cards) ----
    function buildChartDaily() {{
      const daily = (summaryPayload && summaryPayload.daily) ? summaryPayload.daily : [];
      const needles = platformFilter.size ? [...platformFilter].map(p => p.toLowerCase()) : null;
      const byDate = new Map();
      for (const r of daily) {{
        if (needles && !needles.some(nd => String(r.source||'').toLowerCase().includes(nd))) continue;
        let d = byDate.get(r.date);
        if (!d) {{ d = {{ date:r.date, spend:0, impressions:0, clicks:0, conversions:0 }}; byDate.set(r.date, d); }}
        d.spend += num(r.spend); d.impressions += num(r.impressions); d.clicks += num(r.clicks); d.conversions += num(r.conversions);
      }}
      const out = [...byDate.values()].sort((a,b) => a.date < b.date ? -1 : 1);
      for (const d of out) {{ d.cpc = d.clicks ? d.spend/d.clicks : 0; d.cpa = d.conversions ? d.spend/d.conversions : 0; d.ctr = d.impressions ? d.clicks/d.impressions*100 : 0; }}
      return out;
    }}
    async function loadSummary() {{
      setStatus('summaryStatus','Loading…');
      summaryCards.innerHTML = skelCards(7);
      try {{
        const [curr, prev] = await Promise.all([
          getJson(withDates(SUMMARY_API)),
          getJson(withDatesRange(SUMMARY_API, compareStart, compareEnd)).catch(()=>null),
        ]);
        summaryPayload = curr;
        compareSummaryPayload = prev;
        renderSummary();
        // Date range is already shown by the Range dropdown, so keep this to
        // just the source note (blank unless the data is combined across sources).
        setStatus('summaryStatus', summaryPayload.by_source ? '' : 'combined');
      }} catch(err) {{
        summaryPayload=null;
        compareSummaryPayload=null;
        summaryCards.innerHTML = '';
        setStatus('summaryStatus', err.message||String(err), true);
      }}
    }}

    // ---- No-paid-ads Overview snapshot (GA4 traffic + GSC search) ----
    // cards: [label, currentRaw, prevRawOrNull, formatFn, ok]. ok===false means
    // the underlying request failed -- render "--" rather than a misleading 0,
    // which is indistinguishable from genuinely-zero activity.
    function renderSnapshotCards(containerId, cards) {{
      document.getElementById(containerId).innerHTML = cards.map(([label,curr,prev,fmt,ok]) => {{
        const valueHtml = ok===false
          ? `<span title="This metric failed to load -- try switching ranges or refreshing.">—</span>`
          : fmt(curr);
        const deltaOrNote = ok===false ? '' : deltaHtml(curr,prev);
        return `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{valueHtml}}</div>${{deltaOrNote}}</div>`;
      }}).join('');
    }}
    async function ga4TrafficTotals(s, e) {{
      // Sequential (not parallel) to keep concurrent BQ query load down --
      // and each sub-request tracks its own success so a failure renders as
      // "unavailable" rather than a misleading zero.
      let trafficOk = true, pagesOk = true;
      const traffic = await getJson(withDatesRange(TRAFFIC_ACQ_API, s, e)).catch(()=>{{ trafficOk=false; return {{by_channel:[]}}; }});
      const pages = await getJson(withDatesRange(PAGES_TOP_API, s, e)).catch(()=>{{ pagesOk=false; return {{rows:[]}}; }});
      const sessions = (traffic.by_channel||[]).reduce((sum,r)=>sum+num(r.sessions),0);
      const engaged = (traffic.by_channel||[]).reduce((sum,r)=>sum+num(r.engaged_sessions),0);
      const rows = pages.rows||[];
      const pageViews = rows.reduce((sum,r)=>sum+num(r.page_views),0);
      const keyEvents = rows.reduce((sum,r)=>sum+num(r.key_events),0);
      return {{sessions, engaged, pageViews, keyEvents, trafficOk, pagesOk}};
    }}
    async function loadOverviewSnapshot() {{
      setStatus('ga4SnapshotStatus','Loading…');
      document.getElementById('ga4SnapshotCards').innerHTML = skelCards(4);
      try {{
        const [curr, prev] = await Promise.all([
          ga4TrafficTotals(currentStart, currentEnd),
          ga4TrafficTotals(compareStart, compareEnd).catch(()=>null),
        ]);
        renderSnapshotCards('ga4SnapshotCards', [
          ['Sessions', curr.sessions, prev&&prev.sessions, count, curr.trafficOk],
          ['Engaged sessions', curr.engaged, prev&&prev.engaged, count, curr.trafficOk],
          ['Page views', curr.pageViews, prev&&prev.pageViews, count, curr.pagesOk],
          ['Key events', curr.keyEvents, prev&&prev.keyEvents, count, curr.pagesOk],
        ]);
        setCmpWarn('ga4SnapshotCmpWarn', ['google_analytics']);
        if (!curr.trafficOk || !curr.pagesOk) {{
          setStatus('ga4SnapshotStatus', 'Some metrics failed to load — try switching ranges or refreshing.', true);
        }} else {{
          setStatus('ga4SnapshotStatus', curr.sessions || curr.pageViews ? '' : 'No data for this range yet.');
        }}
      }} catch(err) {{
        document.getElementById('ga4SnapshotCards').innerHTML = '';
        setStatus('ga4SnapshotStatus', err.message||String(err), true);
      }}
      setStatus('gscSnapshotStatus','Loading…');
      document.getElementById('gscSnapshotCards').innerHTML = skelCards(4);
      try {{
        const [p, prevP] = await Promise.all([
          getJson(withDatesRange(GSC_API, currentStart, currentEnd)),
          getJson(withDatesRange(GSC_API, compareStart, compareEnd)).catch(()=>null),
        ]);
        const k = (p&&p.kpis)||{{}};
        const pk = (prevP&&prevP.kpis)||null;
        renderSnapshotCards('gscSnapshotCards', [
          ['Clicks', k.clicks, pk&&pk.clicks, count],
          ['Impressions', k.impressions, pk&&pk.impressions, count],
          ['CTR', k.ctr, pk&&pk.ctr, v=>v==null?'—':num(v).toFixed(2)+'%'],
          ['Avg position', k.avg_position, pk&&pk.avg_position, v=>v==null?'—':num(v).toFixed(1)],
        ]);
        setCmpWarn('gscSnapshotCmpWarn', ['gsc']);
        const empty = !p || (!p.kpis && !(p.top_queries||[]).length && !(p.top_pages||[]).length);
        setStatus('gscSnapshotStatus', empty ? 'No data for this range yet.' : '');
      }} catch(err) {{
        document.getElementById('gscSnapshotCards').innerHTML = '';
        setStatus('gscSnapshotStatus', err.message||String(err), true);
      }}
    }}
    // ---- Search Console ----
    const gscPos = v => v==null ? '—' : num(v).toFixed(1);
    const gscPct = v => v==null ? '—' : (num(v)).toFixed(2) + '%';
    // How a query's CTR compares with what its average rank normally earns.
    // The bands are the standard organic click-curve ranges (position 1 pulls
    // 20-40%, position 11+ almost nothing), so the dot answers "is this CTR
    // good?" in the only way that means anything -- good *for that rank*.
    const GSC_CTR_BANDS = [
      {{max:1.5,  lo:20, hi:40, label:'20–40%'}},
      {{max:2.5,  lo:10, hi:20, label:'10–20%'}},
      {{max:3.5,  lo:6,  hi:12, label:'6–12%'}},
      {{max:5.5,  lo:3,  hi:8,  label:'3–8%'}},
      {{max:10.5, lo:1,  hi:5,  label:'1–5%'}},
      {{max:Infinity, lo:0, hi:2, label:'under 2%'}},
    ];
    const gscCtrBand = pos => GSC_CTR_BANDS.find(b => pos < b.max) || GSC_CTR_BANDS[GSC_CTR_BANDS.length-1];
    function gscCtrCell(v, row) {{
      const txt = gscPct(v);
      const pos = row ? row.avg_position : null;
      if (v==null || pos==null) return txt;
      const b = gscCtrBand(num(pos)), c = num(v);
      const state = c > b.hi ? 'above' : (c < b.lo ? 'below' : 'typical');
      const word = state==='above' ? 'Ahead of' : state==='below' ? 'Behind' : 'In line with';
      const title = `${{word}} the ${{b.label}} a query at position ${{num(pos).toFixed(1)}} typically earns`;
      return `<span class="gsc-ctr gsc-ctr-${{state}}" title="${{esc(title)}}"><span class="gsc-ctr-dot"></span>${{txt}}</span>`;
    }}
    // Position movement vs. the comparison period (whichever the Compare picker
    // selected -- the backend is told which): value is prior - current, so a
    // positive number means the keyword improved (moved toward rank 1). null =
    // the query didn't rank in that period ("New").
    const gscDelta = v => {{
      if (v==null) return '<span class="gsc-mv gsc-mv-new">New</span>';
      const n=num(v);
      if (Math.abs(n)<0.05) return '<span class="gsc-mv gsc-mv-flat">\\u2013</span>';
      if (n>0) return '<span class="gsc-mv gsc-mv-up">\\u25B4 '+n.toFixed(1)+'</span>';
      return '<span class="gsc-mv gsc-mv-down">\\u25BE '+Math.abs(n).toFixed(1)+'</span>';
    }};
    function renderGscKpis(k, daily) {{
      k = k || {{}}; daily = daily || [];
      // [label, formatted value, kpi key, prior value, good-direction, spark color].
      // Avg position is "lower is better" so its delta direction is 'down'.
      const cards = [
        ['Clicks', count(k.clicks), 'clicks', k.prior_clicks, 'up', '#1769aa'],
        ['Impressions', count(k.impressions), 'impressions', k.prior_impressions, 'up', '#7c3aed'],
        ['CTR', gscPct(k.ctr), 'ctr', k.prior_ctr, 'up', '#0a7f3f'],
        ['Avg position', gscPos(k.avg_position), 'avg_position', k.prior_avg_position, 'down', '#d97706'],
      ];
      document.getElementById('gscKpis').innerHTML = cards.map(([label,val,key,prior,dir,color]) => {{
        const delta = summaryDeltaHtml(k[key], (prior!=null?prior:null), dir);
        const spark = sparkSvg(daily.map(d=>num(d[key])), color);
        return `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{val}}</div><div class="card-foot">${{delta}}${{spark}}</div></div>`;
      }}).join('');
    }}
    // ---- GSC queries/pages: sortable + paginated (top 10/page) ----
    const GSC_PER_PAGE = 10;
    const GSC_SORT_COLS = [
      {{key:'clicks', label:'Clicks', format:count, defDir:'desc'}},
      {{key:'impressions', label:'Impr.', format:count, defDir:'desc'}},
      {{key:'ctr', label:'CTR', format:gscCtrCell, defDir:'desc'}},
      {{key:'avg_position', label:'Position', format:gscPos, defDir:'asc'}},
    ];
    // Δ Position vs. the comparison period. Every query-keyed table (top queries,
    // branded, target) carries prior_avg_position/delta_position from the
    // backend; only the pages table doesn't. Default asc so the first click
    // surfaces the biggest sinkers.
    const GSC_DELTA_COL = {{key:'delta_position', label:'\\u0394 Pos', format:gscDelta, defDir:'asc'}};
    const gscColsFor = which => which==='pages' ? GSC_SORT_COLS : GSC_SORT_COLS.concat([GSC_DELTA_COL]);
    // page_url rows come back as full URLs (https://host/path) -- show just the
    // path in the table (full URL stays in the title tooltip on hover).
    function pathOnly(url) {{
      try {{ const u = new URL(url); return (u.pathname || '/') + (u.search || ''); }}
      catch {{ return url; }}
    }}
    const GSC_LABEL_COL_WIDTH = 185;
    const gscTables = {{
      queries: {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscQueriesTable', pagerId:'gscQueriesPager', labelWidth:GSC_LABEL_COL_WIDTH}},
      pages:   {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'page_url', labelText:'Page', tableId:'gscPagesTable', pagerId:'gscPagesPager', labelWidth:GSC_LABEL_COL_WIDTH, labelFormat:pathOnly}},
      branded: {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscBrandedTable', pagerId:'gscBrandedPager', labelWidth:GSC_LABEL_COL_WIDTH}},
      target:  {{rows:[], sortKey:'clicks', sortDir:'desc', page:1, labelKey:'query', labelText:'Query', tableId:'gscTargetTable', pagerId:'gscTargetPager', labelWidth:GSC_LABEL_COL_WIDTH}},
    }};
    // Reapply a user-chosen label-column width after each (re)render, since the
    // table is rebuilt on sort/paginate. Overrides the default max-width:0 clamp.
    function applyGscColWidth(el, st) {{
      if (!el || !st.labelWidth) return;
      const w=st.labelWidth+'px';
      const th=el.querySelector('th.left'); if (th) th.style.width=w;
      el.querySelectorAll('td.left').forEach(td=>{{ td.style.maxWidth=w; td.style.width=w; }});
    }}
    function renderGscTable(which) {{
      const st = gscTables[which];
      const cols = gscColsFor(which);
      const el = document.getElementById(st.tableId);
      const pager = document.getElementById(st.pagerId);
      if (!st.rows.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No data for this range.</td></tr></tbody>`; pager.innerHTML=''; return; }}
      const sorted=[...st.rows].sort((a,b)=>{{const va=num(a[st.sortKey]),vb=num(b[st.sortKey]);return st.sortDir==='asc'?va-vb:vb-va;}});
      const totalPages=Math.max(1,Math.ceil(sorted.length/GSC_PER_PAGE));
      if (st.page>totalPages) st.page=totalPages;
      const start=(st.page-1)*GSC_PER_PAGE, pageRows=sorted.slice(start,start+GSC_PER_PAGE);
      const arrow=k=>st.sortKey===k?(st.sortDir==='asc'?' \\u25B4':' \\u25BE'):'';
      const head=`<thead><tr><th class="left col-resizable">${{esc(st.labelText)}}<span class="col-resizer" data-which="${{which}}"></span></th>`+cols.map(c=>`<th class="gsc-sort${{st.sortKey===c.key?' active':''}}" data-which="${{which}}" data-key="${{c.key}}">${{c.label}}${{arrow(c.key)}}</th>`).join('')+`</tr></thead>`;
      const body=`<tbody>`+pageRows.map(r=>{{const raw=r[st.labelKey];const label=st.labelFormat?st.labelFormat(raw):raw;return`<tr><td class="left"><span class="page-path" title="${{esc(raw)}}">${{esc(label)}}</span></td>`+cols.map(c=>`<td>${{c.format(r[c.key], r)}}</td>`).join('')+`</tr>`;}}).join('')+`</tbody>`;
      el.innerHTML=head+body;
      applyGscColWidth(el, st);
      if (totalPages<=1) {{ pager.innerHTML=''; }}
      else {{ pager.innerHTML=`<button type="button" class="pager-btn" data-which="${{which}}" data-dir="prev"${{st.page<=1?' disabled':''}}>\\u2039 Prev</button><span class="pager-info">Page ${{st.page}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-which="${{which}}" data-dir="next"${{st.page>=totalPages?' disabled':''}}>Next \\u203A</button>`; }}
    }}
    document.getElementById('pane-gsc').addEventListener('click', ev => {{
      const th=ev.target.closest('th.gsc-sort');
      if (th) {{ const st=gscTables[th.dataset.which], key=th.dataset.key;
        if (st.sortKey===key) st.sortDir=st.sortDir==='asc'?'desc':'asc';
        else {{ st.sortKey=key; st.sortDir=(gscColsFor(th.dataset.which).find(c=>c.key===key)||{{}}).defDir||'desc'; }}
        st.page=1; renderGscTable(th.dataset.which); return;
      }}
      const pb=ev.target.closest('.pager-btn[data-which]');
      if (pb && !pb.disabled) {{ const st=gscTables[pb.dataset.which]; st.page+=(pb.dataset.dir==='next'?1:-1); renderGscTable(pb.dataset.which); }}
    }});
    // Drag the label column edge to widen/narrow it (persists across sort/paginate).
    (function initGscColResize(){{
      const pane=document.getElementById('pane-gsc'); if (!pane) return;
      let active=null;
      pane.addEventListener('mousedown', ev => {{
        const h=ev.target.closest('.col-resizer'); if (!h) return;
        ev.preventDefault(); ev.stopPropagation();
        const st=gscTables[h.dataset.which], table=h.closest('table');
        const th=table.querySelector('th.left');
        active={{st, table, startX:ev.clientX, startW:th.getBoundingClientRect().width}};
        document.body.classList.add('col-resizing');
      }});
      document.addEventListener('mousemove', ev => {{
        if (!active) return;
        active.st.labelWidth=Math.max(90, Math.round(active.startW+(ev.clientX-active.startX)));
        applyGscColWidth(active.table, active.st);
      }});
      document.addEventListener('mouseup', () => {{
        if (!active) return; active=null; document.body.classList.remove('col-resizing');
      }});
    }})();
    async function loadGsc() {{
      setStatus('gscStatus','Loading…');
      document.getElementById('gscKpis').innerHTML = skelCards(4);
      document.getElementById('gscQueriesTable').innerHTML = skelTable(5,6);
      document.getElementById('gscPagesTable').innerHTML = skelTable(5,6);
      try {{
        const p = await getJson(withCompare(withDates(GSC_API)));
        renderGscKpis((p&&p.kpis)||{{}}, (p&&p.daily)||[]);
        for (const which of ['queries','pages']) {{
          const st=gscTables[which]; st.rows = (p && (which==='queries'?p.top_queries:p.top_pages)) || [];
          st.page=1; st.sortKey='clicks'; st.sortDir='desc'; renderGscTable(which);
        }}
        renderGscKeywordTables();
        loadGscWatchlist();
        const k=(p&&p.kpis)||{{}};
        const empty = !p || (!p.kpis && !(p.top_queries||[]).length && !(p.top_pages||[]).length);
        setStatus('gscStatus', empty ? 'No data for this range yet.' : `${{count(k.clicks)}} clicks · ${{count(k.impressions)}} impressions`);
      }} catch(err) {{
        setStatus('gscStatus', err.message||String(err), true);
      }}
    }}

    // ---- Branded / target keyword filtering (GSC queries only) ----
    // Each group has include roots plus optional exclude roots: a query counts
    // when it contains any include root AND none of the exclude roots (so you
    // can include "benjamin" but exclude "dr").
    let gscBrandedRoots = GSC_BRANDED_ROOTS.slice();
    let gscTargetKeywords = GSC_TARGET_KEYWORDS.slice();
    let gscBrandedExclude = GSC_BRANDED_EXCLUDE.slice();
    let gscTargetExclude = GSC_TARGET_EXCLUDE.slice();
    // Branded/target queries are matched against the FULL date-range dataset
    // via a dedicated backend scan (gsc/keyword-matches), not filtered from
    // the top_queries subset (top_queries is LIMIT 25 by clicks in
    // gsc/summary, so a real match outside the top 25 would otherwise be
    // silently missed).
    let _gscKwReqId = 0;
    async function fetchKeywordMatches(terms, excludeTerms) {{
      if (!terms.length) return {{rows:[], weekly:[]}};
      let url = withCompare(withDates(GSC_KEYWORD_MATCHES_API)) + '&terms=' + encodeURIComponent(terms.join(','));
      if (excludeTerms && excludeTerms.length) url += '&exclude=' + encodeURIComponent(excludeTerms.join(','));
      try {{
        const r = await getJson(url);
        return {{rows: r.rows || [], weekly: r.weekly || []}};
      }} catch (err) {{
        return {{rows:[], weekly:[]}};
      }}
    }}
    // Weekly avg-position trend: a single impression-weighted average-position
    // line per week over the matched keyword basket. For position lower is
    // better, so the y-axis is reversed (best rank at the top) like a rank
    // chart. The include/exclude roots keep the basket on-target, so the mean
    // tracks real rank movement rather than basket churn.
    function drawKeywordTrend(canvasId, rows, valueKey, color, invert) {{
      clearSkelChart(canvasId);
      if (!document.getElementById(canvasId)) return;
      const n=rows.length;
      if (!n) {{ __destroyChart(canvasId); return; }}
      const labels=rows.map(r=>String(r.week_start||'').slice(5));
      const data=rows.map(r=>num(r[valueKey]));
      const fmtPos=v=>(Math.round(v*10)/10).toFixed(1);
      lineChart(canvasId, labels, [{{ label:'Avg position', data, color, fill:true }}], {{
        points:true, yReverse: !!invert, yDisplay:true, beginAtZero:false,
        yFmt: v => fmtPos(v),
        tooltip: {{ label: c => `Avg position: ${{fmtPos(c.raw)}}` }},
      }});
    }}
    async function renderGscKeywordTables() {{
      const reqId = ++_gscKwReqId;
      // Skeleton only the tables that will actually resolve to data, so we
      // never flash a skeleton in front of a "nothing configured" empty state.
      if (gscBrandedRoots.length) document.getElementById('gscBrandedTable').innerHTML = skelTable(4,6);
      if (gscTargetKeywords.length) document.getElementById('gscTargetTable').innerHTML = skelTable(4,6);
      const [branded, target] = await Promise.all([
        fetchKeywordMatches(gscBrandedRoots, gscBrandedExclude),
        fetchKeywordMatches(gscTargetKeywords, gscTargetExclude),
      ]);
      if (reqId !== _gscKwReqId) return; // a newer call (date range/terms changed) superseded this one
      gscTables.branded.rows = branded.rows;
      gscTables.target.rows  = target.rows;
      gscTables.branded.page = 1; gscTables.target.page = 1;
      renderGscTable('branded'); renderGscTable('target');
      drawKeywordTrend('gscBrandedTrendChart', branded.weekly, 'avg_position', '#1d6fd0', true);
      drawKeywordTrend('gscTargetTrendChart', target.weekly, 'avg_position', '#7c3aed', true);
      const setCount=(id,n,configured)=>{{const el=document.getElementById(id); if(el) el.textContent = configured ? `(${{n}})` : '';}};
      setCount('gscBrandedCount', gscTables.branded.rows.length, gscBrandedRoots.length);
      setCount('gscTargetCount', gscTables.target.rows.length, gscTargetKeywords.length);
      const none = !gscBrandedRoots.length && !gscTargetKeywords.length;
      setStatus('gscKwStatus', none ? 'Set branded roots and target keywords to see matching queries.' : '');
      // Empty-state hint when configured but nothing matched anywhere in range.
      if (gscBrandedRoots.length && !gscTables.branded.rows.length) document.getElementById('gscBrandedTable').innerHTML=`<tbody><tr><td class="empty">No queries match these branded roots.</td></tr></tbody>`;
      if (gscTargetKeywords.length && !gscTables.target.rows.length) document.getElementById('gscTargetTable').innerHTML=`<tbody><tr><td class="empty">No queries match these target keywords.</td></tr></tbody>`;
      if (!gscBrandedRoots.length) document.getElementById('gscBrandedTable').innerHTML=`<tbody><tr><td class="empty">No branded roots set.</td></tr></tbody>`;
      if (!gscTargetKeywords.length) document.getElementById('gscTargetTable').innerHTML=`<tbody><tr><td class="empty">No target keywords set.</td></tr></tbody>`;
    }}
    // ---- Keyword watchlist -------------------------------------------------
    // The branded/target panels above pour every query containing a root into
    // one bucket. Here each ROW is one watched keyword an admin entered -- "this
    // page was written for this keyword" -- with its own rank spark line, so a
    // commitment made when the blog was briefed has somewhere to be checked.
    let gscWatchItems = (GSC_WATCH_ITEMS || []).map(it => ({{kw:String(it.kw||''), page:String(it.page||'')}}));
    // Default sort is 'manual' -- the order the admin arranged, which is the
    // order the list is stored in. A curated list should open the way it was
    // curated; the metric columns are still one click away.
    const gscWatch = {{rows:{{}}, weekly:{{}}, sortKey:'manual', sortDir:'asc', dragFrom:null}};
    const watchKey = kw => String(kw||'').trim().toLowerCase();
    const watchTerms = () => gscWatchItems.map(i=>i.kw.trim()).filter(Boolean);
    // Position: lower is better, so the spark plots the NEGATED series -- the
    // line then rises exactly when the rank improves, which is how a trend line
    // reads. Weeks with no impressions are absent from the series rather than
    // zero (a zero would draw as rank 1), so the line spans the weeks that
    // actually ranked. Green when the last week beats the first.
    function watchSpark(series) {{
      const vals=(series||[]).map(r=>num(r.avg_position)).filter(v=>isFinite(v)&&v>0);
      if (vals.length<2) return '<span class="spark-empty"></span>';
      const improving=vals[vals.length-1]<=vals[0];
      return sparkSvg(vals.map(v=>-v), improving ? '#0a7f3f' : '#c02626');
    }}
    const WATCH_COLS = [
      {{key:'impressions', label:'Impr.', format:count, defDir:'desc'}},
      {{key:'clicks', label:'Clicks', format:count, defDir:'desc'}},
      {{key:'ctr', label:'CTR', format:gscCtrCell, defDir:'desc'}},
    ];
    // One row per configured keyword, whether or not it ranked -- a keyword with
    // no impressions yet is the most interesting row on a watchlist, so it stays
    // visible with empty metrics instead of dropping out.
    function watchRows() {{
      // Every configured item becomes a row, including one just added and not
      // yet named (`draft`) -- adding a row and having nothing appear is the
      // one thing an editable list must not do.
      return gscWatchItems.map((i,idx)=>{{
        const k=watchKey(i.kw);
        const d=gscWatch.rows[k]||{{}};
        return Object.assign({{}}, d, {{
          kw:i.kw, page:i.page, idx, draft:!i.kw.trim(),
          series:gscWatch.weekly[k]||[],
        }});
      }});
    }}
    function renderGscWatchTable() {{
      const el=document.getElementById('gscWatchTable'); if(!el) return;
      const rows=watchRows();
      if (!rows.length) {{
        const hint=IS_ADMIN ? ' Use “+ Add keyword” or “Bulk add” to start the list.' : '';
        el.innerHTML=`<tbody><tr><td class="empty">No keywords on the watchlist yet.${{hint}}</td></tr></tbody>`;
        return;
      }}
      // Missing metrics always sort last, in both directions: an unranked
      // keyword reads as 0 impressions but as no position at all, and sorting it
      // to the top of "best position" would be a lie.
      const manual = gscWatch.sortKey==='manual';
      const sorted=[...rows].sort((a,b)=>{{
        // List order is the stored order, and a dragged row means nothing if the
        // table then re-sorts it away.
        if (manual) return a.idx-b.idx;
        // A just-added row has no keyword yet and no metrics to sort on, so it
        // pins to the top rather than falling in with the unranked rows.
        if (a.draft!==b.draft) return a.draft ? -1 : 1;
        const va=a[gscWatch.sortKey], vb=b[gscWatch.sortKey];
        const ma=(va==null||!isFinite(num(va))), mb=(vb==null||!isFinite(num(vb)));
        if (ma&&mb) return 0;
        if (ma) return 1;
        if (mb) return -1;
        return gscWatch.sortDir==='asc' ? num(va)-num(vb) : num(vb)-num(va);
      }});
      const arrow=k=>gscWatch.sortKey===k?(gscWatch.sortDir==='asc'?' ▴':' ▾'):'';
      const th=(k,label,cls,tip)=>`<th class="watch-sort${{cls?' '+cls:''}}${{gscWatch.sortKey===k?' active':''}}" data-watch-key="${{k}}"${{tip?` title="${{esc(tip)}}"`:''}}>${{label}}${{arrow(k)}}</th>`;
      // The handle column's header is the way back to list order, so a metric
      // sort is never a dead end for someone who wants to rearrange.
      const gripHead = IS_ADMIN
        ? `<th class="watch-sort watch-grip-cell${{manual?' active':''}}" data-watch-key="manual" title="List order — drag rows to reorder">⇅</th>`
        : '';
      const head=`<thead><tr>${{gripHead}}<th class="left">Keyword</th>`
        + th('avg_position','Position · 13 wks', '', 'Impression-weighted average rank over the selected range. The spark line covers the last 13 weeks and rises as the rank improves.')
        + th('delta_position','Δ Pos', '', 'Change in average rank against the comparison period. Positive means the keyword moved toward rank 1.')
        + WATCH_COLS.map(c=>th(c.key,c.label)).join('')
        + (IS_ADMIN ? `<th></th>` : '') + `</tr></thead>`;
      const body=`<tbody>`+sorted.map(r=>{{
        const kw=r.kw.trim(), variants=kw.endsWith('*');
        const kwTxt=kw.replace(/[*]$/,'').trim();
        const vTip=variants
          ? `Counts this keyword and its variants${{r.query_count?' (' + count(r.query_count) + ' queries in this range)':''}}`
          : '';
        const vTag=variants ? `<span class="watch-variants" title="${{esc(vTip)}}">+ variants</span>` : '';
        const posTxt=(r.avg_position==null)
          ? '<span class="watch-pos watch-pos-none">—</span>'
          : `<span class="watch-pos">${{num(r.avg_position).toFixed(1)}}</span>`;
        // Admin cells are the same read-only cells plus a click target, so the
        // list looks identical to what a client sees until you click into it.
        const editable=(field, inner) => IS_ADMIN
          ? `<span class="watch-editable" data-watch-edit="${{field}}" data-watch-i="${{r.idx}}" title="Click to edit">${{inner}}</span>`
          : inner;
        const kwCell = (IS_ADMIN && r.draft)
          ? watchCellInput('kw', r.idx, '')
          : editable('kw', `<span class="watch-kw" title="${{esc(kw)}}">${{esc(kwTxt)}}${{vTag}}</span>`);
        // The grip is focusable so the order can be changed from the keyboard
        // (Arrow keys) as well as dragged.
        const grip = !IS_ADMIN ? '' : (manual
          ? `<td class="watch-grip-cell"><span class="watch-grip" draggable="true" data-watch-grip="${{r.idx}}" tabindex="0" role="button" aria-label="Reorder ${{esc(kwTxt||'this row')}} — drag, or use the arrow keys">⠿</span></td>`
          : `<td class="watch-grip-cell"></td>`);
        return `<tr${{r.draft ? ' class="watch-draft"' : ''}} data-watch-row="${{r.idx}}">`
          + grip
          + `<td class="left">${{kwCell}}</td>`
          + `<td><span class="watch-spark">${{watchSpark(r.series)}}${{posTxt}}</span></td>`
          // "New" (what a null delta means elsewhere) would be wrong for a
          // keyword that has no rank at all yet -- that row gets a plain dash.
          + `<td>${{r.avg_position==null ? '—' : gscDelta(r.delta_position, r)}}</td>`
          + WATCH_COLS.map(c=>`<td>${{r[c.key]==null ? '—' : c.format(r[c.key], r)}}</td>`).join('')
          + (IS_ADMIN ? `<td class="watch-rm-cell"><button type="button" class="watch-rm" data-watch-rm="${{r.idx}}" aria-label="Remove ${{esc(kwTxt||'this row')}}">×</button></td>` : '')
          + `</tr>`;
      }}).join('')+`</tbody>`;
      el.innerHTML=head+body;
    }}
    let _gscWatchReqId=0;
    async function loadGscWatchlist() {{
      const terms=watchTerms();
      gscWatch.rows={{}}; gscWatch.weekly={{}};
      // Nothing on the list and nobody who could add one: the whole section is
      // noise on a client's tab, so it stays out of the page rather than
      // explaining an empty table to them.
      const sec=document.getElementById('sec-gsc-watchlist');
      // Hide the editable wrapper, not just the section, so an empty watchlist
      // doesn't leave a stray edit bar behind on an admin's tab.
      const secUnit=sec&&(sec.closest('.ov-unit')||sec);
      if (secUnit) secUnit.hidden = !terms.length && !IS_ADMIN;
      if (!terms.length) {{
        renderGscWatchTable();
        setStatus('gscWatchStatus', IS_ADMIN ? 'Add the keywords this site is being written for.' : '');
        return;
      }}
      const reqId=++_gscWatchReqId;
      const el=document.getElementById('gscWatchTable');
      if (el) el.innerHTML=skelTable(IS_ADMIN ? 8 : 6, Math.min(6, terms.length));
      setStatus('gscWatchStatus','Loading…');
      const url=withCompare(withDates(GSC_WATCHLIST_API))+'&terms='+encodeURIComponent(terms.join(','));
      try {{
        const p=await getJson(url);
        if (reqId!==_gscWatchReqId) return; // superseded by a newer range/keyword change
        (p.rows||[]).forEach(r=>{{ gscWatch.rows[watchKey(r.term)]=r; }});
        (p.weekly||[]).forEach(r=>{{ const k=watchKey(r.term); (gscWatch.weekly[k]=gscWatch.weekly[k]||[]).push(r); }});
        renderGscWatchTable();
        const ranked=Object.keys(gscWatch.rows).length;
        setStatus('gscWatchStatus', `${{ranked}} of ${{terms.length}} watched keyword${{terms.length===1?'':'s'}} earned impressions in this range.`);
      }} catch(err) {{
        if (reqId!==_gscWatchReqId) return;
        renderGscWatchTable();
        setStatus('gscWatchStatus', err.message||String(err), true);
      }}
    }}
    (function initWatchSort(){{
      const el=document.getElementById('gscWatchTable'); if(!el) return;
      el.addEventListener('click', ev => {{
        const th=ev.target.closest('th.watch-sort'); if(!th) return;
        const key=th.dataset.watchKey;
        // List order has one direction -- the stored one -- so clicking it is a
        // switch, not a toggle.
        if (key==='manual') {{ gscWatch.sortKey='manual'; gscWatch.sortDir='asc'; renderGscWatchTable(); return; }}
        if (gscWatch.sortKey===key) gscWatch.sortDir = gscWatch.sortDir==='asc' ? 'desc' : 'asc';
        else {{
          gscWatch.sortKey=key;
          const col=WATCH_COLS.find(c=>c.key===key);
          // Position and Δ Pos default ascending, so the first click surfaces
          // the best rank / the biggest sinker rather than the largest number.
          gscWatch.sortDir = col ? col.defDir : 'asc';
        }}
        renderGscWatchTable();
      }});
    }})();
    // Editing is the list. An admin clicks a keyword or page cell, it becomes an
    // input in place, and blur/Enter commits -- there is no second copy of the
    // watchlist to keep in sync with what the table shows.
    let _watchSaveTimer=null;
    function saveWatchConfig() {{
      clearTimeout(_watchSaveTimer);
      _watchSaveTimer=setTimeout(async () => {{
        const text=gscWatchItems.filter(i=>i.kw.trim())
          .map(i=>i.page.trim() ? `${{i.kw.trim()}}|${{i.page.trim()}}` : i.kw.trim()).join('\\n');
        try {{
          const r=await fetch(GSC_WATCHLIST_CONFIG_API, {{method:'POST', headers:{{'Content-Type':'application/json'}}, credentials:'same-origin', body:JSON.stringify({{watch_keywords:text}})}});
          const body=await r.json().catch(()=>({{}}));
          if (!r.ok||!body.ok) throw new Error((body&&body.detail&&(body.detail.error||body.detail))||r.statusText);
        }} catch(err) {{ setStatus('gscWatchStatus','Save failed: '+(err.message||err), true); }}
      }}, 700);
    }}
    // The input a cell turns into. Rendered by the table for a draft row, and
    // swapped in on click for an existing one.
    function watchCellInput(field, idx, value) {{
      const ph = field==='kw' ? 'keyword' : 'page this keyword is for (optional)';
      return `<input class="watch-cell-in" type="text" data-watch-in="${{field}}" data-watch-i="${{idx}}" value="${{esc(value||'')}}" placeholder="${{ph}}" spellcheck="false">`;
    }}
    function focusWatchInput(idx, field) {{
      const el=document.getElementById('gscWatchTable'); if(!el) return;
      const inp=el.querySelector(`input[data-watch-in="${{field}}"][data-watch-i="${{idx}}"]`);
      if (inp) {{ inp.focus(); inp.select(); }}
    }}
    // Commit one cell. An existing row whose keyword is cleared is a removal --
    // a nameless row would just sit there unrankable.
    function commitWatchCell(idx, field, raw) {{
      const item=gscWatchItems[idx]; if(!item) return;
      const val=String(raw||'').trim();
      const changed = item[field]!==val;
      item[field]=val;
      if (field==='kw' && !val) gscWatchItems.splice(idx,1);
      if (changed) {{ saveWatchConfig(); loadGscWatchlist(); }}
      else renderGscWatchTable();
    }}
    // New rows go to the TOP of the list: it is the keyword you are working on
    // now, and on a long list an appended row would land below the fold. In list
    // order that is also where it stays until it is dragged.
    function addWatchRow() {{
      gscWatchItems.unshift({{kw:'', page:''}});
      renderGscWatchTable();
      focusWatchInput(0, 'kw');
    }}
    // Move one row and persist -- no refetch, because the set of watched terms
    // is unchanged and the data behind each row is already in hand.
    function moveWatchItem(from, to) {{
      if (from===to || from==null || to==null) return;
      if (from<0 || from>=gscWatchItems.length) return;
      to=Math.max(0, Math.min(gscWatchItems.length-1, to));
      const [item]=gscWatchItems.splice(from,1);
      gscWatchItems.splice(to,0,item);
      // Reordering only means something in list order, so a drag under a metric
      // sort switches the view to where the change is visible.
      gscWatch.sortKey='manual'; gscWatch.sortDir='asc';
      saveWatchConfig();
      renderGscWatchTable();
      const grip=document.querySelector(`[data-watch-grip="${{to}}"]`);
      if (grip) grip.focus();
    }}
    // Bulk add. Two shapes people actually paste, told apart by whether the
    // second field looks like a page: "keyword, /page" is one row, and a line of
    // plain comma-separated keywords is one row each. "keyword|page" (the stored
    // format) always means one row, so a saved list can be pasted straight back.
    function parseWatchBulk(text) {{
      const looksPage=v=>/^(https?:[/][/]|[/])/i.test(v);
      const out=[];
      String(text||'').split(/[\\r\\n]+/).forEach(line=>{{
        const t=line.trim(); if(!t) return;
        if (t.includes('|')) {{
          const bar=t.indexOf('|');
          const kw=t.slice(0,bar).trim();
          if (kw) out.push({{kw, page:t.slice(bar+1).trim()}});
          return;
        }}
        const parts=t.split(',').map(v=>v.trim()).filter(Boolean);
        if (parts.length===2 && looksPage(parts[1])) {{ out.push({{kw:parts[0], page:parts[1]}}); return; }}
        parts.forEach(v=>{{ if (!looksPage(v)) out.push({{kw:v, page:''}}); }});
      }});
      return out;
    }}
    // Adds what is new and says what it skipped -- pasting a list that overlaps
    // the current one is normal, and silently dropping half of it is not.
    function applyWatchBulk(text) {{
      const parsed=parseWatchBulk(text);
      if (!parsed.length) {{ setStatus('gscWatchStatus','Nothing to add — paste one keyword per line, or comma-separated.', true); return false; }}
      const seen=new Set(gscWatchItems.map(i=>watchKey(i.kw)).filter(Boolean));
      let added=0, skipped=0;
      parsed.forEach(it=>{{
        const k=watchKey(it.kw);
        if (!k || seen.has(k)) {{ skipped++; return; }}
        seen.add(k); gscWatchItems.push(it); added++;
      }});
      if (!added) {{ setStatus('gscWatchStatus','Already on the watchlist — nothing added.'); return false; }}
      saveWatchConfig(); loadGscWatchlist();
      const tail = skipped ? ` ${{skipped}} already on the list.` : '';
      setStatus('gscWatchStatus', `Added ${{added}} keyword${{added===1?'':'s'}}.${{tail}}`);
      return true;
    }}
    (function initWatchEditing(){{
      const table=document.getElementById('gscWatchTable'); if(!table) return;
      if (IS_ADMIN) {{
        // Swap a cell for an input on click. Ignore clicks on the page link so
        // an admin can still follow it.
        table.addEventListener('click', ev => {{
          if (ev.target.closest('a')) return;
          const rm=ev.target.closest('button[data-watch-rm]');
          if (rm) {{
            gscWatchItems.splice(+rm.dataset.watchRm,1);
            saveWatchConfig(); loadGscWatchlist(); return;
          }}
          const cell=ev.target.closest('[data-watch-edit]'); if(!cell) return;
          const field=cell.dataset.watchEdit, idx=+cell.dataset.watchI;
          const item=gscWatchItems[idx]; if(!item) return;
          const td=cell.closest('td'); if(!td) return;
          td.innerHTML=watchCellInput(field, idx, item[field]);
          focusWatchInput(idx, field);
        }});
        table.addEventListener('keydown', ev => {{
          const inp=ev.target.closest('input[data-watch-in]'); if(!inp) return;
          if (ev.key==='Enter') {{ ev.preventDefault(); inp.blur(); }}
          else if (ev.key==='Escape') {{ ev.preventDefault(); inp.dataset.cancelled='1'; renderGscWatchTable(); }}

        }});
        table.addEventListener('focusout', ev => {{
          const inp=ev.target.closest('input[data-watch-in]'); if(!inp) return;
          if (inp.dataset.cancelled) return;
          commitWatchCell(+inp.dataset.watchI, inp.dataset.watchIn, inp.value);
        }});
      }}
      if (IS_ADMIN) {{
        // Drag to reorder. The row under the pointer shows where the dragged row
        // will land -- above it or below it, by which half you are over.
        const clearMarks=()=>table.querySelectorAll('tr.watch-drop-above,tr.watch-drop-below')
          .forEach(tr=>tr.classList.remove('watch-drop-above','watch-drop-below'));
        table.addEventListener('dragstart', ev => {{
          const grip=ev.target.closest('[data-watch-grip]'); if(!grip) return;
          gscWatch.dragFrom=+grip.dataset.watchGrip;
          const tr=grip.closest('tr'); if (tr) tr.classList.add('watch-dragging');
          if (ev.dataTransfer) {{ ev.dataTransfer.effectAllowed='move'; ev.dataTransfer.setData('text/plain', String(gscWatch.dragFrom)); }}
        }});
        table.addEventListener('dragover', ev => {{
          if (gscWatch.dragFrom==null) return;
          const tr=ev.target.closest('tr[data-watch-row]'); if(!tr) return;
          ev.preventDefault();
          if (ev.dataTransfer) ev.dataTransfer.dropEffect='move';
          const box=tr.getBoundingClientRect();
          const above=(ev.clientY-box.top) < box.height/2;
          clearMarks();
          tr.classList.add(above ? 'watch-drop-above' : 'watch-drop-below');
        }});
        table.addEventListener('drop', ev => {{
          if (gscWatch.dragFrom==null) return;
          const tr=ev.target.closest('tr[data-watch-row]');
          ev.preventDefault();
          const from=gscWatch.dragFrom;
          gscWatch.dragFrom=null;
          clearMarks();
          if (!tr) {{ renderGscWatchTable(); return; }}
          const over=+tr.dataset.watchRow;
          const box=tr.getBoundingClientRect();
          const above=(ev.clientY-box.top) < box.height/2;
          // Dropping below a row that sits above the dragged one lands ON that
          // row's index; the splice removing the source first shifts the rest.
          let to=above ? over : over+1;
          if (from<to) to--;
          moveWatchItem(from, to);
        }});
        table.addEventListener('dragend', () => {{
          gscWatch.dragFrom=null;
          clearMarks();
          table.querySelectorAll('tr.watch-dragging').forEach(tr=>tr.classList.remove('watch-dragging'));
        }});
        // Same move from the keyboard, for anyone not dragging with a mouse.
        table.addEventListener('keydown', ev => {{
          const grip=ev.target.closest('[data-watch-grip]'); if(!grip) return;
          const from=+grip.dataset.watchGrip;
          if (ev.key==='ArrowUp') {{ ev.preventDefault(); moveWatchItem(from, from-1); }}
          else if (ev.key==='ArrowDown') {{ ev.preventDefault(); moveWatchItem(from, from+1); }}
          else if (ev.key==='Home') {{ ev.preventDefault(); moveWatchItem(from, 0); }}
          else if (ev.key==='End') {{ ev.preventDefault(); moveWatchItem(from, gscWatchItems.length-1); }}
        }});
      }}
      const addBtn=document.getElementById('gscWatchAdd');
      if (addBtn) addBtn.addEventListener('click', addWatchRow);
      const bulkBtn=document.getElementById('gscWatchBulkBtn');
      const bulk=document.getElementById('gscWatchBulk');
      const bulkText=document.getElementById('gscWatchBulkText');
      const closeBulk=(refocus)=>{{
        if (!bulk || bulk.hidden) return;
        bulk.hidden=true;
        if (bulkBtn) {{ bulkBtn.setAttribute('aria-expanded','false'); if (refocus) bulkBtn.focus(); }}
      }};
      if (bulkBtn && bulk) bulkBtn.addEventListener('click', () => {{
        const open=bulk.hidden;
        bulk.hidden=!open;
        bulkBtn.setAttribute('aria-expanded', open?'true':'false');
        if (open && bulkText) bulkText.focus();
      }});
      // Click anywhere else closes it, the way a popover should. mousedown, not
      // click, so it closes as soon as you press outside it.
      document.addEventListener('mousedown', ev => {{
        if (!bulk || bulk.hidden) return;
        if (ev.target.closest('#gscWatchBulk') || ev.target.closest('#gscWatchBulkBtn')) return;
        closeBulk();
      }});
      const bulkAdd=document.getElementById('gscWatchBulkAdd');
      if (bulkAdd && bulkText) bulkAdd.addEventListener('click', () => {{
        if (applyWatchBulk(bulkText.value)) {{ bulkText.value=''; closeBulk(true); }}
      }});
      const bulkCancel=document.getElementById('gscWatchBulkCancel');
      if (bulkCancel) bulkCancel.addEventListener('click', () => {{ if (bulkText) bulkText.value=''; closeBulk(true); }});
      // Ctrl/Cmd+Enter submits, so a long paste doesn't need a trip to the mouse.
      if (bulkText) bulkText.addEventListener('keydown', ev => {{
        if (ev.key==='Enter' && (ev.metaKey||ev.ctrlKey)) {{ ev.preventDefault(); if (applyWatchBulk(bulkText.value)) {{ bulkText.value=''; closeBulk(true); }} }}
        else if (ev.key==='Escape') {{ ev.preventDefault(); closeBulk(true); }}
      }});
    }})();
    // Inline tag editors for branded roots + target keywords. Add with Enter or
    // comma, remove with the chip ×; changes re-filter live and auto-save.
    let _kwSaveTimer=null;
    function saveKwConfig() {{
      clearTimeout(_kwSaveTimer);
      _kwSaveTimer=setTimeout(async () => {{
        setStatus('gscKwStatus','Saving…');
        try {{
          const r=await fetch(GSC_KEYWORD_CONFIG_API, {{method:'POST', headers:{{'Content-Type':'application/json'}}, credentials:'same-origin', body:JSON.stringify({{branded_roots:gscBrandedRoots.join('\\n'), target_keywords:gscTargetKeywords.join('\\n'), branded_exclude:gscBrandedExclude.join('\\n'), target_exclude:gscTargetExclude.join('\\n')}})}});
          const body=await r.json().catch(()=>({{}}));
          if (!r.ok||!body.ok) throw new Error((body&&body.detail&&(body.detail.error||body.detail))||r.statusText);
          setStatus('gscKwStatus','Saved.'); setTimeout(()=>{{const el=document.getElementById('gscKwStatus'); if(el&&el.textContent==='Saved.') el.textContent='';}}, 2000);
        }} catch(err) {{ setStatus('gscKwStatus','Save failed: '+(err.message||err), true); }}
      }}, 700);
    }}
    function makeTagEditor(containerId, label, getTerms, setTerms) {{
      const el=document.getElementById(containerId); if(!el) return;
      function commit(v) {{ v=v.trim(); if(!v) return false; const t=getTerms().slice(); if(t.some(x=>x.toLowerCase()===v.toLowerCase())) return false; t.push(v); setTerms(t); return true; }}
      function render() {{
        const terms=getTerms();
        el.innerHTML=`<span class="tag-editor-label">${{esc(label)}}</span>`
          + terms.map((t,i)=>`<span class="tag-chip">${{esc(t)}}<button type="button" data-i="${{i}}" aria-label="Remove ${{esc(t)}}">×</button></span>`).join('')
          + `<input class="tag-input" type="text" placeholder="${{terms.length?'Add…':'Add a '+label.toLowerCase().replace(/s$/,'')+'…'}}">`;
        el.querySelectorAll('.tag-chip button').forEach(btn=>btn.addEventListener('click',()=>{{
          const t=getTerms().slice(); t.splice(+btn.dataset.i,1); setTerms(t); render(); renderGscKeywordTables(); saveKwConfig();
        }}));
        const inp=el.querySelector('.tag-input');
        const add=(refocus)=>{{ if(commit(inp.value)){{ render(); renderGscKeywordTables(); saveKwConfig(); if(refocus){{const ni=el.querySelector('.tag-input'); if(ni) ni.focus();}} }} else {{ inp.value=''; }} }};
        inp.addEventListener('keydown',e=>{{ if(e.key==='Enter'||e.key===','){{ e.preventDefault(); add(true); }} else if(e.key==='Backspace'&&!inp.value){{ const t=getTerms().slice(); if(t.length){{ t.pop(); setTerms(t); render(); renderGscKeywordTables(); saveKwConfig(); const ni=el.querySelector('.tag-input'); if(ni) ni.focus(); }} }} }});
        inp.addEventListener('blur',()=>add(false));
      }}
      render();
    }}
    makeTagEditor('gscBrandedTags','Include roots', ()=>gscBrandedRoots, t=>{{gscBrandedRoots=t;}});
    makeTagEditor('gscBrandedExcludeTags','Exclude terms', ()=>gscBrandedExclude, t=>{{gscBrandedExclude=t;}});
    makeTagEditor('gscTargetTags','Include keywords', ()=>gscTargetKeywords, t=>{{gscTargetKeywords=t;}});
    makeTagEditor('gscTargetExcludeTags','Exclude terms', ()=>gscTargetExclude, t=>{{gscTargetExclude=t;}});
    // Toggle the include/exclude tag editors from the "Edit" button in each
    // keyword panel's headline row (keeps the editors tucked away by default).
    document.querySelectorAll('.kw-edit-btn[data-kw-edit]').forEach(btn=>{{
      btn.addEventListener('click',()=>{{
        const box=document.getElementById(btn.dataset.kwEdit); if(!box) return;
        const open=box.hidden; box.hidden=!open; btn.setAttribute('aria-expanded', open?'true':'false');
        if(open){{const inp=box.querySelector('.tag-input'); if(inp) inp.focus();}}
      }});
    }});

    // ---- SEMrush (domain-level snapshot — not date-range scoped) ----
    function renderSemrushKpis(ov, bl, series, aio) {{
      ov = ov || {{}}; bl = bl || {{}}; series = series || []; aio = aio || {{}};
      const first = series.length ? series[0] : null;
      const last  = series.length ? series[series.length-1] : null;
      // Delta is first-vs-last over the ~90-day daily series (SEMrush snapshots
      // are domain-level, not date-range scoped). Spark draws the same series.
      const sdelta = (key,dir) => (series.length>=2 && first && last) ? summaryDeltaHtml(last[key], first[key], dir) : '';
      const sspark = (key,color) => sparkSvg(series.map(d=>num(d[key])), color);
      const aioFoot = (aio.keywords_cited!=null)
        ? `<span class="cmp-delta flat" title="ranked keywords citing this domain in an AI Overview">${{count(aio.keywords_cited)}} cited</span>`
        : '';
      // [label, value, foot-delta html, spark html]. AI Overview keyword count
      // has no "good" direction (more AIOs can cannibalize clicks), so it shows
      // the cited count instead of a colored delta.
      const cards = [
        ['Organic Traffic (est.)', count(ov.organic_traffic), sdelta('organic_traffic','up'), sspark('organic_traffic','#1769aa')],
        ['Organic Keywords', count(ov.organic_keywords), sdelta('organic_keywords','up'), sspark('organic_keywords','#7c3aed')],
        ['Authority Score', bl.authority_score != null ? bl.authority_score + '/100' : '—', sdelta('authority_score','up'), sspark('authority_score','#0a7f3f')],
        ['Referring Domains', count(bl.referring_domains), sdelta('referring_domains','up'), sspark('referring_domains','#d97706')],
        ['Total Backlinks', count(bl.total_backlinks), sdelta('total_backlinks','up'), sspark('total_backlinks','#0891b2')],
        ['AI Overview Keywords', count(aio.keywords_with_aio), aioFoot, sspark('ai_overview_keywords','#a855f7')],
      ];
      document.getElementById('semrushKpis').innerHTML = cards.map(([label,val,delta,spark]) =>
        `<div class="card"><div class="card-title">${{label}}</div><div class="card-value">${{val}}</div><div class="card-foot">${{delta}}${{spark}}</div></div>`).join('');
    }}
    async function loadSemrush() {{
      const host=document.getElementById('semrushKpis');
      if (!host) return;   // section omitted when SEMrush isn't connected
      setStatus('semrushStatus','Loading…');
      host.innerHTML = skelCards(4);
      try {{
        const p = await getJson(SEMRUSH_API);
        if (!p || !p.domain) {{
          renderSemrushKpis({{}}, {{}}, [], {{}});
          setStatus('semrushStatus','No SEMrush data yet.');
          return;
        }}
        renderSemrushKpis(p.overview, p.backlinks, p.series, p.ai_overview);
        setStatus('semrushStatus', esc(p.domain));
      }} catch(err) {{
        setStatus('semrushStatus', err.message||String(err), true);
      }}
    }}
    {pagespeed_pane_js}
    // Earliest synced date per source (google/linkedin/meta/google_analytics/gsc),
    // populated by loadHealth() below. Used to warn when a comparison period
    // (see compareStart/compareEnd) falls before the data actually starts.
    let earliestDates = {{}};
    // The mart-health TABLE now lives on the Settings page; on the dashboard we
    // still fetch it (quietly) only to populate earliestDates, which drives the
    // "comparison period predates synced data" warnings on the summary cards.
    // Deduped: the page kicks this off at startup for the comparison notice
    // while the Overview loader asks for it too -- one fetch serves both.
    let _healthInflight = null;
    function loadHealth() {{
      if (_healthInflight) return _healthInflight;
      _healthInflight = (async () => {{
        try {{
          const payload = await getJson(withDates(HEALTH_API));
          const rows = payload.rows||[];
          earliestDates = {{}};
          for (const r of rows) {{
            const k = String(r.source||'').toLowerCase();
            if (k && r.earliest_date) earliestDates[k] = r.earliest_date;
          }}
          syncCompareNotice();
        }} catch(err) {{
          // Non-fatal: no earliest-date info just means no comparison warnings.
        }} finally {{
          _healthInflight = null;
        }}
      }})();
      return _healthInflight;
    }}

    // ---- Explorer ----
    const METRIC_COLS = [
      {{key:'spend',label:'Spend',format:money}},
      {{key:'impressions',label:'Impr.',format:count}},
      {{key:'clicks',label:'Clicks',format:count}},
      {{key:'ctr',label:'CTR',format:pct}},
      {{key:'conversions',label:'Conv.',format:count}},
      {{key:'verified_sel',label:'Verified conv.',format:count,cls:'ga4-col',keSelect:true,title:'GA4-verified conversions, matched to the Meta ad by id (utm_content) and rolled up. Independent of the platform-reported Conv. Use the selector to isolate a single GA4 key event.'}},
    ];
    // Explorer table sort — click a column header to sort every tree level (campaigns,
    // ad groups, ads) by it. 'name' sorts the label column alphabetically.
    let explorerSort = {{ key:'spend', dir:'desc' }};
    function explorerMetricVal(m, key) {{
      if (key==='ctr') return num(m.impressions) ? num(m.clicks)/num(m.impressions)*100 : 0;
      return num(m[key]);
    }}
    function explorerAdName(a) {{ return String(a.ad_name||a.ad_label||a.ad_id||''); }}
    // Client-configured filter chip groups: [{{id,label,chips:[{{label,phrases}}]}}].
    // phrases are pre-lowercased server-side; a chip matches a campaign whose
    // (lowercased) name contains any of its phrases.
    const EXPLORER_FILTER_GROUPS = {explorer_filter_groups_json};
    const EXPLORER_FILTERS_API = "{_aurl(f'/api/clients/{api_client_key}/explorer/filters')}";
    // Admin "Edit filters" popover in the Campaign explorer header. Saves the raw
    // chip-rule text, then reloads so the rebuilt chips reflect the new config.
    (function(){{
      const btn = document.getElementById('efEditBtn'); if (!btn) return;
      const pop = document.getElementById('efPop');
      const save = document.getElementById('efSave');
      const ta = document.getElementById('efText');
      const status = document.getElementById('efStatus');
      const setOpen = (o) => {{ pop.hidden = !o; btn.setAttribute('aria-expanded', o ? 'true' : 'false'); }};
      btn.addEventListener('click', (e) => {{ e.stopPropagation(); setOpen(pop.hidden); }});
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.querySelector('.ef-pop-x').addEventListener('click', () => setOpen(false));
      document.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') setOpen(false); }});
      save.addEventListener('click', async () => {{
        save.disabled = true; status.className = 'ef-status'; status.textContent = 'Saving…';
        try {{
          const r = await fetch(EXPLORER_FILTERS_API, {{
            method: 'POST', credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ filters: ta.value }}),
          }});
          const b = await r.json().catch(() => ({{}}));
          if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
          status.textContent = 'Saved. Reloading…';
          setTimeout(() => window.location.reload(), 600);
        }} catch (err) {{
          status.className = 'ef-status err'; status.textContent = 'Save failed: ' + (err.message || err);
          save.disabled = false;
        }}
      }});
    }})();
    // Website Analytics page-path scope: the scope indicator (shown to every
    // viewer when a filter is set) plus the admin "Edit page filter" popover.
    // Saving reloads so the server re-scopes the page-path panels and the
    // site-wide panels re-hide via getModules().
    (function(){{
      const bar = document.getElementById('analyticsFilterBar');
      const note = document.getElementById('analyticsScopeNote');
      const btn = document.getElementById('pfEditBtn');
      const active = pathFilterActive();
      if (note && active) {{
        note.hidden = false;
        note.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg><span>Limited to pages matching ' + ANALYTICS_PATH_FILTER.map(p => '<code>' + esc(p) + '</code>').join(' ') + '</span>';
      }}
      // The bar takes space only when it has content: the admin editor or a scope.
      if (bar && (btn || active)) bar.hidden = false;
      // The sessions trend stays visible under a scope, but it now counts
      // sessions that viewed a matching page (GA4's per-page Sessions metric),
      // so say that rather than letting it read as site-wide traffic.
      if (active) {{
        const title = document.getElementById('sessionsTrendTitle');
        if (title) title.textContent = 'Sessions on matching pages';
        const snote = document.getElementById('sessionsScopeNote');
        if (snote) {{
          snote.hidden = false;
          snote.textContent = 'Sessions that viewed a page matching this filter. A session that viewed more than one matching page is counted once per page, the same as GA4’s per-page Sessions metric.';
        }}
        // Average session duration comes from the landing-page report, so under
        // a scope it covers the sessions that *started* on a matching page.
        const adnote = document.getElementById('avgDurNote');
        if (adnote) adnote.textContent = 'How long a session that started on a matching page lasted on average over this range, weighted by how many sessions each of those pages brought in.';
        // Demographics keeps its geography half (served page-scoped) and drops
        // age/gender, which are user-scoped in GA4 with no page to scope by —
        // so the section is geography only, and says so.
        const dtitle = document.getElementById('demoSectionTitle');
        if (dtitle) dtitle.textContent = 'Geography';
        const dpanels = document.getElementById('demoUserPanels');
        if (dpanels) dpanels.hidden = true;
        const dnote = document.getElementById('demoScopeNote');
        if (dnote) {{
          dnote.hidden = false;
          dnote.textContent = 'Where the users who viewed a matching page came from. Counts come from GA4’s per-page geography, so a user who viewed more than one matching page is counted once per page — read the map as relative concentration, not a headcount. Age and gender are user-scoped in GA4 with no page to scope them by, so they’re hidden here.';
        }}
      }}
      if (!btn) return;
      const pop = document.getElementById('pfPop');
      const save = document.getElementById('pfSave');
      const ta = document.getElementById('pfText');
      const status = document.getElementById('pfStatus');
      const setOpen = (o) => {{ pop.hidden = !o; btn.setAttribute('aria-expanded', o ? 'true' : 'false'); }};
      btn.addEventListener('click', (e) => {{ e.stopPropagation(); setOpen(pop.hidden); }});
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.querySelector('.ef-pop-x').addEventListener('click', () => setOpen(false));
      document.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') setOpen(false); }});
      save.addEventListener('click', async () => {{
        save.disabled = true; status.className = 'ef-status'; status.textContent = 'Saving…';
        try {{
          const r = await fetch(ANALYTICS_PATH_FILTER_API, {{
            method: 'POST', credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ filter: ta.value }}),
          }});
          const b = await r.json().catch(() => ({{}}));
          if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
          status.textContent = 'Saved. Reloading…';
          setTimeout(() => window.location.reload(), 600);
        }} catch (err) {{
          status.className = 'ef-status err'; status.textContent = 'Save failed: ' + (err.message || err);
          save.disabled = false;
        }}
      }});
    }})();
    // Admin "Campaigns" picker: restrict the Explorer to a chosen subset of
    // campaigns. The checklist is built (on open) from the campaigns currently
    // loaded plus any already-saved names; Save POSTs the ticked set and reloads.
    (function(){{
      const btn = document.getElementById('ecEditBtn'); if (!btn) return;
      const pop = document.getElementById('ecPop');
      const list = document.getElementById('ecList');
      const search = document.getElementById('ecSearch');
      const selAll = document.getElementById('ecSelectAll');
      const clr = document.getElementById('ecClear');
      const save = document.getElementById('ecSave');
      const status = document.getElementById('ecStatus');
      const count = document.getElementById('ecCount');
      const badge = document.getElementById('ecBadge');
      const chosen = new Set(EXPLORER_CAMPAIGN_ALLOWLIST);
      if (EXPLORER_CAMPAIGN_ALLOWLIST.length) {{ badge.hidden=false; badge.textContent=String(EXPLORER_CAMPAIGN_ALLOWLIST.length); }}
      const updateCount = () => {{
        const n = list.querySelectorAll('input:checked').length;
        count.textContent = n ? (n + ' selected') : 'All campaigns';
      }};
      function build(){{
        // Union of currently-loaded campaign names and any already-saved ones, so
        // a saved campaign with no spend in the current range still shows (checked).
        const names = new Set();
        for (const r of explorerRows) {{ const n=String(r.campaign_name||''); if (n) names.add(n); }}
        for (const n of chosen) names.add(n);
        const sorted = [...names].sort((a,b)=>a.localeCompare(b));
        if (!sorted.length) {{ list.innerHTML = '<div class="ec-empty">No campaigns loaded yet.</div>'; updateCount(); return; }}
        list.innerHTML = sorted.map(n=>`<label class="ec-option"><input type="checkbox" value="${{esc(n)}}"${{chosen.has(n)?' checked':''}}><span class="ec-name" title="${{esc(n)}}">${{esc(n)}}</span></label>`).join('');
        updateCount();
      }}
      const setOpen = (o) => {{ pop.hidden = !o; btn.setAttribute('aria-expanded', o ? 'true' : 'false'); if (o) {{ build(); search.value=''; status.className='ef-status'; status.textContent=''; }} }};
      btn.addEventListener('click', (e) => {{ e.stopPropagation(); setOpen(pop.hidden); }});
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.querySelector('.ef-pop-x').addEventListener('click', () => setOpen(false));
      document.addEventListener('click', () => setOpen(false));
      document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') setOpen(false); }});
      list.addEventListener('change', updateCount);
      search.addEventListener('input', () => {{
        const q = search.value.trim().toLowerCase();
        list.querySelectorAll('.ec-option').forEach(o => {{
          const n = o.querySelector('.ec-name').textContent.toLowerCase();
          o.hidden = !!q && !n.includes(q);
        }});
      }});
      selAll.addEventListener('click', () => {{ list.querySelectorAll('.ec-option:not([hidden]) input').forEach(cb=>cb.checked=true); updateCount(); }});
      clr.addEventListener('click', () => {{ list.querySelectorAll('input').forEach(cb=>cb.checked=false); updateCount(); }});
      save.addEventListener('click', async () => {{
        const campaigns = [...list.querySelectorAll('input:checked')].map(cb=>cb.value);
        save.disabled = true; status.className='ef-status'; status.textContent='Saving…';
        try {{
          const r = await fetch(EXPLORER_CAMPAIGNS_API, {{
            method:'POST', credentials:'same-origin',
            headers:{{ 'Content-Type':'application/json' }},
            body: JSON.stringify({{ campaigns }}),
          }});
          const b = await r.json().catch(() => ({{}}));
          if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
          status.className='ef-status ok'; status.textContent='Saved. Reloading…';
          setTimeout(() => window.location.reload(), 600);
        }} catch (err) {{
          status.className='ef-status err'; status.textContent='Save failed: ' + (err.message || err);
          save.disabled = false;
        }}
      }});
    }})();
    const explorerFilterState = new Map(); // groupId -> Set of active chip labels
    let explorerRows = [];
    // Same-shape rows for the Compare picker's window (previous period/year),
    // fetched alongside the current period so the summary cards and table
    // total can show a "vs previous" delta like every other panel does.
    let explorerPrevRows = [];
    let verifiedByAdId = {{}};
    let verifiedByAdIdEvent = {{}};
    let verifiedByGoogleCampaignId = {{}};
    let verifiedByGoogleCampaignIdEvent = {{}};
    let verifiedByLinkedinGroup = {{}};
    let verifiedByLinkedinGroupEvent = {{}};
    let keyEventList = [];
    function normalizeLiName(name) {{ return String(name||'').replace(/\\+/g,' ').replace(/\\s+/g,' ').trim().toLowerCase(); }}
    const KE_STORAGE_KEY = 'ce_verified_ke_{api_client_key}';
    let selectedKeyEvent = (function(){{ try {{ return localStorage.getItem(KE_STORAGE_KEY) || '__all__'; }} catch(e) {{ return '__all__'; }} }})();
    function applyVerifiedSelection() {{
      for (const r of explorerRows) {{
        const id=String(r.ad_id||'');
        r.verified = num(verifiedByAdId[id]);
        r.verified_sel = (selectedKeyEvent==='__all__') ? r.verified : num((verifiedByAdIdEvent[id]||{{}})[selectedKeyEvent]);
      }}
    }}
    function keSelectHtml() {{
      if (!keyEventList.length) return '';
      const opts=[['__all__','All key events'],...keyEventList.map(e=>[e,e])]
        .map(([v,l])=>`<option value="${{esc(v)}}"${{selectedKeyEvent===v?' selected':''}}>${{esc(l)}}</option>`).join('');
      const active = selectedKeyEvent!=='__all__';
      const funnel = '<svg class="ke-select-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 3h12l-4.5 5.5V13L6.5 11.5V8.5z"/></svg>';
      return `<span class="ke-select-wrap${{active?' active':''}}">${{funnel}}<select class="ke-select" title="Show GA4-verified conversions for a single key event">${{opts}}</select><span class="ke-select-caret" aria-hidden="true">▾</span></span>`;
    }}

    function buildChips(container, keys, stateSet, onChange) {{
      const el = typeof container==='string' ? document.getElementById(container) : container;
      el.innerHTML = ['All',...keys].map(k=>`<button type="button" class="chip" data-key="${{esc(k)}}">${{esc(k)}}</button>`).join('');
      el.querySelectorAll('.chip').forEach(btn => btn.addEventListener('click', () => {{
        const key=btn.dataset.key;
        if (key==='All') stateSet.clear(); else if (stateSet.has(key)) stateSet.delete(key); else stateSet.add(key);
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
        (onChange||renderExplorer)();
      }}));
      el.querySelectorAll('.chip').forEach(b => b.classList.toggle('active', b.dataset.key==='All' ? stateSet.size===0 : stateSet.has(b.dataset.key)));
    }}
    // Explorer filter groups (Business line / Product / Region …) render as
    // space-saving dropdowns in the sticky top bar, each holding a multi-select
    // checkbox list. Reuses the .ke-dd-* dropdown styling.
    function closeAllExplDropdowns(except) {{
      document.querySelectorAll('#explorerFilterBar .expl-dd').forEach(dd => {{
        if (dd===except) return;
        const p=dd.querySelector('.ke-dd-panel'); if (p) p.hidden=true;
        dd.classList.remove('open');
        const t=dd.querySelector('.ke-dd-toggle'); if (t) t.setAttribute('aria-expanded','false');
      }});
    }}
    function wireExplorerDropdown(dd, g, set) {{
      const toggle=dd.querySelector('.ke-dd-toggle');
      const panel=dd.querySelector('.ke-dd-panel');
      const list=dd.querySelector('.ke-dd-list');
      const label=dd.querySelector('.expl-dd-label');
      const updateLabel=()=>{{ label.textContent = set.size ? `${{label.dataset.base}} · ${{set.size}}` : label.dataset.base; toggle.classList.toggle('has-active', set.size>0); }};
      list.innerHTML = g.chips.map(c=>`<label class="ke-dd-option${{set.has(c.label)?' active':''}}"><input type="checkbox"${{set.has(c.label)?' checked':''}} data-val="${{esc(c.label)}}"><span class="ke-dd-name">${{esc(c.label)}}</span></label>`).join('');
      list.querySelectorAll('input[data-val]').forEach(cb=>cb.addEventListener('change',()=>{{
        const v=cb.dataset.val;
        if (set.has(v)) set.delete(v); else set.add(v);
        const opt=cb.closest('.ke-dd-option'); if (opt) opt.classList.toggle('active', set.has(v));
        updateLabel();
        renderExplorer();
      }}));
      toggle.addEventListener('click', e=>{{
        e.stopPropagation();
        if (panel.hidden) {{ closeAllExplDropdowns(dd); panel.hidden=false; dd.classList.add('open'); toggle.setAttribute('aria-expanded','true'); }}
        else {{ panel.hidden=true; dd.classList.remove('open'); toggle.setAttribute('aria-expanded','false'); }}
      }});
      updateLabel();
    }}
    function buildExplorerFilters() {{
      const host = document.getElementById('explorerFilterBar');
      if (!host) return;
      explorerFilterState.clear();
      if (!EXPLORER_FILTER_GROUPS.length) {{ host.innerHTML=''; host.hidden=true; return; }}
      host.innerHTML = EXPLORER_FILTER_GROUPS.map((g,i) =>
        `<div class="ke-dropdown expl-dd" data-group="${{i}}">`+
          `<button type="button" class="ke-dd-toggle" aria-haspopup="listbox" aria-expanded="false">`+
            `<span class="expl-dd-label" data-base="${{esc(g.label)}}">${{esc(g.label)}}</span>`+
            `<span class="ke-dd-caret">▾</span>`+
          `</button>`+
          `<div class="ke-dd-panel" hidden><div class="ke-dd-list"></div></div>`+
        `</div>`
      ).join('');
      EXPLORER_FILTER_GROUPS.forEach((g,i) => {{
        const set = new Set();
        explorerFilterState.set(g.id, set);
        wireExplorerDropdown(host.querySelector(`.expl-dd[data-group="${{i}}"]`), g, set);
      }});
    }}
    document.addEventListener('click', e=>{{ if (!e.target.closest('#explorerFilterBar .expl-dd')) closeAllExplDropdowns(); }});
    document.addEventListener('keydown', e=>{{ if (e.key==='Escape') closeAllExplDropdowns(); }});
    function explorerRowMatches(row) {{
      const name=String(row.campaign_name||'').toLowerCase();
      for (const g of EXPLORER_FILTER_GROUPS) {{
        const set=explorerFilterState.get(g.id);
        if (!set||!set.size) continue;
        const ok=g.chips.some(c=>set.has(c.label)&&c.phrases.some(p=>p&&name.includes(p)));
        if (!ok) return false;
      }}
      const platOk=!platformFilter.size||[...platformFilter].some(k=>k.toLowerCase()===(row.platform||''));
      return platOk;
    }}
    function zeroMetrics() {{ return {{spend:0,impressions:0,clicks:0,conversions:0,verified:0,verified_sel:0}}; }}
    function addMetrics(acc,r) {{ acc.spend+=num(r.spend);acc.impressions+=num(r.impressions);acc.clicks+=num(r.clicks);acc.conversions+=num(r.conversions);acc.verified+=num(r.verified);acc.verified_sel+=num(r.verified_sel); }}
    function withCtr(m) {{ return {{...m,ctr:m.impressions?(num(m.clicks)/num(m.impressions)*100):0}}; }}
    function buildExplorerTree(rows) {{
      const campaigns=new Map();
      for (const r of rows) {{
        const cName=r.campaign_name||'—', platform=(r.platform||'google').toLowerCase(), cKey=platform+'|'+cName;
        if (!campaigns.has(cKey)) campaigns.set(cKey,{{name:cName,platform,campaign_id:r.campaign_id||'',metrics:zeroMetrics(),groups:new Map()}});
        const camp=campaigns.get(cKey);
        addMetrics(camp.metrics,r);
        const gName=r.ad_group_name||'—';
        if (!camp.groups.has(gName)) camp.groups.set(gName,{{name:gName,metrics:zeroMetrics(),ads:[]}});
        const grp=camp.groups.get(gName);
        if (r._verifiedNa) grp.metrics._verifiedNa=true;
        addMetrics(grp.metrics,r);
        grp.ads.push(r);
      }}
      // Sort every level by the active column / direction.
      const {{key,dir}}=explorerSort, mul=dir==='asc'?1:-1;
      const cmpName=(x,y)=>mul*String(x).localeCompare(String(y),undefined,{{numeric:true}});
      const cmpMetric=(x,y)=>mul*(explorerMetricVal(x,key)-explorerMetricVal(y,key));
      const cmpNode=(a,b)=> key==='name' ? cmpName(a[1].name,b[1].name) : cmpMetric(a[1].metrics,b[1].metrics);
      // No verified data yet (table not synced) -> show "—", not a misleading 0.
      const gHasData = Object.keys(verifiedByGoogleCampaignId).length > 0;
      const lHasData = Object.keys(verifiedByLinkedinGroup).length > 0;
      for (const camp of campaigns.values()) {{
        // Google verified is a campaign-level number (native GA4 link); attach it
        // to the campaign node — sub-levels stay "—" (no reliable per-ad id).
        if (camp.platform==='google') {{
          if (gHasData && camp.campaign_id) {{
            const cid=String(camp.campaign_id);
            const gv=num(verifiedByGoogleCampaignId[cid]);
            camp.metrics.verified=gv;
            camp.metrics.verified_sel=(selectedKeyEvent==='__all__')
              ? gv
              : num((verifiedByGoogleCampaignIdEvent[cid]||{{}})[selectedKeyEvent]);
          }} else {{
            camp.metrics._verifiedNa=true;
          }}
        }}
        // LinkedIn verified is campaign-group-level, matched by normalized name
        // (no ids in GA4). Sub-levels (ad set / creative) stay "—".
        else if (camp.platform==='linkedin') {{
          if (lHasData) {{
            const gname=normalizeLiName(camp.name);
            const lv=num(verifiedByLinkedinGroup[gname]);
            camp.metrics.verified=lv;
            camp.metrics.verified_sel=(selectedKeyEvent==='__all__')
              ? lv
              : num((verifiedByLinkedinGroupEvent[gname]||{{}})[selectedKeyEvent]);
          }} else {{
            camp.metrics._verifiedNa=true;
          }}
        }}
        // Microsoft/Bing has no GA4 verified-conversion mapping — show "—".
        else if (camp.platform==='microsoft') {{
          camp.metrics._verifiedNa=true;
        }}
        for (const grp of camp.groups.values()) {{
          grp.ads.sort((a,b)=> key==='name' ? cmpName(explorerAdName(a),explorerAdName(b)) : cmpMetric(a,b));
        }}
        camp.groups=new Map([...camp.groups.entries()].sort(cmpNode));
      }}
      return new Map([...campaigns.entries()].sort(cmpNode));
    }}
    function metricCells(m) {{ const wc=withCtr(m); return METRIC_COLS.map(c=>{{ const cell=(c.key==='verified_sel'&&m._verifiedNa)?'—':c.format(wc[c.key]); return `<td${{c.cls?` class="${{c.cls}}"`:''}}>${{cell}}</td>`; }}).join(''); }}
    // Same as metricCells, but for the tree footer's grand total: when a
    // comparison period is set (prevAgg), each metric gets a "vs previous"
    // delta chip underneath, same convention as the summary cards above.
    function explorerTotalCells(m, prevAgg) {{
      const wc=withCtr(m);
      return METRIC_COLS.map(c=>{{
        if (c.key==='verified_sel') {{ const cell=m._verifiedNa?'—':c.format(wc[c.key]); return `<td${{c.cls?` class="${{c.cls}}"`:''}}>${{cell}}</td>`; }}
        const cell=c.format(wc[c.key]);
        const delta=prevAgg?summaryDeltaHtml(wc[c.key],prevAgg[c.key],EXPLORER_METRIC_DIR[c.key]):'';
        return `<td${{c.cls?` class="${{c.cls}}"`:''}}>${{cell}}${{delta?`<div class="expl-total-delta">${{delta}}</div>`:''}}</td>`;
      }}).join('');
    }}
    // Grand total for the tree footer. Summed from the campaign nodes rather than
    // the raw rows so the footer is always exactly the sum of the campaign rows
    // above it — including verified conversions, which are resolved per campaign
    // (Google by campaign id, LinkedIn by group name) and would otherwise be
    // double counted or missed. CTR is recomputed from total clicks/impressions,
    // never averaged. Verified stays "—" only when no campaign in view has data.
    function explorerTotals(tree) {{
      const t=zeroMetrics();
      let anyVerified=false;
      for (const camp of tree.values()) {{
        addMetrics(t, camp.metrics);
        if (!camp.metrics._verifiedNa) anyVerified=true;
      }}
      if (!anyVerified) t._verifiedNa=true;
      return t;
    }}
    // Brand marks for the platform column — inline SVG so the tree reads as a
    // sleek icon rail instead of text pills. Google = 4-colour G, LinkedIn +
    // Meta = their single-path glyphs in brand colours.
    const PLATFORM_SVG = {{
      google: '<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>',
      linkedin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0A66C2" d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.55V9h3.57v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.72V1.72C24 .77 23.2 0 22.22 0z"/></svg>',
      meta: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#0668E1" d="M6.915 4.03c-1.968 0-3.683 1.28-4.871 3.113C.704 9.208 0 11.883 0 14.449c0 .706.07 1.369.21 1.973a6.624 6.624 0 0 0 .265.86 5.297 5.297 0 0 0 .371.761c.696 1.159 1.818 1.927 3.593 1.927 1.497 0 2.633-.671 3.965-2.444.76-1.012 1.144-1.626 2.663-4.32l.756-1.339.186-.325c.061.1.121.196.183.294l2.152 3.595c.724 1.21 1.665 2.556 2.47 3.314 1.046.987 1.992 1.22 3.06 1.22 1.075 0 1.876-.355 2.455-.843a3.743 3.743 0 0 0 .81-.973c.542-.939.861-2.127.861-3.745 0-2.72-.681-5.357-2.084-7.45-1.282-1.912-2.957-2.93-4.716-2.93-1.047 0-2.088.467-3.053 1.308-.652.57-1.257 1.29-1.82 2.05-.69-.875-1.335-1.547-1.958-2.056-1.182-.966-2.315-1.303-3.454-1.303zm10.16 2.053c1.147 0 2.188.758 2.992 1.999 1.132 1.748 1.647 4.195 1.647 6.4 0 1.548-.368 2.9-1.839 2.9-.58 0-1.027-.235-1.664-1.001-.496-.601-1.343-1.878-2.832-4.358l-.617-1.028a44.908 44.908 0 0 0-1.255-1.98c.07-.109.141-.224.211-.327 1.12-1.667 2.118-2.605 3.325-2.605zm-10.201.553c1.265 0 2.058.791 2.675 1.446.307.327.737.871 1.234 1.579l-1.02 1.566c-.757 1.163-1.882 3.017-2.837 4.338-1.191 1.649-1.81 1.817-2.486 1.817-.524 0-1.038-.237-1.383-.794-.263-.426-.464-1.13-.464-2.046 0-2.221.63-4.535 1.66-6.088.454-.687.964-1.226 1.533-1.533a2.264 2.264 0 0 1 1.088-.282z"/></svg>',
      microsoft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#F25022" d="M1 1h10.4v10.4H1z"/><path fill="#7FBA00" d="M12.6 1H23v10.4H12.6z"/><path fill="#00A4EF" d="M1 12.6h10.4V23H1z"/><path fill="#FFB900" d="M12.6 12.6H23V23H12.6z"/></svg>',
    }};
    function platformIcon(p) {{
      const k=(p||'google').toLowerCase();
      const key=k==='linkedin'?'linkedin':k==='meta'?'meta':k==='microsoft'?'microsoft':'google';
      const label=key==='linkedin'?'LinkedIn':key==='meta'?'Meta':key==='microsoft'?'Microsoft Ads':'Google';
      return `<span class="plat-ico plat-ico-${{key}}" title="${{label}}" aria-label="${{label}}">${{PLATFORM_SVG[key]}}</span>`;
    }}
    function parseCopyList(v) {{
      if (Array.isArray(v)) return v.filter(Boolean);
      if (typeof v==='string' && v) {{ try {{ const a=JSON.parse(v); return Array.isArray(a)?a.filter(Boolean):[]; }} catch(e) {{ return []; }} }}
      return [];
    }}
    const HEADLINES_VISIBLE = 5;
    const ICON_SHUFFLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
    // Turn a final URL into a Google-style display path (host + first segment).
    function gadsDisplayUrl(ad) {{
      const raw=String(ad.final_url||'').trim();
      try {{
        const u=new URL(raw.match(/^https?:\\/\\//)?raw:('https://'+raw));
        const seg=u.pathname.split('/').filter(Boolean)[0];
        return u.hostname.replace(/^www\\./,'')+(seg?('/'+seg):'');
      }} catch(e) {{
        return (raw||'example.com').replace(/^https?:\\/\\//,'').replace(/^www\\./,'').split('/').slice(0,2).join('/');
      }}
    }}
    // Fisher–Yates pick of n items — how the shuffle randomizes the RSA mix.
    function gadsPick(arr,n) {{
      const a=arr.slice();
      for (let i=a.length-1;i>0;i--) {{ const j=Math.floor(Math.random()*(i+1)); const t=a[i]; a[i]=a[j]; a[j]=t; }}
      return a.slice(0,n);
    }}
    // Re-render a Google preview's shown assets (up to 3 headlines, 2 descriptions).
    // shuffle=true randomizes the mix like Google's RSA serving would.
    function gadsUpdatePreview(cell, shuffle) {{
      let hs=[], ds=[];
      try {{ hs=JSON.parse(cell.dataset.hs||'[]'); }} catch(e) {{}}
      try {{ ds=JSON.parse(cell.dataset.ds||'[]'); }} catch(e) {{}}
      const h=shuffle?gadsPick(hs,3):hs.slice(0,3);
      const d=shuffle?gadsPick(ds,2):ds.slice(0,2);
      const t=cell.querySelector('.gads-title'); if (t) t.textContent=h.join(' | ');
      const de=cell.querySelector('.gads-desc'); if (de) de.textContent=d.join(' ');
    }}
    // Google search RSA → a realistic ad preview (3 headlines / 2 descriptions),
    // a shuffle button that re-rolls the mix, and the full asset list in an
    // accordion. Non-search / image ads fall through to the thumbnail layout.
    function googleAdCell(ad, hs, ds) {{
      const sub=ad.ad_id?`<span class="ad-label-sub"><span class="ad-id">#${{esc(ad.ad_id)}}</span></span>`:'';
      const disp=esc(gadsDisplayUrl(ad));
      const h0=hs.slice(0,3).map(esc).join(' | ');
      const d0=ds.slice(0,2).map(esc).join(' ');
      const allH=hs.map((v,i)=>`<span class="ad-copy-line"><span class="ad-copy-tag ad-copy-tag--h">H${{i+1}}</span>${{esc(v)}}</span>`).join('');
      const allD=ds.map((v,i)=>`<span class="ad-copy-line"><span class="ad-copy-tag ad-copy-tag--d">D${{i+1}}</span>${{esc(v)}}</span>`).join('');
      const cnt=`${{hs.length}} headline${{hs.length===1?'':'s'}} · ${{ds.length}} description${{ds.length===1?'':'s'}}`;
      const acc=(hs.length>3||ds.length>2)
        ? `<button type="button" class="ad-copy-more" data-more-label="All assets (${{cnt}})">All assets (${{cnt}})</button><div class="ad-copy-extra" hidden>${{allH}}${{allD}}</div>`
        : '';
      return `<div class="ad-cell gads" data-hs="${{esc(JSON.stringify(hs))}}" data-ds="${{esc(JSON.stringify(ds))}}"><span class="ad-meta">
        <div class="gads-preview">
          <div class="gads-top"><span class="gads-badge">Ad</span><span class="gads-url">${{disp}}</span><button type="button" class="gads-shuffle" title="Shuffle asset mix" aria-label="Shuffle asset mix">${{ICON_SHUFFLE}}</button></div>
          <div class="gads-title">${{h0}}</div>
          <div class="gads-desc">${{d0}}</div>
        </div>
        ${{sub}}
        ${{acc}}
      </span></div>`;
    }}
    function adCell(ad) {{
      const platform=(ad.platform||'google').toLowerCase();
      // Full RSA copy (up to 15 headlines / 4 descriptions) from the JSON arrays;
      // fall back to the legacy flat columns for rows synced before the repull.
      let hs=parseCopyList(ad.headlines); if(!hs.length) hs=[ad.headline_1,ad.headline_2,ad.headline_3].filter(Boolean);
      let ds=parseCopyList(ad.descriptions); if(!ds.length) ds=[ad.description_1,ad.description_2].filter(Boolean);
      // Google search ads (text/RSA) get the true ad preview; image/display and
      // LinkedIn/Meta creatives keep the thumbnail-based layout below.
      if (platform==='google' && (hs.length||ds.length) && !ad.thumbnail_url) {{
        return googleAdCell(ad, hs, ds);
      }}
      const type=ad.media_type?`<span class="ad-type">${{esc(ad.media_type)}}</span>`:'';
      // Full-size preview prefers image_url/video_url over the (often smaller/
      // cropped) thumbnail_url; click opens it in the modal (see creativePreview).
      const fullImg=ad.image_url||ad.thumbnail_url||'';
      const thumb=ad.thumbnail_url?`<img class="ad-thumb" src="${{esc(ad.thumbnail_url)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'" data-preview-image="${{esc(fullImg)}}" data-preview-video="${{esc(ad.video_url||'')}}">` :'';
      // ad_name is often blank for RSAs; prefer it, then the first headline, then
      // the raw ad ID as a last resort (shown small/muted, not as the headline label).
      const label=esc(ad.ad_name || hs[0] || ad.ad_label || '—');
      const idTag=ad.ad_id?`<span class="ad-id">#${{esc(ad.ad_id)}}</span>`:'';
      const visible=hs.slice(0, HEADLINES_VISIBLE), extra=hs.slice(HEADLINES_VISIBLE);
      const visibleLines=visible.map((v,i)=>['H'+(i+1),v]);
      const extraLines=extra.map((v,i)=>['H'+(i+1+HEADLINES_VISIBLE),v]);
      const descLines=ds.map((v,i)=>['D'+(i+1),v]);
      const line=([tag,v])=>`<span class="ad-copy-line"><span class="ad-copy-tag ad-copy-tag--${{(tag[0]||'').toLowerCase()}}">${{tag}}</span>${{esc(v)}}</span>`;
      const visibleHtml=visibleLines.filter(([,v])=>v).map(line).join('');
      const extraHtml=extraLines.filter(([,v])=>v).map(line).join('');
      const descHtml=descLines.filter(([,v])=>v).map(line).join('');
      const more=extra.length
        ? `<button type="button" class="ad-copy-more" data-more-label="+${{extra.length}} more">+${{extra.length}} more</button><div class="ad-copy-extra" hidden>${{extraHtml}}</div>`
        : '';
      const copyLines=visibleHtml+more+descHtml;
      const copy=copyLines?`<div class="ad-copy">${{copyLines}}</div>`:'';
      return `<div class="ad-cell">${{thumb}}<span class="ad-meta"><span class="ad-label">${{label}}${{idTag}}</span>${{type}}${{copy}}</span></div>`;
    }}
    // Sleek child-count indicator: up to 5 dots represent the count at a glance
    // (a "+" marks overflow). The exact count + unit ("3 ad groups") stays on
    // the hover title rather than as inline text next to the row name.
    function treeCount(n, unit) {{
      const cap=Math.min(n, 5);
      let dots='';
      for (let i=0;i<cap;i++) dots+='<span class="tc-dot"></span>';
      if (n>5) dots+='<span class="tc-dot tc-more">+</span>';
      const label=esc(unit)+(n===1?'':'s');
      return `<span class="tree-count" title="${{n}} ${{label}}" aria-label="${{n}} ${{label}}">`
        + `<span class="tc-dots" aria-hidden="true">${{dots}}</span></span>`;
    }}
    // Campaign allowlist: when an admin has scoped this portal to specific
    // campaigns, everything the Explorer shows (table + summary cards + counts)
    // is limited to that set. Empty allowlist = no restriction.
    const _campaignAllowSet = new Set(EXPLORER_CAMPAIGN_ALLOWLIST);
    function explorerAllowedRows() {{
      if (!_campaignAllowSet.size) return explorerRows;
      return explorerRows.filter(r => _campaignAllowSet.has(String(r.campaign_name||'')));
    }}
    function explorerPrevAllowedRows() {{
      if (!_campaignAllowSet.size) return explorerPrevRows;
      return explorerPrevRows.filter(r => _campaignAllowSet.has(String(r.campaign_name||'')));
    }}
    // Direction each explorer metric moves in that counts as "good," for the
    // vs-previous delta coloring — same convention as SUMMARY_CARDS above.
    // Verified conv. isn't included: it's stitched together from separate
    // per-platform verified-conversions calls that aren't fetched for the
    // comparison window (yet), so it has nothing to diff against.
    const EXPLORER_METRIC_DIR = {{ spend:'neutral', impressions:'up', clicks:'up', ctr:'up', conversions:'up' }};
    function renderExplorer() {{
      const base=explorerAllowedRows();
      const filtered=base.filter(explorerRowMatches);
      // Aggregate summary cards — slice with the same filters as the table
      // (date range, Platform chips, and the explorer filter chips).
      const agg=withCtr(filtered.reduce((a,r)=>{{addMetrics(a,r);return a;}}, zeroMetrics()));
      // Same aggregation over the Compare picker's window (previous period/year),
      // sliced with the same filters, so cards and the table total can show a
      // "vs previous" delta consistent with the rest of the dashboard.
      const prevFiltered = compareStart ? explorerPrevAllowedRows().filter(explorerRowMatches) : null;
      const aggPrev = prevFiltered ? withCtr(prevFiltered.reduce((a,r)=>{{addMetrics(a,r);return a;}}, zeroMetrics())) : null;
      // Google verified is campaign-level (not on rows), so add it once per distinct
      // Google campaign in the filtered set for the summary total.
      const gcSeen=new Set(); let googleVerifiedTotal=0;
      for (const r of filtered) {{ const cid=String(r.campaign_id||''); if (r.platform==='google' && cid && !gcSeen.has(cid)) {{ gcSeen.add(cid); googleVerifiedTotal+=num(verifiedByGoogleCampaignId[cid]); }} }}
      // LinkedIn verified is campaign-group-level (name-matched); add once per group.
      const liSeen=new Set(); let linkedinVerifiedTotal=0;
      for (const r of filtered) {{ if (r.platform==='linkedin') {{ const gn=normalizeLiName(r.campaign_name||''); if (gn && !liSeen.has(gn)) {{ liSeen.add(gn); linkedinVerifiedTotal+=num(verifiedByLinkedinGroup[gn]); }} }} }}
      const scards=document.getElementById('explorerSummaryCards');
      if (scards) scards.innerHTML=[
        ['spend','Spend',v=>money(v)],['impressions','Impressions',v=>count(v)],['clicks','Clicks',v=>count(v)],['ctr','CTR',v=>num(v).toFixed(2)+'%'],
        ['conversions','Conversions',v=>count(v)],
      ].map(([k,l,fmt])=>{{
        const delta=aggPrev?summaryDeltaHtml(agg[k],aggPrev[k],EXPLORER_METRIC_DIR[k]):'';
        return `<div class="card"><div class="card-title">${{l}}</div><div class="card-value">${{fmt(agg[k])}}</div>${{delta?`<div class="card-foot">${{delta}}</div>`:''}}</div>`;
      }}).join('') + `<div class="card"><div class="card-title">Verified conv. (GA4)</div><div class="card-value">${{count(num(agg.verified)+googleVerifiedTotal+linkedinVerifiedTotal)}}</div></div>`;
      const el=document.getElementById('explorerTable');
      const tree=buildExplorerTree(filtered);
      if (!tree.size) {{ el.innerHTML=`<tbody><tr><td class="empty">No campaigns match these filters.</td></tr></tbody>`; }} else {{
        const sArrow=k=>explorerSort.key===k?(explorerSort.dir==='asc'?' ▲':' ▼'):'';
        const thInner=c=>c.keSelect
          ? `<span class="ga4-head"><span class="ga4-head-top"><span class="ga4-badge">GA4</span><span class="ga4-head-label">${{esc(c.label)}}</span>${{sArrow(c.key)}}</span>${{keSelectHtml()}}</span>`
          : `${{esc(c.label)}}${{sArrow(c.key)}}`;
        const head=`<thead><tr><th class="left expl-sort${{explorerSort.key==='name'?' active':''}}" data-key="name">Campaign / Ad group / Ad${{sArrow('name')}}</th>${{METRIC_COLS.map(c=>`<th class="expl-sort${{c.cls?' '+c.cls:''}}${{explorerSort.key===c.key?' active':''}}" data-key="${{c.key}}"${{c.title?` title="${{esc(c.title)}}"`:''}}>${{thInner(c)}}</th>`).join('')}}</tr></thead>`;
        let body='', cIdx=0;
        for (const camp of tree.values()) {{
          const cId='c'+(cIdx++), gCount=camp.groups.size;
          body+=`<tr class="tree-row lvl-campaign" data-id="${{cId}}" data-expandable="1"><td class="left"><span class="caret"></span>${{platformIcon(camp.platform)}}<span class="tree-name">${{esc(camp.name)}}</span>${{treeCount(gCount,'ad group')}}</td>${{metricCells(camp.metrics)}}</tr>`;
          let gIdx=0;
          for (const grp of camp.groups.values()) {{
            const gId=cId+'g'+(gIdx++), aCount=grp.ads.length;
            body+=`<tr class="tree-row lvl-group" data-id="${{gId}}" data-parent="${{cId}}" data-expandable="1" hidden><td class="left"><span class="indent1"></span><span class="caret"></span><span class="tree-name">${{esc(grp.name)}}</span>${{treeCount(aCount,'ad')}}</td>${{metricCells(grp.metrics)}}</tr>`;
            for (const ad of grp.ads) {{ body+=`<tr class="tree-row lvl-ad" data-parent="${{gId}}" hidden><td class="left"><span class="indent2"></span>${{adCell(ad)}}</td>${{metricCells(ad)}}</tr>`; }}
          }}
        }}
        // Grand total pinned under the tree. It sums whatever is currently in
        // view, so a platform chip, a filter dropdown, the campaign allowlist or
        // a new date range all re-total it on the next render.
        const totals=explorerTotals(tree);
        const nCamp=tree.size;
        const foot=`<tfoot><tr class="expl-total"><td class="left"><span class="tree-name">Total</span><span class="tot-sub">${{nCamp}} campaign${{nCamp===1?'':'s'}}</span></td>${{explorerTotalCells(totals,aggPrev)}}</tr></tfoot>`;
        el.innerHTML=head+`<tbody>${{body}}</tbody>`+foot;
      }}
      const filterActive=[...explorerFilterState.values()].some(s=>s.size);
      const totalCampaigns=new Set(base.map(r=>r.campaign_name||'—')).size;
      setStatus('explorerStatus', base.length
        ? (filterActive ? `${{tree.size}} of ${{totalCampaigns}} campaign(s)` : `${{tree.size}} campaign(s) · ${{base.length}} ads`)
        : 'No campaigns found');
    }}
    function toggleExplorerRow(row) {{
      const id=row.dataset.id, table=row.closest('table'), expanded=row.classList.toggle('open');
      if (expanded) {{ table.querySelectorAll(`tr[data-parent="${{id}}"]`).forEach(c=>{{c.hidden=false;}}); }}
      else {{
        const stack=[id];
        while (stack.length) {{ const pid=stack.pop(); table.querySelectorAll(`tr[data-parent="${{pid}}"]`).forEach(c=>{{c.hidden=true;c.classList.remove('open');if(c.dataset.id)stack.push(c.dataset.id);}}); }}
      }}
    }}
    function normalizeExplorerRows(google, linkedin, meta, microsoft) {{
      const out=[];
      for (const r of (google&&google.rows?google.rows:[])) {{
        out.push({{platform:'google',campaign_id:r.campaign_id,campaign_name:r.campaign_name,ad_group_name:r.ad_group_name,ad_label:r.ad_label,ad_id:r.ad_id,headlines:r.headlines,descriptions:r.descriptions,headline_1:r.headline_1,headline_2:r.headline_2,headline_3:r.headline_3,description_1:r.description_1,description_2:r.description_2,ad_name:r.ad_name,final_url:r.final_url,ad_type:r.ad_type,thumbnail_url:'',media_type:r.ad_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),_verifiedNa:true}});
      }}
      for (const r of (linkedin&&linkedin.rows?linkedin.rows:[])) {{
        out.push({{platform:'linkedin',campaign_name:r.campaign_group_name||r.campaign_name,ad_group_name:r.campaign_name,ad_label:r.creative_name,thumbnail_url:r.thumbnail_url||r.image_url||'',image_url:r.image_url||'',video_url:r.video_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),_verifiedNa:true}});
      }}
      for (const r of (meta&&meta.rows?meta.rows:[])) {{
        out.push({{platform:'meta',campaign_name:r.campaign_name,ad_group_name:r.adset_name,ad_label:r.ad_name,ad_id:r.ad_id,thumbnail_url:r.thumbnail_url||r.image_url||'',image_url:r.image_url||'',video_url:r.video_url||'',media_type:r.media_type||'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions)}});
      }}
      // Microsoft: campaign → ad group → ad (with served ad copy), like Google
      // text ads. The served title parts / descriptions map onto the same
      // headline_1..3 / description_1..2 fields adCell renders. Before ad-level
      // data syncs, the rows are campaign-grain (ad fields blank) and collapse to
      // a single campaign node. No GA4 verified mapping for Bing → verified "—".
      for (const r of (microsoft&&microsoft.rows?microsoft.rows:[])) {{
        // Prefer the full RSA asset lists (JSON from Campaign Management); fall
        // back to the served title parts when creative copy hasn't synced.
        const heads=r.headlines||[r.title_part_1,r.title_part_2,r.title_part_3].filter(Boolean);
        const descs=r.descriptions||[r.description_1,r.description_2].filter(Boolean);
        out.push({{platform:'microsoft',campaign_id:r.campaign_id,campaign_name:r.campaign_name,ad_group_name:r.ad_group_name||'',ad_id:r.ad_id,ad_label:r.ad_title||r.title_part_1||'',ad_name:r.ad_title||'',headlines:heads,descriptions:descs,headline_1:r.title_part_1,headline_2:r.title_part_2,headline_3:r.title_part_3,description_1:r.description_1,description_2:r.description_2,final_url:r.final_url,ad_type:r.ad_type,media_type:r.ad_type||'',thumbnail_url:'',spend:num(r.spend),impressions:num(r.impressions),clicks:num(r.clicks),conversions:num(r.conversions),_verifiedNa:true}});
      }}
      return out;
    }}
    // "Keyword Performance" — Google Ads search keywords only; the section stays
    // hidden for clients with no keyword data. Summary cards + an insight
    // banner + a sortable/searchable table with match badges and in-cell bars,
    // all derived from the same keyword rows (no extra fetch).
    let kwAllRows=[];
    let kwSort={{ key:'spend', dir:'desc' }};
    let kwSearch='';
    let kwMatchFilter=new Set();  // uppercased match types; empty = all
    const KW_PER_PAGE=10; let kwPageNum=1;
    const KW_COLS=[
      {{key:'keyword_text',label:'Keyword',left:true}},
      {{key:'match_type',label:'Match',left:true}},
      {{key:'spend',label:'Spend'}},
      {{key:'impressions',label:'Impr.'}},
      {{key:'clicks',label:'Clicks'}},
      {{key:'ctr',label:'CTR'}},
      {{key:'conversions',label:'Conv.'}},
      {{key:'cvr',label:'CVR'}},
      {{key:'cpa',label:'CPA'}},
      {{key:'conversion_value',label:'Conv. value'}},
    ];
    const kwTitle=t=>t?t.charAt(0)+t.slice(1).toLowerCase():'';
    function kwMatchBadge(mt) {{
      const t=(mt||'').toUpperCase();
      if (!t) return '<span class="kw-sub">—</span>';
      const cls=t==='EXACT'?'badge-exact':t==='PHRASE'?'badge-phrase':'badge-broad';
      return `<span class="badge-match ${{cls}}">${{esc(kwTitle(t))}}</span>`;
    }}
    // Values without a defined metric (e.g. CPA with no conversions) return null
    // so the sort comparator can always sink them to the bottom.
    function kwSortVal(r,key) {{
      if (key==='keyword_text') return (r.keyword_text||'').toLowerCase();
      if (key==='match_type')   return (r.match_type||'').toLowerCase();
      if (key==='cvr')  return num(r.clicks) ? num(r.conversions)/num(r.clicks) : null;
      if (key==='cpa')  return num(r.conversions) ? num(r.spend)/num(r.conversions) : null;
      return num(r[key]);
    }}
    function kwFiltered() {{
      let rows=kwAllRows.slice();
      if (kwMatchFilter.size) rows=rows.filter(r=>kwMatchFilter.has((r.match_type||'').toUpperCase()));
      const q=kwSearch.trim().toLowerCase();
      if (q) rows=rows.filter(r=>((r.keyword_text||'')+' '+(r.ad_group_name||'')+' '+(r.campaign_name||'')).toLowerCase().includes(q));
      const {{key,dir}}=kwSort, mul=dir==='asc'?1:-1;
      rows.sort((a,b)=>{{
        const x=kwSortVal(a,key), y=kwSortVal(b,key);
        const xb=(x==null), yb=(y==null);
        if (xb&&yb) return 0; if (xb) return 1; if (yb) return -1;
        if (typeof x==='string') return mul*x.localeCompare(y,undefined,{{numeric:true}});
        return mul*(x-y);
      }});
      return rows;
    }}
    function renderKeywordInsight() {{
      const el=document.getElementById('keywordInsight');
      if (!el) return;
      const items=[];
      const bySpend=kwAllRows.slice().sort((a,b)=>num(b.spend)-num(a.spend));
      const total=bySpend.reduce((s,r)=>s+num(r.spend),0);
      const topN=Math.min(3, bySpend.length);
      if (total>0 && topN) {{
        const topSpend=bySpend.slice(0,topN).reduce((s,r)=>s+num(r.spend),0);
        const pct=Math.round(topSpend/total*100);
        const info='<svg class="kw-ins-icon" viewBox="0 0 24 24" fill="none" stroke="#1d6fd0" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 11v5" stroke-linecap="round"/><circle cx="12" cy="7.6" r="1.1" fill="#1d6fd0" stroke="none"/></svg>';
        items.push(`<span class="kw-ins">${{info}}<span><strong>${{pct}}% of spend</strong> came from the top ${{topN}} keyword${{topN===1?'':'s'}}.</span></span>`);
      }}
      const wasteful=kwAllRows.filter(r=>num(r.spend)>100 && !num(r.conversions)).length;
      if (wasteful) {{
        const warn='<svg class="kw-ins-icon" viewBox="0 0 24 24" fill="none" stroke="#c98a00" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 1.8 20.5h20.4z"/><path d="M12 10v4"/><circle cx="12" cy="17.4" r="0.6" fill="#c98a00" stroke="none"/></svg>';
        items.push(`<span class="kw-ins kw-ins-warn">${{warn}}<span><strong>${{wasteful}} keyword${{wasteful===1?'':'s'}}</strong> spent over $100 without a conversion.</span></span>`);
      }}
      el.innerHTML=items.join('');
      el.hidden=!items.length;
    }}
    function buildKeywordControls() {{
      const host=document.getElementById('keywordMatchChips');
      if (!host) return;
      const order=['EXACT','PHRASE','BROAD'];
      const types=[...new Set(kwAllRows.map(r=>(r.match_type||'').toUpperCase()).filter(Boolean))]
        .sort((a,b)=>(order.indexOf(a)<0?9:order.indexOf(a))-(order.indexOf(b)<0?9:order.indexOf(b)));
      // A single match type isn't worth a filter row.
      host.innerHTML = types.length<2 ? '' : ['All',...types].map(t=>
        `<button type="button" class="chip" data-match="${{esc(t)}}">${{t==='All'?'All':esc(kwTitle(t))}}</button>`).join('');
      syncKeywordChips();
    }}
    function syncKeywordChips() {{
      document.querySelectorAll('#keywordMatchChips .chip').forEach(b=>{{
        const m=b.dataset.match;
        b.classList.toggle('active', m==='All' ? !kwMatchFilter.size : kwMatchFilter.has(m));
      }});
    }}
    function renderKeywordTable() {{
      const rows=kwFiltered();
      const total=kwAllRows.length;
      const el=document.getElementById('keywordTable');
      const arrow=k=>kwSort.key===k?(kwSort.dir==='asc'?' ▲':' ▼'):'';
      const head=`<thead><tr>${{KW_COLS.map(c=>`<th class="expl-sort${{c.left?' left':''}}${{kwSort.key===c.key?' active':''}}" data-key="${{c.key}}">${{esc(c.label)}}${{arrow(c.key)}}</th>`).join('')}}</tr></thead>`;
      if (!rows.length) {{
        el.innerHTML=head+`<tbody><tr><td class="empty" colspan="${{KW_COLS.length}}">No keywords match these filters.</td></tr></tbody>`;
        setStatus('keywordStatus', total?`0 of ${{total}} keyword(s)`:'No keyword data');
        document.getElementById('keywordPager').innerHTML='';
        return;
      }}
      // Bars scale to the whole filtered set (not just the page) so they stay
      // comparable as you move between pages.
      const maxSpend=Math.max(...rows.map(r=>num(r.spend)), 0);
      const maxCpa=Math.max(...rows.map(r=>num(r.conversions)?num(r.spend)/num(r.conversions):0), 0);
      const totalPages=Math.max(1, Math.ceil(rows.length/KW_PER_PAGE));
      if (kwPageNum>totalPages) kwPageNum=totalPages;
      const startIdx=(kwPageNum-1)*KW_PER_PAGE;
      const pageRows=rows.slice(startIdx, startIdx+KW_PER_PAGE);
      const body=pageRows.map(r=>{{
        const clk=num(r.clicks), conv=num(r.conversions), spend=num(r.spend);
        const spendCell=`<div class="cell-bar"><span class="cell-bar-val">${{money(spend)}}</span>${{pctBar(maxSpend?spend/maxSpend*100:0)}}</div>`;
        const ctr=r.ctr==null?'—':num(r.ctr).toFixed(2)+'%';
        const cvrCell=clk?`<span class="${{conv?'num-good':'num-bad'}}">${{(conv/clk*100).toFixed(2)}}%</span>`:'—';
        let cpaCell;
        if (conv) {{
          const cpa=spend/conv;
          cpaCell=`<div class="cell-bar"><span class="cell-bar-val">${{money(cpa)}}</span>${{pctBar(maxCpa?cpa/maxCpa*100:0)}}</div>`;
        }} else {{
          const flag=spend>0?`<span class="cell-flag" title="Spent without a conversion">&#9888;</span>`:'';
          cpaCell=`<span class="cell-bar-val">—</span>${{flag}}`;
        }}
        return `<tr>`+
          `<td class="left"><div class="kw-name">${{esc(r.keyword_text||'—')}}</div>${{r.ad_group_name?`<div class="kw-sub">${{esc(r.ad_group_name)}}</div>`:''}}</td>`+
          `<td class="left">${{kwMatchBadge(r.match_type)}}</td>`+
          `<td>${{spendCell}}</td>`+
          `<td>${{count(r.impressions)}}</td>`+
          `<td>${{count(clk)}}</td>`+
          `<td>${{ctr}}</td>`+
          `<td>${{count(conv)}}</td>`+
          `<td>${{cvrCell}}</td>`+
          `<td>${{cpaCell}}</td>`+
          `<td>${{r.conversion_value==null?'—':money(r.conversion_value)}}</td>`+
        `</tr>`;
      }}).join('');
      el.innerHTML=head+`<tbody>${{body}}</tbody>`;
      const filtered=rows.length!==total;
      const suffix=filtered?` (of ${{total}})`:'';
      const status=totalPages>1
        ? `${{startIdx+1}}–${{startIdx+pageRows.length}} of ${{rows.length}} keyword(s)${{suffix}}`
        : `${{rows.length}} keyword(s)${{suffix}}`;
      setStatus('keywordStatus', status);
      renderKeywordPager(totalPages);
    }}
    function renderKeywordPager(totalPages) {{
      const pager=document.getElementById('keywordPager');
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" id="kwPrev"${{kwPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{kwPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" id="kwNext"${{kwPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      const prev=document.getElementById('kwPrev'), next=document.getElementById('kwNext');
      if (prev) prev.onclick=()=>{{ if(kwPageNum>1){{ kwPageNum--; renderKeywordTable(); }} }};
      if (next) next.onclick=()=>{{ if(kwPageNum<totalPages){{ kwPageNum++; renderKeywordTable(); }} }};
    }}
    function renderKeywords() {{
      renderKeywordInsight();
      renderKeywordTable();
    }}
    // ---- LinkedIn audience (member demographics) ----
    // These rows are per-window totals, NOT a daily series: LinkedIn's MEMBER_*
    // pivots carry no date dimension and withhold categories below a minimum
    // event count, so they can be neither summed across days nor re-cut to the
    // date range selected above. The endpoint serves one whole synced window and
    // names it; the panel shows that name and spells out the caveat rather than
    // letting the numbers pass for range-scoped ones.
    const LIDEMO_TABS=[
      ['company','Company'],['job_title','Job title'],['job_function','Job function'],
      ['seniority','Seniority'],['industry','Industry'],['company_size','Company size'],
    ];
    const LIDEMO_WINDOW_LABELS={{'LAST_30_DAYS':'Last 30 days','LAST_90_DAYS':'Last 90 days'}};
    let lidemoData=null, lidemoDim='';
    function lidemoWindowLabel(w) {{
      return LIDEMO_WINDOW_LABELS[w]||String(w||'').replace(/_/g,' ').toLowerCase();
    }}
    function lidemoRows(dim) {{
      return ((lidemoData&&lidemoData.by_dimension)||{{}})[dim]||[];
    }}
    function lidemoLiveDims() {{
      return LIDEMO_TABS.filter(([k])=>lidemoRows(k).length);
    }}
    function renderLidemoTabs() {{
      const host=document.getElementById('lidemoTabs');
      if (!host) return;
      const dims=lidemoLiveDims();
      host.innerHTML=dims.map(([k,label])=>
        `<button type="button" class="pnl-tab" role="tab" data-lidim="${{k}}" aria-selected="${{k===lidemoDim?'true':'false'}}">${{esc(label)}}</button>`
      ).join('');
      host.classList.toggle('one-tab',dims.length===1);
    }}
    function renderLidemo() {{
      const rows=lidemoRows(lidemoDim);
      const el=document.getElementById('lidemoTable');
      if (!el) return;
      if (!rows.length) {{ el.innerHTML=''; return; }}
      // Share of reach is relative to the top category in this dimension, so the
      // bars compare like with like even though the dimensions don't partition
      // the same total (a member with no listed job title lands in no bucket).
      const top=rows.reduce((mx,r)=>Math.max(mx,num(r.impressions)),0);
      // spend/conversions come back null when LinkedIn refused that projection
      // for the pivot — show "—", never a measured-looking 0.
      const moneyCell=v=>(v===null||v===undefined)?'—':money(v);
      const head='<thead><tr><th class="left">Category</th><th>Impressions</th>'
        +'<th>Share of reach</th><th>Clicks</th><th>CTR</th><th>Spend</th></tr></thead>';
      const body=rows.map(r=>{{
        const imp=num(r.impressions);
        const share=top?(imp/top*100):0;
        const ctrVal=(r.ctr===null||r.ctr===undefined)?(imp?num(r.clicks)/imp:0):num(r.ctr);
        const label=r.category||r.category_urn||'—';
        return `<tr><td class="left lid-cat" title="${{esc(label)}}">${{esc(label)}}</td>`
          +`<td>${{count(imp)}}</td>`
          +`<td class="lid-share"><span class="lid-bar"><span style="width:${{share.toFixed(1)}}%"></span></span></td>`
          +`<td>${{count(r.clicks)}}</td><td>${{pct(ctrVal*100)}}</td><td>${{moneyCell(r.spend)}}</td></tr>`;
      }}).join('');
      el.innerHTML=head+`<tbody>${{body}}</tbody>`;
    }}
    async function loadLinkedinDemographics() {{
      const sec=document.getElementById('sec-lidemo');
      if (!sec) return;
      // Toggle the editable-panel wrapper too, so a client with no demographics
      // doesn't get an empty panel (and admins no edit bar for one).
      const unit=sec.closest('.ov-unit')||sec;
      setStatus('lidemoStatus','Loading…');
      const d=await getJson(withDates(LINKEDIN_DEMOGRAPHICS_API)).catch(()=>null);
      lidemoData=d;
      const dims=lidemoLiveDims();
      if (!dims.length) {{
        sec.style.display='none'; unit.style.display='none';
        setStatus('lidemoStatus','');
        return;
      }}
      sec.style.display=''; unit.style.display='';
      if (!dims.some(([k])=>k===lidemoDim)) lidemoDim=dims[0][0];
      const wLabel=lidemoWindowLabel(d.window);
      const span=(d.window_start&&d.window_end)?`${{d.window_start}} to ${{d.window_end}}`:'';
      const badge=document.getElementById('lidemoWindow');
      badge.textContent=wLabel;
      badge.title=span;
      document.getElementById('lidemoNote').textContent=
        `LinkedIn reports audience demographics over a fixed window${{span?` (${{span}})`:''}}, `
        +'not the date range selected above. Categories with very few events are '
        +'withheld to protect member privacy, so these figures are approximate and '
        +'do not add up to campaign totals.';
      setStatus('lidemoStatus','');
      renderLidemoTabs();
      renderLidemo();
    }}

    // ---- Google Ads demographics (age / gender segments) ----
    // Unlike the LinkedIn panel this one honours the date picker: Google reports
    // demographics per day, so the rows are re-cut to whatever range is selected.
    const GDEMO_TABS=[['age_range','Age'],['gender','Gender']];
    let gdemoData=null, gdemoDim='';
    function gdemoDimData(dim) {{
      return ((gdemoData&&gdemoData.by_dimension)||{{}})[dim]||null;
    }}
    function gdemoRows(dim) {{
      const d=gdemoDimData(dim);
      return (d&&d.segments)||[];
    }}
    function gdemoLiveDims() {{
      return GDEMO_TABS.filter(([k])=>gdemoRows(k).length);
    }}
    function renderGdemoTabs() {{
      const host=document.getElementById('gdemoTabs');
      if (!host) return;
      const dims=gdemoLiveDims();
      host.innerHTML=dims.map(([k,label])=>
        `<button type="button" class="pnl-tab" role="tab" data-gdim="${{k}}" aria-selected="${{k===gdemoDim?'true':'false'}}">${{esc(label)}}</button>`
      ).join('');
      host.classList.toggle('one-tab',dims.length===1);
    }}
    function renderGdemoRecs() {{
      const host=document.getElementById('gdemoRecs');
      if (!host) return;
      const recs=gdemoRows(gdemoDim).map(r=>r.recommendation).filter(Boolean);
      if (!recs.length) {{ host.hidden=true; host.innerHTML=''; return; }}
      host.hidden=false;
      host.innerHTML=recs.map(r=>
        `<div class="gd-rec gd-rec-${{esc(r.severity||'medium')}}"><div class="gd-rec-body">`
        +`<div class="gd-rec-head">${{esc(r.headline||'')}}</div>`
        +`<div class="gd-rec-detail">${{esc(r.detail||'')}}</div>`
        +`</div></div>`
      ).join('');
    }}
    function renderGdemo() {{
      const el=document.getElementById('gdemoTable');
      if (!el) return;
      const rows=gdemoRows(gdemoDim);
      if (!rows.length) {{ el.innerHTML=''; return; }}
      // Spend share and conversion share are both percentages of this
      // dimension's total, so they share one scale — that is the whole point of
      // stacking them, and rescaling either one separately would invent a
      // difference. The common max only decides how much width the pair uses.
      const scale=rows.reduce((mx,r)=>Math.max(mx,num(r.spend_share),num(r.conversion_share)),0)||100;
      const head='<thead><tr><th class="left">Segment</th><th>Spend</th>'
        +'<th title="Top bar: share of this dimension\\'s spend. Bottom bar: share of its conversions.">Spend vs conversions</th>'
        +'<th>Clicks</th><th>Conversions</th><th>Cost / conv.</th></tr></thead>';
      const body=rows.map(r=>{{
        const spendW=Math.min(100,num(r.spend_share)/scale*100);
        const convW=Math.min(100,num(r.conversion_share)/scale*100);
        const label=r.segment_label||r.segment_value||'—';
        const rec=r.recommendation;
        let flags='';
        if (r.excluded_everywhere) {{
          flags+=`<span class="gd-flag gd-flag-excluded" title="Already excluded in every ad group it appears in">Excluded</span>`;
        }} else if (r.excluded_ad_groups) {{
          flags+=`<span class="gd-flag gd-flag-excluded" title="Excluded in ${{count(r.excluded_ad_groups)}} of ${{count(r.ad_groups)}} ad groups">Partly excluded</span>`;
        }}
        if (rec&&rec.kind==='no_conversions') flags+=`<span class="gd-flag gd-flag-waste">No conv.</span>`;
        else if (rec&&rec.kind==='high_cpa') flags+=`<span class="gd-flag gd-flag-cpa">High CPA</span>`;
        const cpa=(r.cpa===null||r.cpa===undefined)?'—':money(r.cpa);
        const sharesTitle=`${{pct(num(r.spend_share))}} of spend, ${{pct(num(r.conversion_share))}} of conversions`;
        return `<tr><td class="left gd-seg" title="${{esc(label)}}">${{esc(label)}}${{flags}}</td>`
          +`<td>${{money(r.spend)}}</td>`
          +`<td class="gd-shares" title="${{esc(sharesTitle)}}"><span class="gd-split">`
          +`<span class="gd-split-row"><span style="width:${{spendW.toFixed(1)}}%"></span></span>`
          +`<span class="gd-split-row"><span style="width:${{convW.toFixed(1)}}%"></span></span>`
          +`</span></td>`
          +`<td>${{count(r.clicks)}}</td><td>${{count(r.conversions)}}</td><td>${{cpa}}</td></tr>`;
      }}).join('');
      el.innerHTML=head+`<tbody>${{body}}</tbody>`;

      const dim=gdemoDimData(gdemoDim)||{{}};
      const badge=document.getElementById('gdemoCoverage');
      const unknown=dim.undetermined_spend_share;
      if (badge) {{
        if (unknown===null||unknown===undefined) {{ badge.hidden=true; }}
        else {{
          badge.hidden=false;
          badge.textContent=`${{pct(unknown)}} unattributed`;
          badge.title='Share of this dimension\\'s spend Google could not assign to a segment.';
        }}
      }}
      const note=document.getElementById('gdemoNote');
      if (note) {{
        note.textContent=
          'Google only reports demographics for traffic it can classify, and '
          +'Performance Max campaigns report none at all — so these totals are a '
          +'subset of account spend and will not reconcile with the campaign '
          +'numbers above. Unknown is kept in the table because leaving it out '
          +'would make the other shares look like the whole account.';
      }}
      renderGdemoRecs();
    }}
    async function loadGoogleDemographics() {{
      const sec=document.getElementById('sec-gdemo');
      if (!sec) return;
      // Toggle the editable-panel wrapper too, so a client with no demographic
      // data doesn't get an empty panel (and admins no edit bar for one).
      const unit=sec.closest('.ov-unit')||sec;
      setStatus('gdemoStatus','Loading…');
      const d=await getJson(withDates(GOOGLE_ADS_DEMOGRAPHICS_API)).catch(()=>null);
      gdemoData=d;
      const dims=gdemoLiveDims();
      if (!dims.length) {{
        sec.style.display='none'; unit.style.display='none';
        setStatus('gdemoStatus','');
        return;
      }}
      sec.style.display=''; unit.style.display='';
      if (!dims.some(([k])=>k===gdemoDim)) gdemoDim=dims[0][0];
      setStatus('gdemoStatus','');
      renderGdemoTabs();
      renderGdemo();
    }}

    async function loadExplorer() {{
      setStatus('explorerStatus','Loading…');
      // Demographics are independent of the explorer tree (different grain,
      // different window), so they load alongside it rather than blocking it.
      const lidemoDone=loadLinkedinDemographics();
      const gdemoDone=loadGoogleDemographics();
      document.getElementById('explorerSummaryCards').innerHTML = skelCards(5);
      document.getElementById('explorerTable').innerHTML = skelTable(6,8);
      // The Compare picker's window (previous period/year) — fetched only when
      // set, so a page load with no comparison configured yet skips these.
      const cmpOn = !!compareStart;
      const [g,l,m,ms,kw,ver,gver,lver,gPrev,lPrev,mPrev,msPrev]=await Promise.all([
        getJson(withDates(EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(LINKEDIN_EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(META_EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(MICROSOFT_EXPLORER_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(GOOGLE_ADS_KEYWORDS_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(META_VERIFIED_API)).catch(()=>({{by_ad_id:{{}}}})),
        getJson(withDates(GOOGLE_VERIFIED_API)).catch(()=>({{by_campaign_id:{{}}}})),
        getJson(withDates(LINKEDIN_VERIFIED_API)).catch(()=>({{by_group_name:{{}}}})),
        cmpOn ? getJson(withDatesRange(EXPLORER_API, compareStart, compareEnd)).catch(()=>({{rows:[]}})) : Promise.resolve({{rows:[]}}),
        cmpOn ? getJson(withDatesRange(LINKEDIN_EXPLORER_API, compareStart, compareEnd)).catch(()=>({{rows:[]}})) : Promise.resolve({{rows:[]}}),
        cmpOn ? getJson(withDatesRange(META_EXPLORER_API, compareStart, compareEnd)).catch(()=>({{rows:[]}})) : Promise.resolve({{rows:[]}}),
        cmpOn ? getJson(withDatesRange(MICROSOFT_EXPLORER_API, compareStart, compareEnd)).catch(()=>({{rows:[]}})) : Promise.resolve({{rows:[]}}),
      ]);
      verifiedByAdId=(ver&&ver.by_ad_id)?ver.by_ad_id:{{}};
      verifiedByAdIdEvent=(ver&&ver.by_ad_id_event)?ver.by_ad_id_event:{{}};
      verifiedByGoogleCampaignId=(gver&&gver.by_campaign_id)?gver.by_campaign_id:{{}};
      verifiedByGoogleCampaignIdEvent=(gver&&gver.by_campaign_id_event)?gver.by_campaign_id_event:{{}};
      verifiedByLinkedinGroup=(lver&&lver.by_group_name)?lver.by_group_name:{{}};
      verifiedByLinkedinGroupEvent=(lver&&lver.by_group_name_event)?lver.by_group_name_event:{{}};
      const metaEvents=(ver&&ver.events)?ver.events:[];
      const googleEvents=(gver&&gver.events)?gver.events:[];
      const linkedinEvents=(lver&&lver.events)?lver.events:[];
      keyEventList=[...new Set([...metaEvents,...googleEvents,...linkedinEvents])];
      if (selectedKeyEvent!=='__all__' && keyEventList.indexOf(selectedKeyEvent)<0) selectedKeyEvent='__all__';
      explorerRows=normalizeExplorerRows(g,l,m,ms);
      explorerPrevRows=cmpOn ? normalizeExplorerRows(gPrev,lPrev,mPrev,msPrev) : [];
      applyVerifiedSelection();
      renderExplorer();
      // Keyword table: only show the section when this client actually has
      // Google Ads search-keyword data (empty for LinkedIn/Meta-only clients).
      kwAllRows=(kw&&kw.rows)||[];
      const kwSec=document.getElementById('sec-keywords');
      // Toggle the editable-panel wrapper when there is one, so a client with no
      // keyword data doesn't get an empty panel (and no edit bar for admins).
      const kwUnit=kwSec.closest('.ov-unit')||kwSec;
      if (kwAllRows.length) {{
        kwSec.style.display='';
        kwUnit.style.display='';
        kwPageNum=1;
        buildKeywordControls();
        renderKeywords();
      }} else {{
        kwSec.style.display='none';
        kwUnit.style.display='none';
      }}
      await lidemoDone;
      await gdemoDone;
    }}

    // ---- GA4: Top pages ----
    let pagesTopRows=[], pagesSourceRows=[], pagesSearchQuery='';
    let pagesEventMap={{}};   // page_path -> {{ event_name: count }}, from TOP_PAGES_KEY_EVENTS_API
    // Recompute key_events per row from the global key-event selection, same
    // fallback rule as Traffic/User acquisition: only override once our own
    // event map actually has data, otherwise keep the base report's real value.
    function applyPageEvents(rows) {{
      if (!Object.keys(pagesEventMap).length) return rows;
      return rows.map(r=>({{...r, key_events:keSum(pagesEventMap, r.page_path)}}));
    }}
    const PAGES_PER_PAGE=10; let pagesPageNum=1;
    // Cap Top pages to the top N by views — the long tail past this is almost
    // always checkout steps and one-off paths that just add noise.
    const PAGES_TOP_LIMIT=50;
    // Per-page source / AI-referral rows (vw_page_path_source_daily), used by
    // the AI Traffic tab. Fetched once per date range and memoized so switching
    // between date ranges is instant.
    let pagesSourceLoadedFor=null;
    async function ensurePagesSources() {{
      const k=currentStart+'|'+currentEnd;
      if (pagesSourceLoadedFor===k) return pagesSourceRows;
      const src=await getJson(withDates(PAGES_SOURCES_API)).catch(()=>({{rows:[]}}));
      pagesSourceRows=src.rows||[]; pagesSourceLoadedFor=k;
      return pagesSourceRows;
    }}
    // Click-to-sort for the Top pages and Landing pages tables. Follows the
    // same house pattern as the GSC tables above (th.gsc-sort + ▴/▾ arrow,
    // delegated click on the pane). Both default to Key events, highest first.
    const PAGES_SORT_COLS=[
      {{key:'page_path',label:'Page',left:true}},
      {{key:'page_views',label:'Views',num:true}},
      {{key:'users',label:'Users',num:true}},
      {{key:'key_events',label:'Key events',num:true}},
      {{key:'avg_engt',label:'Avg engt',num:true,get:p=>p.users?p.engagement_seconds/p.users:0}},
    ];
    // Both page tables now run the full width of the card, so the path column has
    // room for a real path instead of the 24-char default. Anything past this
    // still ellipsizes in the cell, and the full path stays in the tooltip.
    const PAGE_TABLE_PATH_MAX=48;
    let pagesSort={{key:'key_events',dir:'desc'}};
    const LANDING_SORT_COLS=[
      {{key:'page_path',label:'Landing page',left:true}},
      {{key:'sessions',label:'Sessions',num:true}},
      {{key:'users',label:'Users',num:true}},
      {{key:'new_users',label:'New users',num:true}},
      {{key:'key_events',label:'Key events',num:true}},
      {{key:'key_event_rate',label:'KE rate',num:true}},
      {{key:'avg_engagement_seconds',label:'Avg engt',num:true}},
    ];
    let landingSort={{key:'key_events',dir:'desc'}};
    function analyticsSortHead(cols,sort,tableName) {{
      const arrow=k=>sort.key===k?(sort.dir==='asc'?' \\u25B4':' \\u25BE'):'';
      return `<thead><tr>`+cols.map(c=>`<th class="gsc-sort${{c.left?' left':''}}${{sort.key===c.key?' active':''}}" data-table="${{tableName}}" data-key="${{esc(c.key)}}" aria-sort="${{sort.key===c.key?(sort.dir==='asc'?'ascending':'descending'):'none'}}">${{esc(c.label)}}${{arrow(c.key)}}</th>`).join('')+`</tr></thead>`;
    }}
    function analyticsSort(rows,cols,sort) {{
      const col=cols.find(c=>c.key===sort.key)||cols[0];
      const get=col.get||(r=>r[col.key]);
      const mult=sort.dir==='asc'?1:-1;
      return rows.slice().sort((a,b)=>{{
        if (col.num) return (num(get(a))-num(get(b)))*mult;
        return String(get(a)??'').localeCompare(String(get(b)??''))*mult;
      }});
    }}
    (function(){{
      const pane=document.getElementById('pane-analytics');
      if (!pane) return;
      pane.addEventListener('click', ev=>{{
        const th=ev.target.closest('th.gsc-sort'); if (!th) return;
        const which=th.dataset.table;
        const sort=which==='pages'?pagesSort:landingSort;
        const cols=which==='pages'?PAGES_SORT_COLS:LANDING_SORT_COLS;
        const col=cols.find(c=>c.key===th.dataset.key); if (!col) return;
        if (sort.key===col.key) sort.dir=sort.dir==='asc'?'desc':'asc';
        else {{ sort.key=col.key; sort.dir=col.num?'desc':'asc'; }}
        if (which==='pages') {{ pagesPageNum=1; renderPages(); }}
        else {{ landingPageNum=1; renderLanding(); }}
      }});
    }})();
    function renderPages() {{
      let base=applyPageEvents(pagesTopRows);
      // Rows are already sorted by views desc (server ORDER BY), so slicing
      // keeps the top N. Search then filters within that top set.
      base=base.slice(0, PAGES_TOP_LIMIT);
      if (pagesSearchQuery) {{ const q=pagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }}
      base=analyticsSort(base,PAGES_SORT_COLS,pagesSort);
      const el=document.getElementById('pagesTable');
      if (!base.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No pages match${{pagesSearchQuery?' "'+esc(pagesSearchQuery)+'"':''}}.</td></tr></tbody>`; setStatus('pagesStatus','No results'); document.getElementById('pagesPager').innerHTML=''; return; }}
      const totalPages=Math.max(1,Math.ceil(base.length/PAGES_PER_PAGE));
      if (pagesPageNum>totalPages) pagesPageNum=totalPages;
      const startIdx=(pagesPageNum-1)*PAGES_PER_PAGE;
      const pageRows=base.slice(startIdx,startIdx+PAGES_PER_PAGE);
      el.innerHTML=analyticsSortHead(PAGES_SORT_COLS,pagesSort,'pages')+
        `<tbody>${{pageRows.map(p=>{{const engt=p.users?p.engagement_seconds/p.users:0;return`<tr><td class="left"><span class="page-path" title="${{esc(p.page_path)}}">${{esc(truncPath(p.page_path,PAGE_TABLE_PATH_MAX))}}</span></td><td>${{count(p.page_views)}}</td><td>${{count(p.users)}}</td><td>${{count(p.key_events)}}</td><td>${{fmtDuration(engt)}}</td></tr>`;}}). join('')}}</tbody>`;
      enableColResize('pagesTable');
      const tag=pagesSearchQuery?' (filtered)':'';
      setStatus('pagesStatus', `${{startIdx+1}}–${{startIdx+pageRows.length}} of ${{base.length}}${{tag}}`);
      renderPagesPager(totalPages);
    }}
    function renderPagesPager(totalPages) {{
      const pager=document.getElementById('pagesPager');
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" id="pagesPrev"${{pagesPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{pagesPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" id="pagesNext"${{pagesPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      const prev=document.getElementById('pagesPrev'), next=document.getElementById('pagesNext');
      if (prev) prev.onclick=()=>{{if(pagesPageNum>1){{pagesPageNum--;renderPages();}}}};
      if (next) next.onclick=()=>{{if(pagesPageNum<totalPages){{pagesPageNum++;renderPages();}}}};
    }}
    async function loadPages() {{
      setStatus('pagesStatus','Loading…');
      document.getElementById('pagesTable').innerHTML = skelTable(5,8);
      const [top,ev]=await Promise.all([
        getJson(withDates(PAGES_TOP_API)).catch(()=>({{rows:[]}})),
        getJson(withDates(TOP_PAGES_KEY_EVENTS_API)).catch(()=>({{rows:[],events:[]}})),
      ]);
      pagesTopRows=top.rows||[]; pagesPageNum=1;
      pagesEventMap={{}};
      for (const r of (ev.rows||[])) {{
        (pagesEventMap[r.page_path]=pagesEventMap[r.page_path]||{{}})[r.event_name]=num(r.event_count);
      }}
      mergeEvents(ev.events);
      renderPages();
    }}
    (function(){{
      const inp=document.getElementById('pagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{pagesSearchQuery=inp.value.trim();pagesPageNum=1;renderPages();}},180); }});
    }})();

    // ---- AI Traffic tab (from vw_page_path_source_daily, is_ai_referral) ----
    let aiRows=[], aiPagesSearchQuery='', aiSourceFilter=new Set();
    const AI_PALETTE=['#1d6fd0','#7c3aed','#0a7f3f','#dc2626','#d97706','#0891b2','#be185d','#4b5563'];
    // Stacked-area trend: sessions/day, one band per AI platform. Data comes from
    // the daily endpoint (the range-aggregated pages/sources has no time axis).
    // AI-traffic trend granularity (Daily/Weekly chips), mirroring the sessions
    // trend. Daily rows are re-bucketed to Monday-start weeks client-side: each
    // row's date is remapped to its week start, and renderAiTrend's per-date sum
    // collapses the days into weeks. Only drives the main tab chart (default
    // chartId), not the overview mini-chart.
    let aiTrendGran = 'daily';
    let aiTrendDailyCache = [];
    function aggregateAiWeekly(rows) {{
      return (rows || []).map(function(r) {{
        const dt = new Date(String(r.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        return Object.assign({{}}, r, {{ date: key }});
      }});
    }}
    function renderAiTrendGran() {{
      renderAiTrend(aiTrendGran === 'weekly' ? aggregateAiWeekly(aiTrendDailyCache) : aiTrendDailyCache);
    }}
    document.querySelectorAll('#aiTrendGranChips .chip').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        if (btn.dataset.gran === aiTrendGran) return;
        aiTrendGran = btn.dataset.gran;
        document.querySelectorAll('#aiTrendGranChips .chip').forEach(b => b.classList.toggle('active', b === btn));
        renderAiTrendGran();
      }});
    }});
    function renderAiTrend(daily, chartId, statusId) {{
      chartId=chartId||'aiTrendChart'; statusId=statusId||'aiTrendStatus';
      const rows=daily||[];
      const dates=[...new Set(rows.map(r=>String(r.date)))].sort();
      const pivot=new Map();   // platform -> Map(date -> sessions)
      for (const r of rows) {{
        const pl=r.ai_platform||'Unknown';
        let m=pivot.get(pl); if(!m){{m=new Map();pivot.set(pl,m);}}
        m.set(String(r.date),(m.get(String(r.date))||0)+num(r.sessions));
      }}
      // Biggest platform first so it sits at the bottom of the stack.
      const ordered=[...pivot.keys()].map(pl=>[pl,[...pivot.get(pl).values()].reduce((a,b)=>a+b,0)]).sort((a,b)=>b[1]-a[1]);
      if (!dates.length || !ordered.length) {{ __destroyChart(chartId); setStatus(statusId,'No AI traffic in this range.'); return; }}
      // Stack the data ourselves (scales.y.stacked doesn't stack lines in this
      // Chart.js build): each series plots the running cumulative total and fills
      // down to the series below it. _raw keeps the per-platform value for tooltips.
      const cum=dates.map(()=>0);
      const datasets=ordered.map(([pl],i)=>{{
        const color=AI_PALETTE[i%AI_PALETTE.length], m=pivot.get(pl);
        const raw=dates.map(d=>m.get(d)||0);
        const data=raw.map((v,idx)=>(cum[idx]+=v));
        return {{ label:pl, data, _raw:raw, borderColor:color, backgroundColor:color+'59',
                  fill:i===0?'origin':'-1', borderWidth:2, tension:0.3, pointRadius:0, pointHoverRadius:4 }};
      }});
      __chart(chartId, {{
        type:'line',
        data:{{ labels:dates.map(d=>d.slice(5)), datasets }},
        options:{{
          interaction:{{mode:'index',intersect:false}},
          scales:{{
            x:{{ grid:{{display:false}}, border:{{display:false}}, ticks:{{maxRotation:0,autoSkip:true,maxTicksLimit:8}} }},
            y:{{ beginAtZero:true, grid:{{color:'#f1f4f9'}}, border:{{display:false}}, ticks:{{maxTicksLimit:5}} }},
          }},
          plugins:{{
            legend:{{ display:true, position:'bottom', labels:{{usePointStyle:true, boxWidth:8, padding:12}} }},
            tooltip:{{ callbacks:{{ label:c=>`${{c.dataset.label}}: ${{count(c.dataset._raw[c.dataIndex])}}` }} }},
          }},
        }},
      }});
      setStatus(statusId,'');
    }}
    async function loadAiTraffic() {{
      setStatus('aiTrafficStatus','Loading…');
      setStatus('aiTrendStatus','Loading…');
      document.getElementById('aiSourcesTable').innerHTML=skelTable(5,5);
      document.getElementById('aiPagesTable').innerHTML=skelTable(4,8);
      const [rows, daily]=await Promise.all([
        ensurePagesSources().then(rs=>rs.filter(r=>r.is_ai_referral)),
        getJson(withDates(AI_TRAFFIC_DAILY_API)).then(d=>d.rows||[]).catch(()=>[]),
      ]);
      aiTrendDailyCache = daily;
      renderAiTrendGran();
      const bySrc=new Map();
      for (const r of rows) {{
        const key=r.ai_platform||'Unknown';
        let g=bySrc.get(key); if(!g){{g={{source:key,sessions:0,users:0,page_views:0,engagement_seconds:0}};bySrc.set(key,g);}}
        g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);g.engagement_seconds+=num(r.engagement_seconds);
      }}
      const srcRows=[...bySrc.values()].sort((a,b)=>b.sessions-a.sessions);
      renderTable('aiSourcesTable',[
        {{key:'source',label:'AI source',left:true}},
        {{key:'sessions',label:'Sessions',format:count}},
        {{key:'users',label:'Users',format:count}},
        {{key:'page_views',label:'Page views',format:count}},
        {{key:'engt',label:'Avg engt',format:(_,r)=>fmtDuration(r.users?r.engagement_seconds/r.users:0)}},
      ],srcRows,'No AI-referred traffic in this range.');
      setStatus('aiTrafficStatus', srcRows.length?`${{srcRows.length}} source(s)`:'No AI traffic');
      aiRows=rows;
      buildAiSourceChips();
      renderAiPages();
    }}
    function buildAiSourceChips() {{
      const el=document.getElementById('aiPageSourceChips');
      if (!el) return;
      const sources=[...new Set(aiRows.map(r=>r.ai_platform).filter(Boolean))].sort();
      // Drop any selected source no longer present in this range.
      for (const s of [...aiSourceFilter]) if (!sources.includes(s)) aiSourceFilter.delete(s);
      el.innerHTML=[['__all__','All'],...sources.map(s=>[s,s])].map(([v,l])=>`<button type="button" class="chip" data-key="${{esc(v)}}">${{esc(l)}}</button>`).join('');
      const sync=()=>el.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active', b.dataset.key==='__all__'?aiSourceFilter.size===0:aiSourceFilter.has(b.dataset.key)));
      el.querySelectorAll('.chip').forEach(btn=>btn.addEventListener('click',()=>{{
        const key=btn.dataset.key;
        if(key==='__all__')aiSourceFilter.clear();else if(aiSourceFilter.has(key))aiSourceFilter.delete(key);else aiSourceFilter.add(key);
        sync();renderAiPages();
      }}));
      sync();
    }}
    function renderAiPages() {{
      const src=aiSourceFilter.size?aiRows.filter(r=>aiSourceFilter.has(r.ai_platform)):aiRows;
      const byPage=new Map();
      for (const r of src) {{
        let g=byPage.get(r.page_path); if(!g){{g={{page_path:r.page_path,sessions:0,users:0,page_views:0}};byPage.set(r.page_path,g);}}
        g.sessions+=num(r.sessions);g.users+=num(r.users);g.page_views+=num(r.page_views);
      }}
      let base=[...byPage.values()].sort((a,b)=>b.sessions-a.sessions);
      if (aiPagesSearchQuery) {{ const q=aiPagesSearchQuery.toLowerCase(); base=base.filter(p=>p.page_path.toLowerCase().includes(q)); }}
      base=base.slice(0,PAGES_TOP_LIMIT);
      renderTable('aiPagesTable',[
        {{key:'page_path',label:'Page',left:true}},
        {{key:'sessions',label:'Sessions',format:count}},
        {{key:'users',label:'Users',format:count}},
        {{key:'page_views',label:'Page views',format:count}},
      ],base,(aiPagesSearchQuery||aiSourceFilter.size)?'No pages match.':'No AI-referred pages in this range.');
      const tag=aiSourceFilter.size?` · ${{[...aiSourceFilter].join(', ')}}`:'';
      setStatus('aiPagesStatus', base.length?`${{base.length}} page(s)${{tag}}`:'');
    }}
    (function(){{
      const inp=document.getElementById('aiPagesSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{aiPagesSearchQuery=inp.value.trim();renderAiPages();}},180); }});
    }})();

    // ---- Campaign Explorer: paid-source module (paid_* from source_platform) ----
    // ---- GA4: Traffic acquisition ----
    // Sessions/day for the current period (solid blue, filled) with the prior
    // period overlaid (dashed grey), aligned day-for-day by index so the shapes
    // line up regardless of the actual calendar dates.
    // Sessions-over-time granularity (Daily/Weekly chips). Daily rows are fetched
    // once and re-bucketed to weeks client-side, so switching is instant and needs
    // no extra query. Weeks start Monday; the week's label is its start date.
    let sessionsGran = 'daily';
    let sessionsTrendCache = {{ cur: [], prev: [] }};
    function aggregateWeekly(daily) {{
      if (!daily || !daily.length) return [];
      const out = []; let cur = null;
      for (const d of daily) {{
        const dt = new Date(String(d.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        if (!cur || cur.date !== key) {{ cur = {{ date: key, sessions: 0 }}; out.push(cur); }}
        cur.sessions += num(d.sessions);
      }}
      return out;
    }}
    function renderSessionsTrend() {{
      const c = sessionsTrendCache;
      if (sessionsGran === 'weekly') drawSessionsTrend(aggregateWeekly(c.cur), aggregateWeekly(c.prev));
      else drawSessionsTrend(c.cur, c.prev);
    }}
    // Re-drawn (not just re-pinned) when the timeline changes, so the canvas
    // rules and the DOM pins are rebuilt from one pass.
    registerAnnotatedChart(() => {{ if (sessionsTrendCache && sessionsTrendCache.cur) renderSessionsTrend(); }});
    function drawSessionsTrend(daily, prevDaily) {{
      clearSkelChart('sessionsTrendChart');
      const legend=document.getElementById('sessionsTrendLegend');
      const n=daily.length;
      if (!n) {{ __destroyChart('sessionsTrendChart'); if(legend) legend.innerHTML=''; return; }}
      const vals=daily.map(d=>num(d.sessions));
      const prevVals=(prevDaily||[]).map(d=>num(d.sessions));
      const hasPrev=prevVals.length>0;
      const labels=daily.map(d=>String(d.date).slice(5));
      const series=[];
      // Previous first so the current line renders on top of it.
      if (hasPrev) series.push({{ label:cmpSeriesLabel(), data: prevVals.slice(0,n), color:'#9aa7bd', dashed:true }});
      series.push({{ label:'Current', data: vals, color:'#1d6fd0', fill:true }});
      lineChart('sessionsTrendChart', labels, series, {{
        yFmt: v => count(v),
        tooltip: {{ label: c => `${{c.dataset.label}}: ${{count(c.raw)}} sessions` }},
        // Full ISO dates behind the MM-DD labels, so timeline markers can be
        // placed on the right point (and on the right week when aggregated).
        dates: daily.map(d => String(d.date)),
      }});
      if (legend) {{
        const curTot=vals.reduce((a,b)=>a+b,0), prevTot=prevVals.reduce((a,b)=>a+b,0);
        const delta=(hasPrev&&prevTot)?((curTot-prevTot)/prevTot*100):null;
        const deltaTxt=delta==null?'':` <span class="cmp-delta ${{delta>=0?'up':'down'}}">${{delta>=0?'+':''}}${{delta.toFixed(0)}}%</span>`;
        legend.innerHTML=`<span class="cmp-item"><span class="cmp-swatch cur"></span>Current (${{esc(currentStart.slice(5))}} – ${{esc(currentEnd.slice(5))}}) · ${{count(curTot)}} sessions${{deltaTxt}}</span>`
          + (hasPrev?`<span class="cmp-item"><span class="cmp-swatch prev"></span>${{esc(cmpSeriesLabel())}} (${{esc(compareMode==='prev_year'?compareStart:compareStart.slice(5))}} – ${{esc(compareMode==='prev_year'?compareEnd:compareEnd.slice(5))}}) · ${{count(prevTot)}}</span>`:'');
      }}
    }}
    function renderBarList(containerId, rows, valueKey, labelKey) {{
      const el=document.getElementById(containerId);
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const total=rows.reduce((s,r)=>s+num(r[valueKey]),0);
      el.innerHTML=rows.map(r=>{{const p=total?num(r[valueKey])/total*100:0;return`<div class="bar-row"><div class="bar-label">${{esc(r[labelKey])}}</div>${{pctBar(p)}}<div class="bar-count">${{count(r[valueKey])}}<span class="bar-pct">${{p.toFixed(0)}}%</span></div></div>`;}}).join('');
    }}
    // Categorical palette for stacked/segmented charts (channels, gender, etc.).
    const CHART_PALETTE=['#1d6fd0','#7c3aed','#0a7f3f','#e08a1e','#d6336c','#0d9488','#5661b3','#b4530a','#3b7ddd','#8a4fbe'];
    // Traffic → single 100% bar, one segment per channel; hover shows channel,
    // sessions and share. A legend under the bar lists each channel + %.
    // Single 100% bar, one segment per channel. No legend — hovering a segment
    // shows its channel, sessions and share in a floating tooltip.
    function renderChannelStacked(rows) {{
      const el=document.getElementById('channelBars');
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const ordered=[...rows].sort((a,b)=>num(b.sessions)-num(a.sessions));
      const total=ordered.reduce((s,r)=>s+num(r.sessions),0)||1;
      const seg=ordered.map((r,i)=>{{
        const p=num(r.sessions)/total*100, color=CHART_PALETTE[i%CHART_PALETTE.length];
        return`<div class="stack-seg" style="width:${{p.toFixed(2)}}%;background:${{color}}" data-label="${{esc(r.channel)}}" data-detail="${{count(r.sessions)}} sessions · ${{p.toFixed(1)}}%"></div>`;
      }}).join('');
      el.innerHTML=`<div class="stack-wrap"><div class="stack-bar">${{seg}}</div><div class="stack-tip" hidden></div></div>`;
      const wrap=el.querySelector('.stack-wrap'), tip=el.querySelector('.stack-tip');
      wrap.querySelectorAll('.stack-seg').forEach(s=>{{
        s.addEventListener('mousemove', e=>{{
          tip.textContent=s.dataset.label+' — '+s.dataset.detail;
          tip.hidden=false;
          const rect=wrap.getBoundingClientRect();
          tip.style.left=Math.max(0,Math.min(e.clientX-rect.left, rect.width))+'px';
        }});
        s.addEventListener('mouseleave', ()=>{{ tip.hidden=true; }});
      }});
    }}
    // Bar list capped to the top N with a Prev/Next pager for the tail. Shares
    // are computed against the full total so pagination doesn't skew percentages.
    function renderBarListPaged(containerId, pagerId, rows, valueKey, labelKey, state) {{
      const el=document.getElementById(containerId), pager=document.getElementById(pagerId);
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; if(pager) pager.innerHTML=''; return; }}
      const perPage=10, total=rows.reduce((s,r)=>s+num(r[valueKey]),0)||1;
      const totalPages=Math.max(1,Math.ceil(rows.length/perPage));
      if (state.page>totalPages) state.page=totalPages;
      const startIdx=(state.page-1)*perPage, pageRows=rows.slice(startIdx,startIdx+perPage);
      el.innerHTML=pageRows.map(r=>{{const p=num(r[valueKey])/total*100;return`<div class="bar-row"><div class="bar-label">${{esc(r[labelKey])}}</div>${{pctBar(p)}}<div class="bar-count">${{count(r[valueKey])}}<span class="bar-pct">${{p.toFixed(0)}}%</span></div></div>`;}}).join('');
      if (!pager) return;
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${{state.page<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{state.page}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-dir="next"${{state.page>=totalPages?' disabled':''}}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{{ state.page+=b.dataset.dir==='next'?1:-1; renderBarListPaged(containerId,pagerId,rows,valueKey,labelKey,state); }});
    }}
    // Top sources/medium: 10 per page, rest behind a pager. Rows arrive already
    // sorted by sessions desc, so page 1 is the true top 10.
    const SOURCES_PER_PAGE=10; let sourcesPageNum=1;
    function renderTrafficSources() {{
      const rows=trafficSources||[];
      const totalPages=Math.max(1,Math.ceil(rows.length/SOURCES_PER_PAGE));
      if (sourcesPageNum>totalPages) sourcesPageNum=totalPages;
      const startIdx=(sourcesPageNum-1)*SOURCES_PER_PAGE;
      const pageRows=rows.slice(startIdx,startIdx+SOURCES_PER_PAGE);
      renderTable('sourcesTable',[
        {{key:'source',label:'Source',left:true}},
        {{key:'medium',label:'Medium',left:true}},
        {{key:'sessions',label:'Sessions',format:count}},
        {{key:'engaged_sessions',label:'Engaged',format:count}},
        {{key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'}},
        {{key:'key_events',label:'Key events',format:count}},
      ], pageRows, 'No source data.');
      const pager=document.getElementById('sourcesPager');
      if (!pager) return;
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${{sourcesPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{sourcesPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-dir="next"${{sourcesPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{{ sourcesPageNum+=b.dataset.dir==='next'?1:-1; renderTrafficSources(); }});
    }}
    // Sessions-over-time hero chart (top of the analytics tab). Fetches the
    // current period and the equivalent prior period (compareStart/compareEnd,
    // always set by applyPreset) and overlays them for an at-a-glance trend.
    async function loadSessionsTrend() {{
      setStatus('sessionsTrendStatus','Loading…');
      skelChart('sessionsTrendChart','trend-md-svg');
      try {{
        const [cur, prev] = await Promise.all([
          getJson(withDatesRange(TRAFFIC_ACQ_API, currentStart, currentEnd)),
          compareStart ? getJson(withDatesRange(TRAFFIC_ACQ_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        ]);
        sessionsTrendCache = {{ cur: cur.daily || [], prev: (prev && prev.daily) || [] }};
        renderSessionsTrend();
        setCmpWarn('sessionsCmpWarn', ['google_analytics']);
        setStatus('sessionsTrendStatus','');
      }} catch(err) {{ setStatus('sessionsTrendStatus',err.message||String(err),true); }}
    }}
    // Daily/Weekly chips for the sessions trend — re-render from cache, no refetch.
    document.querySelectorAll('#sessionsGranChips .chip').forEach(btn =>
      btn.addEventListener('click', () => {{
        if (btn.dataset.gran === sessionsGran) return;
        sessionsGran = btn.dataset.gran;
        document.querySelectorAll('#sessionsGranChips .chip').forEach(b => b.classList.toggle('active', b === btn));
        renderSessionsTrend();
      }})
    );
    async function loadTrafficAcq() {{
      setStatus('trafficAcqStatus','Loading…');
      document.getElementById('channelBars').innerHTML = skelBars(5);
      document.getElementById('sourcesTable').innerHTML = skelTable(6,6);
      try {{
        const [payload, ev] = await Promise.all([
          getJson(withDates(TRAFFIC_ACQ_API)),
          getJson(withDates(TRAFFIC_KEY_EVENTS_API)).catch(()=>({{by_source_events:[],events:[]}})),
        ]);
        renderChannelStacked(payload.by_channel||[]);
        sourcesPageNum=1;
        trafficBaseSources = payload.by_source||[];
        trafficSourceEventMap={{}};
        for (const r of (ev.by_source_events||[])) {{
          const k=srcKey(r.source,r.medium);
          (trafficSourceEventMap[k]=trafficSourceEventMap[k]||{{}})[r.event_name]=num(r.event_count);
        }}
        mergeEvents(ev.events);
        applyTrafficSources(); renderTrafficSources();
        setStatus('trafficAcqStatus','');
      }} catch(err) {{ setStatus('trafficAcqStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Device split ----
    async function loadDeviceSplit() {{
      const sec=document.getElementById('sec-audience');
      setStatus('deviceStatus','Loading…');
      document.getElementById('deviceBars').innerHTML = skelBars(3);
      try {{
        const payload=await getJson(withDates(DEVICE_SPLIT_API));
        const rows=payload.rows||[];
        // Audience only holds the device split — when GA4 returns nothing for the
        // range, drop the whole section rather than showing an empty panel.
        if (sec) sec.hidden = !rows.length;
        renderBarList('deviceBars',rows,'users','device');
        setStatus('deviceStatus','');
      }} catch(err) {{ setStatus('deviceStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Global key-event selector (Traffic + Landing pages + User acquisition) ----
    // One control at the top of Website Analytics chooses which GA4 events count as
    // "key events." Default = GA4's own key events; the selection persists per client
    // (admin "Save as default"). Each panel keeps a base row set + a per-row event map
    // so the key-events column recomputes instantly when the selection changes.
    const LANDING_PER_PAGE=10; let landingPageNum=1, landingRows=[], landingSearchQuery='';
    let landingBaseRows=[];            // from LANDING_PAGES_API
    let landingEventMap={{}};            // page_path -> {{ event_name: count }}
    let trafficSources=[], trafficBaseSources=[], trafficSourceEventMap={{}};   // srcKey -> {{ event_name: count }}
    let userAcqSources=[], userAcqBaseSources=[], userAcqSourceEventMap={{}};
    let keyEventCatalog=[];            // [{{event_name,event_count,is_key}}] unioned across panels
    let keyEventCounts={{}};             // event_name -> total count (union)
    let keyEventKeys=new Set();        // GA4-flagged key events (union)
    let selectedKeyEvents=new Set(GA4_KEY_EVENTS_SAVED);
    let keyEventUserTouched=false;     // once true, stop auto-tracking GA4's key set
    let keyEventSearchTerm='';
    const srcKey=(s,m)=>(s||'')+'\\u0000'+(m||'');

    // Fold one panel's event catalog into the shared union + refresh the dropdown.
    function mergeEvents(events) {{
      for (const e of (events||[])) {{
        const n=e.event_name; if (!n) continue;
        keyEventCounts[n]=(keyEventCounts[n]||0)+num(e.event_count);
        if (num(e.key_events)>0) keyEventKeys.add(n);
      }}
      keyEventCatalog=Object.keys(keyEventCounts)
        .map(n=>({{event_name:n, event_count:keyEventCounts[n], is_key:keyEventKeys.has(n)}}))
        .sort((a,b)=>b.event_count-a.event_count);
      // Until the client saved a set or the user edits it, mirror GA4's own key events.
      if (!keyEventUserTouched && !GA4_KEY_EVENTS_SAVED.length && keyEventKeys.size) {{
        selectedKeyEvents=new Set(keyEventKeys);
      }}
      renderKeyEventDropdown();
    }}
    function keSum(map, key) {{
      const evs=map[key]||{{}};
      let s=0; for (const ev of selectedKeyEvents) s+=(evs[ev]||0); return s;
    }}
    function applyLanding() {{
      if (!Object.keys(landingEventMap).length) {{ landingRows=landingBaseRows.slice(); return; }}
      landingRows=landingBaseRows.map(r=>{{
        const ke=keSum(landingEventMap, r.page_path);
        const rate=r.sessions?Math.round(ke/r.sessions*1000)/10:0;
        return {{...r, key_events:ke, key_event_rate:rate}};
      }});
    }}
    function applyTrafficSources() {{
      // Only trust the override map once its own fetch actually returned rows —
      // a missing/unprovisioned events view must fall back to the base report's
      // key_events, not silently zero everything out.
      const hasOverride=Object.keys(trafficSourceEventMap).length>0;
      trafficSources=trafficBaseSources.map(r=>{{
        const ke=hasOverride?keSum(trafficSourceEventMap, srcKey(r.source,r.medium)):num(r.key_events);
        return {{...r, key_events:ke}};
      }});
    }}
    function applyUserAcqSources() {{
      const hasOverride=Object.keys(userAcqSourceEventMap).length>0;
      userAcqSources=userAcqBaseSources.map(r=>{{
        const ke=hasOverride?keSum(userAcqSourceEventMap, srcKey(r.source,r.medium)):num(r.key_events);
        const rate=r.new_users?Math.round(ke/r.new_users*1000)/10:0;
        return {{...r, key_events:ke, key_event_rate:rate}};
      }});
    }}
    // Re-run every loaded panel against the current selection.
    function applyKeyEventsAll() {{
      if (pagesTopRows.length || pagesSourceRows.length) {{ pagesPageNum=1; renderPages(); }}
      if (landingBaseRows.length) {{ applyLanding(); landingPageNum=1; renderLanding(); }}
      if (trafficBaseSources.length) {{ applyTrafficSources(); renderTrafficSources(); }}
      if (userAcqBaseSources.length) {{ applyUserAcqSources(); renderUserAcqSources(); }}
    }}
    function keyEventToggleLabel() {{
      const n=selectedKeyEvents.size;
      if (!n) return 'All key events';
      if (n===1) return [...selectedKeyEvents][0];
      return n+' events selected';
    }}
    function renderKeyEventDropdown() {{
      const label=document.getElementById('keyEventToggleLabel');
      const list=document.getElementById('keyEventList');
      if (label) label.textContent=keyEventToggleLabel();
      if (!list) return;
      if (!keyEventCatalog.length) {{ list.innerHTML='<div class="ke-dd-empty">Per-event data appears after the next GA4 sync.</div>'; return; }}
      const term=keyEventSearchTerm.trim().toLowerCase();
      const matches=keyEventCatalog.filter(e=>!term||e.event_name.toLowerCase().includes(term));
      if (!matches.length) {{ list.innerHTML='<div class="ke-dd-empty">No events match your search.</div>'; return; }}
      list.innerHTML=matches.map(e=>`<label class="ke-dd-option${{selectedKeyEvents.has(e.event_name)?' active':''}}"><input type="checkbox"${{selectedKeyEvents.has(e.event_name)?' checked':''}} data-ev="${{esc(e.event_name)}}"><span class="ke-dd-name">${{esc(e.event_name)}}</span><span class="ke-dd-count">${{count(e.event_count)}}</span></label>`).join('');
      list.querySelectorAll('input[data-ev]').forEach(cb=>cb.addEventListener('change',()=>{{
        const ev=cb.dataset.ev;
        keyEventUserTouched=true;
        if (selectedKeyEvents.has(ev)) selectedKeyEvents.delete(ev); else selectedKeyEvents.add(ev);
        if (label) label.textContent=keyEventToggleLabel();
        const opt=cb.closest('.ke-dd-option'); if (opt) opt.classList.toggle('active', selectedKeyEvents.has(ev));
        applyKeyEventsAll();
      }}));
    }}
    (function initKeyEventDropdown(){{
      const dd=document.getElementById('keyEventDropdown');
      const toggle=document.getElementById('keyEventToggle');
      const panel=document.getElementById('keyEventPanel');
      const search=document.getElementById('keyEventSearch');
      if (!dd||!toggle||!panel) return;
      const open=()=>{{ panel.hidden=false; dd.classList.add('open'); toggle.setAttribute('aria-expanded','true'); if (search) {{ search.value=keyEventSearchTerm; search.focus(); }} }};
      const close=()=>{{ panel.hidden=true; dd.classList.remove('open'); toggle.setAttribute('aria-expanded','false'); }};
      toggle.addEventListener('click', e=>{{ e.stopPropagation(); if (panel.hidden) open(); else close(); }});
      if (search) search.addEventListener('input', ()=>{{ keyEventSearchTerm=search.value; renderKeyEventDropdown(); }});
      document.addEventListener('click', e=>{{ if (!dd.contains(e.target)) close(); }});
      document.addEventListener('keydown', e=>{{ if (e.key==='Escape' && !panel.hidden) {{ close(); toggle.focus(); }} }});
    }})();
    function renderLanding() {{
      let base=landingRows;
      if (landingSearchQuery) {{ const q=landingSearchQuery.toLowerCase(); base=base.filter(r=>String(r.page_path).toLowerCase().includes(q)); }}
      base=analyticsSort(base,LANDING_SORT_COLS,landingSort);
      const el=document.getElementById('landingTable');
      if (!base.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No landing pages match${{landingSearchQuery?' "'+esc(landingSearchQuery)+'"':' this range'}}.</td></tr></tbody>`; setStatus('landingStatus', landingSearchQuery?'No results':''); document.getElementById('landingPager').innerHTML=''; return; }}
      const totalPages=Math.max(1,Math.ceil(base.length/LANDING_PER_PAGE));
      if (landingPageNum>totalPages) landingPageNum=totalPages;
      const startIdx=(landingPageNum-1)*LANDING_PER_PAGE, rows=base.slice(startIdx,startIdx+LANDING_PER_PAGE);
      el.innerHTML=analyticsSortHead(LANDING_SORT_COLS,landingSort,'landing')+
        `<tbody>${{rows.map(r=>`<tr><td class="left"><span class="page-path" title="${{esc(r.page_path)}}">${{esc(truncPath(r.page_path,PAGE_TABLE_PATH_MAX))}}</span></td><td>${{count(r.sessions)}}</td><td>${{count(r.users)}}</td><td>${{count(r.new_users)}}</td><td>${{count(r.key_events)}}</td><td>${{r.key_event_rate!=null?r.key_event_rate+'%':'—'}}</td><td>${{fmtDuration(r.avg_engagement_seconds)}}</td></tr>`).join('')}}</tbody>`;
      enableColResize('landingTable');
      setStatus('landingStatus',`${{startIdx+1}}–${{startIdx+rows.length}} of ${{base.length}}`+(landingSearchQuery?' (filtered)':''));
      const pager=document.getElementById('landingPager');
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" id="landingPrev"${{landingPageNum<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{landingPageNum}} of ${{totalPages}}</span><button type="button" class="pager-btn" id="landingNext"${{landingPageNum>=totalPages?' disabled':''}}>Next ›</button>`;
      const prev=document.getElementById('landingPrev'), next=document.getElementById('landingNext');
      if (prev) prev.onclick=()=>{{if(landingPageNum>1){{landingPageNum--;renderLanding();}}}};
      if (next) next.onclick=()=>{{if(landingPageNum<totalPages){{landingPageNum++;renderLanding();}}}};
    }}
    async function loadLandingPages() {{
      setStatus('landingStatus','Loading…');
      document.getElementById('landingTable').innerHTML = skelTable(7,7);
      try {{
        const [pages, ev] = await Promise.all([
          getJson(withDates(LANDING_PAGES_API)),
          getJson(withDates(LANDING_EVENTS_API)).catch(()=>({{rows:[],events:[]}})),
        ]);
        landingBaseRows = pages.rows||[];
        // Build per-page event map, then fold this panel's events into the shared catalog.
        landingEventMap={{}};
        for (const r of (ev.rows||[])) {{
          (landingEventMap[r.page_path]=landingEventMap[r.page_path]||{{}})[r.event_name]=num(r.event_count);
        }}
        mergeEvents(ev.events);
        applyLanding(); landingPageNum=1; renderLanding();
      }} catch(err) {{ setStatus('landingStatus',err.message||String(err),true); }}
    }}
    (function(){{
      const inp=document.getElementById('landingSearch');
      if (!inp) return;
      let debounce;
      inp.addEventListener('input',()=>{{ clearTimeout(debounce); debounce=setTimeout(()=>{{landingSearchQuery=inp.value.trim();landingPageNum=1;renderLanding();}},180); }});
    }})();
    // ---- GA4: User acquisition ----
    function renderNewVsReturning(byChannel) {{
      const el=document.getElementById('newVsReturning');
      if (!el) return;
      const totalNew=byChannel.reduce((s,r)=>s+num(r.new_users),0);
      const totalActive=byChannel.reduce((s,r)=>s+num(r.active_users),0);
      const totalRet=Math.max(0,totalActive-totalNew);
      const total=totalNew+totalRet||1;
      const newPct=totalNew/total*100, retPct=totalRet/total*100;
      el.innerHTML=`<div class="nr-wrap">
        <div class="nr-stat"><span class="nr-stat-label">New users</span><span class="nr-stat-value">${{count(totalNew)}}</span><span class="nr-stat-pct">${{newPct.toFixed(0)}}%</span></div>
        <div class="nr-stat"><span class="nr-stat-label">Returning</span><span class="nr-stat-value">${{count(totalRet)}}</span><span class="nr-stat-pct">${{retPct.toFixed(0)}}%</span></div>
        <div class="nr-bar-wrap">
          <div class="nr-bar"><div class="nr-bar-new" style="width:${{newPct.toFixed(1)}}%"></div><div class="nr-bar-ret" style="width:${{retPct.toFixed(1)}}%"></div></div>
          <div class="nr-legend">
            <span class="nr-legend-item"><span class="nr-legend-swatch" style="background:#1d6fd0"></span>New</span>
            <span class="nr-legend-item"><span class="nr-legend-swatch" style="background:#c3d9f5"></span>Returning</span>
          </div>
        </div>
      </div>`;
    }}
    const USERACQ_SRC_PER_PAGE=10; let userAcqSrcPage=1;
    const userAcqChanState={{page:1}};
    function renderUserAcqSources() {{
      const rows=userAcqSources||[];
      const totalPages=Math.max(1,Math.ceil(rows.length/USERACQ_SRC_PER_PAGE));
      if (userAcqSrcPage>totalPages) userAcqSrcPage=totalPages;
      const startIdx=(userAcqSrcPage-1)*USERACQ_SRC_PER_PAGE;
      renderTable('userAcqSourceTable',[
        {{key:'source',label:'Source',left:true}},
        {{key:'medium',label:'Medium',left:true}},
        {{key:'new_users',label:'New users',format:count}},
        {{key:'key_events',label:'Key events',format:count}},
        {{key:'key_event_rate',label:'KE rate',format:v=>v!=null?v+'%':'—'}},
      ], rows.slice(startIdx,startIdx+USERACQ_SRC_PER_PAGE), 'No source data.');
      const pager=document.getElementById('userAcqSourcePager');
      if (!pager) return;
      if (totalPages<=1) {{ pager.innerHTML=''; return; }}
      pager.innerHTML=`<button type="button" class="pager-btn" data-dir="prev"${{userAcqSrcPage<=1?' disabled':''}}>‹ Prev</button><span class="pager-info">Page ${{userAcqSrcPage}} of ${{totalPages}}</span><button type="button" class="pager-btn" data-dir="next"${{userAcqSrcPage>=totalPages?' disabled':''}}>Next ›</button>`;
      pager.querySelectorAll('.pager-btn').forEach(b=>b.onclick=()=>{{ userAcqSrcPage+=b.dataset.dir==='next'?1:-1; renderUserAcqSources(); }});
    }}
    async function loadUserAcquisition() {{
      setStatus('userAcqStatus','Loading…');
      document.getElementById('newVsReturning').innerHTML=`<div class="nr-wrap"><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="skel" style="height:42px;width:90px;border-radius:8px"></div><div class="nr-bar-wrap"><div class="skel" style="height:10px;border-radius:5px"></div></div></div>`;
      document.getElementById('userAcqChannelBars').innerHTML = skelBars(5);
      document.getElementById('userAcqSourceTable').innerHTML = skelTable(5,5);
      try {{
        const [payload, ev] = await Promise.all([
          getJson(withDates(USER_ACQ_API)),
          getJson(withDates(USER_ACQ_KEY_EVENTS_API)).catch(()=>({{by_source_events:[],events:[]}})),
        ]);
        renderNewVsReturning(payload.by_channel||[]);
        userAcqChanState.page=1;
        renderBarListPaged('userAcqChannelBars','userAcqChannelPager',payload.by_channel||[],'new_users','channel',userAcqChanState);
        userAcqSrcPage=1;
        userAcqBaseSources = payload.by_source||[];
        userAcqSourceEventMap={{}};
        for (const r of (ev.by_source_events||[])) {{
          const k=srcKey(r.source,r.medium);
          (userAcqSourceEventMap[k]=userAcqSourceEventMap[k]||{{}})[r.event_name]=num(r.event_count);
        }}
        mergeEvents(ev.events);
        applyUserAcqSources(); renderUserAcqSources();
        setStatus('userAcqStatus','');
      }} catch(err) {{ setStatus('userAcqStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Demographics ----
    // Age bracket, with a toggle for the "unknown" bucket (hidden by default so
    // the real brackets read clearly). Rows are cached so toggling is instant.
    let demoAgeRows=[];
    function renderAge() {{
      const showUnknown=!!(document.getElementById('ageUnknownToggle')||{{}}).checked;
      const rows=demoAgeRows.filter(r=>showUnknown||String(r.age_bracket).toLowerCase()!=='unknown');
      renderBarList('ageBars',rows,'users','age_bracket');
    }}
    {{ const t=document.getElementById('ageUnknownToggle'); if (t) t.addEventListener('change', renderAge); }}
    // Gender → 100% split bar + per-gender stat cards.
    const GENDER_COLORS={{male:'#1d6fd0',female:'#d6336c'}};
    function genderColor(g,i) {{ return GENDER_COLORS[String(g).toLowerCase()]||CHART_PALETTE[i%CHART_PALETTE.length]; }}
    function renderGender(rows) {{
      const el=document.getElementById('genderBars');
      if (!rows||!rows.length) {{ el.innerHTML='<div class="empty">No data.</div>'; return; }}
      const ordered=[...rows].sort((a,b)=>num(b.users)-num(a.users));
      const total=ordered.reduce((s,r)=>s+num(r.users),0)||1;
      const cap=g=>String(g).replace(/\b\w/g,c=>c.toUpperCase());
      const seg=ordered.map((r,i)=>{{const p=num(r.users)/total*100;return`<div class="gender-seg" style="width:${{p.toFixed(2)}}%;background:${{genderColor(r.gender,i)}}" title="${{esc(cap(r.gender))}} — ${{count(r.users)}} (${{p.toFixed(1)}}%)">${{p>=10?p.toFixed(0)+'%':''}}</div>`;}}).join('');
      const stats=ordered.map((r,i)=>{{const p=num(r.users)/total*100;return`<div class="gender-stat"><span class="gender-dot" style="background:${{genderColor(r.gender,i)}}"></span><div><div class="gender-stat-label">${{esc(cap(r.gender))}}</div><div class="gender-stat-value">${{count(r.users)}}</div><div class="gender-stat-pct">${{p.toFixed(0)}}% of known</div></div></div>`;}}).join('');
      el.innerHTML=`<div class="gender-wrap"><div class="gender-bar">${{seg}}</div><div class="gender-stats">${{stats}}</div></div>`;
    }}
    // Users-by-state tile-grid heat map. Each state is a labeled square on an
    // approximate US grid, shaded by its share of users; hover for exact counts.
    // Unmapped regions (non-US, territories) simply don't appear — they remain
    // visible in the cities table beside it.
    const STATE_TILES={{
      'Alabama':['AL',6,6],'Alaska':['AK',0,0],'Arizona':['AZ',5,1],'Arkansas':['AR',5,4],
      'California':['CA',4,0],'Colorado':['CO',4,2],'Connecticut':['CT',3,9],'Delaware':['DE',4,9],
      'Florida':['FL',7,8],'Georgia':['GA',6,7],'Hawaii':['HI',7,0],'Idaho':['ID',2,1],
      'Illinois':['IL',2,5],'Indiana':['IN',3,5],'Iowa':['IA',3,4],'Kansas':['KS',5,3],
      'Kentucky':['KY',4,5],'Louisiana':['LA',6,4],'Maine':['ME',0,10],'Maryland':['MD',4,8],
      'Massachusetts':['MA',2,9],'Michigan':['MI',2,7],'Minnesota':['MN',2,4],'Mississippi':['MS',6,5],
      'Missouri':['MO',4,4],'Montana':['MT',2,2],'Nebraska':['NE',4,3],'Nevada':['NV',3,1],
      'New Hampshire':['NH',1,10],'New Jersey':['NJ',3,8],'New Mexico':['NM',5,2],'New York':['NY',2,8],
      'North Carolina':['NC',5,6],'North Dakota':['ND',2,3],'Ohio':['OH',3,6],'Oklahoma':['OK',6,3],
      'Oregon':['OR',3,0],'Pennsylvania':['PA',3,7],'Rhode Island':['RI',2,10],'South Carolina':['SC',5,7],
      'South Dakota':['SD',3,3],'Tennessee':['TN',5,5],'Texas':['TX',7,3],'Utah':['UT',4,1],
      'Vermont':['VT',1,9],'Virginia':['VA',4,7],'Washington':['WA',2,0],'West Virginia':['WV',4,6],
      'Wisconsin':['WI',2,6],'Wyoming':['WY',3,2],'District of Columbia':['DC',5,8]
    }};
    function lerpColor(t) {{
      // #eaf1fb (light) → #1d6fd0 (accent), gamma-eased so mid values read.
      const a=[234,241,251], b=[29,111,208], e=Math.sqrt(Math.max(0,Math.min(1,t)));
      return 'rgb('+a.map((v,i)=>Math.round(v+(b[i]-v)*e)).join(',')+')';
    }}
    function renderStateMap(regionRows) {{
      const host=document.getElementById('stateMap');
      if (!host) return;
      // Users drive the shading; sessions/new users ride along for the tooltip
      // (absent on the city-derived fallback rows, which carry users only).
      const byState={{}};
      for (const r of (regionRows||[])) {{
        const s=byState[r.region]||(byState[r.region]={{users:0,sessions:0,new_users:0,has_detail:false}});
        s.users+=num(r.users);
        if (r.sessions!=null||r.new_users!=null) {{
          s.sessions+=num(r.sessions); s.new_users+=num(r.new_users); s.has_detail=true;
        }}
      }}
      const max=Math.max(1,...Object.values(byState).map(s=>s.users));
      const TS=26, GAP=3, CELL=TS+GAP, COLS=11, ROWS=8;
      const W=COLS*CELL-GAP, H=ROWS*CELL-GAP;
      let tiles='';
      for (const [name,[ab,r,c]] of Object.entries(STATE_TILES)) {{
        const s=byState[name], v=s?s.users:0, x=c*CELL, y=r*CELL;
        const fill=v>0?lerpColor(v/max):'#eef2f7';
        const txt=v/max>0.55?'#fff':'#5a6b82';
        const detail=(s&&s.has_detail&&v>0)?` · ${{count(s.sessions)}} sessions · ${{count(s.new_users)}} new`:'';
        tiles+=`<g class="state-tile"><rect x="${{x}}" y="${{y}}" width="${{TS}}" height="${{TS}}" rx="4" fill="${{fill}}"><title>${{esc(name)}} — ${{count(v)}} users${{detail}}</title></rect>`+
          `<text class="state-tile-label" x="${{x+TS/2}}" y="${{y+TS/2+3}}" text-anchor="middle" style="fill:${{txt}}">${{ab}}</text></g>`;
      }}
      const hasData=Object.keys(byState).length>0;
      host.innerHTML=`<svg viewBox="0 0 ${{W}} ${{H}}" role="img" aria-label="Users by US state">${{tiles}}</svg>`+
        (hasData?`<div class="state-map-scale"><span>Fewer</span><span class="state-map-scale-bar"></span><span>More</span></div>`:`<div class="empty" style="padding:12px">No state-level data for this range.</div>`);
    }}
    async function loadDemographics() {{
      setStatus('demoStatus','Loading…');
      document.getElementById('stateMap').innerHTML = `<div class="skel" style="height:200px;border-radius:8px"></div>`;
      document.getElementById('citiesTable').innerHTML = skelTable(5,5);
      document.getElementById('ageBars').innerHTML = skelBars(5);
      document.getElementById('genderBars').innerHTML = skelBars(2);
      try {{
        const payload=await getJson(withDates(DEMOGRAPHICS_API));
        // Under a page-path scope the geography comes from the page-scoped geo
        // table, which only exists once a GA4 sync has written it. Until then
        // say so — falling back to the site-wide table here would present
        // whole-site geography as if it were scoped to the filtered pages.
        if (payload.scoped && payload.geo_scope_available === false) {{
          const msg='Page-scoped geography isn’t available yet. It appears after the next GA4 sync writes the per-page geography report.';
          document.getElementById('stateMap').innerHTML=`<div class="empty" style="padding:12px">${{esc(msg)}}</div>`;
          renderTable('citiesTable',[{{key:'city',label:'City',left:true}}],[],msg);
          setStatus('demoStatus','');
          return;
        }}
        // Prefer the accurate state rollup; fall back to summing the top cities.
        let regionRows=payload.by_region;
        if (!regionRows||!regionRows.length) {{
          const agg={{}};
          for (const r of (payload.by_city||[])) if (r.region) agg[r.region]=(agg[r.region]||0)+num(r.users);
          regionRows=Object.entries(agg).map(([region,users])=>({{region,users}}));
        }}
        renderStateMap(regionRows);
        // Eng. rate is served derived (engaged ÷ sessions); it reads '—' only for
        // dates synced before the geo report carried engaged_sessions.
        renderTable('citiesTable',[
          {{key:'city',label:'City',left:true}},
          {{key:'region',label:'Region',left:true}},
          {{key:'users',label:'Users',format:count}},
          {{key:'new_users',label:'New',format:v=>v!=null?count(v):'—'}},
          {{key:'sessions',label:'Sessions',format:count}},
          {{key:'engagement_rate',label:'Eng. rate',format:v=>v!=null?v+'%':'—'}},
          {{key:'key_events',label:'Key events',format:count}},
        ], payload.by_city||[], 'No city data.');
        demoAgeRows=payload.by_age||[];
        renderAge();
        renderGender(payload.by_gender||[]);
        setStatus('demoStatus','');
      }} catch(err) {{ setStatus('demoStatus',err.message||String(err),true); }}
    }}

    // ---- GA4: Average session duration ----
    // GA4 only reports averageSessionDuration next to a dimension, so the
    // endpoint re-weights the per-landing-page averages by their sessions to
    // rebuild the site-wide figure: one card for the range, and the same figure
    // per day beneath it. Bars rather than a line -- each day is its own
    // average, not a running total -- with the comparison period riding over
    // them as a dashed line.
    let avgDurGran = 'daily';
    let avgDurCache = {{ cur: [], prev: [] }};
    // Weeks start Monday, and a week's average is re-weighted by its days'
    // sessions: averaging the daily averages would let a dead Sunday count for
    // as much as a busy Tuesday.
    function avgDurWeekly(daily) {{
      if (!daily || !daily.length) return [];
      const out = []; let cur = null;
      for (const d of daily) {{
        const dt = new Date(String(d.date) + 'T00:00:00');
        const dow = (dt.getDay() + 6) % 7;            // 0 = Monday
        const mon = new Date(dt); mon.setDate(dt.getDate() - dow);
        const key = `${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        if (!cur || cur.date !== key) {{ cur = {{ date:key, sessions:0, secs:0 }}; out.push(cur); }}
        const sess = num(d.sessions);
        cur.sessions += sess;
        cur.secs += num(d.avg_session_duration_seconds) * sess;
      }}
      return out.map(w => ({{
        date: w.date, sessions: w.sessions,
        avg_session_duration_seconds: w.sessions ? w.secs / w.sessions : 0,
      }}));
    }}
    // Session-weighted average of a bucketed series -- what the legend quotes
    // for a whole window, and the basis of its "vs previous" figure.
    function avgDurWeighted(rows) {{
      let secs = 0, sess = 0;
      for (const r of (rows||[])) {{
        const s = num(r.sessions);
        sess += s; secs += num(r.avg_session_duration_seconds) * s;
      }}
      return sess ? secs / sess : null;
    }}
    function avgDurRows(rows) {{ return avgDurGran === 'weekly' ? avgDurWeekly(rows) : (rows || []); }}
    function renderAvgDurTrend() {{
      drawAvgDurTrend(avgDurRows(avgDurCache.cur), avgDurRows(avgDurCache.prev));
    }}
    registerAnnotatedChart(() => {{ if (avgDurCache && avgDurCache.cur) renderAvgDurTrend(); }});
    function drawAvgDurTrend(rows, prevRows) {{
      clearSkelChart('avgDurTrendChart');
      const legend = document.getElementById('avgDurTrendLegend');
      const n = rows.length;
      if (!n) {{ __destroyChart('avgDurTrendChart'); if (legend) legend.innerHTML = ''; return; }}
      const vals = rows.map(r => num(r.avg_session_duration_seconds));
      const sess = rows.map(r => num(r.sessions));
      const prevVals = (prevRows || []).map(r => num(r.avg_session_duration_seconds));
      const hasPrev = prevVals.length > 0;
      const labels = rows.map(r => String(r.date).slice(5));
      const series = [];
      // Previous first so the dashed line draws over the current bars.
      if (hasPrev) series.push({{ kind:'line', label:cmpSeriesLabel(), data:prevVals.slice(0,n), color:'#9aa7bd', dashed:true }});
      series.push({{ label:'Current', data:vals, color:'rgba(29,111,208,.72)', hoverColor:'#1d6fd0' }});
      barChart('avgDurTrendChart', labels, series, {{
        yFmt: v => fmtDuration(v),
        maxBarThickness: 26,
        tooltip: {{
          label: c => `${{c.dataset.label}}: ${{fmtDuration(c.raw)}}`,
          // How many sessions the bar averages, so a freak one-visit day reads
          // as exactly that rather than as a great day.
          afterBody: items => {{
            const i = (items && items.length) ? items[0].dataIndex : -1;
            return i < 0 ? '' : `${{count(sess[i])}} session${{sess[i] === 1 ? '' : 's'}}`;
          }},
        }},
        dates: rows.map(r => String(r.date)),
      }});
      if (legend) {{
        const curAvg = avgDurWeighted(rows), prevAvg = hasPrev ? avgDurWeighted(prevRows) : null;
        const delta = prevAvg ? ((curAvg - prevAvg) / prevAvg * 100) : null;
        const deltaTxt = delta == null ? '' : ` <span class="cmp-delta ${{delta>=0?'up':'down'}}">${{delta>=0?'+':''}}${{delta.toFixed(0)}}%</span>`;
        legend.innerHTML = `<span class="cmp-item"><span class="cmp-swatch cur"></span>Current (${{esc(currentStart.slice(5))}} \u2013 ${{esc(currentEnd.slice(5))}}) \u00b7 ${{fmtDuration(curAvg)}} avg${{deltaTxt}}</span>`
          + (hasPrev ? `<span class="cmp-item"><span class="cmp-swatch prev"></span>${{esc(cmpSeriesLabel())}} (${{esc(compareMode==='prev_year'?compareStart:compareStart.slice(5))}} \u2013 ${{esc(compareMode==='prev_year'?compareEnd:compareEnd.slice(5))}}) \u00b7 ${{fmtDuration(prevAvg)}}</span>` : '');
      }}
    }}
    async function loadSessionDuration() {{
      setStatus('avgDurStatus','Loading\u2026');
      document.getElementById('avgDurCards').innerHTML = skelCards(1);
      skelChart('avgDurTrendChart','trend-md-svg');
      try {{
        const [cur, prev] = await Promise.all([
          getJson(withDatesRange(SESSION_DURATION_API, currentStart, currentEnd)),
          compareStart ? getJson(withDatesRange(SESSION_DURATION_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
        ]);
        const v  = cur ? cur.avg_session_duration_seconds : null;
        const pv = prev ? prev.avg_session_duration_seconds : null;
        renderSnapshotCards('avgDurCards', [
          ['Avg session duration', v, pv, x => x==null ? '\u2014' : fmtDuration(x)],
        ]);
        avgDurCache = {{ cur: (cur && cur.daily) || [], prev: (prev && prev.daily) || [] }};
        renderAvgDurTrend();
        setCmpWarn('avgDurCmpWarn', ['google_analytics']);
        setStatus('avgDurStatus', v==null ? 'No data for this range yet.' : '');
      }} catch(err) {{
        document.getElementById('avgDurCards').innerHTML = '';
        clearSkelChart('avgDurTrendChart');
        __destroyChart('avgDurTrendChart');
        setStatus('avgDurStatus', err.message||String(err), true);
      }}
    }}
    // Daily/Weekly chips -- re-bucketed from cache, no refetch.
    document.querySelectorAll('#avgDurGranChips .chip').forEach(btn =>
      btn.addEventListener('click', () => {{
        if (btn.dataset.gran === avgDurGran) return;
        avgDurGran = btn.dataset.gran;
        document.querySelectorAll('#avgDurGranChips .chip').forEach(b => b.classList.toggle('active', b === btn));
        renderAvgDurTrend();
      }})
    );

    // ---- Loaders ----
    function loadAllAnalytics() {{
      // Staggered, not simultaneous: 8 modules x 1-3 sub-fetches each means
      // ~12-14 concurrent BigQuery queries if fired all at once, which was
      // intermittently tripping transient 500s under load. Spreading module
      // starts out keeps peak concurrency down without a noticeable delay.
      const modules=getModules();
      const loaders=[];
      if (modules.sessions)         loaders.push(loadSessionsTrend);
      if (modules.top_pages)        loaders.push(loadPages);
      if (modules.traffic)          loaders.push(loadTrafficAcq);
      if (modules.audience)         loaders.push(loadDeviceSplit);
      if (modules.landing)          loaders.push(loadLandingPages);
      if (modules.user_acquisition) loaders.push(loadUserAcquisition);
      if (modules.avg_duration)     loaders.push(loadSessionDuration);
      if (modules.demographics)     loaders.push(loadDemographics);
      loaders.forEach((fn,i)=>setTimeout(fn, i*250));
    }}
    // ---- Overview home: a widget per section, each with a "See more" jump ----
    // The Website analytics + AI traffic panels overlay the equivalent prior
    // period (compareStart/compareEnd) and support a Daily/Weekly interval
    // toggle; both re-render from cache without refetching. Search Console shows
    // branded vs. target keyword rank-distribution bands side by side.
    let ovSessionsGran='daily', ovAiGran='daily';
    let ovSessionsCache={{cur:[],prev:[]}}, ovAiCache={{cur:[],prev:[]}};
    // Collapse daily rows to one {{date,value}} per date (AI rows are per-platform,
    // so this also sums sessions across assistants for the total-traffic line).
    function ovSumByDate(rows, key) {{
      const m=new Map();
      for (const r of (rows||[])) {{ const d=String(r.date); m.set(d,(m.get(d)||0)+num(r[key])); }}
      return [...m.keys()].sort().map(d=>({{date:d, value:m.get(d)}}));
    }}
    // Re-bucket {{date,value}} rows into Monday-start weeks (client-side, no refetch).
    function ovAggregateWeekly(rows) {{
      if (!rows||!rows.length) return [];
      const out=[]; let cur=null;
      for (const r of rows) {{
        const dt=new Date(String(r.date)+'T00:00:00');
        const dow=(dt.getDay()+6)%7;                 // 0 = Monday
        const mon=new Date(dt); mon.setDate(dt.getDate()-dow);
        const key=`${{mon.getFullYear()}}-${{String(mon.getMonth()+1).padStart(2,'0')}}-${{String(mon.getDate()).padStart(2,'0')}}`;
        if (!cur||cur.date!==key) {{ cur={{date:key, value:0}}; out.push(cur); }}
        cur.value+=num(r.value);
      }}
      return out;
    }}
    // Current-vs-previous line, aligned by index (previous mapped onto the current
    // labels), mirroring the Website Analytics sessions hero chart.
    function ovDrawCompareTrend(chartId, legendId, curRows, prevRows, color, unit) {{
      const n=curRows.length;
      const lg=document.getElementById(legendId);
      if (!n) {{ __destroyChart(chartId); if(lg) lg.innerHTML=''; return; }}
      const vals=curRows.map(d=>num(d.value));
      const prevVals=(prevRows||[]).map(d=>num(d.value));
      const hasPrev=prevVals.length>0;
      const labels=curRows.map(d=>String(d.date).slice(5));
      const series=[];
      if (hasPrev) series.push({{label:cmpSeriesLabel(), data:prevVals.slice(0,n), color:'#9aa7bd', dashed:true}});
      series.push({{label:'Current', data:vals, color, fill:true}});
      lineChart(chartId, labels, series, {{
        yFmt: v=>count(v),
        tooltip: {{ label: c=>`${{c.dataset.label}}: ${{count(c.raw)}} ${{unit}}` }},
        dates: curRows.map(d=>String(d.date)),
      }});
      if (lg) {{
        const curTot=vals.reduce((a,b)=>a+b,0), prevTot=prevVals.reduce((a,b)=>a+b,0);
        const delta=(hasPrev&&prevTot)?((curTot-prevTot)/prevTot*100):null;
        const deltaTxt=delta==null?'':` <span class="cmp-delta ${{delta>=0?'up':'down'}}">${{delta>=0?'+':''}}${{delta.toFixed(0)}}%</span>`;
        lg.innerHTML=`<span class="cmp-item"><span class="cmp-swatch cur"></span>Current · ${{count(curTot)}} ${{unit}}${{deltaTxt}}</span>`
          + (hasPrev?`<span class="cmp-item"><span class="cmp-swatch prev"></span>${{esc(cmpSeriesLabel())}} · ${{count(prevTot)}}</span>`:'');
      }}
    }}
    function ovRenderSessions() {{
      const c=ovSessionsCache, wk=ovSessionsGran==='weekly';
      ovDrawCompareTrend('ovSessionsTrend','ovSessionsLegend',
        wk?ovAggregateWeekly(c.cur):c.cur, wk?ovAggregateWeekly(c.prev):c.prev, '#1769aa', 'sessions');
    }}
    registerAnnotatedChart(() => {{ if (ovSessionsCache && ovSessionsCache.cur && ovSessionsCache.cur.length) ovRenderSessions(); }});
    function ovRenderAi() {{
      const c=ovAiCache, wk=ovAiGran==='weekly';
      ovDrawCompareTrend('ovAiTrend','ovAiLegend',
        wk?ovAggregateWeekly(c.cur):c.cur, wk?ovAggregateWeekly(c.prev):c.prev, '#7c3aed', 'sessions');
    }}
    // Site Performance scorecard (the four Lighthouse scores) — reuses psScoreCard
    // + PAGESPEED_TARGETS from the Site Performance pane's JS. Emitted only when
    // the pagespeed connector is present (Python gates the #ovPsScores element).
    async function loadOverviewPagespeed() {{
      const host=document.getElementById('ovPsScores');
      if (!host) return;
      setStatus('ovPsStatus','Loading…');
      host.innerHTML=skelCards(4);
      const strat=(typeof PS_STRATEGIES!=='undefined'&&PS_STRATEGIES.length)?PS_STRATEGIES[0]:'desktop';
      const url=PAGESPEED_API+(PAGESPEED_API.includes('?')?'&':'?')+'strategy='+strat;
      try {{
        const p=await getJson(url);
        if (!p||!p.url) {{ host.innerHTML=''; setStatus('ovPsStatus','No PageSpeed data yet'); return; }}
        const scores=[['Performance','performance',p.performance],['Accessibility','accessibility',p.accessibility],
          ['Best Practices','best_practices',p.best_practices],['SEO','seo',p.seo]];
        host.innerHTML=scores.map(([l,k,v])=>psScoreCard(l,v,PAGESPEED_TARGETS[k])).join('');
        setStatus('ovPsStatus', p.metric_date?('measured '+p.metric_date):'');
      }} catch(err) {{ host.innerHTML=''; setStatus('ovPsStatus', err.message||String(err), true); }}
    }}
    // Overview Search Console leaderboard: the top keywords by current rank
    // (best position first) — just keyword, position, and movement vs. the prior
    // period. Full sortable/paginated tables live on the Search Console tab; this
    // is the at-a-glance snapshot next to the rank-distribution trend.
    const OV_KW_LEADERS = 5;
    function renderKwLeaderboard(tableId, rows, configured) {{
      const el=document.getElementById(tableId); if(!el) return;
      if (!configured) {{ el.innerHTML=`<tbody><tr><td class="empty">No keywords set — add them on the Search Console tab.</td></tr></tbody>`; return; }}
      const top=(rows||[]).slice()
        .sort((a,b)=>num(a.avg_position)-num(b.avg_position))
        .slice(0,OV_KW_LEADERS);
      if (!top.length) {{ el.innerHTML=`<tbody><tr><td class="empty">No matching queries in this range.</td></tr></tbody>`; return; }}
      const head=`<thead><tr><th class="left">Keyword</th><th>Clicks</th><th>Position</th><th>Movement</th></tr></thead>`;
      const body=top.map(r=>`<tr><td class="left" title="${{esc(r.query)}}"><span>${{esc(r.query)}}</span></td><td>${{count(r.clicks)}}</td><td>${{gscPos(r.avg_position)}}</td><td>${{gscDelta(r.delta_position)}}</td></tr>`).join('');
      el.innerHTML=head+`<tbody>${{body}}</tbody>`;
    }}
    // Website analytics + AI traffic trends. Every other Overview card is
    // optional — connector-gated in Python, or hidden by an admin — so this
    // loader must never depend on one of them being in the DOM. It used to run
    // in the same function as the Search Console block, which meant a client
    // without GSC (a freshly connected GA4-only client, say) hit a TypeError on
    // the missing #ovGscBrandedLeaders before either chart drew, leaving both
    // panels blank.
    async function loadOverviewTrends() {{
      setStatus('ovSessionsStatus','Loading…'); setStatus('ovAiStatus','Loading…');
      const hasPrev=!!compareStart;
      try {{
        const [traffic, trafficPrev, aiCur, aiPrev] = await Promise.all([
          getJson(withDatesRange(TRAFFIC_ACQ_API, currentStart, currentEnd)).catch(()=>({{daily:[]}})),
          hasPrev ? getJson(withDatesRange(TRAFFIC_ACQ_API, compareStart, compareEnd)).catch(()=>null) : Promise.resolve(null),
          getJson(withDatesRange(AI_TRAFFIC_DAILY_API, currentStart, currentEnd)).then(d=>d.rows||[]).catch(()=>[]),
          hasPrev ? getJson(withDatesRange(AI_TRAFFIC_DAILY_API, compareStart, compareEnd)).then(d=>d.rows||[]).catch(()=>[]) : Promise.resolve([]),
        ]);
        // Website analytics — sessions, current vs previous.
        ovSessionsCache={{ cur: ovSumByDate(traffic.daily||[],'sessions'), prev: ovSumByDate((trafficPrev&&trafficPrev.daily)||[],'sessions') }};
        ovRenderSessions();
        const sTot=ovSessionsCache.cur.reduce((s,d)=>s+num(d.value),0);
        setStatus('ovSessionsStatus', sTot?count(sTot)+' sessions':'No data for this range yet.');
        // AI traffic — total AI sessions, current vs previous.
        ovAiCache={{ cur: ovSumByDate(aiCur,'sessions'), prev: ovSumByDate(aiPrev,'sessions') }};
        ovRenderAi();
        const aTot=ovAiCache.cur.reduce((s,d)=>s+num(d.value),0);
        setStatus('ovAiStatus', aTot?count(aTot)+' sessions':'No AI traffic in this range.');
      }} catch(err) {{
        const msg=err.message||String(err);
        setStatus('ovSessionsStatus', msg, true); setStatus('ovAiStatus', msg, true);
      }}
    }}
    // Search Console — branded & target keyword leaderboard (top by rank) plus
    // the weekly avg-position trend over time. The whole card is dropped when
    // GSC isn't connected (and can be hidden by an admin), so bail out unless
    // it's actually on the page.
    async function loadOverviewGsc() {{
      const brandedEl=document.getElementById('ovGscBrandedLeaders');
      const targetEl=document.getElementById('ovGscTargetLeaders');
      if (!brandedEl && !targetEl) return;
      setStatus('ovGscStatus','Loading…');
      if (brandedEl) brandedEl.innerHTML = skelTable(3,4);
      if (targetEl) targetEl.innerHTML = skelTable(3,4);
      try {{
        const [branded, target] = await Promise.all([
          fetchKeywordMatches(gscBrandedRoots, gscBrandedExclude),
          fetchKeywordMatches(gscTargetKeywords, gscTargetExclude),
        ]);
        renderKwLeaderboard('ovGscBrandedLeaders', branded.rows, gscBrandedRoots.length);
        renderKwLeaderboard('ovGscTargetLeaders', target.rows, gscTargetKeywords.length);
        drawKeywordTrend('ovGscBrandedTrend', branded.weekly, 'avg_position', '#1d6fd0', true);
        drawKeywordTrend('ovGscTargetTrend', target.weekly, 'avg_position', '#7c3aed', true);
        const noteFor=(roots, weekly)=> !roots.length ? 'Set keywords on the Search Console tab.'
          : (!(weekly||[]).length ? 'No matching queries in this range.' : '');
        const bn=document.getElementById('ovGscBrandedNote'); if(bn) bn.textContent=noteFor(gscBrandedRoots, branded.weekly);
        const tn=document.getElementById('ovGscTargetNote'); if(tn) tn.textContent=noteFor(gscTargetKeywords, target.weekly);
        setStatus('ovGscStatus', (!gscBrandedRoots.length && !gscTargetKeywords.length) ? ''
          : `${{gscBrandedRoots.length}} branded · ${{gscTargetKeywords.length}} target`);
      }} catch(err) {{
        setStatus('ovGscStatus', err.message||String(err), true);
      }}
    }}
    // Each card loads on its own so one card's failure (or absence) can't blank
    // the others.
    function loadOverviewHome() {{
      loadOverviewTrends();
      loadOverviewGsc();
      // Site performance scorecard (only present when the connector is on).
      loadOverviewPagespeed();
    }}
    // Daily/Weekly interval toggles for the two overview trend panels.
    document.querySelectorAll('#ovSessionsGranChips .chip').forEach(btn=>
      btn.addEventListener('click',()=>{{
        if (btn.dataset.gran===ovSessionsGran) return;
        ovSessionsGran=btn.dataset.gran;
        document.querySelectorAll('#ovSessionsGranChips .chip').forEach(b=>b.classList.toggle('active', b===btn));
        ovRenderSessions();
      }})
    );
    document.querySelectorAll('#ovAiGranChips .chip').forEach(btn=>
      btn.addEventListener('click',()=>{{
        if (btn.dataset.gran===ovAiGran) return;
        ovAiGran=btn.dataset.gran;
        document.querySelectorAll('#ovAiGranChips .chip').forEach(b=>b.classList.toggle('active', b===btn));
        ovRenderAi();
      }})
    );
    document.querySelectorAll('.ov-more[data-goto]').forEach(btn=>
      btn.addEventListener('click', ()=>switchTab(btn.dataset.goto))
    );
    function loadCurrentTab() {{
      // Goals are window-scoped (the stored monthly target is prorated to the
      // selected range), so they reload with the tab; benchmarks are not, and
      // loadBenchmarks() no-ops after its first success.
      if (currentTab==='overview' && HAS_PAID_ADS) {{ loadGoals(); loadBenchmarks(); }}
      // Annotations are window-independent (the API returns the client's whole
      // timeline and each chart filters to what it is showing), so one load
      // serves every tab and every range change.
      loadAnnotations();
      if (currentTab==='overview')   {{ loadHealth().then(()=>{{ if (HAS_PAID_ADS) loadSummary(); loadOverviewHome(); }}); }}
      else if (currentTab==='explorer') {{ explorerLoaded=false; loadExplorer(); explorerLoaded=true; }}
      else if (currentTab==='analytics') {{ analyticsLoaded=false; applyModules(); loadAllAnalytics(); analyticsLoaded=true; }}
      else if (currentTab==='ai_traffic') {{ aiTrafficLoaded=false; loadAiTraffic(); aiTrafficLoaded=true; }}
      // Search Console reads the selected window (loadGsc builds its URL from
      // currentStart/currentEnd), so it has to reload here or the tab stays
      // pinned to whatever range was active when it first opened. loadSemrush()
      // is deliberately not called: its endpoint takes no dates.
      else if (currentTab==='gsc') {{ gscLoaded=false; loadGsc(); gscLoaded=true; }}
    }}

    // ---- Date presets ----
    let currentStart='{start.isoformat()}', currentEnd='{end.isoformat()}';
    // The window every "vs previous" figure on the page is measured against.
    // Which window that is depends on the Compare picker: the equivalent period
    // immediately before the selected range (the default -- e.g. "This month"
    // July 1-6 -> June 1-6), or the same range a year earlier. applyPreset()
    // computes the previous-period option into prevPeriodStart/End;
    // resolveCompare() picks between that and the year-ago shift.
    let compareStart='', compareEnd='';
    let prevPeriodStart='', prevPeriodEnd='';
    const fmtDate=d=>`${{d.getFullYear()}}-${{String(d.getMonth()+1).padStart(2,'0')}}-${{String(d.getDate()).padStart(2,'0')}}`;
    function mondayOf(d) {{
      const day=d.getDay(); const diff=(day===0?-6:1)-day;
      const m=new Date(d); m.setDate(d.getDate()+diff); return m;
    }}
    // First day of the calendar quarter `d` falls in (Jan/Apr/Jul/Oct 1).
    function quarterStart(d) {{ return new Date(d.getFullYear(), Math.floor(d.getMonth()/3)*3, 1); }}
    // Shift an ISO date back n years, clamping to the last day of the target
    // month so Feb 29 lands on Feb 28 rather than rolling into March.
    function shiftYears(iso, n) {{
      const p=String(iso).split('-').map(Number);
      const y=p[0]-n, m=p[1], day=p[2];
      const daysInMonth=new Date(y, m, 0).getDate();
      return fmtDate(new Date(y, m-1, Math.min(day, daysInMonth)));
    }}
    function applyPreset(name) {{
      const today=new Date(); let s, e=today, cs, ce;
      const lastN=n=>{{
        e=new Date(today);e.setDate(today.getDate()-1);
        s=new Date(today);s.setDate(today.getDate()-n);
        ce=new Date(s); ce.setDate(s.getDate()-1);
        cs=new Date(ce); cs.setDate(ce.getDate()-(n-1));
      }};
      // GA4/GSC syncs typically lag a day or more, so "today" itself is
      // usually incomplete or entirely unsynced -- these presets end at
      // yesterday like the trailing (last_N) ones do, falling back to the
      // period start when yesterday would fall outside it (e.g. Monday for
      // "this_week", the 1st for "this_month").
      const yesterday=new Date(today); yesterday.setDate(today.getDate()-1);
      if (name==='this_week') {{
        s=mondayOf(today); e=(yesterday>=s)?yesterday:s;
        cs=new Date(s); cs.setDate(s.getDate()-7);
        ce=new Date(e); ce.setDate(e.getDate()-7);
      }} else if (name==='last_week') {{
        const lw=new Date(today); lw.setDate(today.getDate()-7);
        s=mondayOf(lw); e=new Date(s); e.setDate(s.getDate()+6);
        cs=new Date(s); cs.setDate(s.getDate()-7);
        ce=new Date(e); ce.setDate(e.getDate()-7);
      }} else if (name==='this_month') {{
        s=new Date(today.getFullYear(),today.getMonth(),1);
        e=(yesterday>=s)?yesterday:s;
        const dom=e.getDate();
        const daysInPrevMonth=new Date(today.getFullYear(),today.getMonth(),0).getDate();
        cs=new Date(today.getFullYear(),today.getMonth()-1,1);
        ce=new Date(today.getFullYear(),today.getMonth()-1,Math.min(dom,daysInPrevMonth));
      }} else if (name==='last_month') {{
        s=new Date(today.getFullYear(),today.getMonth()-1,1); e=new Date(today.getFullYear(),today.getMonth(),0);
        cs=new Date(today.getFullYear(),today.getMonth()-2,1); ce=new Date(today.getFullYear(),today.getMonth()-1,0);
      }} else if (name==='this_quarter') {{
        // Quarter-to-date, ending yesterday like the other "this_*" presets.
        // Previous period = the same number of days into the prior quarter,
        // clamped to that quarter's last day (quarters differ by a day or two).
        s=quarterStart(today);
        e=(yesterday>=s)?yesterday:s;
        cs=new Date(s.getFullYear(), s.getMonth()-3, 1);
        const prevQEnd=new Date(cs.getFullYear(), cs.getMonth()+3, 0);
        const daysIn=Math.round((e-s)/86400000);
        ce=new Date(cs); ce.setDate(cs.getDate()+daysIn);
        if (ce>prevQEnd) ce=prevQEnd;
      }} else if (name==='last_quarter') {{
        const qs=quarterStart(today);
        s=new Date(qs.getFullYear(), qs.getMonth()-3, 1);
        e=new Date(qs.getFullYear(), qs.getMonth(), 0);
        cs=new Date(qs.getFullYear(), qs.getMonth()-6, 1);
        ce=new Date(qs.getFullYear(), qs.getMonth()-3, 0);
      }} else if (name==='last_7') lastN(7);
      else if (name==='last_30') lastN(30);
      else if (name==='last_90') lastN(90);
      else if (name==='last_365') lastN(365);
      else return;
      currentStart=fmtDate(s); currentEnd=fmtDate(e);
      prevPeriodStart=fmtDate(cs); prevPeriodEnd=fmtDate(ce);
      resolveCompare();
      syncRangeUI(name);
      loadCurrentTab();
    }}
    // ---- Custom range ----
    // Parse a YYYY-MM-DD value as a *local* date. `new Date(iso)` would read it
    // as UTC and land on the previous day west of Greenwich, which would shift
    // every window the user picked by a day.
    function parseIsoDate(iso) {{
      const p=String(iso||'').split('-').map(Number);
      if (p.length!==3 || p.some(n=>!Number.isFinite(n))) return null;
      const d=new Date(p[0], p[1]-1, p[2]);
      return isNaN(d.getTime()) ? null : d;
    }}
    // A hand-picked window. Once applied it behaves exactly like a preset: the
    // comparison period is the same number of days immediately before it, so
    // every "vs previous" figure on the page keeps working (and the Compare
    // picker's "Previous year" still shifts the chosen window back 12 months).
    // Returns an error string to show in the panel, or '' on success.
    function applyCustomRange(startIso, endIso) {{
      const s=parseIsoDate(startIso), e=parseIsoDate(endIso);
      if (!s || !e) return 'Pick a start and end date.';
      if (e<s) return 'The end date must be on or after the start date.';
      const days=Math.round((e-s)/86400000)+1;
      const ce=new Date(s); ce.setDate(s.getDate()-1);
      const cs=new Date(ce); cs.setDate(ce.getDate()-(days-1));
      currentStart=fmtDate(s); currentEnd=fmtDate(e);
      prevPeriodStart=fmtDate(cs); prevPeriodEnd=fmtDate(ce);
      resolveCompare();
      syncRangeUI('custom');
      loadCurrentTab();
      return '';
    }}
    // ---- Comparison period ----
    // 'prev_period' (default) or 'prev_year'. Remembered per client in this
    // browser -- it's a reading preference, not a client-wide setting.
    let compareMode='prev_period';
    try {{
      const saved=localStorage.getItem(COMPARE_MODE_STORAGE_KEY);
      if (saved && COMPARE_MODE_LABELS[saved]) compareMode=saved;
    }} catch(e) {{}}
    // Point compareStart/compareEnd at the window the current mode asks for and
    // refresh everything that spells the comparison out in words.
    function resolveCompare() {{
      if (compareMode==='prev_year' && currentStart && currentEnd) {{
        compareStart=shiftYears(currentStart,1); compareEnd=shiftYears(currentEnd,1);
      }} else {{
        compareStart=prevPeriodStart; compareEnd=prevPeriodEnd;
      }}
      syncCompareUI();
    }}
    // Wording for the comparison, used by every delta label and tooltip so the
    // page never says "vs prior period" while comparing against last year.
    function cmpNoun() {{ return compareMode==='prev_year' ? 'prior year' : 'prior period'; }}
    function cmpSeriesLabel() {{ return compareMode==='prev_year' ? 'Previous year' : 'Previous'; }}
    function syncCompareUI() {{
      const lbl=document.getElementById('compareToggleLabel');
      if (lbl && COMPARE_MODE_LABELS[compareMode]) lbl.textContent=COMPARE_MODE_LABELS[compareMode];
      document.querySelectorAll('#compareList .range-opt').forEach(o =>
        o.classList.toggle('active', o.dataset.cmp===compareMode));
      const foot=document.getElementById('compareRangeLabel');
      if (foot) foot.textContent = compareStart ? `${{compareStart}} – ${{compareEnd}}` : '';
      syncCompareNotice();
    }}
    function setCompareMode(mode) {{
      if (!COMPARE_MODE_LABELS[mode] || mode===compareMode) return;
      compareMode=mode;
      try {{ localStorage.setItem(COMPARE_MODE_STORAGE_KEY, mode); }} catch(e) {{}}
      resolveCompare();
      loadCurrentTab();
    }}
    (function(){{
      const dd=document.getElementById('compareDropdown'); if (!dd) return;
      const toggle=document.getElementById('compareToggle');
      const panel=document.getElementById('comparePanel');
      const list=document.getElementById('compareList');
      const setOpen=o=>{{ panel.hidden=!o; dd.classList.toggle('open', o); toggle.setAttribute('aria-expanded', o?'true':'false'); }};
      toggle.addEventListener('click', ()=>setOpen(panel.hidden));
      document.addEventListener('click', e=>{{ if (!dd.contains(e.target)) setOpen(false); }});
      document.addEventListener('keydown', e=>{{ if (e.key==='Escape') setOpen(false); }});
      list.addEventListener('click', e=>{{
        const opt=e.target.closest('.range-opt'); if (!opt) return;
        setCompareMode(opt.dataset.cmp);
        setOpen(false);
      }});
    }})();
    // ---- Range dropdown (custom): preset list + admin "Make default" / Apply ----
    // The preset list instant-applies on click (the panel stays open so an admin
    // can then tick "Make default" and Apply). syncRangeUI is hoisted so
    // applyPreset can refresh the toggle label + active row on every change.
    let currentPreset = DEFAULT_DATE_PRESET || 'last_30';
    function syncRangeUI(name) {{
      currentPreset = name;
      const lbl = document.getElementById('rangeToggleLabel');
      // A custom window has no preset label, so the toggle spells out the dates.
      if (lbl) {{
        if (name === 'custom') lbl.textContent = `${{currentStart}} – ${{currentEnd}}`;
        else if (DATE_PRESET_LABELS[name]) lbl.textContent = DATE_PRESET_LABELS[name];
      }}
      document.querySelectorAll('#rangeList .range-opt').forEach(o =>
        o.classList.toggle('active', !!o.dataset.preset && o.dataset.preset === name));
      const customRow = document.getElementById('rangeCustomOpen');
      if (customRow) customRow.classList.toggle('active', name === 'custom');
      const chk = document.getElementById('rangeMakeDefault');
      if (chk) {{
        chk.checked = !!STORED_DEFAULT_PRESET && STORED_DEFAULT_PRESET === name;
        // Only presets can be a client's landing range -- a one-off window is
        // not something the API stores, so "Make default" is off the table.
        chk.disabled = name === 'custom';
      }}
    }}
    (function(){{
      const dd = document.getElementById('rangeDropdown'); if (!dd) return;
      const toggle = document.getElementById('rangeToggle');
      const panel = document.getElementById('rangePanel');
      const list = document.getElementById('rangeList');
      const setOpen = (o) => {{ panel.hidden=!o; dd.classList.toggle('open', o); toggle.setAttribute('aria-expanded', o?'true':'false'); }};
      // The toggle deliberately lets the click bubble: the document handler
      // below ignores clicks inside this dropdown, so a click on a *sibling*
      // picker (Compare, Events) still closes this one instead of leaving two
      // panels stacked over each other.
      toggle.addEventListener('click', ()=>setOpen(panel.hidden));
      document.addEventListener('click', e=>{{ if (!dd.contains(e.target)) setOpen(false); }});
      document.addEventListener('keydown', e=>{{ if (e.key==='Escape') setOpen(false); }});
      // Custom range: the last row in the list toggles a start/end form inside
      // the panel instead of applying anything, so picking dates never costs a
      // round of loaders until the user hits Apply.
      const customRow = document.getElementById('rangeCustomOpen');
      const customBox = document.getElementById('rangeCustom');
      const customStart = document.getElementById('rangeCustomStart');
      const customEnd = document.getElementById('rangeCustomEnd');
      const customApply = document.getElementById('rangeCustomApply');
      const customErr = document.getElementById('rangeCustomErr');
      const setCustomOpen = (o) => {{
        if (!customBox || !customRow) return;
        customBox.hidden = !o;
        customRow.setAttribute('aria-expanded', o ? 'true' : 'false');
        if (o) {{
          // Seed from whatever is on screen, so the form opens on the range the
          // user is already looking at rather than empty fields.
          if (customStart && !customStart.value) customStart.value = currentStart;
          if (customEnd && !customEnd.value) customEnd.value = currentEnd;
          if (customStart) customStart.focus();
        }} else if (customErr) {{
          customErr.textContent = '';
        }}
      }};
      list.addEventListener('click', e=>{{
        const opt = e.target.closest('.range-opt'); if (!opt) return;
        if (opt.dataset.custom) {{ setCustomOpen(customBox && customBox.hidden); return; }}
        setCustomOpen(false);
        applyPreset(opt.dataset.preset);
      }});
      if (customApply) {{
        const submit = () => {{
          const err = applyCustomRange(customStart.value, customEnd.value);
          customErr.textContent = err;
          if (!err) {{ setCustomOpen(false); setOpen(false); }}
        }};
        customApply.addEventListener('click', submit);
        [customStart, customEnd].forEach(inp => inp.addEventListener('keydown', ev=>{{
          if (ev.key === 'Enter') {{ ev.preventDefault(); submit(); }}
        }}));
      }}
      // Admin footer: persist (or clear) the applied preset as the client default.
      const apply = document.getElementById('rangeApply');
      const chk = document.getElementById('rangeMakeDefault');
      const status = document.getElementById('rangeDefaultStatus');
      if (apply && chk) {{
        apply.addEventListener('click', async () => {{
          // Non-destructive: tick → save the applied preset; untick → clear only a
          // stored default that matches what's applied, so viewing a non-default
          // range and hitting Apply never wipes an unrelated default.
          let preset;
          if (chk.checked) preset = currentPreset;
          else if (STORED_DEFAULT_PRESET && STORED_DEFAULT_PRESET === currentPreset) preset = '';
          else {{ setOpen(false); return; }}
          apply.disabled = true; status.className='range-default-status'; status.textContent='Saving…';
          try {{
            const r = await fetch(DEFAULT_DATE_RANGE_API, {{
              method:'POST', credentials:'same-origin',
              headers:{{ 'Content-Type':'application/json' }},
              body: JSON.stringify({{ preset }}),
            }});
            const b = await r.json().catch(()=>({{}}));
            if (!r.ok) throw new Error((b && (b.detail && (b.detail.error || b.detail) || b.detail)) || r.statusText);
            STORED_DEFAULT_PRESET = preset;
            status.className='range-default-status ok';
            status.textContent = preset ? 'Saved as default' : 'Default cleared';
            setTimeout(()=>setOpen(false), 700);
          }} catch (err) {{
            status.className='range-default-status err'; status.textContent='Save failed';
          }} finally {{
            apply.disabled = false;
          }}
        }});
      }}
    }})();

    // ---- Platform chips ----
    // One row per card the filter acts on (Paid summary, Campaign explorer),
    // both driving the same platformFilter Set — so a click in either has to
    // repaint the other's active states as well as the two views.
    if (HAS_PAID_ADS) {{
      const platformRows = [...document.querySelectorAll('.platform-chips')];
      const syncPlatformRows = () => platformRows.forEach(el =>
        el.querySelectorAll('.chip').forEach(b => b.classList.toggle(
          'active', b.dataset.key==='All' ? platformFilter.size===0 : platformFilter.has(b.dataset.key))));
      platformRows.forEach(el => buildChips(el,['Google','LinkedIn','Meta','Microsoft'],platformFilter,()=>{{
        syncPlatformRows(); renderSummary(); renderExplorer();
      }}));
    }}

    // ---- Explorer chips ----
    buildExplorerFilters();

    document.getElementById('explorerTable').addEventListener('click',ev=>{{
      const shuf=ev.target.closest('.gads-shuffle');
      if (shuf) {{
        ev.stopPropagation();
        const cell=shuf.closest('.ad-cell');
        if (cell) gadsUpdatePreview(cell, true);
        return;
      }}
      const thumb=ev.target.closest('.ad-thumb');
      if (thumb) {{
        openCreativePreview(thumb.dataset.previewImage||'', thumb.dataset.previewVideo||'');
        return;
      }}
      const moreBtn=ev.target.closest('.ad-copy-more');
      if (moreBtn) {{
        const extra=moreBtn.nextElementSibling;
        extra.hidden=!extra.hidden;
        moreBtn.textContent = extra.hidden ? moreBtn.dataset.moreLabel : 'Show less';
        return;
      }}
      if (ev.target.closest('.ke-select-wrap')) return;  // filter select handles its own change
      const sortTh=ev.target.closest('th.expl-sort');
      if (sortTh) {{
        const key=sortTh.dataset.key;
        if (explorerSort.key===key) explorerSort.dir = explorerSort.dir==='asc'?'desc':'asc';
        else explorerSort = {{ key, dir: key==='name'?'asc':'desc' }};
        renderExplorer();
        return;
      }}
      const row=ev.target.closest('tr[data-expandable]');
      if (row) toggleExplorerRow(row);
    }});
    document.getElementById('explorerTable').addEventListener('change',ev=>{{
      const sel=ev.target.closest('.ke-select');
      if (!sel) return;
      selectedKeyEvent = sel.value || '__all__';
      try {{ localStorage.setItem(KE_STORAGE_KEY, selectedKeyEvent); }} catch(e) {{}}
      applyVerifiedSelection();
      renderExplorer();
    }});
    // ---- LinkedIn audience: dimension tabs ----
    // Delegated: the tab strip is rebuilt from whichever dimensions came back
    // with rows, so there are no buttons to bind at startup. Null-guarded — an
    // admin can hide this panel from the layout editor, and a hidden panel is
    // not emitted at all for clients.
    const lidemoTabHost=document.getElementById('lidemoTabs');
    if (lidemoTabHost) lidemoTabHost.addEventListener('click',ev=>{{
      const btn=ev.target.closest('.pnl-tab[data-lidim]');
      if (!btn) return;
      lidemoDim=btn.dataset.lidim;
      renderLidemoTabs();
      renderLidemo();
    }});

    // ---- Google Ads demographics: dimension tabs ----
    // Delegated and null-guarded for the same reasons as the LinkedIn strip.
    const gdemoTabHost=document.getElementById('gdemoTabs');
    if (gdemoTabHost) gdemoTabHost.addEventListener('click',ev=>{{
      const btn=ev.target.closest('.pnl-tab[data-gdim]');
      if (!btn) return;
      gdemoDim=btn.dataset.gdim;
      renderGdemoTabs();
      renderGdemo();
    }});

    // ---- Keyword Performance: search, match filter and column sorting ----
    document.getElementById('keywordSearch').addEventListener('input',ev=>{{
      kwSearch=ev.target.value; kwPageNum=1; renderKeywordTable();
    }});
    document.getElementById('keywordMatchChips').addEventListener('click',ev=>{{
      const btn=ev.target.closest('.chip'); if (!btn) return;
      const m=btn.dataset.match;
      if (m==='All') kwMatchFilter.clear();
      else kwMatchFilter.has(m) ? kwMatchFilter.delete(m) : kwMatchFilter.add(m);
      kwPageNum=1; syncKeywordChips(); renderKeywordTable();
    }});
    document.getElementById('keywordTable').addEventListener('click',ev=>{{
      const th=ev.target.closest('th.expl-sort'); if (!th) return;
      const key=th.dataset.key;
      if (kwSort.key===key) kwSort.dir = kwSort.dir==='asc'?'desc':'asc';
      else kwSort = {{ key, dir: (key==='keyword_text'||key==='match_type')?'asc':'desc' }};
      kwPageNum=1; renderKeywordTable();
    }});
    // ---- Creative preview modal (click a thumbnail to see it full size) ----
    function openCreativePreview(imageUrl, videoUrl) {{
      const body=document.getElementById('creativePreviewBody');
      const modal=document.getElementById('creativePreview');
      if (!body || !modal) return;
      if (videoUrl) {{
        body.innerHTML = `<video src="${{esc(videoUrl)}}" controls autoplay playsinline poster="${{esc(imageUrl)}}"></video>`;
      }} else if (imageUrl) {{
        body.innerHTML = `<img src="${{esc(imageUrl)}}" alt="" referrerpolicy="no-referrer">`;
      }} else {{
        return;
      }}
      modal.hidden = false;
    }}
    function closeCreativePreview() {{
      const modal=document.getElementById('creativePreview');
      const body=document.getElementById('creativePreviewBody');
      if (!modal) return;
      modal.hidden = true;
      if (body) body.innerHTML = ''; // stop any playing video
    }}
    document.querySelectorAll('[data-close-preview]').forEach(el=>el.addEventListener('click', closeCreativePreview));
    document.addEventListener('keydown', ev=>{{ if (ev.key==='Escape') closeCreativePreview(); }});
    applyPreset(DEFAULT_DATE_PRESET || 'last_30');
    // Earliest-synced-date lookup drives the comparison-window notice, which is
    // page-level -- so fetch it even when the landing tab isn't Overview (that
    // loader asks for it too; loadHealth dedupes the concurrent call).
    loadHealth();

    // Deep-link + page-visibility prefs: land on the tab named in ?view= (set
    // by the sidebar links on Settings/Files/Connectors), unless an admin hid
    // that tab for this client (Admin > Advanced) -- then fall back to the first
    // visible page. The hidden set is server-side (window.__sfHiddenTabs, set by
    // the sidebar nav), so it's the same for every user and browser. Runs last,
    // after all loaders are initialized.
    (function(){{
      const hidden = new Set(Array.isArray(window.__sfHiddenTabs) ? window.__sfHiddenTabs : []);
      // A tab is reachable only if it's a known tab, not admin-hidden, AND the
      // sidebar actually rendered a button for it. That last check covers the
      // connector-gated tabs (Search Console, Site Performance): their panes are
      // always emitted, so without it a stale ?view=gsc link would strand the
      // user on an empty pane with no nav item to click back from.
      function reachable(t) {{
        return !!t && TABS.includes(t) && !hidden.has(t)
          && !!document.querySelector('.dash-view-btn[data-tab="' + t + '"]');
      }}
      const v = new URLSearchParams(location.search).get('view');
      let target = reachable(v) ? v : 'overview';
      if (!reachable(target)) target = TABS.find(reachable) || 'overview';
      if (target !== currentTab) switchTab(target);
    }})();
  </script>
  {budget_scripts}
  {notes_widget_html}
</body>
</html>"""
