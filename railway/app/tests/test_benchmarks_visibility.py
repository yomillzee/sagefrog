"""Peer benchmarks are opt-in per client, and off until someone opts in.

A benchmark is a claim about an account's performance relative to others, and it
is only as good as that account's industry tags. So the interesting property is
not that the toggle works — it is that *every* path defaults to off: a client
with no config row, a config row predating the column, a renderer called without
the flag, and the API when the flag is unset.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class RendererDefaultTests(unittest.TestCase):
    """The dashboard must not request benchmarks unless told to."""

    def _render(self, **kw):
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )

        return render_bigquery_dashboard_page(
            client_slug="demo", api_client_key="demo", label="Demo",
            use_session=True, session_email="t@e.com", session_is_admin=True, **kw
        )

    def test_benchmarks_are_off_when_the_flag_is_not_passed(self):
        self.assertIn("const SHOW_BENCHMARKS = false", self._render())

    def test_benchmarks_turn_on_only_when_explicitly_enabled(self):
        self.assertIn("const SHOW_BENCHMARKS = true", self._render(show_benchmarks=True))

    def test_the_loader_checks_both_the_admin_gate_and_the_toggle(self):
        # Belt and braces: an admin with the toggle off must still not fetch.
        html = self._render(show_benchmarks=True)
        self.assertIn("if (!IS_ADMIN || !SHOW_BENCHMARKS || benchLoaded) return;", html)


class SettingsToggleTests(unittest.TestCase):
    def _render(self, **kw):
        from dashboard.renderers.bigquery_settings_renderer import (
            render_bigquery_settings_page,
        )

        return render_bigquery_settings_page(
            use_session=True, session_email="t@e.com", **kw
        )

    def test_admin_sees_an_unchecked_toggle_by_default(self):
        html = self._render(session_is_admin=True)
        self.assertIn('id="sec-benchmarks"', html)
        self.assertIn('<input type="checkbox" id="benchmarksToggle">', html)

    def test_a_stored_on_setting_renders_the_toggle_checked(self):
        html = self._render(session_is_admin=True, benchmarks_enabled=True)
        self.assertIn('<input type="checkbox" id="benchmarksToggle" checked>', html)

    def test_a_non_admin_never_gets_the_control(self):
        html = self._render(session_is_admin=False, benchmarks_enabled=True)
        self.assertNotIn('id="sec-benchmarks"', html)
        self.assertNotIn('<input type="checkbox" id="benchmarksToggle"', html)

    def test_the_copy_points_at_industry_tags(self):
        # The setting is only meaningful once tags are right; the page has to say
        # so, or someone will switch it on for an untagged book.
        html = self._render(session_is_admin=True)
        self.assertIn("industry tags", html)


class ConfigDefaultTests(unittest.TestCase):
    """The stored default, independent of any renderer."""

    def test_the_dataclass_defaults_to_off(self):
        import client_dashboard_config

        row = client_dashboard_config.ClientConfigRow(
            client_slug="demo", label="Demo", google_customer_id=None,
            linkedin_account_id=None, meta_account_id=None, ga4_client_key=None,
        )
        self.assertFalse(row.benchmarks_enabled)

    def test_a_client_with_no_config_row_reads_as_off(self):
        import client_dashboard_config

        real = client_dashboard_config.get_config
        client_dashboard_config.get_config = lambda slug: None
        try:
            self.assertFalse(client_dashboard_config.benchmarks_enabled("ghost"))
        finally:
            client_dashboard_config.get_config = real

    def test_a_null_column_reads_as_off(self):
        # Rows written before the column existed come back as None, not False.
        import client_dashboard_config

        real = client_dashboard_config.get_config
        client_dashboard_config.get_config = lambda slug: types.SimpleNamespace(
            benchmarks_enabled=None
        )
        try:
            self.assertFalse(client_dashboard_config.benchmarks_enabled("legacy"))
        finally:
            client_dashboard_config.get_config = real

    def test_the_column_ddl_defaults_to_false(self):
        import client_dashboard_config

        ddl = " ".join(client_dashboard_config.SCHEMA_SQL_STATEMENTS)
        self.assertIn("benchmarks_enabled BOOLEAN NOT NULL DEFAULT FALSE", ddl)


if __name__ == "__main__":
    unittest.main()
