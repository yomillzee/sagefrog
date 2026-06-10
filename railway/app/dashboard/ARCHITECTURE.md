# Dashboard package architecture

Incremental refactor of the monolithic `dashboard_service.py` module. Behavior, routes, and public APIs stay unchanged until each pass is complete.

## Folder layout

```
dashboard/
  ARCHITECTURE.md          # This file
  routes/                  # (Pass 4) FastAPI route handlers — done
    helpers.py             # Slug validation, session kwargs, files redirects
    settings_routes.py     # GET/POST /dashboard/{slug}/settings
    core_routes.py         # Dashboard HTML, refresh, insights, JSON, cron sync
    files_routes.py        # Files browser, time tracking, insight documents
  services/                # (Pass 3) Refresh, warehouse sync, metrics loading — done
    refresh_service.py       # refresh_client, refresh_client_quick, save_penn_insights
    warehouse_metrics_service.py  # penn_sync_warehouses, daily metrics load, LinkedIn media
    snapshot_metrics_service.py   # totals, breakdowns, budget pacing for renderers
  renderers/               # (Pass 2) HTML builders — done
    base_layout.py         # Favicon, topbar, shell pages, refresh toolbar
    cards_renderer.py      # Summary cards, hero metrics, budget pacing panel
    tables_renderer.py     # Drill-down platform tables, GA4 row cells
    settings_renderer.py   # Insights editor + overview card
    files_renderer.py      # Files browser, time-tracking pages
    dashboard_renderer.py  # Main Penn dashboard (render_penn_html + tabs/filters)
  utils/                   # (Pass 1) Pure helpers — done
    formatting.py
    urls.py
    auth.py
    dates.py               # Also WAREHOUSE_DATE_RANGES constant
```

`dashboard_service.py` remains the compatibility façade (~130 lines): re-exports of utils, services, and renderers so `main.py`, `dashboard_settings.py`, and cron jobs need no import changes.

`main.py` calls `register_dashboard_routes(app)` to attach all `/dashboard/*` handlers.

## Pass 1 (complete): `utils/`

| Module | Functions |
|--------|-----------|
| `formatting.py` | `fmt_money`, `fmt_int`, `fmt_pct`, `fmt_cpa`, `esc`, `json_for_html_script`, `platform_error`, `fmt_file_size`, `fmt_short_date`, `entity_level_label`, `platform_title_html`, `file_type_icon_html`, `folder_icon_html` |
| `urls.py` | All dashboard URL builders (`dashboard_page_url`, `settings_page_url`, `files_page_url`, insight document/folder URLs, `client_switch_target_url`, etc.) |
| `auth.py` | `configured_dashboard_secret`, `min_refresh_seconds`, `refresh_cooldown_status`, `verify_dashboard_key`, `can_edit_penn_insights` |
| `dates.py` | `parse_monthly_budget_input`, `mtd_calendar_bounds`, `paid_daily_spend_map`, `WAREHOUSE_DATE_RANGES` |

## Pass 2 (complete): `renderers/`

| Module | Functions moved |
|--------|-----------------|
| `base_layout.py` | `favicon_head_html`, `session_account_html`, `refresh_toolbar`, `topbar_client_selector_html`, `dash_top_header_html`, `DASH_TOPBAR_CSS`, `dashboard_topbar_js`, `dashboard_view_tabs_html`, `render_client_shell_page` |
| `cards_renderer.py` | `summary_cards_html`, `summary_card`, `aggregated_card`, `budget_pacing_panel_html`, `ga4_paid_key_events`, `paid_ad_overview_metrics`, `paid_ad_overview_html` |
| `tables_renderer.py` | `rows_for_display`, `GA4_TABLE_HEADERS`, `ga4_row_cells`, `drillable_table`, `entity_table`, `ga4_platform_reports`, `platform_breakdown_html`, `platform_site_impact_html` |
| `settings_renderer.py` | `insights_from_snapshot`, `format_insights_body_html`, `insights_editor_html`, `insights_card_html` |
| `files_renderer.py` | `files_breadcrumb_html`, `client_files_browser_html`, `files_page_css`, `files_page_js`, `render_files_page`, `render_insights_upload_page`, `time_tracking_page_css`, `time_tracking_page_js`, `render_time_tracking_page` |
| `dashboard_renderer.py` | `ga4_website_search_html`, `ga4_metrics_summary_html`, `ga4_website_content_html`, `ga4_pages_panel_html`, `global_filters_bar_html`, `campaign_explorer_content_html`, `business_line_merged_section_html`, **`render_penn_html`** |

