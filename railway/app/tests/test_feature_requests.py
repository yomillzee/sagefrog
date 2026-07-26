"""Feature requests: data-layer helpers, route wiring, widget markup.

DB-backed reads/writes need Postgres, so these cover the pure logic instead:
input normalization, the JSON/dataclass shapes, the agency-only submit gate, and
the rendered FAB menu + feature-request panel. Heavy deps are stubbed only when
the real ones can't be imported so the test still runs standalone.
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

import feature_requests  # noqa: E402


class CleanHelperTests(unittest.TestCase):
    def test_body_is_trimmed_and_bounded(self) -> None:
        self.assertEqual(feature_requests._clean_body("  hi  "), "hi")
        long = "x" * (feature_requests.MAX_BODY_LEN + 50)
        self.assertEqual(len(feature_requests._clean_body(long)), feature_requests.MAX_BODY_LEN)

    def test_path_and_label_normalize_empties_to_none(self) -> None:
        self.assertIsNone(feature_requests._clean_path("   "))
        self.assertIsNone(feature_requests._clean_label(""))
        self.assertEqual(feature_requests._clean_path("/dashboard/penn"), "/dashboard/penn")
        self.assertEqual(feature_requests._clean_label(" Penn "), "Penn")

    def test_slug_is_lowercased_or_none(self) -> None:
        self.assertEqual(feature_requests._clean_slug("  Penn "), "penn")
        self.assertIsNone(feature_requests._clean_slug("   "))

    def test_status_normalizes_unknown_to_new(self) -> None:
        self.assertEqual(feature_requests._normalize_status("done"), "done")
        self.assertEqual(feature_requests._normalize_status("bogus"), "new")
        self.assertEqual(feature_requests._normalize_status(None), "new")


class RequestShapeTests(unittest.TestCase):
    def _req(self) -> feature_requests.FeatureRequest:
        return feature_requests.FeatureRequest(
            id=3,
            client_slug="penn",
            page_path="/dashboard/penn",
            page_label="Penn Community Bank",
            body="Add a dark mode toggle.",
            status="new",
            created_at="2026-07-26T00:00:00+00:00",
            created_by="who@agency.com",
            resolved_at=None,
            resolved_by=None,
        )

    def test_to_dict_round_trips_fields(self) -> None:
        d = self._req().to_dict()
        self.assertEqual(d["id"], 3)
        self.assertEqual(d["page_label"], "Penn Community Bank")
        self.assertEqual(d["status"], "new")
        self.assertEqual(d["body"], "Add a dark mode toggle.")


class SubmitRouteAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        import web_auth
        from dashboard.routes import notes_routes

        self.notes_routes = notes_routes
        self.DashboardAuth = web_auth.DashboardAuth

    def _auth(self, role: str):
        user = types.SimpleNamespace(role=role, email="who@agency.com")
        return self.DashboardAuth(access_key=None, use_session=True, user=user)

    def test_client_role_cannot_submit(self) -> None:
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self.notes_routes._require_agency(self._auth("client"))
        self.assertEqual(ctx.exception.status_code, 403)


class RouteRegistrationTests(unittest.TestCase):
    def test_router_exposes_feature_request_path(self) -> None:
        from dashboard.routes.notes_routes import router

        paths = {r.path for r in router.routes}
        self.assertIn("/dashboard/{client_slug}/notes/feature-request", paths)


class WidgetMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        from dashboard.renderers import notes_widget

        self.notes_widget = notes_widget

    def test_fab_menu_has_notes_and_feature_request(self) -> None:
        html = self.notes_widget.widget_html(client_slug="penn", label="Penn Community Bank")
        # Sleek round FAB fans out both actions.
        self.assertIn('id="sfnoteFab"', html)
        self.assertIn('id="sfnoteOpen"', html)
        self.assertIn('id="sffrOpen"', html)
        self.assertIn("Feature request", html)
        # Notes panel and feature-request panel both present.
        self.assertIn('id="sfnotePanel"', html)
        self.assertIn('id="sffrPanel"', html)
        self.assertIn('id="sffrBody"', html)
        # Submit posts to the client-scoped feature-request endpoint.
        self.assertIn("/feature-request", html)

    def test_standalone_window_has_no_feature_request_panel(self) -> None:
        html = self.notes_widget.window_page_html(client_slug="nixon", label="Nixon")
        self.assertNotIn('id="sffrPanel"', html)
        self.assertNotIn('id="sfnoteFab"', html)


if __name__ == "__main__":
    unittest.main()
