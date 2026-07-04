from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

if "google.cloud.bigquery" not in sys.modules:
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _FakeQueryJobConfig:
        def __init__(self, dry_run=False):
            self.dry_run = dry_run
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig
    service_account_mod.Credentials = _FakeCredentials
    cloud_mod.bigquery = bigquery_mod
    google_mod.cloud = cloud_mod
    google_mod.oauth2 = oauth2_mod
    oauth2_mod.service_account = service_account_mod
    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.service_account"] = service_account_mod

import nixon_marketing_service  # noqa: E402


class _FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self, max_results=None):
        return self._rows[:max_results]


class _FakeClient:
    def __init__(self):
        self.calls = []

    def query(self, sql, job_config=None):
        self.calls.append({"sql": sql, "job_config": job_config})
        if "SAFE_DIVIDE(spend, clicks)" in sql:
            return _FakeQueryJob([{
                "spend": 5298,
                "impressions": 59645,
                "clicks": 1175,
                "conversions": 44,
                "cpc": 4.51,
                "cpa": 120.41,
                "ctr": 1.97,
            }])
        if "explorer_google_ads_daily" in sql:
            return _FakeQueryJob([{
                "source": "google",
                "campaign_id": "c1",
                "campaign_name": "Campaign 1",
                "ad_group_id": "ag1",
                "ad_group_name": "Ad Group 1",
                "ad_id": "ad1",
                "ad_label": "Ad 1",
                "spend": 25,
                "impressions": 250,
                "clicks": 10,
                "conversions": 2,
                "conversion_value": 50,
            }])
        if "GROUP BY source, campaign_id" in sql:
            return _FakeQueryJob([{
                "source": "google",
                "campaign_id": "c1",
                "campaign_name": "Campaign 1",
                "spend": 25,
                "impressions": 250,
                "clicks": 10,
                "conversions": 2,
                "conversion_value": 50,
            }])
        if "GROUP BY `date`, source" in sql:
            return _FakeQueryJob([{
                "date": "2026-06-01",
                "source": "google",
                "spend": 25,
                "impressions": 250,
                "clicks": 10,
                "conversions": 2,
                "conversion_value": 50,
            }])
        if "GROUP BY source" in sql:
            return _FakeQueryJob([{
                "source": "google",
                "spend": 25,
                "impressions": 250,
                "clicks": 10,
                "conversions": 2,
                "conversion_value": 50,
            }])
        return _FakeQueryJob([{
            "spend": 25,
            "impressions": 250,
            "clicks": 10,
            "conversions": 2,
            "conversion_value": 50,
        }])