`dashboard_service.py` re-exports private renderer names with `_` aliases (e.g. `_drillable_table`) so `dashboard_settings.py` and internal code need no changes.

`render_penn_html` imports snapshot data helpers from `dashboard.services.snapshot_metrics_service` to avoid circular imports with `dashboard_service`.

## Pass 3 (complete): `services/`

| Module | Functions |
|--------|-----------|
| `refresh_service.py` | `refresh_client`, `refresh_client_quick`, `refresh_penn`, `refresh_penn_quick`, `patch_snapshot_from_config`, `save_penn_insights` |
| `warehouse_metrics_service.py` | `totals_from_daily_rows`, `penn_sync_warehouses`, `penn_load_daily_metrics_from_warehouse`, `load_mtd_daily_metrics`, `load_organic_daily_metrics`, `merge_linkedin_creative_media`, `sync_meta` |
| `snapshot_metrics_service.py` | `normalize_entity_row`, `account_totals`, `hydrate_platform_totals`, `platforms_with_summary_data`, `aggregated_paid_media`, `build_budget_pacing_payload`, `breakdowns_from_snapshot`, `business_line_campaigns_from_snapshot` |

## Pass 4 (complete): `routes/`

| Module | Routes |
|--------|--------|
| `settings_routes.py` | `GET/POST /dashboard/{client_slug}/settings` |
| `core_routes.py` | `GET /dashboard/{client_slug}`, `GET /dashboard/penn`, `POST /dashboard/{client_slug}/refresh`, `POST /dashboard/{client_slug}/insights`, `GET /dashboard/{client_slug}.json`, `POST /internal/sync-penn` |
| `files_routes.py` | Files browser, time tracking, insight document/folder upload/download/delete/move |
| `helpers.py` | `validate_client_slug`, session kwargs, files flash redirects, JSON API helpers |

OAuth routes (`/oauth/{platform}/*`) remain in `main.py` (admin tooling, not client dashboard).

## Where to add things

| Task | Start here |
|------|------------|
| New dashboard tab / filter UI / chart JS | `renderers/dashboard_renderer.py` |
| Summary cards, hero row, budget pacing panel | `renderers/cards_renderer.py` |
| Platform drill-down tables | `renderers/tables_renderer.py` |
| Topbar, shell layout, refresh toolbar | `renderers/base_layout.py` |
| Insights editor / card | `renderers/settings_renderer.py` |
| Files browser, time tracking page | `renderers/files_renderer.py` |
| Settings page body (theme, OAuth) | `dashboard_settings.py` (not moved yet) |
| Refresh / sync behavior | `services/refresh_service.py` |
| Platform API pulls, warehouse writes | `services/warehouse_metrics_service.py` |
| Snapshot totals, breakdowns, budget pacing | `services/snapshot_metrics_service.py` |
| New `/dashboard/*` HTTP routes | `routes/` (appropriate module) |
| Formatting, URLs, auth keys | `dashboard/utils/` |

## What Cursor should inspect first

1. **Filter UI, chart JS, Campaign Explorer** — `renderers/dashboard_renderer.py` (`render_penn_html` inline script).
2. **Platform table rows / GA4 columns** — `renderers/tables_renderer.py`.
3. **Overview cards / budget pacing HTML** — `renderers/cards_renderer.py`.
4. **Topbar / client switcher** — `renderers/base_layout.py`.
5. **Refresh / missing data / warehouse** — `services/refresh_service.py`, `services/warehouse_metrics_service.py`.
6. **Dashboard HTTP handlers** — `routes/core_routes.py`, `routes/files_routes.py`, `routes/settings_routes.py`.
7. **Business line classification** — `penn_business_lines.py`.
8. **Auth / admin** — `web_auth.py`, `dashboard/utils/auth.py`.

Each pass should end with `python3 -m py_compile` on touched modules and `import dashboard_service; import main`.
