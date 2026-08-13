from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.renderers.connectors_renderer import render_connectors_directory  # noqa: E402
from dashboard.routes import settings_routes  # noqa: E402

# The BQ Connection admin tab was retired; the BigQuery destination it showed now
# lives on the Connectors page, next to the wizard that actually sets it.


def _render(**over) -> str:
    kw = dict(
        client_slug="acme", label="Acme", configs=[],
        use_session=True, session_email="admin@sf.com", session_is_admin=True,
        bq_project="acme-proj", bq_marts_dataset="marketing_marts",
    )
    kw.update(over)
    return render_connectors_directory(**kw)


class ConnectorsBqDestinationTests(unittest.TestCase):
    def test_strip_shows_destination_and_verify_button(self):
        html = _render()
        self.assertIn('id="bqDestCard"', html)
        self.assertIn(">BigQuery destination<", html)
        self.assertIn("acme-proj", html)
        # The verify button points at the retained client-level endpoint.
        self.assertIn("/api/clients/acme/bq-verify", html)
        self.assertIn(">Verify access<", html)

    def test_strip_is_admin_only(self):
        # (The stylesheet always ships; it's the card that must be absent.)
        html = _render(session_is_admin=False)
        self.assertNotIn('id="bqDestCard"', html)
        self.assertNotIn("bq-verify", html)
        self.assertNotIn("acme-proj", html)

    def test_unset_project_degrades_instead_of_blanking(self):
        # A brand-new client has no project until its first connector saves one.
        html = _render(bq_project=None, bq_marts_dataset=None)
        self.assertIn("not set yet", html)
        self.assertIn(">marketing_marts<", html)  # the default mart still shows

    def test_verify_url_carries_access_key(self):
        html = _render(access_key="k e y", use_session=False)
        self.assertIn("/api/clients/acme/bq-verify?key=k%20e%20y", html)


class RetiredBqTabTests(unittest.TestCase):
    def test_bq_tab_is_no_longer_an_admin_tool_tab(self):
        self.assertNotIn("bq", settings_routes._ADMIN_TOOL_TABS)

    def test_old_bq_tab_url_redirects_to_connectors(self):
        # Permanent redirect rather than a 404, so a bookmarked tab lands on the
        # page that now owns the destination.
        with patch.object(settings_routes, "validate_client_slug", side_effect=lambda s: s):
            resp = settings_routes.admin_tools_tab(
                client_slug="acme", request=types.SimpleNamespace(), tab="bq",
            )
        self.assertEqual(resp.status_code, 308)
        self.assertEqual(resp.headers["location"], "/dashboard/acme/connectors")


if __name__ == "__main__":
    unittest.main()
