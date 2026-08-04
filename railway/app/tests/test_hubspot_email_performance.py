"""Tests for HubSpot marketing-email performance.

Covers the two ends the feature adds: the sync-side parsing of the
GET /marketing/v3/emails?includeStats=true payload (hubspot_sync_service) and
the Email Performance page's rate math over deliveries + display payload
(email_performance_renderer), including the graceful "no emails" case.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hubspot_sync_service as sync  # noqa: E402
from dashboard.renderers import email_performance_renderer as epr  # noqa: E402


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
        self.assertEqual(epr._rate_str(40, 100), "40.0%")
        self.assertEqual(epr._rate_str(1, 100, decimals=2), "1.00%")

    def test_zero_or_missing_denominator_is_dash(self):
        self.assertEqual(epr._rate_str(5, 0), "—")
        self.assertEqual(epr._rate_str(5, None), "—")


class EmailPayloadTests(unittest.TestCase):
    def _emails(self):
        return [
            {"email_id": "1", "name": "August Newsletter", "subject": "Your update",
             "email_type": "batch", "publish_date": None,
             "sent": 12000, "delivered": 11800, "opens": 4720,
             "clicks": 590, "unsubscribed": 24, "bounces": 200},
        ]

    def test_empty_emails_yields_empty_payload(self):
        self.assertEqual(epr._email_payload([]), [])

    def test_payload_computes_rates_over_deliveries(self):
        p = epr._email_payload(self._emails())[0]
        self.assertEqual(p["id"], "1")
        self.assertEqual(p["name"], "August Newsletter")
        self.assertEqual(p["deliveries"], "11,800")
        self.assertEqual(p["open"], "40.0%")    # 4720/11800
        self.assertEqual(p["click"], "5.0%")    # 590/11800
        self.assertEqual(p["unsub"], "0.20%")   # 24/11800 (2dp)

    def test_payload_survives_zero_deliveries(self):
        emails = [{"email_id": "9", "name": "No sends", "subject": "",
                   "email_type": "ab", "publish_date": None,
                   "sent": 3, "delivered": 0, "opens": 0,
                   "clicks": 0, "unsubscribed": 0, "bounces": 0}]
        p = epr._email_payload(emails)[0]
        # rates fall back to em dash, no ZeroDivisionError
        self.assertEqual(p["open"], "—")
        self.assertEqual(p["click"], "—")
        self.assertEqual(p["unsub"], "—")

    def test_untitled_fallback_for_missing_name(self):
        p = epr._email_payload([{"email_id": "7", "delivered": 0}])[0]
        self.assertEqual(p["name"], "Untitled email")


if __name__ == "__main__":
    unittest.main()
