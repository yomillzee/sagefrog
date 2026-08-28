# Dashboard package architecture

The `dashboard/` package holds the client-facing dashboard: one master HTML
template plus per-feature renderers, the HTTP routes, and the services that load
and refresh data.

## The single master template

**Every client's dashboard renders through one function:**
`render_bigquery_dashboard_page()` in
`renderers/bigquery_dashboard_renderer.py`.

There is **no per-client renderer**. The route `GET /dashboard/{client_slug}`
(`routes/core_routes.py`) calls the master template for every slug. The template
renders a shell that self-fetches its data client-side from `/api/clients/{slug}/*`.

Per-client differences are driven by **configuration**, not by branching on a
client's name:

| Difference | Driven by |
|------------|-----------|
| Segment filters (business lines vs. regions) | `segment_filter_profile` on `client_dashboard_config` (see `penn_business_lines.py`) |
| Marketing-mart destination (project/dataset) | `gcp_project_id` / `bq_mart_dataset_id` on `client_dashboard_config` |
| Theme | `theme_json` on `client_dashboard_config` (`dashboard_theme.py`) |
| Feature flags | `features_json` (`dashboard_features.py`) |
| Budget / KPIs | `monthly_budget_usd`, `primary_kpi` |

**Rule:** no client name (`nixon`, `penn`, …) belongs in renderer or data-layer
control flow. Slugs are URL/identity keys only; everything else is config/DB data.
An edit to the master template applies to all dashboards app-wide — that is the point.

> History: an earlier `dashboard_renderer.py` (`render_penn_html`) server-rendered
> a Penn-specific snapshot dashboard, and a `bq_dashboard_renderer.py` was a
> parallel BigQuery renderer. Both have been removed. The BigQuery template is
> now the only dashboard renderer. If you find a doc or comment referencing
> `render_penn_html`, it is stale.

## Folder layout

```
dashboard/
  ARCHITECTURE.md          # This file
  routes/                  # FastAPI route handlers
    helpers.py             # Slug validation, session kwargs, files redirects
    core_routes.py         # Dashboard HTML, refresh, insights, JSON, cron sync
    settings_routes.py     # GET/POST /dashboard/{slug}/settings
    api_routes.py          # /api/clients/{slug}/* JSON the dashboard self-fetches
    files_routes.py        # Files browser and insight documents
    connector_routes.py    # Connector config CRUD
    consent_routes.py, accessibility_routes.py, notes_routes.py
    web_mentions_routes.py # Web Mentions page, alert admin, RSS ingest cron
  renderers/               # HTML builders
    bigquery_dashboard_renderer.py  # ★ THE master dashboard template
    base_layout.py         # Favicon, sidebar/nav, shell pages, refresh toolbar
    bigquery_settings_renderer.py   # Settings page for BigQuery-mode clients
    cards_renderer.py      # Summary cards, hero metrics, budget pacing panel
    tables_renderer.py     # Drill-down platform tables, GA4 row cells
    budget_tracker.py, agency_trends_renderer.py, hq_renderer.py
    lead_tracking_renderer.py, gsc_renderer.py, gtm_renderer.py, semrush_renderer.py
    pagespeed_renderer.py, consent_renderer.py, accessibility_renderer.py
    connectors_renderer.py, client_hours_renderer.py, linkedin_organic_renderer.py
    docs_renderer.py, files_renderer.py, notes_widget.py
    web_mentions_renderer.py        # Google Alerts / web mention reporting
  services/                # Refresh, warehouse sync, metrics loading
    refresh_service.py             # refresh_client, refresh_client_quick
    bigquery_refresh_orchestrator.py
    snapshot_metrics_service.py    # totals, breakdowns, budget pacing for renderers
    warehouse_metrics_service.py   # daily metrics load, LinkedIn media
    agency_trends_service.py, hq_budget_service.py, kpi_registry.py
    dashboard_warm_service.py
  utils/                   # Pure helpers
    formatting.py          # fmt_money, fmt_int, fmt_pct, esc, json_for_html_script, …
    urls.py                # dashboard URL builders
    auth.py                # dashboard secret / refresh cooldown / key verification
    dates.py               # WAREHOUSE_DATE_RANGES, monthly budget parsing
    pacing.py
```

`main.py` calls `register_dashboard_routes(app)` to attach all `/dashboard/*` and
`/api/clients/*` handlers.

## Where to add things

| Task | Start here |
|------|------------|
| Dashboard layout / tabs / a card the whole app should get | `renderers/bigquery_dashboard_renderer.py` (the master template) |
| Sidebar / nav / shell | `renderers/base_layout.py` |
| Summary cards, hero row, budget pacing panel | `renderers/cards_renderer.py` |
| Platform drill-down tables | `renderers/tables_renderer.py` |
| Website Analytics (GA4) | the Analytics pane in `renderers/bigquery_dashboard_renderer.py` (`pane-analytics`) |
| A new per-feature view | a dedicated `renderers/<feature>_renderer.py` |
| Settings page body | `renderers/bigquery_settings_renderer.py`, `dashboard_settings.py` |
| Refresh / sync behavior | `services/refresh_service.py`, `services/bigquery_refresh_orchestrator.py` |
| Snapshot totals, breakdowns, budget pacing | `services/snapshot_metrics_service.py` |
| New `/dashboard/*` or `/api/clients/*` routes | `routes/` (appropriate module) |
| Web Mentions (Google Alerts) | `web_mentions_store.py` / `web_mentions_service.py`, `renderers/web_mentions_renderer.py` |
| Segment classification (business lines / regions) | `penn_business_lines.py` |
| Formatting, URLs, auth keys | `utils/` |

After touching modules, run `python3 -m py_compile` on them and the test suite:
`python -m unittest discover -s tests`.
