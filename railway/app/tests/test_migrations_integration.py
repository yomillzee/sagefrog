"""Real-Postgres checks for the migration runner (db_migrate).

Runs only when DATABASE_URL is set — CI provides a throwaway Postgres; skipped
otherwise so the pure suite stays hermetic. Uses only synthetic data it creates
and cleans up; never production data.

This is the automated version of the manual "fresh DB + already-initialized DB"
verification done by hand for #299/#301: it proves the runner is a non-destructive
no-op on re-run, which is the gate for every remaining module's migration.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_SENTINEL = "ci-migration-sentinel@example.com"


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires a Postgres DATABASE_URL")
class MigrationRunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        import db
        import db_migrate
        import web_users  # noqa: F401  (import registers web_users:0001_baseline)

        self.db = db
        self.db_migrate = db_migrate
        self._cleanup_sentinel()

    def tearDown(self):
        self._cleanup_sentinel()

    def _cleanup_sentinel(self):
        try:
            with self.db.connection() as conn:
                conn.execute("DELETE FROM web_users WHERE email = %s", (_SENTINEL,))
        except Exception:
            pass  # table may not exist yet on a truly fresh DB

    def _baseline_applied_at(self):
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT applied_at FROM schema_migrations WHERE id = %s",
                ("web_users:0001_baseline",),
            ).fetchone()
        return row[0] if row else None

    def test_fresh_apply_is_recorded(self):
        self.assertTrue(self.db_migrate.run_migrations())
        self.assertIsNotNone(
            self._baseline_applied_at(), "baseline not recorded after run"
        )
        # Exactly one baseline row.
        with self.db.connection() as conn:
            n = conn.execute(
                "SELECT count(*) FROM schema_migrations WHERE id = %s",
                ("web_users:0001_baseline",),
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_rerun_is_idempotent_and_non_destructive(self):
        self.db_migrate.run_migrations()
        first_applied_at = self._baseline_applied_at()
        self.assertIsNotNone(first_applied_at)

        # Insert a synthetic row; a re-run must not touch it.
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO web_users (email, password_hash, role) VALUES (%s, %s, %s)",
                (_SENTINEL, "not-a-real-hash", "admin"),
            )

        # Re-run the runner.
        self.db_migrate.run_migrations()

        # applied_at unchanged (migration not re-applied) ...
        self.assertEqual(
            first_applied_at,
            self._baseline_applied_at(),
            "baseline applied_at changed on re-run — migration re-applied",
        )
        # ... and the synthetic row survives (non-destructive).
        with self.db.connection() as conn:
            n = conn.execute(
                "SELECT count(*) FROM web_users WHERE email = %s", (_SENTINEL,)
            ).fetchone()[0]
        self.assertEqual(n, 1, "synthetic row lost after re-running migrations")


if __name__ == "__main__":
    unittest.main()
