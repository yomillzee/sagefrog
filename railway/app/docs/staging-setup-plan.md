# Staging site — refined setup plan (Proposal B, decisions locked)

Status: **ready to build**, pending Railway provisioning + sign-off.
This refines Proposal B in [`staging-and-ci-proposal.md`](./staging-and-ci-proposal.md)
into a concrete, decision-complete plan. Nothing in this doc is built yet.

**Prerequisite:** the agreed sequencing puts **CI (Proposal A) first**. This plan
should land _after_ CI is green and required on `main`, not ahead of it.

## Goal

A persistent staging site the team can iterate on before shipping to live, that:

- serves **real client numbers** (accuracy), not synthetic data;
- stays **cheap on BigQuery** — target **~zero incremental BQ calls**;
- is **simple**: one extra promotion hop, own URL, own login;
- lets the team **break things safely** without touching production.

## Locked decisions

| Question | Decision |
|---|---|
| Data model | **Own staging Postgres, seeded by copying prod's cache/data tables** (isolated writes, real numbers, ~zero BQ). |
| PII policy | **Copy real marketing data; scrub auth & tokens.** No `web_users`, OAuth tokens, or audit logs land on staging. Reconciles "real data" with the agreed "never clone prod raw" (decision #3). |
| Branch model | Persistent **`staging` branch** → Railway **staging environment** (agreed decision #4). |

## Why this is cheap: how reads actually hit BigQuery

Every dashboard panel, for every client, funnels through **one** helper:
`_cached_bq_read()` in `railway/app/dashboard/routes/api_routes.py`.

```
demo slug        → synthetic demo_data           (BQ never touched)
cache hit        → return api_cache row           (BQ never touched)
cache miss       → live BigQuery fetch(), then write result into api_cache
```

BigQuery is hit **only on a cache miss**. The only things that invalidate that
cache and re-query BQ are the **daily cron-sync services** (`cron-sync-bq`,
`cron-sync-hubspot`), which call `db_cache.invalidate_prefix()` after a sync. The
app itself does almost no BQ when the cache is warm — the TTL floor
(`DASH_CACHE_TTL_SECONDS`, read inside `_cached_bq_read`) already exists to keep it
warm between syncs.

**Therefore staging can serve real, warm data with ~zero BQ** by (1) not running
crons, (2) seeding the cache from prod, (3) freezing the TTL, and (4) a fail-closed
live-fetch guard. See BQ cost controls below.

## Architecture

- **Railway staging environment**, deployed from the **`staging` branch**. Same
  `railway/app/Dockerfile`, same `/health` healthcheck, `restartPolicyType`
  `on_failure` — identical runtime to prod.
- **Own Postgres** (`DATABASE_URL` distinct from prod).
- **No cron-sync services attached.** This is the primary BQ cost control — nothing
  triggers a sync, so nothing invalidates the seeded cache or re-queries BigQuery.

### Data seeding (Postgres → Postgres, zero BQ)

Seed staging by copying prod's **already-cached** rows — prod already paid for those
BigQuery queries, so the copy costs **no BQ**.

**Copy as-is (the "real data"):**

- `api_cache` — the cached BQ dashboard reads (the core of the accuracy).
- `dashboard_snapshots` — full per-client dashboard JSON.
- `metrics_daily`, `campaign_daily` — the Postgres metrics warehouse.
- `dashboard_clients`, `client_dashboard_config`, `client_groups`,
  `dashboard_client_suppressions` — client registry + display config.
- `client_business_line_rules`, `client_ga`, `client_gsc_config`,
  `connector_configs` — routing/classification config (non-secret).
- `harvest_client_goals`, `harvest_client_owners`, `harvest_project_tags` — goals/config.
- `schema_migrations` — copied so staging starts **prod-shaped**, which is the
  point of migration rehearsals.

**Scrub / do not copy (PII, secrets, per-user, operational):**

- `web_users` — emails + password hashes. Staging seeds its **own** admin only,
  via `AUTH_BOOTSTRAP_ADMIN_EMAIL` / `AUTH_BOOTSTRAP_ADMIN_PASSWORD`.
- `oauth_credentials` — live connector tokens. Never copied; staging fails closed
  on live connectors by design (see below).
- `audit_events`, `login_rate_buckets`, `connector_sync_runs`, `cron_locks` —
  audit/operational, not needed for dashboard accuracy.
- `admin_dev_notes`, `client_notepads`, `client_insight_documents`,
  `client_insight_folders`, `feature_requests`, `client_hours_share_links`,
  `consent_scan_configs`, `consent_scan_runs` — internal notes / share tokens /
  scan history; skipped for lightness and to avoid leaking internal commentary.

A `seed_staging.py` one-shot script (run as a Railway job or manually) does the copy
into staging's `DATABASE_URL`, honoring the two lists above. Re-runnable for refreshes.

### BQ cost controls (defense in depth)

1. **No cron services on staging** — nothing invalidates the cache or triggers a sync.
2. **`DASH_CACHE_TTL_SECONDS` set very high** (e.g. `2592000` = 30 days) so seeded
   cache rows effectively never expire → reads always hit cache.
3. **New fail-closed guard — one small app change.** Add a `DASH_DISABLE_LIVE_FETCH`
   env check in `_cached_bq_read`: on a cache **miss**, return the last stale row (or
   an empty-but-valid response) instead of calling the live BigQuery `fetch()`.
   Roughly six lines, **defaults off** so production behavior is unchanged; on
   staging it guarantees even a cache miss or a brand-new panel cannot run up a BQ
   bill. This is the _only_ application-code change the plan requires.
4. **Belt-and-suspenders:** point staging's `BQ_*` project/dataset envs at a
   non-existent/sandbox dataset so any stray query fails fast instead of billing.

Net effect: real numbers on screen, and the only path that could ever touch
BigQuery on staging is disabled at three independent layers.

### Access & safety

Staging holds real client numbers, so treat it as sensitive:

- **Login required** (auth already exists). Staging gets its **own**
  `AUTH_SESSION_SECRET`, `API_KEY`, and admin bootstrap — no shared secrets with prod.
- **No prod OAuth creds** (`GCP_CREDS_*`, connector tokens) on staging.
- **`noindex`** / keep it out of search engines; optionally an IP allowlist or shared
  gate in front of the Railway domain.
- Distinct `PUBLIC_BASE_URL` so generated links stay on the staging host.

## Team workflow (the "simple to iterate" ask)

```
PR → CI green → merge to staging → auto-deploy staging → verify → promote staging→main → prod
```

One extra hop over today's merge-to-`main`. Verification on staging is the checklist
already in the proposal: startup clean, `/health` 200, auth works, **no
`DeadlockDetected`**, `schema_migrations` stable.

**Refresh cadence:** re-run `seed_staging.py` on demand (or weekly) to pull prod's
latest cached numbers into staging — still **zero BQ**, since it copies prod's cache.

## Environment variables (staging)

| Var | Value / note |
|---|---|
| `DATABASE_URL` | staging Postgres (distinct from prod) |
| `DASH_CACHE_TTL_SECONDS` | `2592000` (30d) — freeze seeded cache |
| `DASH_DISABLE_LIVE_FETCH` | `1` — fail-closed on cache miss (new guard) |
| `AUTH_SESSION_SECRET` | staging-only secret |
| `API_KEY` | staging-only key |
| `AUTH_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | staging admin login |
| `PUBLIC_BASE_URL` | staging domain |
| `BQ_*` project/dataset | sandbox/non-existent (fail-fast) |
| OAuth / `GCP_CREDS_*` | **unset** — no live connectors on staging |

## Build checklist (once approved)

1. Create the `staging` branch and the Railway staging environment (own Postgres),
   deploy from `staging`.
2. Set the staging env vars above.
3. Land the `DASH_DISABLE_LIVE_FETCH` guard in `_cached_bq_read` (defaults off).
4. Add `seed_staging.py` (prod-cache → staging copy, honoring copy/scrub lists).
5. Seed, then verify: `/health` 200, login works, dashboards render real numbers,
   BigQuery query count stays flat, `schema_migrations` stable, no `DeadlockDetected`.
6. Document the promotion flow for the team.

## What only you (dashboard actions) can do

Railway environment + Postgres provisioning, backups access, and all secrets live in
the Railway dashboard — those steps are yours. Everything else (the guard, the seed
script, env templates, verification) is code I can implement once the environment
exists and this plan is approved.

## Costs

Per the proposal's estimate: **~$15–30/mo** for a modest staging app + Postgres,
plus the one-time seed-script work. BigQuery adds **~$0** by design.
