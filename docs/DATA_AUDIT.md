# Sagefrog Data Audit — Connectors, Table Provisions, Endpoints & Front-End Calculations

**Purpose.** A single reference that documents how data enters, is stored in, is
served from, and is calculated by the Sagefrog platform, so the team can verify
**data accuracy** (numbers are correct) and **data cleanliness** (no duplication,
stale, or mis-routed data). This is a documentation/audit artifact — it describes
current behavior and flags the specific places where accuracy/cleanliness depends
on assumptions worth confirming.

_Last audited: 2026-07-17 · Scope: `railway/app` + `railway/cron-*`_

---

## 1. Data flow at a glance

```
             ┌─────────────┐   OAuth / API key    ┌──────────────────┐
 Ad & SEO    │  Connector  │  ───────────────────▶│  Source API       │
 platforms   │  handlers   │                       │  (Google, LI, …) │
             └─────┬───────┘                       └────────┬─────────┘
                   │ run_sync()                             │
                   ▼                                        ▼
        ┌───────────────────────┐          ┌────────────────────────────────┐
        │ Raw BigQuery datasets │          │ Postgres warehouse (metrics_    │
        │ raw_google_ads,       │◀────────▶│ daily, campaign_daily) — legacy │
        │ raw_linkedin_ads, …   │          │ Penn path, optional mirror       │
        └──────────┬────────────┘          └────────────────────────────────┘
                   │ CREATE OR REPLACE VIEW
                   ▼
        ┌───────────────────────┐
        │ marketing_marts.*      │  vw_paid_media_daily, mart_health,
        │ fact_* views           │  fact_google/linkedin/meta_*_daily,
        └──────────┬────────────┘  fact_marketing_daily
                   │ SELECT (bq_mart_service, ga4_*_service)
                   ▼
        ┌───────────────────────┐   derived metrics (CTR/CPC/CPA/pacing)
        │ Renderers / API JSON  │  ─────────────────────────────────────▶ Dashboard / ChatGPT
        └───────────────────────┘
```

Two storage backends coexist:

- **BigQuery** — the primary warehouse for all new-build (connector) clients.
  Raw per-source datasets → `marketing_marts` views → read by the dashboard.
- **Postgres** (`metrics_daily`, `campaign_daily`) — the legacy Penn path plus an
  optional BigQuery mirror. Gated by `DATABASE_URL` / `warehouse.enabled()`.

---

## 2. Connectors

All connectors implement `ConnectorHandler` (`connectors/base.py`) with two
required methods — `list_accounts()` (wizard account picker / test) and
`run_sync()` — and register themselves in a global registry. The directory page
order is fixed in `CONNECTOR_ORDER`.

`VALID_CONNECTOR_TYPES` (`connector_config_store.py`) also lists `circle`, but no
handler is registered for it — it is a reserved/manual type, not an active feed.

### 2.1 Registered connectors

