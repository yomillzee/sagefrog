"""Google Business Profile connector wiring: OAuth, nav gate, and sync failures.

These cover the seams where a new connector usually breaks — a platform added to
one PLATFORMS set but not the other, a tab that renders but has no nav button —
plus the two sync failures worth getting right in words: no account configured,
and Google not having approved API access.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import connectors  # noqa: F401,E402  (imports register every handler)
import connectors.base as connector_base  # noqa: E402
import google_business_service as gbs  # noqa: E402
import oauth_flows  # noqa: E402
import oauth_store  # noqa: E402
from connectors.google_business import GoogleBusinessConnector  # noqa: E402
from dashboard.renderers import base_layout  # noqa: E402


class RegistrationTests(unittest.TestCase):
    def test_handler_is_registered_and_ordered(self) -> None:
        handler = connector_base.get("google_business")
        self.assertIsNotNone(handler)
        self.assertEqual(handler.display_name, "Google Business Profile")
        self.assertIn("google_business", connector_base.CONNECTOR_ORDER)

    def test_platform_is_known_to_both_oauth_modules(self) -> None:
        """oauth_flows mints the token and oauth_store persists it. A platform in
        only one of the two sets fails at whichever step comes second."""
        self.assertIn("google_business", oauth_flows.PLATFORMS)
        self.assertIn("google_business", oauth_store.PLATFORMS)

    def test_connector_icon_exists(self) -> None:
        from dashboard.renderers import connectors_renderer

        self.assertIn("google_business", connectors_renderer._PLATFORM_ICONS)


class OAuthTests(unittest.TestCase):
    def test_authorize_url_requests_business_manage_offline(self) -> None:
        with patch("auth._get_env", return_value="client-id-123"):
            url = oauth_flows.build_authorize_url("google_business", state="abc")

        self.assertIn("accounts.google.com", url)
        # business.manage is the only scope Google publishes for these APIs —
        # there is no read-only variant to fall back on.
        self.assertIn("business.manage", url)
        # Without offline access there is no refresh token, so the nightly sync
        # would die the first time the access token expired.
        self.assertIn("access_type=offline", url)
        self.assertIn("state=abc", url)

    def test_prerequisites_flag_the_missing_google_oauth_env(self) -> None:
        with patch("auth.env_summary", return_value={
            "has_client_id": False, "has_client_secret": False,
        }):
            prereq = oauth_flows.connect_prerequisites("google_business")

        self.assertFalse(prereq["ready"])
        self.assertIn("GOOGLE_ADS_CLIENT_ID", prereq["missing"])
        # The note has to warn about Google's approval gate; otherwise the first
        # sync fails with a 429 nobody can explain.
        self.assertIn("429", prereq["note"])

    def test_prerequisites_ready_when_the_google_client_is_configured(self) -> None:
        with patch("auth.env_summary", return_value={
            "has_client_id": True, "has_client_secret": True,
        }):
            self.assertTrue(oauth_flows.connect_prerequisites("google_business")["ready"])


class NavGateTests(unittest.TestCase):
    def _nav(self, flags: dict) -> str:
        with patch.object(base_layout, "platform_nav_flags", return_value=flags), \
             patch.object(base_layout, "_sidebar_hidden_tabs", return_value=()), \
             patch.object(base_layout, "_dashboard_page_url", return_value="/dashboard/acme"):
            return base_layout.dashboard_sidebar_view_nav_html(
                client_slug="acme", access_key=None, use_session=True, as_tabs=True,
            )

    def test_tab_appears_only_once_the_connector_is_connected(self) -> None:
        off = self._nav({"_connector_read_ok": True})
        self.assertNotIn('data-tab="google_business"', off)

        on = self._nav({"_connector_read_ok": True, "show_google_business": True})
        self.assertIn('data-tab="google_business"', on)
        self.assertIn("Google Business", on)

    def test_flag_set_is_complete_when_connector_state_is_unreadable(self) -> None:
        """The fail-closed branch must carry every key, so a caller's .get()
        never silently reads a missing one as False for the wrong reason."""
        with patch("connector_config_store.list_configs", side_effect=RuntimeError("pg down")):
            flags = base_layout.platform_nav_flags("acme")

        self.assertFalse(flags["_connector_read_ok"])
        self.assertIn("show_google_business", flags)


class SyncFailureTests(unittest.TestCase):
    def test_no_account_configured_says_so(self) -> None:
        with patch("connector_config_store.get_config", return_value=None):
            result = GoogleBusinessConnector().run_sync(client_slug="acme")

        self.assertEqual(result.rows_loaded, 0)
        self.assertIn("No Business Profile account", result.error)

    def test_missing_token_is_reported_before_any_api_call(self) -> None:
        cfg = SimpleNamespace(source_account_id="accounts/1", source_account_name="Acme",
                              bq_project_id="p", raw_dataset_id=None)
        with patch("connector_config_store.get_config", return_value=cfg), \
             patch("oauth_store.get_refresh_token", return_value=None), \
             patch("oauth_store.token_error", return_value="No google_business token found."), \
             patch.object(gbs, "list_locations") as listed:
            result = GoogleBusinessConnector().run_sync(client_slug="acme")

        listed.assert_not_called()
        self.assertIn("token", result.error)

    def test_unapproved_access_surfaces_as_a_setup_step_not_a_crash(self) -> None:
        cfg = SimpleNamespace(source_account_id="accounts/1", source_account_name="Acme",
                              bq_project_id="p", raw_dataset_id=None)
        with patch("connector_config_store.get_config", return_value=cfg), \
             patch("oauth_store.get_refresh_token", return_value="refresh"), \
             patch.object(gbs, "list_locations",
                          side_effect=gbs.GoogleBusinessAccessNotApproved(
                              "Google has not approved Business Profile API access")):
            result = GoogleBusinessConnector().run_sync(client_slug="acme")

        self.assertEqual(result.rows_loaded, 0)
        self.assertIn("not approved", result.error)

    def test_account_with_no_locations_is_an_error_not_a_silent_zero(self) -> None:
        cfg = SimpleNamespace(source_account_id="accounts/1", source_account_name="Acme",
                              bq_project_id="p", raw_dataset_id=None)
        with patch("connector_config_store.get_config", return_value=cfg), \
             patch("oauth_store.get_refresh_token", return_value="refresh"), \
             patch.object(gbs, "list_locations", return_value=[]):
            result = GoogleBusinessConnector().run_sync(client_slug="acme")

        self.assertIn("No locations", result.error)

    def test_successful_sync_reports_rows_and_the_window(self) -> None:
        cfg = SimpleNamespace(source_account_id="accounts/1", source_account_name="Acme",
                              bq_project_id="proj", raw_dataset_id=None)
        with patch("connector_config_store.get_config", return_value=cfg), \
             patch("oauth_store.get_refresh_token", return_value="refresh"), \
             patch.object(gbs, "list_locations",
                          return_value=[{"id": "locations/1", "name": "Main"}]), \
             patch("bq_google_business_service.sync_google_business_to_bq",
                   return_value={"total_rows": 42, "locations": 1, "errors": {}}):
            result = GoogleBusinessConnector().run_sync(
                client_slug="acme", date_range="LAST_30_DAYS"
            )

        self.assertEqual(result.rows_loaded, 42)
        self.assertIsNone(result.error)
        self.assertIsInstance(result.range_start, date)


class TestConnectionTests(unittest.TestCase):
    def test_account_without_locations_fails_the_wizard_check(self) -> None:
        """An empty account syncs cleanly and shows nothing — catch it in the
        wizard rather than letting someone debug a blank tab later."""
        cfg = SimpleNamespace(source_account_id="accounts/1", source_account_name="Acme")
        with patch("oauth_store.get_refresh_token", return_value="refresh"), \
             patch("connector_config_store.get_config", return_value=cfg), \
             patch.object(gbs, "list_locations", return_value=[]):
            with self.assertRaises(RuntimeError):
                GoogleBusinessConnector().test_connection(client_slug="acme")

    def test_label_counts_the_locations_found(self) -> None:
        cfg = SimpleNamespace(source_account_id="accounts/1", source_account_name="Acme")
        with patch("oauth_store.get_refresh_token", return_value="refresh"), \
             patch("connector_config_store.get_config", return_value=cfg), \
             patch.object(gbs, "list_locations", return_value=[
                 {"id": "locations/1", "name": "A"}, {"id": "locations/2", "name": "B"},
             ]):
            label = GoogleBusinessConnector().test_connection(client_slug="acme")

        self.assertIn("2 locations", label)


if __name__ == "__main__":
    unittest.main()
