"""Page comments, the team they notify, and the inbox that shows the result.

The DB-backed reads/writes need Postgres, so these cover everything around
them: how a page URL collapses to one thread key, who a comment routes to, the
addressing rules a notification write applies, the agency-only route gates, and
the rendered inbox / FAB markup.
"""

from __future__ import annotations

import contextlib
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_team  # noqa: E402
import notifications  # noqa: E402
import page_comments  # noqa: E402


class FakeCursor:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Records every statement so a test can assert on what was written."""

    def __init__(self, rows=(), rowcount=0):
        self.calls: list[tuple[str, tuple]] = []
        self._rows = rows
        self._rowcount = rowcount

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return FakeCursor(self._rows, self._rowcount)


def fake_db(conn: FakeConn) -> types.ModuleType:
    m = types.ModuleType("db")

    @contextlib.contextmanager
    def connection():
        yield conn

    m.connection = connection
    return m


class PageKeyTests(unittest.TestCase):
    def test_path_is_lowercased_and_trailing_slash_trimmed(self) -> None:
        self.assertEqual(page_comments.page_key("/Dashboard/Acme/"), "/dashboard/acme")

    def test_view_is_part_of_the_key_but_a_date_range_is_not(self) -> None:
        # Two people looking at Analytics on different date ranges are looking at
        # the same page, and should land in the same thread.
        a = page_comments.page_key("/dashboard/acme?view=analytics&start=2026-01-01")
        b = page_comments.page_key("/dashboard/acme?view=analytics&start=2026-06-01")
        self.assertEqual(a, b)
        self.assertEqual(a, "/dashboard/acme?view=analytics")

    def test_different_views_are_different_threads(self) -> None:
        self.assertNotEqual(
            page_comments.page_key("/dashboard/acme?view=analytics"),
            page_comments.page_key("/dashboard/acme?view=overview"),
        )

    def test_blank_path_is_the_root(self) -> None:
        self.assertEqual(page_comments.page_key(""), "/")
        self.assertEqual(page_comments.page_key(None), "/")


class DisplayNameTests(unittest.TestCase):
    def test_local_part_becomes_a_name(self) -> None:
        self.assertEqual(page_comments.display_name("sam.jones@sagefrog.com"), "Sam Jones")
        self.assertEqual(page_comments.display_name("mikem@sagefrog.com"), "Mikem")

    def test_missing_email_has_a_fallback(self) -> None:
        self.assertEqual(page_comments.display_name(None), "Someone")
        self.assertEqual(page_comments.display_name("   "), "Someone")


class DefaultTeamTests(unittest.TestCase):
    ROSTER = [
        {"email": "Sam@sagefrog.com", "role": "standard", "allowed_client_slugs": ["Acme", "penn"]},
        {"email": "kelly@sagefrog.com", "role": "standard", "allowed_client_slugs": ["penn"]},
        # An admin can open everything; that is access, not staffing.
        {"email": "boss@sagefrog.com", "role": "admin", "allowed_client_slugs": []},
    ]

    def test_maps_scoped_standard_users_to_their_accounts(self) -> None:
        mapping = client_team.default_team_map(self.ROSTER)
        self.assertEqual(mapping["acme"], ("sam@sagefrog.com",))
        self.assertEqual(set(mapping["penn"]), {"sam@sagefrog.com", "kelly@sagefrog.com"})

    def test_admins_are_not_a_default_team_anywhere(self) -> None:
        mapping = client_team.default_team_map(self.ROSTER)
        for emails in mapping.values():
            self.assertNotIn("boss@sagefrog.com", emails)


class ResolveTeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            "enabled": client_team.enabled,
            "list_team": client_team.list_team,
            "default_team": client_team.default_team,
            "agency_users": client_team.agency_users,
        }
        client_team.enabled = lambda: True
        client_team.agency_users = lambda: [
            {"email": "sam@sagefrog.com", "role": "standard"},
            {"email": "kelly@sagefrog.com", "role": "standard"},
        ]

    def tearDown(self) -> None:
        for name, fn in self._saved.items():
            setattr(client_team, name, fn)

    def test_explicit_team_wins_over_the_fallback(self) -> None:
        client_team.list_team = lambda slug: ("kelly@sagefrog.com",)
        client_team.default_team = lambda slug: ("sam@sagefrog.com",)
        self.assertEqual(client_team.resolve_team("acme"), ("kelly@sagefrog.com",))

    def test_unset_team_falls_back_to_access(self) -> None:
        client_team.list_team = lambda slug: ()
        client_team.default_team = lambda slug: ("sam@sagefrog.com",)
        self.assertEqual(client_team.resolve_team("acme"), ("sam@sagefrog.com",))

    def test_members_who_left_are_dropped_without_editing_the_team(self) -> None:
        client_team.list_team = lambda slug: ("gone@sagefrog.com", "sam@sagefrog.com")
        client_team.default_team = lambda slug: ()
        self.assertEqual(client_team.resolve_team("acme"), ("sam@sagefrog.com",))


class NotifyAddressingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = FakeConn()
        self._db = notifications.db
        self._enabled = notifications.enabled
        self._ensure = notifications.ensure_schema
        notifications.db = fake_db(self.conn)
        notifications.enabled = lambda: True
        notifications.ensure_schema = lambda: True

    def tearDown(self) -> None:
        notifications.db = self._db
        notifications.enabled = self._enabled
        notifications.ensure_schema = self._ensure

    def _recipients(self) -> list[str]:
        return [c[1][0] for c in self.conn.calls if "INSERT INTO user_notifications" in c[0]]

    def test_one_row_per_recipient_lowercased_and_deduped(self) -> None:
        written = notifications.notify(
            ["Sam@sagefrog.com", "sam@sagefrog.com", "kelly@sagefrog.com"],
            kind=notifications.KIND_COMMENT,
            title="t",
        )
        self.assertEqual(written, 2)
        self.assertEqual(self._recipients(), ["sam@sagefrog.com", "kelly@sagefrog.com"])

    def test_the_author_is_not_notified_about_their_own_comment(self) -> None:
        notifications.notify(
            ["sam@sagefrog.com", "kelly@sagefrog.com"],
            kind=notifications.KIND_COMMENT,
            title="t",
            actor_email="Sam@sagefrog.com",
        )
        self.assertEqual(self._recipients(), ["kelly@sagefrog.com"])

    def test_no_recipients_writes_nothing(self) -> None:
        self.assertEqual(notifications.notify([], kind="comment", title="t"), 0)
        self.assertEqual(self.conn.calls, [])

    def test_a_write_failure_never_reaches_the_caller(self) -> None:
        # The comment is already saved by the time we notify — losing a
        # notification must not lose the comment.
        class Boom(FakeConn):
            def execute(self, sql, params=()):
                raise RuntimeError("db down")

        notifications.db = fake_db(Boom())
        self.assertEqual(
            notifications.notify(["sam@sagefrog.com"], kind="comment", title="t"), 0
        )


class CommentNotificationRoutingTests(unittest.TestCase):
    """What ``_notify`` addresses, given a comment and (for a reply) its root."""

    def setUp(self) -> None:
        self.sent: list[dict] = []
        self._resolve = client_team.resolve_team
        self._notify = notifications.notify
        self._participants = page_comments._thread_participants
        client_team.resolve_team = lambda slug: ("kelly@sagefrog.com",)
        page_comments._thread_participants = lambda root_id: ("outsider@sagefrog.com",)

        def capture(recipients, **kwargs):
            self.sent.append({"recipients": list(recipients), **kwargs})
            return len(recipients)

        notifications.notify = capture

    def tearDown(self) -> None:
        client_team.resolve_team = self._resolve
        notifications.notify = self._notify
        page_comments._thread_participants = self._participants

    def _comment(self, **over) -> page_comments.Comment:
        base = dict(
            id=5,
            client_slug="acme",
            page_key="/dashboard/acme",
            page_path="/dashboard/acme?view=analytics",
            page_label="Website Analytics",
            body="This number looks off.",
            parent_id=None,
            created_at="2026-08-26T12:00:00+00:00",
            created_by="sam@sagefrog.com",
        )
        base.update(over)
        return page_comments.Comment(**base)

    def test_a_new_comment_goes_to_the_accounts_team(self) -> None:
        page_comments._notify(self._comment(), root=None)
        sent = self.sent[0]
        self.assertEqual(sent["recipients"], ["kelly@sagefrog.com"])
        self.assertEqual(sent["kind"], notifications.KIND_COMMENT)
        self.assertIn("Sam commented on Website Analytics", sent["title"])
        self.assertEqual(sent["body"], "This number looks off.")
        self.assertEqual(sent["client_slug"], "acme")

    def test_a_reply_also_reaches_everyone_already_in_the_thread(self) -> None:
        root = self._comment(id=1)
        page_comments._notify(self._comment(id=6, parent_id=1), root=root)
        sent = self.sent[0]
        self.assertEqual(
            set(sent["recipients"]), {"kelly@sagefrog.com", "outsider@sagefrog.com"}
        )
        self.assertEqual(sent["kind"], notifications.KIND_COMMENT_REPLY)
        self.assertIn("replied", sent["title"])

    def test_a_notification_failure_never_reaches_the_caller(self) -> None:
        def boom(*a, **k):
            raise RuntimeError("nope")

        notifications.notify = boom
        page_comments._notify(self._comment(), root=None)  # must not raise


class RouteGateTests(unittest.TestCase):
    def setUp(self) -> None:
        import web_auth
        from dashboard.routes import notes_routes, notifications_routes

        self.notes_routes = notes_routes
        self.notifications_routes = notifications_routes
        self.DashboardAuth = web_auth.DashboardAuth

    def test_comment_routes_are_agency_only(self) -> None:
        from fastapi import HTTPException

        auth = self.DashboardAuth(
            access_key=None,
            use_session=True,
            user=types.SimpleNamespace(role="client", email="them@client.com"),
        )
        with self.assertRaises(HTTPException) as ctx:
            self.notes_routes._require_agency(auth)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_inbox_helper_rejects_a_client_login(self) -> None:
        import web_auth

        saved = web_auth.get_current_user
        try:
            web_auth.get_current_user = lambda request: types.SimpleNamespace(
                role="client", email="them@client.com"
            )
            self.assertIsNone(self.notifications_routes._agency_user(object()))
            web_auth.get_current_user = lambda request: types.SimpleNamespace(
                role="standard", email="sam@sagefrog.com"
            )
            self.assertIsNotNone(self.notifications_routes._agency_user(object()))
        finally:
            web_auth.get_current_user = saved


class InboxRenderTests(unittest.TestCase):
    def _note(self, **over) -> notifications.Notification:
        base = dict(
            id=3,
            recipient_email="kelly@sagefrog.com",
            kind="comment",
            client_slug="acme",
            page_path="/dashboard/acme?view=analytics",
            page_label="Website Analytics",
            title="Sam commented on Website Analytics",
            body="<script>alert(1)</script>",
            actor_email="sam@sagefrog.com",
            comment_id=5,
            created_at="2026-08-26T12:00:00+00:00",
            read_at=None,
        )
        base.update(over)
        return notifications.Notification(**base)

    def test_rows_escape_their_body_and_link_through_open(self) -> None:
        from dashboard.renderers.notifications_page import render_notifications_page

        html = render_notifications_page(
            email="kelly@sagefrog.com", is_admin=True, items=[self._note()], unread=1
        )
        self.assertIn("/notifications/3/open", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Mark all read", html)

    def test_read_rows_do_not_offer_mark_all_read(self) -> None:
        from dashboard.renderers.notifications_page import render_notifications_page

        html = render_notifications_page(
            email="kelly@sagefrog.com",
            is_admin=True,
            items=[self._note(read_at="2026-08-26T13:00:00+00:00")],
            unread=0,
        )
        self.assertNotIn("Mark all read", html)

    def test_a_standard_user_does_not_get_the_admin_menu(self) -> None:
        from dashboard.renderers.notifications_page import render_notifications_page

        html = render_notifications_page(
            email="sam@sagefrog.com", is_admin=False, items=[], unread=0
        )
        self.assertNotIn("/admin/clients", html)
        self.assertIn("Nothing yet", html)


class FabWidgetTests(unittest.TestCase):
    def test_the_fab_offers_comments_alongside_notes_and_requests(self) -> None:
        from dashboard.renderers import notes_widget

        html = notes_widget.widget_html(client_slug="acme", label="Acme")
        self.assertIn('id="sfcmOpen"', html)
        self.assertIn('id="sfcmPanel"', html)
        self.assertIn("__sfCmPrime", html)
        # Still the original two actions.
        self.assertIn('id="sfnoteOpen"', html)
        self.assertIn('id="sffrOpen"', html)

    def test_the_popup_notes_window_has_no_comment_panel(self) -> None:
        from dashboard.renderers import notes_widget

        html = notes_widget.window_page_html(client_slug="acme", label="Acme")
        self.assertNotIn("sfcmPanel", html)


class AccountsTeamPickerTests(unittest.TestCase):
    """The Accounts grid is where an account's team is read and set."""

    # The same shape web_users.list_users returns, which is what the page is
    # handed — the roster it renders and the roster the picker draws from.
    ROSTER = [
        {"id": 1, "email": "sam@sagefrog.com", "role": "standard",
         "allowed_client_slugs": ["acme"], "is_active": True},
        {"id": 2, "email": "kelly@sagefrog.com", "role": "standard",
         "allowed_client_slugs": [], "is_active": True},
        {"id": 3, "email": "boss@sagefrog.com", "role": "admin",
         "allowed_client_slugs": [], "is_active": True},
        # A client login is never offered as a team member.
        {"id": 4, "email": "them@client.com", "role": "client",
         "allowed_client_slugs": [], "is_active": True},
    ]

    def _render(self, saved_teams):
        import dashboard_registry
        import web_auth
        import web_users
        from unittest.mock import patch

        rows = [
            dashboard_registry.DashboardClientRow(
                client_slug="acme", label="Acme", source="admin"
            )
        ]
        user = web_users.WebUser(
            id=1, email="admin@sagefrog.com", role="admin", client_slug=None, is_active=True
        )
        with patch.object(dashboard_registry, "enabled", return_value=True),              patch.object(dashboard_registry, "list_clients", return_value=rows),              patch.object(client_team, "teams_by_slug", return_value=saved_teams):
            return web_auth.render_admin_page(
                user=user, users=self.ROSTER, page="clients", is_super_admin=True
            )

    def test_saved_team_shows_on_the_row_and_opens_pre_ticked(self) -> None:
        html = self._render({"acme": ("kelly@sagefrog.com",)})
        self.assertIn("dash-team", html)
        self.assertIn("Kelly", html)
        self.assertIn("/admin/dashboards/acme/team", html)
        self.assertIn(
            '<input type="checkbox" name="team_emails" value="kelly@sagefrog.com" checked',
            html,
        )
        self.assertIn(
            '<input type="checkbox" name="team_emails" value="sam@sagefrog.com">', html
        )

    def test_unset_team_falls_back_to_whoever_has_access(self) -> None:
        html = self._render({})
        self.assertIn("Sam", html)
        self.assertNotIn("dash-team unset", html)
        self.assertIn(
            '<input type="checkbox" name="team_emails" value="sam@sagefrog.com" checked',
            html,
        )

    def test_an_account_nobody_is_on_says_so(self) -> None:
        import dashboard_registry
        import web_auth
        import web_users
        from unittest.mock import patch

        rows = [
            dashboard_registry.DashboardClientRow(
                client_slug="orphan", label="Orphan", source="admin"
            )
        ]
        user = web_users.WebUser(
            id=1, email="admin@sagefrog.com", role="admin", client_slug=None, is_active=True
        )
        with patch.object(dashboard_registry, "enabled", return_value=True),              patch.object(dashboard_registry, "list_clients", return_value=rows),              patch.object(client_team, "teams_by_slug", return_value={}):
            html = web_auth.render_admin_page(
                user=user, users=self.ROSTER, page="clients", is_super_admin=True
            )
        self.assertIn("dash-team unset", html)
        self.assertIn("No team", html)

    def test_client_logins_are_not_offered_as_team_members(self) -> None:
        html = self._render({})
        self.assertNotIn('value="them@client.com"', html)


if __name__ == "__main__":
    unittest.main()
