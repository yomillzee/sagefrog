# EOS Ads + GA4 Service (Railway)

FastAPI wrapper for Google Ads API search (GAQL), LinkedIn Marketing API reporting, and GA4 BigQuery querying.

## Railway settings

| Setting | Value |
|--------|--------|
| **Root directory** | `railway/app` |
| **Start command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

`railway.toml` in this folder sets the start command and health check automatically if Railway picks it up.

## Environment variables

Copy from `.env.example` into Railway **Variables**:

- **`API_KEY`** (recommended in production) — random secret you generate. When set, all `/google-ads/*`, `/linkedin/*`, and `/ga4/*` endpoints require it via **`Authorization: Bearer <API_KEY>`** or **`X-API-Key: <API_KEY>`**. Leave unset for local-only testing (no auth).
- `GOOGLE_ADS_DEVELOPER_TOKEN`
- `GOOGLE_ADS_CLIENT_ID`
- `GOOGLE_ADS_CLIENT_SECRET`
- `GOOGLE_ADS_REFRESH_TOKEN`
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (optional, MCC / manager account)
- `GCP_SERVICE_ACCOUNT_JSON` — see [GCP key on Railway](#gcp_service_account_json-on-railway) below
- `BQ_PROJECT_ID` (e.g. `penn-community-b-1699391543298`)
- `BQ_DATASET_ID` (e.g. `analytics_313855909`)

### Postgres warehouse (`metrics_daily`)

Attach a **Postgres** plugin on Railway so `DATABASE_URL` is injected. The service creates:

| Table | Purpose |
|--------|---------|
| `api_cache` | Short-lived API response cache (~1 hour) |
| `metrics_daily` | One row per **day** per account (`source` = `linkedin` or `google`) |

**Backfill history (up to `LAST_180_DAYS` per call):**

```http
POST /linkedin/warehouse/sync
{"account_id": "502439493", "date_range": "LAST_180_DAYS"}

POST /google-ads/warehouse/sync
{"customer_id": "8032778786", "date_range": "LAST_180_DAYS"}

POST /ga4/warehouse/sync
{"date_range": "LAST_180_DAYS"}
```

Each `linkedinPerformance` call also syncs LinkedIn for that window when Postgres is enabled.

**GA4 warehouse field mapping:** `clicks` = sessions, `impressions` = page views, `conversions` = key events, `spend` = 0.

### Browser login (admin / client users)

Requires **Postgres** (`DATABASE_URL`). The service creates a `web_users` table.

| Variable | Purpose |
|----------|---------|
| `AUTH_SESSION_SECRET` | Signs session cookies and OAuth connect-link/state tokens (use a long random string). **Required in production** — it no longer falls back to `CRON_SECRET` / `API_KEY`; local dev keeps that fallback for convenience. |
| `AUTH_SESSION_HTTPS_ONLY` | Set `1` on Railway so cookies are HTTPS-only |
| `AUTH_BOOTSTRAP_ADMIN_EMAIL` | Creates the **first** admin if no admin exists yet |
| `AUTH_BOOTSTRAP_ADMIN_PASSWORD` | Password for bootstrap admin (min 10 characters) |

**Routes:**

- `GET /login` — sign in
- `GET /admin` — admin hub (create users, manage dashboards, open dashboards)
- `GET /dashboard/penn` — Penn dashboard (admin or `client` user with `client_slug=penn`)

**Roles:**

- `admin` — all dashboards + `/admin`
- `client` — only dashboards matching `client_slug` (e.g. `penn`)

### Client document sharing

Requires **Postgres** (`DATABASE_URL`). Uploaded `.docx` and `.pdf` files (up to 25 MB) are stored in the `client_insight_documents` table. Admins create folders, upload, and delete at `/dashboard/{client}/files`; all authenticated dashboard users can browse folders and download files from the same page.

Create additional users at `/admin` after signing in as admin. The **Audit log** section on `/admin` records sign-ins, sign-outs, failed logins, and user create/deactivate actions (with actor, target, IP).

Dashboards are session-only: the legacy `?key=` share link (and its `DASHBOARD_SECRET`) has been retired — give a viewer a `client`-role login instead. **`API_KEY` is unchanged** — still used for the `/google-ads/*`, `/linkedin/*`, `/meta/*` API routes. `CRON_SECRET` is now dedicated to the internal `/internal/sync-*` cron endpoints only.

**Production hardening:** On Railway, `/docs` and `/openapi.json` are disabled automatically (`DISABLE_API_DOCS=0` to re-enable). Failed logins are rate-limited per IP/email (`AUTH_LOGIN_MAX_FAILURES`, default 5 per 15 minutes, then 15-minute lockout).

### Multiple GA4 clients (different GCP projects)

Railway `BQ_PROJECT_ID` / `BQ_DATASET_ID` stay the **default** (e.g. Penn). Add other projects via **`GA4_CLIENTS`** (one-line JSON) or per-request overrides on sync.

1. **IAM:** Grant the active service account **BigQuery Data Viewer** on each project it queries (`penn-community-b-...`, `nixon-medical`, `sagefrog`, `synergistix-497616`). Cross-project reads fail without this even if SQL is correct.

2. **Client-specific credentials (optional):** add `credentials_env` to a GA4 client when that client should use its own base64 service account JSON Railway variable. If `credentials_env` is omitted, the backend falls back to the legacy global `GCP_SERVICE_ACCOUNT_JSON`. Never expose or commit the credential value itself.

3. **Registry (recommended for GPT):** set `GA4_CLIENTS` with slugs `penn`, `nixon`, `sagefrog`, `synergistix` and each project's `bq_dataset_id` (`analytics_XXXXX` from GA4 → BigQuery Link). Nixon Medical example:
   ```json
   "nixon": {
     "label": "Nixon Medical",
     "bq_project_id": "nixon-medical",
     "bq_dataset_id": "analytics_test",
     "credentials_env": "GCP_CREDS_NIXON_BASE64"
   }
   ```

4. **Sync per client:**
   ```http
   POST /ga4/warehouse/sync
   {"client_key": "sagefrog", "date_range": "LAST_180_DAYS"}
   ```
   Or without registry:
   ```http
   {"bq_project_id": "sagefrog", "bq_dataset_id": "analytics_123456789", "date_range": "LAST_180_DAYS"}
   ```

5. **List configured clients:** `GET /ga4/clients`

6. **Ad-hoc SQL:** `POST /ga4/query` with fully qualified tables:
   `` `sagefrog.analytics_XXXXX.events_*` ``

Each client gets separate rows in `metrics_daily` (`source=ga4`, different `account_id`).

**Verify storage:**

```http
GET /warehouse/status
GET /warehouse/metrics?from_date=2025-12-01&to_date=2026-05-28&source=linkedin&account_id=502439493
```

### LinkedIn Marketing API

Set these in Railway (same names as `help/linkedin-ads-dashboard`):

- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_REFRESH_TOKEN` — long-lived refresh token from the LinkedIn OAuth flow (scopes: `r_ads`, `r_ads_reporting`)
- `LINKEDIN_VERSION` (optional, default `202604`) — sent as the `Linkedin-Version` header

Mint a refresh token locally with the dashboard (`npm start` → Settings → Connect LinkedIn), or run your OAuth callback once and copy `refresh_token` into Railway.

**Verify wiring:** `GET /linkedin/env` then `GET /linkedin/test-token` (refreshes OAuth and lists ad accounts).

**Performance:** `GET /linkedin/performance?account_id=123456789&date_range=LAST_30_DAYS`

### Meta Marketing API (Business Manager)

One Business Manager token lists **all client ad accounts** under that BM. Set in Railway:

- `META_APP_ID` — Meta app ID
- `META_APP_SECRET` — Meta app secret
- Connect Meta in Settings stores a long-lived token with `ads_read` and `business_management`
- `META_BUSINESS_ID` — e.g. `1064007753695517`
- `META_API_VERSION` (optional, default `v21.0`)

**Setup:** Business Settings → Users → System users → assign all ad accounts → generate token.

**Verify:** `GET /meta/env` then `GET /meta/test-token` then `GET /meta/accounts`.

**Performance:** `GET /meta/performance?account_id=123456789&date_range=LAST_30_DAYS`

**Warehouse:** `POST /meta/warehouse/sync` with `{"account_id":"123456789","date_range":"LAST_180_DAYS"}` → `metrics_daily` rows with `source=meta`.

### `GCP_SERVICE_ACCOUNT_JSON` on Railway

Pasting the raw multiline JSON into Railway often fails (the UI may truncate or strip content so you only see a single `{`). **Do not rely on multiline paste.**

**Option A — Base64 (recommended):** Put the **entire key file** into one line of base64. This service decodes it when the value does **not** start with `{`.

PowerShell (replace the path, then paste the printed line into Railway):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\your-key.json"))
```

Or copy straight to the clipboard:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\your-key.json")) | Set-Clipboard
```

Paste that **one long line** into `GCP_SERVICE_ACCOUNT_JSON` in Railway (no quotes around it). For client-specific credentials, paste the same kind of base64 value into the variable named by `GA4_CLIENTS.<client>.credentials_env`, such as `GCP_CREDS_PENN_BASE64`.

**Option B — Minified one line:** If you prefer raw JSON, it must be a **single line** with no line breaks inside the string. Example with Python:

```bash
python -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(',',':')))" key.json
```

**Option C — Railway CLI:** From the folder that contains the key file, you can set the variable from a file without the web UI mangling newlines (adjust service name as needed):

```bash
railway variables --set "GCP_SERVICE_ACCOUNT_JSON=$(cat key.json)"
```

### YouTube video links (recommended for video creative review)

Use **`POST /google-ads/youtube-videos`** — reads `asset.youtube_video_asset.youtube_video_id` from Google Ads (not ad name text).

```json
{
  "customer_id": "1234567890",
  "include_account_assets": true,
  "include_metrics": false,
  "date_range": "LAST_30_DAYS"
}
```

Example response fields per row: `campaign_name`, `ad_name`, `youtube_video_id`, `youtube_watch_url`, `youtube_embed_url`, `youtube_thumbnail_url`, `source` (`ad_group_ad_asset_view`, `video_ad`, or `asset`).

## Endpoints

- `GET /health` — deploy health check
- `GET /login` — browser sign-in (Postgres users)
- `GET /admin` — manage users (admin role)
- `GET /dashboard/penn` — Penn HTML dashboard (requires a signed-in session)
- `GET /google-ads/env` — which credentials are set (no secrets returned)
- `GET /google-ads/test-token` — OAuth refresh only (debug `invalid_grant` before GAQL)
- `POST /google-ads/search` — run a GAQL query (rows are structured JSON dicts)
- `POST /google-ads/youtube-videos` — YouTube watch/embed/thumbnail URLs from **video assets** (for GPT Custom Actions)
- `GET /google-ads/accounts` — list accessible customer accounts under current credentials
- `POST /google-ads/search-many` — run one GAQL query across multiple customer IDs
- `POST /google-ads/summary-all` — aggregate account-level metrics across all (or selected) accounts
- `GET /linkedin/env` — which LinkedIn credentials are set (no secrets)
- `GET /linkedin/test-token` — OAuth refresh + ad account count
- `GET /linkedin/accounts` — LinkedIn ad accounts for the token
- `GET /linkedin/performance` — spend/clicks/impressions/conversions by active campaign (auto-syncs daily rows to Postgres when `DATABASE_URL` is set)
- `GET /linkedin/campaign-groups` — list campaign groups for one ad account
- `GET /linkedin/campaign-groups/performance` — spend/clicks/impressions/conversions by campaign group
- `POST /linkedin/warehouse/sync` — backfill LinkedIn into `metrics_daily`
- `GET /meta/env` — which Meta credentials are set (no secrets)
- `GET /meta/test-token` — verify access token + ad account count
- `GET /meta/accounts` — Meta ad accounts in Business Manager (owned + client)
- `GET /meta/performance` — spend/clicks/impressions/conversions by campaign (auto-syncs daily rows to Postgres when `DATABASE_URL` is set)
- `POST /meta/warehouse/sync` — backfill Meta into `metrics_daily`
- `POST /google-ads/warehouse/sync` — backfill Google Ads (`customer_id` + `date_range`)
- `POST /ga4/warehouse/sync` — backfill GA4 from BigQuery export (`source=ga4`)
- `GET /warehouse/status` — row counts: `linkedin_rows`, `google_rows`, `ga4_rows`, `meta_rows`
- `GET /warehouse/metrics?from_date=&to_date=&source=linkedin|google|ga4|meta&account_id=` — read stored daily history
- `GET /ga4/env` — validate GA4 BigQuery env wiring (includes **`gcp_service_account_json_char_count`**, **`gcp_service_account_json_parse_ok`**, and a short **`gcp_service_account_json_parse_error`** when parsing fails — no secrets returned)
- `POST /ga4/query` — run a BigQuery SQL query (returns rows)

Example body:

```json
{
  "customer_id": "1234567890",
  "query": "SELECT campaign.id, campaign.name FROM campaign LIMIT 5"
}
```

GA4 query body:

```json
{
  "sql": "SELECT event_date, COUNT(*) AS events FROM `penn-community-b-1699391543298.analytics_313855909.events_*` WHERE _TABLE_SUFFIX BETWEEN '20260501' AND '20260527' GROUP BY event_date ORDER BY event_date",
  "max_rows": 500
}
```

## Local run

```bash
cd railway/app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# fill in .env
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs
