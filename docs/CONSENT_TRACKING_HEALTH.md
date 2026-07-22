# Consent & Tracking Health

A production monitoring feature that scans a client's website in a **fresh,
automated browser session** and reports exactly what tracks a visitor, *when*,
and *whether it should* — before consent, after **Reject All**, and after
**Accept All**.

It is built for two audiences at once: a marketer sees a clear health verdict,
an executive summary, and plain-English recommendations; a technical user can
drill into every script, request, cookie, and storage entry captured in each
consent state.

---

## What makes it different

Naïve cookie scanners flag anything that loads. This one draws the three
distinctions that actually determine GDPR/ePrivacy compliance:

| Observation | Is it a violation? |
|---|---|
| A script is **downloaded** (e.g. `gtag.js`, a GTM container) | **No** — loading a tag is not tracking. What matters is whether it *fires* before consent. |
| A **request is transmitted** to a tracking endpoint | **Depends** — a Google Consent Mode *cookieless ping* (`gcs=G100`, no client id) is measurement without tracking and is **allowed**. An identified beacon is not. |
| An **identifier is stored or transmitted** (`_ga`, `_fbp`, a UUID, a client id in a beacon) for a consent-required category, before consent or after Reject All | **Yes** — this is the real breach. |

Every finding is judged against **configurable consent expectations**, so a
Consent Mode cookieless ping is never mislabelled as a leak, and a client can
declare which categories require opt-in for their jurisdiction.

---

## Where to find it

The report lives at `/dashboard/{client_slug}/consent`; the page itself handles
first-time setup. It's **hidden from the client sidebar by default** — most
clients don't need the scanner in their nav, and it only adds confusion. An
admin turns it on per client from **Settings → Consent health → "Show on client
sidebar"**; while it's off, admins still reach the page from the same Settings
section ("Open Consent Health"). The toggle is persisted per client
(`client_dashboard_config.consent_sidebar_enabled`).

---

## Architecture

Pure, testable layers with I/O pushed to the edges (mirrors the
`connector_config_store` + service split used elsewhere in the app):

```
consent_knowledge.py    Vendor & category knowledge base, identifier heuristics,
                        Google Consent Mode (gcs) parsing.        [pure]
consent_classifier.py   Raw browser captures -> typed Findings
                        (script_loaded / tracking_beacon / cookieless_ping /
                         identifier_stored / identifier_transmitted / ...).  [pure]
consent_evaluator.py    Findings + expectations -> severity + verdict +
                        health rollup, per phase.                  [pure]
consent_scanner.py      Playwright Chromium driver: 3 phases in fresh
                        contexts; captures scripts, requests, cookies,
                        storage, consent signals. Degrades gracefully.
consent_store.py        Postgres persistence (configs + runs, JSONB).
consent_service.py      Orchestration: scan -> classify -> evaluate -> diff vs
                        previous -> build the view model -> persist.
dashboard/renderers/consent_renderer.py   Premium server-rendered UI.
dashboard/routes/consent_routes.py        Page, JSON API, scan, config, cron.
```

### Scan flow

For every configured page, the scanner opens **three independent browser
contexts** (empty cookie jar + storage each time) so the states never
contaminate one another:

1. **pre_consent** — load the page, interact with nothing.
2. **reject_all** — load, click the CMP's *Reject All* control, let tags settle.
3. **accept_all** — load, click *Accept All*, let tags settle.

The scanner finds consent controls via a library of known CMP selectors
(OneTrust, Cookiebot, Usercentrics, Termly, CookieYes, Complianz, …) plus an
accessible-text fallback (“Reject All”, “Accept”, …). Per page/phase it records
scripts downloaded, every network request (method, resource type, body snippet),
cookies, `localStorage`/`sessionStorage`, and consent signals (TCF `__tcfapi`,
GPP, Google Consent Mode defaults, and whether a banner was actually shown).

### Sites with no consent banner

