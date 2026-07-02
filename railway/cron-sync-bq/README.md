# BigQuery client cron (Railway)

Generic Railway **cron service** that triggers a daily refresh on the main
sagefrog API. Two modes, controlled by whether `CLIENT_SLUG` is set:

- **Hands-off (recommended, default):** leave `CLIENT_SLUG` unset. The worker
  POSTs `/internal/sync-bq-all`, which re-derives the full client list from
  `connector_configs` on every run. Connecting a source via the Connectors
  wizard (and leaving "Enable daily sync" checked) is then the **only** setup
  step — no Railway provisioning needed per client, and a newly connected
  client is picked up automatically on the next run.
- **Single-client (advanced):** set `CLIENT_SLUG` to scope this service to
  exactly one client via `/internal/sync-bq/{slug}`. Use this only if you
  need a client on its own schedule/variables, separate from everyone else.

This is the sibling of `cron-sync-penn` (which is Penn-specific via
`/internal/sync-penn`, a legacy path unrelated to the Connectors system).

## Add to Railway (one time — hands-off mode)

1. In your **sagefrog** Railway project → **New** → **GitHub Repo** → same `yomillzee/sagefrog` repo.
2. **Settings → Root Directory:** `railway/cron-sync-bq`
3. **Settings → Config-as-code:** picks up `railway.toml` (`cronSchedule = 30 11 * * *` ≈ 6:30am US Eastern).
4. **No health check:** disabled in `railway.toml` / `railway.json` — this worker has no web server. Do **not** copy `/health` from the main service.
5. **Variables:**
   - `CRON_SECRET` — same value as on the main API service (a mismatch → HTTP 401).
   - Do **not** set `CLIENT_SLUG` — leaving it unset is what enables hands-off "sync every connected client" mode.
   - Optional: `SYNC_BASE_URL` (default `https://sagefrog-production.up.railway.app`)
   - Optional: `SYNC_DATE_RANGE` (default `LAST_30_DAYS`)
6. Deploy. Use **Settings → Cron → Run now** to test.

You only need **one** service in this mode — it covers every client with a
connector configured, forever. Don't create additional per-client services
unless you specifically need one client on a separate schedule.

## Notes

- Daily run uses the `cron` trigger → a rolling **30-day** ingestion window
  per client. `connector_configs.sync_enabled` gates which connectors within
  each client actually run (unchecking "Enable daily sync" for a connector
  excludes it from this loop, but a manual "Run sync now" click always
  still works regardless).
- For a one-time deep history pull use the backfill (`/internal/backfill-bq/{slug}`,
  180 days) or the "Backfill" step in the connector setup wizard.
- After **Run now**, deploy logs should show `HTTP 200` and, in hands-off
  mode, `clients_synced=N failed=[]`.
- If any client fails (bad credentials, BQ outage, etc.) the run continues
  for every other client — check the `failed` list and `results` in the
  response body for per-client detail.

## Manual run (local)

```powershell
$env:CRON_SECRET = "your-secret"
python run_sync_bq.py   # hands-off: syncs every connected client

# or, single-client:
$env:CLIENT_SLUG = "nixon-bq-test"
python run_sync_bq.py
```
