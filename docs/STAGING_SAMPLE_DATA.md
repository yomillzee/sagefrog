# Staging sample data

Staging runs the same code as production but against its own (empty) Postgres,
so there are no login users and the dashboards have no metrics to draw. The
`railway/app/seed_staging.py` script fills those gaps with deterministic,
obviously-fake data so the app is usable end-to-end on staging.

## What it seeds

- **Login users** — an admin, a client user, and a standard user (see
  credentials below).
- **A client group** ("Staging Demo Group") bundling the built-in demo client.
- **~90 days of warehouse metrics** — daily totals and per-campaign rows for the
  built-in Nixon client across the linkedin / google / meta sources, so the
  dashboards render charts.

It is **idempotent** (uses upserts and skips users/groups that already exist)
and **never deletes** anything.

## Running it

From the Railway **staging** service shell (Console tab), or any environment
pointed at the staging database:

```bash
cd railway/app
SEED_STAGING=1 python seed_staging.py
```

### Safety guards

- Refuses to run unless `SEED_STAGING=1` is set.
- Refuses to run if `DATABASE_URL` is missing.
- Aborts if `RAILWAY_ENVIRONMENT_NAME` looks like production (`production` /
  `prod`) unless `SEED_FORCE=1` is also set.

## Default credentials

Override any of these with the matching env vars before running.

| Role | Email (env override) | Password (env override) |
|------|----------------------|-------------------------|
| admin | `admin@staging.sagefrog.test` (`STAGING_ADMIN_EMAIL`) | `staging-admin-1234` (`STAGING_ADMIN_PASSWORD`) |
| client | `client@staging.sagefrog.test` (`STAGING_CLIENT_EMAIL`) | `staging-client-1234` (`STAGING_CLIENT_PASSWORD`) |
| standard | `standard@staging.sagefrog.test` (`STAGING_STANDARD_EMAIL`) | `staging-standard-1234` (`STAGING_STANDARD_PASSWORD`) |

Other knobs: `STAGING_SEED_DAYS` (default `90`) controls how many days of
metrics are generated.

> These are throwaway staging credentials. Do not reuse these emails/passwords
> anywhere real, and prefer setting the `STAGING_*_PASSWORD` env vars to
> something private on the staging service.
