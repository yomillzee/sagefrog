"""Tests for HubSpot marketing-email performance.

Covers the two ends the feature adds: the sync-side parsing of the
GET /marketing/v3/emails?includeStats=true payload (hubspot_sync_service) and
the Email Performance page's rate math + display payload
(email_performance_renderer), including which denominator each rate uses, the
graceful "no emails" case, and that the page's controls actually reach the HTML.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hubspot_reports_service  # noqa: E402
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


class TypeLabelTests(unittest.TestCase):
    def test_known_shapes_are_spelled_out(self):
        self.assertEqual(epr._type_label("BATCH_EMAIL"), "Batch")
        self.assertEqual(epr._type_label("batch"), "Batch")
        self.assertEqual(epr._type_label("AUTOMATED_AB_EMAIL"), "A/B")
        self.assertEqual(epr._type_label("automated"), "Automated")

    def test_unknown_kind_is_tidied_not_dropped(self):
        self.assertEqual(epr._type_label("SOME_NEW_EMAIL"), "Some New")

    def test_missing_kind_is_blank(self):
        self.assertEqual(epr._type_label(None), "")
        self.assertEqual(epr._type_label(""), "")


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

    def test_bounce_rate_is_over_sends_not_deliveries(self):
        # A bounce is precisely a send that never became a delivery, so the
        # denominator has to be sends -- over deliveries it would overstate.
        p = epr._email_payload(self._emails())[0]
        self.assertEqual(p["bounce"], "1.67%")  # 200/12000, not 200/11800

    def test_click_to_open_is_clicks_over_opens(self):
        p = epr._email_payload(self._emails())[0]
        self.assertEqual(p["ctor"], "12.5%")    # 590/4720

    def test_type_reaches_the_payload(self):
        self.assertEqual(epr._email_payload(self._emails())[0]["type"], "Batch")

    def test_raw_counts_are_carried_for_client_side_aggregation(self):
        p = epr._email_payload(self._emails())[0]
        self.assertEqual(p["_sent"], 12000.0)
        self.assertEqual(p["_opens"], 4720.0)
        self.assertEqual(p["_bounces"], 200.0)

    def test_click_to_open_survives_zero_opens(self):
        emails = [{"email_id": "3", "name": "Nobody opened", "delivered": 100,
                   "sent": 100, "opens": 0, "clicks": 0}]
        self.assertEqual(epr._email_payload(emails)[0]["ctor"], "—")

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
        self.assertEqual(p["ctor"], "—")
        # 3 sends and 0 bounces is still a real bounce rate, so it is not a dash
        self.assertEqual(p["bounce"], "0.00%")

    def test_untitled_fallback_for_missing_name(self):
        p = epr._email_payload([{"email_id": "7", "delivered": 0}])[0]
        self.assertEqual(p["name"], "Untitled email")


class RenderPageTests(unittest.TestCase):
    """The page is one big f-string of HTML + JS; these assert the controls a
    user reaches for actually make it into the markup."""

    def _report(self, emails):
        return hubspot_reports_service.EmailPerformanceReport(
            configured=True, available=True, emails=emails)

    def _render(self, emails, **kw):
        return epr.render_email_performance(
            client_slug="demo", label="Demo Co", report=self._report(emails), **kw)

    def _emails(self, n=3):
        return [
            {"email_id": str(i), "name": f"Email {i}", "subject": "Hi",
             "email_type": "batch", "publish_date": date(2026, 6, i + 1),
             "sent": 1000, "delivered": 980, "opens": 400,
             "clicks": 40, "unsubscribed": 2, "bounces": 20}
            for i in range(1, n + 1)
        ]

    def test_page_carries_ranges_export_and_the_new_metrics(self):
        html = self._render(self._emails())
        for needle in ('id="ep-export"', 'class="ep-range"', 'data-days="90"',
                       'id="ep-kpi-ctor"', 'id="ep-kpi-bounce"',
                       'data-key="ctor"', 'data-key="bounce"',
                       'id="ep-empty-open"', 'id="ep-add-shown"',
                       'id="ep-backdrop"'):
            self.assertIn(needle, html, needle)

    def test_save_control_is_admin_only(self):
        self.assertNotIn('id="ep-save"', self._render(self._emails()))
        self.assertIn('id="ep-save"', self._render(self._emails(), session_is_admin=True))

    def test_unconfigured_and_empty_render_a_note_not_a_table(self):
        unconfigured = epr.render_email_performance(
            client_slug="demo", label="Demo Co",
            report=hubspot_reports_service.EmailPerformanceReport(
                configured=False, error="HubSpot is not connected."))
        self.assertIn("HubSpot is not connected.", unconfigured)
        self.assertNotIn('id="ep-tbody"', unconfigured)

        empty = self._render([])
        self.assertIn("No marketing email data has synced yet", empty)
        self.assertNotIn('id="ep-tbody"', empty)


if __name__ == "__main__":
    unittest.main()