If a page has **no CMP at all**, the reject/accept phases cannot actuate a
control (`interaction.clicked` is `False`) — they simply re-run the pre-consent
load. The evaluator therefore treats a reject phase whose control was never
clicked as *inventory, not a distinct rejected state*: its findings are recorded
but are **not** counted as violations and are **not** phrased as “after the
visitor clicked Reject All” (there was no Reject All to click). This prevents the
pre-consent leak being double-counted and stops the report inventing an opt-out
the site never offered. When no page shows a banner and none offers a reject
control, the run is flagged `no_cmp` and the verdict leads with the root cause —
“No consent banner was detected, so nothing is gated” — rather than a per-tag
list. A site whose CMP *is* present but ignores Reject All (the control was
clicked and tracking continued) is still flagged as a genuine reject violation,
exactly as before.

### Data model (Postgres)

- **`consent_scan_configs`** — one row per client: `pages` (JSONB), consent
  `expectations` (JSONB), `scan_options`, `scan_enabled`, `scan_frequency`.
- **`consent_scan_runs`** — one row per scan: `status`, `health`, headline
  counts, `summary` (JSONB) and the full `result` (JSONB) the UI renders.

Schema is created at startup (`consent_store.ensure_schema()`), and scans left
`running` by a redeploy are closed out (`fail_orphaned_runs`), exactly like
connector sync runs.

---

## Consent expectations (configurable)

Defaults encode a GDPR/ePrivacy posture; edit per client in the **Settings**
drawer on the page:

| Setting | Default |
|---|---|
| Categories that require opt-in | Advertising, Analytics, Social, Personalization |
| Google Consent Mode cookieless pings before consent | Allowed |
| Consent banner required on every page | Yes |
| After **Reject All** | No non-essential identifiers permitted |

Strictly-necessary cookies and the consent banner itself are always allowed.

---

## The report

The page is deliberately layered so a non-technical team member gets the answer
first and detail-lovers can still drill all the way down. Everything above the
fold answers one question — *is consent set up correctly?* — and every jargon
term (cookieless pings, trackers before consent, the three consent states, …)
carries a hover/focus **ⓘ tooltip** that explains what it is and why the number
matters.

**Above the fold (always visible):**

- **Verdict hero** — Healthy / Needs attention / Critical issues, headed by the
  literal question "Is consent set up correctly?", with a one-line headline.
- **Key numbers** — the six figures that matter, each with a tooltip: trackers
  before consent, issues before consent, issues after Reject All, cookieless
  pings, third-party vendors, banner coverage. (These used to be duplicated
  between the hero chips and a separate executive-summary row; now they live in
  one place.)
- **Changes since last scan** — new vs resolved violations, vendor add/remove,
  health delta.
- **What to fix** — ranked violations with a plain-English reason and a concrete
  recommendation.
- **Consent states compared** — before / Reject / Accept side by side.

**Technical detail (one collapsed disclosure, all detail preserved):**

- **Vendors & requests** — full inventory, category-filterable, with evidence
  types and an identifier flag.
- **Page-by-page detail** — drill into every finding per phase.
- **What we checked against** — the expectations + methodology.
- **Scan history** — health over recent scans.

---

## Manual & scheduled scans

- **Manual:** the **Run scan** button (`POST …/consent/scan`) starts a scan as a
  background task; the page polls `…/consent/status` and refreshes when done.
- **Scheduled:** enable scheduling per client (daily / weekly / monthly). A cron
  tick hits `POST /internal/consent/scan-due` (guarded by `X-Cron-Secret`),
  which scans every client whose cadence is due — hands-off, like
  `/internal/sync-bq-all`, with a Postgres lock so runs never stack.

  The existing Railway cron worker (`railway/cron-sync-bq`) supports this: create
  a second cron service with the same root directory, set `CRON_JOB=consent-scan-due`
  and a schedule (e.g. weekly), and share `CRON_SECRET` with the main API.

---

## Deployment notes (Playwright)

The scanner uses Playwright's Chromium. `playwright` is in
`railway/app/requirements.txt`, and the browser ships **inside the deploy image**:

