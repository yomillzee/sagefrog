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

## Manual run (local)

```powershell
$env:CRON_SECRET = "your-secret"
python run_sync_penn.py
```
