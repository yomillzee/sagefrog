"""Regression: the Nixon portal is listed and granted under the "nixon-bq-test"
slug, but its routes historically authed against the bare "nixon" slug. A
standard user granted only "nixon-bq-test" therefore saw "Nixon Medical" in
their picker yet 403'd on every Nixon route.

These tests pin the fix: authenticate_dashboard_any / _api_any grant access when
the user can reach ANY of the equivalent slugs, and the Nixon routes use both.
"""

from __future__ import annotations

import re
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


class _WebUser:
    """Fake user scoped to an explicit set of accessible slugs (admins = all)."""

    def __init__(self, *, role="standard", slugs=()):
        self.role = role
        self.email = "u@x.com"
        self._slugs = frozenset(slugs)

    def can_access_client(self, slug: str) -> bool:
        return self.role == "admin" or slug in self._slugs


def _web_users_stub() -> types.ModuleType:
    m = types.ModuleType("web_users")
    m.WebUser = _WebUser
    m.enabled = lambda: True
    return m


_ensure_stub("audit_log", lambda: types.ModuleType("audit_log"))
_ensure_stub("client_config", lambda: types.ModuleType("client_config"))
_ensure_stub("web_users", _web_users_stub)

import web_auth  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from starlette.requests import Request  # noqa: E402

_NIXON_SLUGS = ("nixon-bq-test", "nixon")


def _request() -> Request:
    return Request(
        {"type": "http", "headers": [], "query_string": b"", "path": "/dashboard/nixon-bq-test"}
    )


class MultiSlugAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        # web_auth gates on web_users.enabled() (DATABASE_URL); force it on so
        # these unit tests exercise the access logic, not the 503 short-circuit.
        self._orig_enabled = web_auth.web_users.enabled
        web_auth.web_users.enabled = lambda: True
        self.addCleanup(lambda: setattr(web_auth.web_users, "enabled", self._orig_enabled))

    def _patch_user(self, user):
        orig = web_auth.get_current_user
        web_auth.get_current_user = lambda request: user
        self.addCleanup(lambda: setattr(web_auth, "get_current_user", orig))

    def test_grant_on_nixon_bq_test_slug_opens_the_portal(self) -> None:
        # The exact regression: granted only "nixon-bq-test" (what the picker
        # lists), the user must pass a route that also accepts "nixon".
        self._patch_user(_WebUser(role="standard", slugs=("nixon-bq-test",)))
        auth = web_auth.authenticate_dashboard_any(_request(), client_slugs=_NIXON_SLUGS)
        self.assertIsInstance(auth, web_auth.DashboardAuth)
        self.assertTrue(auth.use_session)

    def test_legacy_grant_on_nixon_slug_still_opens_the_portal(self) -> None:
        self._patch_user(_WebUser(role="standard", slugs=("nixon",)))
        auth = web_auth.authenticate_dashboard_any(_request(), client_slugs=_NIXON_SLUGS)
        self.assertIsInstance(auth, web_auth.DashboardAuth)

    def test_user_without_any_slug_is_forbidden(self) -> None:
        self._patch_user(_WebUser(role="standard", slugs=("other",)))
        with self.assertRaises(HTTPException) as ctx:
            web_auth.authenticate_dashboard_any(_request(), client_slugs=_NIXON_SLUGS)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_anonymous_is_redirected_to_login(self) -> None:
        self._patch_user(None)
        result = web_auth.authenticate_dashboard_any(_request(), client_slugs=_NIXON_SLUGS)
        self.assertIsInstance(result, RedirectResponse)
        self.assertIn("/login", result.headers["location"])

    def test_api_variant_grants_on_either_slug(self) -> None:
        self._patch_user(_WebUser(role="standard", slugs=("nixon-bq-test",)))
        auth = web_auth.authenticate_dashboard_api_any(_request(), client_slugs=_NIXON_SLUGS)
        self.assertIsInstance(auth, web_auth.DashboardAuth)

    def test_api_variant_401_for_anonymous(self) -> None:
        self._patch_user(None)
        with self.assertRaises(HTTPException) as ctx:
            web_auth.authenticate_dashboard_api_any(_request(), client_slugs=_NIXON_SLUGS)
        self.assertEqual(ctx.exception.status_code, 401)


class NixonRoutesUseMultiSlugTests(unittest.TestCase):
    """Source-level guard: no Nixon route may auth against the bare "nixon" slug
    again, and the constant must include the picker/grant slug."""

    def _source(self) -> str:
        return (APP_DIR / "dashboard" / "routes" / "api_routes.py").read_text()

    def test_no_route_auths_against_bare_nixon_slug(self) -> None:
        src = self._source()
        self.assertNotIn('authenticate_dashboard(request, client_slug="nixon")', src)
        self.assertNotIn('authenticate_dashboard_api(request, client_slug="nixon")', src)

    def test_access_slugs_constant_includes_nixon_bq_test(self) -> None:
        src = self._source()
        match = re.search(r"_NIXON_ACCESS_SLUGS\s*=\s*\(([^)]*)\)", src)
        self.assertIsNotNone(match, "_NIXON_ACCESS_SLUGS constant not found")
        body = match.group(1)
        self.assertIn("nixon-bq-test", body)
        self.assertIn('"nixon"', body)


if __name__ == "__main__":
    unittest.main()
