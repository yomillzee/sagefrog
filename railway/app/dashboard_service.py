"""Refresh Penn dashboard data and render HTML from Postgres snapshots.

Compatibility façade: re-exports utils, services, and renderers so existing
imports (`import dashboard_service`, `dashboard_service.refresh_penn`, etc.)
keep working unchanged.
"""

from __future__ import annotations

from dashboard.utils.auth import (
    can_edit_penn_insights,
    min_refresh_seconds,
    refresh_cooldown_status,
)
from dashboard.utils.dates import (
    mtd_calendar_bounds as _mtd_calendar_bounds,
    paid_daily_spend_map as _paid_daily_spend_map,
    parse_monthly_budget_input,
)
from dashboard.utils.formatting import (
    entity_level_label as _entity_level_label,
    esc as _esc,
    file_type_icon_html as _file_type_icon_html,
    folder_icon_html as _folder_icon_html,
    fmt_cpa as _fmt_cpa,
    fmt_file_size as _fmt_file_size,
    fmt_int as _fmt_int,
    fmt_money as _fmt_money,
    fmt_pct as _fmt_pct,
    fmt_short_date as _fmt_short_date,
    json_for_html_script as _json_for_html_script,
    platform_error as _platform_error,
    platform_title_html as _platform_title_html,
)
from dashboard.utils.urls import (
    client_switch_target_url as _client_switch_target_url,
    dashboard_page_url as _dashboard_page_url,
    files_page_url as _files_page_url,
    insight_document_delete_url as _insight_document_delete_url,
    insight_document_download_url as _insight_document_download_url,
    insight_document_move_url as _insight_document_move_url,
    insight_documents_action_url as _insight_documents_action_url,
    insight_folder_action_url as _insight_folder_action_url,
    insight_folder_delete_url as _insight_folder_delete_url,
    insights_action_url as _insights_action_url,
    insights_upload_page_url as _insights_upload_page_url,
    refresh_action_url as _refresh_action_url,
    settings_page_url as _settings_page_url,
)

# --- Pass 3 services (re-exported for backward compatibility) ---
from dashboard.services.refresh_service import (
    patch_snapshot_from_config,
    refresh_bq_client,
    refresh_client,
    refresh_client_quick,
    refresh_penn,
    refresh_penn_quick,
    save_penn_insights,
)
from dashboard.services.snapshot_metrics_service import (
    account_totals as _account_totals,
    aggregated_paid_media as _aggregated_paid_media,
    breakdowns_from_snapshot as _breakdowns_from_snapshot,
    build_budget_pacing_payload as _build_budget_pacing_payload,
    business_line_campaigns_from_snapshot as _business_line_campaigns_from_snapshot,
    hydrate_platform_totals as _hydrate_platform_totals,
    normalize_entity_row as _normalize_entity_row,
    platforms_with_summary_data as _platforms_with_summary_data,
)
from dashboard.services.warehouse_metrics_service import (
    load_mtd_daily_metrics as _load_mtd_daily_metrics,
    load_organic_daily_metrics as _load_organic_daily_metrics,
    merge_linkedin_creative_media as _merge_linkedin_creative_media,
    penn_load_daily_metrics_from_warehouse as _penn_load_daily_metrics_from_warehouse,
    penn_sync_warehouses as _penn_sync_warehouses,
    sync_meta as _sync_meta,
    totals_from_daily_rows as _totals_from_daily_rows,
)

# --- Pass 2 renderers (re-exported for backward compatibility) ---
from dashboard.renderers.base_layout import (
    DASH_TOPBAR_CSS as _DASH_TOPBAR_CSS,
    dash_top_header_html as _dash_top_header_html,
    dashboard_topbar_js as _dashboard_topbar_js,
    dashboard_view_tabs_html as _dashboard_view_tabs_html,
    favicon_head_html as _favicon_head_html,
    refresh_toolbar as _refresh_toolbar,
    render_client_shell_page,
    session_account_html as _session_account_html,
    topbar_client_selector_html as _topbar_client_selector_html,
)
from dashboard.renderers.cards_renderer import (
    aggregated_card as _aggregated_card,
    budget_pacing_panel_html as _budget_pacing_panel_html,
    ga4_paid_key_events as _ga4_paid_key_events,
    paid_ad_overview_html as _paid_ad_overview_html,
    paid_ad_overview_metrics as _paid_ad_overview_metrics,
    summary_card as _summary_card,
    summary_cards_html as _summary_cards_html,
)
from dashboard.renderers.files_renderer import (
    client_files_browser_html as _client_files_browser_html,
    files_breadcrumb_html as _files_breadcrumb_html,
    files_page_css as _files_page_css,
    files_page_js as _files_page_js,
    render_files_page,
    render_insights_upload_page,
)
from dashboard.renderers.settings_renderer import (
    format_insights_body_html as _format_insights_body_html,
    insights_card_html as _insights_card_html,
    insights_editor_html as _insights_editor_html,
    insights_from_snapshot as _insights_from_snapshot,
)
from dashboard.renderers.tables_renderer import (
    GA4_TABLE_HEADERS as _GA4_TABLE_HEADERS,
    drillable_table as _drillable_table,
    entity_table as _entity_table,
    ga4_platform_reports as _ga4_platform_reports,
    ga4_row_cells as _ga4_row_cells,
    platform_breakdown_html as _platform_breakdown_html,
    platform_site_impact_html as _platform_site_impact_html,
    rows_for_display as _rows_for_display,
)
