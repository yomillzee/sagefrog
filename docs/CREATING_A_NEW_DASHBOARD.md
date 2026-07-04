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

Two systems write into `marketing_marts`:

| System | What it does | Trigger |
|---|---|---|
| **Railway app connector syncs** | Pull from platform APIs → write `raw_*` datasets → build most marts | Per-connector "Sync now", the dashboard refresh, or the daily cron |
| **`sagefrog-dataform` project** (separate repo, runs in GCP Dataform) | Transform `raw_*` → `vw_paid_media_daily`, `mart_health`, page-path views, etc. | Manual/scheduled Dataform workflow **per client workspace** |

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

### Built ONLY by the `sagefrog-dataform` project (manual per client) ⚠️

| Mart | Dashboard panel | Notes |
|---|---|---|
| **`vw_paid_media_daily`** | **Overview → Summary cards + Trend chart** | The first panel users see. No app code builds this. |
| **`mart_health`** | **Overview → Data health** | (A GA4 freshness row is appended by the app, but the paid-media rows come from Dataform.) |

**Consequence:** with only the portal steps above, a new client's **Overview
Summary and Trend chart will be empty/error** until Dataform is set up for that
client's project.

### Dataform setup per client (from `sagefrog-dataform/README.md`)

1. In **GCP Console → Dataform**, link the `sagefrog-dataform` repo and create a
   **workspace for the client's GCP project**.
2. Set **compilation variables**: `raw_google_dataset`, `raw_linkedin_dataset`,
   `ga4_dataset` (= the GA4 property dataset, e.g. `analytics_123456789`),
   `mart_dataset` (`marketing_marts`).
3. Grant the **Dataform service account** BigQuery access (Data Editor on
   `marketing_marts`, Data Viewer on the raw/GA4 datasets, Job User on the
   project).
4. **Run** (Execute all) and **schedule** a daily workflow (after the 11:30 UTC
   Railway cron, e.g. 12:00 UTC).

---

## 8. Verification checklist

After setup, confirm:

- [ ] `/dashboard/{slug}` loads the Nixon template (not the old snapshot page).
- [ ] Sidebar is identical across Overview / Settings / Connectors / Files.
- [ ] Each connected connector card shows a successful last sync.
- [ ] Overview **Summary** + **Trend** show numbers → confirms **Dataform ran**.
- [ ] Website Analytics panels show data → confirms GA4 sync + app views.
- [ ] Explorer tabs (Google/LinkedIn/Meta) show ads → confirms ad connectors.
- [ ] Search Console / SEMrush panels populate (if those connectors are used).

If Summary is empty but Explorer/Analytics work, **Dataform hasn't run** for the
project (see §7).

---

## Critical gaps / follow-ups

These are known gaps between the "create dashboard + connect connectors" ideal
and what's actually automated today. **Flagged for follow-up — not yet fixed.**

1. **🔴 Dataform is a manual, per-client, out-of-band step.** `vw_paid_media_daily`
   (Overview Summary/Trend) and `mart_health` are Dataform-only, and Dataform
   requires a hand-created workspace + compilation vars + service-account grants
   + a schedule **per client GCP project**. Nothing in the portal creates or
   triggers this. This is the single biggest blocker to true self-serve
   onboarding. *Follow-up: either port `vw_paid_media_daily`/`mart_health` into
   an app-built provisioner (like the GA4/Google/Meta marts already are), or
   automate Dataform workspace creation + runs via the Dataform API.*

2. **🟠 Duplicate/competing mart definitions (drift risk).**
   `explorer_google_ads_daily`, `fact_linkedin_ads_creative_daily`,
   `vw_page_path_daily`, and `vw_page_path_source_daily` are defined in **both**
   the app connector syncs **and** `sagefrog-dataform`. Whichever runs last wins,
   and the two definitions can diverge in schema (this has bitten the GA4 views
   before). *Follow-up: pick one owner per mart and delete the other definition.*

3. **🟠 GCP project + IAM is manual.** Creating the project and granting the two
   roles (Data Editor + Job User) is unavoidably GCP-side, but there's no
   in-portal checklist or "test access" button outside the connector wizard, and
   the roles requirement is easy to get wrong (Data Editor alone is not enough —
   Job User is also required).

4. **🟡 No settings-UI control for `dashboard_mode`.** New dashboards get
   `bigquery_nixon` automatically at creation, but there's no admin toggle to
   change a dashboard's mode later — it requires a direct DB update
   (`UPDATE client_dashboard_config SET dashboard_mode='bigquery_nixon' WHERE client_slug=…`).
   Dashboards created before this default was added must be migrated by hand.

5. **🟡 First-sync data latency isn't surfaced.** After connecting connectors,
   the Overview stays empty until (a) a sync runs and (b) Dataform runs. There's
   no in-dashboard "provisioning in progress / waiting for first sync" state —
   it just looks empty, which is easy to mistake for a bug.

6. **🟡 GA4 dual path.** The app pulls GA4 via the Data API and builds the GA4
   views, but `sagefrog-dataform` also references a native-export `ga4_dataset`
   var for its page-path views. Confirm which path is authoritative per client
   to avoid two sources for the same panel.
