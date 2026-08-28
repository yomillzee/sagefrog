"""Schema teardown for features that have been removed from the portal.

When a feature is deleted, its Python modules go with it — but its Postgres
tables and columns do not. They linger: they show up in schema dumps, in the
staging-copy lists, and in the next person's search for "what is this table
for?", long after the code that explained them is gone. This module is where
that cleanup lives, as ordered migrations on the central runner
(:mod:`db_migrate`), so the drop happens exactly once per database, at startup,
under the one shared schema advisory lock — never from a request path.

These migrations are deliberately **destructive**, which is the one place this
file departs from the rest of the runner's contract: elsewhere a migration
preserves data, here the whole point is to remove it. Two properties keep that
safe:

  * Every statement is ``DROP ... IF EXISTS``, so it is idempotent and a database
    that never had the feature (a fresh one, or staging seeded without it) is
    unaffected rather than erroring.
  * The ledger records each migration, so a drop runs once ever — a redeploy
    cannot re-drop a table a later feature happens to reuse the name of.

Adding a teardown: append a new numbered migration; never edit a shipped one
(its checksum is recorded, and databases that already applied it will not
re-run it).
"""

from __future__ import annotations

import db_migrate

# ── consent & tracking health (removed 2026-08-28) ──────────────────────────
# The scanner, its dashboard page, the per-client scan config and the sidebar
# opt-in are all gone from the code. This drops what they left in Postgres: the
# two scan tables (``idx_consent_runs_client`` goes with its table) and the
# client_dashboard_config flag that decided whether the tab appeared. Runs are
# dropped before configs so the teardown reads in dependency order even though
# no foreign key ties them.
_CONSENT_HEALTH_TEARDOWN: tuple[str, ...] = (
    "DROP TABLE IF EXISTS consent_scan_runs",
    "DROP TABLE IF EXISTS consent_scan_configs",
    """
    ALTER TABLE IF EXISTS client_dashboard_config
      DROP COLUMN IF EXISTS consent_sidebar_enabled
    """,
)

MIGRATIONS: list[db_migrate.Migration] = [
    db_migrate.Migration(
        id="retired:0001_consent_health",
        statements=_CONSENT_HEALTH_TEARDOWN,
    ),
]

db_migrate.register(MIGRATIONS)
