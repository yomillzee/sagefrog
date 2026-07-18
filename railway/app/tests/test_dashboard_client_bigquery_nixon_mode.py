from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import core_routes  # noqa: E402


class _FakeCfgRow:
    def __init__(self, dashboard_mode: str, label: str | None = None):
        self.dashboard_mode = dashboard_mode
        self.label = label


class _FakeAuth:
    def __init__(self):
        self.access_key = None
        self.use_session = True
        self.user = types.SimpleNamespace(email="a@b.com", role="admin")


class DashboardClientRoutingTests(unittest.TestCase):
    """The Penn dashboard has been removed: /dashboard/{slug} always renders the
    BigQuery template, regardless of any legacy dashboard_mode on the row."""

    def _run(self, mode: str) -> tuple[object, dict]:
        fake_cdc = types.SimpleNamespace(
            get_config=lambda slug: _FakeCfgRow(mode, label="Acme Co"),
        )
        captured: dict = {}

        def fake_render(**kwargs):
            captured.update(kwargs)
            return "<html>bq-template</html>"

        with patch.dict(sys.modules, {"client_dashboard_config": fake_cdc}), \
             patch.object(core_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(core_routes.web_auth, "authenticate_dashboard", return_value=_FakeAuth()), \
             patch.object(core_routes, "session_can_switch_clients", return_value=False), \
             patch.object(core_routes, "_view_as_user_options", return_value=None), \
             patch.object(core_routes, "penn_html_session_kwargs", return_value={}), \
             patch.object(core_routes, "render_bigquery_dashboard_page", fake_render):
            resp = core_routes.dashboard_client(
                client_slug="acme", request=types.SimpleNamespace()
            )
        return resp, captured

    def test_renders_bigquery_template_regardless_of_mode(self) -> None:
        for mode in ("bigquery_nixon", "bigquery", "api"):
            with self.subTest(mode=mode):
                resp, captured = self._run(mode)
                self.assertIn("bq-template", resp.body.decode())
                self.assertEqual(captured["client_slug"], "acme")
                self.assertEqual(captured["api_client_key"], "acme")
                self.assertEqual(captured["label"], "Acme Co")


if __name__ == "__main__":
    unittest.main()
