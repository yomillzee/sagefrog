from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import api_routes  # noqa: E402


class GtmHealthStatusTests(unittest.TestCase):
    """GTM isn't in the daily sync, so the live-read endpoint must write its
    status back — otherwise the connectors card shows a stale 'connected' after
    the token dies. Only writes on a status change."""

    def _calls(self, *, ok, prev_status, err=None):
        import connector_config_store
        recorded = []
        with patch.object(connector_config_store, "update_status",
                          side_effect=lambda slug, ctype, **kw: recorded.append((slug, ctype, kw))):
            api_routes._record_gtm_health("acme", ok=ok, err=err, prev_status=prev_status)
        return recorded

    def test_failure_marks_error_with_message(self):
        calls = self._calls(ok=False, prev_status="connected", err=Exception("invalid_grant: revoked"))
        self.assertEqual(len(calls), 1)
        slug, ctype, kw = calls[0]
        self.assertEqual((slug, ctype), ("acme", "gtm"))
        self.assertEqual(kw["status"], "error")
        self.assertIn("invalid_grant", kw["error_message"])

    def test_failure_when_already_error_is_a_noop(self):
        self.assertEqual(self._calls(ok=False, prev_status="error", err=Exception("x")), [])

    def test_success_recovers_from_error(self):
        calls = self._calls(ok=True, prev_status="error")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["status"], "connected")

    def test_success_when_already_connected_is_a_noop(self):
        # No DB write on every page load when nothing changed.
        self.assertEqual(self._calls(ok=True, prev_status="connected"), [])


if __name__ == "__main__":
    unittest.main()