| Connector | `connector_type` | Auth model | Account input | Raw dataset | Cron min interval | Writes → |
|---|---|---|---|---|---|---|
| LinkedIn Ads | `linkedin_ads` | Per-client OAuth (`linkedin`) refresh→access token | `list_ad_accounts` picker → `source_account_id` | `raw_linkedin_ads` | every tick | `campaign_daily`, `campaigns`, `ad_daily`, `creative_metadata`; rebuilds `fact_linkedin_ads_campaign_daily` + `vw_paid_media_daily` |
| Meta Ads | `meta_ads` | Per-client OAuth (`meta`), falls back to global token | `list_ad_accounts` (falls back to `/me/adaccounts` if no business_management) | `raw_meta_ads` | every tick | `campaign_daily`, `adset_daily`, `ad_daily`, `ad_creative` + mart views |
| Google Ads | `google_ads` | Per-client OAuth (`google_ads`) refresh token + developer token | `list_accessible_customer_accounts` → `source_account_id` | `raw_google_ads` | every tick | `campaign_daily` → `fact_google_ads_campaign_daily` view + `vw_paid_media_daily` |
| Google Analytics 4 | `ga4` | Per-client OAuth (`google_analytics`, analytics.readonly) | `list_properties` → property id | `raw_ga4` | every tick | GA4 raw tables + `provision_ga4_mart_views` |
| Search Console | `gsc` | **Agency-level** (shared Google OAuth `gsc_read_creds`) with **service-account fallback** (`no_oauth=True`, `agency_oauth=True`) | `list_accessible_properties` → site URL | `raw_gsc` | every tick | `fact_gsc_query_daily`, `fact_gsc_page_daily` + GSC mart views |
| Google Tag Manager | `gtm` | Per-client OAuth (`google_tag_manager`) | `list_containers` → `account:container` in `source_account_id` | `raw_gtm` | every tick | Live-container tag audit (no fact table; `rows_loaded` = tag count) |
| HubSpot | `hubspot` | Per-client OAuth (`hubspot`), falls back to global `HUBSPOT_ACCESS_TOKEN` | Portal behind the token → portal id | `raw_hubspot` (mart dataset) | every tick | Contacts + deals sync |
| SEMrush | `semrush` | **Agency** shared `SEMRUSH_API_KEY` (`no_oauth=True`, `manual_account_entry=True`) | Manually typed root domain | `raw_semrush` | every tick | Domain analytics rows |
| PageSpeed Insights | `pagespeed` | Keyless / shared `PAGESPEED_API_KEY` (`no_oauth=True`, `manual_account_entry=True`) | Manually typed homepage URL | `raw_pagespeed` | **7 days** (`min_sync_interval_days=7`) | Lighthouse scores (per synced strategy) |

### 2.2 Connector accuracy / cleanliness notes

- **Client-scoped token threading.** LinkedIn, Google Ads and GA4 handlers
  explicitly resolve **this client's** refresh token and pass it through, because
  the underlying service layers default to a global/agency token when
  `client_slug` is omitted. This is the guard against one client's sync silently
  reading another client's account. Confirm every new connector follows this
  pattern (see the extended comments in `connectors/linkedin_ads.py` and
  `connectors/google_ads.py`).
- **BQ project routing.** Syncs wrap writes in `bigquery_warehouse.route(bq_project_id=…)`.
  `_linkedin_project_id()` / `_meta_project_id()` now **raise** rather than fall
  back to a default project when nothing is routed — a deliberate change to stop
  cross-client contamination. Any caller that skips `route()` will fail loudly.
- **GTM has no persisted fact table** — `run_sync` performs a live audit and
  returns the tag count as `rows_loaded`. It is a health/audit check, not a time
  series, so it will not appear in the paid-media marts.
- **GSC / SEMrush / PageSpeed are agency-authed**, not per-client. Verify the
  shared credential (service account, `SEMRUSH_API_KEY`, `PAGESPEED_API_KEY`) has
  access to each client's property/domain before trusting an empty result.
- **PageSpeed cron cadence is intentionally ~monthly.** A "no recent rows" reading
  for PageSpeed is expected between the 30-day windows and is not a stall.
- **Sync-run bookkeeping** (`connector_config_store.py`): manual syncs run as
  in-process FastAPI `BackgroundTasks` and do **not** survive a redeploy.
  `fail_orphaned_sync_runs()` at startup closes out `running` rows so a connector
  cannot get stuck showing "syncing" forever. `last_rows_loaded` / `last_sync_range_*`
  record the most recent successful window for the UI.

---

## 3. Table provisions

### 3.1 Datasets

