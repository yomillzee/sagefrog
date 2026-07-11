# Sagefrog Marketing Analytics Platform

A multi-platform marketing analytics dashboard and API service built for a digital
marketing agency. Sagefrog aggregates paid-media performance from **Google Ads,
LinkedIn, Meta, GA4 / BigQuery, Indeed, Harvest, and HubSpot** into a single,
branded, per-client view — and exposes the same data through an authenticated API
that a ChatGPT Custom Action can query on behalf of agency staff.

Penn Community Bank is the primary (and most built-out) client dashboard, but the
platform is designed to host many clients, each with its own dashboard, data
connectors, and access controls.

---

## Table of contents

- [What it does](#what-it-does)
- [Who uses it](#who-uses-it)
- [Architecture at a glance](#architecture-at-a-glance)
- [Repository layout](#repository-layout)
- [Backend](#backend)
- [Frontend](#frontend)
- [Data model](#data-model)
- [Authentication & security](#authentication--security)
- [Third-party integrations](#third-party-integrations)
- [Data ingestion & refresh](#data-ingestion--refresh)
- [Deployment (Railway)](#deployment-railway)
- [Local development](#local-development)
- [Environment variables](#environment-variables)
- [Testing](#testing)
- [Further reading](#further-reading)

---

## What it does

- **Unified paid-media reporting.** Pulls daily spend, impressions, clicks, and
  conversions from every connected ad platform into one time-series fact table and
  renders it as summary cards, campaign explorers, budget pacing, and drill-down
  tables.
- **GA4 / BigQuery analytics.** Runs SQL against each client's GA4 events-export
  dataset for traffic, top pages, source/medium, device split, and attribution
  panels.
- **Branded client dashboards.** Each client sees a read-only, logo-branded view of
  only their own data.
- **Admin console.** Agency staff manage dashboards, users, OAuth connections,
  business-line classification rules, budgets, and client-facing documents.
- **API for automation.** OpenAPI-described endpoints let a ChatGPT Custom Action
  (or any authorized caller) query live performance data.

## Who uses it

| Audience | Access | Capabilities |
|---|---|---|
| **Agency admins** | Browser login (session) | Manage dashboards, users, connectors, rules, documents; view any client |
| **Clients** | Browser login (session) | Read-only view of their own branded dashboard |
| **ChatGPT / API callers** | `API_KEY` (Bearer / `X-API-Key`) | Query live platform data via the JSON API |
| **Scheduled workers** | `CRON_SECRET` header | Trigger periodic data syncs |

---

## Architecture at a glance

```
                       ┌──────────────────────────────────────────────┐
   Browser  ─────────► │  FastAPI app  (railway/app, uvicorn)          │
   (session cookie)    │                                              │
                       │  • server-rendered HTML dashboards           │
   ChatGPT / API ────► │  • JSON API (API_KEY protected)              │ ──► PostgreSQL
   (API_KEY)           │  • admin & OAuth flows (session protected)   │     (metrics,
                       │  • /internal/sync-* (CRON_SECRET protected)  │      cache,
   Cron workers  ────► │                                              │      snapshots,
   (CRON_SECRET)       └───────────────┬──────────────────────────────┘      users, …)
                                       │
                    ┌──────────────────┼───────────────────────────────┐
                    ▼                  ▼                                ▼
              Google Ads          GA4 / BigQuery                  LinkedIn / Meta /
              (google-ads)        (google-cloud-bigquery)         Indeed / Harvest /
                                                                  HubSpot (httpx)
```

- **No SPA / JS framework.** All UI is server-side HTML rendered by Python; page
  interactivity is vanilla JavaScript inlined in `<script>` tags.
- **No ORM / migrations framework.** Raw SQL via `psycopg` v3; each module runs its
  own idempotent `ensure_schema()` (`CREATE TABLE IF NOT EXISTS …`).
- **Cache-first.** Every third-party API call checks a SHA256-keyed Postgres cache
  (`api_cache`, ~1h TTL) before hitting the live platform.

---

## Repository layout

```
sagefrog/
├── ARCHITECTURE_REVIEW.md          # Deep architecture & risk review
├── docs/
│   └── CREATING_A_NEW_DASHBOARD.md # How to onboard a new client dashboard
└── railway/                        # One folder per Railway service
    ├── app/                        # Main FastAPI application
    │   ├── main.py                 # App + all non-dashboard routes, middleware
    │   ├── models.py               # Pydantic request/response schemas
    │   ├── web_security.py         # Security headers + CSRF protection
    │   ├── web_auth.py / web_users.py / security.py / cron_security.py
    │   ├── login_rate_limit.py / audit_log.py / oauth_flows.py / oauth_store.py
    │   ├── google_ads_service.py   # Platform integrations …
    │   ├── linkedin_service.py / meta_service.py / indeed_service.py
    │   ├── bigquery_service.py / ga4_*.py / hubspot_*.py / harvest_*.py
    │   ├── warehouse.py / db_cache.py / dashboard_snapshots.py   # Data layer
    │   ├── dashboard/              # Refactored dashboard sub-package
    │   │   ├── routes/             #   FastAPI handlers (core, settings, files, …)
    │   │   ├── services/           #   Refresh, warehouse sync, snapshot metrics
    │   │   ├── renderers/          #   HTML generation
    │   │   └── utils/              #   Formatting, URLs, auth & date helpers
    │   ├── openapi_gpt.py          # ChatGPT Custom Action OpenAPI schema
    │   └── tests/                  # unittest suite
    ├── cron-sync-penn/             # Cron worker → POST /internal/sync-penn
    ├── cron-sync-bq/               # Cron worker → generic BigQuery client refresh
    ├── cron-sync-hubspot/          # Cron worker → POST /internal/sync-hubspot
    └── cron-penn-bq-test/          # Cron worker (Penn BigQuery test harness)
```

---

## Backend

- **Language / framework:** Python 3, FastAPI (ASGI), served by Uvicorn.
- **Entry point:** `railway/app/main.py`.
- **Module style:** flat modules at `railway/app/` for platform integrations, auth,
  and the data layer, plus a partially-refactored `dashboard/` sub-package
  (`routes` / `services` / `renderers` / `utils`). `dashboard_service.py` is a
  compatibility façade re-exporting the sub-package.

### API surface

**Public / no auth**
- `GET /` — root info object
- `GET /health` — health check for the load balancer
- `GET /login`, `POST /login`, `POST /logout`
- static assets under `/static`

**Session protected (browser)**
- `GET /dashboard/{slug}/…` — dashboards, settings, files (client or admin)
- `GET|POST /admin/…` — admin console (admin role)
- `GET|POST /oauth/{platform}/…` — OAuth connect / callback

**API-key protected (`Authorization: Bearer <API_KEY>` or `X-API-Key`)**
- `GET|POST /google-ads/…`, `/linkedin/…`, `/meta/…`, `/ga4/…`, `/indeed/…`
- `GET /warehouse/…`

**Cron-secret protected (`X-Cron-Secret`)**
- `POST /internal/sync-penn`, `/internal/sync-bq/{slug}`, `/internal/sync-bq-all`,
  `/internal/sync-hubspot`

> API docs (`/docs`, `/redoc`, `/openapi.json`) are available in local dev and can
> be disabled in production with `DISABLE_API_DOCS=1`.

---

## Frontend

There is **no client-side framework** (no React/Vue/Alpine, no Tailwind/Bootstrap).
Everything is generated by Python renderer modules under
`railway/app/dashboard/renderers/` and returned as `text/html`.

| Renderer | Responsibility |
|---|---|
| `base_layout.py` | Topbar, account chip, refresh toolbar, page shell, sidebar |
| `dashboard_renderer.py` | Main dashboard (Campaign Explorer, GA4 panels, filters) |
| `cards_renderer.py` | Summary cards, hero metrics row, budget pacing |
| `bigquery_dashboard_renderer.py` | BigQuery-mode ("Nixon" template) client dashboards |
| `analytics_renderer.py` | GA4 pages / traffic / device / landing panels |
| `settings_renderer.py` | Insights editor, connector/OAuth status |
| `files_renderer.py` | File browser, time-tracking, insight document library |
| `connectors_renderer.py` | Connector wizard (connect / test / sync / disconnect) |

State lives in **server-side rendering + a signed session cookie** (`eos_session`,
14-day max age, signed via Starlette's `SessionMiddleware` / `itsdangerous`). Date
filtering, charts, and drill-downs are handled by inline vanilla JS.

---

## Data model

PostgreSQL (Railway-managed), accessed with **`psycopg` v3** and raw SQL. Key tables:

| Table | Purpose |
|---|---|
| `metrics_daily` | Central daily paid-media fact table (spend, clicks, impressions, conversions). Unique on `(source, account_id, metric_date)` with upsert semantics. |
| `api_cache` | SHA256-keyed cache of platform API responses (~1h TTL). |
| `dashboard_snapshots` | JSONB snapshots of refreshed dashboard data per client per date. |
| `web_users` | Browser login accounts (email, bcrypt hash, role, `client_slug`). |
| `oauth_credentials` | Fernet-encrypted refresh/access tokens per platform. |
| `client_dashboard_config` / `dashboard_registry` | Per-client config and dashboard registry. |
| `client_business_line_rules` | Keyword-based campaign classification per client. |
| `client_insight_documents` | Client documents stored as `BYTEA`. |
| `audit_events` | Append-only audit trail (logins, user management, connector changes). |
| `login_rate_buckets` | Failed-login tracking for IP/email rate limiting. |
| `admin_dev_notes` | Internal agency notes. |

---

## Authentication & security

Three independent auth surfaces:

1. **Browser sessions** — login form → bcrypt verify → `user_id` stored in the signed
   `eos_session` cookie. Role check: `admin` sees any dashboard; `client` is scoped to
   its own `client_slug`. Login is rate-limited per IP and per email.
2. **API key** — `Authorization: Bearer <API_KEY>` or `X-API-Key`, compared in constant
   time. **Fails closed in production**: if `API_KEY` is unset on Railway, protected
   endpoints return `503` rather than opening up.
3. **Cron secret** — `X-Cron-Secret: <CRON_SECRET>`, exact match, for the internal
   `/internal/sync-*` endpoints.

OAuth tokens for the ad platforms are exchanged via PKCE flows and stored **encrypted
at rest** (Fernet) in `oauth_credentials`.

### Security headers & CSRF

`web_security.py` adds two browser-hardening layers, wired in as middleware in
`main.py`:

- **Response security headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`,
  a `Permissions-Policy`, `Strict-Transport-Security` on HTTPS, and a Content-Security-Policy
  scoped to `frame-ancestors` / `base-uri` / `object-src` / `form-action` (intentionally
  **not** restricting inline scripts/styles, which the dashboards rely on).
- **CSRF protection** via a per-session synchronizer token seeded into the session
  cookie. The token is enforced on cookie-authenticated, state-changing requests and
  delivered to the browser two ways with no per-renderer plumbing:
  - a hidden `csrf_token` field auto-injected into every server-rendered `<form method=post>`, and
  - a `<meta name="csrf-token">` plus a small `fetch` wrapper that attaches the
    `X-CSRF-Token` header to same-origin AJAX writes.

  Header-authenticated API callers (Bearer / `X-API-Key`) and the cron secret are
  exempt — they aren't CSRF-prone and stay working unchanged.

---

## Third-party integrations

| Platform | Client library | Auth | Highlights |
|---|---|---|---|
| **Google Ads** | `google-ads` | OAuth refresh token | GAQL queries, account listing, YouTube asset discovery, daily backfill |
| **LinkedIn** | `httpx` | OAuth refresh token | Campaign/creative performance, video URLs, daily backfill; version via `LINKEDIN_VERSION` |
| **Meta (Facebook)** | `httpx` (Graph API) | System-user token via OAuth | Campaign/ad-set insights, creative assets; needs `ads_read` + `business_management` |
| **GA4 / BigQuery** | `google-cloud-bigquery` | GCP service account (per-client or global) | Raw SQL over GA4 events export, daily aggregation; multi-project via `GA4_CLIENTS` |
| **Indeed** | `httpx` | Client-credentials OAuth | Job postings, registration analytics |
| **Harvest** | `httpx` | OAuth refresh token | Time-tracking data (Files page) |
| **HubSpot** | `httpx` | OAuth / token | CRM/marketing sync via `/internal/sync-hubspot` |
| **ChatGPT Custom Actions** | — | shared `API_KEY` | `GET /openapi-gpt.json` returns a dynamic OpenAPI 3.1 schema |

Credentials for GCP are parsed flexibly (raw JSON, base64, or double-encoded) by
`ga4_credentials.py`.

---

## Data ingestion & refresh

- **Scheduled sync (cron workers).** Each `railway/cron-*` service is a minimal
  `urllib`-only script that POSTs to an `/internal/sync-*` endpoint on the app with the
  `X-Cron-Secret` header. Schedules are configured in Railway, not in code.
  - `cron-sync-penn` → `POST /internal/sync-penn`
  - `cron-sync-bq` → generic per-client BigQuery refresh; with `CLIENT_SLUG` unset it
    hits `/internal/sync-bq-all`, which re-derives the client list from connector
    configs so connecting a source in the wizard is the only setup step
  - `cron-sync-hubspot` → `POST /internal/sync-hubspot`
- **On-demand warehouse sync.** `POST /{platform}/warehouse/sync` pulls daily metrics
  and upserts into `metrics_daily`.
- **Dashboard refresh.** `POST /dashboard/{slug}/refresh` pulls live data across
  platforms, syncs the warehouse for the current month, computes totals/breakdowns/
  budget pacing, and writes a `dashboard_snapshots` row. A 60-second cooldown is
  enforced per client.

---

## Deployment (Railway)

The app runs on [Railway](https://railway.app) as several services in one project,
each rooted at a folder under `railway/`:

```
Railway Project: sagefrog
  ├── app                 root: railway/app/         start: uvicorn main:app --host 0.0.0.0 --port $PORT
  │                       plugin: PostgreSQL (injects DATABASE_URL)   health: GET /health
  ├── cron-sync-penn      root: railway/cron-sync-penn/     start: python run_sync_penn.py
  ├── cron-sync-bq        root: railway/cron-sync-bq/       start: python run_sync_bq.py
  └── cron-sync-hubspot   root: railway/cron-sync-hubspot/  start: python run_sync_hubspot.py
```

**Startup behavior** (`main.py`): load env → attach `SessionMiddleware` → mount
`/static` → register dashboard routes → each module self-initializes its schema on
first use → bootstrap an admin user if `AUTH_BOOTSTRAP_ADMIN_EMAIL` is set and none
exists. Deploys are triggered by Railway's GitHub integration on push.

---

## Local development

```bash
cd railway/app
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then populate credentials
uvicorn main:app --reload
# → http://127.0.0.1:8000
# → http://127.0.0.1:8000/docs   (Swagger, local only)
```

Notes for local dev:
- With `API_KEY` unset, the platform API endpoints are open (dev convenience); in a
  deployed/Railway environment an unset `API_KEY` fails closed with `503`.
- Session cookies are secure-only by default in deployed environments; for plain-HTTP
  local dev set `AUTH_SESSION_HTTPS_ONLY=0`.
- A `DATABASE_URL` (Postgres) is required for browser login and most data features.

---

## Environment variables

Only the most important variables are listed here; see
[`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) for the exhaustive per-platform table.

### Authentication & security

| Variable | Purpose |
|---|---|
| `API_KEY` | Bearer/`X-API-Key` token for platform API endpoints (fails closed in prod if unset) |
| `CRON_SECRET` | Auth for the `/internal/sync-*` endpoints **only** (no longer shared with other surfaces) |
| `AUTH_SESSION_SECRET` | Signs `eos_session` cookies and OAuth connect-link/state tokens. **Required in production** — no longer falls back to `CRON_SECRET`/`API_KEY` (dev keeps the fallback) |
| `OAUTH_TOKEN_ENCRYPTION_KEY` | Dedicated key for OAuth token encryption (required in production; dev falls back to `AUTH_SESSION_SECRET`) |
| `AUTH_SESSION_HTTPS_ONLY` | Force / disable HTTPS-only cookies (`0` for local HTTP) |
| `AUTH_BOOTSTRAP_ADMIN_EMAIL` / `AUTH_BOOTSTRAP_ADMIN_PASSWORD` | First admin account on initial deploy (password ≥ 10 chars) |
| `AUTH_LOGIN_MAX_FAILURES` / `AUTH_LOGIN_WINDOW_SECONDS` / `AUTH_LOGIN_LOCKOUT_SECONDS` | Login rate-limit tuning |

### Database, cache, deployment

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (auto-injected by Railway) |
| `CACHE_TTL_SECONDS` | API response cache lifetime (default `3600`) |
| `PUBLIC_BASE_URL` | Override base URL for OAuth callbacks |
| `DISABLE_API_DOCS` | `1` to hide `/docs`, `/redoc`, `/openapi.json` |
| `DASHBOARD_CLIENTS` | JSON registry of additional (non-Penn) client dashboards |

### Platforms

Google Ads (`GOOGLE_ADS_*`), LinkedIn (`LINKEDIN_*`), Meta (`META_*`), GA4/BigQuery
(`GCP_SERVICE_ACCOUNT_JSON`, `BQ_PROJECT_ID`, `BQ_DATASET_ID`, `GA4_CLIENTS`,
`GCP_CREDS_*_BASE64`), Indeed (`INDEED_*`), Harvest (`HARVEST_*`), and Penn-specific
overrides (`PENN_*`). See the architecture review for the full list.

---

## Testing

Tests live in `railway/app/tests/` and use Python's built-in `unittest`.

```bash
cd railway/app
python -m unittest discover -s tests
# or a single module:
python -m unittest tests.test_web_security
```

---

## Further reading

- [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) — full architecture walk-through,
  environment-variable reference, technical-debt notes, and risk register.
- [`docs/CREATING_A_NEW_DASHBOARD.md`](docs/CREATING_A_NEW_DASHBOARD.md) — how to onboard
  a new client dashboard.
