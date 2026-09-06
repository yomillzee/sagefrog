"""Microsoft Ads connector wiring + report-parsing tests.

Covers the parts that can be verified without a live Microsoft Advertising API:
the OAuth/authorize plumbing, connector registration, the callback route
resolving (not 404), and the CSV report → campaign_daily mapping.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Must be set before importing main (its callback-alias registration reads them).
os.environ.setdefault("MICROSOFT_ADS_DEVELOPER_TOKEN", "test-dev-token")
os.environ.setdefault("MICROSOFT_ADS_OAUTH_PROVIDER", "google")
os.environ.setdefault("MICROSOFT_ADS_CLIENT_ID", "test-cid.apps.googleusercontent.com")
os.environ.setdefault("MICROSOFT_ADS_CLIENT_SECRET", "test-secret")
os.environ.setdefault("MICROSOFT_ADS_REDIRECT_URI", "https://example.up.railway.app/oauth/microsoft_ads/callback")
os.environ.setdefault("AUTH_SESSION_SECRET", "x" * 32)

import connector_config_store  # noqa: E402
import connectors  # noqa: E402,F401  (triggers handler registration)
import oauth_flows  # noqa: E402
import oauth_store  # noqa: E402
from connectors.base import CONNECTOR_ORDER, get as get_handler  # noqa: E402


class MicrosoftAdsRegistrationTests(unittest.TestCase):
    def test_platform_registered_everywhere(self) -> None:
        self.assertIn("microsoft_ads", oauth_flows.PLATFORMS)
        self.assertIn("microsoft_ads", oauth_store.PLATFORMS)
        self.assertIn("microsoft_ads", connector_config_store.VALID_CONNECTOR_TYPES)
        self.assertIn("microsoft_ads", CONNECTOR_ORDER)

    def test_handler_registered(self) -> None:
        handler = get_handler("microsoft_ads")
        self.assertIsNotNone(handler)
        self.assertEqual(handler.oauth_platform, "microsoft_ads")
        self.assertEqual(handler.default_raw_dataset, "raw_microsoft_ads")
        self.assertFalse(handler.no_oauth)

    def test_included_in_automated_sync(self) -> None:
        # Without this, the cron + onboarding-backfill orchestrator never syncs
        # Microsoft Ads (data would only land via a manual sync click).
        import dashboard.services.bigquery_refresh_orchestrator as orch

        self.assertIn("microsoft_ads", orch._SYNC_CONNECTORS)

    def test_dataset_provisioning_includes_microsoft(self) -> None:
        import client_bigquery_setup

        self.assertEqual(
            client_bigquery_setup.dataset_ids().get("microsoft"), "raw_microsoft_ads"
        )

    def test_explorer_mart_view_builder_exists(self) -> None:
        import bigquery_warehouse

        self.assertTrue(hasattr(bigquery_warehouse, "create_microsoft_ads_mart_view"))

    def test_connector_builds_explorer_view(self) -> None:
        import inspect

        import connectors.microsoft_ads as mod

        src = inspect.getsource(mod)
        self.assertIn("create_microsoft_ads_mart_view", src)

    def test_ad_level_ingestion_wired(self) -> None:
        import inspect

        import bigquery_warehouse
        import connectors.microsoft_ads as mod

        self.assertTrue(hasattr(bigquery_warehouse, "mirror_microsoft_ad_daily_batch"))
        src = inspect.getsource(mod)
        self.assertIn("fetch_ad_daily_metrics", src)
        self.assertIn("mirror_microsoft_ad_daily_batch", src)

    def test_explorer_renders_ad_hierarchy(self) -> None:
        from dashboard.assets import dashboard_js

        # The explorer's JS ships as a cached asset now, not renderer source.
        src = dashboard_js()[1]
        # Microsoft rows carry ad-group / ad copy so the tree drills like Google.
        self.assertIn("title_part_1", src)
        self.assertIn("ad_group_name:r.ad_group_name", src)

    def test_explorer_endpoint_registered(self) -> None:
        import main

        paths = main.app.openapi()["paths"]
        self.assertIn("/api/clients/{client_key}/microsoft-ads/explorer", paths)

    def test_explorer_wired_into_dashboard_renderer(self) -> None:
        from dashboard.assets import dashboard_js

        src = dashboard_js()[1]
        self.assertIn("MICROSOFT_EXPLORER_API", src)
        self.assertIn("normalizeExplorerRows(g,l,m,ms)", src)
        self.assertIn("platform:'microsoft'", src)

    def test_fetch_microsoft_explorer_degrades_to_empty(self) -> None:
        from unittest import mock

        import marketing_service

        with marketing_service.route(client_key="t", project_id="p", mart_dataset_id="m"):
            with mock.patch.object(
                marketing_service, "_run_query", side_effect=RuntimeError("no such table")
            ):
                out = marketing_service.fetch_microsoft_explorer(
                    start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
                )
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["row_count"], 0)

    def test_paid_media_mart_unions_microsoft(self) -> None:
        # The unified paid-media mart must union the microsoft raw campaign_daily
        # so Microsoft spend reaches the dashboard Overview / totals / trend.
        import inspect

        import bigquery_warehouse

        src = inspect.getsource(bigquery_warehouse.create_paid_media_mart_views)
        self.assertIn("paid_microsoft", src)
        self.assertIn('_dataset_id("microsoft")', src)

    def test_connect_prerequisites_ready(self) -> None:
        prereq = oauth_flows.connect_prerequisites("microsoft_ads")
        self.assertTrue(prereq["ready"])
        self.assertEqual(prereq["missing"], [])

    def test_connect_prerequisites_reports_missing(self) -> None:
        saved = os.environ.pop("MICROSOFT_ADS_DEVELOPER_TOKEN", None)
        try:
            prereq = oauth_flows.connect_prerequisites("microsoft_ads")
            self.assertFalse(prereq["ready"])
            self.assertIn("MICROSOFT_ADS_DEVELOPER_TOKEN", prereq["missing"])
        finally:
            if saved is not None:
                os.environ["MICROSOFT_ADS_DEVELOPER_TOKEN"] = saved


class MicrosoftAdsAuthorizeTests(unittest.TestCase):
    def test_authorize_url_uses_google_with_identity_scopes(self) -> None:
        url = oauth_flows.build_authorize_url("microsoft_ads", state="STATEVAL")
        self.assertIn("https://accounts.google.com/o/oauth2/v2/auth", url)
        self.assertIn("scope=profile+email", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("state=STATEVAL", url)
        self.assertIn("client_id=test-cid.apps.googleusercontent.com", url)
        # redirect_uri must be the exact configured value, url-encoded.
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fexample.up.railway.app%2Foauth%2Fmicrosoft_ads%2Fcallback",
            url,
        )

    def test_callback_url_is_the_env_redirect_uri(self) -> None:
        self.assertEqual(
            oauth_flows.callback_url("microsoft_ads"),
            "https://example.up.railway.app/oauth/microsoft_ads/callback",
        )

    def test_callback_path_derives_from_env(self) -> None:
        self.assertEqual(
            oauth_flows.microsoft_ads_callback_path(), "/oauth/microsoft_ads/callback"
        )
        saved = os.environ["MICROSOFT_ADS_REDIRECT_URI"]
        try:
            os.environ["MICROSOFT_ADS_REDIRECT_URI"] = "https://host.example/custom/ms-callback"
            self.assertEqual(
                oauth_flows.microsoft_ads_callback_path(), "/custom/ms-callback"
            )
        finally:
            os.environ["MICROSOFT_ADS_REDIRECT_URI"] = saved


class MicrosoftAdsCallbackRouteTests(unittest.TestCase):
    def test_callback_route_resolves_not_404(self) -> None:
        from starlette.testclient import TestClient

        import main

        client = TestClient(main.app, raise_server_exceptions=False)
        # An OAuth error redirect exercises the real callback handler. We only
        # assert it is routed (not 404); the redirect/handling details are
        # covered elsewhere and depend on session/db config not present here.
        resp = client.get(
            "/oauth/microsoft_ads/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        self.assertNotEqual(resp.status_code, 404)

    def test_hyphenated_callback_path_resolves_not_404(self) -> None:
        # Google redirect URIs are commonly registered with hyphens
        # (/oauth/microsoft-ads/callback) while our platform key uses an
        # underscore. The generic route must normalize the hyphen and resolve
        # it instead of 404-ing (regression for the deployed callback 404).
        from starlette.testclient import TestClient

        import main

        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get(
            "/oauth/microsoft-ads/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        self.assertNotEqual(resp.status_code, 404)


class MicrosoftAdsReportParsingTests(unittest.TestCase):
    def test_parse_report_date_formats(self) -> None:
        import microsoft_ads_service as svc

        self.assertEqual(svc._parse_report_date("2026-01-15"), "2026-01-15")
        self.assertEqual(svc._parse_report_date("1/15/2026"), "2026-01-15")
        self.assertEqual(svc._parse_report_date(""), "")

    def test_numeric_coercion(self) -> None:
        import microsoft_ads_service as svc

        self.assertEqual(svc._to_float("1,234.5"), 1234.5)
        self.assertEqual(svc._to_float(None), 0.0)
        self.assertEqual(svc._to_int("42.0"), 42)
        self.assertEqual(svc._to_int("bad"), 0)

    def test_parse_report_csv_maps_to_campaign_daily(self) -> None:
        import io
        import zipfile
        from unittest import mock

        import microsoft_ads_service as svc

        csv_text = (
            "TimePeriod,CampaignId,CampaignName,Spend,Impressions,Clicks,Conversions,Revenue\n"
            "2026-01-01,111,Brand,12.50,1000,50,3,150.00\n"
            "2026-01-02,111,Brand,10.00,800,40,2,90.00\n"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("report.csv", csv_text)
        zipped = buf.getvalue()

        class _Resp:
            status_code = 200
            content = zipped

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                return _Resp()

        with mock.patch.object(svc.httpx, "Client", return_value=_Client()):
            rows = svc._download_report_rows(
                "https://download.example/report", label="campaign", account_id="999"
            )

        # _download_report_rows returns raw column-keyed dicts; mapping to the
        # campaign_daily shape happens in fetch_campaign_daily_metrics.
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["CampaignId"], "111")
        self.assertEqual(rows[0]["Spend"], "12.50")
        self.assertEqual(rows[0]["TimePeriod"], "2026-01-01")

    def test_fetch_campaign_daily_maps_raw_rows(self) -> None:
        from unittest import mock

        import microsoft_ads_service as svc

        raw = [{
            "TimePeriod": "2026-01-01", "CampaignId": "5", "CampaignName": "C",
            "Spend": "2.50", "Impressions": "10", "Clicks": "1",
            "Conversions": "0", "Revenue": "0",
        }]
        with mock.patch.object(svc, "_run_report", return_value=raw):
            rows = svc.fetch_campaign_daily_metrics(
                "999", start=date(2026, 1, 1), end=date(2026, 1, 2),
                access_token="t", customer_id="c",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "microsoft")
        self.assertEqual(rows[0]["campaign_id"], "5")
        self.assertEqual(rows[0]["spend"], 2.5)

    def test_fetch_ad_daily_maps_raw_rows(self) -> None:
        from unittest import mock

        import microsoft_ads_service as svc

        raw = [{
            "TimePeriod": "2026-01-01", "CampaignId": "5", "CampaignName": "C",
            "AdGroupId": "50", "AdGroupName": "AG", "AdId": "900",
            "AdType": "ResponsiveSearch", "AdTitle": "Buy Scrubs",
            "TitlePart1": "Scrubs", "TitlePart2": "Fast Ship", "TitlePart3": "Save",
            "AdDescription": "Great scrubs", "AdDescription2": "Order today",
            "Path1": "scrubs", "Path2": "sale", "DisplayUrl": "nixon.com/scrubs",
            "FinalUrl": "https://nixon.com/scrubs",
            "Spend": "9.00", "Impressions": "100", "Clicks": "7",
            "Conversions": "2", "Revenue": "40",
        }]
        with mock.patch.object(svc, "_run_report", return_value=raw):
            rows = svc.fetch_ad_daily_metrics(
                "999", start=date(2026, 1, 1), end=date(2026, 1, 2),
                access_token="t", customer_id="c",
            )
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["ad_id"], "900")
        self.assertEqual(r["ad_group_id"], "50")
        self.assertEqual(r["ad_group_name"], "AG")
        self.assertEqual(r["title_part_1"], "Scrubs")
        self.assertEqual(r["description_1"], "Great scrubs")
        self.assertEqual(r["final_url"], "https://nixon.com/scrubs")
        self.assertEqual(r["spend"], 9.0)

    def test_report_request_columns_and_shape(self) -> None:
        """fetch_campaign_daily_metrics submits a daily CampaignPerformanceReport
        with the expected columns and identity headers, and returns parsed rows."""
        from unittest import mock

        import microsoft_ads_service as svc

        captured: dict = {}

        def fake_post(url, headers, body):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = body
            return {"ReportRequestId": "req-1"}

        raw = [{
            "TimePeriod": "2026-01-01", "CampaignId": "1", "CampaignName": "C",
            "Spend": "1", "Impressions": "1", "Clicks": "1", "Conversions": "0", "Revenue": "0",
        }]
        with mock.patch.object(svc, "_post", side_effect=fake_post), \
             mock.patch.object(svc, "_poll_for_report", return_value="https://dl/report"), \
             mock.patch.object(svc, "_download_report_rows", return_value=raw):
            rows = svc.fetch_campaign_daily_metrics(
                "12345",
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
                access_token="tok",
                customer_id="cust-1",
            )

        self.assertEqual(rows[0]["campaign_id"], "1")
        self.assertTrue(captured["url"].endswith("/GenerateReport/Submit"))
        self.assertEqual(captured["headers"]["IdentityProvider"], "Google")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(captured["headers"]["DeveloperToken"], "test-dev-token")
        self.assertEqual(captured["headers"]["CustomerId"], "cust-1")
        self.assertEqual(captured["headers"]["CustomerAccountId"], "12345")
        report = captured["body"]["ReportRequest"]
        self.assertEqual(report["Type"], "CampaignPerformanceReportRequest")
        self.assertEqual(report["Aggregation"], "Daily")
        self.assertEqual(report["Scope"]["AccountIds"], [12345])
        for col in ("TimePeriod", "CampaignId", "Spend", "Impressions", "Clicks", "Conversions", "Revenue"):
            self.assertIn(col, report["Columns"])
        # Field names must be singular — the plural forms are ignored by the API,
        # which then leaves metadata in the CSV and drops every data row.
        self.assertTrue(report["ExcludeReportHeader"])
        self.assertTrue(report["ExcludeReportFooter"])
        self.assertNotIn("ExcludeReportHeaders", report)
        self.assertNotIn("ExcludeReportFooters", report)

    def test_parse_report_csv_skips_metadata_preamble(self) -> None:
        """Regression: even if the CSV carries a report-metadata preamble, the
        parser must locate the real column-header row (not drop every data row)."""
        import io
        import zipfile
        from unittest import mock

        import microsoft_ads_service as svc

        csv_text = (
            '"Report Name: Campaign Performance Report"\n'
            '"Report Time: 1/1/2026-1/2/2026"\n'
            '"Report Aggregation: Daily"\n'
            "\n"
            "TimePeriod,CampaignId,CampaignName,Spend,Impressions,Clicks,Conversions,Revenue\n"
            "1/1/2026,222,Search,20.00,2000,80,4,300.00\n"
            '"©2026 Microsoft Corporation"\n'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("report.csv", csv_text)
        zipped = buf.getvalue()

        class _Resp:
            status_code = 200
            content = zipped

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                return _Resp()

        with mock.patch.object(svc.httpx, "Client", return_value=_Client()):
            rows = svc._download_report_rows("https://dl/report", label="campaign", account_id="222")

        # The metadata preamble must be skipped (header located correctly), so the
        # real data row is parsed with the right column keys. (The trailing footer
        # line has no CampaignId and is dropped by the mapping layer downstream.)
        data = [r for r in rows if (r.get("CampaignId") or "").strip().isdigit()]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["CampaignId"], "222")
        self.assertEqual(data[0]["Spend"], "20.00")


class MicrosoftAdsAdReportTests(unittest.TestCase):
    def test_ad_report_request_shape(self) -> None:
        """fetch_ad_daily_metrics submits an AdPerformanceReport with the ad-copy
        and hierarchy columns needed for the campaign → ad group → ad drilldown."""
        from unittest import mock

        import microsoft_ads_service as svc

        captured: dict = {}

        def fake_post(url, headers, body):
            captured["body"] = body
            return {"ReportRequestId": "req-ad"}

        with mock.patch.object(svc, "_post", side_effect=fake_post), \
             mock.patch.object(svc, "_poll_for_report", return_value="https://dl/ad"), \
             mock.patch.object(svc, "_download_report_rows", return_value=[]):
            svc.fetch_ad_daily_metrics(
                "12345", start=date(2026, 1, 1), end=date(2026, 1, 31),
                access_token="tok", customer_id="cust-1",
            )

        report = captured["body"]["ReportRequest"]
        self.assertEqual(report["Type"], "AdPerformanceReportRequest")
        self.assertEqual(report["Aggregation"], "Daily")
        self.assertTrue(report["ExcludeReportHeader"])
        for col in (
            "AdGroupId", "AdGroupName", "AdId", "AdType", "AdTitle",
            "TitlePart1", "TitlePart2", "TitlePart3", "AdDescription", "AdDescription2",
            "FinalUrl", "TimePeriod", "CampaignId", "Spend", "Clicks",
        ):
            self.assertIn(col, report["Columns"])


class MicrosoftAdsCreativeAssetTests(unittest.TestCase):
    def test_extract_rsa_assets(self) -> None:
        import microsoft_ads_service as svc

        ad = {
            "Type": "ResponsiveSearchAd", "Id": "1",
            "Headlines": [{"Asset": {"Text": "H1"}}, {"Asset": {"Text": "H2"}}, {"Asset": {"Text": ""}}],
            "Descriptions": [{"Asset": {"Text": "D1"}}],
            "Path1": "p1", "Path2": "p2", "FinalUrls": ["https://x.com/a"],
        }
        a = svc._extract_ad_assets(ad)
        self.assertEqual(a["headlines"], ["H1", "H2"])
        self.assertEqual(a["descriptions"], ["D1"])
        self.assertEqual(a["path_1"], "p1")
        self.assertEqual(a["final_url"], "https://x.com/a")

    def test_extract_expanded_text_ad_assets(self) -> None:
        import microsoft_ads_service as svc

        ad = {
            "Type": "ExpandedTextAd", "Id": "2",
            "TitlePart1": "T1", "TitlePart2": "T2", "TitlePart3": "",
            "Text": "TX", "TextPart2": "TX2", "FinalUrls": ["https://y.com"],
        }
        a = svc._extract_ad_assets(ad)
        self.assertEqual(a["headlines"], ["T1", "T2"])
        self.assertEqual(a["descriptions"], ["TX", "TX2"])
        self.assertEqual(a["final_url"], "https://y.com")

    def test_fetch_ad_assets_queries_campaign_management(self) -> None:
        from unittest import mock

        import microsoft_ads_service as svc

        captured: dict = {}

        def fake_post(url, headers, body):
            captured.setdefault("urls", []).append(url)
            captured.setdefault("bodies", []).append(body)
            return {"Ads": [{"Type": "ResponsiveSearchAd", "Id": "900",
                             "Headlines": [{"Asset": {"Text": "Buy Scrubs"}}],
                             "Descriptions": [{"Asset": {"Text": "Fast ship"}}]}]}

        with mock.patch.object(svc, "_post", side_effect=fake_post):
            out = svc.fetch_ad_assets("999", ["50"], access_token="t", customer_id="c")

        self.assertIn("900", out)
        self.assertEqual(out["900"]["headlines"], ["Buy Scrubs"])
        self.assertTrue(captured["urls"][0].endswith("/Ads/QueryByAdGroupId"))
        self.assertEqual(captured["bodies"][0]["AdGroupId"], 50)

    def test_enrich_ad_copy_merges_headlines_json(self) -> None:
        import json

        import connectors.microsoft_ads as mod

        ad_rows = [
            {"ad_id": "900", "ad_group_id": "50", "spend": 9.0},
            {"ad_id": "901", "ad_group_id": "50", "spend": 1.0},
        ]

        class _Svc:
            def fetch_ad_assets(self, account_id, group_ids, *, access_token, customer_id):
                # highest-spend ad group visited first
                assert group_ids[0] == "50"
                return {"900": {"headlines": ["A", "B"], "descriptions": ["C"],
                                "final_url": "https://z.com", "path_1": "", "path_2": "",
                                "ad_type": "ResponsiveSearchAd"}}

        mod._enrich_ad_copy(_Svc(), ad_rows, account_id="999", access_token="t", customer_id="c")
        self.assertEqual(json.loads(ad_rows[0]["headlines"]), ["A", "B"])
        self.assertEqual(json.loads(ad_rows[0]["descriptions"]), ["C"])
        self.assertEqual(ad_rows[0]["final_url"], "https://z.com")
        # Ad with no matching creative is left unchanged (no headlines key).
        self.assertNotIn("headlines", ad_rows[1])


if __name__ == "__main__":
    unittest.main()
