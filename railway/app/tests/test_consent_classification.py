"""Unit tests for the consent knowledge base, classifier, and evaluator.

These cover the intelligence the feature lives or dies on: keeping *script
downloaded* vs *request transmitted* vs *identifier stored/transmitted* distinct,
and never mislabelling a Google Consent Mode cookieless ping as a violation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import consent_classifier as cc  # noqa: E402
import consent_evaluator as ev  # noqa: E402
import consent_knowledge as kb  # noqa: E402


class HostMatchingTests(unittest.TestCase):
    def test_boundary_aware_domain_match(self):
        # "t.co" (X/Twitter) must not match an unrelated host that merely contains it.
        self.assertIsNone(kb.classify_vendor("https://content.company.com/app.js"))
        self.assertEqual(kb.classify_vendor("https://t.co/abc").key, "twitter")

    def test_subdomain_match(self):
        self.assertEqual(kb.classify_vendor("https://px.ads.linkedin.com/collect").key, "linkedin")
        self.assertEqual(kb.classify_vendor("https://www.google-analytics.com/g/collect").key, "ga4")

    def test_path_scoped_pattern(self):
        # gtag.js is GA4; gtm.js is the tag manager — same domain, different path.
        self.assertEqual(kb.classify_vendor("https://www.googletagmanager.com/gtag/js?id=G-1").key, "ga4")
        self.assertEqual(kb.classify_vendor("https://www.googletagmanager.com/gtm.js?id=GTM-1").key, "gtm")


class ConsentModeTests(unittest.TestCase):
    def test_gcs_denied_is_cookieless_ping(self):
        sig = kb.parse_consent_mode("https://www.google-analytics.com/g/collect?v=2&tid=G-1&gcs=G100&en=page_view")
        self.assertTrue(sig.present)
        self.assertFalse(sig.ad_storage_granted)
        self.assertFalse(sig.analytics_storage_granted)
        self.assertTrue(sig.cookieless_ping)

    def test_gcs_granted_is_not_cookieless(self):
        sig = kb.parse_consent_mode("https://www.google-analytics.com/g/collect?gcs=G111&cid=123456.789012")
        self.assertTrue(sig.ad_storage_granted)
        self.assertTrue(sig.analytics_storage_granted)
        self.assertFalse(sig.cookieless_ping)

    def test_denied_but_with_cid_is_not_cookieless(self):
        # Storage denied but a client id is present -> not a clean cookieless ping.
        sig = kb.parse_consent_mode("https://www.google-analytics.com/g/collect?gcs=G100&cid=123456.789012")
        self.assertFalse(sig.cookieless_ping)

    def test_no_gcs_absent(self):
        self.assertFalse(kb.parse_consent_mode("https://example.com/x").present)


class IdentifierHeuristicTests(unittest.TestCase):
    def test_known_id_names(self):
        self.assertTrue(kb.looks_like_identifier("_ga", "GA1.1.123.456"))
        self.assertTrue(kb.looks_like_identifier("_fbp", "fb.1.1700000000.1234567890"))

    def test_long_random_value_is_id(self):
        self.assertTrue(kb.looks_like_identifier("sess", "aZ39kLmnQ7pR2sT0uVwX"))

    def test_short_flag_is_not_id(self):
        self.assertFalse(kb.looks_like_identifier("consent", "true"))
        self.assertFalse(kb.looks_like_identifier("count", "3"))

    def test_essential_cookie_excluded(self):
        self.assertTrue(kb.is_essential_cookie("__cf_bm"))
        self.assertTrue(kb.is_essential_cookie("OptanonConsent"))

    def test_request_carries_identifier(self):
        self.assertTrue(kb.request_carries_identifier("https://x.com/tr?_fbp=fb.1.1.2&ev=Page"))
        self.assertFalse(kb.request_carries_identifier("https://x.com/collect?v=2&gcs=G100"))


class ClassifierTests(unittest.TestCase):
    PAGE = "https://www.northwind.example/"

    def _classify_req(self, url, rtype="", post=None):
        return cc.classify_request(
            {"url": url, "resource_type": rtype, "post_data_snippet": post},
            page_host="www.northwind.example",
        )

    def test_script_download_is_not_a_beacon(self):
        f = self._classify_req("https://www.googletagmanager.com/gtag/js?id=G-1", "script")
        self.assertIsNotNone(f)
        self.assertEqual(f.evidence, cc.EV_SCRIPT_LOADED)
        self.assertFalse(f.carries_identifier)

    def test_cookieless_ping_evidence(self):
        f = self._classify_req("https://www.google-analytics.com/g/collect?v=2&gcs=G100&en=page_view", "image")
        self.assertEqual(f.evidence, cc.EV_COOKIELESS_PING)

    def test_identifier_transmitted_evidence(self):
        f = self._classify_req("https://www.facebook.com/tr?id=1&ev=PageView&_fbp=fb.1.1.2", "image")
        self.assertEqual(f.evidence, cc.EV_IDENTIFIER_TRANSMITTED)
        self.assertTrue(f.carries_identifier)

    def test_cookie_identifier(self):
        f = cc.classify_cookie(
            {"name": "_fbp", "value": "fb.1.1700000000.99", "domain": "northwind.example"},
            page_host="www.northwind.example",
        )
        self.assertEqual(f.evidence, cc.EV_IDENTIFIER_STORED)
        self.assertEqual(f.vendor_key, "meta")
        self.assertTrue(f.carries_identifier)

    def test_essential_cookie_not_identifier(self):
        f = cc.classify_cookie(
            {"name": "__cf_bm", "value": "abcdefghijklmnop", "domain": "northwind.example"},
            page_host="www.northwind.example",
        )
        self.assertEqual(f.evidence, cc.EV_COOKIE_SET)
        self.assertFalse(f.carries_identifier)


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.exp = ev.normalize_expectations(None)

    def _ev(self, finding, phase):
        return ev.evaluate_finding(finding, phase=phase, expectations=self.exp)

    def test_identifier_before_consent_is_critical_violation(self):
        f = {"evidence": cc.EV_IDENTIFIER_STORED, "category": "advertising",
             "vendor_key": "meta", "carries_identifier": True}
        r = self._ev(f, ev.PHASE_PRE)
        self.assertEqual(r["severity"], ev.SEV_CRITICAL)
        self.assertTrue(r["is_violation"])

    def test_cookieless_ping_is_ok(self):
        f = {"evidence": cc.EV_COOKIELESS_PING, "category": "analytics", "vendor_key": "ga4"}
        r = self._ev(f, ev.PHASE_PRE)
        self.assertEqual(r["severity"], ev.SEV_OK)
        self.assertFalse(r["is_violation"])

    def test_script_download_is_never_a_violation(self):
        f = {"evidence": cc.EV_SCRIPT_LOADED, "category": "advertising", "vendor_key": "meta"}
        r = self._ev(f, ev.PHASE_PRE)
        self.assertFalse(r["is_violation"])

    def test_identifier_after_accept_is_expected(self):
        f = {"evidence": cc.EV_IDENTIFIER_STORED, "category": "advertising",
             "vendor_key": "meta", "carries_identifier": True}
        r = self._ev(f, ev.PHASE_ACCEPT)
        self.assertEqual(r["severity"], ev.SEV_OK)
        self.assertFalse(r["is_violation"])

    def test_identifier_after_reject_is_critical(self):
        f = {"evidence": cc.EV_IDENTIFIER_STORED, "category": "advertising",
             "vendor_key": "meta", "carries_identifier": True}
        r = self._ev(f, ev.PHASE_REJECT)
        self.assertTrue(r["is_violation"])

    def test_beacon_without_id_before_consent_is_high(self):
        f = {"evidence": cc.EV_TRACKING_BEACON, "category": "analytics", "vendor_key": "ga4"}
        r = self._ev(f, ev.PHASE_PRE)
        self.assertEqual(r["severity"], ev.SEV_HIGH)
        self.assertTrue(r["is_violation"])

    def test_essential_identifier_allowed(self):
        f = {"evidence": cc.EV_IDENTIFIER_STORED, "category": "essential",
             "vendor_key": "cloudflare", "carries_identifier": True}
        r = self._ev(f, ev.PHASE_PRE)
        self.assertFalse(r["is_violation"])

    def test_phase_rollup_and_banner(self):
        findings = [
            {"evidence": cc.EV_IDENTIFIER_STORED, "category": "advertising",
             "vendor_key": "meta", "carries_identifier": True},
        ]
        pe = ev.evaluate_phase(findings, phase=ev.PHASE_PRE, expectations=self.exp, banner_detected=False)
        self.assertEqual(pe["status"], ev.HEALTH_FAIL)
        self.assertEqual(pe["violation_count"], 1)
        # banner issue only lowers status when it would otherwise pass
        pe2 = ev.evaluate_phase([], phase=ev.PHASE_PRE, expectations=self.exp, banner_detected=False)
        self.assertEqual(pe2["status"], ev.HEALTH_ATTENTION)
        self.assertTrue(pe2["banner_issue"])


if __name__ == "__main__":
    unittest.main()
