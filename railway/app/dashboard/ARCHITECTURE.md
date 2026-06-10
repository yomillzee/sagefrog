# Dashboard package architecture

Incremental refactor of the monolithic `dashboard_service.py` module. Behavior, routes, and public APIs stay unchanged until each pass is complete.

## Folder layout

```
dashboard/
  ARCHITECTURE.md          # This file
  routes/                  # (Pass 4) FastAPI route handlers
  services/                # (Pass 3) Refresh, warehouse sync, metrics loading
  renderers/               # (Pass 2) HTML builders (cards, tables, panels, settings)
  utils/                   # (Pass 1) Pure helpers — done
    formatting.py          # Money/int/%, HTML escape, JSON-in-script, icons
    urls.py                  # Dashboard URL builders (session vs ?key=)
    auth.py                  # Secret key, refresh cooldown, edit permissions
    dates.py                 # Budget input parsing, MTD bounds, daily spend maps
```

`dashboard_service.py` remains the compatibility façade: it re-exports public functions and wires renderers/services until later passes finish.

## Pass 1 (complete): `utils/`

| Module | Functions moved |
|--------|-----------------|
| `formatting.py` | `fmt_money`, `fmt_int`, `fmt_pct`, `fmt_cpa`, `esc`, `json_for_html_script`, `platform_error`, `fmt_file_size`, `fmt_short_date`, `entity_level_label`, `platform_title_html`, `file_type_icon_html`, `folder_icon_html` |
| `urls.py` | `dashboard_page_url`, `settings_page_url`, `files_page_url`, `time_tracking_page_url`, `insights_upload_page_url`, `refresh_action_url`, `insights_action_url`, `insight_documents_action_url`, `insight_document_download_url`, `insight_document_delete_url`, `insight_document_move_url`, `insight_folder_action_url`, `insight_folder_delete_url`, `client_switch_target_url` |
| `auth.py` | `configured_dashboard_secret`, `min_refresh_seconds`, `parse_refreshed_at`, `refresh_cooldown_status`, `verify_dashboard_key`, `can_edit_penn_insights` |
| `dates.py` | `parse_monthly_budget_input`, `mtd_calendar_bounds`, `paid_daily_spend_map` |

Imports in `dashboard_service.py` use the same private names (`_esc`, `_fmt_money`, etc.) via aliases so call sites inside the file did not need rewrites.

## Where to add things (after full refactor)

| Task | Start here |
|------|------------|
| New dashboard page or tab | `routes/dashboard_routes.py` → `renderers/dashboard_renderer.py` |
| Settings / theme / OAuth UI | `routes/settings_routes.py` → `renderers/settings_renderer.py` |
| Files / insight documents UI | `routes/files_routes.py` → `renderers/files_renderer.py` |
| Refresh / sync behavior | `routes/refresh_routes.py` → `services/refresh_service.py` |
| Platform API pulls, warehouse writes | `services/warehouse_metrics_service.py` |
| Budget pacing math / panel | `services/budget_pacing_service.py` → `renderers/cards_renderer.py` |
| Summary cards, platform tables | `renderers/cards_renderer.py`, `renderers/tables_renderer.py` |
| Formatting, URLs, auth keys | `utils/formatting.py`, `utils/urls.py`, `utils/auth.py` |
| Date ranges, MTD pacing | `utils/dates.py` (shared) + `dates_util.py` (global presets) |

## What Cursor should inspect first

1. **Bug in filter UI or chart JS** — `dashboard_service.py` (`render_penn_html` inline script) until Pass 2 moves renderers.
2. **Bug in refresh / missing data** — `dashboard_service.py` refresh functions until Pass 3; then `services/refresh_service.py`.
3. **New route or HTTP behavior** — `main.py` (registers routes today); later `dashboard/routes/`.
4. **Business line / campaign classification** — `penn_business_lines.py`, not this package.
5. **Auth / admin / users** — `web_auth.py`, `web_users.py`; dashboard key helpers in `dashboard/utils/auth.py`.
6. **Formatting or link URLs in HTML** — `dashboard/utils/formatting.py`, `dashboard/utils/urls.py`.

## Next passes (not started)

- **Pass 2:** Move HTML renderers (`_summary_card`, `_drillable_table`, `_budget_pacing_panel_html`, files/settings pages, etc.) into `renderers/`.
- **Pass 3:** Move `refresh_client`, warehouse sync, daily metrics loading into `services/`.
- **Pass 4:** Move FastAPI handlers from `main.py` into `dashboard/routes/`.

Each pass should end with `python3 -m py_compile` on touched modules and a full `import dashboard_service; import main` check.
