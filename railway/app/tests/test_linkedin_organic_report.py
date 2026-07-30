"""Tests for linkedin_organic_report_service.build_report shaping.

Stubs bigquery_service.run_query to return recorded-shape rows and verifies
totals, top-post ordering carry-through, follower lifetime-total extraction, and
graceful handling of a missing project / missing table.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# bigquery_service (imported by the report service) imports google.cloud +
# google.oauth2.service_account; stub the tree exactly like the other tests.
if "google.cloud.bigquery" not in sys.modules:
    g = types.ModuleType("google")
    c = types.ModuleType("google.cloud")
    bq = types.ModuleType("google.cloud.bigquery")
    oauth2 = types.ModuleType("google.oauth2")
    sa = types.ModuleType("google.oauth2.service_account")

    class _FakeQueryJobConfig:
        def __init__(self, *a, **k):
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *a, **k):
            return cls()

    bq.QueryJobConfig = _FakeQueryJobConfig
    bq.ScalarQueryParameter = lambda *a, **k: None
    sa.Credentials = _FakeCredentials
    g.cloud = c
    c.bigquery = bq
    g.oauth2 = oauth2
    oauth2.service_account = sa
    sys.modules.update({
        "google": g, "google.cloud": c, "google.cloud.bigquery": bq,
        "google.oauth2": oauth2, "google.oauth2.service_account": sa,
    })

import linkedin_organic_report_service as svc  # noqa: E402


class _Cfg:
    def __init__(self, project, dataset):
        self.bq_project_id = project
        self.raw_dataset_id = dataset


def _install_routing(project="proj-x", dataset="raw_linkedin_organic"):
    fake = types.ModuleType("connector_config_store")
    fake.get_config = lambda slug, ctype: _Cfg(project, dataset)
    sys.modules["connector_config_store"] = fake


class BuildReportTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("connector_config_store", None)

    def test_no_project_returns_unconfigured(self):
        fake = types.ModuleType("connector_config_store")
        fake.get_config = lambda slug, ctype: _Cfg(None, None)
        sys.modules["connector_config_store"] = fake
        # No client_dashboard_config project either.
        cdc = types.ModuleType("client_dashboard_config")
        cdc.get_config = lambda slug: None
        sys.modules["client_dashboard_config"] = cdc
        try:
            report = svc.build_report("acme")
        finally:
            sys.modules.pop("client_dashboard_config", None)
        self.assertFalse(report.configured)
        self.assertIn("BigQuery project", report.error or "")

    def test_report_shapes_all_families(self):
        _install_routing()

        posts = [
            {"org_id": "777", "post_id": "1", "title": "Big post", "post_type": "video",
             "published_at": "2026-06-10", "impressions": 500, "clicks": 10,
             "likes": 20, "comments": 3, "shares": 2, "engagement_rate": 0.07},
            {"org_id": "777", "post_id": "2", "title": "Small post", "post_type": "text",
             "published_at": "2026-06-11", "impressions": 100, "clicks": 1,
             "likes": 5, "comments": 1, "shares": 0, "engagement_rate": 0.07},
        ]
        followers = [
            {"metric_date": "2026-06-10", "organic_follower_gain": 5, "paid_follower_gain": 1,
             "total_follower_gain": 6, "total_followers": 0},
            {"metric_date": "2026-06-11", "organic_follower_gain": 4, "paid_follower_gain": 0,
             "total_follower_gain": 4, "total_followers": 1200},
        ]
        pages = [
            {"metric_date": "2026-06-10", "page_views": 40, "unique_visitors": 30},
            {"metric_date": "2026-06-11", "page_views": 10, "unique_visitors": 8},
        ]

        calls = {"n": 0}

        def fake_run_query(sql, **kw):
            calls["n"] += 1
            low = sql.lower()
            if "post_stats" in low:
                return posts
            if "follower_daily" in low:
                return followers
            if "page_daily" in low:
                return pages
            return []

        svc.bigquery_service.run_query = fake_run_query  # type: ignore
        report = svc.build_report("acme")

        self.assertTrue(report.configured)
        self.assertEqual(report.org_id, "777")
        self.assertEqual(report.post_count, 2)
        self.assertEqual(report.total_impressions, 600)
        self.assertEqual(report.total_likes, 25)
        self.assertEqual(report.total_shares, 2)
        # Lifetime total is taken from the most recent non-zero day.
        self.assertEqual(report.total_followers, 1200)
        self.assertEqual(report.follower_gain, 10)
        self.assertEqual(report.total_page_views, 50)
        self.assertEqual(report.total_unique_visitors, 38)

    def test_report_shapes_new_families(self):
        _install_routing()

        posts = [
            {"org_id": "777", "post_id": "1", "title": "Big post", "post_type": "video",
             "published_at": "2026-06-10", "impressions": 500, "clicks": 10,
             "likes": 20, "comments": 3, "shares": 2, "engagement_rate": 0.07},
            {"org_id": "777", "post_id": "2", "title": "Small post", "post_type": "text",
             "published_at": "2026-06-11", "impressions": 100, "clicks": 1,
             "likes": 5, "comments": 1, "shares": 0, "engagement_rate": 0.07},
        ]
        reach = [
            {"post_id": "1", "unique_impressions": 420, "reactions_by_type": '{"LIKE":18,"PRAISE":2}'},
            {"post_id": "2", "unique_impressions": 90, "reactions_by_type": None},
        ]
        demographics = [
            {"dimension": "seniority", "category": "Senior",
             "organic_followers": 6, "paid_followers": 1, "total_followers": 7},
            {"dimension": "seniority", "category": "Entry",
             "organic_followers": 2, "paid_followers": 0, "total_followers": 2},
            {"dimension": "company_size", "category": "51-200",
             "organic_followers": 4, "paid_followers": 0, "total_followers": 4},
        ]
        engagement = [
            {"metric_date": "2026-06-10", "impressions": 400, "unique_impressions": 320,
             "clicks": 8, "likes": 15, "comments": 2, "shares": 3, "engagement_rate": 0.07},
        ]
        page_split = [{"desktop": 30, "mobile": 20, "careers_page_views": 12}]

        def fake_run_query(sql, **kw):
            low = sql.lower()
            if "reactions_by_type" in low:
                return reach
            if "post_stats" in low:
                return posts
            if "follower_demographics" in low:
                return demographics
            if "engagement_daily" in low:
                return engagement
            if "sum(desktop_page_views" in low:
                return page_split
            if "page_daily" in low:
                return [{"metric_date": "2026-06-10", "page_views": 40, "unique_visitors": 30}]
            return []

        svc.bigquery_service.run_query = fake_run_query  # type: ignore
        report = svc.build_report("acme")

        self.assertEqual(report.total_unique_impressions, 510)
        self.assertEqual(report.top_posts[0]["reactions"], {"LIKE": 18, "PRAISE": 2})
        self.assertEqual(
            [r["category"] for r in report.follower_demographics["seniority"]],
            ["Senior", "Entry"],
        )
        self.assertEqual(report.follower_demographics["company_size"][0]["category"], "51-200")
        self.assertEqual(len(report.engagement_series), 1)
        self.assertEqual(report.engagement_series[0]["impressions"], 400)
        self.assertEqual(report.page_desktop_views, 30)
        self.assertEqual(report.page_mobile_views, 20)
        self.assertIn({"label": "Careers", "views": 12}, report.page_sections)

    def test_missing_table_is_not_fatal(self):
        _install_routing()

        def fake_run_query(sql, **kw):
            raise RuntimeError("404 Not found: Table proj-x:raw_linkedin_organic.post_stats")

        svc.bigquery_service.run_query = fake_run_query  # type: ignore
        report = svc.build_report("acme")
        # Configured (project resolved) but empty — absent source, not an error.
        self.assertTrue(report.configured)
        self.assertEqual(report.post_count, 0)
        self.assertEqual(report.top_posts, [])


if __name__ == "__main__":
    unittest.main()
