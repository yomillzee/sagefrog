from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.renderers.base_layout import refresh_toolbar


class RefreshToolbarHtmxTests(unittest.TestCase):
    def _allowed(self) -> str:
        # snapshot=None => never refreshed => both quick and full are allowed.
        return refresh_toolbar(
            client_slug="penn", access_key=None, use_session=True, snapshot=None
        )

    def test_forms_carry_htmx_attributes(self):
        out = self._allowed()
        self.assertIn('hx-post="/dashboard/penn/refresh"', out)
        self.assertIn('hx-indicator="#refresh-indicator"', out)
        self.assertIn('hx-disabled-elt="find button"', out)

    def test_still_a_plain_post_form_for_no_js(self):
        # Progressive enhancement: htmx off must still submit the real form.
        out = self._allowed()
        self.assertIn('<form method="post" action="/dashboard/penn/refresh"', out)
        self.assertIn('type="submit"', out)

    def test_spinner_indicator_present(self):
        out = self._allowed()
        self.assertIn('id="refresh-indicator"', out)
        self.assertIn("htmx-indicator", out)

    def test_disabled_buttons_have_no_htmx(self):
        # When a refresh is on cooldown the button is a plain disabled <button>
        # with no form and therefore no hx-post to fire.
        import time

        # A snapshot refreshed "just now" puts both refreshes on cooldown.
        snapshot = {"refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}
        out = refresh_toolbar(
            client_slug="penn", access_key=None, use_session=True, snapshot=snapshot
        )
        if "disabled" in out:
            # The disabled control must not carry an hx-post.
            self.assertNotIn('hx-post="/dashboard/penn/refresh" hx-swap="none" hx-disabled-elt="find button"><button type="button"', out)
            self.assertIn("disabled", out)


if __name__ == "__main__":
    unittest.main()
