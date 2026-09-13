from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.assets import shell_css, shell_css_url  # noqa: E402
from dashboard.renderers.base_layout import (  # noqa: E402
    SIDEBAR_CSS,
    render_admin_shell_page,
    render_client_shell_page,
)
from dashboard.renderers.bigquery_settings_renderer import (  # noqa: E402
    render_bigquery_settings_page,
)

# The dashboard's CSS and JS moved behind cached /assets URLs; every *other*
# page — Settings, Connectors, Client Hours, Benchmarks, the admin pages — kept
# inlining SIDEBAR_CSS, ~37 KB of identical chrome re-sent on every load. It is
# a <link> now, and these tests pin what made that safe: no page inlines it any
# more, every page links the same URL, the URL follows the bytes, and a page's
# own CSS still lands after the chrome so its overrides still win.

EXTRA_CSS = "\n    .page-thing { color: red; }\n"
SIDEBAR_RULE = ".dash-sidebar {"


def _client_shell(**kwargs) -> str:
    base = {
        "client_slug": "demo",
        "label": "Demo",
        "active_nav": "files",
        "page_title": "Files",
        "page_subtitle": "",
        "content_html": "<main>hi</main>",
        "use_session": True,
        "session_email": "t@e.com",
    }
    base.update(kwargs)
    return render_client_shell_page(**base)


def _admin_shell(**kwargs) -> str:
    base = {
        "active_nav": "clients",
        "page_title": "Clients",
        "content_html": "<main>hi</main>",
        "session_email": "t@e.com",
    }
    base.update(kwargs)
    return render_admin_shell_page(**base)


def _settings() -> str:
    return render_bigquery_settings_page(
        use_session=True, session_email="t@e.com",
        client_slug="demo", api_client_key="demo", label="Demo",
    )


def _pages() -> dict[str, str]:
    return {
        "client shell": _client_shell(extra_css=EXTRA_CSS),
        "client shell (no page CSS)": _client_shell(),
        "admin shell": _admin_shell(extra_css=EXTRA_CSS),
        "settings": _settings(),
    }


class ShellStylesheetTest(unittest.TestCase):
    def test_the_body_is_the_shared_chrome(self):
        self.assertEqual(shell_css()[1], SIDEBAR_CSS)

    def test_it_carries_the_footer_and_scrollbar_rules(self):
        """base_layout appends both to SIDEBAR_CSS at the end of its module
        body. Reading the constant too early would serve it half-built."""
        _, css = shell_css()
        self.assertIn(".site-footer {", css)
        self.assertIn("scrollbar-color:", css)

    def test_the_url_carries_a_digest_of_the_content(self):
        import hashlib

        digest, css = shell_css()
        self.assertRegex(shell_css_url(), r"^/assets/shell-[0-9a-f]{12}\.css$")
        self.assertEqual(digest, hashlib.sha256(css.encode("utf-8")).hexdigest()[:12])


class RenderedPagesTest(unittest.TestCase):
    def test_no_page_inlines_the_chrome_any_more(self):
        for name, html in _pages().items():
            with self.subTest(page=name):
                self.assertIn(f'<link rel="stylesheet" href="{shell_css_url()}">', html)
                self.assertNotIn(SIDEBAR_RULE, html)

    def test_every_page_links_the_same_url(self):
        """One URL across pages and clients is the whole point — a second one
        would mean a second 37 KB download."""
        urls = {
            re.search(r'href="(/assets/shell-[0-9a-f]{12}\.css)"', html).group(1)
            for html in (
                *_pages().values(),
                _client_shell(client_slug="other", label="Other", session_is_admin=True),
                _admin_shell(active_nav="users", page_title="Users"),
            )
        }
        self.assertEqual(len(urls), 1, f"pages disagree on the stylesheet URL: {urls}")

    def test_a_page_keeps_its_own_css_after_the_chrome(self):
        """Document order is what the cascade reads. The page's rules sat after
        SIDEBAR_CSS in the old single block, so they have to stay after the
        link — otherwise a page that deliberately overrides the sidebar loses."""
        for name, html in (("client shell", _client_shell(extra_css=EXTRA_CSS)),
                           ("admin shell", _admin_shell(extra_css=EXTRA_CSS))):
            with self.subTest(page=name):
                self.assertIn(EXTRA_CSS.strip(), html)
                self.assertLess(html.index(shell_css_url()), html.index(EXTRA_CSS.strip()))

    def test_a_page_without_its_own_css_gets_no_empty_block(self):
        self.assertNotIn("<style></style>", _client_shell())


class AssetRouteTest(unittest.TestCase):
    def test_it_is_served_immutable_from_the_digest_url(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from dashboard.routes.asset_routes import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        digest, css = shell_css()
        res = client.get(shell_css_url())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, css)
        self.assertEqual(res.headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(res.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(res.headers["etag"], f'"{digest}"')

    def test_a_stale_digest_still_gets_the_current_chrome(self):
        """A page served just before a deploy asks for the old URL. Answering it
        is better than a 404 that leaves that page unstyled."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from dashboard.routes.asset_routes import router

        app = FastAPI()
        app.include_router(router)
        res = TestClient(app).get("/assets/shell-000000000000.css")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, shell_css()[1])


if __name__ == "__main__":
    unittest.main()
