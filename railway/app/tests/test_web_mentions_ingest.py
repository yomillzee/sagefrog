"""Google Alerts feed parsing, URL handling and the ingest loop's failure isolation.

No Postgres and no network: the feed fetch is stubbed, so what these cover is
everything the ingest decides for itself — how a malformed feed, a missing date,
a missing publisher, a Google redirect wrapper and a dead feed are each handled,
and that one client's broken feed cannot stop another client's run.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import web_mentions_service as service  # noqa: E402
import web_mentions_store as store  # noqa: E402

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Google Alert - EOS Worldwide</title>
  <entry>
    <id>tag:google.com,2013:googlealerts/feed:111</id>
    <title type="html">&lt;b&gt;EOS Worldwide&lt;/b&gt; names a new COO</title>
    <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://bizjournal.example/a%3Futm_source%3Dgoogle%26id%3D7&amp;ct=ga&amp;usg=abc"/>
    <published>2026-08-26T14:03:00Z</published>
    <content type="html">The &lt;b&gt;EOS&lt;/b&gt; leadership team expands</content>
    <author><name>Business Journal</name></author>
  </entry>
  <entry>
    <id>tag:google.com,2013:googlealerts/feed:222</id>
    <title type="html">No date and no publisher</title>
    <link href="https://example.org/plain"/>
    <content type="html">snippet</content>
  </entry>
</feed>"""

RSS = """<rss version="2.0"><channel><item>
  <title>An RSS &amp; item</title>
  <link>https://news.example/x?utm_medium=rss</link>
  <pubDate>Wed, 20 Aug 2026 09:00:00 GMT</pubDate>
  <description>&lt;b&gt;bold&lt;/b&gt; snippet</description>
</item></channel></rss>"""


def _alert(alert_id: int = 1, *, slug: str = "acme", category: str = "brand") -> store.Alert:
    return store.Alert(
        id=alert_id, client_slug=slug, name=f"Alert {alert_id}", subject=f"Subject {alert_id}",
        feed_url="https://www.google.com/alerts/feeds/1234567890/9876543210",
        category=category, active=True,
    )


class FeedParsingTests(unittest.TestCase):
    def test_atom_entry_is_normalized(self):
        first, second = service.parse_feed(ATOM)
        # Google's bold markup around the matched term is stripped, not escaped.
        self.assertEqual(first["title"], "EOS Worldwide names a new COO")
        # The redirect wrapper is unwrapped and campaign params dropped, so the
        # same article hashes identically on every refresh.
        self.assertEqual(first["url"], "https://bizjournal.example/a?id=7")
        self.assertIn("google.com/url", first["google_url"])
        self.assertEqual(first["source"], "Business Journal")
        self.assertEqual(first["published_at"], datetime(2026, 8, 26, 14, 3, tzinfo=UTC))
        # A publisher-less entry falls back to the destination's domain, and a
        # date-less one reports no date rather than inventing one.
        self.assertEqual(second["source"], "example.org")
        self.assertIsNone(second["published_at"])

    def test_rss_2_0_is_also_understood(self):
        (entry,) = service.parse_feed(RSS)
        self.assertEqual(entry["title"], "An RSS & item")
        self.assertEqual(entry["url"], "https://news.example/x")
        self.assertEqual(entry["published_at"], datetime(2026, 8, 20, 9, 0, tzinfo=UTC))

    def test_malformed_xml_raises_a_readable_error(self):
        with self.assertRaises(ValueError) as ctx:
            service.parse_feed("<feed><entry>")
        self.assertIn("not valid XML", str(ctx.exception))

    def test_empty_body_raises(self):
        with self.assertRaises(ValueError):
            service.parse_feed(b"")

    def test_an_entry_with_nothing_to_report_is_skipped_not_fatal(self):
        feed = ATOM.replace("</feed>", "<entry><id>empty</id></entry></feed>")
        self.assertEqual(len(service.parse_feed(feed)), 2)


class FeedUrlTests(unittest.TestCase):
    def test_accepts_a_google_alerts_feed_and_normalizes_it(self):
        ok, value = service.validate_feed_url("google.com/alerts/feeds/12345/67890")
        self.assertTrue(ok)
        self.assertEqual(value, "https://www.google.com/alerts/feeds/12345/67890")

    def test_rejects_anything_that_is_not_an_alerts_feed(self):
        for bad in (
            "",
            "not a url",
            "https://www.google.com/search?q=x",   # Google, but not a feed
            "http://127.0.0.1/alerts/feeds/1/2",   # would point the fetcher inward
            "https://evil.example/alerts/feeds/1/2",
        ):
            ok, message = service.validate_feed_url(bad)
            self.assertFalse(ok, bad)
            self.assertTrue(message)

    def test_masking_keeps_the_ids_out_of_the_page(self):
        masked = store.mask_feed_url(
            "https://www.google.com/alerts/feeds/01234567890123456789/9876543210"
        )
        self.assertNotIn("01234567890123456789", masked)
        self.assertNotIn("9876543210", masked)
        self.assertIn("google.com/alerts/feeds", masked)


