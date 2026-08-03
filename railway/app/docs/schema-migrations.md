# Centralized, versioned schema migrations — inventory & design

Status: **proposal** (design only; no runtime change in this PR)

## Why

Every Postgres-backed store in `railway/app` owns its schema through an
`ensure_schema()` function that runs a list of idempotent DDL statements
(`CREATE TABLE IF NOT EXISTS`, `CREATE/DROP INDEX`, `ALTER TABLE ADD/DROP
CONSTRAINT`, `ADD COLUMN IF NOT EXISTS`, …). Those functions are then called at
the **top of nearly every read and write function** as a safety net.

That pattern just caused a production incident: `web_users.ensure_schema()` runs
on the hot path of every authenticated request, its DDL takes an
`AccessExclusiveLock` on `web_users` held until the surrounding transaction
commits, and under concurrency two of these deadlocked
(`psycopg.errors.DeadlockDetected` → HTTP 500). Fixed in #297 by guarding that
one function to run at most once per process behind an advisory lock.

#297 patched the symptom for one module. **20 modules** have the same shape: they
run schema DDL from **142 ordinary read/write call sites**, plus **13** startup
calls in `main.py` (163 `ensure_schema(` references in all). This doc inventories
them and proposes one centralized, versioned migration process so that no
schema-changing DDL ever runs from a normal read/write function again.

## Inventory

All 20 owners are Postgres and go through the single `db.connection()` helper
(`db.py`). "Call sites in file" counts the `ensure_schema()` invocations **within
each owning file** — the read/write-path calls to remove (142 total); each module
also owns one `def` and, at startup, the 13 marked below are invoked from
`main.py`.

| Module | ensure_schema call sites in file | Run at startup (`main.py`)? | Notes |
|---|---:|:--:|---|
| `client_dashboard_config.py` | 25 | yes | heaviest caller |
| `web_users.py` | 18 | yes | guarded in #297; still calls per read/write |
| `consent_store.py` | 14 | yes | |
| `client_insight_documents.py` | 12 | yes | |
| `connector_config_store.py` | 12 | yes | |
| `feature_requests.py` | 8 | **no** | lazy-only |
| `dashboard_registry.py` | 7 | yes | also **seeds data** (`seed_defaults=True`) |
| `client_notes.py` | 6 | **no** | lazy-only |
| `client_registry_store.py` | 6 | **no** | lazy-only; `-> None` |
| `warehouse.py` | 6 | yes | |
| `admin_dev_notes.py` | 5 | **no** | lazy-only |
| `a11y_store.py` | 4 | **no** | lazy-only |
| `dashboard_snapshots.py` | 4 | yes | |
| `oauth_store.py` | 4 | yes | |
| `login_rate_limit.py` | 3 | yes | |
| `app_settings.py` | 2 | **no** | lazy-only |
| `audit_log.py` | 2 | yes | |
| `business_line_rules.py` | 2 | yes | |
| `cron_locks.py` | 1 | yes | |
| `db_cache.py` | 1 | yes | |

Two things to call out:

- **7 modules never initialize at startup** and rely *entirely* on lazy per-call
  `ensure_schema()`: `feature_requests`, `client_notes`, `client_registry_store`,
  `admin_dev_notes`, `a11y_store`, `app_settings`, `cron_locks`. These are the
  most exposed to the #297 failure mode — the first concurrent access to the
  feature does DDL under request load.
- **Signatures vary**: most are `ensure_schema() -> bool`; `client_registry_store`
  returns `None`; `dashboard_registry.ensure_schema(*, seed_defaults=True)` mixes
  **data seeding** into schema setup. The design must separate DDL (versioned,
  forward-only) from idempotent **data seeds/backfills** (a different concern that
  legitimately may run at startup).

There is currently **no** migration/versioning infrastructure (no Alembic, no
`schema_migrations` table); ordering is implicit in each module's statement list
and in the hand-maintained call order in `main.py`.

## Goals / non-goals

**Goals**
1. Schema DDL runs from exactly one place, once per deploy, before traffic.
2. Versioned and forward-only: each change is a numbered migration applied at
   most once and recorded.
3. Safe under multiple replicas/workers (no concurrent DDL, no deadlocks).
4. No schema DDL reachable from read/write request paths.
5. Incremental adoption — never a 20-module big-bang.

**Non-goals**
- Replacing the query layer or adopting an ORM.
- Changing the runtime DB (still Postgres via `db.connection()`).
- Rewriting idempotent data seeds/backfills (kept separate; may run at startup).

## Design

### 1. A version ledger

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  id          TEXT PRIMARY KEY,          -- "web_users:0003_add_last_seen"
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  checksum    TEXT                        -- of the migration body, for drift detection
);
```

### 2. A migration unit

Each migration is an ordered, namespaced step contributed by a domain module:

```python
@dataclass(frozen=True)
class Migration:
    id: str                       # "<module>:<NNNN>_<slug>", globally unique, sorts in order
    statements: tuple[str, ...]   # forward-only DDL; runs in one transaction
    # optional Python hook for data-shaped steps that can't be pure SQL:
    run: Callable[[psycopg.Connection], None] | None = None
