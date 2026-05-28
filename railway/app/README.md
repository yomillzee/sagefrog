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
- `GET /linkedin/performance` — spend/clicks/impressions/conversions by active campaign
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
