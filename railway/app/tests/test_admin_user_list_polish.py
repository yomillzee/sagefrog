"""Polish on the admin user list: last-login column, search, and the
client-group access preview.

These pin the pieces that make a growing client roster safe and scannable:
a relative "last login" per user (usage at a glance), a client-side filter
box, and a live preview of exactly which dashboards a chosen group grants —
the redundancy that stops a client being bound to the wrong portal.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import web_auth  # noqa: E402


class _Admin:
    id = 1
    email = "admin@sf.com"
    role = "admin"


def _iso_ago(**kwargs) -> str:
    return (datetime.now(tz=UTC) - timedelta(**kwargs)).isoformat()


def _users():
    return [
        {"id": 1, "email": "admin@sf.com", "role": "admin", "is_active": True,
         "last_login_at": _iso_ago(hours=2)},
        {"id": 2, "email": "client@pcb.com", "role": "client",
         "client_slug": "penn", "group_id": 7, "group_name": "Penn Medical",
         "group_client_slugs": ["penn"], "is_active": True, "last_login_at": None},
        {"id": 3, "email": "staff@sf.com", "role": "standard",
         "allowed_client_slugs": ["penn"], "is_active": True,
         "last_login_at": _iso_ago(days=3)},
    ]


def _groups():
    return [
        {"id": 7, "name": "Penn Medical", "client_slugs": ["penn"],
         "description": None, "member_count": 1, "is_active": True},
        {"id": 8, "name": "Empty Group", "client_slugs": [],
         "description": None, "member_count": 0, "is_active": True},
    ]


def _render():
    return web_auth.render_admin_page(
        user=_Admin(), users=_users(), groups=_groups(), audit_events=[]
    )


class LastLoginFormatTests(unittest.TestCase):
    def test_never_when_missing(self) -> None:
        rel, tip = web_auth._format_last_login(None)
        self.assertEqual(rel, "Never")
        self.assertIn("not signed in", tip.lower())

    def test_relative_hours(self) -> None:
        rel, tip = web_auth._format_last_login(_iso_ago(hours=2))
        self.assertEqual(rel, "2h ago")
        self.assertIn("UTC", tip)

    def test_relative_days_and_weeks(self) -> None:
        self.assertEqual(web_auth._format_last_login(_iso_ago(days=3))[0], "3d ago")
        self.assertEqual(web_auth._format_last_login(_iso_ago(days=14))[0], "2w ago")

    def test_just_now(self) -> None:
        self.assertEqual(web_auth._format_last_login(_iso_ago(seconds=5))[0], "Just now")

    def test_bad_input_is_never(self) -> None:
        self.assertEqual(web_auth._format_last_login("not-a-date")[0], "Never")


class UserTableTests(unittest.TestCase):
    def test_last_login_column_header(self) -> None:
        self.assertIn("<th>Last login</th>", _render())

    def test_last_login_cells(self) -> None:
        html = _render()
        self.assertIn("2h ago", html)
        self.assertIn("3d ago", html)
        # The never-logged-in client is flagged distinctly.
        self.assertIn('class="last-login never"', html)
        self.assertIn(">Never<", html)

    def test_rows_carry_search_key(self) -> None:
        html = _render()
        # data-search folds together email, role, group, and slug for filtering.
        m = re.search(r'data-search="([^"]*client@pcb\.com[^"]*)"', html)
        self.assertIsNotNone(m)
        key = m.group(1)
        for token in ("client@pcb.com", "client", "penn medical", "penn"):
            self.assertIn(token, key)
        self.assertIn('id="userSearch"', html)
        self.assertIn('id="userTableBody"', html)

    def test_user_count_chip(self) -> None:
        html = _render()
        self.assertIn('id="userCount"', html)
        self.assertIn(">3</span>", html)  # three users


class GroupPreviewTests(unittest.TestCase):
    def test_options_carry_dashboard_labels(self) -> None:
        html = _render()
        # The group <option> exposes its dashboard labels for the live preview.
        self.assertIn('data-labels="penn"', html)

    def test_preview_container_present(self) -> None:
        self.assertIn('class="group-preview"', _render())

    def test_toggle_and_preview_js_wired(self) -> None:
        html = _render()
        self.assertIn("syncGroupPreview", html)
        self.assertIn("Grants access to", html)


if __name__ == "__main__":
    unittest.main()
