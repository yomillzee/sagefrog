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

import bigquery_warehouse  # noqa: E402


class BigQueryClientRoutingTests(unittest.TestCase):
    def test_meta_reprocessing_uses_stable_merge_key(self) -> None:
        queries: list[str] = []

        class Job:
            def result(self):
                return None

        class Client:
            def load_table_from_json(self, payload, table_id, job_config=None):
                return Job()

            def query(self, sql):
                queries.append(sql)
                return Job()

            def delete_table(self, table_id, not_found_ok=False):
                return None

        fake_bq = SimpleNamespace(LoadJobConfig=lambda **kwargs: object())
        rows = [{
            "campaign_id": "c1", "campaign_name": "Campaign",
            "metric_date": "2026-06-22", "spend": 1,
            "clicks": 2, "impressions": 3, "conversions": 4,
        }]
        with patch.object(bigquery_warehouse, "_ensure_meta_table", return_value="p.raw_meta_ads.campaign_daily"), patch.object(
            bigquery_warehouse, "_meta_client", return_value=Client()
        ), patch.object(bigquery_warehouse, "_bigquery", return_value=fake_bq), patch.object(
            bigquery_warehouse, "_meta_campaign_daily_schema", return_value=[]
        ):
            bigquery_warehouse.mirror_meta_campaign_daily_batch("a1", rows)
            bigquery_warehouse.mirror_meta_campaign_daily_batch("a1", rows)

        self.assertEqual(len(queries), 2)
        self.assertTrue(all("MERGE `p.raw_meta_ads.campaign_daily`" in sql for sql in queries))
        self.assertTrue(all(
            "T.account_id = S.account_id AND T.campaign_id = S.campaign_id AND T.metric_date = S.metric_date" in sql
            for sql in queries
        ))

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
                google_dataset_id="client_google",
                linkedin_dataset_id="client_linkedin",
                meta_dataset_id="client_meta",
                mart_dataset_id="client_marts",
            ):
                self.assertEqual(bigquery_warehouse._linkedin_project_id(), "client-project")
                self.assertEqual(bigquery_warehouse._meta_project_id(), "client-project")
                self.assertEqual(bigquery_warehouse._dataset_id("linkedin"), "client_linkedin")
                self.assertEqual(bigquery_warehouse._dataset_id("google"), "client_google")
                self.assertEqual(bigquery_warehouse._meta_dataset_id(), "client_meta")
                self.assertEqual(bigquery_warehouse._mart_dataset_id(), "client_marts")
                bigquery_warehouse._client("client-project")

        self.assertEqual(calls, [("client-project", "GCP_CREDS_CLIENT")])

    def test_native_google_rows_are_normalized_without_api_fallback(self) -> None:
        queries: list[str] = []

        class Client:
            def create_dataset(self, dataset, exists_ok=False):
                return None

            def get_table(self, table_id):
                if table_id.endswith("fact_google_ads_campaign_daily"):
                    raise RuntimeError("not found")
                return SimpleNamespace(table_id=table_id)

            def query(self, sql):
                queries.append(sql)
                return SimpleNamespace(result=lambda: None)

        fake_bq = SimpleNamespace(Dataset=lambda dataset_id: dataset_id)
        with patch.object(bigquery_warehouse, "_client", return_value=Client()), patch.object(
            bigquery_warehouse, "_bigquery", return_value=fake_bq
        ):
            with bigquery_warehouse.route(
                bq_project_id="client-project",
                google_dataset_id="client_google",
                mart_dataset_id="client_marts",
            ):
                result = bigquery_warehouse.create_google_campaign_mart_view()

        self.assertEqual(result["status"], "success")
        self.assertIn("client-project.client_marts.fact_google_ads_campaign_daily", queries[0])
        self.assertIn("client-project.client_google.campaign_daily", queries[0])

    def test_missing_native_google_table_does_not_create_a_fact(self) -> None:
        class Client:
            def create_dataset(self, dataset, exists_ok=False):
                return None

            def get_table(self, table_id):
                raise RuntimeError("not found")

            def query(self, sql):
                raise AssertionError("No fact query should run without native data")

        fake_bq = SimpleNamespace(Dataset=lambda dataset_id: dataset_id)
        with patch.object(bigquery_warehouse, "_client", return_value=Client()), patch.object(
            bigquery_warehouse, "_bigquery", return_value=fake_bq
        ), bigquery_warehouse.route(
            bq_project_id="client-project",
            google_dataset_id="raw_google_ads",
            mart_dataset_id="marketing_marts",
        ):
            result = bigquery_warehouse.create_google_campaign_mart_view()

        self.assertEqual(result["status"], "pending_data")


if __name__ == "__main__":
    unittest.main()
