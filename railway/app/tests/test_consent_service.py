"""Tests for consent_service.build_result: view-model assembly, health rollup,
change-tracking diff, and the SSRF URL guard. All pure — no DB, no browser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import consent_classifier as cc  # noqa: E402
import consent_scanner  # noqa: E402
import consent_service as svc  # noqa: E402


def _capture(url, phase, *, requests=None, cookies=None, storage=None, banner=True):
    return {
        "url": url, "final_url": url, "phase": phase,
        "requests": requests or [], "scripts": [], "cookies": cookies or [],
        "storage": storage or [], "consent_signals": {"banner_detected": banner},
        "banner_detected": banner, "interaction": {"clicked": phase != "pre_consent"},
        "errors": [],
    }


def _leaky_page(url):
    """A page that leaks a Meta _fbp identifier before consent (and after reject)."""
    meta_req = {"url": "https://www.facebook.com/tr?id=1&ev=PageView&_fbp=fb.1.1.9",
                "resource_type": "image", "post_data_snippet": None}
    ga_ping = {"url": "https://www.google-analytics.com/g/collect?v=2&gcs=G100&en=page_view",
               "resource_type": "image", "post_data_snippet": None}
    fbp_cookie = {"name": "_fbp", "value": "fb.1.1700000000.9", "domain": "northwind.example"}
    ga_cookie = {"name": "_ga", "value": "GA1.1.111.222", "domain": "northwind.example"}
    return {
        "url": url,
        "phases": {
            "pre_consent": _capture(url, "pre_consent", requests=[meta_req, ga_ping], cookies=[fbp_cookie]),
            "reject_all": _capture(url, "reject_all", requests=[meta_req, ga_ping], cookies=[fbp_cookie]),
            "accept_all": _capture(url, "accept_all", requests=[meta_req, ga_ping],
                                   cookies=[fbp_cookie, ga_cookie]),
        },
    }


class BuildResultTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"available": True, "error": None, "engine": "chromium",
                    "pages": [_leaky_page("https://www.northwind.example/")]}
        self.exp = svc.default_expectations()

    def test_overall_health_fail_on_preconsent_identifier(self):
        res = svc.build_result(self.raw, expectations=self.exp, previous_result=None)
        self.assertEqual(res["health"], "fail")
        self.assertGreaterEqual(res["summary"]["identifiers_before_consent"], 1)

    def test_cookieless_ping_not_counted_as_violation(self):
        res = svc.build_result(self.raw, expectations=self.exp, previous_result=None)
        # GA ping present in every phase, but never a violation.
        self.assertGreaterEqual(res["summary"]["cookieless_pings"], 1)
        ga_violations = [f for f in res["top_findings"] if f["vendor_key"] == "ga4"]
        self.assertEqual(ga_violations, [])

    def test_accept_phase_has_no_violations(self):
        res = svc.build_result(self.raw, expectations=self.exp, previous_result=None)
        self.assertEqual(res["phase_totals"]["accept_all"]["violation_count"], 0)

    def test_vendor_inventory_named(self):
        res = svc.build_result(self.raw, expectations=self.exp, previous_result=None)
        keys = {v["vendor_key"] for v in res["vendors"]}
        self.assertIn("meta", keys)
        self.assertIn("ga4", keys)

    def test_unavailable_scan_is_unknown_health(self):
        res = svc.build_result({"available": False, "error": "no browser", "pages": []},
                               expectations=self.exp, previous_result=None)
        self.assertEqual(res["health"], "unknown")
        self.assertFalse(res["available"])

    def test_diff_detects_resolved_violation(self):
        before = svc.build_result(self.raw, expectations=self.exp, previous_result=None)
        # A clean re-scan: the leak is gone.
        clean_page = _leaky_page("https://www.northwind.example/")
        for phase in ("pre_consent", "reject_all"):
            clean_page["phases"][phase]["requests"] = [
                {"url": "https://www.google-analytics.com/g/collect?v=2&gcs=G100&en=page_view",
                 "resource_type": "image", "post_data_snippet": None}]
            clean_page["phases"][phase]["cookies"] = []
        clean_raw = {"available": True, "error": None, "engine": "chromium", "pages": [clean_page]}
        after = svc.build_result(clean_raw, expectations=self.exp, previous_result=before)
        self.assertEqual(after["health"], "pass")
        self.assertTrue(after["diff"]["has_previous"])
        self.assertGreaterEqual(len(after["diff"]["resolved_violations"]), 1)
        self.assertEqual(after["diff"]["health_change"], {"from": "fail", "to": "pass"})

    def test_diff_detects_new_violation(self):
        clean_page = _leaky_page("https://www.northwind.example/")
        for phase in ("pre_consent", "reject_all"):
            clean_page["phases"][phase]["requests"] = []
            clean_page["phases"][phase]["cookies"] = []
        clean = svc.build_result({"available": True, "engine": "chromium", "pages": [clean_page]},
                                 expectations=self.exp, previous_result=None)
        regressed = svc.build_result(self.raw, expectations=self.exp, previous_result=clean)
        self.assertGreaterEqual(len(regressed["diff"]["new_violations"]), 1)


class UrlGuardTests(unittest.TestCase):
    def test_rejects_non_http(self):
        ok, _ = consent_scanner.validate_scan_url("ftp://example.com")
        self.assertFalse(ok)

    def test_adds_scheme(self):
        ok, norm = consent_scanner.validate_scan_url("example.com")
        self.assertTrue(ok)
        self.assertTrue(norm.startswith("https://"))

    def test_blocks_private_hosts_by_default(self):
        import os
        prev = os.environ.pop("CONSENT_ALLOW_PRIVATE_HOSTS", None)
        try:
            ok, _ = consent_scanner.validate_scan_url("http://127.0.0.1:8000/")
            self.assertFalse(ok)
            ok2, _ = consent_scanner.validate_scan_url("http://localhost/")
            self.assertFalse(ok2)
        finally:
            if prev is not None:
                os.environ["CONSENT_ALLOW_PRIVATE_HOSTS"] = prev


if __name__ == "__main__":
    unittest.main()
