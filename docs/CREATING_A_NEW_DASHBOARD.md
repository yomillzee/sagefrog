# Creating a New Client Dashboard

This is the exact, end-to-end process for standing up a new client dashboard on
the Sagefrog portal, mapped to the actual code paths. Read the **[Critical
gaps](#critical-gaps--follow-ups)** section at the bottom before onboarding a
real client — some required steps are **not** automated yet.

> Scope: this describes the **`bigquery_nixon`** dashboard (the Nixon-style
> connector-driven dashboard). New dashboards created via `/admin` use this mode
> automatically. The older Penn snapshot dashboard is not covered here.

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
required — see §7 and gap #2.

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

---

## 7. Mart ownership — what's automatic vs. manual

This is the part to watch. The dashboard reads ~13 marts; they come from two
different places.

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
workflow, disable it in GCP Console → Dataform (see gap #2).

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

## Critical gaps / follow-ups

These are known gaps between the "create dashboard + connect connectors" ideal
and what's actually automated today. **Flagged for follow-up — not yet fixed.**

1. **✅ RESOLVED — `vw_paid_media_daily` + `mart_health` are now app-built
   (Google + LinkedIn + Meta).** These were Dataform-only, which forced a manual
   per-client Dataform workspace. They're now built by
   `bigquery_warehouse.create_paid_media_mart_views`, called from the Google Ads,
   LinkedIn, and Meta connector syncs (so they rebuild on every sync, refresh,
   and cron run). No Dataform workspace is required for the Overview.

2. **✅ RESOLVED (repo side) — `sagefrog-dataform` retired.** All its mart +
   source definitions were removed and its README replaced with a deprecation
   notice, so a stray Dataform run is now a no-op and can't overwrite the
   app-built marts. **⚠️ One manual GCP-side action remains:** in GCP Console →
   Dataform, **delete or disable any scheduled workflow / release configuration**
   linked to a client project — a workspace pinned to an older commit could still
   run the old definitions until its schedule is stopped.

3. **🟠 GCP project + IAM is manual (inherent) — now verifiable in-portal.**
   Creating the project and granting the two roles (Data Editor + Job User) is
   unavoidably GCP-side — the app cannot create projects. Mitigations now in
   place: the connector wizard verifies access on the destination step, and the
   settings page has a **Verify BigQuery access** button
   (`POST /api/clients/{slug}/bq-verify`) that re-runs the same check any time
   (pre-flight before setup, or diagnosing "data stopped showing"). The error
   message names both required roles. *Only the project creation + IAM grant
   itself remains manual, by GCP design.*

4. **✅ RESOLVED — admin convert-to-new-template action.** The `/admin`
   Dashboards table now shows a **Template** column: `bigquery_nixon` dashboards
   show a "New template" badge; legacy ones show a **Use new template** button
   (`POST /admin/dashboards/{slug}/mode`) that converts them in one click
   (preserving all other config). No more manual DB update to migrate a legacy
   dashboard. (Penn stays on the snapshot template, protected.)

5. **✅ RESOLVED — first-run onboarding state.** A dashboard with no connectors
   now shows a "Connect your first data source" card (with a **Set up connectors**
   CTA) at the top of the Overview instead of a bare empty page. Renders only when
   the client has zero connector configs; fails open (never shown to an
   established client). *Remaining nicety: a "sync running / waiting for first
   data" state after connectors exist but before the first sync completes.*

6. **✅ RESOLVED — GA4 is single-path.** The app pulls GA4 via the Data API and
   builds the GA4 views; with `sagefrog-dataform` retired there is no longer a
   competing native-export definition, so there's one authoritative source per
   GA4 panel.
