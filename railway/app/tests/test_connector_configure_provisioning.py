from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import connector_routes  # noqa: E402


class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


class _FakeHandler:
    oauth_platform = "ga4"
    default_raw_dataset = "raw_ga4"
    default_mart_dataset = "marketing_marts"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class ConnectorConfigureProvisioningTests(unittest.TestCase):
    """The destination step must provision + verify BigQuery up front so a
    missing/inaccessible GCP project fails fast in the wizard, rather than
    silently deferring to the first sync."""

    def _call(self, body):
        with patch.object(connector_routes, "validate_client_slug", side_effect=lambda s: s), \
             patch.object(connector_routes, "get_handler", return_value=_FakeHandler()), \
             patch.object(connector_routes, "_auth", return_value=(None, "k", True, "a@b.com", True)), \
             patch.object(connector_routes.connector_config_store, "upsert_config", return_value=None), \
             patch.object(connector_routes.client_dashboard_config, "get_config",
                          return_value=types.SimpleNamespace(
                              label="Test Co", gcp_project_id="test-proj",
                              google_customer_id=None, linkedin_account_id=None,
                              meta_account_id=None, ga4_client_key=None)):
            return _run(connector_routes.connector_configure(
                client_slug="test", connector_type="ga4",
                request=_FakeRequest(body)))

    def test_destination_step_provisions_and_returns_ok(self) -> None:
        fake_setup = types.SimpleNamespace(
            ensure_client_datasets=lambda *, project_id: {"ok": True, "datasets": []},
        )
        with patch.dict(sys.modules, {"client_bigquery_setup": fake_setup}):
            resp = self._call({"bq_project_id": "test-proj", "raw_dataset_id": "raw_ga4",
                               "mart_dataset_id": "marketing_marts"})
        import json
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.body)["ok"])

    def test_provision_failure_returns_actionable_error(self) -> None:
        def _boom(*, project_id):
            raise RuntimeError("404 Not found: Project bad-proj")
        fake_setup = types.SimpleNamespace(ensure_client_datasets=_boom)
        with patch.dict(sys.modules, {"client_bigquery_setup": fake_setup}):
            resp = self._call({"bq_project_id": "bad-proj", "raw_dataset_id": "raw_ga4",
                               "mart_dataset_id": "marketing_marts"})
        import json
        self.assertEqual(resp.status_code, 400)
        payload = json.loads(resp.body)
        self.assertFalse(payload["ok"])
        self.assertIn("bad-proj", payload["error"])
        self.assertIn("marketing-data-reader@sagefrog.iam.gserviceaccount.com", payload["error"])

    def test_non_destination_configure_does_not_provision(self) -> None:
        # A configure call with no bq_project_id (e.g. the backfill step) must
        # NOT trigger a provisioning round-trip.
        called = {"n": 0}

        def _count(*, project_id):
            called["n"] += 1
            return {"ok": True}
        fake_setup = types.SimpleNamespace(ensure_client_datasets=_count)
        with patch.dict(sys.modules, {"client_bigquery_setup": fake_setup}):
            resp = self._call({"backfill_days": 90})
        import json
        self.assertTrue(json.loads(resp.body)["ok"])
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
