from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import web_security  # noqa: E402


# ── unit-level checks ───────────────────────────────────────────────────────
class WebSecurityUnitTests(unittest.TestCase):
    def test_hsts_only_when_https(self) -> None:
        self.assertNotIn("Strict-Transport-Security", web_security.security_headers(https=False))
        self.assertIn("Strict-Transport-Security", web_security.security_headers(https=True))

    def test_headers_are_inline_safe(self) -> None:
        # The CSP must not restrict script/style sources — the dashboards render
        # inline scripts and styles that a script-src rule would break.
        csp = web_security.security_headers(https=True)["Content-Security-Policy"]
        self.assertNotIn("script-src", csp)
        self.assertNotIn("style-src", csp)
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("form-action 'self'", csp)

    def test_ensure_csrf_token_is_stable(self) -> None:
        session: dict = {}
        t1 = web_security.ensure_csrf_token(session)
        t2 = web_security.ensure_csrf_token(session)
        self.assertTrue(t1)
        self.assertEqual(t1, t2)
        self.assertEqual(session[web_security.CSRF_SESSION_KEY], t1)

    def test_tokens_match(self) -> None:
        self.assertTrue(web_security.tokens_match("abc", "abc"))
        self.assertFalse(web_security.tokens_match("abc", "abd"))
        self.assertFalse(web_security.tokens_match("", "x"))
        self.assertFalse(web_security.tokens_match("x", None))

    def test_inject_targets_post_forms_only(self) -> None:
        html = (
            "<html><head></head><body>"
            '<form method="post" action="/a"></form>'
            "<FORM  METHOD=POST></FORM>"
            '<form method="get"></form>'
            "</body></html>"
        )
        out = web_security.inject_csrf_html(html, "TOK")
        self.assertEqual(out.count('name="csrf_token" value="TOK"'), 2)
        self.assertIn('name="csrf-token" content="TOK"', out)
        # meta lands inside <head>
        self.assertTrue(re.search(r"<head\b[^>]*><meta name=\"csrf-token\"", out))


# ── integration through the real middleware stack ───────────────────────────
def _build_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_html_extras(request: Request, call_next):
        response = await call_next(request)
        if "text/html" not in (response.headers.get("content-type") or "").lower():
            return response
        token = None
        if "session" in request.scope:
            token = web_security.ensure_csrf_token(request.session)
        if not token:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        text = web_security.inject_csrf_html(body.decode(response.charset or "utf-8"), token)
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-encoding")
        }
        return Response(content=text, status_code=response.status_code, headers=headers,
                        media_type="text/html")

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        web_security.apply_security_headers(response, https=request.url.scheme == "https")
        return response

    @app.middleware("http")
    async def _csrf_protect(request: Request, call_next):
        if web_security.requires_csrf(request):
            if not await web_security.validate_csrf(request):
                return Response("CSRF verification failed.", status_code=403)
        return await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key="t", session_cookie="eos_session")

    @app.get("/page", response_class=HTMLResponse)
    def page():
        return HTMLResponse(
            "<html><head></head><body>"
            '<form method="post" action="/submit"><input name="v"></form>'
            "</body></html>"
        )

    @app.post("/submit")
    def submit(v: str = Form(...)):
        return JSONResponse({"v": v})

    @app.post("/upload")
    async def upload(note: str = Form(...), f: UploadFile = File(...)):
        return JSONResponse({"note": note, "bytes": len(await f.read())})

    @app.post("/api")
    async def api(request: Request):
        return JSONResponse({"got": await request.json()})

    @app.post("/ga4/query")
    async def ga4(request: Request):
        return JSONResponse({"len": len(await request.body())})

    return app


class WebSecurityMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _build_app()

    def _token(self, client: TestClient) -> str:
        html = client.get("/page").text
        return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    def test_security_headers_applied(self) -> None:
        c = TestClient(self.app)
        r = c.get("/page")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", r.headers["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", r.headers)

    def test_hsts_over_https(self) -> None:
        c = TestClient(self.app, base_url="https://testserver")
        self.assertTrue(c.get("/page").headers["Strict-Transport-Security"].startswith("max-age="))

    def test_form_post_enforced_and_body_replayed(self) -> None:
        c = TestClient(self.app)
        token = self._token(c)
        self.assertEqual(c.post("/submit", data={"v": "x"}).status_code, 403)
        r = c.post("/submit", data={"v": "x", "csrf_token": token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"v": "x"})
        self.assertEqual(c.post("/submit", data={"v": "x", "csrf_token": "bad"}).status_code, 403)

    def test_multipart_body_replayed(self) -> None:
        c = TestClient(self.app)
        token = self._token(c)
        r = c.post(
            "/upload",
            data={"note": "n", "csrf_token": token},
            files={"f": ("a.txt", b"hello", "text/plain")},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"note": "n", "bytes": 5})

    def test_ajax_header_token(self) -> None:
        c = TestClient(self.app)
        token = self._token(c)
        self.assertEqual(c.post("/api", json={"a": 1}).status_code, 403)
        r = c.post("/api", json={"a": 1}, headers={"X-CSRF-Token": token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"got": {"a": 1}})

    def test_api_credentials_bypass(self) -> None:
        c = TestClient(self.app)
        c.get("/page")  # establish session cookie
        self.assertEqual(
            c.post("/ga4/query", content=b"body", headers={"X-API-Key": "k"}).status_code, 200
        )
        self.assertEqual(c.post("/ga4/query?key=legacy", content=b"body").status_code, 200)

    def test_no_session_bypasses(self) -> None:
        c = TestClient(self.app)
        self.assertEqual(c.post("/api", json={"a": 1}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
