"""Tests for the removed-feature schema teardown (retired_features).

These migrations are the one place in the runner that deliberately destroys
data, so the properties that make that safe are worth pinning down: every
statement is guarded with IF EXISTS (a database that never had the feature must
be unaffected, not error), the teardown is registered with the central runner so
the ledger applies it once ever, and it targets exactly the objects the removed
feature owned.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db_migrate  # noqa: E402
import retired_features  # noqa: E402


def _statements(migration_id: str) -> tuple[str, ...]:
    for m in db_migrate.registered():
        if m.id == migration_id:
            return m.statements
    raise AssertionError(f"{migration_id} is not registered with db_migrate")


class RegistrationTests(unittest.TestCase):
    def test_teardown_is_registered_with_the_central_runner(self) -> None:
        # Registration is what gets it applied-and-recorded once at startup; a
        # teardown that only sits in MIGRATIONS never runs.
        registered = {m.id for m in db_migrate.registered()}
        for m in retired_features.MIGRATIONS:
            self.assertIn(m.id, registered, f"{m.id} not registered")

    def test_ids_are_namespaced_and_numbered(self) -> None:
        # Ids sort in apply order, so the runner's global sort keeps teardowns in
        # the sequence they were added.
        for m in retired_features.MIGRATIONS:
            self.assertRegex(m.id, r"^retired:\d{4}_[a-z0-9_]+$")


class ConsentHealthTeardownTests(unittest.TestCase):
    MIGRATION_ID = "retired:0001_consent_health"

    def test_every_statement_is_guarded_with_if_exists(self) -> None:
        # A database that never had the feature (fresh, or staging seeded without
        # it) must be a no-op rather than a failed startup.
        for stmt in _statements(self.MIGRATION_ID):
            self.assertIn("IF EXISTS", stmt.upper(), f"unguarded teardown: {stmt!r}")

    def test_drops_both_consent_tables_and_the_sidebar_column(self) -> None:
        joined = " ".join(" ".join(s.split()) for s in _statements(self.MIGRATION_ID))
        self.assertIn("DROP TABLE IF EXISTS consent_scan_runs", joined)
        self.assertIn("DROP TABLE IF EXISTS consent_scan_configs", joined)
        self.assertIn("DROP COLUMN IF EXISTS consent_sidebar_enabled", joined)

    def test_touches_nothing_but_the_removed_feature(self) -> None:
        # A teardown that drops a live table is the worst failure mode here, so
        # the only table this migration may name is a consent scan table, and the
        # only table it may ALTER is the shared client config.
        for stmt in _statements(self.MIGRATION_ID):
            flat = " ".join(stmt.split())
            if flat.upper().startswith("DROP TABLE"):
                self.assertRegex(flat, r"consent_scan_(runs|configs)$")
            else:
                self.assertTrue(
                    flat.upper().startswith("ALTER TABLE IF EXISTS CLIENT_DASHBOARD_CONFIG"),
                    f"unexpected teardown statement: {flat!r}",
                )


class RemovedCodeTests(unittest.TestCase):
    def test_consent_modules_are_gone(self) -> None:
        # The teardown only makes sense once the code is actually removed; a
        # module resurrected without dropping the migration would fight it.
        for name in ("consent_store", "consent_service", "consent_scanner",
                     "consent_classifier", "consent_evaluator", "consent_knowledge"):
            self.assertFalse((APP_DIR / f"{name}.py").exists(), f"{name}.py still present")

    def test_client_config_no_longer_carries_the_sidebar_flag(self) -> None:
        import client_dashboard_config
        self.assertFalse(
            hasattr(client_dashboard_config.ClientConfigRow, "consent_sidebar_enabled"),
            "consent_sidebar_enabled is still on the config row",
        )
        self.assertFalse(
            hasattr(client_dashboard_config, "save_consent_sidebar_enabled"),
            "save_consent_sidebar_enabled is still exported",
        )
        ddl = " ".join(client_dashboard_config.SCHEMA_SQL_STATEMENTS)
        self.assertNotIn("consent_sidebar_enabled", ddl,
                         "schema DDL would re-add the dropped column")


if __name__ == "__main__":
    unittest.main()
