from __future__ import annotations

import datetime as dt
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# agency_benchmarks_service imports agency_trends_service -> marketing_service,
# which imports google.cloud.bigquery at module load. BigQuery is never called
# here (compute_benchmarks is pure), so a stub lets the import tree load without
# the SDK. Kept identical to test_agency_trends_service so neither shadows the
# other when the suite runs in one process.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _FakeQueryJobConfig:
        def __init__(self, dry_run=False):
            self.dry_run = dry_run
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig
    service_account_mod.Credentials = _FakeCredentials
    cloud_mod.bigquery = bigquery_mod
    google_mod.cloud = cloud_mod
    google_mod.oauth2 = oauth2_mod
    oauth2_mod.service_account = service_account_mod
    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.service_account"] = service_account_mod

from dashboard.services import agency_trends_service
from dashboard.services.agency_benchmarks_service import (
    METRICS,
    THIN_SAMPLE_MAX,
    _paid_totals_by_platform,
    _platform_options,
    _sessions_total,
    compute_benchmarks,
    metric_catalog,
    window_bounds,
)

TODAY = dt.date(2025, 6, 18)


def _record(slug, label, platforms, *, sessions=0.0, followers=0.0):
    """Build a collect_client_metrics-shaped record with rolled-up totals."""
    totals = {
        "spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0,
        "sessions": float(sessions), "linkedin_followers": float(followers),
    }
    full = {}
    for key, agg in platforms.items():
        row = {
            "spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0,
            "sessions": 0.0, "linkedin_followers": 0.0,
        }
        row.update(agg)
        full[key] = row
        for field in ("spend", "impressions", "clicks", "conversions"):
            totals[field] += row[field]
    return {"client_slug": slug, "label": label, "platforms": full, "totals": totals}


def _google(spend, impressions, clicks, conversions=0.0):
    return {
        "google": {
            "spend": spend, "impressions": impressions,
            "clicks": clicks, "conversions": conversions,
        }
    }


class WindowBoundsTests(unittest.TestCase):
    def test_month_window_is_month_to_date(self):
        start, end, label = window_bounds(TODAY, "month")
        self.assertEqual((start, end), (dt.date(2025, 6, 1), TODAY))
        self.assertEqual(label, "month to date")

    def test_last_30d_ends_yesterday(self):
        # Platform syncs lag a day; including today would read as a false dip.
        start, end, _label = window_bounds(TODAY, "last_30d")
        self.assertEqual(end, dt.date(2025, 6, 17))
        self.assertEqual((end - start).days + 1, 30)

    def test_every_window_fits_inside_the_shared_fetch_window(self):
        # The cost story depends on this: both benchmark windows must be slices
        # of the span the Health page already fetches and caches, or every
        # refresh triggers fresh per-client BigQuery reads.
        spend_start, spend_end, sess_start, sess_end = (
            agency_trends_service.overview_fetch_bounds(TODAY)
        )
        for window in ("month", "last_30d"):
            start, end, _ = window_bounds(TODAY, window)
            self.assertGreaterEqual(start, spend_start, window)
            self.assertLessEqual(end, spend_end, window)
        # Sessions are only fetched trailing-30 ending yesterday; last_30d must
        # line up with it exactly.
        start, end, _ = window_bounds(TODAY, "last_30d")
        self.assertEqual((start, end), (sess_start, sess_end))


class MetricRegistryTests(unittest.TestCase):
    def test_catalog_is_json_safe(self):
        import json

        json.dumps(metric_catalog())  # must not raise on the compute callables

    def test_every_metric_declares_its_display_contract(self):
        for metric in METRICS:
            for field in ("key", "label", "format", "direction", "hint", "platform_scoped"):
                self.assertIn(field, metric, metric["key"])
            self.assertIn(metric["direction"], ("up", "down", "none"), metric["key"])
            self.assertIn(
                metric["format"], ("percent", "currency", "currency2", "number"), metric["key"]
            )

    def test_metric_keys_are_unique(self):
        keys = [m["key"] for m in METRICS]
        self.assertEqual(len(keys), len(set(keys)))


