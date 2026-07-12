from __future__ import annotations

import datetime as dt
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# agency_trends_service imports marketing_service, which imports
# google.cloud.bigquery at module load. BigQuery is never called in these tests
# (compute_agency_trends is pure), so a stub lets the import tree load without
# the SDK. Kept identical to test_marketing_service so neither shadows the other.
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

from dashboard.services.agency_trends_service import (
    _channel_label,
    _window_bounds,
    compute_agency_trends,
)

TODAY = dt.date(2026, 7, 12)


def _daily(slug, source, prior_daily, last_daily):
    """14 rows spanning today-14..today-1: two full 7-day windows.

    d in 1..7  -> last window (today-1 .. today-7)
    d in 8..14 -> prior window (today-8 .. today-14)
    """
    rows = []
    for d in range(1, 15):
        day = TODAY - dt.timedelta(days=d)
        rows.append((slug, source, day, float(last_daily if d <= 7 else prior_daily)))
    return rows


class WindowBoundsTests(unittest.TestCase):
    def test_two_seven_day_windows_ending_yesterday(self):
        last_lo, last_hi, prior_lo, prior_hi = _window_bounds(TODAY)
        self.assertEqual(last_hi, dt.date(2026, 7, 11))   # yesterday
        self.assertEqual(last_lo, dt.date(2026, 7, 5))    # 7 days inclusive
        self.assertEqual(prior_hi, dt.date(2026, 7, 4))
        self.assertEqual(prior_lo, dt.date(2026, 6, 28))


class ChannelLabelTests(unittest.TestCase):
    def test_strips_paid_prefix(self):
        self.assertEqual(_channel_label("paid_google"), "google")
        self.assertEqual(_channel_label("paid_linkedin"), "linkedin")
        self.assertEqual(_channel_label("organic"), "organic")


class ComputeAgencyTrendsTests(unittest.TestCase):
    def _run(self):
        rows = (
            _daily("penn", "paid_google", 900, 1150)     # up
            + _daily("acme", "paid_google", 1200, 700)   # down hard
            + _daily("acme", "paid_meta", 200, 150)      # acme also runs meta
        )
        labels = {"penn": "Penn Community Bank", "acme": "Acme Manufacturing"}
        return compute_agency_trends(rows, labels, today=TODAY)

    def test_sorted_steepest_decline_first(self):
        res = self._run()
        labels = [c["label"] for c in res["clients"]]
        self.assertEqual(labels[0], "Acme Manufacturing")     # most negative
        self.assertEqual(labels[-1], "Penn Community Bank")   # most positive

    def test_pct_change_math(self):
        res = self._run()
        by = {c["label"]: c for c in res["clients"]}
        # Acme: last=(700+150)*7=5950, prior=(1200+200)*7=9800 -> -39.3%
        self.assertEqual(by["Acme Manufacturing"]["last_7"], 5950.0)
        self.assertEqual(by["Acme Manufacturing"]["prior_7"], 9800.0)
        self.assertAlmostEqual(by["Acme Manufacturing"]["pct_change"], -39.3, places=1)

    def test_channel_mix_labels_and_sums(self):
        res = self._run()
        srcs = [c["source"] for c in res["channels"]]
        self.assertIn("google", srcs)          # 'paid_' prefix stripped
        self.assertIn("meta", srcs)
        self.assertAlmostEqual(sum(c["pct_of_total"] for c in res["channels"]), 100.0, places=0)
        self.assertEqual(res["channels"][0]["source"], "google")  # google dominates

    def test_totals_and_declining_count(self):
        res = self._run()
        t = res["totals"]
        self.assertEqual(t["client_count"], 2)
        self.assertEqual(t["clients_declining"], 1)  # only acme declined
        self.assertAlmostEqual(t["last_7"], 1150 * 7 + 5950, places=0)

    def test_no_prior_baseline_sorts_last_with_null_pct(self):
        rows = _daily("new", "paid_google", 0, 500) + _daily("acme", "paid_google", 1000, 600)
        res = compute_agency_trends(
            rows, {"new": "New Client", "acme": "Acme"}, today=TODAY,
        )
        self.assertIsNone(res["clients"][-1]["pct_change"])
        self.assertEqual(res["clients"][-1]["label"], "New Client")

    def test_empty_inputs(self):
        res = compute_agency_trends([], {}, today=TODAY)
        self.assertEqual(res["clients"], [])
        self.assertEqual(res["channels"], [])
        self.assertEqual(res["totals"]["client_count"], 0)
        self.assertIsNone(res["totals"]["pct_change"])

    def test_zero_spend_client_excluded(self):
        rows = _daily("live", "paid_google", 100, 120)
        rows += [("idle", "paid_google", TODAY - dt.timedelta(days=3), 0.0)]
        res = compute_agency_trends(
            rows, {"live": "Live", "idle": "Idle"}, today=TODAY,
        )
        self.assertEqual([c["label"] for c in res["clients"]], ["Live"])


if __name__ == "__main__":
    unittest.main()
