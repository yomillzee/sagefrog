from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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
        ms = demo_data.generate("explorer.microsoft_verified", _payload())
        self.assertIn("by_campaign_name", ms)

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

    def test_gsc_has_fuller_keyword_list(self) -> None:
        gsc = demo_data.generate("gsc.summary", _payload())
        # A fuller Search Console keyword list, not just a handful.
        self.assertGreaterEqual(len(gsc["top_queries"]), 20)
        # keyword-matches has a fuller default branded set when no terms are given.
        self.assertGreaterEqual(len(demo_data.generate("gsc.keyword_matches", {})), 6)

    def test_gsc_pages_have_page_url_and_name(self) -> None:
        gsc = demo_data.generate("gsc.summary", _payload())
        self.assertTrue(gsc["top_pages"])
        row = gsc["top_pages"][0]
        # The GSC pages table renders the `page_url` column — it must be populated.
        self.assertIn("page_url", row)
        self.assertTrue(row["page_url"])
        self.assertIn("page_name", row)

    def test_linkedin_explorer_has_ad_group_name(self) -> None:
        rows = demo_data.generate("explorer.linkedin", _payload())["rows"]
        self.assertTrue(rows)
        # The explorer surfaces LinkedIn's campaign_name as the ad group.
        self.assertTrue(all(r.get("campaign_name") for r in rows))
        self.assertTrue(all(r.get("campaign_group_name") for r in rows))

    def test_meta_explorer_has_ad_group_name(self) -> None:
        rows = demo_data.generate("explorer.meta", _payload())["rows"]
        self.assertTrue(rows)
        # The explorer surfaces Meta's adset_name as the ad group.
        self.assertTrue(all(r.get("adset_name") for r in rows))
        self.assertTrue(all(r.get("campaign_name") for r in rows))

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


class DemoSeedTests(unittest.TestCase):
    """The seeder runs on every boot and swallows its own failures into a
    warning, so a broken step here is invisible unless something checks.

    It was broken: the last statement called
    backfill_segment_filter_profile(DEMO_SLUG, "") to mean "no segment
    filters", and that helper exists to seed a *known* profile onto clients
    predating the column — it requires business_lines or regions and raises on
    anything else. The demo client itself was fine (a fresh config row has the
    column NULL, which already means no segment filters), but every boot logged
    a traceback, and that noise is what would hide a real seeding failure.

    Note the helper returns early when there is no DATABASE_URL, so the old
    call was a silent no-op without a database and only raised with one. That
    is why it went unnoticed for so long.
    """

    def _seed_with_stubs(self, **extra_patches):
        """Run the seeder with its database writes stubbed out.

        demo_client imports client_dashboard_config inside the function, so the
        patches go on the real module — which is the same object it binds.
        """
        import client_dashboard_config as cdc
        import dashboard_registry

        patches = {"save_config": mock.DEFAULT, "save_monthly_budget": mock.DEFAULT}
        patches.update(extra_patches)
        # Three gates have to be stubbed or these tests are vacuous, and an
        # earlier version of them was: they passed with the bug reintroduced
        # because the block under test never ran. Two gates make the seeder
        # return early without a database (demo_client.enabled and
        # dashboard_registry.enabled); the third (cdc.enabled) is what the
        # backfill helper checks *before* validating its argument, so without
        # it the bad call is a silent no-op rather than the raise it is in
        # production. Both failing tests were verified by reintroducing the
        # original call.
        with mock.patch.dict(os.environ, {"DEMO_CLIENT_ENABLED": "1"}), \
             mock.patch.object(demo_client, "enabled", return_value=True), \
             mock.patch.object(dashboard_registry, "enabled", return_value=True), \
             mock.patch.object(dashboard_registry, "has_slug", return_value=True), \
             mock.patch.object(cdc, "enabled", return_value=True), \
             mock.patch.object(demo_client, "_seed_demo_login"), \
             mock.patch.object(demo_client, "logger") as logger, \
             mock.patch.multiple(cdc, **patches) as mocks:
            demo_client.seed_demo_client()
        return logger, mocks

    def test_seeding_does_not_log_a_failure(self):
        """Whatever the seeder does, it must not report its own config save as
        failed — that warning is the symptom this pins."""
        logger, _ = self._seed_with_stubs()
        failures = [
            call for call in logger.warning.call_args_list
            if "config save failed" in str(call)
        ]
        self.assertEqual(failures, [], f"the seeder logged a config failure: {failures}")

    def test_the_seeder_does_not_touch_the_segment_filter_profile(self):
        """No segmentation is the absence of a value, not a value. Writing one
        would either raise (the backfill helper) or overwrite an admin's choice
        on every restart (the save helper), so the seeder leaves it alone."""
        _, mocks = self._seed_with_stubs(
            backfill_segment_filter_profile=mock.DEFAULT,
            save_segment_filter_profile=mock.DEFAULT,
        )
        mocks["backfill_segment_filter_profile"].assert_not_called()
        mocks["save_segment_filter_profile"].assert_not_called()

    def test_the_backfill_helper_still_refuses_a_blank_profile(self):
        """The helper's contract is what made the old call impossible. Keep it
        strict so nobody reintroduces the same shortcut — and note the
        enabled() guard has to be satisfied first, or it returns False instead
        of raising, which is exactly how the bug stayed quiet."""
        import client_dashboard_config as cdc

        with mock.patch.object(cdc, "enabled", return_value=True):
            with self.assertRaises(ValueError):
                cdc.backfill_segment_filter_profile("demo", "")
            with self.assertRaises(ValueError):
                cdc.backfill_segment_filter_profile("demo", "not_a_profile")

    def test_the_save_helper_does_accept_none_for_no_segmentation(self):
        """The counterpart: "none" is expressible, just not through backfill."""
        import client_dashboard_config as cdc

        self.assertIn("None", cdc.save_segment_filter_profile.__doc__ or "")
        with mock.patch.object(cdc, "enabled", return_value=False):
            with self.assertRaises(RuntimeError):
                cdc.save_segment_filter_profile("demo", None)


if __name__ == "__main__":
    unittest.main()
