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
- **Hands-off mode returns immediately** — `/internal/sync-bq-all` queues the
  actual sync as a background task (looping every client's every connector
  with live external API calls + BQ writes can run well past a few minutes
  once there's more than a handful of clients, long enough to hit Railway's
  edge proxy timeout on a synchronous request). After **Run now**, the
  deploy log just confirms `HTTP 200` and `queued N client(s): [...]` — it
  does **not** wait for the sync to finish or report success/failure.
- **To check whether a sync actually succeeded**, look at each client's
  Connectors page (`/dashboard/{client_slug}/connectors`) — per-connector
  `last_success_at` / `last_error_message` are updated there as each one
  completes, independent of this cron log. One client failing (bad
  credentials, BQ outage, etc.) never blocks any other client's sync.
- Single-client mode (`CLIENT_SLUG` set) is unchanged and still runs
  synchronously — its response includes `refresh_run: status=...` directly.

## Manual run (local)

```powershell
$env:CRON_SECRET = "your-secret"
python run_sync_bq.py   # hands-off: syncs every connected client

# or, single-client:
$env:CLIENT_SLUG = "nixon-bq-test"
python run_sync_bq.py
```

## Consent & Tracking Health scans (`CRON_JOB=consent-scan-due`)

The same worker can drive scheduled consent scans instead of a BigQuery sync.
Create a **second** Railway cron service with this same root directory and set:

- `CRON_JOB=consent-scan-due` — targets `/internal/consent/scan-due` instead of
  the sync endpoints.
- `CRON_SECRET` — same value as the main API service.
- A schedule to taste (e.g. weekly). The endpoint scans every client whose
  per-client cadence (daily/weekly/monthly, set on each client's Consent Health
  page) is actually due, and is hands-off like `sync-bq-all` — it queues the
  work and returns immediately, guarded by a Postgres lock so runs never stack.

```powershell
$env:CRON_SECRET = "your-secret"
$env:CRON_JOB = "consent-scan-due"
python run_sync_bq.py
```

## Web Mentions ingest (`CRON_JOB=web-mentions`)

The same worker can drive the Google Alerts RSS ingest that feeds each client's
**Web Mentions** page. Create another Railway cron service with this root
directory and set:

- `CRON_JOB=web-mentions` — targets `/internal/web-mentions/ingest-due`.
- `CRON_SECRET` — same value as the main API service.
- A daily schedule (Google Alerts feeds only carry the most recent results, so
  daily is the useful floor; more often mostly re-reads the same entries).

The endpoint polls every client that has at least one **active** alert, is
hands-off like `sync-bq-all` (it queues the work and returns immediately), and
is guarded by a Postgres lock so runs never stack. One client's broken feed is
recorded on that alert and never stops another client's ingest — check a
client's Web Mentions page (admins see per-alert "last successful sync" and the
error) rather than this log.

```powershell
$env:CRON_SECRET = "your-secret"
$env:CRON_JOB = "web-mentions"
python run_sync_bq.py
```