class NixonMarketingServiceTests(unittest.TestCase):
    def test_marketing_endpoint_queries_only_parameterized_marketing_mart(self) -> None:
        fake_client = _FakeClient()

        with patch.object(
            nixon_marketing_service.bigquery_service,
            "build_client",
            return_value=fake_client,
        ):
            payload = nixon_marketing_service.fetch_nixon_marketing(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 30),
                top_limit=5,
            )

        self.assertEqual(payload["summary"]["spend"], 25)
        self.assertEqual(len(payload["by_source"]), 1)
        self.assertEqual(len(payload["daily_trend"]), 1)
        self.assertEqual(len(payload["top_campaigns_by_spend"]), 1)
        self.assertEqual(len(fake_client.calls), 4)
        for call in fake_client.calls:
            sql = call["sql"]
            self.assertIn("`nixon-medical.marketing_marts.fact_marketing_daily`", sql)
            self.assertNotIn("raw_google_ads", sql)
            self.assertNotIn("raw_linkedin_ads", sql)
            self.assertNotIn("staging", sql.lower())
            params = {
                param.name: param.value
                for param in call["job_config"].query_parameters
            }
            self.assertEqual(params["start_date"], date(2026, 6, 1))
            self.assertEqual(params["end_date"], date(2026, 6, 30))

        top_params = {
            param.name: param.value
            for param in fake_client.calls[-1]["job_config"].query_parameters
        }
        self.assertEqual(top_params["top_limit"], 5)

    def test_health_queries_only_parameterized_marketing_mart(self) -> None:
        fake_client = _FakeClient()

        with patch.object(
            nixon_marketing_service.bigquery_service,
            "build_client",
            return_value=fake_client,
        ):
            payload = nixon_marketing_service.fetch_nixon_marketing_health(limit=25)

        self.assertEqual(payload["client"], "nixon")
        self.assertEqual(payload["row_count"], 1)
        # Two queries: mart_health, then a GA4 freshness row from vw_page_path_daily.
        self.assertEqual(len(fake_client.calls), 2)
        sql = fake_client.calls[0]["sql"]
        self.assertIn("`nixon-medical.marketing_marts.mart_health`", sql)
        self.assertNotIn("raw_google_ads", sql)
        self.assertNotIn("raw_linkedin_ads", sql)
        self.assertNotIn("staging", sql.lower())
        params = {
            param.name: param.value
            for param in fake_client.calls[0]["job_config"].query_parameters
        }
        self.assertEqual(params["limit"], 25)

    def test_google_ads_explorer_queries_only_parameterized_marketing_mart(self) -> None:
        fake_client = _FakeClient()

        with patch.object(
            nixon_marketing_service.bigquery_service,
            "build_client",
            return_value=fake_client,
        ):
            payload = nixon_marketing_service.fetch_nixon_google_ads_explorer(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 30),
            )

        self.assertEqual(payload["client"], "nixon")
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["ad_id"], "ad1")
        self.assertEqual(len(fake_client.calls), 1)
        sql = fake_client.calls[0]["sql"]
        self.assertIn("`nixon-medical.marketing_marts.explorer_google_ads_daily`", sql)
        self.assertIn("WHERE date BETWEEN @start_date AND @end_date", sql)
        self.assertNotIn("raw_google_ads", sql)
        self.assertNotIn("raw_linkedin_ads", sql)
        self.assertNotIn("staging", sql.lower())
        params = {
            param.name: param.value
            for param in fake_client.calls[0]["job_config"].query_parameters
        }
        self.assertEqual(params["start_date"], date(2026, 6, 1))
        self.assertEqual(params["end_date"], date(2026, 6, 30))

    def test_summary_queries_only_parameterized_marketing_mart(self) -> None:
        fake_client = _FakeClient()

        with patch.object(
            nixon_marketing_service.bigquery_service,
            "build_client",
            return_value=fake_client,
        ):
            payload = nixon_marketing_service.fetch_nixon_summary(
                start_date=date(2026, 5, 25),
                end_date=date(2026, 6, 23),
            )

        self.assertEqual(payload["client_key"], "nixon")
        self.assertEqual(payload["start_date"], "2026-05-25")
        self.assertEqual(payload["end_date"], "2026-06-23")
        self.assertEqual(payload["summary"]["spend"], 5298)
        self.assertEqual(payload["summary"]["clicks"], 1175)
        self.assertEqual(payload["summary"]["conversions"], 44)
        self.assertEqual(payload["summary"]["cpa"], 120.41)
        self.assertEqual(payload["summary"]["ctr"], 1.97)
        # Three queries: totals, per-platform by_source, and the daily series.
        self.assertEqual(len(fake_client.calls), 3)
        sql = fake_client.calls[0]["sql"]
        # Summary reads the unified paid-media view, not the legacy fact table.
        self.assertIn("`nixon-medical.marketing_marts.vw_paid_media_daily`", sql)
        self.assertIn("WHERE `date` BETWEEN @start_date AND @end_date", sql)
        self.assertNotIn("raw_google_ads", sql)
        self.assertNotIn("raw_linkedin_ads", sql)
        self.assertNotIn("staging", sql.lower())
        params = {
            param.name: param.value
            for param in fake_client.calls[0]["job_config"].query_parameters
        }
        self.assertEqual(params["start_date"], date(2026, 5, 25))
        self.assertEqual(params["end_date"], date(2026, 6, 23))

    def test_route_scopes_queries_to_another_clients_project_and_dataset(self) -> None:
        fake_client = _FakeClient()

        with patch.object(
            nixon_marketing_service.bigquery_service,
            "build_client",
            return_value=fake_client,
        ), nixon_marketing_service.route(
            client_key="acme",
            project_id="acme-project",
            mart_dataset_id="acme_marts",
        ):
            payload = nixon_marketing_service.fetch_nixon_google_ads_explorer(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 30),
            )

        self.assertEqual(payload["client"], "acme")
        sql = fake_client.calls[0]["sql"]
        self.assertIn("`acme-project.acme_marts.explorer_google_ads_daily`", sql)
        self.assertNotIn("nixon-medical", sql)

    def test_route_context_does_not_leak_across_calls(self) -> None:
        fake_client = _FakeClient()

        with patch.object(
            nixon_marketing_service.bigquery_service,
            "build_client",
            return_value=fake_client,
        ):
            with nixon_marketing_service.route(project_id="acme-project", mart_dataset_id="acme_marts"):
                pass
            payload = nixon_marketing_service.fetch_nixon_google_ads_explorer(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 30),
            )

        self.assertEqual(payload["client"], "nixon")
        sql = fake_client.calls[0]["sql"]
        self.assertIn("`nixon-medical.marketing_marts.explorer_google_ads_daily`", sql)


if __name__ == "__main__":
    unittest.main()
