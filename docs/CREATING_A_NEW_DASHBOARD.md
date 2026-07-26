# Creating a New Client Dashboard

This is the exact, end-to-end process for standing up a new client dashboard on
the Sagefrog portal, mapped to the actual code paths.

> Scope: this describes the **`bigquery_nixon`** dashboard (the Nixon-style
> connector-driven dashboard). New dashboards created via `/admin` use this mode
> automatically. The older Penn snapshot dashboard is not covered here.

> **Status (audited):** the onboarding path is production-ready. Every dashboard
> panel is populated by an app-built mart (no Dataform dependency), a finished
> connector is picked up by the daily cron automatically, and the app builds all
> raw/mart datasets on sync. The only steps that are **not** automated are the
> two that inherently can't be — see [Manual steps](#manual-steps-that-cant-be-automated).

---

## 0. Quick checklist (per new client)

1. **GCP (manual):** create/confirm the client's GCP project; grant
   `marketing-data-reader@sagefrog.iam.gserviceaccount.com` **BigQuery Data
   Editor** + **BigQuery Job User** on it. (§3)
2. **Create the dashboard:** `/admin` → Dashboards → **Add** (slug + label). (§4)
3. **Connect each platform:** dashboard → **Connectors** → run each wizard to the
   end (Connect → account → destination → backfill → test → **finish**). The
   destination step verifies BigQuery access on the spot; finishing enables the
   daily sync. (§5)
4. **First data:** click **Sync now** on a connector (or wait for the 11:30 UTC
   cron). Overview fills once a paid sync runs; other panels as their connectors
   sync. (§6)
