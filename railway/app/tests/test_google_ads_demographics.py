"""Google Ads age/gender segments — the fetch and the "consider excluding" call.

The fetch is mostly mechanical, but two of its details are load-bearing and
easy to regress:

  * ``negative`` is absent from the response when it is false (MessageToDict
    drops false booleans), so a targeted segment must not read as excluded; and
  * one demographic view failing has to leave the other's rows intact, because
    an account with no Display campaigns legitimately returns nothing for one.

The recommendation logic gets the most attention here. It is the part that makes
a claim to a client — "you are wasting money on this segment" — so the tests
pin down when it stays quiet: no benchmark, small spend, the Unknown bucket, and
segments somebody already excluded.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Keep this runnable without the external Google SDKs installed (same approach
# as test_google_ads_accounts.py / test_marketing_service.py).
if "google.ads.googleads.client" not in sys.modules:
    google = sys.modules.get("google") or types.ModuleType("google")
    google.__path__ = []  # type: ignore[attr-defined]
    ads = types.ModuleType("google.ads")
    ads.__path__ = []  # type: ignore[attr-defined]
    googleads = types.ModuleType("google.ads.googleads")
    googleads.__path__ = []  # type: ignore[attr-defined]
    client_module = types.ModuleType("google.ads.googleads.client")
    client_module.GoogleAdsClient = object  # type: ignore[attr-defined]
    google_auth = types.ModuleType("google.auth")
    google_auth.__path__ = []  # type: ignore[attr-defined]
    transport = types.ModuleType("google.auth.transport")
    transport.__path__ = []  # type: ignore[attr-defined]
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = object  # type: ignore[attr-defined]
    oauth2 = sys.modules.get("google.oauth2") or types.ModuleType("google.oauth2")
    oauth2.__path__ = []  # type: ignore[attr-defined]
    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = object  # type: ignore[attr-defined]
    protobuf = types.ModuleType("google.protobuf")
    protobuf.__path__ = []  # type: ignore[attr-defined]
    json_format = types.ModuleType("google.protobuf.json_format")
    json_format.MessageToDict = lambda value, **kwargs: value  # type: ignore[attr-defined]
    sys.modules.update(
        {
            "google": google,
            "google.ads": ads,
            "google.ads.googleads": googleads,
            "google.ads.googleads.client": client_module,
            "google.auth": google_auth,
            "google.auth.transport": transport,
            "google.auth.transport.requests": requests_module,
            "google.oauth2": oauth2,
            "google.oauth2.credentials": credentials_module,
            "google.protobuf": protobuf,
            "google.protobuf.json_format": json_format,
        }
    )

# marketing_service needs google.cloud.bigquery on top of the Ads SDK. Both
# stubs hang off the same 'google' namespace module, so this block reuses
# whatever the one above (or another test module) already put there.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = sys.modules.get("google") or types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = sys.modules.get("google.oauth2") or types.ModuleType("google.oauth2")
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

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter  # type: ignore[attr-defined]
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig  # type: ignore[attr-defined]
    service_account_mod.Credentials = _FakeCredentials  # type: ignore[attr-defined]
    cloud_mod.bigquery = bigquery_mod  # type: ignore[attr-defined]
    google_mod.cloud = cloud_mod  # type: ignore[attr-defined]
    google_mod.oauth2 = oauth2_mod  # type: ignore[attr-defined]
    oauth2_mod.service_account = service_account_mod  # type: ignore[attr-defined]
    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.service_account"] = service_account_mod

import google_ads_service  # noqa: E402
import marketing_service  # noqa: E402


def _row(
    *,
    dimension: str,
    value: str,
    ad_group: str = "77",
    day: str = "2026-08-01",
    cost_micros: int = 10_000_000,
    conversions: float = 0.0,
    negative: bool | None = None,
) -> dict:
    criterion: dict = {"criterion_id": "9001", "status": "ENABLED"}
    criterion[dimension] = {"type": value}
    if negative is not None:
        # MessageToDict omits false booleans entirely — only a true 'negative'
        # actually appears on the wire.
        criterion["negative"] = negative
    return {
        "ad_group_criterion": criterion,
        "ad_group": {"id": ad_group, "name": f"Ad group {ad_group}"},
        "campaign": {
            "id": "55",
            "name": "Brand Search",
            "advertising_channel_type": "SEARCH",
        },
        "segments": {"date": day},
        "metrics": {
            "cost_micros": cost_micros,
            "clicks": 4,
            "impressions": 100,
            "conversions": conversions,
            "conversions_value": conversions * 10,
        },
    }


class DemographicFetchTests(unittest.TestCase):
    def test_reads_both_views_and_labels_segments(self) -> None:
        by_resource = {
            "age_range_view": [_row(dimension="age_range", value="AGE_RANGE_18_24")],
            "gender_view": [_row(dimension="gender", value="MALE")],
        }

        def fake_search(customer_id, query, client=None):
            for resource, rows in by_resource.items():
                if f"FROM {resource}" in query:
                    return rows
            raise AssertionError(f"unexpected query: {query}")

        with patch.object(google_ads_service, "search", side_effect=fake_search):
            rows = google_ads_service.fetch_demographic_daily_metrics(
                "123-456-7890", start=date(2026, 8, 1), end=date(2026, 8, 1)
            )

        got = {(r["dimension"], r["segment_value"]): r for r in rows}
        self.assertEqual(set(got), {("age_range", "AGE_RANGE_18_24"), ("gender", "MALE")})
        self.assertEqual(got[("age_range", "AGE_RANGE_18_24")]["segment_label"], "18–24")
        self.assertEqual(got[("gender", "MALE")]["segment_label"], "Male")
        self.assertEqual(got[("gender", "MALE")]["campaign_name"], "Brand Search")
        self.assertEqual(got[("gender", "MALE")]["channel_type"], "SEARCH")
        self.assertEqual(got[("gender", "MALE")]["spend"], 10.0)

    def test_absent_negative_flag_means_targeted_not_excluded(self) -> None:
        rows_in = {
            "age_range_view": [
                _row(dimension="age_range", value="AGE_RANGE_18_24"),
                _row(dimension="age_range", value="AGE_RANGE_25_34", ad_group="78", negative=True),
            ],
            "gender_view": [],
        }

        def fake_search(customer_id, query, client=None):
            return next(r for k, r in rows_in.items() if f"FROM {k}" in query)

        with patch.object(google_ads_service, "search", side_effect=fake_search):
            rows = google_ads_service.fetch_demographic_daily_metrics(
                "1234567890", start=date(2026, 8, 1), end=date(2026, 8, 1)
            )

        by_value = {r["segment_value"]: r for r in rows}
        self.assertIs(by_value["AGE_RANGE_18_24"]["is_excluded"], False)
        self.assertIs(by_value["AGE_RANGE_25_34"]["is_excluded"], True)

    def test_one_failing_view_does_not_lose_the_other(self) -> None:
        def fake_search(customer_id, query, client=None):
            if "FROM gender_view" in query:
                raise RuntimeError("REQUESTED_METRICS_FOR_MANAGER")
            return [_row(dimension="age_range", value="AGE_RANGE_35_44")]

        with patch.object(google_ads_service, "search", side_effect=fake_search):
            rows = google_ads_service.fetch_demographic_daily_metrics(
                "1234567890", start=date(2026, 8, 1), end=date(2026, 8, 1)
            )

        self.assertEqual([r["segment_value"] for r in rows], ["AGE_RANGE_35_44"])

    def test_same_segment_across_days_stays_separate_and_sums_within_a_day(self) -> None:
        def fake_search(customer_id, query, client=None):
            if "FROM gender_view" in query:
                return []
            return [
                # Two rows for the same (ad group, segment, day) — Google splits
                # by fields we don't select; they must add up, not overwrite.
                _row(dimension="age_range", value="AGE_RANGE_45_54", cost_micros=6_000_000),
                _row(dimension="age_range", value="AGE_RANGE_45_54", cost_micros=4_000_000),
                _row(
                    dimension="age_range", value="AGE_RANGE_45_54",
                    day="2026-08-02", cost_micros=5_000_000,
                ),
            ]

        with patch.object(google_ads_service, "search", side_effect=fake_search):
            rows = google_ads_service.fetch_demographic_daily_metrics(
                "1234567890", start=date(2026, 8, 1), end=date(2026, 8, 2)
            )

        by_day = {r["metric_date"]: r["spend"] for r in rows}
        self.assertEqual(by_day, {"2026-08-01": 10.0, "2026-08-02": 5.0})

    def test_labels_fall_back_to_readable_text_for_unknown_enums(self) -> None:
        self.assertEqual(google_ads_service.demographic_label("age_range", "AGE_RANGE_65_UP"), "65+")
        self.assertEqual(
            google_ads_service.demographic_label("age_range", "AGE_RANGE_UNDETERMINED"), "Unknown"
        )
        self.assertEqual(google_ads_service.demographic_label("gender", "UNDETERMINED"), "Unknown")
        # A bucket Google adds later still renders as words, not a constant.
        self.assertEqual(
            google_ads_service.demographic_label("age_range", "AGE_RANGE_75_UP"), "75 Up"
        )


def _seg(value: str, *, spend: float, conversions: float, label: str = "", **extra) -> dict:
    return {
        "dimension": extra.pop("dimension", "gender"),
        "segment_value": value,
        "segment_label": label or value.title(),
        "spend": spend,
        "conversions": conversions,
        "clicks": extra.pop("clicks", 100),
        "impressions": extra.pop("impressions", 5000),
        "conversion_value": extra.pop("conversion_value", 0.0),
        "ad_groups": extra.pop("ad_groups", 3),
        "excluded_ad_groups": extra.pop("excluded_ad_groups", 0),
        "excluded_everywhere": extra.pop("excluded_everywhere", False),
        **extra,
    }


class DemographicRecommendationTests(unittest.TestCase):
    def test_flags_a_segment_that_spends_past_the_benchmark_with_no_conversions(self) -> None:
        rows = [
            _seg("FEMALE", spend=400.0, conversions=20.0, label="Female"),
            _seg("MALE", spend=300.0, conversions=0.0, label="Male"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]

        # Benchmark comes off the classified segments: 700 spend / 20 conv = 35.
        self.assertEqual(out["benchmark_cpa"], 35.0)
        male = next(s for s in out["segments"] if s["segment_value"] == "MALE")
        self.assertEqual(male["recommendation"]["kind"], "no_conversions")
        self.assertEqual(male["recommendation"]["headline"], "Consider excluding Male")
        female = next(s for s in out["segments"] if s["segment_value"] == "FEMALE")
        self.assertIsNone(female["recommendation"])

    def test_stays_quiet_when_the_segment_has_not_had_a_fair_shot(self) -> None:
        # 60 spend clears the absolute floor but not 2x the 100.0 benchmark.
        rows = [
            _seg("FEMALE", spend=1000.0, conversions=10.0, label="Female"),
            _seg("MALE", spend=60.0, conversions=0.0, label="Male"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        male = next(s for s in out["segments"] if s["segment_value"] == "MALE")
        self.assertIsNone(male["recommendation"])

    def test_stays_quiet_below_the_absolute_spend_floor(self) -> None:
        rows = [
            _seg("FEMALE", spend=100.0, conversions=50.0, label="Female"),
            _seg("MALE", spend=20.0, conversions=0.0, label="Male"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        male = next(s for s in out["segments"] if s["segment_value"] == "MALE")
        self.assertIsNone(male["recommendation"])

    def test_never_recommends_excluding_the_unknown_bucket(self) -> None:
        rows = [
            _seg("FEMALE", spend=400.0, conversions=20.0, label="Female"),
            _seg("UNDETERMINED", spend=5000.0, conversions=0.0, label="Unknown"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        unknown = next(s for s in out["segments"] if s["segment_value"] == "UNDETERMINED")
        self.assertIsNone(unknown["recommendation"])
        # ...and it is still counted in the totals, so shares stay honest.
        self.assertEqual(out["spend"], 5400.0)
        self.assertEqual(out["undetermined_spend_share"], 92.6)

    def test_benchmark_ignores_the_unknown_bucket(self) -> None:
        rows = [
            _seg("FEMALE", spend=400.0, conversions=20.0, label="Female"),
            _seg("UNDETERMINED", spend=5000.0, conversions=0.0, label="Unknown"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        # 400/20 from the classified segment alone — not 5400/20.
        self.assertEqual(out["benchmark_cpa"], 20.0)

    def test_never_repeats_an_exclusion_already_in_place(self) -> None:
        rows = [
            _seg("FEMALE", spend=400.0, conversions=20.0, label="Female"),
            _seg("MALE", spend=300.0, conversions=0.0, label="Male", excluded_everywhere=True),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        male = next(s for s in out["segments"] if s["segment_value"] == "MALE")
        self.assertIsNone(male["recommendation"])

    def test_says_nothing_at_all_without_a_benchmark(self) -> None:
        # No conversions anywhere: "this segment doesn't convert" is not a
        # finding about the segment, it is a finding about the account.
        rows = [
            _seg("FEMALE", spend=900.0, conversions=0.0, label="Female"),
            _seg("MALE", spend=800.0, conversions=0.0, label="Male"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        self.assertIsNone(out["benchmark_cpa"])
        self.assertEqual(out["recommendation_count"], 0)
        self.assertTrue(all(s["recommendation"] is None for s in out["segments"]))

    def test_flags_an_expensive_converter_more_softly(self) -> None:
        rows = [
            _seg("FEMALE", spend=100.0, conversions=10.0, label="Female"),
            _seg("MALE", spend=300.0, conversions=5.0, label="Male"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["gender"]
        # Benchmark 400/15 = 26.67; male CPA 60 is past 1.5x.
        male = next(s for s in out["segments"] if s["segment_value"] == "MALE")
        self.assertEqual(male["recommendation"]["kind"], "high_cpa")
        self.assertEqual(male["recommendation"]["severity"], "medium")
        self.assertEqual(male["cpa"], 60.0)

    def test_derives_shares_and_orders_by_spend(self) -> None:
        rows = [
            _seg("AGE_RANGE_25_34", spend=100.0, conversions=5.0, dimension="age_range"),
            _seg("AGE_RANGE_18_24", spend=300.0, conversions=5.0, dimension="age_range"),
        ]
        out = marketing_service.assess_demographic_segments(rows)["age_range"]
        self.assertEqual(
            [s["segment_value"] for s in out["segments"]],
            ["AGE_RANGE_18_24", "AGE_RANGE_25_34"],
        )
        top = out["segments"][0]
        self.assertEqual(top["spend_share"], 75.0)
        self.assertEqual(top["conversion_share"], 50.0)

    def test_splits_dimensions_so_age_never_benchmarks_against_gender(self) -> None:
        rows = [
            _seg("MALE", spend=100.0, conversions=10.0, label="Male"),
            _seg("AGE_RANGE_18_24", spend=100.0, conversions=1.0, dimension="age_range"),
        ]
        out = marketing_service.assess_demographic_segments(rows)
        self.assertEqual(set(out), {"gender", "age_range"})
        self.assertEqual(out["gender"]["benchmark_cpa"], 10.0)
        self.assertEqual(out["age_range"]["benchmark_cpa"], 100.0)


if __name__ == "__main__":
    unittest.main()
