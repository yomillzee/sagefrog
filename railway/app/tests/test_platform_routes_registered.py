from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

# The platform endpoints moved out of main.py into the platforms/ package. They
# are not internal pages: the Custom GPT action is configured against the
# OpenAPI document this app generates, so a route going missing, losing its
# response model, or quietly shedding its API-key dependency is a change to a
# published contract — and none of those show up as a failing page.
#
# So this checks the generated spec rather than just whether requests route:
# every platform path is present, every operation still declares a response
# schema, and every one still requires the API key.

PLATFORM_PREFIXES = (
    "/google-ads",
    "/linkedin",
    "/meta",
    "/indeed",
    "/ga4",
    "/warehouse",
)

# Route count per prefix at the time of the move. A drop means something is no
# longer registered; a rise is fine and should be reflected here.
EXPECTED_MINIMUM = {
    "/google-ads": 9,
    "/linkedin": 9,
    "/meta": 8,
    "/indeed": 5,
    "/ga4": 4,
    "/warehouse": 2,
}


def _platform_operations(spec):
    """(path, method, operation) for every platform endpoint in the spec."""
    for path, methods in spec.get("paths", {}).items():
        if not path.startswith(PLATFORM_PREFIXES):
            continue
        for method, operation in methods.items():
            if method.lower() in {"get", "post", "put", "delete", "patch"}:
                yield path, method.lower(), operation


class PlatformRoutesAreRegisteredTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = main.app.openapi()
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    def test_every_platform_prefix_still_has_its_routes(self):
        counts = dict.fromkeys(PLATFORM_PREFIXES, 0)
        for path, _method, _op in _platform_operations(self.spec):
            for prefix in PLATFORM_PREFIXES:
                if path.startswith(prefix):
                    counts[prefix] += 1
                    break
        for prefix, expected in EXPECTED_MINIMUM.items():
            with self.subTest(prefix=prefix):
                self.assertGreaterEqual(
                    counts[prefix],
                    expected,
                    f"{prefix} has {counts[prefix]} operations in the OpenAPI "
                    f"document, expected at least {expected} — check that its "
                    "router is included in platforms/__init__.py",
                )

    def test_every_platform_operation_still_requires_the_api_key(self):
        """These are public endpoints; the key is the only thing in front of
        live client marketing data."""
        unguarded = [
            f"{method.upper()} {path}"
            for path, method, op in _platform_operations(self.spec)
            if not op.get("security")
        ]
        self.assertEqual(
            unguarded,
            [],
            "These platform operations no longer declare an API-key "
            "requirement in the OpenAPI document:\n  " + "\n  ".join(unguarded),
        )

    def test_every_platform_operation_still_declares_a_response_schema(self):
        """The Custom GPT is configured against this document; an operation
        without a response schema is one it can no longer interpret."""
        missing = [
            f"{method.upper()} {path}"
            for path, method, op in _platform_operations(self.spec)
            if "200" not in op.get("responses", {})
        ]
        self.assertEqual(missing, [], "Operations with no documented 200 response:\n  " + "\n  ".join(missing))

    def test_the_endpoints_actually_route(self):
        """The spec is generated from the same objects, so it agrees with itself
        by construction. Ask the app to route one endpoint per platform as well."""
        for path, method, _op in _platform_operations(self.spec):
            if "{" in path:  # skip templated paths; one concrete probe per prefix is enough
                continue
            with self.subTest(route=f"{method.upper()} {path}"):
                code = self.client.request(method, path, follow_redirects=False).status_code
                self.assertNotIn(
                    code,
                    (404, 405),
                    f"{method.upper()} {path} answered {code}: nothing is routed there.",
                )


if __name__ == "__main__":
    unittest.main()
