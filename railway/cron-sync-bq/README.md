# BigQuery client cron (Railway)

Generic Railway **cron service** that triggers `POST /internal/sync-bq/{CLIENT_SLUG}`
on the main sagefrog API daily, refreshing a BigQuery-mode client (Nixon by
default). One codebase serves any client — set `CLIENT_SLUG` per service.

This is the sibling of `cron-sync-penn` (which is Penn-specific via
`/internal/sync-penn`). Use this for every BQ-first client.

## Add to Railway (one time, per client)

1. In your **sagefrog** Railway project → **New** → **GitHub Repo** → same `yomillzee/sagefrog` repo.
2. **Settings → Root Directory:** `railway/cron-sync-bq`
3. **Settings → Config-as-code:** picks up `railway.toml` (`cronSchedule = 30 11 * * *` ≈ 6:30am US Eastern, offset from Penn's 11:00).
4. **No health check:** disabled in `railway.toml` / `railway.json` — this worker has no web server. Do **not** copy `/health` from the main service.
5. **Variables** (same project/environment as the API):
   - `CRON_SECRET` — same value as on the main API service (a mismatch → HTTP 401).
   - `CLIENT_SLUG` — the client to refresh, e.g. `nixon` (default if unset).
   - Optional: `SYNC_BASE_URL` (default `https://sagefrog-production.up.railway.app`)
   - Optional: `SYNC_DATE_RANGE` (default `LAST_30_DAYS`)
6. Deploy. Use **Settings → Cron → Run now** to test.

For another client later, add a second service with the same root directory and
a different `CLIENT_SLUG` (e.g. `andesa`).

## Notes

- Daily run uses the `cron` trigger → a rolling **30-day** ingestion window. For
  a one-time deep history pull use the backfill (`/internal/backfill-bq/{slug}`,
  180 days) or the admin "Backfill" button on the client's page.
- After **Run now**, deploy logs should show `HTTP 200` and
  `refresh_run: status=... date_range=...`.

## Manual run (local)

```powershell
$env:CRON_SECRET = "your-secret"
$env:CLIENT_SLUG = "nixon"
python run_sync_bq.py
```
