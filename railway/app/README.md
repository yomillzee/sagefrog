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

### Multiple GA4 clients (different GCP projects)

Railway `BQ_PROJECT_ID` / `BQ_DATASET_ID` stay the **default** (e.g. Penn). Add other projects via **`GA4_CLIENTS`** (one-line JSON) or per-request overrides on sync.

1. **IAM:** Grant your Railway service account **BigQuery Data Viewer** on each project (`penn-community-b-...`, `sagefrog`, `synergistix-497616`). Cross-project reads fail without this even if SQL is correct.

2. **Registry (recommended for GPT):** set `GA4_CLIENTS` with slugs `penn`, `sagefrog`, `synergistix` and each project's `bq_dataset_id` (`analytics_XXXXX` from GA4 → BigQuery Link).

3. **Sync per client:**
   ```http
   POST /ga4/warehouse/sync
   {"client_key": "sagefrog", "date_range": "LAST_180_DAYS"}
   ```
   Or without registry:
   ```http
   {"bq_project_id": "sagefrog", "bq_dataset_id": "analytics_123456789", "date_range": "LAST_180_DAYS"}
   ```

4. **List configured clients:** `GET /ga4/clients`

5. **Ad-hoc SQL:** `POST /ga4/query` with fully qualified tables:
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

Paste that **one long line** into `GCP_SERVICE_ACCOUNT_JSON` in Railway (no quotes around it).

**Option B — Minified one line:** If you prefer raw JSON, it must be a **single line** with no line breaks inside the string. Example with Python:

```bash
python -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(',',':')))" key.json
```

**Option C — Railway CLI:** From the folder that contains the key file, you can set the variable from a file without the web UI mangling newlines (adjust service name as needed):

```bash
railway variables --set "GCP_SERVICE_ACCOUNT_JSON=$(cat key.json)"
```

### ChatGPT Custom Action (GPT)

1. Deploy this service on Railway and set **`API_KEY`** to a long random string.
2. In the GPT Action, point the schema at your public base URL (e.g. `https://<your-service>.up.railway.app/openapi.json`) or paste an OpenAPI fragment for the routes you need.
3. **Authentication:** use **API key** (or equivalent) and send either:
   - Header **`Authorization`**: `Bearer <API_KEY>`, or
   - Header **`X-API-Key`**: `<API_KEY>`
4. `GET /health` stays **unauthenticated** so Railway’s health check keeps working.

#### YouTube video links (recommended for video creative review)

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

**GPT instruction snippet:** “When the user asks for YouTube links for Google Ads videos, call `youtubeVideos` with their customer ID. Return `youtube_watch_url` and campaign/ad context from the response.”

## Endpoints

- `GET /health` — deploy health check
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
- `POST /linkedin/warehouse/sync` — backfill LinkedIn into `metrics_daily`
- `POST /google-ads/warehouse/sync` — backfill Google Ads (`customer_id` + `date_range`)
- `POST /ga4/warehouse/sync` — backfill GA4 from BigQuery export (`source=ga4`)
- `GET /warehouse/status` — row counts: `linkedin_rows`, `google_rows`, `ga4_rows`
- `GET /warehouse/metrics?from_date=&to_date=&source=linkedin|google|ga4&account_id=` — read stored daily history
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
