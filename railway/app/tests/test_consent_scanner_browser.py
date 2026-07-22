"""Browser-backed regression tests for banner detection in consent_scanner.

These launch the real Chromium the scanner drives and assert that a consent
banner is still detected when it is rendered inside an open shadow root (the
pattern CookieYes uses when injected via Google Tag Manager) or a same-origin
iframe — the cases a plain ``document.querySelector`` misses. Skipped when
Playwright or its Chromium binary is unavailable, so the pure suite stays
hermetic (mirrors ``test_consent_store``, which requires a Postgres URL)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import consent_scanner as sc  # noqa: E402

_BROWSER_READY = sc.playwright_available() and bool(sc._chromium_executable())


# A CookieYes-style banner inside an OPEN shadow root, as GTM injection often
# produces. The reject/accept controls are <a data-cky-tag=...> rather than
# <button>, which is why the selectors are element-agnostic.
_SHADOW_HTML = """
<!doctype html><html><body><h1>Site</h1><div id="host"></div>
<script>
  const sr = document.getElementById('host').attachShadow({mode:'open'});
  sr.innerHTML = `<div class="cky-consent-container"
      style="position:fixed;bottom:0;left:0;width:100%;height:120px;background:#fff">
    <p>We use cookies to improve your experience. See our privacy policy.</p>
    <a data-cky-tag="reject-button" role="button" href="#">Reject All</a>
    <a data-cky-tag="accept-button" role="button" href="#">Accept All</a></div>`;
</script></body></html>
"""

_NO_BANNER_HTML = """
<!doctype html><html><body><h1>Just a site</h1>
<p>No consent UI here whatsoever.</p></body></html>
"""


@unittest.skipUnless(_BROWSER_READY, "requires Playwright + a Chromium binary")
class BannerDetectionBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(**sc._launch_kwargs())

    @classmethod
    def tearDownClass(cls):
        try:
            cls._browser.close()
        finally:
            cls._pw.stop()

    def _detect(self, html: str):
        ctx = self._browser.new_context()
        try:
            page = ctx.new_page()
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(200)
            signals = page.evaluate(sc._SIGNAL_JS) or {}
            reject = sc._try_click(page, list(sc.DEFAULT_REJECT_SELECTORS), sc.REJECT_TEXTS)
            return signals, reject
        finally:
            ctx.close()

    def test_shadow_dom_banner_is_detected(self):
        signals, reject = self._detect(_SHADOW_HTML)
        self.assertTrue(signals.get("banner_detected"),
                        "shadow-DOM CookieYes banner should be detected")
        # And the shadow-DOM reject control is reachable/clickable.
        self.assertTrue(reject.get("clicked"),
                        "reject control inside the shadow root should be clickable")

    def test_no_banner_stays_false(self):
        signals, _ = self._detect(_NO_BANNER_HTML)
        self.assertFalse(signals.get("banner_detected"),
                         "a page with no consent UI must not be flagged as having a banner")


if __name__ == "__main__":
    unittest.main()
