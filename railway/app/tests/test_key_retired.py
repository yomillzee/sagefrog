"""The legacy ?key= dashboard share-link mechanism has been retired.

These tests pin the resulting behavior of the auth layer: dashboards are
session-only, authenticate_dashboard no longer accepts a key, and the old
key-secret helpers are gone.
"""

from __future__ import annotations

import inspect
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# web_auth pulls audit_log / client_config / web_users (which import the DB
# driver). Stub the heavy deps only when the real ones can't be imported, so
# the test runs standalone without disturbing environments that have them.
def _ensure_stub(name: str, build) -> None:
    try:
        __import__(name)
    except Exception:
        sys.modules[name] = build()


class _WebUser:
    def __init__(self, *, email="u@x.com", role="client", slug="penn"):
        self.email = email
        self.role = role
        self.client_slug = slug

    def can_access_client(self, slug: str) -> bool:
        return self.role == "admin" or self.client_slug == slug


def _web_users_stub() -> types.ModuleType:
    m = types.ModuleType("web_users")
    m.WebUser = _WebUser
    m.enabled = lambda: True
    return m


_ensure_stub("audit_log", lambda: types.ModuleType("audit_log"))
_ensure_stub("client_config", lambda: types.ModuleType("client_config"))
_ensure_stub("web_users", _web_users_stub)

import web_auth  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402


def _request() -> Request:
    return Request({"type": "http", "headers": [], "query_string": b"", "path": "/dashboard/penn"})


class SignatureTests(unittest.TestCase):
    def test_authenticate_dashboard_has_no_key_param(self) -> None:
        for fn in (web_auth.authenticate_dashboard, web_auth.authenticate_dashboard_api):
            self.assertNotIn("key", inspect.signature(fn).parameters)

    def test_removed_symbols_are_gone(self) -> None:
        for name in ("legacy_dashboard_key_ok", "resolve_client_dashboard_user"):
            self.assertFalse(hasattr(web_auth, name), f"{name} should be removed")


class AuthBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        # These cases need web_users to look "enabled"; save the real attribute
        # and restore it in tearDown so an override never leaks into later test
        # modules (which would make DB-backed helpers think a DB is configured).
        self._orig_enabled = web_auth.web_users.enabled
        web_auth.web_users.enabled = lambda: True

    def tearDown(self) -> None:
        web_auth.web_users.enabled = self._orig_enabled

    def _patch_user(self, user):
        self._orig = web_auth.get_current_user
        web_auth.get_current_user = lambda request: user
        self.addCleanup(lambda: setattr(web_auth, "get_current_user", self._orig))

    def test_user_with_access_gets_session_auth(self) -> None:
        self._patch_user(_WebUser(role="client", slug="penn"))
        auth = web_auth.authenticate_dashboard(_request(), client_slug="penn")
        self.assertIsInstance(auth, web_auth.DashboardAuth)
        self.assertIsNone(auth.access_key)     # never a key anymore
        self.assertTrue(auth.use_session)

    def test_user_without_access_is_forbidden(self) -> None:
        self._patch_user(_WebUser(role="client", slug="other"))
        with self.assertRaises(HTTPException) as ctx:
            web_auth.authenticate_dashboard(_request(), client_slug="penn")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_anonymous_is_redirected_to_login(self) -> None:
        self._patch_user(None)
        result = web_auth.authenticate_dashboard(_request(), client_slug="penn")
        self.assertIsInstance(result, RedirectResponse)
        self.assertIn("/login", result.headers["location"])

    def test_api_variant_401_for_anonymous(self) -> None:
        self._patch_user(None)
        with self.assertRaises(HTTPException) as ctx:
            web_auth.authenticate_dashboard_api(_request(), client_slug="penn")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_503_when_web_users_disabled(self) -> None:
        self._patch_user(_WebUser())
        web_auth.web_users.enabled = lambda: False
        with self.assertRaises(HTTPException) as ctx:
            web_auth.authenticate_dashboard(_request(), client_slug="penn")
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
