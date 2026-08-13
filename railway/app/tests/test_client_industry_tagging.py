"""Tagging an account with an industry: storage, and how the Accounts page shows it.

The tag is descriptive metadata on the registry row — it changes nothing about
the client's own dashboard, and its only consumer is the agency Benchmarks
rollup. These cover the write path (normalization, clearing) and the admin
surface that sets it.
"""

from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_industries  # noqa: E402
import dashboard_registry  # noqa: E402
import web_auth  # noqa: E402
import web_users  # noqa: E402


def _fake_conn(rowcount: int = 1):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = rowcount
    executed: list[tuple] = []

    def _execute(*args, **kwargs):
        executed.append(args)
        return cursor

    conn.execute.side_effect = _execute

    @contextmanager
    def connection():
        yield conn

    return connection, executed


class SetIndustryTests(unittest.TestCase):
    def test_valid_key_is_stored_against_the_normalized_slug(self):
        connection, executed = _fake_conn()
        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry.db, "connection", connection):
            ok = dashboard_registry.set_industry("Nixon-BQ-Test", "healthcare_life_sciences")

        self.assertTrue(ok)
        self.assertTrue(
            any(
                "UPDATE dashboard_clients" in a[0]
                and a[1] == ("healthcare_life_sciences", "nixon-bq-test")
                for a in executed
            ),
            executed,
        )

    def test_empty_value_clears_the_tag(self):
        connection, executed = _fake_conn()
        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry.db, "connection", connection):
            dashboard_registry.set_industry("nixon", "")

        self.assertTrue(any(a[1] == (None, "nixon") for a in executed), executed)

    def test_unknown_key_clears_rather_than_storing_garbage(self):
        # The taxonomy lives in code; a retired key must not persist as a bucket
        # nothing can render.
        connection, executed = _fake_conn()
        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry.db, "connection", connection):
            dashboard_registry.set_industry("nixon", "widget_making")

        self.assertTrue(any(a[1] == (None, "nixon") for a in executed), executed)

    def test_no_database_is_a_no_op_not_a_crash(self):
        with patch.object(dashboard_registry, "enabled", return_value=False):
            self.assertFalse(dashboard_registry.set_industry("nixon", "other"))

    def test_industry_map_skips_unknown_keys(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ("nixon", "healthcare_life_sciences"),
            ("acme", "retired_bucket"),
            ("beta", None),
        ]

        @contextmanager
        def connection():
            yield conn

        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry.db, "connection", connection):
            out = dashboard_registry.industry_map()

        self.assertEqual(out, {"nixon": "healthcare_life_sciences"})


class AccountsPageTests(unittest.TestCase):
    """The Accounts card grid is where the tag is read and set."""

    def _render(self, rows):
        user = web_users.WebUser(
            id=1, email="admin@sagefrog.com", role="admin", client_slug=None, is_active=True
        )
        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "list_clients", return_value=rows):
            return web_auth.render_admin_page(
                user=user, users=[], page="clients", is_super_admin=True
            )

    def test_tagged_account_shows_its_industry_chip(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="nixon", label="Nixon Medical", source="builtin",
                industry="healthcare_life_sciences",
            )
        ])
        self.assertIn("dash-industry", html)
        self.assertIn("Health &amp; Life Sciences", html)

    def test_untagged_account_reads_unassigned(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin", industry=None,
            )
        ])
        self.assertIn("dash-industry unset", html)
        self.assertIn("Unassigned", html)

    def test_row_offers_the_whole_taxonomy_and_posts_to_the_industry_route(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin", industry=None,
            )
        ])
        self.assertIn('action="/admin/dashboards/acme/industry"', html)
        self.assertIn('data-kebab-panel="industry"', html)
        for _key, label in client_industries.choices():
            self.assertIn(web_auth._esc(label), html, label)

    def test_current_industry_is_preselected(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
                industry="industrial_manufacturing",
            )
        ])
        self.assertIn('<option value="industrial_manufacturing" selected>', html)

    def test_industry_joins_the_account_filter_text(self):
        # The "Filter accounts…" box should double as "show me the manufacturing book".
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
                industry="industrial_manufacturing",
            )
        ])
        self.assertIn('data-search="acme acme industrial manufacturing"', html)


if __name__ == "__main__":
    unittest.main()