class DedupeTests(unittest.TestCase):
    def test_same_article_hashes_the_same_across_refreshes(self):
        # Google mints a fresh redirect wrapper every refresh; the destination is
        # what identifies the article.
        a, _ = service.unwrap_link(
            "https://www.google.com/url?rct=j&url=https%3A%2F%2Fx.example%2Fa%3Futm_source%3Dg&ct=ga&usg=1"
        )
        b, _ = service.unwrap_link(
            "https://www.google.com/url?rct=j&url=https%3A%2F%2Fx.example%2Fa%3Futm_source%3Dg&ct=ga&usg=2"
        )
        self.assertEqual(a, b)
        self.assertEqual(a, "https://x.example/a")
        self.assertEqual(
            store.dedupe_hash(alert_id=1, url=a, title="t"),
            store.dedupe_hash(alert_id=1, url=b, title="t"),
        )

    def test_two_alerts_catching_one_article_are_two_mentions(self):
        # Each alert is a monitored term; share of mentions counts both.
        self.assertNotEqual(
            store.dedupe_hash(alert_id=1, url="https://x.example/a", title="t"),
            store.dedupe_hash(alert_id=2, url="https://x.example/a", title="t"),
        )

    def test_a_link_less_entry_falls_back_to_its_headline(self):
        self.assertEqual(
            store.dedupe_hash(alert_id=1, url="", title="Same Headline"),
            store.dedupe_hash(alert_id=1, url="", title="same headline"),
        )


class IngestIsolationTests(unittest.TestCase):
    """One bad feed is recorded on its own alert and never stops the run."""

    def setUp(self):
        self.fetched: list[str] = []
        self.recorded: list[tuple[int, bool, str | None]] = []
        self.inserted: list[tuple[int, int]] = []
        self._fetch = service.fetch_feed
        self._record = store.record_fetch_result
        self._insert = store.insert_mentions

        def fake_record(alert_id, *, ok, new_count=0, error_message=None):
            self.recorded.append((alert_id, ok, error_message))

        def fake_insert(slug, alert, entries):
            self.inserted.append((alert.id, len(entries)))
            return len(entries)

        store.record_fetch_result = fake_record
        store.insert_mentions = fake_insert

    def tearDown(self):
        service.fetch_feed = self._fetch
        store.record_fetch_result = self._record
        store.insert_mentions = self._insert

    def test_a_failing_feed_is_recorded_and_the_next_one_still_runs(self):
        def fake_fetch(url, *, timeout=None):
            self.fetched.append(url)
            if len(self.fetched) == 1:
                raise RuntimeError("The feed returned 404 — the Google Alert may have been deleted.")
            return ATOM.encode("utf-8")

        service.fetch_feed = fake_fetch
        alerts = [_alert(1), _alert(2)]
        results = [service.ingest_alert(a) for a in alerts]

        self.assertFalse(results[0]["ok"])
        self.assertIn("404", results[0]["error"])
        self.assertTrue(results[1]["ok"])
        self.assertEqual(results[1]["new"], 2)
        self.assertEqual([(1, False), (2, True)], [(r[0], r[1]) for r in self.recorded])

    def test_a_malformed_feed_is_a_recorded_error_not_a_crash(self):
        service.fetch_feed = lambda url, *, timeout=None: b"<feed><entry>"
        result = service.ingest_alert(_alert(7))
        self.assertFalse(result["ok"])
        self.assertIn("not valid XML", result["error"])
        self.assertEqual(self.recorded[0][0], 7)

    def test_an_unreadable_stored_url_fails_that_alert_only(self):
        # What a rotated encryption key looks like: the store hands back ''.
        alert = _alert(9)
        alert.feed_url = ""
        result = service.ingest_alert(alert)
        self.assertFalse(result["ok"])
        self.assertIn("Re-paste", result["error"])
        self.assertEqual(self.fetched, [])


class OutcomeMessageTests(unittest.TestCase):
    """What the admin is told after a feed is polled in front of them."""

    def setUp(self):
        from dashboard.routes.web_mentions_routes import _outcome_message

        self.msg = _outcome_message

    def test_new_results_are_counted(self):
        saved, error = self.msg("EOS", {"ok": True, "new": 12, "seen": 12})
        self.assertIn("12 mentions found", saved)
        self.assertEqual(error, "")
        saved, _ = self.msg("EOS", {"ok": True, "new": 1, "seen": 1})
        self.assertIn("1 mention found", saved)   # not "1 mentions"

    def test_a_working_but_quiet_feed_says_so(self):
        saved, error = self.msg("EOS", {"ok": True, "new": 0, "seen": 8})
        self.assertIn("nothing new", saved)
        self.assertEqual(error, "")
        saved, error = self.msg("EOS", {"ok": True, "new": 0, "seen": 0})
        self.assertIn("reachable but empty", saved)
        self.assertEqual(error, "")

    def test_a_broken_feed_still_confirms_the_alert_was_saved(self):
        saved, error = self.msg(
            "EOS", {"ok": False, "error": "The feed returned 404 \u2014 the alert may be deleted."}
        )
        self.assertEqual(saved, "")
        self.assertIn("saved", error)      # the row is not lost
        self.assertIn("404", error)        # …and the reason is named


if __name__ == "__main__":
    unittest.main()
