"""The findings engine: what gets reported, what gets suppressed, and in what order.

``findings_service`` is pure, so these drive it with hand-built payloads and no
warehouse. The interesting cases are the suppressions — a findings list that
reports everything is as useless as one that reports nothing, and the rules that
keep it short are exactly the ones that would rot silently.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.services import findings_service as fs

WINDOW = (date(2026, 8, 1), date(2026, 8, 31))


def summary(*, spend=0, impressions=0, clicks=0, conversions=0, by_source=None):
    return {
        "summary": {
            "spend": spend, "impressions": impressions,
            "clicks": clicks, "conversions": conversions,
        },
        "by_source": by_source or {},
    }


def kinds(result, kind):
    return [f for f in result["findings"] if f["kind"] == kind]


def by_id(result, finding_id):
    return next((f for f in result["findings"] if f["id"] == finding_id), None)


class MovementTests(unittest.TestCase):
    def test_a_real_movement_is_reported_with_both_values(self):
        got = fs.build_findings(
            current=summary(conversions=138, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=980, impressions=49000, spend=4900),
            window=WINDOW, compare_label="last month",
        )
        f = by_id(got, "movement:conversions")
        self.assertIsNotNone(f)
        self.assertIn("up 38%", f["headline"])
        self.assertIn("100", f["headline"])
        self.assertIn("138", f["headline"])
        self.assertEqual(f["severity"], "good")

    def test_a_small_base_is_suppressed_however_large_the_percentage(self):
        # 1 → 4 conversions is +300% and means nothing.
        got = fs.build_findings(
            current=summary(conversions=4, clicks=90, impressions=1500, spend=200),
            previous=summary(conversions=1, clicks=80, impressions=1400, spend=180),
            window=WINDOW,
        )
        self.assertEqual(kinds(got, "movement"), [])

    def test_a_move_below_the_threshold_is_not_news(self):
        got = fs.build_findings(
            current=summary(conversions=103, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=1000, impressions=50000, spend=5000),
            window=WINDOW,
        )
        self.assertEqual(kinds(got, "movement"), [])

    def test_severity_follows_the_metrics_meaning_not_the_arrow(self):
        # Spend flat, conversions halved => CPA doubled, which is bad even
        # though the number went "up".
        got = fs.build_findings(
            current=summary(conversions=50, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=1000, impressions=50000, spend=5000),
            window=WINDOW,
        )
        cpa = by_id(got, "movement:cpa")
        self.assertIsNotNone(cpa)
        self.assertEqual(cpa["severity"], "bad")
        self.assertIn("up", cpa["headline"])

    def test_a_rate_with_too_little_volume_behind_it_is_suppressed(self):
        # CTR doubled, but on 40 impressions.
        got = fs.build_findings(
            current=summary(impressions=40, clicks=4),
            previous=summary(impressions=40, clicks=2),
            window=WINDOW,
        )
        self.assertEqual([f for f in kinds(got, "movement") if f["key"] == "ctr"], [])

    def test_no_comparison_window_means_no_movement_findings(self):
        got = fs.build_findings(
            current=summary(conversions=138, clicks=1000, impressions=50000, spend=5000),
            previous=None, window=WINDOW,
        )
        self.assertEqual(kinds(got, "movement"), [])
        self.assertFalse(got["inputs"]["has_comparison"])


class DriverTests(unittest.TestCase):
    def _payloads(self, cur_google, prev_google, cur_meta, prev_meta):
        return (
            summary(conversions=cur_google + cur_meta, clicks=2000, impressions=90000,
                    spend=9000,
                    by_source={"paid_google": {"conversions": cur_google},
                               "paid_meta": {"conversions": cur_meta}}),
            summary(conversions=prev_google + prev_meta, clicks=2000, impressions=90000,
                    spend=9000,
                    by_source={"paid_google": {"conversions": prev_google},
                               "paid_meta": {"conversions": prev_meta}}),
        )

    def test_a_dominant_platform_is_named(self):
        cur, prev = self._payloads(160, 100, 55, 50)
        got = fs.build_findings(current=cur, previous=prev, window=WINDOW)
        f = by_id(got, "movement:conversions")
        self.assertIn("Google Ads", f["detail"])
        self.assertEqual(f["evidence"]["driver"]["source"], "paid_google")

    def test_an_evenly_split_change_names_nobody(self):
        # Both platforms moved the same amount; naming either would mislead.
        cur, prev = self._payloads(130, 100, 130, 100)
        got = fs.build_findings(current=cur, previous=prev, window=WINDOW)
        f = by_id(got, "movement:conversions")
        self.assertIsNone(f["evidence"]["driver"])
        self.assertNotIn("accounts for", f["detail"])

    def test_blended_rate_metrics_never_get_an_attribution(self):
        # "Which platform drove the CPA change" has no honest answer — a blended
        # rate moves when the mix shifts, with no platform's own rate moving.
        cur, prev = self._payloads(160, 100, 55, 50)
        got = fs.build_findings(current=cur, previous=prev, window=WINDOW)
        cpa = by_id(got, "movement:cpa")
        if cpa:
            self.assertIsNone(cpa["evidence"]["driver"])


class TimelineTests(unittest.TestCase):
    def test_a_movement_that_coincides_with_an_event_says_so(self):
        got = fs.build_findings(
            current=summary(conversions=50, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=1000, impressions=50000, spend=5000),
            annotations=[{"id": 7, "event_date": "2026-08-12", "title": "Site migration",
                          "category_label": "Website", "color": "#7c3aed"}],
            window=WINDOW,
        )
        f = by_id(got, "movement:conversions")
        self.assertIn("Site migration", f["detail"])
        self.assertIn(7, f["evidence"]["annotations"])

    def test_events_outside_the_window_are_ignored(self):
        got = fs.build_findings(
            current=summary(conversions=50, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=1000, impressions=50000, spend=5000),
            annotations=[{"id": 7, "event_date": "2026-05-01", "title": "Old thing"}],
            window=WINDOW,
        )
        self.assertNotIn("Old thing", by_id(got, "movement:conversions")["detail"])
        self.assertEqual(got["timeline_events"], [])

    def test_a_ranged_event_overlapping_the_window_counts(self):
        got = fs.build_findings(
            current=summary(conversions=50, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=1000, impressions=50000, spend=5000),
            annotations=[{"id": 9, "event_date": "2026-07-20", "end_date": "2026-08-05",
                          "title": "Budget freeze"}],
            window=WINDOW,
        )
        self.assertIn("Budget freeze", by_id(got, "movement:conversions")["detail"])


class GoalTests(unittest.TestCase):
    def _goals(self, metric, target, direction):
        return {
            "goals": {metric: {"target": target, "goal": target, "direction": direction,
                               "cumulative": False, "label": metric, "format": "currency",
                               "note": "Target rate."}},
            "thresholds": fs_thresholds(),
        }

    def test_a_metric_over_its_ceiling_is_reported(self):
        got = fs.build_findings(
            current=summary(spend=5000, conversions=31),   # CPA ≈ 161
            goals=self._goals("cpa", 120, "down"), window=WINDOW,
        )
        f = by_id(got, "goal:cpa")
        self.assertIsNotNone(f)
        self.assertEqual(f["severity"], "bad")
        self.assertIn("over its $120 target", f["headline"])

    def test_a_metric_comfortably_on_target_is_not_a_finding(self):
        got = fs.build_findings(
            current=summary(spend=5000, conversions=50),   # CPA = 100, under 120
            goals=self._goals("cpa", 120, "down"), window=WINDOW,
        )
        self.assertEqual(kinds(got, "goal"), [])

    def test_a_band_metric_reports_undershooting_too(self):
        got = fs.build_findings(
            current=summary(spend=2000),
            goals=self._goals("spend", 10000, "band"), window=WINDOW,
        )
        f = by_id(got, "goal:spend")
        self.assertIsNotNone(f)
        self.assertIn("under plan", f["headline"])


def fs_thresholds():
    """The threshold block metric_goals ships in its payload."""
    return {
        "up": {"good": 90.0, "warn": 60.0},
        "down": {"good": 100.0, "warn": 125.0},
        "band": {"good_low": 90.0, "good_high": 110.0,
                 "warn_low": 75.0, "warn_high": 125.0},
    }


class BenchmarkTests(unittest.TestCase):
    def _bench(self, metric, value, median, n=9, thin=False):
        return {
            "available": True,
            "metrics": {metric: {
                "label": metric, "format": "percent", "direction": "up",
                "value": value, "peer_label": "health care",
                "peer": {"median": median, "n": n, "thin": thin},
            }},
        }

    def test_a_wide_gap_is_reported_with_its_sample_size(self):
        got = fs.build_findings(
            current=summary(), benchmarks=self._bench("ctr", 3.0, 2.0), window=WINDOW,
        )
        f = by_id(got, "benchmark:ctr")
        self.assertIsNotNone(f)
        self.assertEqual(f["severity"], "good")
        self.assertIn("better than", f["headline"])
        self.assertIn("9 comparable accounts", f["detail"])

    def test_a_narrow_gap_is_not_worth_saying(self):
        got = fs.build_findings(
            current=summary(), benchmarks=self._bench("ctr", 2.05, 2.0), window=WINDOW,
        )
        self.assertEqual(kinds(got, "benchmark"), [])

    def test_a_thin_sample_is_labelled_and_scored_down(self):
        thin = fs.build_findings(
            current=summary(), benchmarks=self._bench("ctr", 3.0, 2.0, n=2, thin=True),
            window=WINDOW,
        )
        deep = fs.build_findings(
            current=summary(), benchmarks=self._bench("ctr", 3.0, 2.0, n=9), window=WINDOW,
        )
        self.assertIn("directional", by_id(thin, "benchmark:ctr")["detail"])
        self.assertLess(
            by_id(thin, "benchmark:ctr")["score"], by_id(deep, "benchmark:ctr")["score"]
        )

    def test_disabled_benchmarks_contribute_nothing(self):
        got = fs.build_findings(
            current=summary(), benchmarks={"available": False, "metrics": {}}, window=WINDOW,
        )
        self.assertEqual(kinds(got, "benchmark"), [])
        self.assertFalse(got["inputs"]["has_benchmarks"])


class FreshnessTests(unittest.TestCase):
    def test_a_lagging_source_is_reported(self):
        got = fs.build_findings(
            current=summary(),
            freshness=[{"source": "meta", "latest_date": "2026-08-27"}],
            window=WINDOW,
        )
        f = by_id(got, "data_quality:meta")
        self.assertIsNotNone(f)
        self.assertIn("Meta Ads", f["headline"])
        self.assertEqual(f["evidence"]["lag_days"], 4)

    def test_the_normal_one_day_platform_lag_is_not_flagged(self):
        got = fs.build_findings(
            current=summary(),
            freshness=[{"source": "google", "latest_date": "2026-08-30"}],
            window=WINDOW,
        )
        self.assertEqual(kinds(got, "data_quality"), [])

    def test_stale_data_outranks_a_large_movement(self):
        # The whole point: a movement computed on incomplete data must not be the
        # first thing someone reads.
        got = fs.build_findings(
            current=summary(conversions=200, clicks=2000, impressions=90000, spend=9000),
            previous=summary(conversions=100, clicks=1000, impressions=45000, spend=4500),
            freshness=[{"source": "meta", "latest_date": "2026-08-20"}],
            window=WINDOW,
        )
        self.assertEqual(got["findings"][0]["kind"], "data_quality")


class RankingAndShapeTests(unittest.TestCase):
    def test_findings_are_sorted_by_score_and_limited(self):
        got = fs.build_findings(
            current=summary(conversions=200, clicks=2000, impressions=90000, spend=9000),
            previous=summary(conversions=100, clicks=1000, impressions=45000, spend=4500),
            window=WINDOW, limit=2,
        )
        self.assertEqual(len(got["findings"]), 2)
        scores = [f["score"] for f in got["findings"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreaterEqual(got["total_found"], 2)

    def test_conversions_outrank_impressions_on_an_equal_move(self):
        got = fs.build_findings(
            current=summary(conversions=200, clicks=2000, impressions=90000, spend=9000),
            previous=summary(conversions=100, clicks=1000, impressions=45000, spend=4500),
            window=WINDOW, limit=8,
        )
        ids = [f["id"] for f in got["findings"]]
        self.assertLess(ids.index("movement:conversions"), ids.index("movement:impressions"))

    def test_an_empty_client_produces_an_empty_list_not_an_error(self):
        got = fs.build_findings(current=None, window=WINDOW)
        self.assertEqual(got["findings"], [])
        self.assertEqual(got["total_found"], 0)

    def test_every_finding_carries_the_fields_a_consumer_needs(self):
        got = fs.build_findings(
            current=summary(conversions=200, clicks=2000, impressions=90000, spend=9000),
            previous=summary(conversions=100, clicks=1000, impressions=45000, spend=4500),
            window=WINDOW,
        )
        self.assertTrue(got["findings"])
        for f in got["findings"]:
            for key in ("id", "kind", "key", "severity", "headline", "detail", "score", "evidence"):
                self.assertIn(key, f)
            self.assertIn(f["severity"], ("good", "warn", "bad", "neutral"))

    def test_evidence_carries_the_numbers_behind_the_sentence(self):
        # A later prose layer must be able to check every claim without the
        # warehouse, so the numbers ride along with the wording.
        got = fs.build_findings(
            current=summary(conversions=138, clicks=1000, impressions=50000, spend=5000),
            previous=summary(conversions=100, clicks=980, impressions=49000, spend=4900),
            window=WINDOW,
        )
        ev = by_id(got, "movement:conversions")["evidence"]
        self.assertEqual(ev["current"], 138.0)
        self.assertEqual(ev["previous"], 100.0)
        self.assertEqual(ev["pct_change"], 38.0)


class FormattingTests(unittest.TestCase):
    def test_values_render_the_way_their_cards_do(self):
        self.assertEqual(fs.fmt(5000, "currency"), "$5,000")
        self.assertEqual(fs.fmt(1.5, "currency2"), "$1.50")
        self.assertEqual(fs.fmt(2.5, "percent"), "2.50%")
        self.assertEqual(fs.fmt(1234, "number"), "1,234")
        self.assertEqual(fs.fmt(None, "number"), "—")

    def test_platform_keys_get_human_names(self):
        self.assertEqual(fs.platform_label("paid_google"), "Google Ads")
        self.assertEqual(fs.source_label("google_analytics"), "GA4")


if __name__ == "__main__":
    unittest.main()
