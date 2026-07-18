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


class SettingsAlwaysBigqueryTests(unittest.TestCase):
    """The Penn settings form has been removed: /dashboard/{slug}/settings always
    renders the BigQuery settings page, regardless of any legacy dashboard_mode."""

    def _render(self, mode: str):
        """Returns the bq-settings mock after invoking the settings route.

        The renderer is patched so the assertion is purely about routing (the
        BigQuery settings page is chosen) and needs no database.
        """
        with patch.object(settings_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(settings_routes.client_dashboard_config, "get_config",
                          return_value=_FakeCfgRow(mode)), \
             patch.object(settings_routes.web_auth, "authenticate_dashboard", return_value=_FakeAuth()), \
             patch.object(settings_routes, "_render_bq_nixon_settings",
                          return_value="<html>bq-settings</html>") as bq_settings:
            settings_routes.dashboard_client_settings(
                client_slug="test", request=types.SimpleNamespace()
            )
        return bq_settings

    def test_renders_bigquery_settings_regardless_of_mode(self) -> None:
        for mode in ("bigquery_nixon", "bigquery", "api"):
            with self.subTest(mode=mode):
                bq_settings = self._render(mode)
                bq_settings.assert_called_once()


if __name__ == "__main__":
    unittest.main()
