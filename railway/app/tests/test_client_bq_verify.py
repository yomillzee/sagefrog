from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import api_routes  # noqa: E402


class ClientBqVerifyTests(unittest.TestCase):
    def _call(self):
        return api_routes.client_bq_verify(
            client_key="acme", request=types.SimpleNamespace(),
            key=None, bearer_credentials=None, x_api_key=None,
        )

    def test_ok_when_access_verified(self):
        fake_setup = types.SimpleNamespace(
            ensure_client_datasets=lambda *, project_id: {"ok": True, "datasets": ["acme-proj.marketing_marts"]},
        )
        with patch.object(api_routes, "_load_bq_test_config", return_value=("acme-proj", "marketing_marts")), \
             patch.object(api_routes, "_authorize_bq_client_api", return_value=None), \
             patch.dict(sys.modules, {"client_bigquery_setup": fake_setup}):
            out = self._call()
        self.assertTrue(out["ok"])
        self.assertEqual(out["project_id"], "acme-proj")
        self.assertIn("verified", out["message"].lower())

    def test_reports_actionable_error_on_access_failure(self):
        def _boom(*, project_id):
            raise RuntimeError("403 Access Denied: bigquery.jobs.create")
        fake_setup = types.SimpleNamespace(ensure_client_datasets=_boom)
        with patch.object(api_routes, "_load_bq_test_config", return_value=("acme-proj", "marketing_marts")), \
             patch.object(api_routes, "_authorize_bq_client_api", return_value=None), \
             patch.dict(sys.modules, {"client_bigquery_setup": fake_setup}):
            out = self._call()
        self.assertFalse(out["ok"])
        self.assertIn("BigQuery Data Editor", out["error"])
        self.assertIn("BigQuery Job User", out["error"])


if __name__ == "__main__":
    unittest.main()
