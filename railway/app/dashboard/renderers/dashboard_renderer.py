"""Main Penn dashboard page renderer."""

from __future__ import annotations

from typing import Any

import client_config
import client_dashboard_config
import dashboard_features
import dashboard_theme
from penn_config import PennDashboardConfig
from ga4_attribution_service import build_ga4_campaign_index
from penn_business_lines import (
    active_client_product_line_catalog,
    active_client_segment_catalog,
    active_platform_catalog,
    client_filter_profile,
    client_has_product_line_filters,
    client_has_segment_filters,
    platform_catalog,
    platforms_present_in_snapshot,
    segment_column_label,
    segment_filter_label,
)

from dashboard.renderers.base_layout import (
    DASH_TOPBAR_CSS as _DASH_TOPBAR_CSS,
    dash_top_header_html as _dash_top_header_html,
    dashboard_topbar_js as _dashboard_topbar_js,
    dashboard_view_tabs_html as _dashboard_view_tabs_html,
    favicon_head_html as _favicon_head_html,
    render_client_shell_page,
)
from dashboard.renderers.cards_renderer import (
    budget_pacing_panel_html as _budget_pacing_panel_html,
    paid_ad_overview_html as _paid_ad_overview_html,
    paid_ad_overview_metrics as _paid_ad_overview_metrics,
    summary_cards_html as _summary_cards_html,
)
from dashboard.renderers.tables_renderer import ga4_platform_reports as _ga4_platform_reports
from dashboard.utils.formatting import esc as _esc, json_for_html_script as _json_for_html_script
from dashboard.utils.urls import settings_page_url as _settings_page_url

def _preset_to_label(preset: str) -> str:
    return {
        "LAST_7_DAYS": "Last 7 days",
        "LAST_30_DAYS": "Last 30 days",
        "LAST_90_DAYS": "Last 90 days",
        "LAST_180_DAYS": "Last 180 days",
        "THIS_MONTH": "This month",
        "LAST_MONTH": "Last month",
    }.get((preset or "").upper(), "Last 30 days")


_VR_PRESETS: tuple[tuple[str, str], ...] = (
    ("LAST_7_DAYS", "Last 7 days"),
    ("LAST_30_DAYS", "Last 30 days"),
    ("LAST_90_DAYS", "Last 90 days"),
    ("LAST_180_DAYS", "Last 180 days"),
    ("THIS_MONTH", "This month"),
    ("LAST_MONTH", "Last month"),
)


def _filter_snapshot_for_view(
    snapshot: dict[str, Any],
    vr_preset: str,
) -> tuple[dict[str, Any], str]:
    """Return (view_snapshot, notice). Filters daily_metrics to vr_preset bounds in memory.

    Does not mutate snapshot. platform_totals and aggregated_paid_media are cleared so
    downstream helpers recompute them from the filtered rows.
    """
    from dates_util import resolve_date_range

    start, end, _ = resolve_date_range(vr_preset)
    start_key = start.isoformat()
    end_key = end.isoformat()

    raw_daily: dict[str, Any] = snapshot.get("daily_metrics") or {}
    filtered_daily: dict[str, list] = {
        platform: [
            row for row in (rows or [])
            if start_key <= str(row.get("metric_date") or "")[:10] <= end_key
        ]
        for platform, rows in raw_daily.items()
    }

    snap_start = str((snapshot.get("date_range") or {}).get("start") or "")
    notice = (
        "Showing available stored data. Run a wider refresh to populate older dates."
        if snap_start and start_key < snap_start
        else ""
    )

    return (
        {
            **snapshot,
            "daily_metrics": filtered_daily,
            "platform_totals": {},
            "aggregated_paid_media": None,
        },
        notice,
    )


