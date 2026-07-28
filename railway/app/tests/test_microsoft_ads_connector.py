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
            rows = svc._parse_report_csv("https://download.example/report", account_id="999")

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["source"], "microsoft")
        self.assertEqual(first["account_id"], "999")
        self.assertEqual(first["campaign_id"], "111")
        self.assertEqual(first["campaign_name"], "Brand")
        self.assertEqual(first["metric_date"], "2026-01-01")
        self.assertEqual(first["spend"], 12.5)
        self.assertEqual(first["clicks"], 50)
        self.assertEqual(first["impressions"], 1000)
        self.assertEqual(first["conversions"], 3.0)
        self.assertEqual(first["conversion_value"], 150.0)

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

        with mock.patch.object(svc, "_post", side_effect=fake_post), \
             mock.patch.object(svc, "_poll_for_report", return_value="https://dl/report"), \
             mock.patch.object(svc, "_parse_report_csv", return_value=[{"campaign_id": "1"}]):
            rows = svc.fetch_campaign_daily_metrics(
                "12345",
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
                access_token="tok",
                customer_id="cust-1",
            )

        self.assertEqual(rows, [{"campaign_id": "1"}])
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


if __name__ == "__main__":
    unittest.main()
