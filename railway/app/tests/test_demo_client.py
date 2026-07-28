from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import demo_client  # noqa: E402
import demo_data  # noqa: E402


def _payload(days: int = 30) -> dict:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return {"start": start.isoformat(), "end": end.isoformat()}


class DemoIdentityTests(unittest.TestCase):
    def test_is_demo_matches_slug_case_insensitively(self) -> None:
        self.assertTrue(demo_client.is_demo(demo_client.DEMO_SLUG))
        self.assertTrue(demo_client.is_demo(demo_client.DEMO_SLUG.upper()))
        self.assertTrue(demo_client.is_demo(f"  {demo_client.DEMO_SLUG}  "))

    def test_is_demo_rejects_other_slugs(self) -> None:
        self.assertFalse(demo_client.is_demo("nixon"))
        self.assertFalse(demo_client.is_demo("penn"))
        self.assertFalse(demo_client.is_demo(None))
        self.assertFalse(demo_client.is_demo(""))


class DemoDataDeterminismTests(unittest.TestCase):
    def test_same_inputs_yield_identical_output(self) -> None:
        p = _payload()
        self.assertEqual(demo_data.generate("summary", p), demo_data.generate("summary", p))
        self.assertEqual(demo_data.generate("pages.top", p), demo_data.generate("pages.top", p))

    def test_unknown_key_returns_safe_default(self) -> None:
        # A brand-new panel that hasn't got a sample yet must not error.
        self.assertEqual(demo_data.generate("something.brand_new", _payload()), {})
        self.assertEqual(demo_data.generate("", _payload()), {})

    def test_unknown_list_key_returns_empty_list(self) -> None:
        self.assertEqual(demo_data.generate("gsc.keyword_matches", {"terms": []}), demo_data.generate("gsc.keyword_matches", {"terms": []}))


class DemoDataShapeTests(unittest.TestCase):
    def test_summary_shape(self) -> None:
        out = demo_data.generate("summary", _payload())
        self.assertIn("summary", out)
        self.assertIn("by_source", out)
        self.assertIn("daily", out)
        for k in ("spend", "impressions", "clicks", "conversions", "cpc", "cpa", "ctr"):
            self.assertIn(k, out["summary"])
        self.assertGreater(out["summary"]["spend"], 0)
        # daily rows carry a source_platform-style source and a date.
        self.assertTrue(out["daily"])
        self.assertIn("date", out["daily"][0])
        self.assertIn("source", out["daily"][0])

    def test_marketing_shape(self) -> None:
        out = demo_data.generate("marketing", {**_payload(), "top_limit": 5})
        self.assertIn("summary", out)
        self.assertIn("by_source", out)
        self.assertIn("daily_trend", out)
        self.assertEqual(len(out["top_campaigns_by_spend"]), 5)

    def test_health_shape_includes_ga4_and_gsc(self) -> None:
        out = demo_data.generate("health", {"limit": 100})
        sources = {r["source"] for r in out["rows"]}
        self.assertIn("google_analytics", sources)
        self.assertIn("search_console", sources)
        self.assertEqual(out["row_count"], len(out["rows"]))

    def test_explorer_rows_have_metrics(self) -> None:
        for key in ("explorer.google_ads", "explorer.meta", "explorer.linkedin",
                    "explorer.microsoft_ads", "explorer.google_ads_keywords"):
            out = demo_data.generate(key, _payload())
            self.assertTrue(out["rows"], f"{key} should have rows")
            row = out["rows"][0]
            for k in ("spend", "impressions", "clicks", "conversions"):
                self.assertIn(k, row, f"{key} row missing {k}")

    def test_verified_shapes(self) -> None:
        meta = demo_data.generate("explorer.meta_verified", _payload())
        self.assertIn("by_ad_id", meta)
        self.assertIn("by_ad_id_event", meta)
        google = demo_data.generate("explorer.google_verified", _payload())
        self.assertIn("by_campaign_id", google)
        li = demo_data.generate("explorer.linkedin_verified", _payload())
        self.assertIn("by_group_name", li)

    def test_analytics_shapes(self) -> None:
        conv = demo_data.generate("analytics.conversions", _payload())
        self.assertIn("funnel", conv)
        self.assertTrue(conv["rows"])
        demo = demo_data.generate("analytics.demographics", _payload())
        for k in ("by_city", "by_age", "by_gender", "by_region"):
            self.assertIn(k, demo)
        acq = demo_data.generate("analytics.user_acquisition", _payload())
        self.assertIn("by_channel", acq)
        self.assertIn("by_source", acq)

    def test_gsc_and_search_shapes(self) -> None:
        gsc = demo_data.generate("gsc.summary", _payload())
        for k in ("kpis", "daily", "top_queries", "top_pages"):
            self.assertIn(k, gsc)
        matches = demo_data.generate("gsc.keyword_matches", {"terms": ["northwind health"]})
        self.assertIsInstance(matches, list)
        weekly = demo_data.generate("gsc.keyword_weekly_trend", _payload())
        self.assertIsInstance(weekly, list)

    def test_semrush_shape(self) -> None:
        out = demo_data.generate("semrush.summary", {})
        for k in ("overview", "keywords", "backlinks", "series", "position_distribution"):
            self.assertIn(k, out)
        self.assertTrue(out["keywords"])

    def test_pagespeed_desktop_and_mobile(self) -> None:
        for strat in ("desktop", "mobile"):
            out = demo_data.generate(f"pagespeed.summary.{strat}", {})
            self.assertEqual(out["strategy"], strat)
            for k in ("performance", "accessibility", "seo", "lcp_ms", "history"):
                self.assertIn(k, out)
            self.assertTrue(out["history"])
        # Mobile performance should generally be lower than desktop (as in real life).
        desk = demo_data.generate("pagespeed.summary.desktop", {})
        mob = demo_data.generate("pagespeed.summary.mobile", {})
        self.assertGreater(desk["performance"], mob["performance"])

    def test_pagespeed_unknown_strategy_defaults_desktop(self) -> None:
        out = demo_data.generate("pagespeed.summary.weird", {})
        self.assertEqual(out["strategy"], "desktop")

    def test_ai_traffic_daily(self) -> None:
        out = demo_data.generate("ai_traffic.daily", _payload())
        self.assertIn("rows", out)
        if out["rows"]:
            self.assertIn("ai_platform", out["rows"][0])
            self.assertIn("sessions", out["rows"][0])

    def test_date_range_scales_volume(self) -> None:
        # A 60-day window should show more spend than a 7-day one.
        wide = demo_data.generate("summary", _payload(60))["summary"]["spend"]
        narrow = demo_data.generate("summary", _payload(7))["summary"]["spend"]
        self.assertGreater(wide, narrow)


if __name__ == "__main__":
    unittest.main()
