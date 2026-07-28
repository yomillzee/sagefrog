# The Demo Client

A built-in client dashboard populated **entirely with synthetic sample data** —
no GCP project, no BigQuery, no connectors, no OAuth, and no real client data.
It exists so the agency can:

- **Pitch prospects** with a live, fully-populated portal instead of screenshots.
- **Walk existing clients through a working version** before they approve a live
  data sync.
- **Train internal staff** on the portal without touching a real account.

## What it looks like

- **Name:** Northwind Health (Demo) — a fictional B2B healthcare brand.
- **Slug:** `demo` → reachable at `/dashboard/demo`.
- **Appears in the dashboards picker** for any admin, out of the box.
- Every panel is populated: Overview (summary cards, trend, budget pacing, data
  health, website analytics, AI traffic), the Campaign Explorer (Google, Meta,
  LinkedIn, Microsoft, keywords, GA4-verified conversions), Website Analytics
  (pages, sources, devices, landing, user acquisition, demographics,
  conversions), and Search Console (GSC, SEMrush, PageSpeed).

The numbers are **deterministic** — the same panel and date range always render
the same values, so a demo never jitters between refreshes — and they **scale
with the selected date range** and carry realistic weekday seasonality.

## Why it's lightweight

The demo is a normal `bigquery_nixon` client, so it renders through the **one
master dashboard template** and reaches every panel through the same
`/api/clients/{slug}/*` routes as a real client. The only difference is where
the data comes from: the shared `_cached_bq_read` helper in
[`dashboard/routes/api_routes.py`](../railway/app/dashboard/routes/api_routes.py)
**short-circuits to synthetic data** for the demo slug instead of querying
BigQuery. That's the whole mechanism — one hook, no parallel dashboard, no
seeded warehouse.

## Why it stays aligned with future changes

Because the demo renders through the master template and the real API routes:

- **Any UI change, new nav item, or restyle applies to the demo automatically** —
  there is no separate demo template to keep in sync.
- **New panels that reuse `_cached_bq_read` are wired in for free.** The only
  thing a new panel ever needs is a matching sample in
  [`demo_data.py`](../railway/app/demo_data.py). Until that sample is added, the
  panel **degrades gracefully to an empty-but-valid response** — it never errors.

To add a sample for a new panel: find the cache source string the endpoint
passes (e.g. `f"{normalized}.pages.new_thing"`), and add a builder under that
suffix key (`pages.new_thing`) to the `_BUILDERS` map in `demo_data.py`, shaped
like the real `fetch_*` it stands in for.

## Files

| File | Responsibility |
|---|---|
| [`railway/app/demo_client.py`](../railway/app/demo_client.py) | Demo identity (slug, label, budget), `is_demo()`, and idempotent startup seeding. |
| [`railway/app/demo_data.py`](../railway/app/demo_data.py) | Deterministic synthetic data for every panel, keyed by cache-source suffix, with a safe default for unknown keys. |
| `railway/app/dashboard/routes/api_routes.py` | `_cached_bq_read` short-circuit + `_load_bq_test_config` bypass for the demo slug. |
| `railway/app/main.py` | Calls `demo_client.seed_demo_client()` on startup. |
| [`railway/app/tests/test_demo_client.py`](../railway/app/tests/test_demo_client.py) | Determinism, shape, and safe-default tests. |

## Configuration

All optional — the demo works out of the box.

| Env var | Default | Purpose |
|---|---|---|
| `DEMO_CLIENT_ENABLED` | `1` | Set to `0` to keep the demo out of a deployment entirely. |
| `DEMO_CLIENT_SLUG` | `demo` | Override the slug (only if a real client legitimately needs `demo`). |
| `DEMO_CLIENT_LABEL` | `Northwind Health (Demo)` | Display name in the picker/topbar. |
| `DEMO_CLIENT_LOGIN_EMAIL` | _(unset)_ | Create a dedicated read-only client login for the demo. |
| `DEMO_CLIENT_LOGIN_PASSWORD` | _(unset)_ | Password for that login (≥ 10 chars). |

### Logging in

- **Admins** can open the demo from the dashboards picker with no extra setup.
- To hand a **dedicated, shareable client login** to a prospect or trainee, set
  `DEMO_CLIENT_LOGIN_EMAIL` and `DEMO_CLIENT_LOGIN_PASSWORD` (≥ 10 chars). The
  login is created on the next startup as a read-only `client` account scoped to
  the demo dashboard only. Credentials are **never** hardcoded — a login exists
  only when you set those two variables.

## Guarantees

- **No real data.** The demo never queries BigQuery, connectors, or any client
  account. It cannot leak a real client's numbers.
- **Read-only.** The demo login is a `client` role scoped to the demo slug.
- **Idempotent & non-fatal.** Seeding runs every boot, only creates what's
  missing, and never crashes startup if it fails.