def _patch_campaign_breakdowns(
    breakdowns: dict[str, dict[str, list[dict[str, Any]]]],
    campaign_daily: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Overlay campaign-level metrics with view-range warehouse values."""
    if not campaign_daily:
        return breakdowns
    patched = dict(breakdowns)
    for source, by_cid in campaign_daily.items():
        src_data = patched.get(source)
        if not src_data or "campaign" not in src_data:
            continue
        new_campaigns = []
        for row in src_data["campaign"]:
            cid = row.get("id") or ""
            wh = by_cid.get(cid)
            if wh:
                row = dict(row)
                row["spend"] = float(wh.get("spend") or 0)
                row["clicks"] = int(wh.get("clicks") or 0)
                row["impressions"] = int(wh.get("impressions") or 0)
                row["conversions"] = float(wh.get("conversions") or 0)
            new_campaigns.append(row)
        patched[source] = {**src_data, "campaign": new_campaigns}
    return patched


def ga4_website_search_html(
    *,
    has_pages: bool,
    show_segment_filters: bool = False,
    segment_filter_label: str = "Business line",
) -> str:
    if not has_pages:
        return ""
    return """
        <div class="website-results-status">
          <span class="badge" id="ga4PagesCount">0 pages</span>
        </div>
        <p class="ga4-traffic-filter-note muted" id="ga4TrafficFilterNote" hidden></p>"""


def ga4_metrics_summary_html(*, has_summary: bool) -> str:
    if not has_summary:
        return ""
    return """
        <section class="ga4-metrics-section" id="ga4MetricsSection" aria-label="GA4 metrics summary">
          <div class="ga4-metrics-heading">
            <span class="ga4-metrics-pill">GA4 METRICS</span>
          </div>
          <div class="ga4-metrics-grid" id="ga4MetricsGrid"></div>
        </section>"""


def ga4_website_content_html(ga4_pages: dict[str, Any] | None) -> str:
    pages = (ga4_pages or {}).get("pages") or []
    dr = (ga4_pages or {}).get("date_range") or {}
    range_label = ""
    if dr.get("start") and dr.get("end"):
        range_label = f"{dr.get('start')} → {dr.get('end')}"
    if not pages:
        return (
            '<p class="ga4-pages-empty muted">No page data yet. Run a <strong>full refresh</strong> '
            "from Settings after GA4 BigQuery is connected.</p>"
        )
    return f"""
        <p class="table-note muted" id="ga4PagesTableNote">Site-wide GA4 metrics{f' for {range_label}' if range_label else ''}. Filter by paid traffic source to see click-ID landing pages (gclid, li_fat_id, fbclid). Use business line filters to match campaign, path, and title keywords.</p>
        <div class="table-wrap">
          <table class="data-table ga4-pages-table" id="ga4PagesTable">
            <thead>
              <tr>
                <th class="sortable" data-sort="page_path" scope="col" aria-sort="none">Page path<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable" data-sort="page_title" scope="col" aria-sort="none">Page title<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="sessions" scope="col" aria-sort="descending">Sessions<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="page_views" scope="col" aria-sort="none">Page views<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="users" scope="col" aria-sort="none">Users<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="engaged_sessions" scope="col" aria-sort="none">Engaged<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="engagement_rate" scope="col" aria-sort="none">Eng. rate<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="key_events" scope="col" aria-sort="none">Key events<span class="sort-icon" aria-hidden="true"></span></th>
              </tr>
            </thead>
            <tbody id="ga4PagesBody"></tbody>
          </table>
        </div>
        <div class="ga4-pages-pagination" id="ga4PagesPagination" hidden></div>"""


def ga4_pages_panel_html(ga4_pages: dict[str, Any] | None) -> str:
    """Legacy wrapper — website content lives on the Website Analytics view."""
    body = ga4_website_content_html(ga4_pages)
    return f"""
    <section class="panel ga4-pages-panel" aria-label="GA4 pages">
      <div class="panel-head"><h2>GA4 Pages</h2></div>
      {body}
    </section>"""


def global_filters_bar_html(
    *,
    show_segment_filters: bool,
    show_product_line_filters: bool,
    show_channel_filters: bool,
    show_date_range_filter: bool = False,
    show_website_search: bool = False,
    date_range_label: str = "Last 30 days",
    segment_filter_label: str = "Business line",
    view_range_preset: str = "LAST_30_DAYS",
    view_range_form_action: str = "",
    view_range_key_field: str = "",
) -> str:
    if (
        not show_segment_filters
        and not show_product_line_filters
        and not show_channel_filters
        and not show_date_range_filter
        and not show_website_search
    ):
        return ""
    bl_column = ""
    if show_segment_filters:
        bl_column = f"""
            <div class="filter-column">
              <span class="filter-column-label">{_esc(segment_filter_label)}</span>
              <div id="blFilters" class="filter-toggles" role="group" aria-label="{_esc(segment_filter_label)}"></div>
            </div>"""
    product_line_column = ""
    if show_product_line_filters:
        product_line_column = """
            <div class="filter-column">
              <span class="filter-column-label">Product line</span>
              <div id="productLineFilters" class="filter-toggles" role="group" aria-label="Product line"></div>
            </div>"""
    channel_column = ""
    if show_channel_filters:
        channel_column = """
            <div class="filter-column">
              <span class="filter-column-label">Channel</span>
              <div id="channelFilters" class="filter-toggles" role="group" aria-label="Channel"></div>
            </div>"""
    date_range_column = ""
    if show_date_range_filter:
        if view_range_form_action:
            _vr_options = "".join(
                f'<option value="{k}"{" selected" if k == view_range_preset else ""}>{v}</option>'
                for k, v in _VR_PRESETS
            )
            date_range_column = f"""
            <div class="filter-column">
              <span class="filter-column-label">Date range</span>
              <form method="get" action="{_esc(view_range_form_action)}" style="display:contents">
                {view_range_key_field}
                <select name="view_range" aria-label="Date range"
                  onchange="(function(sel){{var v=(new URLSearchParams(location.search)).get('view');if(v){{var i=document.createElement('input');i.type='hidden';i.name='view';i.value=v;sel.form.appendChild(i)}}sel.form.submit()}})(this)"
                  style="cursor:pointer;font:inherit;font-size:0.82rem;padding:5px 10px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text)">
                  {_vr_options}
                </select>
              </form>
            </div>"""
        else:
            date_range_column = f"""
            <div class="filter-column filter-column--locked">
              <span class="filter-column-label">Date range</span>
              <div class="filter-toggles filter-range-locked" role="group" aria-label="Date range">
                <button type="button" class="filter-toggle filter-toggle--locked" disabled
                  aria-disabled="true" title="Date range is fixed at {_esc(date_range_label)}">
                  <svg class="filter-lock-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    stroke-width="2" aria-hidden="true">
                    <rect x="5" y="11" width="14" height="10" rx="2"/>
                    <path d="M8 11V8a4 4 0 0 1 8 0v3"/>
                  </svg>
                  {_esc(date_range_label)}
                </button>
              </div>
            </div>"""
    website_search_column = ""
    if show_website_search:
        website_search_column = """
            <div class="filter-column filter-column--website-search" data-website-filter hidden>
              <label for="ga4PageSearch" class="filter-column-label">Search pages</label>
              <input type="search" id="ga4PageSearch" class="ga4-pages-search"
                placeholder="Filter by page path or title…" autocomplete="off">
            </div>"""
    filter_count = sum(
        1
        for flag in (
            show_segment_filters,
            show_product_line_filters,
            show_channel_filters,
            show_date_range_filter,
        )
        if flag
    )
    grid_class = "global-filter-grid"
    if filter_count <= 1:
        grid_class = "global-filter-grid global-filter-grid--single"
    elif filter_count >= 3:
        grid_class = "global-filter-grid global-filter-grid--triple"
    more_options = ""
    if show_segment_filters or show_product_line_filters or show_channel_filters:
        more_options = """
            <details class="filter-more-options">
              <summary>More filter options</summary>
              <label class="filter-zero-spend">
                <input type="checkbox" id="showZeroSpend">
                Show inactive / $0 spend
              </label>
            </details>"""
    return f"""
      <div class="dash-filters-bar" id="dashFiltersBar">
        <section class="filter-panel global-filters" id="globalFiltersPanel" aria-label="Dashboard filters">
          <div class="global-filters-head">
            <button type="button" class="filters-collapse-btn" id="filtersCollapseBtn"
              aria-expanded="true" aria-controls="globalFiltersBody">
              <svg class="filters-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <path d="M6 9l6 6 6-6"/>
              </svg>
              <span>Filters</span>
            </button>
            <div class="filter-status" id="filterStatus"></div>
          </div>
          <div class="global-filters-body" id="globalFiltersBody">
            <div class="{grid_class}">
              {bl_column}
              {product_line_column}
              {channel_column}
              {date_range_column}
              {website_search_column}
            </div>
            <div data-campaign-filter hidden>{more_options}</div>
          </div>
        </section>
      </div>"""


def campaign_explorer_content_html(
    *,
    show_segment_filters: bool,
    segment_column_label: str = "Business line",
    show_product_line_column: bool = False,
) -> str:
    bl_header = (
        f"""
                        <th class="sortable" data-sort="business_line" scope="col" aria-sort="none">{_esc(segment_column_label)}<span class="sort-icon" aria-hidden="true"></span></th>"""
        if show_segment_filters
        else ""
    )
    product_line_header = (
        """
                        <th class="sortable" data-sort="product_line" scope="col" aria-sort="none">Product line<span class="sort-icon" aria-hidden="true"></span></th>"""
        if show_product_line_column
        else ""
    )
    if show_segment_filters and show_product_line_column:
        filter_note = (
            "Use the filters at the top to narrow regions, product lines, and channels — "
            "all are included by default."
        )
    elif show_segment_filters:
        filter_note = (
            f"Use the filters at the top to narrow {segment_column_label.lower()}s and channels — "
            "all are included by default."
        )
    else:
        filter_note = "Use the channel filters at the top to narrow platforms — all are included by default."
    empty_colspan = 10 + int(show_segment_filters) + int(show_product_line_column)
    return f"""
            <section class="campaign-explorer-section" aria-label="Campaign performance">
              <div class="bl-summary" id="blSummary"></div>
              <section class="panel platform-panel">
                <div class="panel-head">
                  <h2>Campaign performance</h2>
                  <span class="badge" id="blRowCount">0 rows</span>
                </div>
                <p class=”table-note”>{filter_note} Expand a row to drill into ad groups and ads.</p>
                <div class="table-wrap">
                  <table class="data-table" id="blTable" data-show-segment-filters="{'true' if show_segment_filters else 'false'}" data-show-product-line="{'true' if show_product_line_column else 'false'}" data-empty-colspan="{empty_colspan}">
                    <thead>
                      <tr>
                        <th class="chevron-col"></th>
                        <th class="sortable" data-sort="platform" scope="col" aria-sort="none">Platform<span class="sort-icon" aria-hidden="true"></span></th>{bl_header}{product_line_header}
                        <th class="sortable" data-sort="name" scope="col" aria-sort="none">Campaign / group<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="spend" scope="col" aria-sort="none">Spend<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="clicks" scope="col" aria-sort="none">Clicks<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="impressions" scope="col" aria-sort="none">Impressions<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="ctr" scope="col" aria-sort="none">CTR<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="conversions" scope="col" aria-sort="none">Conv.<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="cpc" scope="col" aria-sort="none">CPC<span class="sort-icon" aria-hidden="true"></span></th>
                      </tr>
                    </thead>
                    <tbody id="blTableBody" class="tree-table"></tbody>
                  </table>
                </div>
              </section>
            </section>"""


def business_line_merged_section_html() -> str:
    """Deprecated — use _campaign_explorer_content_html with filters in _global_filters_bar_html."""
    return campaign_explorer_content_html(show_segment_filters=True)


def render_penn_html(
    snapshot: dict[str, Any] | None,
    *,
    client_slug: str = "penn",
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
    view_range: str | None = None,
) -> str:
    """use_session: refresh forms omit ?key= (cookie auth). access_key: legacy shared secret."""
    from dashboard.services.snapshot_metrics_service import (
        aggregated_paid_media as _aggregated_paid_media,
        breakdowns_from_snapshot as _breakdowns_from_snapshot,
        build_budget_pacing_payload as _build_budget_pacing_payload,
        business_line_campaigns_from_snapshot as _business_line_campaigns_from_snapshot,
        hydrate_platform_totals as _hydrate_platform_totals,
        platforms_with_summary_data as _platforms_with_summary_data,
    )

    slug = (client_slug or "penn").strip().lower()
    theme = dashboard_theme.load_client_theme(slug)
    try:
        empty_cfg = client_config.load_client_config(slug)
    except Exception:
        empty_cfg = None
    try:
        empty_label = client_config.client_label(slug)
    except ValueError:
        empty_label = slug.replace("-", " ").title()
    filter_kwargs = {"cfg": empty_cfg, "label": empty_label}
    show_segment_filters = client_has_segment_filters(slug, **filter_kwargs)
    show_product_line_filters = client_has_product_line_filters(slug, **filter_kwargs)
    seg_filter_label = segment_filter_label(slug, **filter_kwargs)
    seg_column_label = segment_column_label(slug, **filter_kwargs)
    if not snapshot:
        label = empty_label
        settings_page = _settings_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        )
        flash_html = ""
        if flash_message:
            flash_html = f'<div class="dash-flash">{_esc(flash_message)}</div>'
        empty_body = f"""
        {flash_html}
        <section class="panel">
          <h2>No data yet</h2>
          <p class="muted">Connect your ad platforms and run a refresh from Settings.</p>
          <p><a class="dash-link" href="{settings_page}">Go to Settings →</a></p>
        </section>"""
        return render_client_shell_page(
            client_slug=slug,
            label=label,
            active_nav="overview",
            page_title="Overview",
            page_subtitle=f"{label} · Paid media performance",
            content_html=empty_body,
            access_key=access_key,
            use_session=use_session,
            session_email=session_email,
            session_is_admin=session_is_admin,
            show_business_line=show_segment_filters,
            extra_css="""
    .panel { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow-sm); }
    .panel h2 { margin: 0 0 10px; font-size: 1.05rem; color: var(--navy); }
    .muted { color: var(--muted); }
    .dash-link { color: var(--accent); font-weight: 600; text-decoration: none; }
    .dash-link:hover { text-decoration: underline; }
    .dash-flash { background: var(--ok-bg); border: 1px solid #b8dfc8; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; font-size: 0.9rem; color: var(--ok); }
            """,
        )

    # Filter daily_metrics to the selected view range in memory — no platform sync, no snapshot write.
    from dashboard.utils.dates import WAREHOUSE_DATE_RANGES as _VR_VALID
    _vr_preset = (view_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if _vr_preset not in _VR_VALID:
        _vr_preset = "LAST_30_DAYS"
    _original_daily_metrics = snapshot.get("daily_metrics") or {}
    snapshot, _view_notice = _filter_snapshot_for_view(snapshot, _vr_preset)
    from dates_util import resolve_date_range as _resolve_vr
    _vr_start, _vr_end, _ = _resolve_vr(_vr_preset)

    label = snapshot.get("label") or client_config.client_label(slug)
    client_slug = str(snapshot.get("client_key") or slug)
    try:
        client_cfg = client_config.load_client_config(slug)
    except Exception:
        client_cfg = None
    filter_kwargs = {
        "cfg": client_cfg,
        "label": str(label or ""),
        "ga4_client_key": str((snapshot.get("accounts") or {}).get("ga4_client_key") or ""),
    }
    filter_profile = client_filter_profile(client_slug, **filter_kwargs)
    features = dashboard_features.resolve_features(client_slug, **filter_kwargs)
    show_segment_filters = features.segment_filters
    show_product_line_filters = features.product_line_filters
    seg_filter_label = segment_filter_label(client_slug, **filter_kwargs)
    seg_column_label = segment_column_label(client_slug, **filter_kwargs)
    seg_filter_all_label = f"All {seg_filter_label.lower()}s"
    dr = snapshot.get("date_range") or {}
    refreshed = snapshot.get("refreshed_at") or "—"
    preset = dr.get("preset") or ""
    range_label = f"{dr.get('start', '')} → {dr.get('end', '')} ({preset})"

    totals = _hydrate_platform_totals(snapshot)
    errors = snapshot.get("errors") or {}
    error_html = ""
    if errors:
        items = "".join(f"<li><strong>{_esc(k)}</strong>: {_esc(v)}</li>" for k, v in errors.items())
        error_html = f'<div class="errors"><strong>Partial refresh warnings</strong><ul>{items}</ul></div>'
    flash_html = ""
    if flash_message:
        flash_html = f'<div class="dash-flash">{_esc(flash_message)}</div>'
    view_notice_html = (
        f'<div class="dash-flash" style="background:#fff8e1;border-color:#e5c87a;color:#7a5900;">'
        f"{_esc(_view_notice)}</div>"
        if _view_notice
        else ""
    )

    chart_data = snapshot.get("daily_metrics") or {}
    dates_set: set[str] = set()
    for rows in chart_data.values():
        for row in rows:
            dates_set.add(str(row.get("metric_date") or "")[:10])
    dates = sorted(d for d in dates_set if d)

    def platform_metric_series(source: str, field: str) -> list[float]:
        by_date = {
            str(r.get("metric_date") or "")[:10]: float(r.get(field) or 0)
            for r in chart_data.get(source, [])
        }
        return [by_date.get(d, 0.0) for d in dates]

    accounts_early = snapshot.get("accounts") or {}
    has_ga4_config = bool(accounts_early.get("ga4_client_key") or totals.get("organic"))
    include_organic = has_ga4_config and features.organic_channel
    chart_platform_ids = ("google", "linkedin", "meta") + (
        ("organic",) if include_organic else ()
    )

    performance_chart_json = _json_for_html_script(
        {
            "labels": dates,
            "metrics": {
                metric: {
                    platform: platform_metric_series(platform, metric)
                    for platform in chart_platform_ids
                }
                for metric in ("spend", "clicks", "impressions", "conversions")
            },
        }
    )

    breakdowns = _breakdowns_from_snapshot(snapshot)
    if client_cfg and _vr_preset != preset:
        try:
            from dashboard.services.warehouse_metrics_service import load_campaign_daily_from_warehouse
            _campaign_daily = load_campaign_daily_from_warehouse(client_cfg, start=_vr_start, end=_vr_end)
            breakdowns = _patch_campaign_breakdowns(breakdowns, _campaign_daily)
        except Exception:
            pass
    accounts = accounts_early
    ga4_attr = snapshot.get("ga4_attribution")
    ga4_platforms = _ga4_platform_reports(ga4_attr)
    aggregated = snapshot.get("aggregated_paid_media") or _aggregated_paid_media(totals)
    breakdowns_json = _json_for_html_script(breakdowns)
    ga4_campaign_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for platform in ("google", "linkedin", "meta"):
        campaigns = (breakdowns.get(platform) or {}).get("campaign") or []
        report = ga4_platforms.get(platform) or {}
        ga4_campaign_metrics[platform] = (
            build_ga4_campaign_index(report.get("by_campaign") or [], campaigns)
            if campaigns and report
            else {}
        )
    ga4_campaign_metrics_json = _json_for_html_script(ga4_campaign_metrics)
    bl_campaigns = _business_line_campaigns_from_snapshot(snapshot)
    bl_campaigns_json = _json_for_html_script(bl_campaigns)
    bl_catalog_json = _json_for_html_script(
        active_client_segment_catalog(
            bl_campaigns,
            client_slug=client_slug,
            filter_profile=filter_profile,
        )
    )
    product_line_catalog_json = _json_for_html_script(
        active_client_product_line_catalog(
            bl_campaigns,
            client_slug=client_slug,
            filter_profile=filter_profile,
        )
    )
    try:
        if client_cfg is None:
            client_cfg = client_config.load_client_config(slug)
    except Exception:
        client_cfg = None
    platform_catalog_list = active_platform_catalog(
        bl_campaigns,
        include_organic=include_organic,
    )
    if not platform_catalog_list:
        present = platforms_present_in_snapshot(
            snapshot,
            cfg=client_cfg,
            include_configured=True,
            include_organic=include_organic,
        )
        platform_catalog_list = [
            item for item in platform_catalog(include_organic=include_organic) if item["id"] in present
        ]
    summary_platform_ids = _platforms_with_summary_data(totals, chart_data, breakdowns)
    if not features.organic_channel:
        summary_platform_ids = [pid for pid in summary_platform_ids if pid != "organic"]
    platform_catalog_json = _json_for_html_script(platform_catalog_list)
    overview_paid_html = _paid_ad_overview_html(aggregated, ga4_attr)
    paid_overview_metrics_json = _json_for_html_script(
        _paid_ad_overview_metrics(aggregated, ga4_attr)
    )
    import client_insight_documents as docs

    top_header = _dash_top_header_html(
        client_slug=client_slug,
        label=label,
        active_nav="overview",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        session_email=session_email,
        show_files=docs.enabled(),
    )
    ga4_pages_report = snapshot.get("ga4_pages")
    accounts = snapshot.get("accounts") or {}
    _ga4_client_key = str(accounts.get("ga4_client_key") or "").strip()
    if _ga4_client_key and _vr_preset != preset:
        try:
            import ga4_page_service as _ga4ps
            ga4_pages_report = _ga4ps.fetch_pages_for_dashboard(
                date_range=_vr_preset,
                client_key=_ga4_client_key,
                client_slug=client_slug,
            )
        except Exception:
            pass
    has_ga4 = bool(_ga4_client_key or (ga4_pages_report or {}).get("pages"))
    has_ga4_pages = bool((ga4_pages_report or {}).get("pages")) or any(
        (ga4_pages_report or {}).get("pages_by_platform", {}).get(platform)
        for platform in ("google", "linkedin", "meta")
    )
    has_ga4_summary = bool((ga4_pages_report or {}).get("summary"))
    show_website_tab = features.website_analytics and has_ga4
    _gsc_data = snapshot.get("gsc")
    view_tabs_html = _dashboard_view_tabs_html(
        show_website=show_website_tab,
        show_campaigns=features.campaign_explorer,
        show_gsc=bool(_gsc_data),
    )
    if use_session:
        _vr_action = f"/dashboard/{slug}"
        _vr_key_field = ""
    elif access_key:
        _vr_action = f"/dashboard/{slug}"
        _vr_key_field = f'<input type="hidden" name="key" value="{_esc(access_key)}">'
    else:
        _vr_action = ""
        _vr_key_field = ""
    filters_bar_html = global_filters_bar_html(
        show_segment_filters=show_segment_filters,
        show_product_line_filters=show_product_line_filters,
        show_channel_filters=bool(platform_catalog_list),
        show_date_range_filter=slug in {"penn", "penn-bq-test"},
        show_website_search=show_website_tab and has_ga4_pages,
        date_range_label=_preset_to_label(_vr_preset),
        segment_filter_label=seg_filter_label,
        view_range_preset=_vr_preset,
        view_range_form_action=_vr_action,
        view_range_key_field=_vr_key_field,
    )
    campaign_explorer_html = campaign_explorer_content_html(
        show_segment_filters=show_segment_filters,
        segment_column_label=seg_column_label,
        show_product_line_column=show_product_line_filters,
    )
    website_analytics_html = ga4_website_content_html(ga4_pages_report if has_ga4 else None)
    website_search_html = ga4_website_search_html(
        has_pages=has_ga4_pages,
        show_segment_filters=show_segment_filters,
        segment_filter_label=seg_filter_label,
    )
    ga4_metrics_html = ga4_metrics_summary_html(has_summary=has_ga4_summary)
    website_tab_panel = ""
    if show_website_tab:
        website_tab_panel = f"""
          <div id="view-website" class="view-panel" role="tabpanel" hidden>
            <section class="panel ga4-pages-panel" aria-label="Website analytics">
              {ga4_metrics_html}
              <div class="panel-head"><h2>Page performance</h2></div>
              {website_search_html}
              {website_analytics_html}
            </section>
          </div>"""
    ga4_pages_json = _json_for_html_script((ga4_pages_report or {}).get("pages") or [])
    ga4_pages_by_platform_json = _json_for_html_script(
        (ga4_pages_report or {}).get("pages_by_platform") or {}
    )
    ga4_landing_labels_json = _json_for_html_script(
        (ga4_pages_report or {}).get("landing_page_labels") or {}
    )
    ga4_summary_json = _json_for_html_script((ga4_pages_report or {}).get("summary") or {})
    metric_defs_json = _json_for_html_script(dashboard_theme.chart_metric_defs(theme))
    show_budget_pacing = features.budget_pacing
    can_save_budget = session_is_admin if use_session else bool(access_key)
    settings_url = _settings_page_url(
        client_slug=slug, access_key=access_key, use_session=use_session
    )
    budget_pacing_html = ""
    budget_pacing_json = _json_for_html_script({})
    if show_budget_pacing:
        pacing_cfg = client_cfg
        if pacing_cfg is None:
            try:
                pacing_cfg = client_config.load_client_config(slug)
            except Exception:
                pacing_cfg = PennDashboardConfig(
                    client_key=slug,
                    label=str(label or slug),
                    google_customer_id=None,
                    linkedin_account_id=None,
                    meta_account_id=None,
                    ga4_client_key=slug,
                )
        budget_pacing = _build_budget_pacing_payload(
            pacing_cfg,
            snapshot_daily_metrics=_original_daily_metrics,
        )
        budget_pacing["platform_colors"] = {
            "all": theme.get("sidebar_from", "#0a2540"),
            "pace": theme.get("business_line", "#c9a227"),
            "google": theme.get("google", "#4285f4"),
            "linkedin": theme.get("linkedin", "#0a66c2"),
            "meta": theme.get("meta", "#9333ea"),
        }
        budget_pacing_json = _json_for_html_script(budget_pacing)
        budget_pacing_html = _budget_pacing_panel_html(
            client_slug=slug,
            pacing=budget_pacing,
            settings_url=settings_url,
            can_save_budget=can_save_budget and client_dashboard_config.enabled(),
        )

    performance_trend_html = ""
    if features.performance_trend:
        performance_trend_html = """
            <section class="panel performance-trend-panel">
              <div class="panel-head performance-trend-head">
                <div class="panel-head-text">
                  <h2>Daily Paid Performance Trend</h2>
                  <p class="performance-trend-desc muted">Daily combined paid-media totals across selected channels. Choose one metric at a time.</p>
                </div>
                <div class="performance-trend-controls">
                  <div class="metric-toggle-group" role="group" aria-label="Chart metric">
                    <button type="button" class="metric-toggle active" data-metric="spend" aria-pressed="true">Spend</button>
                    <button type="button" class="metric-toggle" data-metric="clicks" aria-pressed="false">Clicks</button>
                    <button type="button" class="metric-toggle" data-metric="impressions" aria-pressed="false">Impressions</button>
                    <button type="button" class="metric-toggle" data-metric="conversions" aria-pressed="false">Conversions</button>
                  </div>
                </div>
              </div>
              <div class="performance-trend-chart-wrap">
                <p class="performance-trend-empty" id="performanceTrendEmpty">Choose a metric to display the chart.</p>
                <canvas id="performanceTrendChart"></canvas>
              </div>
            </section>"""

    campaign_explorer_panel = ""
    if features.campaign_explorer:
        _breakdown_scope_note = ""
        if slug == "penn" and _vr_preset != preset:
            _breakdown_scope_note = (
                '<p class="table-note muted" style="padding:10px 22px 2px;margin:0;">'
                "Campaign spend, clicks, impressions, and conversions reflect the selected date range. "
                "Ad group and ad rows reflect the full refreshed snapshot period."
                "</p>"
            )
        campaign_explorer_panel = f"""
          <div id="view-campaigns" class="view-panel" role="tabpanel" hidden>
            {_breakdown_scope_note}
            {campaign_explorer_html}
          </div>"""

    # GSC tab panel — only rendered when snapshot carries gsc data (penn-bq-test only)
    gsc_tab_panel = ""
    if _gsc_data:
        from dashboard.renderers.gsc_renderer import GSC_CSS as _GSC_CSS
        from dashboard.renderers.gsc_renderer import gsc_section_html as _render_gsc
        gsc_tab_panel = f"""
          <div id="view-gsc" class="view-panel" role="tabpanel" hidden>
            {_render_gsc(_gsc_data)}
          </div>"""
    else:
        _GSC_CSS = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — Ads Dashboard</title>{_favicon_head_html()}
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    {dashboard_theme.root_css_block(theme)}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    .app-shell {{
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }}
    {_DASH_TOPBAR_CSS}
    .info-tip {{
      appearance: none;
      border: 1px solid var(--border);
      background: #f8fafc;
      color: var(--muted);
      width: 22px;
      height: 22px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      cursor: help;
      flex-shrink: 0;
      line-height: 1;
      position: relative;
    }}
    .info-tip::after {{
      content: attr(data-tip);
      position: absolute;
      right: 0;
      left: auto;
      bottom: calc(100% + 8px);
      width: max-content;
      max-width: 240px;
      padding: 10px 12px;
      border-radius: 10px;
      background: #fff;
      color: var(--text);
      font-size: 0.76rem;
      font-weight: 500;
      line-height: 1.45;
      white-space: pre-line;
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
      opacity: 0;
      visibility: hidden;
      transform: translateY(4px);
      transition: opacity 0.15s, transform 0.15s, visibility 0.15s;
      pointer-events: none;
      z-index: 60;
    }}
    .info-tip:hover::after,
    .info-tip:focus-visible::after {{
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }}
    .info-tip--light::after {{
      left: auto;
      right: 0;
      bottom: calc(100% + 8px);
    }}
    .dash-main {{
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
    }}
    .dash-chrome-bar {{
      display: none;
    }}
    .dash-content {{
      flex: 1;
      width: 100%;
      max-width: none;
      margin: 0;
      padding: 24px 32px 48px;
    }}
    .wrap {{ min-width: 0; width: 100%; max-width: none; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px;
      box-shadow: var(--shadow-sm);
      transition: box-shadow 0.2s, transform 0.2s;
    }}
    .card:hover {{ box-shadow: var(--shadow); transform: translateY(-1px); }}
    .card-empty {{ opacity: 0.85; }}
    .card-label {{
      font-size: 0.78rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .06em;
      font-weight: 600;
    }}
    .card-label .platform-title {{
      text-transform: none;
      letter-spacing: -0.01em;
      font-size: 0.92rem;
      font-weight: 650;
      color: var(--navy);
    }}
    .platform-title {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .platform-favicon {{
      width: 20px;
      height: 20px;
      border-radius: 4px;
      flex-shrink: 0;
      object-fit: contain;
    }}
    .panel-head h2 .platform-title {{
      font-size: 1.12rem;
      font-weight: 650;
      color: var(--navy);
    }}
    .paid-ad-overview {{
      margin-bottom: 16px;
    }}
    .paid-ad-metrics-grid {{
      display: grid;
      grid-template-columns: repeat(8, minmax(0, 1fr));
      gap: 12px;
    }}
    @media (max-width: 1400px) {{
      .paid-ad-metrics-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    }}
    @media (max-width: 900px) {{
      .paid-ad-metrics-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    .client-insights-section {{
      margin-top: 16px;
      margin-bottom: 16px;
    }}
    .client-insights-heading {{
      margin-bottom: 12px;
    }}
    .client-insights-pill::before {{
      background: #22c55e;
    }}
    .client-insights-count {{
      margin-left: 8px;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.16);
      font-size: 0.62rem;
      letter-spacing: 0.04em;
    }}
    .client-insights-doc-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;
    }}
    .client-insights-doc-card {{
      display: block;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      text-decoration: none;
      color: inherit;
      box-shadow: var(--shadow-sm);
      transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
    }}
    .client-insights-doc-card:hover {{
      border-color: var(--accent);
      box-shadow: var(--shadow);
      transform: translateY(-1px);
    }}
    .client-insights-doc-period {{
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .client-insights-doc-title {{
      font-size: 0.92rem;
      font-weight: 650;
      color: var(--navy);
      line-height: 1.35;
      margin-bottom: 6px;
    }}
    .client-insights-doc-meta {{
      font-size: 0.78rem;
      color: var(--muted);
    }}
    .client-insights-empty {{
      margin: 0;
      font-size: 0.9rem;
    }}
    .ga4-pages-pagination {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px 16px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .ga4-pages-pagination-info {{
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .ga4-pages-pagination-controls {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .ga4-pages-page-btn {{
      appearance: none;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--navy);
      border-radius: 8px;
      padding: 6px 12px;
      font: inherit;
      font-size: 0.84rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .ga4-pages-page-btn:hover:not(:disabled) {{
      border-color: var(--accent);
      color: var(--accent);
    }}
    .ga4-pages-page-btn:disabled {{
      opacity: 0.45;
      cursor: not-allowed;
    }}
    .ga4-pages-page-btn.active {{
      background: var(--navy);
      border-color: var(--navy);
      color: #fff;
    }}
    .insights-panel {{
      background: linear-gradient(135deg, #fff 0%, #fffbf5 100%);
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      padding: 20px 22px;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }}
    .insights-head {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .insights-title {{
      margin: 0;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .insights-body {{
      flex: 1;
      font-size: 0.9rem;
      line-height: 1.55;
      color: var(--text);
      overflow: auto;
      max-height: 220px;
    }}
    .insights-list {{
      margin: 0;
      padding-left: 1.15rem;
    }}
    .insights-list li {{
      margin: 0 0 0.45rem;
    }}
    .insights-list li:last-child {{ margin-bottom: 0; }}
    .insights-para {{
      margin: 0 0 0.5rem;
    }}
    .insights-para:last-child {{ margin-bottom: 0; }}
    .insights-empty {{
      margin: 0;
      font-size: 0.88rem;
      line-height: 1.5;
    }}
    .insights-foot {{
      margin: 12px 0 0;
      font-size: 0.72rem;
    }}
    .insights-editor {{
      margin-top: 20px;
      padding-top: 16px;
      border-top: 1px solid var(--border);
    }}
    .insights-editor-title {{
      margin: 0 0 6px;
      font-size: 0.95rem;
      font-weight: 650;
      color: var(--navy);
    }}
    .insights-editor-hint {{
      margin: 0 0 10px;
      font-size: 0.82rem;
    }}
    .insights-editor-meta {{
      margin: 0 0 8px;
      font-size: 0.78rem;
    }}
    .insights-textarea {{
      width: 100%;
      min-height: 120px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font: inherit;
      font-size: 0.86rem;
      line-height: 1.45;
      resize: vertical;
      margin-bottom: 10px;
    }}
    .insights-save-btn {{
      width: 100%;
    }}
    .ga4-pages-panel {{
      margin-bottom: 20px;
    }}
    .ga4-pages-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 0 22px;
      margin-bottom: 8px;
    }}
    .ga4-pages-search-wrap {{
      flex: 1;
      min-width: 220px;
      max-width: 440px;
    }}
    .ga4-pages-search {{
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font: inherit;
      background: #fff;
    }}
    .ga4-pages-search:focus {{
      outline: 2px solid rgba(11, 92, 171, 0.25);
      border-color: var(--accent);
    }}
    .ga4-pages-panel .table-note {{
      padding: 0 22px;
    }}
    .ga4-pages-panel .table-wrap {{
      padding: 0 22px 22px;
    }}
    .ga4-pages-table td.page-path {{
      max-width: 300px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.8rem;
    }}
    .ga4-pages-table td.page-title {{
      max-width: 260px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .ga4-pages-empty {{
      padding: 0 22px 22px;
      margin: 0;
    }}
    .visually-hidden {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
    .total-spend-panel {{
      background: linear-gradient(135deg, #fff 0%, #f8fbff 100%);
      border: 1px solid var(--border);
      border-left: 4px solid var(--gold);
      padding: 24px 28px;
    }}
    .total-spend-head {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }}
    .total-spend-title {{
      margin: 0;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .total-spend-hero {{
      font-size: clamp(2.25rem, 4vw, 3rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--navy);
      line-height: 1.1;
      margin: 8px 0 20px;
      font-variant-numeric: tabular-nums;
    }}
    .total-spend-metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px 28px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }}
    .total-spend-metric {{
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 88px;
    }}
    .total-spend-metric__val {{
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--navy);
      font-variant-numeric: tabular-nums;
    }}
    .total-spend-metric__lbl {{
      font-size: 0.72rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .card-value {{ font-size: 1.75rem; font-weight: 700; margin: 8px 0 4px; letter-spacing: -0.02em; }}
    .card-stats {{ display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 0.84rem; color: var(--muted); }}
    .chart-stack {{ display: flex; flex-direction: column; gap: 24px; }}
    .chart-legend-note {{
      margin: 12px 0 0;
      font-size: 0.78rem;
      color: var(--muted);
    }}
    .site-impact {{
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .site-impact-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 16px;
      font-size: 0.84rem;
    }}
    .site-impact-label {{
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
    }}
    .site-impact-empty {{ font-size: 0.84rem; }}
    .site-impact-details {{
      margin-top: 10px;
      font-size: 0.84rem;
    }}
    .site-impact-details summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 600;
      user-select: none;
    }}
    .site-impact-details .table-wrap {{ margin-top: 10px; }}
    .data-table.compact th, .data-table.compact td {{ padding: 8px 10px; font-size: 0.82rem; }}
    .chart-stack {{ display: flex; flex-direction: column; gap: 24px; }}
    .performance-trend-panel {{
      border-top: 3px solid var(--organic);
    }}
    .performance-trend-head {{
      align-items: flex-start;
      gap: 16px;
    }}
    .performance-trend-head .panel-head-text {{
      flex: 1;
      min-width: 200px;
    }}
    .performance-trend-head h2 {{
      margin: 0 0 6px;
    }}
    .performance-trend-desc {{
      margin: 0;
      font-size: 0.84rem;
      line-height: 1.45;
      max-width: 640px;
    }}
    .performance-trend-controls {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
      gap: 10px 12px;
      flex-shrink: 0;
    }}
    .metric-toggle-group {{
      display: inline-flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
    }}
    .metric-toggle {{
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--muted);
      padding: 7px 14px;
      border-radius: 999px;
      font-size: 0.84rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s, box-shadow 0.12s;
      white-space: nowrap;
    }}
    .metric-toggle:hover {{
      border-color: #b8c4d4;
      color: var(--navy);
    }}
    .metric-toggle.active {{
      font-weight: 600;
      box-shadow: 0 1px 3px rgba(10, 37, 64, 0.1);
    }}
    .metric-toggle[data-metric="spend"].active {{
      background: #eef1f5;
      border-color: var(--navy);
      color: var(--navy);
    }}
    .metric-toggle[data-metric="clicks"].active {{
      background: var(--google-bg);
      border-color: var(--google);
      color: var(--google);
    }}
    .metric-toggle[data-metric="impressions"].active {{
      background: var(--linkedin-bg);
      border-color: var(--linkedin);
      color: var(--linkedin);
    }}
    .metric-toggle[data-metric="conversions"].active {{
      background: var(--organic-bg);
      border-color: var(--organic);
      color: var(--organic);
    }}
    .performance-trend-chart-wrap {{
      position: relative;
      min-height: 320px;
    }}
    .performance-trend-empty {{
      display: none;
      padding: 48px 16px;
      text-align: center;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .performance-trend-empty.show {{
      display: block;
    }}
    .budget-pacing-panel {{
      margin-top: 18px;
    }}
    .budget-pacing-controls {{
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 6px;
      min-width: 220px;
    }}
    .budget-pacing-controls label {{
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .budget-pacing-input-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .budget-pacing-currency {{
      color: var(--muted);
      font-weight: 600;
    }}
    #budgetPacingInput {{
      width: 140px;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 0.95rem;
    }}
    .budget-pacing-save-form {{
      margin: 0;
    }}
    .budget-pacing-slicers {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px 16px;
      padding: 0 22px 14px;
    }}
    .budget-pacing-slicers .filter-column-label {{
      margin-bottom: 0;
    }}
    .budget-pacing-stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      padding: 0 22px 16px;
    }}
    .budget-pacing-stat strong.over {{ color: #b45309; }}
    .budget-pacing-stat strong.under {{ color: #0f766e; }}
    .budget-pacing-guidance {{
      margin: 0 22px 16px;
      padding: 14px 16px;
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 0.9rem;
      color: var(--text);
      line-height: 1.55;
    }}
    .budget-pacing-guidance .guidance-platforms {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 0.84rem;
    }}
    .budget-pacing-stat {{
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
    }}
    .budget-pacing-stat-label {{
      display: block;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .budget-pacing-chart-wrap {{
      padding: 0 22px 22px;
    }}
    @media (max-width: 900px) {{
      .budget-pacing-stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .budget-pacing-controls {{ align-items: stretch; width: 100%; }}
      .budget-pacing-input-row {{ justify-content: flex-start; }}
    }}
    .chart-subhead {{
      margin: 0 0 8px;
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--navy);
    }}
    .chart-period-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 0;
      padding: 3px;
      background: #eef3f9;
      border: 1px solid var(--border);
      border-radius: 10px;
    }}
    .chart-period-btn {{
      appearance: none;
      border: none;
      background: transparent;
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 8px;
      cursor: pointer;
      transition: background 0.12s, color 0.12s, box-shadow 0.12s;
    }}
    .chart-period-btn:hover {{
      color: var(--navy);
    }}
    .chart-period-btn.active {{
      background: #fff;
      color: var(--navy);
      box-shadow: 0 1px 3px rgba(10, 37, 64, 0.1);
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 22px 24px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-sm);
    }}
    .panel.aggregated {{
      background: linear-gradient(to right, #fafbfd, var(--panel));
      border-left: 4px solid var(--gold);
    }}
    .panel-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 16px;
      margin-bottom: 12px;
    }}
    h2 {{ margin: 0; font-size: 1.12rem; font-weight: 650; letter-spacing: -0.01em; }}
    .platform-panel {{ border-left: 4px solid var(--border); }}
    .platform-google {{ border-left-color: var(--google); }}
    .platform-linkedin {{ border-left-color: var(--linkedin); }}
    .platform-meta {{ border-left-color: var(--meta); }}
    .badge {{
      font-size: 0.72rem;
      font-weight: 600;
      background: #eef3f9;
      color: var(--accent);
      padding: 3px 10px;
      border-radius: 999px;
      white-space: nowrap;
    }}
    .table-wrap {{ overflow-x: auto; margin: 0 -4px; }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    .data-table th, .data-table td {{ padding: 11px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
    .data-table th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      background: #f8fafc;
      position: sticky;
      top: 0;
    }}
    .data-table th.sortable {{
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      transition: color 0.12s;
    }}
    .data-table th.sortable:hover {{ color: var(--accent); }}
    .data-table th.sortable.sort-active {{ color: var(--accent); }}
    .sort-icon {{
      display: inline-block;
      width: 0.9em;
      margin-left: 2px;
      opacity: 0.35;
      font-size: 0.85em;
    }}
    .data-table th.sortable.sort-active .sort-icon {{ opacity: 1; }}
    .data-table th.sort-active[data-sort-dir="asc"] .sort-icon::before {{ content: '↑'; }}
    .data-table th.sort-active[data-sort-dir="desc"] .sort-icon::before {{ content: '↓'; }}
    .data-table th.sortable:not(.sort-active) .sort-icon::before {{ content: '↕'; }}
    .data-table tbody tr:last-child td {{ border-bottom: none; }}
    .data-table th.ga4-col, .data-table td.ga4-col {{
      border-left: 1px solid var(--border);
      background: rgba(184, 146, 46, 0.04);
    }}
    .data-table thead th.ga4-col {{
      color: #8a6d1f;
      font-size: 0.78rem;
      white-space: nowrap;
    }}
    .data-table td.ga4-col.muted {{ color: #9aa3ad; }}
    td.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    td.name {{ max-width: 340px; font-weight: 500; }}
    td.chevron, th.chevron-col {{
      width: 28px;
      padding-right: 0;
      color: var(--muted);
      font-size: 1.1rem;
    }}
    tr.tree-row {{ transition: background 0.12s; }}
    tr.tree-expandable {{ cursor: pointer; }}
    tr.tree-expandable:hover {{ background: #f0f5fb; }}
    tr.tree-row.expanded {{ background: #e8f0fa; }}
    tr.tree-row.expanded > td {{ border-bottom-color: #c5d9ef; }}
    tr.tree-empty {{ background: #fafbfc; }}
    tr.tree-empty td {{ font-size: 0.84rem; font-style: italic; }}
    tr.tree-depth-1 {{ background: #fafcfe; }}
    tr.tree-depth-2 {{ background: #f5f9fd; }}
    tr.tree-depth-3 {{ background: #f0f6fc; }}
    .tree-chevron {{
      display: inline-block;
      width: 1em;
      color: var(--accent);
      font-size: 0.85rem;
      font-weight: 700;
      transition: transform 0.15s;
    }}
    .tree-chevron.leaf {{ visibility: hidden; }}
    tr.expanded .tree-chevron {{ transform: rotate(90deg); }}
    td.name {{ max-width: 420px; }}
    .name-inner {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .ad-thumb {{
      width: 40px;
      height: 40px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--border);
      flex-shrink: 0;
      background: #f0f4f8;
      display: block;
    }}
    .ad-thumb-btn {{
      position: relative;
      appearance: none;
      border: none;
      padding: 0;
      background: none;
      cursor: pointer;
      flex-shrink: 0;
      border-radius: 8px;
      line-height: 0;
    }}
    .ad-thumb-btn:hover .ad-thumb {{ box-shadow: 0 0 0 2px var(--accent); }}
    .ad-thumb-btn.has-video .ad-play-icon {{
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.65rem;
      color: #fff;
      background: rgba(0,0,0,0.45);
      border-radius: 8px;
      pointer-events: none;
    }}
    .creative-preview {{
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .creative-preview[hidden] {{ display: none !important; }}
    .creative-preview-backdrop {{
      position: absolute;
      inset: 0;
      background: rgba(10, 37, 64, 0.72);
    }}
    .creative-preview-dialog {{
      position: relative;
      background: #fff;
      border-radius: 14px;
      max-width: min(720px, 96vw);
      max-height: 90vh;
      overflow: hidden;
      box-shadow: var(--shadow);
      width: 100%;
    }}
    .creative-preview-close {{
      position: absolute;
      top: 8px;
      right: 10px;
      z-index: 2;
      appearance: none;
      border: none;
      background: rgba(0,0,0,0.55);
      color: #fff;
      width: 32px;
      height: 32px;
      border-radius: 999px;
      font-size: 1.25rem;
      line-height: 1;
      cursor: pointer;
    }}
    .creative-preview-body {{
      background: #000;
      min-height: 200px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .creative-preview-body iframe,
    .creative-preview-body video,
    .creative-preview-body img {{
      display: block;
      max-width: 100%;
      max-height: 80vh;
      width: 100%;
    }}
    .creative-preview-body img {{ background: #fff; }}
    .creative-preview-caption {{
      padding: 12px 16px;
      font-size: 0.84rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
    }}
    .ad-creative-sub {{
      display: block;
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 2px;
    }}
    .entity-tag {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
      margin-right: 6px;
    }}
    .drill-hint {{
      margin: 0 0 12px;
      font-size: 0.82rem;
      color: var(--accent);
      font-weight: 500;
    }}
    .table-note {{ margin: 0 0 10px; font-size: 0.82rem; color: var(--muted); line-height: 1.4; }}
    .aggregated-stats {{ display: flex; flex-wrap: wrap; gap: 16px 28px; font-size: 1.02rem; margin-top: 4px; }}
    .aggregated-stats strong {{ font-size: 1.35rem; color: var(--navy); }}
    .errors {{
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: var(--radius);
      padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 0.88rem;
    }}
    .errors ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .dash-flash {{
      background: var(--ok-bg);
      border: 1px solid #b8dfc8;
      border-radius: var(--radius);
      padding: 12px 14px;
      margin-bottom: 16px;
      font-size: 0.9rem;
      color: var(--ok);
    }}
    canvas {{ max-height: 280px; }}
    .muted {{ color: var(--muted); }}
    .refresh-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }}
    .refresh-actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .refresh-form {{ margin: 0; }}
    .refresh-btn {{
      padding: 9px 18px;
      border-radius: 9px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font-size: 0.88rem;
      font-weight: 600;
      transition: background 0.15s;
    }}
    .refresh-btn:hover:not(:disabled) {{ filter: brightness(1.08); }}
    .refresh-btn:disabled {{ opacity: 0.45; cursor: not-allowed; background: #94a3b8; border-color: #94a3b8; }}
    .refresh-btn--secondary {{
      background: #fff; color: var(--accent); border-color: var(--accent);
    }}
    .refresh-btn--secondary:hover:not(:disabled) {{ background: #f0f7ff; filter: none; }}
    .notice {{ font-size: 0.86rem; color: var(--muted); }}

    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .view-panel {{ display: none; }}
    .view-panel.active {{ display: block; }}
    .dash-sticky-chrome {{
      background: var(--bg);
      padding: 20px 0 0;
    }}
    .dash-page-header {{
      width: 100%;
      max-width: none;
      margin: 0 auto;
      padding: 0 32px;
    }}
    .dash-view-nav {{
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 8px;
      padding: 0 0 20px;
      background: transparent;
      border-bottom: none;
    }}
    .dash-view-btn {{
      appearance: none;
      border: 1px solid transparent;
      background: #f3f4f6;
      color: var(--muted);
      font-size: 0.88rem;
      font-weight: 650;
      padding: 10px 18px;
      border-radius: 999px;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s, background 0.15s;
    }}
    .dash-view-btn:hover {{
      color: var(--navy);
      background: #e5e7eb;
    }}
    .dash-view-btn.active {{
      color: var(--navy);
      background: #f0f9ff;
      border-color: #93c5fd;
    }}
    .dash-filters-bar {{
      padding: 16px 0 18px;
      background: transparent;
    }}
    .dash-filters-bar:empty {{
      display: none;
    }}
    .global-filters {{
      padding: 12px 16px 14px;
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: none;
      background: var(--surface);
    }}
    .global-filters.is-collapsed {{
      padding-bottom: 10px;
    }}
    .global-filters-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .global-filters.is-collapsed .global-filters-head {{
      margin-bottom: 0;
    }}
    .global-filters.is-collapsed .global-filters-body {{
      display: none;
    }}
    .filters-collapse-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      appearance: none;
      border: none;
      background: none;
      font: inherit;
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--navy);
      cursor: pointer;
      padding: 2px 0;
    }}
    .filters-collapse-btn:hover {{
      color: var(--accent);
    }}
    .filters-chevron {{
      width: 14px;
      height: 14px;
      flex-shrink: 0;
      transition: transform 0.2s ease;
    }}
    .global-filters.is-collapsed .filters-chevron {{
      transform: rotate(-90deg);
    }}
    .global-filter-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px 28px;
      align-items: start;
    }}
    .global-filter-grid--single {{
      grid-template-columns: 1fr;
    }}
    .global-filter-grid--triple {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .filter-column {{
      min-width: 0;
    }}
    .filter-column--locked {{
      opacity: 0.72;
    }}
    .filter-column--locked .filter-column-label {{
      color: #94a3b8;
    }}
    .filter-range-locked {{
      pointer-events: none;
    }}
    .filter-toggle--locked {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      cursor: not-allowed;
      background: #f1f5f9;
      border-color: #cbd5e1;
      color: #64748b;
      font-weight: 600;
      opacity: 1;
      box-shadow: none;
    }}
    .filter-toggle--locked:disabled {{
      opacity: 1;
    }}
    .filter-lock-icon {{
      width: 13px;
      height: 13px;
      flex-shrink: 0;
      opacity: 0.85;
    }}
    .filter-column-label {{
      display: block;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .global-filters-head .filter-status {{
      font-size: 0.78rem;
      color: var(--muted);
      text-align: right;
      flex: 1;
      min-width: 0;
      line-height: 1.35;
    }}
    .filter-more-options {{
      margin-top: 12px;
      border-top: 1px solid var(--border);
    }}
    .filter-more-options summary {{
      font-size: 0.76rem;
      font-weight: 600;
      color: var(--muted);
      cursor: pointer;
      padding: 8px 0 6px;
      user-select: none;
      list-style: none;
    }}
    .filter-more-options summary::-webkit-details-marker {{
      display: none;
    }}
    .filter-more-options summary::before {{
      content: '+';
      display: inline-block;
      width: 14px;
      margin-right: 4px;
      font-weight: 700;
    }}
    .filter-more-options[open] summary::before {{
      content: '−';
    }}
    .filter-more-options .filter-zero-spend {{
      margin: 0 0 4px;
      padding: 0;
      border-top: 0;
      font-size: 0.8rem;
    }}
    .global-filter-rows {{
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px 14px;
    }}
    .filter-row .filter-label {{
      min-width: 92px;
      margin: 0;
      flex-shrink: 0;
    }}
    .filter-toggles {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      flex: 1;
      min-width: 0;
    }}
    .filter-column--website-search {{
      min-width: 240px;
    }}
    .filter-column--website-search .ga4-pages-search {{
      width: 100%;
      min-width: 220px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font: inherit;
      background: #fff;
    }}
    .filter-column--website-search .ga4-pages-search:focus {{
      outline: 2px solid color-mix(in srgb, var(--accent) 24%, transparent);
      border-color: var(--accent);
    }}
    .website-results-status {{
      display: flex;
      justify-content: flex-end;
      margin: -4px 0 8px;
    }}
    .ga4-traffic-filter-note {{
      margin: 0 0 14px;
      font-size: 0.86rem;
    }}
    .ga4-metrics-section {{
      margin-bottom: 20px;
    }}
    .ga4-metrics-heading {{
      margin-bottom: 12px;
    }}
    .ga4-metrics-pill {{
      display: inline-flex;
      align-items: center;
      gap: 0;
      background: var(--navy);
      color: #fff;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      padding: 6px 14px 6px 0;
      border-radius: 999px;
      overflow: hidden;
    }}
    .ga4-metrics-pill::before {{
      content: '';
      display: inline-block;
      width: 4px;
      align-self: stretch;
      background: #22c55e;
      margin-right: 10px;
      border-radius: 999px 0 0 999px;
    }}
    .ga4-metrics-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
    }}
    @media (max-width: 1200px) {{
      .ga4-metrics-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .ga4-metrics-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    .ga4-metric-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: var(--shadow-sm);
      min-width: 0;
    }}
    .ga4-metric-label {{
      font-size: 0.68rem;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 8px;
    }}
    .ga4-metric-value {{
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--navy);
      line-height: 1.15;
    }}
    .ga4-metric-sub {{
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 8px;
    }}

    .filter-panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 18px;
      box-shadow: var(--shadow-sm);
    }}
    .bl-merged-section {{
      margin-top: 0;
    }}
    .campaign-explorer-section {{
      margin-top: 0;
    }}
    .bl-filter-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px 24px;
    }}
    @media (max-width: 960px) {{
      .bl-filter-grid {{ grid-template-columns: 1fr; }}
    }}
    .bl-filter-footer {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px 16px;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
    }}
    .bl-filter-footer .filter-zero-spend {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }}
    .bl-filter-footer .filter-status {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
      flex: 1;
      min-width: 200px;
      text-align: right;
    }}
    @media (max-width: 720px) {{
      .bl-filter-footer .filter-status {{ text-align: left; }}
    }}
    .filter-checks--wrap {{
      flex-direction: row;
      flex-wrap: wrap;
      gap: 6px 10px;
      max-height: 120px;
      overflow-y: auto;
      padding-right: 4px;
    }}
    .filter-group-head {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .filter-group-head .filter-label {{
      min-width: 0;
      flex: 1;
    }}
    .filter-link {{
      appearance: none;
      border: none;
      background: none;
      color: var(--accent);
      font-size: 0.76rem;
      font-weight: 600;
      cursor: pointer;
      padding: 2px 4px;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .filter-link:hover {{ color: var(--navy); }}
    .filter-checks {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      max-height: none;
      overflow: visible;
      padding-right: 0;
    }}
    .filter-check {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.86rem;
      cursor: pointer;
      user-select: none;
      padding: 4px 6px;
      border-radius: 8px;
    }}
    .filter-check:hover {{ background: #f4f7fb; }}
    .filter-check input {{
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
      flex-shrink: 0;
    }}
    .filter-zero-spend {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      font-size: 0.84rem;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
    }}
    .filter-zero-spend input {{
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
    }}
    .filter-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px 20px;
    }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 10px;
      flex: 1;
      min-width: 260px;
    }}
    .filter-label {{
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--muted);
      min-width: 88px;
    }}
    .filter-toggle {{
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--text);
      padding: 7px 14px;
      border-radius: 999px;
      font-size: 0.84rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s, box-shadow 0.12s;
      white-space: nowrap;
    }}
    .filter-toggle:hover {{ border-color: #b8c4d4; background: #f8fafc; }}
    .filter-toggle.active {{
      color: #fff;
      font-weight: 600;
      box-shadow: 0 1px 3px rgba(10, 37, 64, 0.12);
    }}
    .filter-toggle--all {{
      border-color: var(--navy);
      color: var(--navy);
      font-weight: 600;
    }}
    .filter-toggle--all:hover {{
      background: #eef1f5;
      border-color: var(--navy);
    }}
    .filter-toggle--all.active {{
      background: var(--navy);
      border-color: var(--navy);
      color: #fff;
    }}
    .filter-toggle.t-google {{
      border-color: color-mix(in srgb, var(--google) 55%, var(--border));
      color: var(--google);
    }}
    .filter-toggle.t-google:hover {{
      background: #eef4ff;
      border-color: var(--google);
    }}
    .filter-toggle.t-google.active {{
      background: var(--google);
      border-color: var(--google);
      color: #fff;
    }}
    .filter-toggle.t-linkedin {{
      border-color: color-mix(in srgb, var(--linkedin) 55%, var(--border));
      color: var(--linkedin);
    }}
    .filter-toggle.t-linkedin:hover {{
      background: var(--linkedin-bg);
      border-color: var(--linkedin);
    }}
    .filter-toggle.t-linkedin.active {{
      background: var(--linkedin);
      border-color: var(--linkedin);
      color: #fff;
    }}
    .filter-toggle.t-meta {{
      border-color: color-mix(in srgb, var(--meta) 55%, var(--border));
      color: var(--meta);
    }}
    .filter-toggle.t-meta:hover {{
      background: var(--meta-bg);
      border-color: var(--meta);
    }}
    .filter-toggle.t-meta.active {{
      background: var(--meta);
      border-color: var(--meta);
      color: #fff;
    }}
    .filter-toggle.t-organic {{
      border-color: color-mix(in srgb, var(--organic) 55%, var(--border));
      color: var(--organic);
    }}
    .filter-toggle.t-organic:hover {{
      background: var(--organic-bg);
      border-color: var(--organic);
    }}
    .filter-toggle.t-organic.active {{
      background: var(--organic);
      border-color: var(--organic);
      color: #fff;
    }}
    .filter-toggle.t-bl {{
      border-color: color-mix(in srgb, var(--business-line) 50%, var(--border));
      color: var(--business-line);
    }}
    .filter-toggle.t-bl:hover {{
      background: var(--business-line-bg);
      border-color: var(--business-line);
    }}
    .filter-toggle.t-bl.active {{
      background: var(--business-line);
      border-color: var(--business-line);
      color: #fff;
    }}
    .filter-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }}
    .filter-reset {{
      appearance: none;
      border: none;
      background: none;
      color: var(--accent);
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      padding: 6px 4px;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .filter-reset:hover {{ color: var(--navy); }}
    .filter-status {{
      font-size: 0.82rem;
      color: var(--muted);
    }}
    .bl-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .bl-stat {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: var(--shadow-sm);
    }}
    .bl-stat-val {{ font-size: 1.35rem; font-weight: 700; }}
    .bl-stat-lbl {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin-top: 4px; }}
    .attr-platform-section {{ margin-top: 8px; }}
    .attr-platform-summary {{ margin: 16px 0; }}
    .attr-subhead {{
      margin: 20px 0 10px;
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .platform-pill {{
      display: inline-block;
      font-size: 0.68rem;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 999px;
      text-transform: uppercase;
      letter-spacing: .03em;
    }}
    .platform-pill.google {{ background: var(--google-bg); color: var(--google); }}
    .platform-pill.meta {{ background: var(--meta-bg); color: var(--meta); }}
    .platform-pill.linkedin {{ background: var(--linkedin-bg); color: var(--linkedin); }}
    .platform-pill.organic {{ background: var(--organic-bg); color: var(--organic); }}
    .bl-tag {{
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--business-line);
      background: var(--business-line-bg);
      padding: 2px 8px;
      border-radius: 6px;
      white-space: nowrap;
    }}
    .bl-filters h3 {{
      margin: 0 0 14px;
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .bl-filters .filter-group + .filter-group {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }}
    .andre-toast {{
      position: fixed;
      bottom: 28px;
      left: 50%;
      transform: translateX(-50%) translateY(12px);
      background: var(--navy);
      color: #fff;
      padding: 12px 20px;
      border-radius: 999px;
      font-size: 0.92rem;
      font-weight: 600;
      box-shadow: 0 8px 32px rgba(10, 37, 64, 0.28);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.35s ease, transform 0.35s ease;
      z-index: 200;
    }}
    .andre-toast.show {{
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }}
    @media (max-width: 900px) {{
      .dash-page-header {{ padding: 0 16px; }}
      .dash-view-nav {{ gap: 6px; padding-bottom: 16px; }}
      .dash-view-btn {{ padding: 9px 14px; font-size: 0.84rem; }}
      .dash-filters-bar {{ padding: 14px 0 16px; }}
      .global-filter-grid {{ grid-template-columns: 1fr; gap: 14px; }}
      .global-filters-head .filter-status {{ text-align: left; }}
      .dash-content {{ padding: 18px 16px 40px; }}
    }}
    {_GSC_CSS}
  </style>
</head>
<body>
  <div class="app-shell">
    {top_header}
    <div class="dash-main">
      <div class="dash-sticky-chrome">
        <div class="dash-page-header">
          {view_tabs_html}
          {filters_bar_html}
        </div>
      </div>

      <div class="dash-content">
        <div class="wrap">
          {flash_html}
          {view_notice_html}
          {error_html}

          <div id="view-overview" class="view-panel active" role="tabpanel">
            {overview_paid_html}

            <div class="cards">
              {_summary_cards_html(totals, summary_platform_ids)}
            </div>

            {performance_trend_html}

            {budget_pacing_html}
          </div>

          {campaign_explorer_panel}

          {gsc_tab_panel}

          {website_tab_panel}
        </div>
      </div>
    </div>
  </div>
  <div id="creativePreview" class="creative-preview" hidden>
    <div class="creative-preview-backdrop" data-close-preview></div>
    <div class="creative-preview-dialog" role="dialog" aria-modal="true" aria-label="Creative preview">
      <button type="button" class="creative-preview-close" data-close-preview aria-label="Close">×</button>
      <div class="creative-preview-body" id="creativePreviewBody"></div>
      <div class="creative-preview-caption" id="creativePreviewCaption"></div>
    </div>
  </div>
  <div class="andre-toast" id="andreToast" role="status" aria-live="polite" hidden>Hello Andre</div>
  <script type="application/json" id="budget-pacing-chart-data">{budget_pacing_json}</script>
  <script type="application/json" id="performance-chart-data">{performance_chart_json}</script>
  <script type="application/json" id="metric-defs-data">{metric_defs_json}</script>
  <script type="application/json" id="paid-overview-metrics-data">{paid_overview_metrics_json}</script>
  <script type="application/json" id="breakdowns-data">{breakdowns_json}</script>
  <script type="application/json" id="ga4-campaign-metrics">{ga4_campaign_metrics_json}</script>
  <script type="application/json" id="bl-campaigns-data">{bl_campaigns_json}</script>
  <script type="application/json" id="bl-catalog-data">{bl_catalog_json}</script>
  <script type="application/json" id="product-line-catalog-data">{product_line_catalog_json}</script>
  <script type="application/json" id="platform-catalog-data">{platform_catalog_json}</script>
  <script type="application/json" id="ga4-pages-data">{ga4_pages_json}</script>
  <script type="application/json" id="ga4-pages-by-platform-data">{ga4_pages_by_platform_json}</script>
  <script type="application/json" id="ga4-landing-labels-data">{ga4_landing_labels_json}</script>
  <script type="application/json" id="ga4-summary-data">{ga4_summary_json}</script>
  <script>
    function readJson(id, fallback) {{
      const el = document.getElementById(id);
      if (!el) return fallback;
      try {{
        return JSON.parse(el.textContent || 'null') ?? fallback;
      }} catch (err) {{
        console.error('Failed to parse', id, err);
        return fallback;
      }}
    }}

    const SHOW_SEGMENT_FILTERS = {'true' if show_segment_filters else 'false'};
    const SHOW_PRODUCT_LINE_FILTERS = {'true' if show_product_line_filters else 'false'};
    const GA4_SEGMENT_FILTER_LABEL = {_json_for_html_script(seg_filter_label)};
    const SHOW_BUDGET_PACING = {'true' if show_budget_pacing else 'false'};
    const SHOW_PERFORMANCE_TREND = {'true' if features.performance_trend else 'false'};
    const SHOW_CAMPAIGN_EXPLORER = {'true' if features.campaign_explorer else 'false'};
    const SHOW_WEBSITE_ANALYTICS = {'true' if show_website_tab else 'false'};
    const SHOW_GSC = {'true' if _gsc_data else 'false'};
    const PAID_PLATFORM_IDS = new Set(['google', 'linkedin', 'meta']);
    const SEGMENT_FILTER_ALL_LABEL = {_json_for_html_script(seg_filter_all_label)};
    const PRODUCT_LINE_FILTER_ALL_LABEL = "All product lines";

    const breakdowns = readJson('breakdowns-data', {{}});
    const ga4CampaignMetrics = readJson('ga4-campaign-metrics', {{}});
    const blCampaigns = readJson('bl-campaigns-data', []);
    const blCatalog = readJson('bl-catalog-data', []);
    const productLineCatalog = readJson('product-line-catalog-data', []);
    const platformCatalog = readJson('platform-catalog-data', []);
    const ga4Pages = readJson('ga4-pages-data', []);
    const ga4PagesByPlatform = readJson('ga4-pages-by-platform-data', {{}});
    const ga4LandingLabels = readJson('ga4-landing-labels-data', {{}});
    const ga4SiteSummary = readJson('ga4-summary-data', {{}});

    const channelState = new Set();
    const blState = new Set();
    const productLineState = new Set();

    const FILTER_GROUPS = {{
      channel: {{ state: channelState, catalog: platformCatalog, wrapId: 'channelFilters' }},
      bl: {{ state: blState, catalog: blCatalog, wrapId: 'blFilters' }},
      product: {{ state: productLineState, catalog: productLineCatalog, wrapId: 'productLineFilters' }},
    }};

    function isAllSelected(state, catalog) {{
      return !catalog.length || state.size === 0 || state.size === catalog.length;
    }}

    function effectiveFilterIds(state, catalog) {{
      if (isAllSelected(state, catalog)) return catalog.map(item => item.id);
      return [...state];
    }}

    function channelFilterRestricts() {{
      return channelState.size > 0 && channelState.size < platformCatalog.length;
    }}

    function paidPlatformsInCatalog() {{
      return platformCatalog.filter(item => PAID_PLATFORM_IDS.has(item.id));
    }}

    function selectedPaidChannelIds() {{
      return effectiveFilterIds(channelState, platformCatalog).filter(id => PAID_PLATFORM_IDS.has(id));
    }}

    function paidChannelFilterActive() {{
      const paidCatalog = paidPlatformsInCatalog();
      if (!paidCatalog.length || !channelFilterRestricts()) return false;
      const selected = selectedPaidChannelIds();
      if (!selected.length) return true;
      return selected.length < paidCatalog.length;
    }}

    function blFilterRestricts() {{
      return SHOW_SEGMENT_FILTERS && blCatalog.length && !isAllSelected(blState, blCatalog);
    }}

    function productFilterRestricts() {{
      return SHOW_PRODUCT_LINE_FILTERS
        && productLineCatalog.length
        && !isAllSelected(productLineState, productLineCatalog);
    }}

    function segmentFiltersRestrict() {{
      return blFilterRestricts() || productFilterRestricts();
    }}

    function includeZeroSpendCampaigns() {{
      return !!document.getElementById('showZeroSpend')?.checked;
    }}

    function filteredCampaignsForSegmentFilters() {{
      const showZeroSpend = includeZeroSpendCampaigns();
      const bls = SHOW_SEGMENT_FILTERS
        ? effectiveFilterIds(blState, blCatalog)
        : null;
      const products = SHOW_PRODUCT_LINE_FILTERS
        ? effectiveFilterIds(productLineState, productLineCatalog)
        : null;
      if (SHOW_SEGMENT_FILTERS && (!blCatalog.length || !bls.length)) return [];
      if (SHOW_PRODUCT_LINE_FILTERS && (!productLineCatalog.length || !products.length)) return [];
      return blCampaigns.filter(r => {{
        if (SHOW_SEGMENT_FILTERS && !rowMatchesSegmentFilter(r, bls)) return false;
        if (SHOW_PRODUCT_LINE_FILTERS && !rowMatchesProductLineFilter(r, products)) return false;
        if (!showZeroSpend && (r.spend || 0) < 0.01) return false;
        return true;
      }});
    }}

    function platformsWithSegmentCampaigns() {{
      const rows = filteredCampaignsForSegmentFilters();
      return new Set(rows.map(r => r.platform).filter(id => PAID_PLATFORM_IDS.has(id)));
    }}

    function chartPlatformScale(platformId) {{
      if (!segmentFiltersRestrict()) return 1;
      const showZeroSpend = includeZeroSpendCampaigns();
      const segmentRows = filteredCampaignsForSegmentFilters().filter(r => r.platform === platformId);
      const allRows = blCampaigns.filter(r => {{
        if (r.platform !== platformId) return false;
        if (!showZeroSpend && (r.spend || 0) < 0.01) return false;
        return true;
      }});
      const filteredSpend = segmentRows.reduce((sum, r) => sum + (r.spend || 0), 0);
      const totalSpend = allRows.reduce((sum, r) => sum + (r.spend || 0), 0);
      if (totalSpend < 0.01) return 0;
      return filteredSpend / totalSpend;
    }}

    function activeChartPlatforms() {{
      let ids = selectedPaidChannelIds();
      const fromMetrics = performanceChartRaw.metrics?.clicks
        ? Object.keys(performanceChartRaw.metrics.clicks)
        : [];
      if (fromMetrics.length) {{
        ids = ids.filter(id => fromMetrics.includes(id));
      }}
      if (segmentFiltersRestrict()) {{
        const present = platformsWithSegmentCampaigns();
        ids = ids.filter(id => present.has(id));
      }}
      return ids;
    }}

    const METRIC_DEFS = readJson('metric-defs-data', []);
    let activeMetricId = 'spend';

    const fmtMoney = n => '$' + Number(n || 0).toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    const fmtInt = n => Number(n || 0).toLocaleString();
    const fmtPct = (n, d) => d ? (100 * n / d).toFixed(2) + '%' : '—';
    const escHtml = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    const performanceChartRaw = readJson('performance-chart-data', {{ labels: [], metrics: {{}} }});
    const budgetPacingRaw = readJson('budget-pacing-chart-data', {{
      labels: [],
      cumulative_spend: [],
      pace_line: [],
      daily_spend_by_platform: {{}},
      platforms: [],
      monthly_budget: 0,
      days_in_month: 30,
      pct_month_elapsed: 0,
    }});
    let performanceChartInstance = null;
    let budgetPacingChartInstance = null;
    let budgetPacingBudget = Number(budgetPacingRaw.monthly_budget || 0);
    const budgetPacingPlatformCatalog = budgetPacingRaw.platforms || [];
    const budgetPacingColors = budgetPacingRaw.platform_colors || {{}};
    const budgetPacingLineState = new Set(['all', ...budgetPacingPlatformCatalog.map(item => item.id)]);

    function formatDailyLabel(dateStr) {{
      const s = String(dateStr).slice(0, 10);
      if (s.length < 10) return s;
      return `${{s.slice(5, 7)}}-${{s.slice(8, 10)}}`;
    }}

    function combineMetricSeries(metricId) {{
      const platforms = activeChartPlatforms();
      const byPlatform = performanceChartRaw.metrics?.[metricId] || {{}};
      const len = performanceChartRaw.labels?.length || 0;
      const out = new Array(len).fill(0);
      for (const platform of platforms) {{
        const scale = chartPlatformScale(platform);
        if (scale <= 0) continue;
        const series = byPlatform[platform] || [];
        for (let i = 0; i < len; i += 1) {{
          out[i] += Number(series[i] || 0) * scale;
        }}
      }}
      return out;
    }}

    function performanceChartHasVisibleData() {{
      if (!(performanceChartRaw.labels || []).length) return false;
      const series = combineMetricSeries(activeMetricId);
      return series.some(value => Number(value || 0) > 0);
    }}

    function buildPerformancePayload() {{
      const def = METRIC_DEFS.find(m => m.id === activeMetricId) || METRIC_DEFS[0];
      const labels = [...(performanceChartRaw.labels || [])].map(formatDailyLabel);
      return {{
        labels,
        datasets: [{{
          label: def.label,
          data: combineMetricSeries(def.id),
          borderColor: def.color,
          backgroundColor: def.fill || def.color,
          fill: !!def.fill,
          yAxisID: def.yAxisID,
          tension: 0.35,
          borderWidth: 2.5,
          pointRadius: 2,
          pointHoverRadius: 4,
        }}],
      }};
    }}

    function performanceChartScales() {{
      const def = METRIC_DEFS.find(m => m.id === activeMetricId) || METRIC_DEFS[0];
      const ticks = def.format === 'money'
        ? {{ callback: v => '$' + Number(v).toLocaleString() }}
        : {{ callback: v => Number(v).toLocaleString() }};
      return {{
        x: {{ grid: {{ display: false }} }},
        y: {{
          type: 'linear',
          position: 'left',
          beginAtZero: true,
          ticks,
          title: {{ display: true, text: def.label }},
        }},
      }};
    }}

    function formatTooltipValue(def, raw) {{
      if (def.format === 'money') return fmtMoney(raw);
      if (def.id === 'conversions') {{
        const n = Number(raw || 0);
        return Number.isInteger(n) ? fmtInt(n) : n.toLocaleString(undefined, {{ maximumFractionDigits: 2 }});
      }}
      return fmtInt(raw);
    }}

    function refreshCharts() {{
      const emptyEl = document.getElementById('performanceTrendEmpty');
      const canvas = document.getElementById('performanceTrendChart');
      if (!canvas) return;

      if (performanceChartInstance) {{
        performanceChartInstance.destroy();
        performanceChartInstance = null;
      }}

      const hasMetrics = !!activeMetricId;
      const hasLabels = (performanceChartRaw.labels || []).length > 0;
      const hasData = hasLabels && performanceChartHasVisibleData();
      if (emptyEl) {{
        emptyEl.classList.toggle('show', !hasMetrics || !hasData);
        if (!hasMetrics) {{
          emptyEl.textContent = 'Choose a metric to display the chart.';
        }} else if (!hasLabels) {{
          emptyEl.textContent = 'No daily performance data for this period.';
        }} else if (segmentFiltersRestrict() && !activeChartPlatforms().length) {{
          emptyEl.textContent = 'No paid campaigns on the selected channels for this business line.';
        }} else {{
          emptyEl.textContent = 'No daily performance data for the selected filters.';
        }}
      }}
      canvas.hidden = !hasMetrics || !hasData;
      if (!hasMetrics || !hasData) return;

      const chartData = buildPerformancePayload();
      performanceChartInstance = new Chart(canvas, {{
        type: 'line',
        data: chartData,
        options: {{
          responsive: true,
          maintainAspectRatio: true,
          interaction: {{ mode: 'index', intersect: false }},
          scales: performanceChartScales(),
          plugins: {{
            legend: {{
              position: 'bottom',
              align: 'start',
              labels: {{
                usePointStyle: true,
                pointStyle: 'circle',
                boxWidth: 8,
                boxHeight: 8,
                padding: 18,
                color: '#64748b',
                font: {{ size: 12, weight: '500', family: "'Segoe UI', system-ui, sans-serif" }},
              }},
            }},
            tooltip: {{
              callbacks: {{
                label(context) {{
                  const def = METRIC_DEFS.find(m => m.label === context.dataset.label);
                  const value = formatTooltipValue(def || {{ format: 'int' }}, context.parsed.y);
                  return `${{context.dataset.label}}: ${{value}}`;
                }},
              }},
            }},
          }},
          elements: {{ line: {{ tension: 0.35, borderWidth: 2 }}, point: {{ radius: 2, hitRadius: 8 }} }},
        }},
      }});
    }}

    function syncMetricToggleButtons() {{
      document.querySelectorAll('.metric-toggle').forEach(btn => {{
        const on = btn.dataset.metric === activeMetricId;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }});
    }}

    document.querySelectorAll('.metric-toggle').forEach(btn => {{
      btn.addEventListener('click', () => {{
        const metric = btn.dataset.metric;
        if (!metric || metric === activeMetricId) return;
        activeMetricId = metric;
        syncMetricToggleButtons();
        refreshCharts();
      }});
    }});
    if (SHOW_PERFORMANCE_TREND) {{
      syncMetricToggleButtons();
    }}

    function syncBudgetPacingPlatformSlicers() {{
      const wrap = document.getElementById('budgetPacingPlatformSlicers');
      if (!wrap) return;
      wrap.querySelectorAll('.filter-toggle').forEach(btn => {{
        const on = budgetPacingLineState.has(btn.dataset.id);
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }});
    }}

    function toggleBudgetPacingLine(lineId) {{
      if (budgetPacingLineState.has(lineId)) {{
        const visibleLines = [...budgetPacingLineState].filter(id => id !== 'pace');
        if (visibleLines.length <= 1) return;
        budgetPacingLineState.delete(lineId);
      }} else {{
        budgetPacingLineState.add(lineId);
      }}
      syncBudgetPacingPlatformSlicers();
      refreshBudgetPacingChart();
    }}

    function initBudgetPacingPlatformSlicers() {{
      const wrap = document.getElementById('budgetPacingPlatformSlicers');
      if (!wrap) return;
      wrap.innerHTML = '';
      const lineOptions = [
        {{ id: 'all', label: 'All', className: 'filter-toggle--all' }},
        ...budgetPacingPlatformCatalog.map(item => ({{
          id: item.id,
          label: item.label,
          className: `t-${{item.id}}`,
        }})),
      ];
      if (!lineOptions.length) {{
        wrap.innerHTML = '<span class="muted">No paid platform spend this month yet.</span>';
        return;
      }}
      for (const item of lineOptions) {{
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `filter-toggle ${{item.className || ''}}`.trim();
        btn.textContent = item.label;
        btn.dataset.id = item.id;
        btn.addEventListener('click', () => toggleBudgetPacingLine(item.id));
        wrap.appendChild(btn);
      }}
      syncBudgetPacingPlatformSlicers();
    }}

    function buildBudgetPacingSeries() {{
      const labels = budgetPacingRaw.labels || [];
      const byPlatform = budgetPacingRaw.daily_spend_by_platform || {{}};
      const storedByPlatform = budgetPacingRaw.cumulative_by_platform || {{}};
      const cumulativeByPlatform = {{}};
      for (const item of budgetPacingPlatformCatalog) {{
        if (storedByPlatform[item.id]) {{
          cumulativeByPlatform[item.id] = storedByPlatform[item.id];
          continue;
        }}
        let running = 0;
        cumulativeByPlatform[item.id] = labels.map(dayKey => {{
          running += Number(byPlatform[item.id]?.[dayKey] || 0);
          return running;
        }});
      }}
      let allRunning = 0;
      const cumulativeAll = labels.map(dayKey => {{
        const dayTotal = budgetPacingPlatformCatalog.reduce(
          (sum, item) => sum + Number(byPlatform[item.id]?.[dayKey] || 0),
          0
        );
        allRunning += dayTotal;
        return allRunning;
      }});
      const daysInMonth = Number(budgetPacingRaw.days_in_month || labels.length || 30);
      const paceLine = labels.map((_, idx) => (
        budgetPacingBudget > 0 ? budgetPacingBudget * ((idx + 1) / daysInMonth) : 0
      ));
      return {{ labels, cumulativeAll, cumulativeByPlatform, paceLine }};
    }}

    function computeBudgetPacingGuidance(cumulativeAll, paceLine) {{
      const labels = budgetPacingRaw.labels || [];
      const byPlatform = budgetPacingRaw.daily_spend_by_platform || {{}};
      const daysInMonth = Number(budgetPacingRaw.days_in_month || 30);
      const daysElapsed = labels.length;
      const daysRemaining = Math.max(0, daysInMonth - daysElapsed);
      const mtd = cumulativeAll.length ? cumulativeAll[cumulativeAll.length - 1] : 0;
      const expected = paceLine.length ? paceLine[paceLine.length - 1] : 0;
      const budget = budgetPacingBudget;
      const pctVsPace = expected > 0 ? (100 * (mtd - expected) / expected) : null;
      const remainingBudget = budget - mtd;
      const avgDaily = daysElapsed > 0 ? mtd / daysElapsed : 0;
      const requiredDaily = budget > 0 && daysRemaining > 0 ? remainingBudget / daysRemaining : null;
      const dailyAdjustment = requiredDaily !== null && daysElapsed > 0
        ? requiredDaily - avgDaily
        : null;

      const platformAdjustments = [];
      for (const item of budgetPacingPlatformCatalog) {{
        let platformMtd = 0;
        for (const dayKey of labels) {{
          platformMtd += Number(byPlatform[item.id]?.[dayKey] || 0);
        }}
        const platformAvg = daysElapsed > 0 ? platformMtd / daysElapsed : 0;
        const share = mtd > 0 ? platformMtd / mtd : (1 / budgetPacingPlatformCatalog.length);
        const platformRequired = requiredDaily !== null ? remainingBudget * share / daysRemaining : null;
        const platformAdjust = platformRequired !== null ? platformRequired - platformAvg : null;
        platformAdjustments.push({{
          id: item.id,
          label: item.label,
          mtd: platformMtd,
          adjustment: platformAdjust,
        }});
      }}

      return {{
        mtd,
        expected,
        budget,
        pctVsPace,
        daysRemaining,
        requiredDaily,
        dailyAdjustment,
        platformAdjustments,
      }};
    }}

    function formatPctVsPace(pct) {{
      if (pct === null || !Number.isFinite(pct)) return '—';
      const abs = Math.abs(pct).toFixed(1);
      if (Math.abs(pct) < 0.05) return 'On pace (0%)';
      return `${{pct > 0 ? '+' : '-'}}${{abs}}% ${{pct > 0 ? 'over' : 'under'}}`;
    }}

    function formatDailyAdjust(amount) {{
      if (amount === null || !Number.isFinite(amount)) return '—';
      if (Math.abs(amount) < 0.01) return 'On target ($0/day)';
      const verb = amount > 0 ? 'Raise' : 'Lower';
      return `${{verb}} by ${{fmtMoney(Math.abs(amount))}}/day`;
    }}

    function updateBudgetPacingStats(cumulativeAll, paceLine) {{
      const guidance = computeBudgetPacingGuidance(cumulativeAll, paceLine);
      const mtdEl = document.getElementById('budgetPacingMtd');
      const budgetEl = document.getElementById('budgetPacingBudgetStat');
      const expectedEl = document.getElementById('budgetPacingExpected');
      const vsPaceEl = document.getElementById('budgetPacingVsPace');
      const requiredEl = document.getElementById('budgetPacingRequiredDaily');
      const adjustEl = document.getElementById('budgetPacingDailyAdjust');
      const guidanceEl = document.getElementById('budgetPacingGuidance');
      const saveValueEl = document.getElementById('budgetPacingSaveValue');

      if (mtdEl) mtdEl.textContent = fmtMoney(guidance.mtd);
      if (budgetEl) budgetEl.textContent = guidance.budget > 0 ? fmtMoney(guidance.budget) : '—';
      if (expectedEl) expectedEl.textContent = guidance.budget > 0 ? fmtMoney(guidance.expected) : '—';
      if (saveValueEl) saveValueEl.value = guidance.budget > 0 ? String(guidance.budget) : '';

      if (vsPaceEl) {{
        vsPaceEl.textContent = guidance.budget > 0 ? formatPctVsPace(guidance.pctVsPace) : '—';
        vsPaceEl.classList.remove('over', 'under');
        if (guidance.pctVsPace !== null && Math.abs(guidance.pctVsPace) >= 0.05) {{
          vsPaceEl.classList.add(guidance.pctVsPace > 0 ? 'over' : 'under');
        }}
      }}
      if (requiredEl) {{
        if (guidance.budget <= 0) {{
          requiredEl.textContent = 'Set budget first';
        }} else if (guidance.requiredDaily === null) {{
          requiredEl.textContent = '—';
        }} else if (guidance.budget - guidance.mtd <= 0) {{
          requiredEl.textContent = '$0/day (over budget)';
        }} else {{
          requiredEl.textContent = `${{fmtMoney(guidance.requiredDaily)}}/day`;
        }}
      }}
      if (adjustEl) {{
        adjustEl.textContent = guidance.budget > 0 ? formatDailyAdjust(guidance.dailyAdjustment) : '—';
        adjustEl.classList.remove('over', 'under');
        if (guidance.dailyAdjustment !== null && Math.abs(guidance.dailyAdjustment) >= 0.01) {{
          adjustEl.classList.add(guidance.dailyAdjustment > 0 ? 'under' : 'over');
        }}
      }}

      if (guidanceEl) {{
        if (!guidance.budget) {{
          guidanceEl.innerHTML = '<p class="muted">Enter a monthly budget to see pacing guidance for the rest of the month.</p>';
        }} else if (!guidance.daysRemaining) {{
          guidanceEl.innerHTML = '<p class="muted">Last day of the month — compare MTD spend to budget above.</p>';
        }} else {{
          const remaining = guidance.budget - guidance.mtd;
          const adjText = formatDailyAdjust(guidance.dailyAdjustment);
          const direction = guidance.dailyAdjustment > 0 ? 'raise' : 'lower';
          const platformLines = guidance.platformAdjustments
            .filter(row => row.mtd > 0 || row.adjustment !== null)
            .map(row => {{
              if (row.adjustment === null) return `${{row.label}}: —`;
              if (Math.abs(row.adjustment) < 0.01) return `${{row.label}}: on target`;
              const v = row.adjustment > 0 ? 'raise' : 'lower';
              return `${{row.label}}: ${{v}} ${{fmtMoney(Math.abs(row.adjustment))}}/day`;
            }})
            .join(' · ');
          guidanceEl.innerHTML = `
            <p><strong>Pacing note for media:</strong> You are <strong>${{formatPctVsPace(guidance.pctVsPace)}}</strong> vs where spend should be today.
            With <strong>${{guidance.daysRemaining}}</strong> day${{guidance.daysRemaining === 1 ? '' : 's'}} left,
            <strong>${{fmtMoney(Math.max(0, remaining))}}</strong> remains in budget${{remaining <= 0 ? ' (already over budget)' : ''}}.
            To land on budget, ${{direction}} total daily spend — ${{adjText.replace('Raise by ', 'raise by ').replace('Lower by ', 'lower by ')}} for the rest of the month
            (target <strong>${{remaining <= 0 ? '$0/day' : fmtMoney(guidance.requiredDaily) + '/day'}}</strong> vs current avg <strong>${{fmtMoney(guidance.mtd / (budgetPacingRaw.labels?.length || 1))}}/day</strong>).</p>
            ${{platformLines ? `<p class="guidance-platforms"><strong>By platform:</strong> ${{escHtml(platformLines)}}</p>` : ''}}`;
        }}
      }}
    }}

    function refreshBudgetPacingChart() {{
      const emptyEl = document.getElementById('budgetPacingEmpty');
      const canvas = document.getElementById('budgetPacingChart');
      if (!canvas) return;

      if (budgetPacingChartInstance) {{
        budgetPacingChartInstance.destroy();
        budgetPacingChartInstance = null;
      }}

      const {{ labels, cumulativeAll, cumulativeByPlatform, paceLine }} = buildBudgetPacingSeries();
      const hasSpend = cumulativeAll.some(v => Number(v) > 0);
      const showChart = hasSpend || budgetPacingBudget > 0;
      if (emptyEl) {{
        emptyEl.classList.toggle('show', !showChart);
        emptyEl.textContent = budgetPacingBudget > 0
          ? 'No paid spend recorded yet this month.'
          : 'Enter a monthly budget to show the pacing line.';
      }}
      canvas.hidden = !showChart;
      updateBudgetPacingStats(cumulativeAll, paceLine);
      if (!showChart) return;

      const datasets = [];
      if (budgetPacingLineState.has('all')) {{
        datasets.push({{
          label: 'All',
          data: cumulativeAll,
          borderColor: budgetPacingColors.all || '#0a2540',
          backgroundColor: 'rgba(10, 37, 64, 0.06)',
          fill: false,
          tension: 0.25,
          borderWidth: 3,
          pointRadius: 2,
        }});
      }}
      for (const item of budgetPacingPlatformCatalog) {{
        if (!budgetPacingLineState.has(item.id)) continue;
        datasets.push({{
          label: item.label,
          data: cumulativeByPlatform[item.id] || [],
          borderColor: budgetPacingColors[item.id] || '#64748b',
          fill: false,
          tension: 0.25,
          borderWidth: 2,
          pointRadius: 1,
          borderDash: [],
        }});
      }}
      if (budgetPacingBudget > 0) {{
        datasets.push({{
          label: 'Budget pace',
          data: paceLine,
          borderColor: budgetPacingColors.pace || '#c9a227',
          borderDash: [6, 4],
          fill: false,
          tension: 0,
          borderWidth: 2,
          pointRadius: 0,
        }});
      }}

      budgetPacingChartInstance = new Chart(canvas, {{
        type: 'line',
        data: {{
          labels: labels.map(formatDailyLabel),
          datasets,
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: true,
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            x: {{ grid: {{ display: false }} }},
            y: {{
              beginAtZero: true,
              ticks: {{ callback: v => '$' + Number(v).toLocaleString() }},
            }},
          }},
          plugins: {{
            legend: {{ position: 'bottom' }},
            tooltip: {{
              callbacks: {{
                label(context) {{
                  return `${{context.dataset.label}}: ${{fmtMoney(context.parsed.y)}}`;
                }},
              }},
            }},
          }},
        }},
      }});
    }}

    if (SHOW_BUDGET_PACING) {{
      const budgetPacingInput = document.getElementById('budgetPacingInput');
      budgetPacingInput?.addEventListener('input', () => {{
        budgetPacingBudget = Math.max(0, Number(budgetPacingInput.value || 0));
        refreshBudgetPacingChart();
      }});
      initBudgetPacingPlatformSlicers();
      refreshBudgetPacingChart();
    }}

    function showAndreToast() {{
      const toast = document.getElementById('andreToast');
      if (!toast) return;
      toast.hidden = false;
      toast.classList.add('show');
      clearTimeout(toast._hideTimer);
      toast._hideTimer = setTimeout(() => {{
        toast.classList.remove('show');
        setTimeout(() => {{ toast.hidden = true; }}, 400);
      }}, 2800);
    }}

    const VIEW_LABELS = {{
      overview: 'Overview',
      campaigns: 'Campaign Explorer',
      gsc: 'GSC',
      website: 'Website Analytics',
    }};

    function setActiveView(view) {{
      const allowed = ['overview'];
      if (SHOW_CAMPAIGN_EXPLORER) allowed.push('campaigns');
      if (SHOW_GSC) allowed.push('gsc');
      if (SHOW_WEBSITE_ANALYTICS) allowed.push('website');
      if (!allowed.includes(view)) view = 'overview';
      if (view === 'website' && !document.querySelector('.dash-view-btn[data-view="website"]')) {{
        view = 'overview';
      }}
      if (view === 'campaigns' && !SHOW_CAMPAIGN_EXPLORER) {{
        view = 'overview';
      }}
      document.querySelectorAll('.dash-view-btn').forEach(btn => {{
        const on = btn.dataset.view === view;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
      }});
      document.querySelectorAll('.view-panel').forEach(panel => {{
        const on = panel.id === 'view-' + view;
        panel.classList.toggle('active', on);
        panel.hidden = !on;
      }});
      document.querySelectorAll('[data-website-filter]').forEach(control => {{
        control.hidden = view !== 'website';
      }});
      document.querySelectorAll('[data-campaign-filter]').forEach(control => {{
        control.hidden = view !== 'campaigns';
      }});
      const titleSuffix = VIEW_LABELS[view] || 'Dashboard';
      document.title = `${{document.title.split(' — ')[0]}} — ${{titleSuffix}}`;
      try {{
        const url = new URL(window.location.href);
        if (view === 'overview') {{
          url.searchParams.delete('view');
        }} else {{
          url.searchParams.set('view', view);
        }}
        history.replaceState(null, '', url);
      }} catch (err) {{
        /* ignore */
      }}
    }}

    document.querySelectorAll('.dash-view-btn').forEach(btn => {{
      btn.addEventListener('click', () => setActiveView(btn.dataset.view || 'overview'));
    }});

    const initialView = new URLSearchParams(window.location.search).get('view');
    if (initialView) {{
      setActiveView(initialView);
    }}

    const blSort = {{ key: 'spend', dir: 'desc' }};
    const overviewCardDefaults = new Map();
    document.querySelectorAll('.cards .card[data-platform]').forEach(card => {{
      overviewCardDefaults.set(card.dataset.platform, {{
        valueHtml: card.querySelector('.card-value')?.innerHTML || '',
        statsHtml: card.querySelector('.card-stats')?.innerHTML || '',
      }});
    }});
    const paidOverviewDefaults = readJson('paid-overview-metrics-data', {{}});
    const ga4PaidKeyEvents = Number(paidOverviewDefaults.ga4_key_events || 0);

    function fmtCpa(spend, count) {{
      const n = Number(count || 0);
      if (!n) return '—';
      return fmtMoney(Number(spend || 0) / n);
    }}

    function updatePaidOverviewMetrics(totals) {{
      const spendEl = document.getElementById('heroSpend');
      if (!spendEl) return;
      const m = totals || paidOverviewDefaults;
      const spend = Number(m.spend || 0);
      const clicks = Number(m.clicks || 0);
      const impressions = Number(m.impressions || 0);
      const conversions = Number(m.conversions || 0);
      const ga4Events = totals ? ga4PaidKeyEvents : Number(m.ga4_key_events || ga4PaidKeyEvents);

      spendEl.textContent = fmtMoney(spend);
      const impEl = document.getElementById('heroImpressions');
      const clkEl = document.getElementById('heroClicks');
      const ctrEl = document.getElementById('heroCtr');
      const cpcEl = document.getElementById('heroCpc');
      const convEl = document.getElementById('heroConversions');
      const repCpaEl = document.getElementById('heroReportedCpa');
      const verCpaEl = document.getElementById('heroVerifiedCpa');
      if (impEl) impEl.textContent = fmtInt(impressions);
      if (clkEl) clkEl.textContent = fmtInt(clicks);
      if (ctrEl) ctrEl.textContent = impressions ? fmtPct(clicks, impressions) : '—';
      if (cpcEl) cpcEl.textContent = clicks ? fmtCpa(spend, clicks) : '—';
      if (convEl) convEl.textContent = fmtInt(conversions);
      if (repCpaEl) repCpaEl.textContent = conversions ? fmtCpa(spend, conversions) : '—';
      if (verCpaEl) verCpaEl.textContent = ga4Events ? fmtCpa(spend, ga4Events) : '—';
    }}

    function platformVisible(platformId) {{
      if (!effectiveFilterIds(channelState, platformCatalog).includes(platformId)) return false;
      if (platformId !== 'organic' && segmentFiltersRestrict()) {{
        return platformsWithSegmentCampaigns().has(platformId);
      }}
      return true;
    }}

    function syncChannelToggleVisibility() {{
      const wrap = document.getElementById('channelFilters');
      if (!wrap) return;
      const present = segmentFiltersRestrict() ? platformsWithSegmentCampaigns() : null;
      wrap.querySelectorAll('.filter-toggle').forEach(btn => {{
        const id = btn.dataset.id;
        if (!id || id === '__all__' || id === 'organic') return;
        const hasData = !present || present.has(id);
        btn.hidden = !hasData;
        btn.style.display = hasData ? '' : 'none';
      }});
    }}

    function rowMatchesSegmentFilter(row, selectedIds) {{
      const ids = Array.isArray(row.region_ids) && row.region_ids.length
        ? row.region_ids
        : [row.business_line];
      return ids.some(id => selectedIds.includes(id));
    }}

    function rowMatchesProductLineFilter(row, selectedIds) {{
      const ids = Array.isArray(row.product_line_ids) && row.product_line_ids.length
        ? row.product_line_ids
        : [row.product_line];
      return ids.some(id => selectedIds.includes(id));
    }}

    function filteredBlCampaigns() {{
      const showZeroSpend = includeZeroSpendCampaigns();
      const channels = selectedPaidChannelIds();
      if (!channels.length) return [];
      const bls = SHOW_SEGMENT_FILTERS
        ? effectiveFilterIds(blState, blCatalog)
        : null;
      const products = SHOW_PRODUCT_LINE_FILTERS
        ? effectiveFilterIds(productLineState, productLineCatalog)
        : null;
      if (SHOW_SEGMENT_FILTERS && (!blCatalog.length || !bls.length)) return [];
      if (SHOW_PRODUCT_LINE_FILTERS && (!productLineCatalog.length || !products.length)) return [];
      return blCampaigns.filter(r => {{
        if (!channels.includes(r.platform)) return false;
        if (SHOW_SEGMENT_FILTERS && !rowMatchesSegmentFilter(r, bls)) return false;
        if (SHOW_PRODUCT_LINE_FILTERS && !rowMatchesProductLineFilter(r, products)) return false;
        if (!showZeroSpend && (r.spend || 0) < 0.01) return false;
        return true;
      }});
    }}

    function aggregatePlatformTotals(platformId, rows) {{
      const subset = rows.filter(r => r.platform === platformId);
      return subset.reduce((acc, r) => ({{
        spend: acc.spend + (r.spend || 0),
        clicks: acc.clicks + (r.clicks || 0),
        impressions: acc.impressions + (r.impressions || 0),
        conversions: acc.conversions + (r.conversions || 0),
      }}), {{ spend: 0, clicks: 0, impressions: 0, conversions: 0 }});
    }}

    function zeroOverviewTotals() {{
      return {{ spend: 0, clicks: 0, impressions: 0, conversions: 0 }};
    }}

    function updateOverviewCard(platformId, totals, filteredMode = false) {{
      const card = document.querySelector(`.cards .card[data-platform="${{platformId}}"]`);
      if (!card) return;
      const defaults = overviewCardDefaults.get(platformId);
      const valueEl = card.querySelector('.card-value');
      const statsEl = card.querySelector('.card-stats');
      if (!valueEl || !statsEl || !defaults) return;
      if (!totals) {{
        if (!filteredMode || platformId === 'organic') {{
          valueEl.innerHTML = defaults.valueHtml;
          statsEl.innerHTML = defaults.statsHtml;
          return;
        }}
        totals = zeroOverviewTotals();
      }}
      if (platformId === 'organic') {{
        valueEl.innerHTML = defaults.valueHtml;
        statsEl.innerHTML = defaults.statsHtml;
        return;
      }}
      valueEl.textContent = fmtMoney(totals.spend);
      statsEl.innerHTML = `
        <span>${{fmtInt(totals.clicks)}} clicks</span>
        <span>${{fmtInt(totals.impressions)}} impr.</span>
        <span>${{fmtInt(totals.conversions)}} conv.</span>`;
    }}

    function updateHeroTotals(totals) {{
      updatePaidOverviewMetrics(totals);
    }}

    function filtersPartiallyApplied() {{
      const partialBl = SHOW_SEGMENT_FILTERS && blCatalog.length && !isAllSelected(blState, blCatalog);
      const partialProduct = SHOW_PRODUCT_LINE_FILTERS
        && productLineCatalog.length
        && !isAllSelected(productLineState, productLineCatalog);
      return partialBl || partialProduct || paidChannelFilterActive();
    }}

    function computePaidHeroTotals() {{
      if (!filtersPartiallyApplied()) return null;
      return filteredBlCampaigns().reduce((acc, r) => ({{
        spend: acc.spend + (r.spend || 0),
        clicks: acc.clicks + (r.clicks || 0),
        impressions: acc.impressions + (r.impressions || 0),
        conversions: acc.conversions + (r.conversions || 0),
      }}), {{ spend: 0, clicks: 0, impressions: 0, conversions: 0 }});
    }}

    function applyOverviewFilters() {{
      const blFiltered = filtersPartiallyApplied() ? filteredBlCampaigns() : null;

      syncChannelToggleVisibility();

      document.querySelectorAll('.cards .card[data-platform]').forEach(card => {{
        const platformId = card.dataset.platform;
        const visible = platformVisible(platformId);
        card.hidden = !visible;
        card.style.display = visible ? '' : 'none';
        if (!visible) return;
        if (blFiltered && platformId !== 'organic') {{
          const totals = aggregatePlatformTotals(platformId, blFiltered);
          updateOverviewCard(platformId, totals, true);
        }} else {{
          updateOverviewCard(platformId, null, false);
        }}
      }});

      document.querySelectorAll('.platform-panel[data-platform]').forEach(panel => {{
        const visible = platformVisible(panel.dataset.platform);
        panel.hidden = !visible;
        panel.style.display = visible ? '' : 'none';
      }});

      updateHeroTotals(computePaidHeroTotals());
      refreshCharts();
    }}

    let renderGa4Pages = () => {{}};

    function applyGlobalFilters() {{
      applyBlView();
      applyOverviewFilters();
      renderGa4Pages();
    }}

    function blSortValue(r, key) {{
      switch (key) {{
        case 'platform':
          return String(r.platform_label || r.platform || '').toLowerCase();
        case 'business_line':
          return String(r.business_line_label || '').toLowerCase();
        case 'product_line':
          return String(r.product_line_label || '').toLowerCase();
        case 'name':
          return String(r.name || '').toLowerCase();
        case 'spend':
          return Number(r.spend || 0);
        case 'clicks':
          return Number(r.clicks || 0);
        case 'impressions':
          return Number(r.impressions || 0);
        case 'ctr':
          return r.impressions ? Number(r.clicks || 0) / Number(r.impressions) : 0;
        case 'conversions':
          return Number(r.conversions || 0);
        case 'cpc':
          return r.clicks ? Number(r.spend || 0) / Number(r.clicks) : 0;
        default:
          return 0;
      }}
    }}

    function sortBlRows(rows) {{
      const {{ key, dir }} = blSort;
      const mul = dir === 'asc' ? 1 : -1;
      const textKeys = new Set(['platform', 'business_line', 'product_line', 'name']);
      return [...rows].sort((a, b) => {{
        const av = blSortValue(a, key);
        const bv = blSortValue(b, key);
        if (textKeys.has(key)) {{
          return mul * String(av).localeCompare(String(bv));
        }}
        return mul * (av - bv);
      }});
    }}

    function updateBlSortHeaders() {{
      document.querySelectorAll('#blTable thead th[data-sort]').forEach(th => {{
        const active = th.dataset.sort === blSort.key;
        th.classList.toggle('sort-active', active);
        if (active) {{
          th.dataset.sortDir = blSort.dir;
          th.setAttribute('aria-sort', blSort.dir === 'asc' ? 'ascending' : 'descending');
        }} else {{
          delete th.dataset.sortDir;
          th.setAttribute('aria-sort', 'none');
        }}
      }});
    }}

    function onBlSortClick(e) {{
      const th = e.target.closest('#blTable thead th[data-sort]');
      if (!th) return;
      const key = th.dataset.sort;
      if (blSort.key === key) {{
        blSort.dir = blSort.dir === 'asc' ? 'desc' : 'asc';
      }} else {{
        blSort.key = key;
        blSort.dir = (key === 'platform' || key === 'business_line' || key === 'product_line' || key === 'name') ? 'asc' : 'desc';
      }}
      applyBlView();
    }}

    function initToggleGroup(wrapId, catalog, state, group) {{
      const wrap = document.getElementById(wrapId);
      if (!wrap) return;
      wrap.innerHTML = '';

      const allBtn = document.createElement('button');
      allBtn.type = 'button';
      allBtn.className = 'filter-toggle filter-toggle--all';
      allBtn.textContent = 'All';
      allBtn.dataset.group = group;
      allBtn.dataset.id = '__all__';
      allBtn.setAttribute('aria-pressed', 'false');
      allBtn.addEventListener('click', () => {{
        const allOn = state.size === catalog.length;
        state.clear();
        if (!allOn) {{
          catalog.forEach(item => state.add(item.id));
        }}
        syncToggleGroup(group);
        applyGlobalFilters();
      }});
      wrap.appendChild(allBtn);

      for (const item of catalog) {{
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'filter-toggle';
        if (group === 'channel') {{
          btn.classList.add(`t-${{item.id}}`);
        }} else if (group === 'product') {{
          btn.classList.add('t-bl');
        }} else {{
          btn.classList.add('t-bl');
        }}
        btn.textContent = item.label;
        btn.dataset.group = group;
        btn.dataset.id = item.id;
        btn.setAttribute('aria-pressed', state.has(item.id) ? 'true' : 'false');
        if (state.has(item.id)) btn.classList.add('active');
        btn.addEventListener('click', () => {{
          if (state.has(item.id)) {{
            state.delete(item.id);
          }} else {{
            state.add(item.id);
          }}
          syncToggleGroup(group);
          applyGlobalFilters();
        }});
        wrap.appendChild(btn);
      }}
      syncToggleGroup(group);
    }}

    function syncToggleGroup(group) {{
      const cfg = FILTER_GROUPS[group];
      if (!cfg) return;
      const {{ state, catalog, wrapId }} = cfg;
      const wrap = document.getElementById(wrapId);
      if (!wrap) return;
      wrap.querySelectorAll('.filter-toggle').forEach(btn => {{
        if (btn.dataset.id === '__all__') {{
          const on = isAllSelected(state, catalog);
          btn.classList.toggle('active', on);
          btn.setAttribute('aria-pressed', on ? 'true' : 'false');
          return;
        }}
        const on = state.has(btn.dataset.id);
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }});
    }}

    function setGroupSelection(group, ids) {{
      const cfg = FILTER_GROUPS[group];
      if (!cfg) return;
      cfg.state.clear();
      ids.forEach(id => cfg.state.add(id));
      syncToggleGroup(group);
      applyGlobalFilters();
    }}

    function applyBlView() {{
      const showZeroSpend = !!document.getElementById('showZeroSpend')?.checked;
      const filtered = filteredBlCampaigns();
      const spend = filtered.reduce((s, r) => s + (r.spend || 0), 0);
      const clicks = filtered.reduce((s, r) => s + (r.clicks || 0), 0);
      const impressions = filtered.reduce((s, r) => s + (r.impressions || 0), 0);
      const conv = filtered.reduce((s, r) => s + (r.conversions || 0), 0);
      const cpc = clicks ? spend / clicks : 0;

      const summary = document.getElementById('blSummary');
      if (summary) {{
        summary.innerHTML = `
          <div class="bl-stat"><div class="bl-stat-val">${{fmtMoney(spend)}}</div><div class="bl-stat-lbl">Spend</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(clicks)}}</div><div class="bl-stat-lbl">Clicks</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(impressions)}}</div><div class="bl-stat-lbl">Impressions</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(conv)}}</div><div class="bl-stat-lbl">Conversions</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{clicks ? fmtMoney(cpc) : '—'}}</div><div class="bl-stat-lbl">CPC</div></div>`;
      }}

      const tbody = document.getElementById('blTableBody');
      const blTable = document.getElementById('blTable');
      const emptyColspan = Number(blTable?.dataset.emptyColspan || 9);
      if (tbody) {{
        if (!filtered.length) {{
          tbody.innerHTML = `<tr><td colspan="${{emptyColspan}}" class="muted" style="padding:24px;text-align:center">No campaigns match — try other filters or enable $0 spend rows.</td></tr>`;
        }} else {{
          const sorted = sortBlRows(filtered);
          tbody.innerHTML = '';
          for (const r of sorted) {{
            tbody.appendChild(buildBlCampaignRow(r));
          }}
        }}
      }}

      const rowCount = document.getElementById('blRowCount');
      if (rowCount) rowCount.textContent = filtered.length + ' row' + (filtered.length === 1 ? '' : 's');

      const status = document.getElementById('filterStatus');
      if (status) {{
        const chLabels = platformCatalog.filter(p => channelState.has(p.id)).map(p => p.label);
        const blLabels = blCatalog.filter(b => blState.has(b.id)).map(b => b.label);
        const productLabels = productLineCatalog.filter(p => productLineState.has(p.id)).map(p => p.label);
        const chText = isAllSelected(channelState, platformCatalog)
          ? 'All channels'
          : chLabels.join(', ');
        const parts = [];
        if (SHOW_SEGMENT_FILTERS && blCatalog.length) {{
          parts.push(
            isAllSelected(blState, blCatalog) ? SEGMENT_FILTER_ALL_LABEL : blLabels.join(', ')
          );
        }}
        if (SHOW_PRODUCT_LINE_FILTERS && productLineCatalog.length) {{
          parts.push(
            isAllSelected(productLineState, productLineCatalog)
              ? PRODUCT_LINE_FILTER_ALL_LABEL
              : productLabels.join(', ')
          );
        }}
        parts.push(chText);
        const zeroNote = showZeroSpend ? ' · incl. $0 spend' : '';
        status.textContent = `${{parts.join(' · ')}} · ${{filtered.length}} campaign${{filtered.length === 1 ? '' : 's'}}${{zeroNote}}`;
      }}
      updateBlSortHeaders();
    }}

    initToggleGroup('channelFilters', platformCatalog, channelState, 'channel');
    initToggleGroup('blFilters', blCatalog, blState, 'bl');
    initToggleGroup('productLineFilters', productLineCatalog, productLineState, 'product');
    document.getElementById('showZeroSpend')?.addEventListener('change', applyGlobalFilters);

    const filtersPanel = document.getElementById('globalFiltersPanel');
    const filtersCollapseBtn = document.getElementById('filtersCollapseBtn');
    filtersCollapseBtn?.addEventListener('click', () => {{
      if (!filtersPanel) return;
      const collapsed = filtersPanel.classList.toggle('is-collapsed');
      filtersCollapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }});

    document.querySelector('#blTable thead')?.addEventListener('click', onBlSortClick);

    const ga4PagesBody = document.getElementById('ga4PagesBody');
    const ga4MetricsGrid = document.getElementById('ga4MetricsGrid');

    function fmtGa4Seconds(value) {{
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) return '—';
      return n.toFixed(1) + 's';
    }}

    function fmtGa4Rate(value) {{
      const n = Number(value);
      if (!Number.isFinite(n) || n < 0) return '—';
      return (100 * n).toFixed(2) + '%';
    }}

    function fmtGa4Ratio(value) {{
      const n = Number(value);
      if (!Number.isFinite(n) || n < 0) return '—';
      return n.toFixed(2);
    }}

    function summaryFromPages(rows) {{
      const sessions = rows.reduce((sum, row) => sum + Number(row.sessions || 0), 0);
      const engaged = rows.reduce((sum, row) => sum + Number(row.engaged_sessions || 0), 0);
      const pageViews = rows.reduce((sum, row) => sum + Number(row.page_views || 0), 0);
      const keyEvents = rows.reduce((sum, row) => sum + Number(row.key_events || 0), 0);
      return {{
        sessions,
        engagement_rate: sessions ? engaged / sessions : 0,
        avg_engagement_time_sec: ga4SiteSummary.avg_engagement_time_sec,
        avg_session_duration_sec: ga4SiteSummary.avg_session_duration_sec,
        events_per_session: sessions ? keyEvents / sessions : 0,
        views_per_session: sessions ? pageViews / sessions : 0,
        filtered: true,
      }};
    }}

    function renderGa4MetricsSummary(rows, isFiltered) {{
      if (!ga4MetricsGrid || !ga4SiteSummary || !ga4SiteSummary.sessions) return;
      const metrics = isFiltered ? summaryFromPages(rows) : {{ ...ga4SiteSummary, filtered: false }};
      const sublabel = metrics.filtered ? 'GA4 filtered view' : 'Site-wide GA4';
      ga4MetricsGrid.innerHTML = `
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Sessions</div>
          <div class="ga4-metric-value">${{fmtInt(metrics.sessions)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Engagement rate</div>
          <div class="ga4-metric-value">${{fmtGa4Rate(metrics.engagement_rate)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Avg engagement time</div>
          <div class="ga4-metric-value">${{fmtGa4Seconds(metrics.avg_engagement_time_sec)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Avg session duration</div>
          <div class="ga4-metric-value">${{fmtGa4Seconds(metrics.avg_session_duration_sec)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Events / session</div>
          <div class="ga4-metric-value">${{fmtGa4Ratio(metrics.events_per_session)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Views / session</div>
          <div class="ga4-metric-value">${{fmtGa4Ratio(metrics.views_per_session)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>`;
    }}

    if (ga4PagesBody && (ga4Pages.length || Object.values(ga4PagesByPlatform).some(rows => (rows || []).length))) {{
      const ga4PageSearch = document.getElementById('ga4PageSearch');
      const ga4PagesCount = document.getElementById('ga4PagesCount');
      const ga4PagesPagination = document.getElementById('ga4PagesPagination');
      const ga4TrafficFilterNote = document.getElementById('ga4TrafficFilterNote');
      const ga4PagesTableNote = document.getElementById('ga4PagesTableNote');
      const ga4PageSort = {{ key: 'sessions', dir: 'desc' }};
      let ga4PageQuery = '';
      let ga4PageNum = 1;
      const GA4_PAGE_SIZE = 10;

      function rowMatchesGa4SegmentFilter(row) {{
        if (!SHOW_SEGMENT_FILTERS || !blCatalog.length) return true;
        if (isAllSelected(blState, blCatalog)) return true;
        const selected = effectiveFilterIds(blState, blCatalog);
        const ids = Array.isArray(row.region_ids) && row.region_ids.length
          ? row.region_ids
          : [row.business_line];
        return ids.some(id => selected.includes(id));
      }}

      function filterGa4PagesBySegment(rows) {{
        if (!SHOW_SEGMENT_FILTERS || !blCatalog.length) return rows;
        if (isAllSelected(blState, blCatalog)) return rows;
        return rows.filter(rowMatchesGa4SegmentFilter);
      }}

      function ga4ActivePages() {{
        const selectedChannels = effectiveFilterIds(channelState, platformCatalog);
        const paidChannels = selectedChannels.filter(id => PAID_PLATFORM_IDS.has(id));
        const includesOrganic = selectedChannels.includes('organic');
        if (isAllSelected(channelState, platformCatalog) || includesOrganic) return ga4Pages;
        if (!paidChannels.length) return [];
        const merged = new Map();
        paidChannels.flatMap(id => ga4PagesByPlatform[id] || []).forEach(row => {{
          const key = `${{row.page_path || ''}}::${{row.page_title || ''}}`;
          const current = merged.get(key);
          if (!current) {{
            merged.set(key, {{ ...row }});
            return;
          }}
          ['sessions', 'page_views', 'users', 'engaged_sessions', 'key_events'].forEach(field => {{
            current[field] = Number(current[field] || 0) + Number(row[field] || 0);
          }});
          current.region_ids = [...new Set([
            ...(current.region_ids || []),
            ...(row.region_ids || []),
          ])];
        }});
        return [...merged.values()];
      }}

      function syncGa4TrafficNote() {{
        const selectedChannels = effectiveFilterIds(channelState, platformCatalog);
        const paidChannels = selectedChannels.filter(id => PAID_PLATFORM_IDS.has(id));
        const includesOrganic = selectedChannels.includes('organic');
        const usesAllSite = isAllSelected(channelState, platformCatalog) || includesOrganic;
        const label = !usesAllSite && paidChannels.length === 1
          ? ga4LandingLabels[paidChannels[0]] || ''
          : '';
        if (ga4TrafficFilterNote) {{
          if (!usesAllSite && paidChannels.length) {{
            ga4TrafficFilterNote.textContent = label
              || `Paid landing pages for ${{paidChannels.length}} selected channels.`;
            ga4TrafficFilterNote.hidden = false;
          }} else {{
            ga4TrafficFilterNote.hidden = true;
            ga4TrafficFilterNote.textContent = '';
          }}
        }}
        if (ga4PagesTableNote) {{
          ga4PagesTableNote.textContent = usesAllSite
            ? ga4PagesTableNote.dataset.allNote || ga4PagesTableNote.textContent
            : (label || 'Paid landing pages for the selected channels.');
        }}
      }}

      if (ga4PagesTableNote) {{
        ga4PagesTableNote.dataset.allNote = ga4PagesTableNote.textContent;
      }}

      function ga4PageSortValue(row, key) {{
        if (key === 'page_path' || key === 'page_title') {{
          return String(row[key] || '').toLowerCase();
        }}
        if (key === 'engagement_rate') {{
          return Number(row.engagement_rate || 0);
        }}
        return Number(row[key] || 0);
      }}

      function sortGa4Pages(rows) {{
        const {{ key, dir }} = ga4PageSort;
        const mul = dir === 'asc' ? 1 : -1;
        const textKeys = new Set(['page_path', 'page_title']);
        return [...rows].sort((a, b) => {{
          const av = ga4PageSortValue(a, key);
          const bv = ga4PageSortValue(b, key);
          if (textKeys.has(key)) return mul * String(av).localeCompare(String(bv));
          return mul * (av - bv);
        }});
      }}

      function filterGa4Pages(rows) {{
        const q = ga4PageQuery.trim().toLowerCase();
        if (!q) return rows;
        return rows.filter(row => {{
          const path = String(row.page_path || '').toLowerCase();
          const title = String(row.page_title || '').toLowerCase();
          return path.includes(q) || title.includes(q);
        }});
      }}

      function updateGa4SortHeaders() {{
        document.querySelectorAll('#ga4PagesTable thead th[data-sort]').forEach(th => {{
          const active = th.dataset.sort === ga4PageSort.key;
          th.classList.toggle('sort-active', active);
          if (active) {{
            th.dataset.sortDir = ga4PageSort.dir;
            th.setAttribute('aria-sort', ga4PageSort.dir === 'asc' ? 'ascending' : 'descending');
          }} else {{
            delete th.dataset.sortDir;
            th.setAttribute('aria-sort', 'none');
          }}
        }});
      }}

      function renderGa4Pagination(totalRows, page, pageSize) {{
        if (!ga4PagesPagination) return;
        const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
        if (totalRows <= pageSize) {{
          ga4PagesPagination.hidden = true;
          ga4PagesPagination.innerHTML = '';
          return;
        }}
        ga4PagesPagination.hidden = false;
        const start = (page - 1) * pageSize + 1;
        const end = Math.min(totalRows, page * pageSize);
        const prevDisabled = page <= 1 ? ' disabled' : '';
        const nextDisabled = page >= totalPages ? ' disabled' : '';
        ga4PagesPagination.innerHTML = `
          <span class="ga4-pages-pagination-info">Showing ${{start}}–${{end}} of ${{fmtInt(totalRows)}} pages</span>
          <div class="ga4-pages-pagination-controls">
            <button type="button" class="ga4-pages-page-btn" data-page="prev"${{prevDisabled}} aria-label="Previous page">Previous</button>
            <span class="ga4-pages-pagination-info">Page ${{page}} of ${{totalPages}}</span>
            <button type="button" class="ga4-pages-page-btn" data-page="next"${{nextDisabled}} aria-label="Next page">Next</button>
          </div>`;
      }}

      renderGa4Pages = function() {{
        syncGa4TrafficNote();
        const sourcePages = ga4ActivePages();
        const bySegment = filterGa4PagesBySegment(sourcePages);
        const filtered = filterGa4Pages(bySegment);
        const sorted = sortGa4Pages(filtered);
        const segmentFiltered = SHOW_SEGMENT_FILTERS
          && blCatalog.length
          && !isAllSelected(blState, blCatalog);
        const channelFiltered = !isAllSelected(channelState, platformCatalog);
        const isFiltered = !!ga4PageQuery.trim() || channelFiltered || segmentFiltered;
        renderGa4MetricsSummary(filtered, isFiltered);
        const totalPages = Math.max(1, Math.ceil(sorted.length / GA4_PAGE_SIZE));
        if (ga4PageNum > totalPages) ga4PageNum = totalPages;
        if (ga4PageNum < 1) ga4PageNum = 1;
        if (ga4PagesCount) {{
          ga4PagesCount.textContent = sorted.length + ' page' + (sorted.length === 1 ? '' : 's');
        }}
        if (!sorted.length) {{
          const emptyMsg = channelFiltered && !sourcePages.length
            ? 'No landing pages for the selected channels yet. Run a full refresh after GA4 BigQuery is connected.'
            : segmentFiltered
              ? `No pages match the selected ${{GA4_SEGMENT_FILTER_LABEL.toLowerCase()}} filters.`
              : 'No pages match your search.';
          ga4PagesBody.innerHTML = `<tr><td colspan="8" class="muted" style="padding:24px;text-align:center">${{escHtml(emptyMsg)}}</td></tr>`;
          renderGa4Pagination(0, 1, GA4_PAGE_SIZE);
          updateGa4SortHeaders();
          return;
        }}
        const pageRows = sorted.slice((ga4PageNum - 1) * GA4_PAGE_SIZE, ga4PageNum * GA4_PAGE_SIZE);
        ga4PagesBody.innerHTML = pageRows.map(row => {{
          const sessions = Number(row.sessions || 0);
          const engaged = Number(row.engaged_sessions || 0);
          const engRate = sessions ? (100 * engaged / sessions).toFixed(1) + '%' : '—';
          return `<tr>
            <td class="page-path" title="${{escHtml(row.page_path)}}">${{escHtml(row.page_path)}}</td>
            <td class="page-title" title="${{escHtml(row.page_title)}}">${{escHtml(row.page_title)}}</td>
            <td class="num">${{fmtInt(row.sessions)}}</td>
            <td class="num">${{fmtInt(row.page_views)}}</td>
            <td class="num">${{fmtInt(row.users)}}</td>
            <td class="num">${{fmtInt(row.engaged_sessions)}}</td>
            <td class="num">${{engRate}}</td>
            <td class="num">${{fmtInt(row.key_events)}}</td>
          </tr>`;
        }}).join('');
        renderGa4Pagination(sorted.length, ga4PageNum, GA4_PAGE_SIZE);
        updateGa4SortHeaders();
      }};

      ga4PagesPagination?.addEventListener('click', e => {{
        const btn = e.target.closest('[data-page]');
        if (!btn || btn.disabled) return;
        const total = sortGa4Pages(filterGa4Pages(filterGa4PagesBySegment(ga4ActivePages()))).length;
        const totalPages = Math.max(1, Math.ceil(total / GA4_PAGE_SIZE));
        if (btn.dataset.page === 'prev' && ga4PageNum > 1) {{
          ga4PageNum -= 1;
          renderGa4Pages();
        }} else if (btn.dataset.page === 'next' && ga4PageNum < totalPages) {{
          ga4PageNum += 1;
          renderGa4Pages();
        }}
      }});

      ga4PageSearch?.addEventListener('input', e => {{
        ga4PageQuery = e.target.value || '';
        ga4PageNum = 1;
        renderGa4Pages();
      }});
      document.querySelector('#ga4PagesTable thead')?.addEventListener('click', e => {{
        const th = e.target.closest('th[data-sort]');
        if (!th) return;
        const key = th.dataset.sort;
        if (ga4PageSort.key === key) {{
          ga4PageSort.dir = ga4PageSort.dir === 'asc' ? 'desc' : 'asc';
        }} else {{
          ga4PageSort.key = key;
          ga4PageSort.dir = (key === 'page_path' || key === 'page_title') ? 'asc' : 'desc';
        }}
        ga4PageNum = 1;
        renderGa4Pages();
      }});
      renderGa4Pages();
    }} else if (ga4MetricsGrid && ga4SiteSummary && ga4SiteSummary.sessions) {{
      renderGa4MetricsSummary([], false);
    }}

    const DRILL_MAP = {{
      'google:campaign': {{ childLevel: 'ad_group', childLabel: 'Ad group' }},
      'google:ad_group': {{ childLevel: 'ad', childLabel: 'Ad' }},
      'linkedin:campaign_group': {{ childLevel: 'campaign', childLabel: 'Ad set' }},
      'linkedin:campaign': {{ childLevel: 'creative', childLabel: 'Ad' }},
      'meta:campaign': {{ childLevel: 'adset', childLabel: 'Ad set' }},
      'meta:adset': {{ childLevel: 'ad', childLabel: 'Ad' }},
    }};
    const LEVEL_LABELS = {{
      campaign_group: 'Campaign group',
      campaign: 'Campaign',
      ad_group: 'Ad group',
      creative: 'Creative',
      adset: 'Ad set',
      ad: 'Ad',
    }};

    function levelLabel(platform, level) {{
      if (platform === 'linkedin') {{
        if (level === 'campaign_group') return 'Campaign group';
        if (level === 'campaign') return 'Ad set';
        if (level === 'creative') return 'Ad';
      }}
      return LEVEL_LABELS[level] || String(level || '').replace(/_/g, ' ');
    }}

    function childRows(platform, level, parentId) {{
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return [];
      const pool = (breakdowns[platform] || {{}})[rule.childLevel] || [];
      const pid = String(parentId || '');
      return pool.filter(r => String(r.parent_id || '') === pid)
        .sort((a, b) => (b.spend || 0) - (a.spend || 0));
    }}

    function isExpandable(platform, level) {{
      return !!DRILL_MAP[platform + ':' + level];
    }}

    function previewPayload(r) {{
      const embed = r.youtube_embed_url || '';
      if (embed) return {{ type: 'embed', url: embed }};
      const videoUrl = r.video_url || '';
      if (videoUrl && r.media_type === 'video') {{
        const ytMatch = videoUrl.match(/(?:youtube\\.com\\/embed\\/|youtu\\.be\\/)([a-zA-Z0-9_-]{{11}})/);
        if (ytMatch) return {{ type: 'embed', url: 'https://www.youtube.com/embed/' + ytMatch[1] }};
        return {{ type: 'video', url: videoUrl }};
      }}
      const img = r.image_url || r.thumbnail_url || '';
      if (img) return {{ type: 'image', url: img }};
      return null;
    }}

    function thumbReferrerPolicy(url) {{
      const u = String(url || '').toLowerCase();
      if (u.includes('licdn.com') || u.includes('linkedin.com')) return 'origin';
      return 'no-referrer';
    }}

    function buildThumbButton(r) {{
      const thumbUrl = r.thumbnail_url || r.image_url || '';
      if (!thumbUrl) return '';
      const preview = previewPayload(r);
      const playable = preview && (preview.type === 'embed' || preview.type === 'video');
      const cls = 'ad-thumb-btn' + (playable ? ' has-video' : '');
      const attrs = preview
        ? ` data-preview-type="${{escHtml(preview.type)}}" data-preview-url="${{escHtml(preview.url)}}"`
        : ` data-preview-type="image" data-preview-url="${{escHtml(thumbUrl)}}"`;
      const play = playable ? '<span class="ad-play-icon" aria-hidden="true">▶</span>' : '';
      const referrer = thumbReferrerPolicy(thumbUrl);
      return `<button type="button" class="${{cls}}"${{attrs}} aria-label="Preview creative">${{play}}<img class="ad-thumb" src="${{escHtml(thumbUrl)}}" alt="" loading="lazy" referrerpolicy="${{referrer}}" onerror="this.style.opacity='0.35'"></button>`;
    }}

    let previewPlayer = null;

    function closeCreativePreview() {{
      const modal = document.getElementById('creativePreview');
      const body = document.getElementById('creativePreviewBody');
      const caption = document.getElementById('creativePreviewCaption');
      if (!modal || !body) return;
      body.innerHTML = '';
      if (caption) caption.textContent = '';
      modal.hidden = true;
      previewPlayer = null;
    }}

    function openCreativePreview(type, url, label) {{
      const modal = document.getElementById('creativePreview');
      const body = document.getElementById('creativePreviewBody');
      const caption = document.getElementById('creativePreviewCaption');
      if (!modal || !body || !url) return;
      body.innerHTML = '';
      if (type === 'embed') {{
        const iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.allow = 'autoplay; encrypted-media; picture-in-picture';
        iframe.allowFullscreen = true;
        iframe.title = label || 'Video preview';
        body.appendChild(iframe);
      }} else if (type === 'video') {{
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        body.appendChild(video);
        previewPlayer = video;
      }} else {{
        const img = document.createElement('img');
        img.src = url;
        img.alt = label || 'Creative preview';
        body.appendChild(img);
      }}
      if (caption) caption.textContent = label || '';
      modal.hidden = false;
    }}

    function buildNameCell(r, platform, level, depth) {{
      const pad = 8 + depth * 20;
      const tag = levelLabel(platform, level)
        ? `<span class="entity-tag">${{escHtml(levelLabel(platform, level))}}</span>` : '';
      let inner = `${{tag}}${{escHtml(r.name || '—')}}`;
      if (level === 'ad' || level === 'creative') {{
        const thumb = buildThumbButton(r);
        const creativeSub = (r.creative_name && r.creative_name !== r.name)
          ? `<span class="ad-creative-sub">${{escHtml(r.creative_name)}}</span>` : '';
        const typeBadge = r.media_type
          ? `<span class="ad-creative-sub">${{escHtml(r.media_type)}}</span>` : '';
        inner = `<div class="name-inner">${{thumb}}<span><span>${{tag}}${{escHtml(r.name || '—')}}</span>${{creativeSub}}${{typeBadge}}</span></div>`;
      }}
      return `<td class="name" style="padding-left:${{pad}}px">${{inner}}</td>`;
    }}

    function buildGa4Cells(platform, level, rowId) {{
      const platformMetrics = ga4CampaignMetrics[platform];
      if (!platformMetrics) return '';
      // LinkedIn GA4 attribution matches API campaigns (UI ad sets), not campaign groups.
      if (level !== 'campaign') {{
        return '<td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td>';
      }}
      const metrics = platformMetrics[String(rowId || '')] || {{}};
      const sessions = metrics.sessions || 0;
      if (!sessions) {{
        return '<td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td>';
      }}
      const engaged = metrics.engaged_sessions || 0;
      const keyEvents = metrics.key_events || 0;
      return `<td class="num ga4-col">${{fmtInt(sessions)}}</td>`
        + `<td class="num ga4-col">${{fmtPct(engaged, sessions)}}</td>`
        + `<td class="num ga4-col">${{fmtInt(keyEvents)}}</td>`;
    }}

    function blTablePrefixColCount() {{
      let count = 1;
      if (SHOW_SEGMENT_FILTERS) count += 1;
      if (SHOW_PRODUCT_LINE_FILTERS) count += 1;
      return count;
    }}

    function treeNameColIndex(table) {{
      if (table?.id === 'blTable') return blTablePrefixColCount() + 1;
      return 1;
    }}

    function treeEmptyRowCells(table, depth) {{
      const nameIdx = treeNameColIndex(table);
      const cols = table?.querySelectorAll('thead th').length || 8;
      const colspan = cols - nameIdx - 1;
      const beforeName = '<td></td>'.repeat(nameIdx);
      return `${{beforeName}}<td class="name muted" style="padding-left:${{8 + (depth + 1) * 20}}px">No child rows for this period</td><td colspan="${{colspan}}"></td>`;
    }}

    function buildTreeRow(r, platform, level, depth, prefixCellsHtml = '', includeGa4 = true) {{
      const spend = r.spend || 0;
      const clicks = r.clicks || 0;
      const impressions = r.impressions || 0;
      const conv = r.conversions || 0;
      const cpc = clicks ? fmtMoney(spend / clicks) : '—';
      const expandable = isExpandable(platform, level);
      const chevron = expandable
        ? '<span class="tree-chevron" aria-hidden="true">▸</span>'
        : '<span class="tree-chevron leaf"></span>';
      const tr = document.createElement('tr');
      tr.className = `tree-row tree-depth-${{depth}}${{expandable ? ' tree-expandable' : ''}}`;
      tr.dataset.platform = platform;
      tr.dataset.level = level;
      tr.dataset.id = r.id;
      tr.dataset.depth = String(depth);
      if (expandable) {{
        tr.tabIndex = 0;
        tr.setAttribute('role', 'button');
        tr.setAttribute('aria-expanded', 'false');
      }}
      const ga4Cells = includeGa4 ? buildGa4Cells(platform, level, r.id) : '';
      tr.innerHTML = `
        <td class="chevron-col">${{chevron}}</td>
        ${{prefixCellsHtml}}
        ${{buildNameCell(r, platform, level, depth)}}
        <td class="num">${{fmtMoney(spend)}}</td>
        <td class="num">${{fmtInt(clicks)}}</td>
        <td class="num">${{fmtInt(impressions)}}</td>
        <td class="num">${{fmtPct(clicks, impressions)}}</td>
        <td class="num">${{fmtInt(conv)}}</td>
        <td class="num">${{cpc}}</td>
        ${{ga4Cells}}`;
      return tr;
    }}

    function blRootLevel(platform) {{
      return platform === 'linkedin' ? 'campaign_group' : 'campaign';
    }}

    function buildBlCampaignRow(r) {{
      const platform = r.platform;
      const blCell = SHOW_SEGMENT_FILTERS
        ? `<td><span class="bl-tag">${{escHtml(r.business_line_label)}}</span></td>`
        : '';
      const productCell = SHOW_PRODUCT_LINE_FILTERS
        ? `<td><span class="bl-tag">${{escHtml(r.product_line_label || '—')}}</span></td>`
        : '';
      const prefixCells = `
        <td><span class="platform-pill ${{escHtml(platform)}}">${{escHtml(r.platform_label)}}</span></td>${{blCell}}${{productCell}}`;
      const level = r.entity_level || blRootLevel(platform);
      return buildTreeRow(r, platform, level, 0, prefixCells, false);
    }}

    function collapseDescendants(row) {{
      const depth = parseInt(row.dataset.depth || '0', 10);
      let next = row.nextElementSibling;
      while (next && next.classList.contains('tree-row') && parseInt(next.dataset.depth || '0', 10) > depth) {{
        const rm = next;
        next = next.nextElementSibling;
        rm.remove();
      }}
      row.classList.remove('expanded');
      row.setAttribute('aria-expanded', 'false');
    }}

    function toggleTreeRow(row) {{
      if (!row.classList.contains('tree-expandable')) return;
      if (row.classList.contains('expanded')) {{
        collapseDescendants(row);
        return;
      }}
      const platform = row.dataset.platform;
      const level = row.dataset.level;
      const id = row.dataset.id;
      const depth = parseInt(row.dataset.depth || '0', 10);
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return;
      const children = childRows(platform, level, id);
      let insertAfter = row;
      const table = row.closest('table');
      const isBlTable = table?.id === 'blTable';
      const childPrefix = isBlTable ? '<td></td>'.repeat(blTablePrefixColCount()) : '';
      if (!children.length) {{
        const empty = document.createElement('tr');
        empty.className = 'tree-row tree-empty';
        empty.dataset.depth = String(depth + 1);
        empty.innerHTML = treeEmptyRowCells(table, depth + 1);
        insertAfter.after(empty);
      }} else {{
        for (const child of children) {{
          const childRow = buildTreeRow(
            child,
            platform,
            rule.childLevel,
            depth + 1,
            childPrefix,
            !isBlTable
          );
          insertAfter.after(childRow);
          insertAfter = childRow;
        }}
      }}
      row.classList.add('expanded');
      row.setAttribute('aria-expanded', 'true');
    }}

    document.querySelectorAll('.tree-table').forEach(tbody => {{
      tbody.addEventListener('click', e => {{
        if (e.target.closest('.ad-thumb-btn')) return;
        const row = e.target.closest('tr.tree-expandable');
        if (!row || !tbody.contains(row)) return;
        e.preventDefault();
        e.stopPropagation();
        toggleTreeRow(row);
      }});
      tbody.addEventListener('keydown', e => {{
        const row = e.target.closest('tr.tree-expandable');
        if (!row || !tbody.contains(row)) return;
        if (e.key === 'Enter' || e.key === ' ') {{
          e.preventDefault();
          toggleTreeRow(row);
        }}
      }});
    }});

    document.body.addEventListener('click', e => {{
      const btn = e.target.closest('.ad-thumb-btn');
      if (btn) {{
        e.preventDefault();
        e.stopPropagation();
        const type = btn.dataset.previewType || 'image';
        const url = btn.dataset.previewUrl || '';
        const label = btn.closest('.name-inner')?.innerText?.trim() || 'Creative preview';
        openCreativePreview(type, url, label);
        return;
      }}
      if (e.target.closest('[data-close-preview]')) {{
        closeCreativePreview();
      }}
    }});
    document.addEventListener('keydown', e => {{
      if (e.key === 'Escape') closeCreativePreview();
    }});

    if ((performanceChartRaw.labels || []).length) {{
      refreshCharts();
    }}

    applyGlobalFilters();
    {_dashboard_topbar_js()}
  </script>
</body>
</html>"""
