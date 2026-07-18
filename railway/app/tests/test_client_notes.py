"""Client-specific presenter notepads: data-layer helpers, widget, route auth.

DB-backed reads/writes need Postgres, so these cover the pure logic instead:
input normalization, the JSON shapes, the agency-only route gate, and the
rendered widget/window markup. Heavy deps are stubbed only when the real ones
can't be imported so the test still runs standalone.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _ensure_stub(name: str, build) -> None:
    try:
        __import__(name)
    except Exception:
        sys.modules[name] = build()


def _web_users_stub() -> types.ModuleType:
    m = types.ModuleType("web_users")

    class WebUser:  # minimal shape
        def __init__(self, *, email="u@x.com", role="admin", client_slug=None):
            self.email = email
            self.role = role
            self.client_slug = client_slug

        def can_access_client(self, slug: str) -> bool:
            return self.role in ("admin", "standard") or self.client_slug == slug

    m.WebUser = WebUser
    m.enabled = lambda: True
    return m


def _db_stub() -> types.ModuleType:
    m = types.ModuleType("db")
    m.connection = None  # unused: these tests never hit the DB path
    return m


_ensure_stub("db", _db_stub)
_ensure_stub("web_users", _web_users_stub)

import client_notes  # noqa: E402


class CleanHelperTests(unittest.TestCase):
    def test_title_defaults_when_blank(self) -> None:
        self.assertEqual(client_notes._clean_title("   "), client_notes.DEFAULT_TITLE)
        self.assertEqual(client_notes._clean_title(None), client_notes.DEFAULT_TITLE)

    def test_title_is_trimmed_and_bounded(self) -> None:
        self.assertEqual(client_notes._clean_title("  Hi  "), "Hi")
        long = "x" * (client_notes.MAX_TITLE_LEN + 50)
        self.assertEqual(len(client_notes._clean_title(long)), client_notes.MAX_TITLE_LEN)

    def test_body_is_bounded_but_not_trimmed(self) -> None:
        # Leading/trailing whitespace is meaningful in a note body, so it stays.
        self.assertEqual(client_notes._clean_body("  keep  "), "  keep  ")
        long = "y" * (client_notes.MAX_BODY_LEN + 100)
        self.assertEqual(len(client_notes._clean_body(long)), client_notes.MAX_BODY_LEN)

    def test_slug_is_lowercased_and_required(self) -> None:
        self.assertEqual(client_notes._clean_slug("  Penn "), "penn")
        with self.assertRaises(ValueError):
            client_notes._clean_slug("   ")


class NotepadShapeTests(unittest.TestCase):
    def _pad(self) -> client_notes.Notepad:
        return client_notes.Notepad(
            id=7,
            client_slug="penn",
            title="Talking points",
            body="Lead with ROAS.",
            created_at="2026-07-18T00:00:00+00:00",
            updated_at="2026-07-18T01:00:00+00:00",
            created_by="a@x.com",
            updated_by="b@x.com",
        )

    def test_to_dict_has_body(self) -> None:
        d = self._pad().to_dict()
        self.assertEqual(d["id"], 7)
        self.assertEqual(d["body"], "Lead with ROAS.")
        self.assertEqual(d["client_slug"], "penn")

    def test_summary_omits_body(self) -> None:
        s = self._pad().summary()
        self.assertNotIn("body", s)
        self.assertEqual(s["title"], "Talking points")
        self.assertEqual(s["updated_by"], "b@x.com")


class RouteAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        import web_auth
        from dashboard.routes import notes_routes

        self.notes_routes = notes_routes
        self.DashboardAuth = web_auth.DashboardAuth

    def _auth(self, role: str):
        user = types.SimpleNamespace(role=role, email="who@agency.com")
        return self.DashboardAuth(access_key=None, use_session=True, user=user)

    def test_agency_roles_pass(self) -> None:
        for role in ("admin", "standard"):
            user = self.notes_routes._require_agency(self._auth(role))
            self.assertEqual(user.email, "who@agency.com")

    def test_client_role_is_forbidden(self) -> None:
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self.notes_routes._require_agency(self._auth("client"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_no_user_is_forbidden(self) -> None:
        from fastapi import HTTPException

        auth = self.DashboardAuth(access_key=None, use_session=True, user=None)
        with self.assertRaises(HTTPException) as ctx:
            self.notes_routes._require_agency(auth)
        self.assertEqual(ctx.exception.status_code, 403)


class WidgetRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        from dashboard.renderers import notes_widget

        self.notes_widget = notes_widget

    def test_widget_html_has_fab_and_scoped_api_base(self) -> None:
        html = self.notes_widget.widget_html(client_slug="penn", label="Penn Community Bank")
        self.assertIn('id="sfnoteFab"', html)
        self.assertIn('id="sfnotePanel"', html)
        # Config carries the client-scoped API base and the popout target.
        self.assertIn('"/dashboard/penn/notes"', html)
        self.assertIn("/window", html)

    def test_widget_label_is_json_embedded(self) -> None:
        # A label with quotes must not break the config <script>.
        html = self.notes_widget.widget_html(client_slug="penn", label='Penn "PCB"')
        self.assertIn('window.__sfNotesCfg=', html)
        self.assertIn('Penn \\"PCB\\"', html)

    def test_window_page_is_full_document(self) -> None:
        html = self.notes_widget.window_page_html(client_slug="nixon", label="Nixon")
        self.assertTrue(html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn('id="sfnoteBody"', html)
        self.assertIn('"/dashboard/nixon/notes"', html)
        # No FAB/close chrome in the standalone window.
        self.assertNotIn('id="sfnoteFab"', html)


class RouteRegistrationTests(unittest.TestCase):
    def test_router_exposes_notes_paths(self) -> None:
        from dashboard.routes.notes_routes import router

        paths = {r.path for r in router.routes}
        self.assertIn("/dashboard/{client_slug}/notes", paths)
        self.assertIn("/dashboard/{client_slug}/notes/window", paths)
        self.assertIn("/dashboard/{client_slug}/notes/{notepad_id:int}", paths)
        self.assertIn("/dashboard/{client_slug}/notes/{notepad_id:int}/delete", paths)


if __name__ == "__main__":
    unittest.main()