class RollupTests(unittest.TestCase):
    def test_median_is_the_headline_and_mean_ships_alongside(self):
        # CTRs of 1%, 2%, and 9% — the mean (4%) is nobody's experience, the
        # median (2%) is. Both must be present so a skewed bucket is visible.
        records = [
            _record("a", "A", _google(100, 100_000, 1_000)),
            _record("b", "B", _google(100, 100_000, 2_000)),
            _record("c", "C", _google(100, 100_000, 9_000)),
        ]
        industries = {"a": "industrial_manufacturing", "b": "industrial_manufacturing",
                      "c": "industrial_manufacturing"}
        out = compute_benchmarks(records, industries, platform="google")
        ctr = out["industries"][0]["metrics"]["ctr"]
        self.assertEqual(ctr["n"], 3)
        self.assertAlmostEqual(ctr["median"], 2.0)
        self.assertAlmostEqual(ctr["mean"], 4.0)
        self.assertAlmostEqual(ctr["min"], 1.0)
        self.assertAlmostEqual(ctr["max"], 9.0)

    def test_client_without_impressions_is_excluded_not_counted_as_zero(self):
        records = [
            _record("a", "A", _google(100, 100_000, 2_000)),
            _record("b", "B", _google(0, 0, 0)),  # nothing ran
        ]
        out = compute_benchmarks(
            records, {"a": "technology_software", "b": "technology_software"}, platform="google"
        )
        ctr = out["industries"][0]["metrics"]["ctr"]
        self.assertEqual(ctr["n"], 1)
        self.assertAlmostEqual(ctr["median"], 2.0)  # not dragged to 1.0

    def test_thin_buckets_are_flagged(self):
        records = [_record("a", "A", _google(100, 100_000, 2_000))]
        out = compute_benchmarks(records, {"a": "energy_utilities"}, platform="google")
        stat = out["industries"][0]["metrics"]["ctr"]
        self.assertTrue(stat["thin"])
        self.assertLess(stat["n"], THIN_SAMPLE_MAX)

    def test_platform_filter_scopes_paid_metrics_only(self):
        records = [
            _record(
                "a", "A",
                {
                    "google": {"spend": 100, "impressions": 100_000, "clicks": 1_000, "conversions": 0},
                    "linkedin": {"spend": 900, "impressions": 100_000, "clicks": 100, "conversions": 0},
                },
                sessions=5_000,
            )
        ]
        industries = {"a": "business_services"}
        google = compute_benchmarks(records, industries, platform="google")
        linkedin = compute_benchmarks(records, industries, platform="linkedin")
        every = compute_benchmarks(records, industries, platform="all")

        self.assertAlmostEqual(google["industries"][0]["metrics"]["ctr"]["median"], 1.0)
        self.assertAlmostEqual(linkedin["industries"][0]["metrics"]["ctr"]["median"], 0.1)
        self.assertAlmostEqual(every["industries"][0]["metrics"]["ctr"]["median"], 0.55)
        # Sessions are not platform-scoped: the filter must not touch them.
        for payload in (google, linkedin, every):
            self.assertAlmostEqual(payload["industries"][0]["metrics"]["sessions"]["median"], 5_000)

    def test_client_that_never_ran_on_the_platform_drops_out(self):
        records = [
            _record("a", "A", _google(100, 100_000, 1_000)),
            _record("b", "B", {"linkedin": {"spend": 50, "impressions": 10_000, "clicks": 50, "conversions": 0}}),
        ]
        out = compute_benchmarks(
            records, {"a": "consumer_retail", "b": "consumer_retail"}, platform="google"
        )
        self.assertEqual(out["industries"][0]["metrics"]["ctr"]["n"], 1)

    def test_untagged_clients_count_agency_wide_but_not_in_a_bucket(self):
        records = [
            _record("a", "A", _google(100, 100_000, 1_000)),
            _record("b", "B", _google(100, 100_000, 3_000)),
        ]
        out = compute_benchmarks(records, {"a": "aec"}, platform="google")
        self.assertEqual([b["key"] for b in out["industries"]], ["aec"])
        self.assertEqual(out["industries"][0]["metrics"]["ctr"]["n"], 1)
        self.assertEqual(out["agency"]["metrics"]["ctr"]["n"], 2)
        self.assertEqual(out["coverage"], {"total": 2, "tagged": 1, "untagged": 1})

    def test_unknown_industry_key_reads_as_untagged(self):
        records = [_record("a", "A", _google(100, 100_000, 1_000))]
        out = compute_benchmarks(records, {"a": "retired_bucket"}, platform="google")
        self.assertEqual(out["industries"], [])
        self.assertEqual(out["coverage"]["untagged"], 1)
        self.assertIsNone(out["clients"][0]["industry"])
        self.assertEqual(out["clients"][0]["industry_label"], "Unassigned")

    def test_buckets_follow_taxonomy_order_not_insertion_order(self):
        records = [
            _record("z", "Z", _google(100, 1_000, 10)),
            _record("a", "A", _google(100, 1_000, 10)),
        ]
        # technology_software sits above business_services in the taxonomy.
        out = compute_benchmarks(
            records, {"z": "business_services", "a": "technology_software"}, platform="google"
        )
        self.assertEqual([b["key"] for b in out["industries"]],
                         ["technology_software", "business_services"])

    def test_per_client_rows_carry_values_and_bucket_membership(self):
        records = [
            _record("a", "Acme", _google(200, 100_000, 2_000, conversions=40), sessions=1_000,
                    followers=4_200),
        ]
        out = compute_benchmarks(records, {"a": "healthcare_life_sciences"}, platform="google")
        client = out["clients"][0]
        self.assertEqual(client["industry_label"], "Health & Life Sciences")
        self.assertAlmostEqual(client["metrics"]["ctr"], 2.0)
        self.assertAlmostEqual(client["metrics"]["cpc"], 0.1)
        self.assertAlmostEqual(client["metrics"]["cpa"], 5.0)
        self.assertAlmostEqual(client["metrics"]["cvr"], 2.0)
        self.assertAlmostEqual(client["metrics"]["linkedin_followers"], 4_200)
        self.assertEqual(out["industries"][0]["clients"], ["a"])

    def test_metric_with_no_observations_is_none_not_zero(self):
        records = [_record("a", "A", _google(100, 100_000, 1_000))]
        out = compute_benchmarks(records, {"a": "aec"}, platform="google")
        # Nobody has a follower count, so the cell is absent rather than 0.
        self.assertIsNone(out["industries"][0]["metrics"]["linkedin_followers"])
        self.assertIsNone(out["clients"][0]["metrics"]["linkedin_followers"])

    def test_empty_input_is_a_valid_empty_payload(self):
        out = compute_benchmarks([], {}, platform="all")
        self.assertEqual(out["industries"], [])
        self.assertEqual(out["clients"], [])
        self.assertEqual(out["coverage"], {"total": 0, "tagged": 0, "untagged": 0})
        self.assertIsNone(out["agency"]["metrics"]["ctr"])


