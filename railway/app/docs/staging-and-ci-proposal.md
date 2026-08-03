# Staging environment & CI — proposal

Status: **agreed plan** — this document only. Decisions are recorded below.
Proposal A (CI) is implemented in a **separate follow-up PR** (kept out of this
doc PR on purpose). Proposal B (staging) is deferred per the decision to do CI
first; a cost estimate is included below. No code, workflows, or Railway resources
are part of this PR.

## Why now

The migration effort (`docs/schema-migrations.md`) has 19 modules left to adopt,
each running DDL against the **production** database. Today the guardrails around
that are thin:

- **Prod-only, auto-deploy on merge.** Merges to `main` build via
  `railway/app/Dockerfile` and deploy straight to production
  (`railway.toml`: healthcheck `/health`, restart `on_failure`). There is no
  pre-production environment — every migration's first real run is in prod.
- **No CI.** There is no `.github/workflows`. The 70 test files in
  `railway/app/tests` only run when someone runs them locally, and the
  integration tests that need a live `DATABASE_URL` (e.g. `test_consent_store`)
  **skip** in every environment that lacks one — which is every environment right
  now. We've been hand-verifying deploys from logs.
- **Manual verification.** Each migration deploy so far (#299, #301) was checked
  by hand against prod logs and a manual SQL query. That doesn't scale to 19 more.

The remaining migrations are exactly the kind of change that wants a dress
rehearsal on prod-shaped data and an automated safety net. Now that Railway
backups are confirmed, a staging restore is feasible.

## Goals / non-goals

**Goals**
1. Every migration is applied to a prod-shaped database **before** prod.
2. CI runs the full test suite (including the DB-gated tests) on every PR.
3. Migration idempotency + non-destructiveness is proven automatically on both a
   fresh DB and a restored prod snapshot.
4. The design's Phase 3 guard (no schema DDL from read/write paths) is enforced
   mechanically, not by reviewer vigilance.

**Non-goals (for this proposal)**
- Building any of it. This is a plan; implementation is separate, approved work.
- Changing the prod deploy path or the app runtime.

## Proposal A — CI first (GitHub Actions)

Cheapest, highest-leverage, and needs no Railway resources. Triggers on PRs and
pushes to `main`.

1. **Test job.** Reuse the existing Dockerfile / `requirements.txt`, stand up a
   **Postgres service container**, set `DATABASE_URL` to it, and run `pytest`.
   This turns today's silent skips into real runs and catches regressions before
   merge. (Also resolves the "works locally only after I install deps" problem
   the `.claude` session hook papers over.)
2. **Migration-safety job (the important one).** Against two databases:
   - **Fresh/empty** Postgres → run the migration runner, assert every registered
     migration applies and `schema_migrations` ends in the expected state.
   - **Restored prod snapshot** (sanitized) → run the runner and assert it is a
     **non-destructive no-op**: run twice, diff row counts / table structure
     before-and-after, assert `schema_migrations` timestamps don't change on the
     second run and no table loses rows.
   This is the automated version of the manual "fresh DB + already-initialized
   DB" check we did by hand for #299/#301 — and the gate for every remaining module.
3. **DDL guard (design Phase 3).** A check that fails if schema DDL keywords
   (`CREATE TABLE`, `ALTER TABLE`, `CREATE/DROP INDEX`, `ADD/DROP CONSTRAINT`)
   appear outside a module's migration list, or if `ensure_schema()` is called
   from a read/write path again. Keeps the anti-pattern from regrowing.
4. **Optional: lint/format.** There's no linter config today; introducing `ruff`
   would be nice but is independent of migration safety — split it out.

Then require jobs 1–3 as **branch-protection** checks before merge to `main`.

## Proposal B — Staging environment (Railway)

A second Railway service + Postgres that mirrors prod, deployed from a `staging`
branch (or a Railway "staging" environment on the same repo), same Dockerfile and
`/health` healthcheck.

- **Data.** Seed the staging DB from a **periodic, sanitized restore** of the prod
  backup, so migrations meet real-shaped data (row volumes, pre-existing columns,
  legacy constraint states) before prod does.
- **Promotion flow.**
  `PR → CI green → merge to staging → auto-deploy staging → verify → promote to main → prod.`
  Verification on staging is the checklist we've already been running by hand:
  startup clean, `/health` 200, auth works, **no `DeadlockDetected`**,
  `schema_migrations` stable.
- **Payoff.** Each remaining migration gets a prod-shaped rehearsal; a bad
  migration fails in staging, not prod.

## How this gates the remaining migrations

Once A (and ideally B) exist, each of the 19 modules' Phase 1/Phase 2 PRs must:
1. Pass CI: full tests + fresh-DB migrate + restored-snapshot migrate (idempotent,
   non-destructive) + DDL guard.
2. Deploy to staging and pass the verification checklist.
3. Only then promote to prod.

That replaces the manual, log-reading verification we've done for `web_users`.

## Sequencing & effort

- **Step 1 (small, no cost): CI.** Test job + migration-safety job + DDL guard.
  Mostly a day of workflow authoring; no recurring infra spend beyond GitHub
  Actions minutes (likely within free tier). Biggest single risk reduction.
- **Step 2 (needs resources): staging service + DB + sanitized restore job.**
  Recurring Railway cost for a second service/DB; one-time work on the
  restore/sanitize script.
- **Step 3: branch protection + promotion flow**, once staging is trusted.

I'd recommend doing Step 1 before resuming migrations even if Step 2 waits.

## Costs & tradeoffs

- Railway: one extra service + Postgres for staging (recurring). CI: GitHub
  Actions minutes (low).
- Sanitizing prod data for staging is real work if the DB holds PII (user emails,
  etc.) — needs a scrub step in the restore.
- A `staging` branch adds one promotion hop; worth it for schema changes.

## Decisions (agreed)

1. **CI** — build now. Implemented in a separate follow-up PR (Proposal A).
2. **Staging** — yes, but **after CI**, and with a cost estimate (below). Deferred
   to a follow-up PR.
3. **Production data** — treat as sensitive; **never clone it raw**. CI uses only an
   ephemeral synthetic Postgres. Staging must restore from a **sanitized/scrubbed**
   snapshot (PII removed), never a raw prod copy.
4. **Branch model** — a **persistent `staging` branch/environment**, not per-PR
   environments.
5. **Lint (`ruff`)** — later; out of scope for now.

## Staging cost estimate (rough)

For firming up when Proposal B is built; Railway is usage-based so actuals depend
on the plan and how prod-shaped the staging DB is.

- **App service (staging):** a small always-on container mirroring prod —
  order of **~$5–10/mo** of compute at low traffic.
- **Postgres (staging):** a managed Postgres instance — **~$5–15/mo** depending on
  size and storage; larger if the staging DB mirrors a big prod dataset.
- **Sanitized restore job:** a periodic task (scrub PII, load into staging) —
  negligible compute if run on a schedule; main cost is the one-time work to write
  the scrub/restore script.
- **CI (this PR):** GitHub Actions minutes only — expected within the free tier for
  this repo's volume.

**Ballpark: ~$15–30/mo** for a modest staging environment, plus the one-time
scrub-script work. To be confirmed against the actual Railway plan and chosen
staging DB size before provisioning.

Nothing further is built until Proposal B is scheduled.
