from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The /admin routes are moving out of main.py into the admin/ package a group at
# a time. The failure mode that move introduces is a route quietly not being
# registered — a router that nobody calls include_router on, or a module nobody
# imports — and the symptom is a 404 on a page that used to work.
#
# Counting app.routes does not catch it: this version of FastAPI stores an
# included router as a single wrapper entry rather than flattening its routes
# into the list, so a router contributing ten endpoints and one contributing
# none look the same from the outside. The only honest check is to ask the app
# to route a request and see whether it finds anything.
#
# Deliberately no environment fiddling here. An earlier version set DATABASE_URL
# at import time so that session middleware would install; because pytest shares
# one process, that leaked into every other test in the run and broke 192 of
# them. Nothing here needs it: a route that matches but then fails for want of a
# database still answers something other than 404, which is the whole question.

import main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

# Every route the admin/ package is responsible for.
MOVED_ROUTES = [
    # admin/client_hours_routes.py
    ("GET", "/admin/client-hours"),
    ("GET", "/admin/client-hours/data"),
    ("POST", "/admin/client-hours/goal"),
    ("POST", "/admin/client-hours/project-tag"),
    ("POST", "/admin/client-hours/project-split"),
    ("POST", "/admin/client-hours/owner"),
    ("POST", "/admin/client-hours/prefs"),
    ("GET", "/admin/client-hours/shares"),
    ("POST", "/admin/client-hours/share"),
    ("POST", "/admin/client-hours/share/revoke"),
    # admin/dashboards_routes.py
    ("POST", "/admin/dashboards"),
    ("POST", "/admin/dashboards/example/rename"),
    ("POST", "/admin/dashboards/example/industry"),
    ("POST", "/admin/dashboards/example/team"),
    ("POST", "/admin/dashboards/example/mode"),
    ("POST", "/admin/dashboards/example/logo"),
    ("POST", "/admin/dashboards/example/delete"),
    ("POST", "/admin/snapshot/example/delete"),
    # admin/reporting_routes.py
    ("GET", "/admin/benchmarks"),
    ("GET", "/admin/benchmarks/data"),
    ("GET", "/admin/agency-trends"),
    ("GET", "/admin/agency-trends/data"),
    ("GET", "/admin/hq"),
    ("GET", "/admin/hq/data"),
]

# Still in main.py. Listed so that this test keeps covering them as they move,
# rather than silently narrowing to whatever is left in the package.
STILL_IN_MAIN = [
    ("GET", "/admin"),
    ("GET", "/admin/users"),
    ("GET", "/admin/clients"),
    ("GET", "/admin/advanced"),
    ("GET", "/admin/docs"),
    ("GET", "/admin/changelog"),
    ("GET", "/admin/feature-requests"),
]


class AdminRoutesAreRegisteredTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    def _assert_routed(self, method: str, path: str) -> None:
        code = self.client.request(method, path, follow_redirects=False).status_code
        # 404 means nothing matched the path. 405 means the path matched but not
        # the method, which would also be a wiring mistake. Anything else — a
        # redirect to login, a 401, even a 500 from the absent database — means
        # the app found the route.
        self.assertNotIn(
            code,
            (404, 405),
            f"{method} {path} answered {code}: nothing is routed there. Check "
            "that its router is imported and included in admin/__init__.py",
        )

    def test_the_routes_moved_into_the_admin_package_are_registered(self):
        for method, path in MOVED_ROUTES:
            with self.subTest(route=f"{method} {path}"):
                self._assert_routed(method, path)

    def test_the_admin_routes_still_in_main_are_registered(self):
        for method, path in STILL_IN_MAIN:
            with self.subTest(route=f"{method} {path}"):
                self._assert_routed(method, path)


if __name__ == "__main__":
    unittest.main()
