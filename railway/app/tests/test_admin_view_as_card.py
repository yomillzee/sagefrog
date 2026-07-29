"""The global "view as user" card no longer lives on the admin page.

It used to be a floating bubble overlaying the client dashboards, then briefly a
card inside ``render_admin_page``. Impersonation is now offered per-client from
the client shell's "View As" tool tab, so the global picker on the admin page is
gone. These tests pin that removal (on every split-out admin page) and that the
dashboard renderer still carries no floating admin FAB/panel.
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


def _render(users, *, page="users"):
    return web_auth.render_admin_page(
        user=_Admin(), users=users, groups=[], audit_events=[], page=page
    )


class AdminViewAsCardRemovedTests(unittest.TestCase):
    def _users(self):
        return [
            {"id": 1, "email": "admin@sf.com", "role": "admin", "is_active": True},
            {"id": 2, "email": "client@pcb.com", "role": "client",
             "client_slug": "penn", "is_active": True},
            {"id": 3, "email": "staff@sf.com", "role": "standard",
             "allowed_client_slugs": ["penn"], "is_active": True},
        ]

    def test_no_view_as_card_on_any_admin_page(self) -> None:
        for page in ("clients", "users", "feature-requests", "advanced"):
            html = _render(self._users(), page=page)
            self.assertNotIn("<h2>View as user</h2>", html, f"view-as card leaked onto {page}")
            self.assertNotIn('action="/admin/view-as"', html, f"view-as form leaked onto {page}")


class DashboardNoLongerHasAdminFabTests(unittest.TestCase):
    def test_bigquery_renderer_source_has_no_floating_admin_panel(self) -> None:
        src = (APP_DIR / "dashboard" / "renderers" / "bigquery_dashboard_renderer.py").read_text()
        for needle in ("admin-fab", "adminFab", "admin-panel", "adminPanel"):
            self.assertNotIn(needle, src, f"floating admin panel remnant: {needle}")


if __name__ == "__main__":
    unittest.main()
