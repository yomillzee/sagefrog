"""The admin roster's identity column, status dot, and client-access marks.

Three things moved at once and each has a way to regress quietly:

* A user can carry a **full name**. The roster and the sidebar account chip show
  it; when it is unset both fall back to the email's local part ("mikem@…" →
  "Mikem"), which is what every account showed before names existed.
* **Status** stopped being a column of the word "Active" and became a coloured
  dot beside the name — the word still has to reach a screen reader and the
  tooltip, or an invite-pending account becomes invisible.
* **Client access** shows each account's mark (logo, else tinted initials)
  instead of its name, so the label has to survive in the tooltip and in the
  row's search key — otherwise filtering by a client's name stops finding the
  people who can see it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import web_auth  # noqa: E402
import web_users  # noqa: E402
from dashboard.renderers import base_layout  # noqa: E402


class _Admin:
    id = 1
    email = "admin@sf.com"
    role = "admin"


def _users():
    return [
        {
            "id": 1,
            "email": "admin@sf.com",
            "role": "admin",
            "is_active": True,
            "full_name": "Ada Lovelace",
            "display_name": "Ada Lovelace",
            "last_login_at": None,
        },
        {
            "id": 2,
            "email": "mikem@sagefrog.com",
            "role": "standard",
            "allowed_client_slugs": ["penn"],
            "is_active": True,
            "invite_pending": True,
            "last_login_at": None,
        },
        {
            "id": 3,
            "email": "client@pcb.com",
            "role": "client",
            "client_slug": "penn",
            "is_active": False,
            "last_login_at": None,
        },
    ]


def _render() -> str:
    return web_auth.render_admin_page(
        user=_Admin(), users=_users(), groups=[], audit_events=[], page="users"
    )


class DisplayNameTests(unittest.TestCase):
    def test_stored_name_wins(self) -> None:
        self.assertEqual(
            web_users.display_name_for("mikem@sagefrog.com", "Mike Miller"), "Mike Miller"
        )

    def test_falls_back_to_the_email_local_part(self) -> None:
        self.assertEqual(web_users.display_name_for("mikem@sagefrog.com"), "Mikem")
        self.assertEqual(web_users.display_name_for("mike.miller@sagefrog.com"), "Mike Miller")

    def test_blank_name_is_treated_as_unset(self) -> None:
        self.assertEqual(web_users.display_name_for("mikem@sagefrog.com", "   "), "Mikem")

    def test_sidebar_chip_uses_the_same_rule(self) -> None:
        self.assertEqual(
            base_layout._display_name_from_email("mikem@sagefrog.com", "Mike Miller"),
            "Mike Miller",
        )
        self.assertEqual(base_layout._display_name_from_email("mikem@sagefrog.com"), "Mikem")


class RosterIdentityTests(unittest.TestCase):
    def test_name_leads_the_row_with_the_email_beneath(self) -> None:
        html = _render()
        self.assertIn(">Ada Lovelace</span>", html)
        # An account with no stored name still shows a name, not just an address.
        self.assertIn(">Mikem</span>", html)
        self.assertIn('class="user-email"', html)

    def test_name_is_searchable(self) -> None:
        self.assertIn("ada lovelace", _render())

    def test_every_row_offers_an_edit_name_action(self) -> None:
        html = _render()
        self.assertIn("/admin/users/1/name", html)
        self.assertIn("/admin/users/2/name", html)
        self.assertIn('name="full_name"', html)


class StatusDotTests(unittest.TestCase):
    def test_status_is_a_dot_not_a_column(self) -> None:
        html = _render()
        self.assertNotIn("<th>Status</th>", html)
        self.assertIn('class="status-dot status-on"', html)

    def test_invite_pending_and_inactive_are_distinct(self) -> None:
        html = _render()
        self.assertIn("status-invite", html)
        self.assertIn("status-off", html)

    def test_the_wording_survives_for_screen_readers(self) -> None:
        html = _render()
        self.assertIn(">Invite pending</span>", html)
        self.assertIn(">Inactive</span>", html)


class ClientMarkTests(unittest.TestCase):
    def test_access_renders_as_marks(self) -> None:
        html = _render()
        self.assertIn('class="mark-row"', html)
        self.assertIn("client-mark", html)

    def test_slug_tint_is_stable(self) -> None:
        self.assertEqual(web_auth._slug_color("penn"), web_auth._slug_color("penn"))


class SortTests(unittest.TestCase):
    def test_rows_carry_a_sortable_last_session_value(self) -> None:
        html = _render()
        # Nobody in the fixture has signed in, so every row sorts as 0 — the
        # attribute has to be there regardless for the header to sort on it.
        self.assertIn('data-seen="0"', html)
        self.assertIn('data-sort="seen"', html)


if __name__ == "__main__":
    unittest.main()