- **`railway/app/Dockerfile`** is based on `mcr.microsoft.com/playwright/python`,
  which comes with Chromium **and** all its OS libraries pre-installed at
  `/ms-playwright` (with `PLAYWRIGHT_BROWSERS_PATH` already set). This replaced the
  old `nixpacks.toml`, which ran `playwright install --with-deps chromium` on every
  build — an apt install of ~30-40 packages plus a ~150 MB browser download that
  pushed deploys past 10 minutes. With the browser baked into the cached base
  image, per-deploy work is just `pip install`, so builds are dramatically faster.
  - The image tag is pinned to the same Playwright version as `requirements.txt`
    (`v1.61.0`), so the bundled browser always matches the client library — **bump
    both together**.
  - `CONSENT_SCANNER_NO_SANDBOX=1` is set in the Dockerfile — the container runs as
    root, where Chromium refuses to start its sandbox; the scanner passes
    `--no-sandbox` when this is set (it also auto-detects root as a fallback).
  - The scanner still auto-detects the browser across `PLAYWRIGHT_BROWSERS_PATH`,
    `/opt/pw-browsers`, and the per-user cache, tolerating the several on-disk
    layouts Playwright has used — so it also self-heals if the browser is missing.
- **Other hosts:** point `CONSENT_SCANNER_CHROMIUM_PATH` at an existing Chromium,
  or run `python -m playwright install chromium` yourself.
- **If unavailable:** the scanner returns `available=false` and the scan is
  recorded as *failed* with a clear message — it never crashes the app.

### Environment variables (all optional)

| Var | Purpose |
|---|---|
| `CONSENT_SCANNER_CHROMIUM_PATH` | Explicit Chromium executable path. |
| `CONSENT_SCANNER_PROXY` | Proxy server for the browser (e.g. an egress proxy). |
| `CONSENT_SCANNER_NO_SANDBOX` | `1` to pass `--no-sandbox` (root/containers). |
| `CONSENT_SCANNER_IGNORE_HTTPS` | `1` to ignore TLS errors (re-terminating proxy). |
| `CONSENT_SCANNER_SETTLE_MS` | Wait for late beacons after load (default 3500). |
| `CONSENT_SCANNER_TIMEOUT_MS` | Per-navigation timeout (default 30000). |
| `CONSENT_ALLOW_PRIVATE_HOSTS` | `1` to permit private/loopback scan targets (staging, local fixtures). Off by default as an SSRF guard. |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/dashboard/{slug}/consent` | Report page (HTML). |
| `GET` | `/dashboard/{slug}/consent.json` | Full overview (config + latest report + history). |
| `GET` | `/dashboard/{slug}/consent/status` | Latest run status (polling). |
| `POST` | `/dashboard/{slug}/consent/scan` | Start a manual scan (background). |
| `POST` | `/dashboard/{slug}/consent/config` | Save config; `run_now:true` also scans. Admin-only. |
| `POST` | `/internal/consent/scan-due` | Cron: scan all due clients. `X-Cron-Secret`. |

---

## Tests & fixtures

- `tests/test_consent_classification.py` — knowledge base, classifier, evaluator
  (host-matching boundaries, Consent Mode parsing, the three evidence
  distinctions, per-phase verdicts).
- `tests/test_consent_service.py` — view-model assembly, health rollup,
  change-tracking diff, SSRF URL guard.
- `tests/test_consent_store.py` — persistence round-trip (runs only with
  `DATABASE_URL`).
- `tests/fixtures/consent_site/` — a self-contained demo site with Consent Mode,
  a consent banner, GA4, GTM, Meta Pixel, LinkedIn and HubSpot. It deliberately
  includes a realistic **pre-consent Meta Pixel leak** so the scanner can be run
  against it end to end and produce a real, non-mock report.

Run the pure suite:

```bash
cd railway/app
python -m unittest tests.test_consent_classification tests.test_consent_service
```