| Dataset | Owner | Contents |
|---|---|---|
| `raw_google_ads` | Google Ads → BQ Data Transfer **or** app sync | `campaign_daily` |
| `raw_linkedin_ads` | App sync | `campaign_daily`, `campaigns`, `ad_daily`, `creative_metadata`, `metrics_daily` |
| `raw_meta_ads` | App sync | `campaign_daily`, `adset_daily`, `ad_daily`, `ad_creative` |
| `raw_ga4` / GA4 export | Google native export (app can't write) | `events_*` (sharded by date suffix) |
| `raw_gsc` (+ fact tables) | App sync (service account / agency OAuth) | `fact_gsc_query_daily`, `fact_gsc_page_daily` |
| `raw_semrush`, `raw_pagespeed`, `raw_gtm`, `raw_hubspot` | App sync | source-specific |
| `marketing_marts` | App (`CREATE OR REPLACE VIEW`) | all `fact_*` and `vw_*` presentation objects |

### 3.2 Raw fact schemas (canonical grain)

**`metrics_daily`** — account × day (`_schema()`): `source, account_id, metric_date,
spend, clicks, impressions, conversions, conversion_value, synced_at`. Partitioned
by `metric_date`, clustered on `(source, account_id)`.

**`campaign_daily`** — campaign × day (`_campaign_daily_schema()`): adds
`campaign_id, campaign_name`. **All three paid platforms write an identically
shaped `campaign_daily`**, which is what makes the cross-platform `UNION ALL` in
`vw_paid_media_daily` correct with no double counting. Meta's variants additionally
carry a nullable `client_key`.

**GSC** (`bq_provision_service.py`): `fact_gsc_query_daily` /
`fact_gsc_page_daily` — `date` (REQUIRED, partition), `organic_clicks`,
`organic_impressions`, `organic_sum_position`, plus `is_anonymized_query` (query
table only). Clustered on `query` / `page_url`.

Meta/LinkedIn ad + creative tables add `adset_*`, `ad_*`, `creative_*`, and media
URL columns (see `_meta_ad_daily_schema`, `_creative_metadata_schema`).

### 3.3 Mart views (all built by `bigquery_warehouse.py` as `CREATE OR REPLACE VIEW`)

| View | Source | Notes |
|---|---|---|
| `fact_google_ads_campaign_daily` | `raw_google_ads.campaign_daily` | Passthrough view; `metric_date → date`. Missing raw table → `pending_data` (never fabricates rows). |
| `fact_linkedin_ads_campaign_daily` | `raw_linkedin_ads.campaign_daily LEFT JOIN campaigns` | Adds campaign status/group + `reach` (0 if column absent). Built as **view** so it's always live. |
| `fact_linkedin_ads_creative_daily` | ad_daily + creative_metadata + campaigns | Creative-level, `SUM` grouped. |
| `fact_meta_ads_campaign_daily` / `_adset_daily` / `_ad_daily` | Meta raw tables | Ad view LEFT JOINs `ad_creative` for thumbnails. |
| `vw_paid_media_daily` | UNION ALL of google/linkedin/meta `campaign_daily` | `source_platform` = `paid_google`/`paid_linkedin`/`paid_meta`. Dynamically includes only the raw tables that exist. |
| `mart_health` | Same raw tables, aggregated | Per-source `row_count, earliest/latest_date, spend, impressions, clicks, conversions` — the data-health panel source. |
| `fact_marketing_daily` | UNION ALL of the per-platform fact views | Carries `client_key` (literal fallback for sources whose fact lacks the column). |

**Provisioning entry points**

- `bq_provision_service.validate_all()` / `provision_all` — ensures GSC tables
  exist (creates if missing) and validates the GA4 export exists (cannot create;
  Google-managed). Never raises — all failures captured into `ProvisionResult`.
- `create_paid_media_mart_views()` / `rebuild_*_mart()` — rebuilt on every sync,
  idempotent. `_replace_object_with_view()` first drops any pre-existing **table**
  of the same name (a `CREATE OR REPLACE VIEW` cannot replace a table — legacy
  Dataform tables triggered this).

### 3.4 Provisioning accuracy / cleanliness notes

- **Idempotent MERGE upserts.** Every mirror function stages to a
  `_staging_<uuid>` table then `MERGE`s on the natural key
  (`source+account_id+[campaign/creative/ad]+metric_date`) and deletes the staging
  table in a `finally`. `_aggregate_daily_metrics()` pre-collapses finer-grained
  rows to the target grain because BigQuery MERGE requires **≤1 source row per
  target row** — this is the main defense against double-counting on re-sync.
- **Views, not materialized tables.** Marts are live views over raw, so a re-sync
  is reflected immediately and there's no separate refresh to fall stale.
- **`pending_data` vs error.** A missing raw/mart table is treated as "source not
  set up yet" (empty), not an error — see `_is_table_not_found()` in
  `bq_mart_service.py`. Real failures (auth, permission, SQL) are surfaced.
- **Partitioning/clustering** is applied on create for cost/perf, but only tables
  the app creates. The GA4 export and any Data-Transfer-managed Google table are
  outside app control.

---

## 4. Endpoints

Route inventory (extracted from `main.py`, `railway_api.py`, `dashboard/routes/*`).
Four auth tiers:

| Tier | Mechanism | Where |
|---|---|---|
| **Public API** | `Bearer` / `X-API-Key` == `API_KEY` (`security.require_api_key`). **Fails closed in production**, open in local dev only when `API_KEY` unset. | `/google-ads/*`, `/linkedin/*`, `/meta/*`, `/indeed/*`, `/ga4/*`, `/warehouse/*` |
| **Internal cron** | `require_cron_secret` header == `CRON_SECRET` (503 if unset, 401 on mismatch) | `/internal/sync-*`, `/internal/backfill-bq/*` |
| **Session (browser)** | Signed session cookie; `authenticate_dashboard[_api][_any]`, `require_client_access`, super-admin gating | `/dashboard/*`, `/admin/*`, connector wizard, settings, files |
| **OAuth** | Platform OAuth handshake | `/oauth/{platform}/*`, `/connect/{platform}/{client_slug}` |

### 4.1 Public / ChatGPT API (Bearer)

Described by `openapi-chatgpt*.json` + `openapi_gpt.py`; consumed by a ChatGPT
Custom Action. Read + warehouse-sync endpoints per platform:

- `/health`, `/warehouse/status`, `/warehouse/metrics`
- Google Ads: `/google-ads/{accounts,search,search-many,summary-all,youtube-videos,warehouse/sync}`
- LinkedIn: `/linkedin/{accounts,performance,campaign-groups[/performance],creatives/performance,videos,warehouse/sync}`
- Meta: `/meta/{accounts,performance,adsets/performance,videos,test-ads-access,warehouse/sync}`
- GA4: `/ga4/{clients,env,query,warehouse/sync}`

### 4.2 Dashboard data API (session, `/api/clients/...`)

`dashboard/routes/api_routes.py` (73 routes). Two shapes: hard-coded `nixon` demo
routes and the generic `{client_key}` equivalents. Every handler calls
`web_auth.authenticate_dashboard_api[_any]` before touching data. Families:

- **Marketing / health**: `/api/clients/{k}/summary`, `/marketing/health`, `/refresh`, `/bq-verify`
- **Paid explorers**: `/google-ads/{explorer,keywords,verified-conversions}`,
  `/linkedin/explorer`, `/meta/{explorer,verified-conversions}`
- **GA4 analytics**: `/pages/*` (top, sources, landing, device-split, key-events),
  `/analytics/{conversions,user-acquisition,demographics}`, `/ga4/{key-events,provision-views,health/*}`
- **SEO/site**: `/gsc/{summary,keyword-matches,keyword-config}`, `/semrush/summary`,
  `/pagespeed/{summary,targets}`, `/gtm/live-tags`
- **Debug**: `/api/debug/bq` (BigQuery client identity)

### 4.3 Internal cron (CRON_SECRET) & the workers that call them

| Endpoint | Cron worker | Schedule | Window |
|---|---|---|---|
| `/internal/sync-penn` | `cron-sync-penn` | `0 11 * * *` (~6:00am ET) | `LAST_30_DAYS` (Penn legacy) |
| `/internal/sync-bq-all` | `cron-sync-bq` (hands-off; re-derives clients from `connector_configs`) | `30 11 * * *` (~6:30am ET) | rolling 30-day per client; `sync_enabled` gates each connector |
| `/internal/sync-hubspot` | `cron-sync-hubspot` | `0 12 * * *` (12:00 UTC) | 7-day default (`HUBSPOT_SYNC_LOOKBACK_DAYS`) |
| `/internal/sync-bq/{slug}`, `/internal/backfill-bq/{slug}` | manual / single-client mode | on demand | — |

Staggering (11:00 → 11:30 → 12:00) is deliberate so the workers don't overlap.

### 4.4 Endpoint accuracy / cleanliness notes

- **Fail-closed API key** is enforced at startup (`_require_api_key_configured()`
  aborts boot in production if `API_KEY` is unset) — no accidental open API.
- **`CRON_SECRET` must not equal `API_KEY` / `AUTH_SESSION_SECRET`** — the code
  explicitly rejects the shared-secret fallback (`security.py`), preventing one
  leaked secret from unlocking everything.
- **`view-as` impersonation** (`/admin/view-as`) changes which client's data an
  admin sees — relevant when auditing "who saw what": the impersonation banner and
  `audit_log` record it.
- SQL `WHERE client_key = '{client_key}'` filters in `bq_mart_service.py` are
  built by f-string interpolation of the resolved `client_key`/date values (not
  user free-text). Safe today because these come from config, **but** any future
  path that lets an end user supply `client_key`/domain directly should switch to
  BigQuery query parameters. Flagged as a cleanliness item, not a live bug.

---

## 5. Front-end calculations

### 5.1 Derived paid-media metrics

Formatting helpers live in `dashboard/utils/formatting.py`; the hero/overview math
in `dashboard/renderers/cards_renderer.py` (`paid_ad_overview_metrics`):

| Metric | Formula | Guard |
|---|---|---|
| CTR | `clicks / impressions` | `None` when impressions = 0 |
| CPC | `spend / clicks` | `None` when clicks = 0 |
| CPM | `spend / impressions × 1000` (`_safe_ratio` in `bq_mart_service`) | 0 when impressions = 0 |
| **Reported CPA** | `spend / conversions` (**platform-reported** conversions) | `None` when 0 |
| **Verified CPA** | `spend / ga4_key_events` (**GA4-attributed** key events for paid) | `None` when 0 |
| cost_per_conversion (explorer rows) | `spend / conversions` | 0 when 0 |

**Two conversion sources coexist by design.** "Reported" = platform-defined
conversions carried in `campaign_daily`. "Verified" = GA4 paid key events
(`ga4_paid_key_events()` sums `google+linkedin+meta` key_events from the GA4
attribution payload). They will legitimately differ; the UI labels both. When
reconciling numbers, confirm which basis a stakeholder means.

### 5.2 Totals & aggregation

- `aggregated_paid_media()` sums **google + linkedin + meta only** (not GA4,
  not organic) → the paid overview.
- `totals_from_daily_rows()` / `_platform_totals()` sum per-day rows to account
  totals; campaign counts come from distinct campaign ids.
- `bq_mart_service.build_snapshot()` reads Google + LinkedIn (and Meta via its own
  service) marts in parallel and prefers `fact_marketing_daily` for the headline
  aggregate. **Fallback caveat:** if `fact_marketing_daily` is empty/missing, the
  `except` path recomputes the aggregate as **Google + LinkedIn only** — Meta is
  omitted in that specific fallback sum. Worth confirming for any client whose
  `fact_marketing_daily` view isn't provisioned yet.
- **Google daily deliberately excludes account-level non-campaign spend.**
  `google_daily_series()` aggregates the campaign mart, which omits adjustments /
  null-campaign rows the Google Ads API account total includes (documented example:
  API $8,238 vs mart $6,475). This is intentional — the campaign-level number is
  treated as canonical — but it explains a real discrepancy vs the platform UI.

### 5.3 Budget pacing (`snapshot_metrics_service.build_budget_pacing_payload`)

Month-to-date, calendar-month basis:

- `days_in_month` from `calendar.monthrange`; **KPIs reflect the last complete day**.
- `pace_line[i] = budget × (i+1)/days_in_month` (linear target).
- `expected_pace = budget × days_elapsed/days_in_month`.
- `pct_budget = 100 × mtd_spend/budget`; `pct_vs_pace = 100 × (mtd_spend − expected)/expected`.
- `required_daily = remaining_budget / days_remaining`;
  `daily_adjustment = required_daily − avg_daily`.
- Cumulative series are built both overall and **per platform** (google/linkedin/meta),
  each filtered to `[month_start, today]`.

Budget source: `monthly_budget` arg → `cfg.monthly_budget_usd`. All ratios guard
against zero budget/day denominators (return `None`/0).

### 5.4 GA4 warehouse mapping (`ga4_warehouse_service.py`)

GA4 events are normalized into the warehouse metric shape:

- **clicks = `session_start` count**, **impressions = `page_view` count**,
  **spend = 0**.
- **conversions = `COUNTIF(event_name IN (<client key events>))`.**
  The key-event list is resolved per client by
  `ga4_attribution_service.resolve_key_event_names()`: it reads the admin-set
  `client_dashboard_config.ga4_key_events` (Settings page) and falls back to the
  shared `KEY_EVENT_NAMES` default when unset. Names are validated to
  `[A-Za-z0-9_]` before interpolation. The raw-export services (warehouse,
  attribution, page) now share this one source of truth; the GA4 **Reporting-API**
  paths (verified conversions/CPA) already use GA4's native `keyEvents` metric, so
  all conversion definitions now honor the client's real key events. Organic
  variant additionally filters `traffic_source.medium = 'organic'`.
  _(Fixed — see §6.1.)_
- Every day in the range is emitted (zero-filled) so downstream charts have no gaps.

### 5.5 Business-line classification (`penn_business_lines.py` + `business_line_rules.py`)

Campaigns are bucketed into business lines by **keyword match** on their name:

- Custom per-client rules (Postgres `client_business_line_rules`, admin-editable,
  parsed from `keywords = Label` lines) are tried **first**, then built-in
  `BUSINESS_LINE_RULES`. First keyword hit wins; unmatched → `("other","Other")`.
- Matching tries group/parent names before the campaign name
  (`_classification_names` order). Substring match, case-insensitive.

Accuracy note: because it's a first-match substring rule, **rule ordering and
overly broad keywords can mis-bucket spend.** When a business-line split looks
wrong, check the client's custom rules and keyword overlap.

### 5.6 Data-source status / freshness (`data_source_status.py`)

The settings "Data source status" panel and `build_pipeline_summary` classify each
source and compute drift between the snapshot and BigQuery:

- Per-source status ∈ `feeding | fallback | empty | missing | error | not_configured`
  (`feeding` = >0 rows in the recent 30-day window).
- **Snapshot-vs-BQ drift**: `drift_days ≤ 1 → current`, `≤ 3 → lagging`,
  else `stale`. This is the built-in "is the data fresh?" signal — use it as the
  first stop when auditing a specific client.

---

## 6. Accuracy & cleanliness checklist (derived from this audit)

Use these as verification steps, in rough priority order:

1. **GA4 key-event definition** — _Fixed (§6.1)._ The list is now per-client
   configurable via Settings (`ga4_key_events`), shared across the raw-export
   services, with the built-in list as fallback. Remaining action is **operational**:
   set each client's key events in Settings so raw-export conversions match GA4's
   designation (the Reporting-API verified path already did) (§5.4).
2. **`fact_marketing_daily` provisioned** — _Fixed (§6.1)._ The Meta connector now
   rebuilds the Meta fact views + `fact_marketing_daily` on every sync, so the
   headline aggregate uses the all-platform view instead of the Google+LinkedIn-only
   fallback. Verify only if a client predates this change and hasn't re-synced Meta
   (§5.2).
3. **Per-client BQ routing** — every connector sync goes through
   `bigquery_warehouse.route(bq_project_id=…)`; no source is falling back to Penn's
   project (§2.2). Spot-check with `/api/debug/bq` and `bq-verify`.
4. **Reported vs Verified CPA** — make sure stakeholders know which conversion
   basis a given number uses (§5.1).
5. **Google campaign-vs-account spend gap** — expected, but document it wherever a
   client compares the dashboard to the Google Ads UI (§5.2).
6. **Business-line rules** — review keyword rules for over-broad matches and the
   `Other` bucket size per client (§5.5).
7. **Agency-authed sources (GSC/SEMrush/PageSpeed)** — verify the shared credential
   actually covers each client's property/domain (§2.2).
8. **Freshness/drift** — check `drift_status` per client; investigate any `stale`
   (§5.6). Confirm the three cron workers are all deployed and their `CRON_SECRET`
   matches the API (§4.3).
9. **Re-sync duplication** — MERGE keys make re-syncs idempotent; if row counts
   grow unexpectedly on re-sync, check that the source's grain matches the staging
   key (§3.4).
10. **Secrets separation** — `CRON_SECRET ≠ API_KEY ≠ AUTH_SESSION_SECRET`, and
    `API_KEY` set in production (§4.4).

### 6.1 Fixes applied

| # | Fix | Files |
|---|---|---|
| 1 | **Per-client GA4 key events wired into the raw-export queries.** Added `resolve_key_event_names(client_key)` as the single source of truth: reads `client_dashboard_config.ga4_key_events`, validates names to `[A-Za-z0-9_]` (injection-safe), falls back to the shared `KEY_EVENT_NAMES`. Warehouse (`fetch_daily_metrics`, `fetch_organic_daily_metrics`), attribution (`_attribution_base_sql`, `_landing_pages_sql_suffix`), and page (`_key_events_sql_list`) services now thread `target.client_key` through. The Settings control now actually changes the numbers, and the previously-divergent 4-event (warehouse) vs 9-event (attribution) defaults are unified. | `ga4_attribution_service.py`, `ga4_page_service.py`, `ga4_warehouse_service.py` |
| 2 | **Meta connector rebuilds `fact_meta_ads_campaign_daily` + `fact_marketing_daily` on sync.** Previously only the periodic refresh orchestrator rebuilt the unified mart, so a manual Meta sync left `fact_marketing_daily` without Meta and the snapshot fallback dropped Meta from the paid total. | `connectors/meta_ads.py` |

> Behavior note for #1: for clients with **no** key events configured, raw-export
> `conversions` now default to the 9-event `KEY_EVENT_NAMES` list (previously the
> warehouse used only 4). This is intentional unification, but it will nudge those
> clients' warehouse conversion counts upward until they set an explicit list.

---

## 7. Source map (where to look)

| Area | Primary files |
|---|---|
| Connector contracts & handlers | `connectors/base.py`, `connectors/*.py` |
| Connector config / sync-run state | `connector_config_store.py` |
| Raw tables, MERGE upserts, mart views | `bigquery_warehouse.py` |
| GSC/GA4 provisioning & validation | `bq_provision_service.py`, `bq_ga4_mart_service.py`, `bq_gsc_service.py` |
| Mart reads / snapshot build | `bq_mart_service.py`, `ga4_warehouse_service.py` |
| Derived metric math & formatting | `dashboard/renderers/cards_renderer.py`, `dashboard/utils/formatting.py` |
| Budget pacing & totals | `dashboard/services/snapshot_metrics_service.py`, `warehouse_metrics_service.py` |
| Business-line classification | `penn_business_lines.py`, `business_line_rules.py` |
| Freshness / data-source status | `data_source_status.py`, `dashboard/services/bigquery_refresh_orchestrator.py` |
| Endpoints | `main.py`, `dashboard/routes/*.py`, `openapi-chatgpt*.json`, `openapi_gpt.py` |
| Auth | `security.py`, `cron_security.py`, `web_auth.py` |
| Cron workers | `railway/cron-sync-penn`, `railway/cron-sync-bq`, `railway/cron-sync-hubspot` |
