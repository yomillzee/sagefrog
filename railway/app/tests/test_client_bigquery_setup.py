from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_bigquery_setup  # noqa: E402


class _Dataset:
    def __init__(self, dataset_id: str):
        self.dataset_id = dataset_id
        self.location = None


class _Table:
    def __init__(self, table_id: str, schema=None):
        self.table_id = table_id
        self.schema = schema
        self.expires = None


class _SchemaField:
    def __init__(self, name: str, field_type: str, mode: str | None = None):
        self.name = name
        self.field_type = field_type
        self.mode = mode


class _QueryJob:
    def result(self, max_results=None):
        return [SimpleNamespace(ok=True)]


class _Client:
    def __init__(self):
        self.datasets: dict[str, object] = {
            "client-project.analytics_123": SimpleNamespace(location="US")
        }
        self.created_tables: list[str] = []
        self.deleted_tables: list[str] = []

    def query(self, sql, job_config=None):
        return _QueryJob()

    def create_dataset(self, dataset, exists_ok=False):
        self.datasets.setdefault(
            dataset.dataset_id, SimpleNamespace(location=dataset.location)
        )

    def get_dataset(self, dataset_id):
        return self.datasets[dataset_id]

    def create_table(self, table):
        self.created_tables.append(table.table_id)

    def delete_table(self, table_id, not_found_ok=False):
        self.deleted_tables.append(table_id)


class ClientBigQuerySetupTests(unittest.TestCase):
    def test_creates_and_write_checks_required_client_datasets(self) -> None:
        client = _Client()
        build_calls = []
        fake_bq = types.ModuleType("google.cloud.bigquery")
        fake_bq.Dataset = _Dataset
        fake_bq.Table = _Table
        fake_bq.SchemaField = _SchemaField
        google = types.ModuleType("google")
        google.__path__ = []
        cloud = types.ModuleType("google.cloud")
        cloud.__path__ = []
        cloud.bigquery = fake_bq

        fake_service = types.ModuleType("bigquery_service")
        fake_service.make_job_config = lambda: object()

        def build_client(project_id, *, credentials_info=None):
            build_calls.append((project_id, credentials_info))
            return client

        fake_service.build_client = build_client
        fake_clients = types.ModuleType("ga4_clients")
        fake_clients.resolve_client_config = lambda client_key: SimpleNamespace(
            bq_project_id="client-project",
            bq_dataset_id="analytics_123",
            credentials={"client_email": "dashboard@example.test"},
            credentials_env="GCP_CREDS_CLIENT",
        )

        with patch.dict(
            sys.modules,
            {
                "google": google,
                "google.cloud": cloud,
                "google.cloud.bigquery": fake_bq,
                "bigquery_service": fake_service,
                "ga4_clients": fake_clients,
            },
        ):
            result = client_bigquery_setup.ensure_client_bigquery(
                client_key="client",
                needs_linkedin=True,
                needs_meta=True,
                needs_mart=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(build_calls[0][0], "client-project")
        self.assertEqual(len(result["datasets"]), 3)
        self.assertEqual(len(client.created_tables), 3)
        self.assertEqual(client.created_tables, client.deleted_tables)


if __name__ == "__main__":
    unittest.main()
