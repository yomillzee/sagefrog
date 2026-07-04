from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import main  # noqa: E402


class AdminConvertDashboardModeTests(unittest.TestCase):
    def test_convert_sets_bigquery_nixon_and_preserves_fields(self) -> None:
        saved = {}
        existing = types.SimpleNamespace(
            label="Old Co", google_customer_id="123", linkedin_account_id="li1",
            meta_account_id="m1", ga4_client_key="oldco",
        )
        import client_dashboard_config as cdc

        with patch.object(cdc, "get_config", return_value=existing), \
             patch.object(cdc, "save_config", side_effect=lambda slug, **kw: saved.update({"slug": slug, **kw})), \
             patch.object(main.audit_log, "request_context", return_value={}), \
             patch.object(main.audit_log, "record"):
            resp = main.admin_convert_dashboard_mode(
                client_slug="OldCo", request=types.SimpleNamespace(),
                user=types.SimpleNamespace(email="admin@x.com", role="admin"),
            )

        self.assertEqual(resp.status_code, 303)
        self.assertIn("/admin", resp.headers["location"])
        self.assertEqual(saved["slug"], "oldco")
        self.assertEqual(saved["dashboard_mode"], "bigquery_nixon")
        # Other fields preserved, not wiped.
        self.assertEqual(saved["label"], "Old Co")
        self.assertEqual(saved["google_customer_id"], "123")
        self.assertEqual(saved["ga4_client_key"], "oldco")

    def test_convert_failure_redirects_with_message(self) -> None:
        import client_dashboard_config as cdc

        with patch.object(cdc, "get_config", side_effect=RuntimeError("db down")), \
             patch.object(main.audit_log, "request_context", return_value={}):
            resp = main.admin_convert_dashboard_mode(
                client_slug="oldco", request=types.SimpleNamespace(),
                user=types.SimpleNamespace(email="admin@x.com", role="admin"),
            )

        self.assertEqual(resp.status_code, 303)
        self.assertIn("Convert+failed", resp.headers["location"])


if __name__ == "__main__":
    unittest.main()
