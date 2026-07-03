from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import settings_routes  # noqa: E402


class _FakeCfgRow:
    def __init__(self, dashboard_mode, label="Test Co", gcp_project_id="test-proj",
                 bq_mart_dataset_id="marketing_marts"):
        self.dashboard_mode = dashboard_mode
        self.label = label
        self.gcp_project_id = gcp_project_id
        self.bq_mart_dataset_id = bq_mart_dataset_id
        self.updated_at = None


class _FakeAuth:
    def __init__(self):
        self.access_key = None
        self.use_session = True
        self.user = types.SimpleNamespace(email="a@b.com", role="admin")


class SettingsBigqueryNixonModeTests(unittest.TestCase):
    def test_nixon_mode_renders_nixon_settings_not_penn_form(self) -> None:
        with patch.object(settings_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(settings_routes.client_dashboard_config, "get_config",
                          return_value=_FakeCfgRow("bigquery_nixon")), \
             patch.object(settings_routes.web_users, "enabled", return_value=True), \
             patch.object(settings_routes.web_auth, "authenticate_dashboard", return_value=_FakeAuth()), \
             patch.object(settings_routes.dashboard_settings, "render_settings_html") as penn_form:
            resp = settings_routes.dashboard_client_settings(client_slug="test", request=types.SimpleNamespace(), key=None)

        body = resp.body.decode()
        # Nixon-style settings page, not the Penn account-ID form.
        self.assertIn("Test Co — Settings", body)
        self.assertIn("/api/clients/test/refresh", body)
        self.assertNotIn("/api/clients/nixon/", body)
        penn_form.assert_not_called()

    def test_non_nixon_mode_still_renders_penn_form(self) -> None:
        with patch.object(settings_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(settings_routes.client_dashboard_config, "get_config",
                          return_value=_FakeCfgRow("bigquery")), \
             patch.object(settings_routes.web_users, "enabled", return_value=True), \
             patch.object(settings_routes.web_auth, "authenticate_dashboard", return_value=_FakeAuth()), \
             patch.object(settings_routes.dashboard_settings, "load_settings_config", return_value=object()), \
             patch.object(settings_routes.dashboard_settings, "render_settings_html", return_value="<html>penn-form</html>") as penn_form:
            resp = settings_routes.dashboard_client_settings(client_slug="other", request=types.SimpleNamespace(), key=None)

        self.assertIn("penn-form", resp.body.decode())
        penn_form.assert_called_once()


if __name__ == "__main__":
    unittest.main()
