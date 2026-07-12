from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.services.agency_trends_service import (
    _window_bounds,
    compute_agency_trends,
)

TODAY = dt.date(2026, 7, 12)


def _daily(account, source, prior_daily, last_daily):
    """14 rows spanning today-14..today-1: two full 7-day windows.

    d in 1..7  -> last window (today-1 .. today-7)
    d in 8..14 -> prior window (today-8 .. today-14)
    """
    rows = []
    for d in range(1, 15):
        day = TODAY - dt.timedelta(days=d)
        rows.append((source, account, day, float(last_daily if d <= 7 else prior_daily)))
    return rows


class WindowBoundsTests(unittest.TestCase):
    def test_two_seven_day_windows_ending_yesterday(self):
        last_lo, last_hi, prior_lo, prior_hi = _window_bounds(TODAY)
        self.assertEqual(last_hi, dt.date(2026, 7, 11))   # yesterday
        self.assertEqual(last_lo, dt.date(2026, 7, 5))    # 7 days inclusive
        self.assertEqual(prior_hi, dt.date(2026, 7, 4))   # day before last window
        self.assertEqual(prior_lo, dt.date(2026, 6, 28))


class ComputeAgencyTrendsTests(unittest.TestCase):
    def _run(self):
        metrics = (
            _daily("a-penn", "google", 900, 1150)     # up
            + _daily("a-acme", "google", 1200, 700)   # down hard
            + _daily("a-ghost", "meta", 100, 80)      # unmapped account
        )
        mapping = [("a-penn", "penn"), ("a-acme", "acme")]
        labels = {"penn": "Penn Community Bank", "acme": "Acme Manufacturing"}
        return compute_agency_trends(metrics, mapping, labels, today=TODAY)

    def test_sorted_steepest_decline_first(self):
        res = self._run()
        labels = [c["label"] for c in res["clients"]]
        self.assertEqual(labels[0], "Acme Manufacturing")     # most negative
        self.assertEqual(labels[-1], "Penn Community Bank")   # most positive

    def test_pct_change_math(self):
        res = self._run()
        by = {c["label"]: c for c in res["clients"]}
        # Acme: last=4900, prior=8400 -> (4900-8400)/8400 = -41.7%
        self.assertEqual(by["Acme Manufacturing"]["last_7"], 4900.0)
        self.assertEqual(by["Acme Manufacturing"]["prior_7"], 8400.0)
        self.assertAlmostEqual(by["Acme Manufacturing"]["pct_change"], -41.7, places=1)

    def test_unmapped_account_bucketed_as_unattributed(self):
        res = self._run()
        ghost = [c for c in res["clients"] if c["unattributed"]]
        self.assertEqual(len(ghost), 1)
        self.assertIsNone(ghost[0]["client_slug"])
        self.assertEqual(ghost[0]["label"], "Unattributed spend")

    def test_channel_mix_sums_to_100(self):
        res = self._run()
        self.assertTrue(res["channels"])
        self.assertAlmostEqual(sum(c["pct_of_total"] for c in res["channels"]), 100.0, places=0)
        # google should dominate over meta here
        self.assertEqual(res["channels"][0]["source"], "google")

    def test_totals_and_declining_count(self):
        res = self._run()
        t = res["totals"]
        self.assertEqual(t["client_count"], 3)
        self.assertEqual(t["clients_declining"], 2)  # acme + ghost declined
        self.assertAlmostEqual(t["last_7"], 4900 + 8050 + 560, places=0)

    def test_no_prior_baseline_sorts_last_with_null_pct(self):
        # A client that only has spend in the last window (prior_7 == 0) has no
        # baseline, so pct_change is None and it sorts after real deltas.
        metrics = _daily("a-new", "google", 0, 500) + _daily("a-acme", "google", 1000, 600)
        res = compute_agency_trends(
            metrics, [("a-new", "new"), ("a-acme", "acme")],
            {"new": "New Client", "acme": "Acme"}, today=TODAY,
        )
        self.assertIsNone(res["clients"][-1]["pct_change"])
        self.assertEqual(res["clients"][-1]["label"], "New Client")

    def test_empty_inputs(self):
        res = compute_agency_trends([], [], {}, today=TODAY)
        self.assertEqual(res["clients"], [])
        self.assertEqual(res["channels"], [])
        self.assertEqual(res["totals"]["client_count"], 0)
        self.assertIsNone(res["totals"]["pct_change"])

    def test_zero_spend_client_excluded(self):
        # An account mapped to a client but with no spend in either window is
        # dropped rather than shown as a 0/0 row.
        metrics = _daily("a-live", "google", 100, 120)
        metrics += [("google", "a-idle", TODAY - dt.timedelta(days=3), 0.0)]
        res = compute_agency_trends(
            metrics, [("a-live", "live"), ("a-idle", "idle")],
            {"live": "Live", "idle": "Idle"}, today=TODAY,
        )
        self.assertEqual([c["label"] for c in res["clients"]], ["Live"])


if __name__ == "__main__":
    unittest.main()
