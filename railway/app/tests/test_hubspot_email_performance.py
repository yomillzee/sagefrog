"""Tests for HubSpot marketing-email performance.

Covers the two ends the feature adds: the sync-side parsing of the
/marketing/v3/emails/statistics/list payload (hubspot_sync_service) and the
read/render-side email-performance card (lead_tracking_renderer), including the
rate math over deliveries and the graceful "no emails" case.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hubspot_sync_service as sync  # noqa: E402
from dashboard.renderers import lead_tracking_renderer as ltr  # noqa: E402


class EpochTimestampTests(unittest.TestCase):
    def test_epoch_ms_variants(self):
        self.assertEqual(sync._epoch_ms_to_iso(1754006400000), "2025-08-01T00:00:00Z")
        # numeric string coerces the same way
        self.assertEqual(sync._epoch_ms_to_iso("1754006400000"), "2025-08-01T00:00:00Z")

    def test_iso_string_passes_through(self):
        self.assertEqual(sync._epoch_ms_to_iso("2026-08-01T00:00:00Z"), "2026-08-01T00:00:00Z")

    def test_missing_is_none(self):
        self.assertIsNone(sync._epoch_ms_to_iso(None))
        self.assertIsNone(sync._epoch_ms_to_iso(""))


class ParseEmailRowTests(unittest.TestCase):
    def test_maps_counters_and_metadata(self):
        row = sync._parse_email_row({
            "id": 42, "name": "Aug Newsletter", "subject": "Hi",
            "type": "BATCH_EMAIL", "state": "PUBLISHED",
            "publishDate": 1754006400000,
            "stats": {"counters": {
                "sent": 100, "delivered": 98, "open": 40,
                "click": 5, "unsubscribed": 1, "bounce": 2,
            }},
        }, "2026-08-04T00:00:00Z")
        self.assertEqual(row["email_id"], "42")
        self.assertEqual(row["name"], "Aug Newsletter")
        self.assertEqual(row["email_type"], "BATCH_EMAIL")
        self.assertEqual(row["state"], "PUBLISHED")
        self.assertEqual(row["publish_date"], "2025-08-01T00:00:00Z")
        self.assertEqual(row["sent"], 100)
        self.assertEqual(row["delivered"], 98)
        self.assertEqual(row["opens"], 40)
        self.assertEqual(row["clicks"], 5)
        self.assertEqual(row["unsubscribed"], 1)
        self.assertEqual(row["bounces"], 2)

    def test_includestats_list_shape(self):
        # GET /marketing/v3/emails?includeStats=true returns the email kind under
        # `subcategory` and an ISO8601 publishDate, with per-email stats nested
        # under `stats.counters`.
        row = sync._parse_email_row({
            "id": 55, "name": "Spring Promo", "subject": "Save now",
            "subcategory": "batch", "state": "PUBLISHED",
            "publishDate": "2026-03-01T10:00:00Z",
            "stats": {"counters": {
                "sent": 200, "delivered": 195, "open": 80,
                "click": 12, "unsubscribed": 2, "bounce": 5,
            }},
        }, "2026-08-04T00:00:00Z")
        self.assertEqual(row["email_type"], "batch")   # subcategory preferred
        self.assertEqual(row["publish_date"], "2026-03-01T10:00:00Z")
        self.assertEqual(row["delivered"], 195)
        self.assertEqual(row["opens"], 80)

    def test_missing_stats_does_not_crash(self):
        row = sync._parse_email_row({"id": 7}, "x")
        self.assertEqual(row["email_id"], "7")
        # counters absent -> None, never a KeyError
        for k in ("sent", "delivered", "opens", "clicks", "unsubscribed", "bounces"):
            self.assertIsNone(row[k])


class RateStrTests(unittest.TestCase):
    def test_rate_over_denominator(self):
        self.assertEqual(ltr._rate_str(40, 100), "40.0%")
        self.assertEqual(ltr._rate_str(1, 100, decimals=2), "1.00%")

    def test_zero_or_missing_denominator_is_dash(self):
        self.assertEqual(ltr._rate_str(5, 0), "—")
        self.assertEqual(ltr._rate_str(5, None), "—")


class EmailCardTests(unittest.TestCase):
    def _emails(self):
        return [
            {"email_id": "1", "name": "August Newsletter", "subject": "Your update",
             "email_type": "BATCH_EMAIL", "publish_date": None,
             "sent": 12000, "delivered": 11800, "opens": 4720,
             "clicks": 590, "unsubscribed": 24, "bounces": 200},
        ]

    def test_empty_emails_renders_nothing(self):
        self.assertEqual(ltr._email_performance_card([]), "")

    def test_card_shows_rates_over_deliveries(self):
        html = ltr._email_performance_card(self._emails())
        self.assertIn("Email performance", html)
        self.assertIn("August Newsletter", html)
        self.assertIn("11,800", html)      # deliveries
        self.assertIn("40.0%", html)       # 4720/11800 open rate
        self.assertIn("5.0%", html)        # 590/11800 click rate
        self.assertIn("0.20%", html)       # 24/11800 unsub rate (2dp)
        self.assertIn("data-lt-emails", html)  # JSON payload for the picker

    def test_card_survives_zero_deliveries(self):
        emails = [{"email_id": "9", "name": "No sends", "subject": "",
                   "email_type": "AB_EMAIL", "publish_date": None,
                   "sent": 3, "delivered": 0, "opens": 0,
                   "clicks": 0, "unsubscribed": 0, "bounces": 0}]
        html = ltr._email_performance_card(emails)
        self.assertIn("Email performance", html)
        self.assertIn("—", html)  # rates fall back to em dash, no ZeroDivisionError


if __name__ == "__main__":
    unittest.main()
