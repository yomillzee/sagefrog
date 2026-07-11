from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

import not_found_page  # noqa: E402


def _fake_request(path: str, accept: str):
    return types.SimpleNamespace(
        url=types.SimpleNamespace(path=path),
        headers={"accept": accept},
    )


def _handle(path: str, accept: str, status_code: int, detail: str):
    # Imported lazily so the pure render_error_page tests still run in envs
    # without main.py's full dependency tree.
    import main

    exc = StarletteHTTPException(status_code=status_code, detail=detail)
    return asyncio.run(main.custom_http_exception_handler(_fake_request(path, accept), exc))


class ErrorPageRenderTests(unittest.TestCase):
    """Pure renderer — no heavy imports, runs everywhere."""

    def test_403_page_is_navigable_html(self) -> None:
        html = not_found_page.render_error_page(
            status_code=403, detail="You do not have access to this dashboard."
        )
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("You do not have access to this dashboard.", html)
        # A way out is always offered.
        self.assertIn('href="/dashboards"', html)
        self.assertIn('href="/login"', html)

    def test_detail_is_escaped(self) -> None:
        html = not_found_page.render_error_page(status_code=403, detail="<script>x</script>")
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_401_default_message(self) -> None:
        html = not_found_page.render_error_page(status_code=401)
        self.assertIn("Please sign in", html)


class ExceptionHandlerTests(unittest.TestCase):
    """Handler routing between HTML and JSON. Imports main (needs full deps)."""

    def test_browser_403_renders_html(self) -> None:
        resp = _handle("/dashboard/penn", "text/html", 403, "You do not have access to this dashboard.")
        self.assertIsInstance(resp, HTMLResponse)
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"/dashboards", resp.body)

    def test_browser_401_renders_html(self) -> None:
        resp = _handle("/admin", "text/html", 401, "Not signed in.")
        self.assertIsInstance(resp, HTMLResponse)
        self.assertEqual(resp.status_code, 401)

    def test_api_403_stays_json(self) -> None:
        resp = _handle("/dashboard/penn", "application/json", 403, "nope")
        self.assertIsInstance(resp, JSONResponse)
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"nope", resp.body)

    def test_api_prefix_403_stays_json(self) -> None:
        # /ga4/* is an API surface even when a browser Accept header is present.
        resp = _handle("/ga4/query", "text/html", 403, "nope")
        self.assertIsInstance(resp, JSONResponse)
        self.assertEqual(resp.status_code, 403)

    def test_other_status_stays_json(self) -> None:
        resp = _handle("/dashboard/penn", "text/html", 500, "boom")
        self.assertIsInstance(resp, JSONResponse)
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
