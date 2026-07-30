"""Renaming a dashboard changes only its display label, never its slug.

The slug (e.g. "nixon-bq-test") is the immutable internal key wired into routes,
BigQuery routing, OAuth token storage, and config lookups. Renaming must touch
only the human-facing label — in the registry row and, when present, the
client_dashboard_config row that client_label()/the header read.
"""

from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import dashboard_registry  # noqa: E402
import main  # noqa: E402


class RenameClientRegistryTests(unittest.TestCase):
    def test_rename_updates_label_keeps_slug_and_syncs_cdc(self) -> None:
        executed: list[tuple] = []
        conn = MagicMock()
        conn.execute.side_effect = lambda *a, **k: executed.append(a)

        @contextmanager
        def fake_connection():
            yield conn

        existing = dashboard_registry.DashboardClientRow(
            client_slug="nixon-bq-test", label="nixon-bq-test", source="builtin"
        )
        renamed = dashboard_registry.DashboardClientRow(
            client_slug="nixon-bq-test", label="Nixon Medical", source="builtin"
        )
        import client_dashboard_config as cdc

        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry, "get_client", side_effect=[existing, renamed]), \
             patch.object(dashboard_registry.db, "connection", fake_connection), \
             patch.object(cdc, "set_label", return_value=True) as set_label:
            row = dashboard_registry.rename_client(
                client_slug="Nixon-BQ-Test", label="  Nixon Medical  ",
                updated_by="admin@x.com",
            )

        self.assertEqual(row.client_slug, "nixon-bq-test")  # slug unchanged
        self.assertEqual(row.label, "Nixon Medical")
        # The registry UPDATE keys on the slug and sets the trimmed label.
        self.assertTrue(
            any("UPDATE dashboard_clients" in a[0] and a[1] == ("Nixon Medical", "nixon-bq-test")
                for a in executed),
            executed,
        )
        # The config-row label is kept in step (same slug, trimmed label).
        set_label.assert_called_once_with(
            "nixon-bq-test", "Nixon Medical", updated_by="admin@x.com"
        )

    def test_rename_unknown_slug_raises(self) -> None:
        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry, "get_client", return_value=None):
            with self.assertRaises(ValueError):
                dashboard_registry.rename_client(
                    client_slug="ghost", label="Ghost Co", updated_by="admin@x.com"
                )

    def test_rename_blank_label_raises(self) -> None:
        with patch.object(dashboard_registry, "enabled", return_value=True):
            with self.assertRaises(ValueError):
                dashboard_registry.rename_client(
                    client_slug="nixon-bq-test", label="   ", updated_by="admin@x.com"
                )


class AdminRenameRouteTests(unittest.TestCase):
    def test_route_renames_and_redirects(self) -> None:
        renamed = dashboard_registry.DashboardClientRow(
            client_slug="nixon-bq-test", label="Nixon Medical", source="builtin"
        )
        with patch.object(main.dashboard_registry, "rename_client", return_value=renamed) as rc, \
             patch.object(main.audit_log, "request_context", return_value={}), \
             patch.object(main.audit_log, "record"):
            resp = main.admin_rename_dashboard(
                client_slug="nixon-bq-test", request=types.SimpleNamespace(),
                label="Nixon Medical",
                user=types.SimpleNamespace(email="admin@x.com", role="admin"),
            )

        self.assertEqual(resp.status_code, 303)
        self.assertIn("/admin/clients", resp.headers["location"])
        self.assertIn("Nixon%20Medical", resp.headers["location"])
        rc.assert_called_once_with(
            client_slug="nixon-bq-test", label="Nixon Medical", updated_by="admin@x.com"
        )

    def test_route_validation_error_returns_400(self) -> None:
        with patch.object(main.dashboard_registry, "rename_client",
                          side_effect=ValueError("Dashboard name is required.")), \
             patch.object(main.audit_log, "request_context", return_value={}), \
             patch.object(main.web_users, "list_users", return_value=[]), \
             patch.object(main.audit_log, "list_recent", return_value=[]), \
             patch.object(main.web_auth, "render_admin_page", return_value="<html></html>"):
            resp = main.admin_rename_dashboard(
                client_slug="nixon-bq-test", request=types.SimpleNamespace(),
                label="", user=types.SimpleNamespace(email="admin@x.com", role="admin"),
            )

        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
