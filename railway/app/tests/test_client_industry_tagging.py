"""Tagging an account with industries: storage, and how the Accounts page shows it.

The tag is descriptive metadata on the registry row — it changes nothing about
the client's own dashboard, and its only consumer is the agency Benchmarks
rollup. An account can carry several buckets (a clinical-workflow SaaS vendor is
both health and software), stored as a comma-separated list in the one
``industry`` column. These cover the write path (normalization, multi-select,
clearing) and the admin surface that sets it.
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


def _write(*args):
    """Run set_industries against a fake connection; return the executed params."""
    connection, executed = _fake_conn()
    with patch.object(dashboard_registry, "enabled", return_value=True), \
         patch.object(dashboard_registry, "ensure_schema", return_value=True), \
         patch.object(dashboard_registry.db, "connection", connection):
        ok = dashboard_registry.set_industries(*args)
    return ok, executed


class SetIndustriesTests(unittest.TestCase):
    def test_valid_key_is_stored_against_the_normalized_slug(self):
        ok, executed = _write("Nixon-BQ-Test", "healthcare_life_sciences")

        self.assertTrue(ok)
        self.assertTrue(
            any(
                "UPDATE dashboard_clients" in a[0]
                and a[1] == ("healthcare_life_sciences", "nixon-bq-test")
                for a in executed
            ),
            executed,
        )

    def test_several_keys_are_stored_as_one_comma_separated_value(self):
        # Multi-tagging is the whole point: an account that straddles two markets
        # is benchmarked against both books.
        _ok, executed = _write("nixon", ["technology_software", "healthcare_life_sciences"])

        self.assertTrue(
            any(a[1] == ("healthcare_life_sciences,technology_software", "nixon")
                for a in executed),
            executed,
        )

    def test_stored_order_follows_the_taxonomy_not_the_click_order(self):
        # So a tag reads identically however the boxes were ticked, and the
        # stored value is stable across re-saves.
        _ok, a = _write("nixon", ["other", "aec"])
        _ok, b = _write("nixon", ["aec", "other"])
        self.assertEqual(a[-1][1], b[-1][1])
        self.assertEqual(a[-1][1], ("aec,other", "nixon"))

    def test_duplicates_collapse(self):
        _ok, executed = _write("nixon", ["aec", "aec", "AEC"])
        self.assertTrue(any(a[1] == ("aec", "nixon") for a in executed), executed)

    def test_empty_value_clears_the_tag(self):
        for empty in ("", [], None):
            _ok, executed = _write("nixon", empty)
            self.assertTrue(any(a[1] == (None, "nixon") for a in executed), executed)

    def test_unknown_key_is_dropped_rather_than_stored_as_garbage(self):
        # The taxonomy lives in code; a retired key must not persist as a bucket
        # nothing can render — but it must not take a valid sibling down with it.
        _ok, executed = _write("nixon", ["widget_making", "aec"])
        self.assertTrue(any(a[1] == ("aec", "nixon") for a in executed), executed)

        _ok, executed = _write("nixon", "widget_making")
        self.assertTrue(any(a[1] == (None, "nixon") for a in executed), executed)

    def test_single_key_alias_still_works(self):
        connection, executed = _fake_conn()
        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry.db, "connection", connection):
            dashboard_registry.set_industry("nixon", "other")
        self.assertTrue(any(a[1] == ("other", "nixon") for a in executed), executed)

    def test_no_database_is_a_no_op_not_a_crash(self):
        with patch.object(dashboard_registry, "enabled", return_value=False):
            self.assertFalse(dashboard_registry.set_industries("nixon", ["other"]))

    def test_industries_map_parses_lists_and_skips_unknown_keys(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ("nixon", "healthcare_life_sciences,technology_software"),
            ("acme", "retired_bucket"),
            ("beta", None),
            # A row written before multi-select shipped is already a valid
            # one-element list — nothing to backfill.
            ("gamma", "aec"),
        ]

        @contextmanager
        def connection():
            yield conn

        with patch.object(dashboard_registry, "enabled", return_value=True), \
             patch.object(dashboard_registry, "ensure_schema", return_value=True), \
             patch.object(dashboard_registry.db, "connection", connection):
            out = dashboard_registry.industries_map()

        self.assertEqual(
            out,
            {
                "nixon": ("healthcare_life_sciences", "technology_software"),
                "gamma": ("aec",),
            },
        )


class RowShapeTests(unittest.TestCase):
    def test_industry_is_the_first_of_the_row_s_buckets(self):
        row = dashboard_registry.DashboardClientRow(
            client_slug="nixon", label="Nixon", source="admin",
            industries=("healthcare_life_sciences", "technology_software"),
        )
        self.assertEqual(row.industry, "healthcare_life_sciences")
        self.assertEqual(
            row.industry_label, "Health & Life Sciences · Technology & Software"
        )

    def test_untagged_row_reads_unassigned(self):
        row = dashboard_registry.DashboardClientRow(
            client_slug="acme", label="Acme", source="admin"
        )
        self.assertIsNone(row.industry)
        self.assertEqual(row.industry_label, client_industries.UNASSIGNED_LABEL)


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
                industries=("healthcare_life_sciences",),
            )
        ])
        self.assertIn("dash-industry", html)
        self.assertIn("Health &amp; Life Sciences", html)

    def test_multi_tagged_account_shows_a_chip_per_bucket(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="nixon", label="Nixon Medical", source="builtin",
                industries=("healthcare_life_sciences", "technology_software"),
            )
        ])
        self.assertIn("Health &amp; Life Sciences", html)
        self.assertIn("Technology &amp; Software", html)
        self.assertEqual(html.count('<span class="dash-industry" '), 2)
        self.assertNotIn("dash-industry unset", html)

    def test_untagged_account_reads_unassigned(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
            )
        ])
        self.assertIn("dash-industry unset", html)
        self.assertIn("Unassigned", html)

    def test_row_offers_the_whole_taxonomy_and_posts_to_the_industry_route(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
            )
        ])
        self.assertIn('action="/admin/dashboards/acme/industry"', html)
        self.assertIn('data-kebab-panel="industry"', html)
        for key, label in client_industries.choices():
            self.assertIn(f'name="industries" value="{key}"', html, key)
            self.assertIn(web_auth._esc(label), html, label)

    def test_every_current_industry_is_pre_ticked_and_the_rest_are_not(self):
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
                industries=("industrial_manufacturing", "aec"),
            )
        ])
        self.assertIn('name="industries" value="industrial_manufacturing" checked>', html)
        self.assertIn('name="industries" value="aec" checked>', html)
        self.assertIn('name="industries" value="other">', html)

    def test_industries_join_the_account_filter_text(self):
        # The "Filter accounts…" box should double as "show me the manufacturing
        # book" — and find a multi-tagged account under either of its buckets.
        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
                industries=("industrial_manufacturing",),
            )
        ])
        self.assertIn('data-search="acme acme industrial manufacturing"', html)

        html = self._render([
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin",
                industries=("healthcare_life_sciences", "technology_software"),
            )
        ])
        self.assertIn(
            'data-search="acme acme health &amp; life sciences technology &amp; software"',
            html,
        )


if __name__ == "__main__":
    unittest.main()
