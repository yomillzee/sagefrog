"""Polish on the admin user list: last-session column, search, and the
client-group access preview.

These pin the pieces that make a growing client roster safe and scannable:
a relative "last session" per user (recency of use at a glance — last
activity, not just last login, so a long-lived session that keeps coming
back isn't hidden behind a stale login), a client-side filter box, and a
live preview of exactly which dashboards a chosen group grants — the
redundancy that stops a client being bound to the wrong portal.
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
        user=_Admin(), users=_users(), groups=_groups(), audit_events=[], page="users"
    )


class LastSeenFormatTests(unittest.TestCase):
    def test_never_when_missing(self) -> None:
        rel, tip = web_auth._format_last_seen(None)
        self.assertEqual(rel, "Never")
        self.assertIn("not signed in", tip.lower())

    def test_relative_hours(self) -> None:
        rel, tip = web_auth._format_last_seen(_iso_ago(hours=2))
        self.assertEqual(rel, "2h ago")
        self.assertIn("UTC", tip)

    def test_relative_days_and_weeks(self) -> None:
        self.assertEqual(web_auth._format_last_seen(_iso_ago(days=3))[0], "3d ago")
        self.assertEqual(web_auth._format_last_seen(_iso_ago(days=14))[0], "2w ago")

    def test_just_now(self) -> None:
        self.assertEqual(web_auth._format_last_seen(_iso_ago(seconds=5))[0], "Just now")

    def test_bad_input_is_never(self) -> None:
        self.assertEqual(web_auth._format_last_seen("not-a-date")[0], "Never")


class UserTableTests(unittest.TestCase):
    def test_last_session_column_header_is_sortable(self) -> None:
        # The header is a sort control now, not plain text: clicking it re-stacks
        # the roster by recency.
        html = _render()
        self.assertIn('data-sort="seen"', html)
        self.assertIn("Last session", html)

    def test_last_session_cells(self) -> None:
        html = _render()
        self.assertIn("2h ago", html)
        self.assertIn("3d ago", html)
        # The never-active client is flagged distinctly.
        self.assertIn('class="last-login never"', html)
        self.assertIn(">Never<", html)

    def test_last_seen_takes_precedence_over_login(self) -> None:
        # A user who logged in a week ago but was active 10 minutes ago reads as
        # recent — the whole point of tracking activity rather than just login.
        users = [
            {"id": 5, "email": "back@sf.com", "role": "admin", "is_active": True,
             "last_login_at": _iso_ago(days=7), "last_seen_at": _iso_ago(minutes=10)},
        ]
        html = web_auth.render_admin_page(
            user=_Admin(), users=users, groups=_groups(), audit_events=[], page="users"
        )
        self.assertIn("10m ago", html)
        self.assertNotIn("7d ago", html)

    def test_falls_back_to_login_without_activity(self) -> None:
        # Rows recorded before activity tracking (no last_seen_at) still show the
        # login stamp rather than collapsing to "Never".
        users = [
            {"id": 6, "email": "legacy@sf.com", "role": "admin", "is_active": True,
             "last_login_at": _iso_ago(days=3), "last_seen_at": None},
        ]
        html = web_auth.render_admin_page(
            user=_Admin(), users=users, groups=_groups(), audit_events=[], page="users"
        )
        self.assertIn("3d ago", html)

    def test_rows_carry_search_key(self) -> None:
        html = _render()
        # data-search folds together email, role, group, and access for filtering.
        m = re.search(r'data-search="([^"]*client@pcb\.com[^"]*)"', html)
        self.assertIsNotNone(m)
        key = m.group(1)
        for token in ("client@pcb.com", "client", "penn medical", "penn"):
            self.assertIn(token, key)
        self.assertIn('id="userSearch"', html)

    def test_user_count_chip(self) -> None:
        html = _render()
        self.assertIn('id="userCount"', html)
        self.assertIn('id="userCount">3</b>', html)  # three users total


class AudienceSeparationTests(unittest.TestCase):
    """Clients and Sagefrog staff render in two separate, labelled tables."""

    def test_two_labelled_panels(self) -> None:
        html = _render()
        self.assertIn('data-group="team"', html)
        self.assertIn('data-group="client"', html)
        self.assertIn(">Sagefrog team<", html)
        self.assertIn(">Client portal users<", html)

    def test_segmented_control_with_counts(self) -> None:
        html = _render()
        self.assertIn('data-scope="team"', html)
        self.assertIn('data-scope="client"', html)
        # Two team members (admin + standard), one client.
        self.assertRegex(html, r'data-scope="team"[^>]*>Sagefrog team\s*<span class="seg-count">2</span>')
        self.assertRegex(html, r'data-scope="client"[^>]*>Clients\s*<span class="seg-count">1</span>')

    def test_client_lands_in_client_table_only(self) -> None:
        html = _render()
        client_panel = re.search(
            r'data-group="client".*?</table>', html, re.S
        ).group(0)
        team_panel = re.search(
            r'data-group="team".*?</table>', html, re.S
        ).group(0)
        self.assertIn("client@pcb.com", client_panel)
        self.assertNotIn("client@pcb.com", team_panel)
        # Staff/admin never appear in the client table.
        self.assertIn("staff@sf.com", team_panel)
        self.assertNotIn("staff@sf.com", client_panel)

    def test_admin_access_shown_as_all_clients(self) -> None:
        self.assertIn('class="chip-all">All clients</span>', _render())

    def test_scope_filter_js_present(self) -> None:
        self.assertIn(".seg-btn", _render())


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


class RowActionMenuTests(unittest.TestCase):
    """Every row action lives in one kebab menu, not a stack of loose links."""

    def test_each_row_has_exactly_one_kebab(self) -> None:
        html = _render()
        rows = re.findall(r'<tr class="user-row".*?</tr>', html, re.S)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row.count('class="dash-kebab user-kebab"'), 1)

    def test_actions_are_panels_inside_the_menu(self) -> None:
        html = _render()
        row = next(
            r for r in re.findall(r'<tr class="user-row".*?</tr>', html, re.S)
            if "staff@sf.com" in r
        )
        for panel in ("pw-3", "invite-3", "role-3", "off-3"):
            self.assertIn(f'data-kebab-panel="{panel}"', row)
            self.assertIn(f'data-panel="{panel}"', row)
        # The old inline text links are gone.
        self.assertNotIn('class="row-fold"', row)
        self.assertNotIn('class="link danger"', row)

    def test_own_row_cannot_be_re_roled_or_deactivated(self) -> None:
        html = _render()
        own = next(
            r for r in re.findall(r'<tr class="user-row".*?</tr>', html, re.S)
            if "admin@sf.com" in r
        )
        self.assertIn(">You</span>", own)
        self.assertNotIn('data-panel="role-1"', own)
        self.assertNotIn('data-panel="off-1"', own)
        # …but a self password reset is still offered.
        self.assertIn('data-panel="pw-1"', own)

    def test_kebab_js_is_always_emitted(self) -> None:
        # The menus used to depend on script that only shipped with the
        # dashboard registry section; the Users page ships without it.
        self.assertIn("data-kebab-panel", _render())
        self.assertIn(".dash-kebab[open]", _render())


class RosterFirstLayoutTests(unittest.TestCase):
    """The roster leads the page; creating a user folds out of its header."""

    def test_create_form_is_inside_the_roster_header(self) -> None:
        html = _render()
        head = re.search(r'<div class="users-head">.*?</div>\s*<div class="user-group"', html, re.S)
        self.assertIsNotNone(head)
        self.assertIn('id="createUserForm"', head.group(0))
        self.assertIn(">Add user</summary>", head.group(0))

    def test_roster_precedes_the_groups_section(self) -> None:
        html = _render()
        self.assertLess(
            html.index('class="user-group"'),
            html.index('<section class="dash-section groups-section">'),
        )

    def test_no_duplicated_page_title(self) -> None:
        # The page header already says "Users"; the card used to repeat it.
        self.assertEqual(_render().count(">Users <span"), 0)


class GroupRowActionTests(unittest.TestCase):
    def test_group_rows_use_the_same_kebab(self) -> None:
        html = _render()
        self.assertIn('class="dash-kebab group-kebab"', html)
        self.assertIn('data-panel="gedit-7"', html)

    def test_delete_only_offered_for_an_empty_group(self) -> None:
        html = _render()
        # Group 7 has a member; group 8 does not.
        self.assertNotIn('data-panel="gdel-7"', html)
        self.assertIn('data-panel="gdel-8"', html)
        self.assertIn("Reassign its members", html)

if __name__ == "__main__":
    unittest.main()
