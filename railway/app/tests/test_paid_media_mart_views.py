from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bigquery_warehouse  # noqa: E402


class _FakeTable:
    def __init__(self, table_type="TABLE"):
        self.table_type = table_type


class _FakeJob:
    def result(self):
        return None


class _FakeBQClient:
    """Records queries; get_table raises for datasets not in `present`."""

    def __init__(self, present_raw_datasets):
        self.present = set(present_raw_datasets)
        self.queries: list[str] = []
        self.created_datasets: list[str] = []
        self.deleted: list[str] = []

    def create_dataset(self, ds, exists_ok=False):
        self.created_datasets.append(str(ds))

    def get_table(self, table_id):
        # raw tables live in "<proj>.<dataset>.campaign_daily"; the mart
        # views/tables don't exist yet in these tests.
        for ds in self.present:
            if f".{ds}.campaign_daily" in table_id:
                return _FakeTable("TABLE")
        raise Exception(f"404 not found: {table_id}")

    def delete_table(self, table_id, not_found_ok=False):
        self.deleted.append(table_id)

    def query(self, sql):
        self.queries.append(sql)
        return _FakeJob()


class _FakeBigqueryModule:
    def Dataset(self, ref):
        return ref


class PaidMediaMartViewsTests(unittest.TestCase):
    def _run(self, present, project="test-proj"):
        fake = _FakeBQClient(present)
        with patch.object(bigquery_warehouse, "_client", return_value=fake), \
             patch.object(bigquery_warehouse, "_bigquery", return_value=_FakeBigqueryModule()), \
             bigquery_warehouse.route(bq_project_id=project):
            result = bigquery_warehouse.create_paid_media_mart_views(client_key="acme")
        return result, fake

    def test_builds_both_views_when_all_three_present(self):
        result, fake = self._run({"raw_google_ads", "raw_linkedin_ads", "raw_meta_ads"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(sorted(result["sources"]), ["google", "linkedin", "meta"])
        joined = "\n".join(fake.queries)
        self.assertIn("vw_paid_media_daily", joined)
        self.assertIn("mart_health", joined)
        self.assertIn("paid_google", joined)
        self.assertIn("paid_linkedin", joined)
        self.assertIn("paid_meta", joined)
        # Each view is a UNION ALL across the three sources (2 UNION ALLs).
        paid_sql = next(q for q in fake.queries if "vw_paid_media_daily" in q)
        self.assertEqual(paid_sql.count("UNION ALL"), 2)

    def test_meta_only_client_still_gets_a_working_view(self):
        result, fake = self._run({"raw_meta_ads"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sources"], ["meta"])
        paid_sql = next(q for q in fake.queries if "vw_paid_media_daily" in q)
        self.assertIn("paid_meta", paid_sql)
        self.assertNotIn("UNION ALL", paid_sql)

    def test_google_only_client_still_gets_a_working_view(self):
        result, fake = self._run({"raw_google_ads"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sources"], ["google"])
        paid_sql = next(q for q in fake.queries if "vw_paid_media_daily" in q)
        self.assertIn("paid_google", paid_sql)
        self.assertNotIn("paid_linkedin", paid_sql)
        self.assertNotIn("UNION ALL", paid_sql)  # single source, no union

    def test_pending_when_no_raw_tables_yet(self):
        result, fake = self._run(set())
        self.assertEqual(result["status"], "pending_data")
        self.assertEqual(fake.queries, [])  # nothing created

    def test_missing_project_returns_failed(self):
        with patch.object(bigquery_warehouse, "_client"):
            # no route() context and no env → no project
            with patch.dict("os.environ", {}, clear=True):
                result = bigquery_warehouse.create_paid_media_mart_views()
        self.assertEqual(result["status"], "failed")

    def test_drops_preexisting_table_before_creating_view(self):
        # Simulate mart_health already existing as a TABLE (prior Dataform run).
        fake = _FakeBQClient({"raw_google_ads"})
        orig_get = fake.get_table

        def get_table(table_id):
            if "mart_health" in table_id:
                return _FakeTable("TABLE")  # a real table, must be dropped
            return orig_get(table_id)

        with patch.object(bigquery_warehouse, "_client", return_value=fake), \
             patch.object(bigquery_warehouse, "_bigquery", return_value=_FakeBigqueryModule()), \
             patch.object(fake, "get_table", side_effect=get_table), \
             bigquery_warehouse.route(bq_project_id="test-proj"):
            bigquery_warehouse.create_paid_media_mart_views()
        self.assertTrue(any("mart_health" in d for d in fake.deleted))


if __name__ == "__main__":
    unittest.main()
