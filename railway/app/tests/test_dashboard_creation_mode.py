from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import dashboard_registry  # noqa: E402
from dashboard_registry import DashboardClientRow  # noqa: E402


class _FakeConn:
    def execute(self, sql, params=()):
        return self


class DashboardCreationModeTests(unittest.TestCase):
    """A new dashboard must be created on dashboard_mode="bigquery_nixon"
    (the connector-based template), never "bigquery" or the unset "api"
    default."""

    def test_create_client_sets_bigquery_nixon_mode(self) -> None:
        captured = {}

        fake_cdc = types.SimpleNamespace(
            save_config=lambda slug, **kw: captured.update(kw),
        )

        @contextmanager
        def fake_connection():
            yield _FakeConn()

        created_row = DashboardClientRow(
            client_slug="acme", label="Acme Co", source="admin",
            created_at="2026-07-03T00:00:00", created_by="admin",
        )

        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry, "get_client", side_effect=[None, created_row]), \
             patch.object(dashboard_registry.db, "connection", fake_connection), \
             patch.dict(sys.modules, {"client_dashboard_config": fake_cdc}):
            dashboard_registry.create_client(client_slug="acme", label="Acme Co", created_by="admin")

        self.assertEqual(captured.get("dashboard_mode"), "bigquery_nixon")


if __name__ == "__main__":
    unittest.main()