```

Domain modules stop owning an `ensure_schema()`; they own a `MIGRATIONS: list[Migration]`
and register it once at import:

```python
# web_users.py
MIGRATIONS = [
    Migration("web_users:0001_baseline", (CREATE_WEB_USERS, CREATE_CLIENT_GROUPS, ...)),
    Migration("web_users:0002_add_last_seen", ("ALTER TABLE web_users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ",)),
]
db_migrate.register(MIGRATIONS)
```

### 3. One central runner (`db_migrate.py`)

```python
def run_migrations() -> None:
    """Apply all unapplied migrations. Call once at startup; never from a request."""
    if not db.get_db_url():
        return
    with db.connection() as conn:
        # Serialize across replicas/workers; released on commit/rollback.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATIONS_LOCK_KEY,))
        conn.execute(_CREATE_LEDGER_SQL)
        applied = {r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()}
        for m in _ordered_registry():           # sorted by id
            if m.id in applied:
                continue
            for stmt in m.statements:
                conn.execute(stmt)
            if m.run:
                m.run(conn)
            conn.execute(
                "INSERT INTO schema_migrations (id, checksum) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (m.id, _checksum(m)),
            )
```

Properties:
- **Advisory lock** ⇒ only one process migrates; the rest wait, then find
  everything applied. No `AccessExclusiveLock` deadlock.
- **Ledger check** ⇒ each migration's DDL runs at most once, ever — not once per
  process, not once per request.
- Runs inside `main.py` startup, replacing the 13 scattered `*.ensure_schema()`
  calls with a single `db_migrate.run_migrations()`.

### 4. Read/write paths

Delete the `ensure_schema()` call from every query function. After startup the
schema is guaranteed present. A CI guard (below) keeps them from coming back.

### 5. Data seeds / backfills stay separate

`dashboard_registry._seed_defaults`, the `standard`-user access backfill, demo
client seeding, etc. are **not** schema DDL. They remain idempotent startup steps
(or move behind the same ledger as `data:` entries if we want them recorded), but
they never gate read/write calls either.

## Rollout plan (incremental, each a small PR)

- **Phase 0 — infra (this design):** land `db_migrate.py` + `schema_migrations`
  ledger, inert. No behavior change.
- **Phase 1 — adopt baselines:** for each module, wrap its current DDL list as a
  `:0001_baseline` migration and register it. `run_migrations()` is added to
  startup *alongside* the existing `ensure_schema()` calls. Because the existing
  DDL is all `IF NOT EXISTS`, a baseline is safe to run once on already-migrated
  production DBs, then recorded. Verify parity, then remove the module's startup
  `ensure_schema()` call.
- **Phase 2 — de-fang read/write:** module by module, delete the per-call
  `ensure_schema()` invocations (163 → 0). Each module is independent and small;
  `web_users` first (already guarded), then the 7 lazy-only modules (highest risk).
- **Phase 3 — enforce:** new schema changes may only be added as new numbered
  migrations. Add a CI check that fails if DDL keywords (`CREATE TABLE`,
  `ALTER TABLE`, `CREATE INDEX`, `DROP INDEX`, `ADD CONSTRAINT`) appear outside
  `*/migrations/*` or a `MIGRATIONS` list, and that `ensure_schema` is gone.

## Risks & mitigations

- **Already-migrated prod DBs.** Baselines are the current idempotent DDL, so
  applying then recording them is a no-op on existing databases.
- **Multi-replica cold start.** The transaction-scoped advisory lock serializes
  the run; this is the same mechanism #297 introduced, proven under a concurrency
  harness.
- **Ordering across modules.** Migration ids are `<module>:<NNNN>_<slug>` and the
  runner sorts globally; cross-module dependencies (e.g. `web_users.group_id`
  FK → `client_groups`) are handled by ordering within the owning module's
  baseline, exactly as the current statement list already does.
- **Checksum drift.** Editing an applied migration's body changes its checksum;
  the runner can warn (or fail in CI) so history stays append-only.

## Appendix: how the inventory was produced

```
# 20 owner modules
grep -rln "def ensure_schema" --include=*.py railway/app

# 163 total ensure_schema( references (excluding the defs) =
#   142 in-owner read/write call sites + 13 main.py startup calls + 8 comment/doc mentions
grep -rn "ensure_schema(" --include=*.py railway/app | grep -v "def ensure_schema" | wc -l

# 13 startup owners
grep -cE "\.ensure_schema\(" railway/app/main.py

# per-owner read/write call sites (exclude the def line and comment prose)
grep -nE "ensure_schema\(" railway/app/<owner>.py | grep -v "def ensure_schema" | grep -vE ":\s*#"
```