class PayloadSlicingTests(unittest.TestCase):
    """The cached reads span a wider window than any one benchmark window, so the
    slicing has to be right or the numbers silently over-count."""

    def test_paid_totals_slice_to_the_requested_window(self):
        summary = {
            "daily": [
                {"date": "2025-06-01", "source": "paid_google", "spend": 10,
                 "impressions": 1_000, "clicks": 10, "conversions": 1},
                {"date": "2025-05-28", "source": "paid_google", "spend": 999,
                 "impressions": 99_999, "clicks": 999, "conversions": 99},
                {"date": "2025-06-02", "source": "paid_linkedin", "spend": 5,
                 "impressions": 500, "clicks": 5, "conversions": 0},
            ]
        }
        out = _paid_totals_by_platform(
            summary, start=dt.date(2025, 6, 1), end=dt.date(2025, 6, 18)
        )
        self.assertEqual(set(out), {"google", "linkedin"})
        self.assertAlmostEqual(out["google"]["spend"], 10)
        self.assertAlmostEqual(out["google"]["impressions"], 1_000)
        self.assertAlmostEqual(out["linkedin"]["clicks"], 5)

    def test_unparseable_dates_are_skipped(self):
        summary = {"daily": [{"date": "", "source": "paid_google", "spend": 5},
                             {"source": "paid_google", "spend": 5}]}
        self.assertEqual(
            _paid_totals_by_platform(summary, start=dt.date(2025, 6, 1), end=TODAY), {}
        )

    def test_sessions_slice_to_the_requested_window(self):
        payload = {"daily": [
            {"date": "2025-06-01", "sessions": 100},
            {"date": "2025-06-02", "sessions": 150},
            {"date": "2025-05-30", "sessions": 999},
        ]}
        total = _sessions_total(payload, start=dt.date(2025, 6, 1), end=dt.date(2025, 6, 18))
        self.assertAlmostEqual(total, 250)

    def test_platform_options_only_offer_channels_with_data(self):
        records = [
            _record("a", "A", _google(1, 1, 1)),
            _record("b", "B", {"linkedin": {"spend": 1, "impressions": 1, "clicks": 1, "conversions": 0}}),
        ]
        options = _platform_options(records)
        self.assertEqual(options[0]["key"], "all")
        self.assertEqual([o["key"] for o in options[1:]], ["google", "linkedin"])
        self.assertEqual(options[1]["label"], "Google Ads")

    def test_platform_options_degrade_to_all_when_nobody_runs_paid(self):
        self.assertEqual(_platform_options([]), [{"key": "all", "label": "All paid media"}])


if __name__ == "__main__":
    unittest.main()