5. **Verify:** run the [checklist](#8-verification-checklist) — Overview,
   Analytics, and Explorer panels should populate.

Everything else (datasets, marts, views) is created automatically by the app.

---

## 1. How the data actually flows

```
                          ┌─────────────────────────────────────────────┐
   Platform APIs          │              Client's GCP project             │
   (Google Ads, GA4,      │                                               │
    LinkedIn, Meta,       │   raw_google_ads   raw_linkedin_ads   raw_… │
    GSC, SEMrush)         │        │                │              │      │
        │                 │        ▼                ▼              ▼      │
        │  Railway app     │   ┌──────────────── marketing_marts ──────┐  │
        │  connector sync  │   │  app-built marts   +   Dataform marts │  │
        └────────────────► │   └───────────────────────────────────────┘  │
                          │                     ▲                          │
                          └─────────────────────┼──────────────────────────┘
                                                │ reads
                              Railway app  ◄─────┘  /api/clients/{slug}/*
                              dashboard (HTML + JSON API, DB-cached)
```

The **Railway app connector syncs** own the whole pipeline: they pull from
platform APIs → write `raw_*` datasets → build **all** the marts the dashboard
reads (including `vw_paid_media_daily` and `mart_health`, as of the app-build
port). Trigger: per-connector "Sync now", the dashboard refresh, or the daily
cron. The separate `sagefrog-dataform` project is now **redundant** and not
required — see §7 and [Manual steps](#manual-steps-that-cant-be-automated).

The dashboard's HTML never queries BigQuery directly — it calls the app's
`/api/clients/{slug}/*` JSON endpoints ([api_routes.py](../railway/app/dashboard/routes/api_routes.py)),
which read the marts and cache responses in Postgres (`db_cache`), invalidated
when a sync completes.

---

## 2. One-time agency setup (done once, not per client)

1. **Agency OAuth** — In `/admin` → platform connections, connect the agency
   login for each platform (Google Ads, GA4, LinkedIn, Meta). These are stored
   as **agency-wide global tokens** (`oauth_store`, `client_slug=''`). Every
   client's connector reuses them via the fallback in
   [`oauth_store.get_refresh_token`](../railway/app/oauth_store.py) — so you do
   **not** re-authorize Google/LinkedIn/Meta per client. (HubSpot is the
   exception — it's authorized per client via a connect-link.)
2. **Shared service account** — `marketing-data-reader@sagefrog.iam.gserviceaccount.com`
   is the single identity the app uses for BigQuery on every client project.

---

## 3. Per-client GCP prerequisites (manual, GCP-side)

The app **cannot create a GCP project** (that needs org admin + billing). Before
touching the portal:

1. **Create the client's GCP project** (or reuse an existing one).
2. **Grant the shared service account** two roles on that project
   (both are required — the wizard verifies this and will fail without them):
   - **BigQuery Data Editor** — create datasets/tables + write data
   - **BigQuery Job User** — run queries (needed by the verification check *and*
     every dashboard read)
3. **GA4**: the client's GA4 property must be accessible to the agency Google
   login (so it appears in the account picker). GA4 data is **pulled by the app
   via the Data API** — no native BigQuery export linking is required for the
   app-built GA4 views.

---

## 4. Create the dashboard (portal, ~1 minute)

1. Go to **`/admin`** (admin login required).
2. Under **Dashboards → Add**, enter a **slug** (e.g. `acme`) and **display
   label** (e.g. `Acme Co`), submit.
   - Route: `POST /admin/dashboards` → [`dashboard_registry.create_client`](../railway/app/dashboard_registry.py).
   - This creates the `dashboard_clients` row and a `client_dashboard_config`
     row with **`dashboard_mode = "bigquery_nixon"`** and `ga4_client_key = slug`.
3. The dashboard is now reachable at **`/dashboard/{slug}`** and renders the
   Nixon-style template immediately (empty "no data yet" state until connectors
   are set up). Its settings page (`/dashboard/{slug}/settings`) and connectors
   page (`/dashboard/{slug}/connectors`) are also live.

There is **no separate "register GCP project" step** — the project ID is
captured during connector setup (next).

---

## 5. Connect the connectors (portal, per platform)

From the dashboard sidebar → **Connectors** (`/dashboard/{slug}/connectors`),
every connector type is listed with a **Connect** button. The wizard
([connectors_renderer.py](../railway/app/dashboard/renderers/connectors_renderer.py),
[connector_routes.py](../railway/app/dashboard/routes/connector_routes.py)) runs
these steps:

1. **Connect** — reuses the agency OAuth token (or authorizes, for HubSpot).
2. **Select account** — `list_accounts()` lists the platform accounts the agency
   login can see; pick the client's account (`source_account_id`).
3. **Confirm destination** — enter the **GCP project ID** + raw/mart dataset
   names. On submit, the app **provisions and verifies BigQuery immediately**:
   [`client_bigquery_setup.ensure_client_datasets`](../railway/app/client_bigquery_setup.py)
   creates the four standard datasets (`raw_google_ads`, `raw_linkedin_ads`,
   `raw_meta_ads`, `marketing_marts`) and runs a `SELECT 1` + create/delete
   permission check. If the project doesn't exist or the service account lacks
   the two roles, setup fails **here** with an actionable message.
   The project ID is also back-filled onto `client_dashboard_config.gcp_project_id`.
4. **Backfill** — choose how many days of history to pull on the first sync.
5. **Test** — verifies the source connection.
6. **Finish** — sets the connector to `connected` and, via the "Sync
   automatically" toggle (**on by default**), `sync_enabled = true`. **This is
   what enrolls the connector in the daily cron** — a connector left mid-wizard
   (never "finished") stays `sync_enabled = false` and the cron skips it (you'd
   have to sync it manually). So always run the wizard to the end.

Repeat per platform the client uses. The connectors nav and each platform's
panel appear as connectors are connected.

---

## 6. First sync (populate the data)

Data does not appear until a sync runs. Either:

- **Per connector**: the connector card's **Sync now** button, or
- **Whole client**: the settings page **Refresh — last 30 days** button
  (`/api/clients/{slug}/refresh` → `dashboard_service.refresh_bq_client`), or
- **Automatically**: the daily cron **`POST /internal/sync-bq-all`**
  (`cron-sync-bq` Railway service, **11:30 UTC**,
  [core_routes.py](../railway/app/dashboard/routes/core_routes.py)). It syncs
  **every** client that has connectors with `sync_enabled`, in parallel, guarded
  by a Postgres lock.

Each connector's `run_sync` writes its `raw_*` data **and rebuilds its own marts**
(see the ownership table below).

### Scalability (audited for 15–20 clients)

The connector sync is safe to run across many clients concurrently:

- **Isolation:** each sync builds a **fresh** BigQuery client scoped to its own
  project, routing is set per-sync via contextvars (never inherited across
  threads), handlers are stateless, and there are no module-level caches. If a
  project isn't routed, the code **raises** rather than falling back to another
  tenant's project.
- **Bounded concurrency:** the cron runs at most **4 clients in parallel**
  (`_SYNC_BQ_ALL_MAX_WORKERS`), each syncing its connectors **sequentially**, with
  a **600 s per-client timeout** and an overlap lock — one slow/hung client can't
  block or starve the batch. A 20-client run is ~5 waves.
- **No shared BQ bottleneck:** each client has its own GCP project, so BigQuery
  job quotas are per-project. DB pool (≥10, or direct) comfortably exceeds 4
  workers.

Two things to watch **beyond ~20 clients** (not issues now): the shared agency
OAuth means platform **API rate limits** are the real ceiling (would need
per-platform throttling at large scale), and the sync runs as background threads
on the web process (I/O-bound; minor request-latency impact during a run).

---

## 6a. Optional: segment filters (business lines / regions)

Some clients group their campaigns and pages into **segments** — either
keyword-based **business lines** (e.g. a bank's Home Equity / HYS / Commercial)
or **geographic regions**. This is a per-client setting, driven entirely by
configuration — there is **no client-specific code**.

- Set it on the **Settings page** (`/dashboard/{slug}/settings` → **Segment
  filters**): choose **Business lines**, **Regions**, or **None**.
- It is stored as `client_dashboard_config.segment_filter_profile`
  (`business_lines`, `regions`, or empty) and read by
  [`penn_business_lines.client_filter_profile`](../railway/app/penn_business_lines.py).
  The value drives the filter UI label, the campaign grouping, and the Website
  Analytics page segmentation.
- **Business lines** use the built-in keyword taxonomy plus any custom rules an
  admin adds (`business_line_rules`); **regions** use
  [`dashboard_regions`](../railway/app/dashboard_regions.py).
- Leave it **None** for clients who don't segment — most new clients.

> Migration note: this replaced older logic that inferred the profile from a
> client's slug/label/env vars. A one-time startup backfill in `main.py` seeds
> the pre-existing clients (Nixon → `regions`) so their filters keep working; new
> clients set it from Settings.

---

## 7. Mart ownership — all app-built

The dashboard reads ~13 marts; **every one is built by the Railway app** on
connector sync (idempotent `CREATE OR REPLACE`). Nothing here needs Dataform.

### Built automatically by the Railway app connector syncs

| Mart | Built by | Dashboard panel |
|---|---|---|
| `vw_ga4_traffic_acq_daily`, `vw_ga4_tech_daily`, `vw_ga4_landing_pages_daily`, `vw_ga4_events_daily`, `vw_ga4_user_acq_daily`, `vw_ga4_geo_daily`, `vw_ga4_demographics_daily`, `vw_page_path_daily`, `vw_page_path_source_daily` | GA4 connector → `bq_ga4_mart_service.provision_ga4_mart_views` | Website Analytics |
| `explorer_google_ads_daily` | Google Ads connector → `bq_google_ads_service` | Google Ads Explorer |
| Google campaign mart view | Google Ads connector → `bigquery_warehouse.create_google_campaign_mart_view` | (paid media source) |
| `fact_linkedin_ads_creative_daily` | LinkedIn connector → `bq_linkedin_ads_service` | LinkedIn Explorer |
| `fact_meta_ads_ad_daily` | Meta connector → `bq_meta_ads_service` | Meta Explorer |
| `vw_semrush_overview_latest`, `vw_semrush_keywords_latest` | SEMrush connector → `bq_semrush_service` | Search Console tab → SEMrush |
| GSC mart | GSC connector / `bq_gsc_service` | Search Console |
| **`vw_paid_media_daily`** | Google Ads + LinkedIn + Meta connector → `bigquery_warehouse.create_paid_media_mart_views` | **Overview → Summary cards + Trend chart** |
| **`mart_health`** | Google Ads + LinkedIn + Meta connector → `bigquery_warehouse.create_paid_media_mart_views` | **Overview → Data health** (+ a GA4 freshness row appended at read time) |

`vw_paid_media_daily` and `mart_health` used to be Dataform-only (see history
below); they are now built by the app on every Google Ads / LinkedIn / Meta
sync, so the Overview populates with no Dataform dependency. All three platforms
write an identically-shaped `raw_*.campaign_daily` (campaign-per-day grain), and
the builder `UNION ALL`s whichever of the three exist — so a client with any
subset of paid connectors still gets a correct Overview (`source_platform`
values `paid_google` / `paid_linkedin` / `paid_meta`). No double counting, since
ad-level facts are not included here.

### Dataform is no longer required for the Overview

The `sagefrog-dataform` project has been **retired** — every mart it defined is
app-built (`vw_paid_media_daily`/`mart_health` above; the page-path views via
`bq_ga4_mart_service`; `explorer_google_ads_daily`/
`fact_linkedin_ads_creative_daily` via the ad connectors). Its definitions were
removed (repo kept as a deprecated archive), so you do **not** set up a Dataform
workspace per client. If any client project still has a **scheduled** Dataform
workflow, disable it in GCP Console → Dataform (see
[Manual steps](#manual-steps-that-cant-be-automated)).

---

## 8. Verification checklist

After setup, confirm:

- [ ] `/dashboard/{slug}` loads the Nixon template (not the old snapshot page).
- [ ] Sidebar is identical across Overview / Settings / Connectors / Files.
- [ ] Each connected connector card shows a successful last sync.
- [ ] Overview **Summary** + **Trend** show numbers → confirms a Google Ads,
      LinkedIn, or Meta sync ran (builds `vw_paid_media_daily`).
- [ ] Website Analytics panels show data → confirms GA4 sync + app views.
- [ ] Explorer tabs (Google/LinkedIn/Meta) show ads → confirms ad connectors.
- [ ] Search Console / SEMrush panels populate (if those connectors are used).

If Summary is empty but a paid connector shows a successful sync, check that
its raw `campaign_daily` table has rows for the selected date range.

---

## Manual steps that can't be automated

The onboarding path is otherwise fully automated; these two are inherent to GCP
and must be done by a human, once per client:

1. **Create the GCP project + grant IAM.** The app cannot create GCP projects
   (needs org admin + billing). Create the project and grant
   `marketing-data-reader@sagefrog.iam.gserviceaccount.com` **both** BigQuery
   **Data Editor** and **Job User**. The connector wizard and the settings-page
   **Verify BigQuery access** button both check this and fail with an actionable
   message naming the roles.
2. **Stop any old Dataform schedule.** `sagefrog-dataform` is retired (definitions
   removed), but if a client project still has a **scheduled** Dataform workflow,
   disable/delete it in **GCP Console → Dataform** so it can't overwrite the
   app-built marts.

## Known limitations (non-blocking)

- **Some GA4 sub-panels may be empty.** Device split (`vw_ga4_tech_daily`) is an
  empty placeholder stub, and Geo/Demographics are optional (depend on those GA4
  dimensions being present). These panels degrade gracefully (no error), just
  show no data. Not a blocker — the core Analytics panels (traffic, pages,
  landing, conversions, user acquisition) populate normally.
- **Meta only appears in the Overview at campaign grain.** `vw_paid_media_daily`
  unions the campaign-daily tables; Meta ad-level detail lives in its Explorer.
- **First-data lag.** After finishing a connector there's a gap until the first
  sync completes; the onboarding card only covers the "no connectors yet" state,
  not "connected, first sync pending."

## Audit gaps — all closed ✅

The six gaps from the scalability audit are resolved:

| # | Gap | Resolution |
|---|---|---|
| 1 | `vw_paid_media_daily` + `mart_health` Dataform-only | App-built via `bigquery_warehouse.create_paid_media_mart_views` (Google + LinkedIn + Meta) |
| 2 | `sagefrog-dataform` drift risk | Repo retired (definitions removed); one manual GCP action to stop schedules (above) |
| 3 | GCP project + IAM manual | Inherent; mitigated by wizard verify + settings "Verify BigQuery access" button |
| 4 | No `dashboard_mode` UI toggle | Admin "Use new template" one-click convert + Template column |
| 5 | Empty dashboard looks broken | First-run "Connect your first data source" onboarding card |
| 6 | GA4 dual path | Resolved by retiring Dataform — app is the single source |
