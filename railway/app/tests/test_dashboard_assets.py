from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.assets import (  # noqa: E402
    CSS_DIR,
    JS_DIR,
    dashboard_css,
    dashboard_css_url,
    dashboard_js,
    dashboard_js_url,
)
from dashboard.renderers.bigquery_dashboard_renderer import (  # noqa: E402
    render_bigquery_dashboard_page,
)

# The dashboard's CSS and JS used to be a ~960-line <style> block and a
# ~5,400-line <script> block inside the renderer's f-string — 90% of every page.
# They now live in static/css/ and static/js/, stitched back together with the
# CSS and pane scripts other renderers own, and served from digest URLs so they
# can be cached for a year.
#
# These tests pin the properties that made that move safe: the page carries no
# inline styles and links both assets, each composed bundle still contains every
# part in the original order, nothing per-request leaks into the cached bytes
# (which is what lets every client share one copy), and the URL changes whenever
# the content does.


def _render(**kwargs) -> str:
    base = dict(
        client_slug="demo",
        api_client_key="demo",
        label="Demo",
        use_session=True,
        session_email="t@e.com",
    )
    base.update(kwargs)
    return render_bigquery_dashboard_page(**base)


class ComposedStylesheetTest(unittest.TestCase):
    def test_every_part_file_is_present_in_cascade_order(self):
        """A missing or reordered part would change what the cascade resolves
        to, which is exactly what this refactor promised not to do."""
        _, css = dashboard_css()
        positions = []
        for stem in ("dashboard-base", "dashboard-layout", "dashboard-charts", "dashboard-panels"):
            body = (CSS_DIR / f"{stem}.css").read_text(encoding="utf-8")
            head = body.strip().splitlines()[0].strip()
            self.assertIn(head, css, f"{stem}.css is missing from the composed sheet")
            positions.append(css.index(head))
        self.assertEqual(positions, sorted(positions), "part files are out of cascade order")

    def test_the_shared_fragments_are_spliced_in(self):
        """SIDEBAR_CSS and the pane styles stay in Python because other
        renderers import them; the dashboard has to pick them up from there."""
        from dashboard.renderers import (
            budget_tracker,
            google_business_renderer,
            pagespeed_renderer,
        )
        from dashboard.renderers.base_layout import SIDEBAR_CSS

        _, css = dashboard_css()
        for label, frag in (
            ("SIDEBAR_CSS", SIDEBAR_CSS),
            ("budget", budget_tracker.css()),
            ("pagespeed pane", pagespeed_renderer.pane_css()),
            ("google business pane", google_business_renderer.pane_css()),
        ):
            self.assertIn(frag.strip()[:60], css, f"{label} CSS is missing")

    def test_the_url_carries_a_digest_of_the_content(self):
        digest, css = dashboard_css()
        self.assertRegex(dashboard_css_url(), r"^/assets/dashboard-[0-9a-f]{12}\.css$")
        self.assertIn(digest, dashboard_css_url())
        # The digest has to follow the bytes, or a deploy would serve stale CSS
        # from a year-long cache.
        import hashlib

        self.assertEqual(digest, hashlib.sha256(css.encode("utf-8")).hexdigest()[:12])


class ComposedScriptTest(unittest.TestCase):
    def test_both_part_files_are_present_in_order(self):
        _, js = dashboard_js()
        positions = []
        for stem in ("dashboard-shell", "dashboard-panels"):
            head = (JS_DIR / f"{stem}.js").read_text(encoding="utf-8").strip().splitlines()[0].strip()
            self.assertIn(head, js, f"{stem}.js is missing from the composed script")
            positions.append(js.index(head))
        self.assertEqual(positions, sorted(positions), "part files are out of order")

    def test_the_panes_are_spliced_between_them(self):
        """The panes read constants the preamble defines and call helpers the
        shell defines, so they have to land between shell and panels."""
        from dashboard.renderers import google_business_renderer, pagespeed_renderer

        _, js = dashboard_js()
        shell = (JS_DIR / "dashboard-shell.js").read_text(encoding="utf-8")
        panels = (JS_DIR / "dashboard-panels.js").read_text(encoding="utf-8")
        for pane in (pagespeed_renderer.pane_js(), google_business_renderer.pane_js()):
            self.assertIn(pane, js)
            self.assertLess(js.index(shell.strip()[:60]), js.index(pane.strip()[:60]))
            self.assertLess(js.index(pane.strip()[:60]), js.index(panels.strip()[:60]))

    def test_no_per_request_value_is_baked_into_the_cached_script(self):
        """This is what makes one cached copy correct for every client. The
        values that do vary live in the inline preamble the page emits first."""
        _, js = dashboard_js()
        for leaked in ("ce_verified_ke_", "ce_conv_action_", "EXPLORER_FILTERS_API =",
                       "let currentStart=", "EXPLORER_FILTER_GROUPS ="):
            self.assertNotIn(leaked, js, f"{leaked!r} is per-request and must stay in the preamble")

    def test_the_url_carries_a_digest_of_the_content(self):
        import hashlib

        digest, js = dashboard_js()
        self.assertRegex(dashboard_js_url(), r"^/assets/dashboard-[0-9a-f]{12}\.js$")
        self.assertEqual(digest, hashlib.sha256(js.encode("utf-8")).hexdigest()[:12])


class RenderedPageTest(unittest.TestCase):
    def test_the_page_links_the_stylesheet_and_inlines_none_of_it(self):
        html = _render()
        self.assertIn(f'<link rel="stylesheet" href="{dashboard_css_url()}">', html)
        self.assertIn(f'<script src="{dashboard_js_url()}"></script>', html)
        self.assertNotIn("<style", html)

    def test_every_client_gets_the_same_stylesheet_url(self):
        """One URL across clients is what makes the cache worth having — including
        for clients without budget tracking, whose CSS used to differ."""
        a = _render(client_slug="demo", api_client_key="demo")
        b = _render(client_slug="other", api_client_key="other", session_is_admin=True)
        for pat in (r'href="(/assets/dashboard-[0-9a-f]{12}\.css)"',
                    r'src="(/assets/dashboard-[0-9a-f]{12}\.js)"'):
            self.assertEqual(re.search(pat, a).group(1), re.search(pat, b).group(1))


if __name__ == "__main__":
    unittest.main()
