# Penn dashboard cron (Railway)

Lightweight Railway **cron service** that triggers `POST /internal/sync-penn` on the main sagefrog API daily.

## Add to Railway (one time)

1. In your **sagefrog** Railway project → **New** → **GitHub Repo** → same `yomillzee/sagefrog` repo.
2. **Settings → Root Directory:** `railway/cron-sync-penn`
3. **Settings → Config-as-code:** should pick up `railway.toml` (`cronSchedule = 0 11 * * *` = ~6am US Eastern).
4. **Variables** (same project/environment as the API):
   - `CRON_SECRET` — same value as on the main API service
   - Optional: `SYNC_BASE_URL` (default `https://sagefrog-production.up.railway.app`)
   - Optional: `SYNC_DATE_RANGE` (default `LAST_30_DAYS`)
5. Deploy. Use **Settings → Cron → Run now** to test.

The cron container starts, calls the API, prints JSON, and exits. The main API service stays always-on.

## Troubleshooting (cron not updating dashboard)

1. **Separate service required** — Cron does **not** run on the main `railway/app` service. You need a second Railway service with root directory `railway/cron-sync-penn`.
2. **Cron schedule enabled** — In that service: **Settings → Cron** must show `0 11 * * *` (from `railway.toml`) and be **enabled**.
3. **`CRON_SECRET` must match** — Same value on **both** the cron service and the main API. A mismatch returns HTTP 401 and the snapshot will not update.
4. **`CRON_SECRET` on the API** — If missing on the main service, sync returns HTTP 503.
5. **`SYNC_BASE_URL`** — Must point at your live API hostname (default `https://sagefrog-production.up.railway.app`).
6. **`DATABASE_URL` on the API** — Required to save snapshots; without Postgres, sync fails with 503.
7. **Check deploy logs** — After **Run now**, logs should show `HTTP 200` and `sync_meta: trigger=cron`. In the dashboard **Settings**, “Last data pull” should show source `cron` and a recent UTC time.
8. **Full sync can time out** — If the job exceeds Railway’s limit, increase timeout or use a shorter `SYNC_DATE_RANGE` (e.g. `LAST_7_DAYS`) on the cron service until stable.

## Manual run (local)

```powershell
$env:CRON_SECRET = "your-secret"
python run_sync_penn.py
```
