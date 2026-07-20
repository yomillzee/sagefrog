from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# kpi_registry is pure (no BigQuery / DB imports), so it loads standalone.
from dashboard.services import kpi_registry as k


_PAID = {"paid_google": {"spend": 1000.0, "conversions": 30.0, "conversion_value": 4200.0}}


class TestSpecHelpers(unittest.TestCase):
    def test_spec_type_valid_and_invalid(self):
        self.assertEqual(k.spec_type({"type": "hubspot_mqls"}), "hubspot_mqls")
        self.assertIsNone(k.spec_type({"type": "made_up"}))
        self.assertIsNone(k.spec_type(None))
        self.assertIsNone(k.spec_type({}))

    def test_needs_hubspot_only_for_hubspot_source(self):
        self.assertTrue(k.needs_hubspot({"type": "hubspot_mqls"}))
        self.assertFalse(k.needs_hubspot({"type": "google_ads_conversions"}))
        self.assertFalse(k.needs_hubspot({"type": "google_ads_roas"}))
        self.assertFalse(k.needs_hubspot(None))

    def test_choices_start_with_no_kpi(self):
        self.assertEqual(k.KPI_CHOICES[0], ("", "No KPI"))
        vals = [v for v, _ in k.KPI_CHOICES]
        for t in k.KPI_TYPES:
            self.assertIn(t, vals)


class TestResolveKpi(unittest.TestCase):
    def test_unset_or_unknown_returns_none(self):
        self.assertIsNone(k.resolve_kpi(None))
        self.assertIsNone(k.resolve_kpi({"type": "nope"}, paid_mtd=_PAID))

    def test_google_ads_conversions_value_from_paid_google(self):
        r = k.resolve_kpi({"type": "google_ads_conversions"}, paid_mtd=_PAID)
        self.assertEqual(r["value"], 30.0)
        self.assertEqual(r["format"], "number")
        self.assertIsNone(r["goal"])
        self.assertEqual(r["status"], "neutral")

    def test_google_ads_roas_is_value_over_spend(self):
        r = k.resolve_kpi({"type": "google_ads_roas", "goal": 4.0}, paid_mtd=_PAID)
        self.assertEqual(r["value"], 4.2)  # 4200 / 1000
        self.assertEqual(r["format"], "multiplier")
        self.assertEqual(r["status"], "good")  # 105% of goal, rate metric
        self.assertEqual(r["pct_to_goal"], 105.0)

    def test_roas_without_spend_is_no_data(self):
        r = k.resolve_kpi({"type": "google_ads_roas", "goal": 4.0}, paid_mtd={})
        self.assertIsNone(r["value"])
        self.assertEqual(r["status"], "neutral")

    def test_hubspot_mqls_uses_mql_count_and_label_override(self):
        r = k.resolve_kpi(
            {"type": "hubspot_mqls", "label": "Leads", "goal": 50},
            mql_count=42, pct_month_elapsed=66.0,
        )
        self.assertEqual(r["value"], 42.0)
        self.assertEqual(r["label"], "Leads")
        self.assertEqual(r["pct_to_goal"], 84.0)
        # 84% of goal on a 66%-elapsed month is ahead of pace -> good.
        self.assertEqual(r["status"], "good")

    def test_paced_metric_behind_pace_grades_bad(self):
        # 10 conversions vs a 100 goal is 10%; on a 50%-elapsed month that is
        # well behind pace.
        paid = {"paid_google": {"spend": 500.0, "conversions": 10.0, "conversion_value": 0.0}}
        r = k.resolve_kpi(
            {"type": "google_ads_conversions", "goal": 100},
            paid_mtd=paid, pct_month_elapsed=50.0,
        )
        self.assertEqual(r["pct_to_goal"], 10.0)
        self.assertEqual(r["status"], "bad")

    def test_hitting_goal_is_always_good(self):
        r = k.resolve_kpi(
            {"type": "hubspot_mqls", "goal": 40}, mql_count=40, pct_month_elapsed=20.0,
        )
        self.assertEqual(r["status"], "good")
        self.assertEqual(r["pct_to_goal"], 100.0)

    def test_zero_or_missing_goal_is_neutral_no_pct(self):
        r = k.resolve_kpi({"type": "google_ads_conversions", "goal": 0}, paid_mtd=_PAID)
        self.assertIsNone(r["goal"])
        self.assertIsNone(r["pct_to_goal"])
        self.assertEqual(r["status"], "neutral")


if __name__ == "__main__":
    unittest.main()
