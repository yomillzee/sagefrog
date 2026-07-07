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


class DashboardClientBigqueryNixonModeTests(unittest.TestCase):
    def test_bigquery_nixon_mode_renders_nixon_template_not_penn_html(self) -> None:
        fake_cdc = types.SimpleNamespace(
            get_config=lambda slug: _FakeCfgRow("bigquery_nixon", label="Acme Co"),
        )
        captured = {}

        def fake_render(**kwargs):
            captured.update(kwargs)
            return "<html>nixon-template</html>"

        with patch.dict(sys.modules, {"client_dashboard_config": fake_cdc}), \
             patch.object(core_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(core_routes.web_users, "enabled", return_value=True), \
             patch.object(core_routes.web_auth, "authenticate_dashboard", return_value=_FakeAuth()), \
             patch.object(core_routes, "render_bigquery_dashboard_page", fake_render), \
             patch.object(core_routes.dashboard_service, "render_penn_html") as fake_render_penn:
            request = types.SimpleNamespace()
            resp = core_routes.dashboard_client(client_slug="acme", request=request, key=None)

        self.assertIn("nixon-template", resp.body.decode())
        fake_render_penn.assert_not_called()
        self.assertEqual(captured["client_slug"], "acme")
        self.assertEqual(captured["api_client_key"], "acme")
        self.assertEqual(captured["label"], "Acme Co")

    def test_bigquery_mode_still_renders_penn_html_unchanged(self) -> None:
        fake_cdc = types.SimpleNamespace(
            get_config=lambda slug: _FakeCfgRow("bigquery", label="Other Co"),
        )

        with patch.dict(sys.modules, {"client_dashboard_config": fake_cdc}), \
             patch.object(core_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(core_routes.web_users, "enabled", return_value=True), \
             patch.object(core_routes.web_auth, "authenticate_dashboard", return_value=_FakeAuth()), \
             patch.object(core_routes.dashboard_snapshots, "get_snapshot", return_value={"ok": True}), \
             patch.object(core_routes, "render_bigquery_dashboard_page") as fake_render, \
             patch.object(core_routes.dashboard_service, "render_penn_html", return_value="<html>penn</html>") as fake_render_penn:
            request = types.SimpleNamespace()
            resp = core_routes.dashboard_client(client_slug="other", request=request, key=None)

        self.assertIn("penn", resp.body.decode())
        fake_render.assert_not_called()
        fake_render_penn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
