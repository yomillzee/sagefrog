"""Per-metric goal targets: normalization, proration, and grading.

The whole point of this module is that the *dashboard* only divides and picks a
colour — every judgement about what a number means happens here, so this is
where it has to be pinned down. ``metric_goals`` imports nothing from the app, so
these need no stubbing.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.services import metric_goals


class NormalizeGoalsTests(unittest.TestCase):
    def test_keeps_known_keys_and_coerces_numeric_strings(self):
        got = metric_goals.normalize_goals({"spend": "50000", "cpa": 120.5})
        self.assertEqual(got, {"spend": 50000.0, "cpa": 120.5})

    def test_drops_unknown_keys_rather_than_raising(self):
        # A stale key from an older editor must not block a save.
        got = metric_goals.normalize_goals({"spend": 100, "roas": 4, "": 9})
        self.assertEqual(got, {"spend": 100.0})

    def test_drops_non_positive_and_unparseable_values(self):
        got = metric_goals.normalize_goals(
            {"spend": 0, "clicks": -5, "ctr": "abc", "cpc": None, "cpa": "", "impressions": 10}
        )
        self.assertEqual(got, {"impressions": 10.0})

    def test_tolerates_a_json_string_and_junk_payloads(self):
        self.assertEqual(metric_goals.normalize_goals('{"ctr": 3.5}'), {"ctr": 3.5})
        self.assertEqual(metric_goals.normalize_goals("not json"), {})
        self.assertEqual(metric_goals.normalize_goals(None), {})
        self.assertEqual(metric_goals.normalize_goals([1, 2]), {})

    def test_emits_keys_in_catalogue_order(self):
        got = metric_goals.normalize_goals({"cpa": 1, "spend": 2, "ctr": 3})
        self.assertEqual(list(got), ["spend", "ctr", "cpa"])


class ProrationTests(unittest.TestCase):
    def test_monthly_goal_scales_to_the_window(self):
        # 10 of 31 days of a $50k month.
        self.assertAlmostEqual(
            metric_goals.prorate(50000, window_days=10, days_in_month=31),
            50000 * 10 / 31,
            places=6,
        )

    def test_a_full_month_window_prorates_to_the_whole_goal(self):
        self.assertAlmostEqual(
            metric_goals.prorate(50000, window_days=31, days_in_month=31), 50000.0
        )

    def test_degenerate_inputs_fall_back_to_the_full_goal(self):
        self.assertEqual(metric_goals.prorate(100, window_days=0, days_in_month=30), 100.0)
        self.assertEqual(metric_goals.prorate(100, window_days=10, days_in_month=0), 100.0)

    def test_window_days_is_inclusive_and_never_zero(self):
        self.assertEqual(metric_goals.window_days(date(2026, 8, 1), date(2026, 8, 10)), 10)
        self.assertEqual(metric_goals.window_days(date(2026, 8, 5), date(2026, 8, 5)), 1)

    def test_days_in_month_reads_the_end_of_the_window(self):
        self.assertEqual(metric_goals.days_in_month(date(2026, 2, 20)), 28)
        self.assertEqual(metric_goals.days_in_month(date(2024, 2, 20)), 29)


class GradeTests(unittest.TestCase):
    def test_higher_is_better_metrics(self):
        self.assertEqual(metric_goals.grade(120, "up"), "good")
        self.assertEqual(metric_goals.grade(90, "up"), "good")
        self.assertEqual(metric_goals.grade(75, "up"), "warn")
        self.assertEqual(metric_goals.grade(40, "up"), "bad")

    def test_lower_is_better_metrics_invert_the_scale(self):
        # CPA at 80% of its ceiling is good; at 200% it is bad. The same
        # percentage that is "bad" for CTR is "good" for CPA.
        self.assertEqual(metric_goals.grade(80, "down"), "good")
        self.assertEqual(metric_goals.grade(100, "down"), "good")
        self.assertEqual(metric_goals.grade(115, "down"), "warn")
        self.assertEqual(metric_goals.grade(200, "down"), "bad")

    def test_band_metrics_treat_undershooting_as_a_miss_too(self):
        # Spend: half the plan is as much a miss as double it.
        self.assertEqual(metric_goals.grade(100, "band"), "good")
        self.assertEqual(metric_goals.grade(50, "band"), "bad")
        self.assertEqual(metric_goals.grade(150, "band"), "bad")
        self.assertEqual(metric_goals.grade(80, "band"), "warn")
        self.assertEqual(metric_goals.grade(120, "band"), "warn")

    def test_missing_value_or_unknown_direction_is_neutral(self):
        self.assertEqual(metric_goals.grade(None, "up"), "neutral")
        self.assertEqual(metric_goals.grade(100, "sideways"), "neutral")


class ResolveGoalsTests(unittest.TestCase):
    def test_cumulative_metric_is_prorated_and_rate_metric_is_not(self):
        got = metric_goals.resolve_goals(
            {"spend": 31000, "cpa": 120},
            start=date(2026, 8, 1),
            end=date(2026, 8, 10),
        )
        # 10 of August's 31 days.
        self.assertAlmostEqual(got["goals"]["spend"]["target"], 10000.0, places=2)
        self.assertEqual(got["goals"]["spend"]["goal"], 31000.0)
        self.assertTrue(got["goals"]["spend"]["cumulative"])
        # A rate is the same rate over any window.
        self.assertEqual(got["goals"]["cpa"]["target"], 120.0)
        self.assertFalse(got["goals"]["cpa"]["cumulative"])

    def test_carries_direction_and_thresholds_the_page_needs(self):
        got = metric_goals.resolve_goals(
            {"spend": 1000, "ctr": 3.5, "cpa": 90},
            start=date(2026, 8, 1),
            end=date(2026, 8, 31),
        )
        self.assertEqual(got["goals"]["spend"]["direction"], "band")
        self.assertEqual(got["goals"]["ctr"]["direction"], "up")
        self.assertEqual(got["goals"]["cpa"]["direction"], "down")
        self.assertIn("up", got["thresholds"])
        self.assertIn("good_low", got["thresholds"]["band"])

    def test_window_metadata_describes_the_range_that_was_graded(self):
        got = metric_goals.resolve_goals(
            {"clicks": 300}, start=date(2026, 2, 1), end=date(2026, 2, 14)
        )
        self.assertEqual(got["window"]["days"], 14)
        self.assertEqual(got["window"]["days_in_month"], 28)
        self.assertEqual(got["window"]["start"], "2026-02-01")

    def test_empty_goals_resolve_to_an_empty_map_not_an_error(self):
        got = metric_goals.resolve_goals(None, start=date(2026, 8, 1), end=date(2026, 8, 5))
        self.assertEqual(got["goals"], {})

    def test_a_stored_unknown_metric_is_dropped_on_resolve(self):
        got = metric_goals.resolve_goals(
            {"roas": 4, "clicks": 100}, start=date(2026, 8, 1), end=date(2026, 8, 31)
        )
        self.assertEqual(list(got["goals"]), ["clicks"])


class GradingMatchesResolvedTargets(unittest.TestCase):
    """The end-to-end shape the dashboard actually performs: value ÷ target."""

    def test_on_pace_spend_grades_good_midmonth(self):
        resolved = metric_goals.resolve_goals(
            {"spend": 31000}, start=date(2026, 8, 1), end=date(2026, 8, 10)
        )
        target = resolved["goals"]["spend"]["target"]
        # Spent exactly 10 days' worth by day 10.
        pct = 10000.0 / target * 100
        self.assertEqual(metric_goals.grade(pct, "band"), "good")

    def test_barely_spending_grades_bad_even_though_it_is_under_budget(self):
        resolved = metric_goals.resolve_goals(
            {"spend": 31000}, start=date(2026, 8, 1), end=date(2026, 8, 10)
        )
        target = resolved["goals"]["spend"]["target"]
        pct = 2000.0 / target * 100
        self.assertEqual(metric_goals.grade(pct, "band"), "bad")


if __name__ == "__main__":
    unittest.main()
