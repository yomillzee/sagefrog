# EOS Google Ads Service (Railway)

FastAPI wrapper for Google Ads API search (GAQL).

## Railway settings

| Setting | Value |
|--------|--------|
| **Root directory** | `railway/app` |
| **Start command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

`railway.toml` in this folder sets the start command and health check automatically if Railway picks it up.

## Environment variables

Copy from `.env.example` into Railway **Variables**:

- **`API_KEY`** (recommended in production) — random secret you generate. When set, all `/google-ads/*` endpoints require it via **`Authorization: Bearer <API_KEY>`** or **`X-API-Key: <API_KEY>`**. Leave unset for local-only testing (no auth).
- `GOOGLE_ADS_DEVELOPER_TOKEN`
- `GOOGLE_ADS_CLIENT_ID`
- `GOOGLE_ADS_CLIENT_SECRET`
- `GOOGLE_ADS_REFRESH_TOKEN`
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (optional, MCC / manager account)

### ChatGPT Custom Action (GPT)

1. Deploy this service on Railway and set **`API_KEY`** to a long random string.
2. In the GPT Action, point the schema at your public base URL (e.g. `https://<your-service>.up.railway.app/openapi.json`) or paste an OpenAPI fragment for the routes you need.
3. **Authentication:** use **API key** (or equivalent) and send either:
   - Header **`Authorization`**: `Bearer <API_KEY>`, or
   - Header **`X-API-Key`**: `<API_KEY>`
4. `GET /health` stays **unauthenticated** so Railway’s health check keeps working.

## Endpoints

- `GET /health` — deploy health check
- `GET /google-ads/env` — which credentials are set (no secrets returned)
- `GET /google-ads/test-token` — OAuth refresh only (debug `invalid_grant` before GAQL)
- `POST /google-ads/search` — run a GAQL query
- `GET /google-ads/accounts` — list accessible customer accounts under current credentials
- `POST /google-ads/search-many` — run one GAQL query across multiple customer IDs
- `POST /google-ads/summary-all` — aggregate account-level metrics across all (or selected) accounts

Example body:

```json
{
  "customer_id": "1234567890",
  "query": "SELECT campaign.id, campaign.name FROM campaign LIMIT 5"
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
