from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bigquery_warehouse  # noqa: E402


class BigQueryClientRoutingTests(unittest.TestCase):
    def test_route_overrides_project_datasets_and_credentials(self) -> None:
        calls: list[tuple[str, str | None]] = []
        fake_service = types.ModuleType("bigquery_service")

        def build_client(project_id, *, credentials_env=None):
            calls.append((project_id, credentials_env))
            return object()

        fake_service.build_client = build_client

        with patch.dict(sys.modules, {"bigquery_service": fake_service}):
            with bigquery_warehouse.route(
                bq_project_id="client-project",
                credentials_env="GCP_CREDS_CLIENT",
                linkedin_dataset_id="client_linkedin",
                meta_dataset_id="client_meta",
                mart_dataset_id="client_marts",
            ):
                self.assertEqual(bigquery_warehouse._linkedin_project_id(), "client-project")
                self.assertEqual(bigquery_warehouse._meta_project_id(), "client-project")
                self.assertEqual(bigquery_warehouse._dataset_id("linkedin"), "client_linkedin")
                self.assertEqual(bigquery_warehouse._meta_dataset_id(), "client_meta")
                self.assertEqual(bigquery_warehouse._mart_dataset_id(), "client_marts")
                bigquery_warehouse._client("client-project")

        self.assertEqual(calls, [("client-project", "GCP_CREDS_CLIENT")])


if __name__ == "__main__":
    unittest.main()
