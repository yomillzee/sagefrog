"""Main Penn dashboard page renderer."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

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
    SIDEBAR_CSS as _SIDEBAR_CSS,
    dashboard_topbar_js as _dashboard_topbar_js,
    favicon_head_html as _favicon_head_html,
    render_client_shell_page,
    render_sidebar as _render_sidebar,
    sidebar_view_nav_html as _sidebar_view_nav_html,
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
        <div id="websiteAiFilterRow" class="website-ai-filter-row" hidden>
          <span class="ai-filter-badge">
            <svg class="ai-spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 2l1.9 5.1L19 9l-5.1 1.9L12 16l-1.9-5.1L5 9l5.1-1.9L12 2z"/>
              <path d="M19 14l.9 2.4L22 17l-2.1.6L19 20l-.9-2.4L16 17l2.1-.6L19 14z" opacity="0.7"/>
            </svg>
            AI traffic
          </span>
          <div id="aiFilterPills" class="filter-toggles ai-filter-pills" role="group" aria-label="AI traffic source"></div>
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
        range_label = f"{dr.get('start')} â†’ {dr.get('end')}"
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
    """Legacy wrapper â€” website content lives on the Website Analytics view."""
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
                placeholder="Filter by page path or titleâ€¦" autocomplete="off">
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
            "Use the filters at the top to narrow regions, product lines, and channels â€” "
            "all are included by default."
        )
    elif show_segment_filters:
        filter_note = (
            f"Use the filters at the top to narrow {segment_column_label.lower()}s and channels â€” "
            "all are included by default."
        )
    else:
        filter_note = "Use the channel filters at the top to narrow platforms â€” all are included by default."
    empty_colspan = 10 + int(show_segment_filters) + int(show_product_line_column)
    return f"""
            <section class="campaign-explorer-section" aria-label="Campaign performance">
              <div class="bl-summary" id="blSummary"></div>
              <section class="panel platform-panel">
                <div class="panel-head">
                  <h2>Campaign performance</h2>
                  <span class="badge" id="blRowCount">0 rows</span>
                </div>
                <p class=â€table-noteâ€>{filter_note} Expand a row to drill into ad groups and ads.</p>
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
    """Deprecated â€” use _campaign_explorer_content_html with filters in _global_filters_bar_html."""
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
   …38411 tokens truncated…um">${{fmtInt(row.users)}}</td>
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

    // --- AI Traffic filter (website analytics tab only) --------------------
    (function initAiTraffic() {{
      const pillsWrap    = document.getElementById('aiFilterPills');
      const pagesHeading = document.getElementById('ga4PagesHeading');
      const aiRow        = document.getElementById('websiteAiFilterRow');
      if (!pillsWrap) return;
      // No AI-referred sessions in this snapshot â€” keep the control hidden.
      if (!SHOW_AI_TRAFFIC) {{ if (aiRow) aiRow.hidden = true; return; }}
      if (aiRow) aiRow.hidden = false;

      // ---- pages table bridge ----------------------------------------------
      let aiModeActive   = false;
      let aiActiveSource = null;

      function getAiPages(sourceId) {{
        return (aiPagesBySource[sourceId || 'all'] || []);
      }}

      // Wrap renderGa4Pages so pagination / search / segment filters
      // keep serving AI pages while AI mode is on.
      const _origRenderGa4Pages = renderGa4Pages;
      renderGa4Pages = function() {{
        if (!aiModeActive) {{ _origRenderGa4Pages(); return; }}
        const backup = [...ga4Pages];
        ga4Pages.splice(0, ga4Pages.length, ...getAiPages(aiActiveSource));
        _origRenderGa4Pages();
        ga4Pages.splice(0, ga4Pages.length, ...backup);
      }};

      function openAiMode(sourceId) {{
        // Clear paid-channel filters to avoid conflicts.
        channelState.clear();
        syncToggleGroup('channel');

        aiModeActive   = true;
        aiActiveSource = sourceId;
        if (pagesHeading) {{
          const lbl = sourceId
            ? (aiSources.find(s => s.id === sourceId) || {{}}).label || sourceId
            : 'All AI';
          pagesHeading.textContent = `Page performance â€” AI Traffic (${{lbl}})`;
        }}
        renderGa4Pages();
      }}

      function clearAiMode() {{
        aiModeActive   = false;
        aiActiveSource = null;
        if (pagesHeading) pagesHeading.textContent = 'Page performance';
        pillsWrap.querySelectorAll('.filter-toggle').forEach(b => {{
          b.classList.remove('active');
          b.setAttribute('aria-pressed', 'false');
        }});
        renderGa4Pages();
      }}

      // ---- build pills -----------------------------------------------------
      function makePill(label, sourceId, count) {{
        const btn = document.createElement('button');
        btn.type  = 'button';
        btn.className = 'filter-toggle filter-toggle--ai';
        btn.dataset.source = sourceId || '';
        btn.setAttribute('aria-pressed', 'false');
        const text = document.createElement('span');
        text.textContent = label;
        btn.appendChild(text);
        if (count != null) {{
          const chip = document.createElement('span');
          chip.className = 'ai-pill-count';
          chip.textContent = Number(count).toLocaleString();
          btn.appendChild(chip);
        }}
        return btn;
      }}

      // "All AI" aggregates every source's sessions
      const totalAiSessions = aiSources.reduce((sum, s) => sum + (s.sessions || 0), 0);
      pillsWrap.appendChild(makePill('All AI', '', totalAiSessions));
      // Per-source pills, each with its own session count
      aiSources.forEach(s => pillsWrap.appendChild(makePill(s.label, s.id, s.sessions)));

      pillsWrap.addEventListener('click', e => {{
        const btn = e.target.closest('.filter-toggle');
        if (!btn) return;
        const src = btn.dataset.source || null;
        // Clicking the active pill deactivates AI mode
        if (btn.classList.contains('active')) {{
          clearAiMode();
          return;
        }}
        pillsWrap.querySelectorAll('.filter-toggle').forEach(b => {{
          b.classList.remove('active');
          b.setAttribute('aria-pressed', 'false');
        }});
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        openAiMode(src);
      }});
    }})();

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
      const play = playable ? '<span class="ad-play-icon" aria-hidden="true">â–¶</span>' : '';
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
      let inner = `${{tag}}${{escHtml(r.name || 'â€”')}}`;
      if (level === 'ad' || level === 'creative') {{
        const thumb = buildThumbButton(r);
        const creativeSub = (r.creative_name && r.creative_name !== r.name)
          ? `<span class="ad-creative-sub">${{escHtml(r.creative_name)}}</span>` : '';
        const typeBadge = r.media_type
          ? `<span class="ad-creative-sub">${{escHtml(r.media_type)}}</span>` : '';
        const headlines = Array.isArray(r.headlines) ? r.headlines.slice(0, 3) : [];
        const headlinesSub = headlines.length
          ? `<span class="ad-headlines">${{headlines.map((h, i) => `<span class="ad-headline" data-i="${{i + 1}}" title="${{escHtml(h)}}">${{escHtml(h)}}</span>`).join('')}}</span>`
          : '';
        inner = `<div class="name-inner">${{thumb}}<span><span>${{tag}}${{escHtml(r.name || 'â€”')}}</span>${{creativeSub}}${{typeBadge}}${{headlinesSub}}</span></div>`;
      }}
      return `<td class="name" style="padding-left:${{pad}}px">${{inner}}</td>`;
    }}

    function buildGa4Cells(platform, level, rowId) {{
      const platformMetrics = ga4CampaignMetrics[platform];
      if (!platformMetrics) return '';
      // LinkedIn GA4 attribution matches API campaigns (UI ad sets), not campaign groups.
      if (level !== 'campaign') {{
        return '<td class="num ga4-col muted">â€”</td><td class="num ga4-col muted">â€”</td><td class="num ga4-col muted">â€”</td>';
      }}
      const metrics = platformMetrics[String(rowId || '')] || {{}};
      const sessions = metrics.sessions || 0;
      if (!sessions) {{
        return '<td class="num ga4-col muted">â€”</td><td class="num ga4-col muted">â€”</td><td class="num ga4-col muted">â€”</td>';
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
      const cpc = clicks ? fmtMoney(spend / clicks) : 'â€”';
      const expandable = isExpandable(platform, level);
      const chevron = expandable
        ? '<span class="tree-chevron" aria-hidden="true">â–¸</span>'
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
        ? `<td><span class="bl-tag">${{escHtml(r.product_line_label || 'â€”')}}</span></td>`
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
