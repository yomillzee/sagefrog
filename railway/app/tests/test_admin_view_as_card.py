"""The "view as user" tool lives as a card on the admin page.

It used to be a floating bubble overlaying the client dashboards; it now renders
inside ``render_admin_page`` so it never covers a dashboard mid-presentation.
These tests pin that card's presence, its exclusion of the signed-in admin, and
that the dashboard renderer no longer emits the old floating admin FAB/panel.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import web_auth  # noqa: E402


class _Admin:
    id = 1
    email = "admin@sf.com"
    role = "admin"


def _render(users):
    return web_auth.render_admin_page(user=_Admin(), users=users, groups=[], audit_events=[])


class AdminViewAsCardTests(unittest.TestCase):
    def _users(self):
        return [
            {"id": 1, "email": "admin@sf.com", "role": "admin", "is_active": True},
            {"id": 2, "email": "client@pcb.com", "role": "client",
             "client_slug": "penn", "is_active": True},
            {"id": 3, "email": "staff@sf.com", "role": "standard",
             "allowed_client_slugs": ["penn"], "is_active": True},
        ]

    def test_card_and_form_present(self) -> None:
        html = _render(self._users())
        self.assertIn("<h2>View as user</h2>", html)
        self.assertIn('action="/admin/view-as"', html)
        self.assertIn('name="user_id"', html)

    def test_other_users_are_listed(self) -> None:
        html = _render(self._users())
        self.assertIn("client@pcb.com — client · penn", html)
        self.assertIn("staff@sf.com — standard", html)

    def test_signed_in_admin_is_not_an_option(self) -> None:
        html = _render(self._users())
        import re

        section = re.search(r"View as user.*?</section>", html, re.S).group(0)
        self.assertNotIn('value="1"', section)

    def test_empty_state_when_no_other_users(self) -> None:
        html = _render([{"id": 1, "email": "admin@sf.com", "role": "admin", "is_active": True}])
        self.assertIn("<h2>View as user</h2>", html)
        self.assertIn("No other users to view as yet.", html)


class DashboardNoLongerHasAdminFabTests(unittest.TestCase):
    def test_bigquery_renderer_source_has_no_floating_admin_panel(self) -> None:
        src = (APP_DIR / "dashboard" / "renderers" / "bigquery_dashboard_renderer.py").read_text()
        for needle in ("admin-fab", "adminFab", "admin-panel", "adminPanel"):
            self.assertNotIn(needle, src, f"floating admin panel remnant: {needle}")


if __name__ == "__main__":
    unittest.main()
