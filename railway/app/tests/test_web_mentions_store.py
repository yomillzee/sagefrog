"""Persistence round-trip for web_mentions_store. Runs only when DATABASE_URL is
set (a throwaway Postgres); skipped otherwise so the pure suite stays hermetic."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

FEED_A = "https://www.google.com/alerts/feeds/999000111/1111111111"
FEED_B = "https://www.google.com/alerts/feeds/999000111/2222222222"


def _entry(url: str, *, title="A headline", source="Business Journal", published=None):
    return {
        "title": title, "url": url, "google_url": f"https://www.google.com/url?url={url}",
        "source": source, "snippet": "A snippet", "published_at": published,
        "entry_id": f"tag:{url}",
    }


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires a Postgres DATABASE_URL")
class WebMentionsStoreTests(unittest.TestCase):
    def setUp(self):
        import web_mentions_store

        self.store = web_mentions_store
        self.store.ensure_schema()
        self.slug = "unittest-web-mentions"
        with __import__("db").connection() as conn:
            conn.execute("DELETE FROM web_mentions WHERE client_slug = %s", (self.slug,))
            conn.execute("DELETE FROM web_mention_alerts WHERE client_slug = %s", (self.slug,))

    def _alert(self, name="EOS Worldwide", *, feed=FEED_A, category="brand", subject=""):
        return self.store.create_alert(
            self.slug, name=name, feed_url=feed, category=category, subject=subject,
            created_by="tester",
        )

    def test_alert_round_trip_and_feed_url_secrecy(self):
        alert = self._alert()
        self.assertTrue(alert.id)
        self.assertEqual(alert.feed_url, FEED_A)      # decrypted on read
        self.assertEqual(alert.category, "brand")
        self.assertEqual(alert.subject, "EOS Worldwide")  # defaults to the name

        # Whatever is on disk, it is not something a page could leak verbatim
        # unless it went through the store's own read path.
        with __import__("db").connection() as conn:
            (stored,) = conn.execute(
                "SELECT feed_url FROM web_mention_alerts WHERE id = %s", (alert.id,)
            ).fetchone()
        if self.store._encryption_secret():
            self.assertNotIn("999000111", stored)
        self.assertNotIn("999000111", alert.public_dict()["feed_url_masked"])
        self.assertNotIn("feed_url", alert.public_dict())

    def test_re_adding_a_feed_edits_rather_than_duplicating(self):
        # Encryption is randomised, so this only works because uniqueness is on
        # ``feed_key`` (a digest) rather than the stored ciphertext. Two rows on
        # one feed would poll it twice and count every result twice.
        first = self._alert(category="brand")
        again = self.store.create_alert(
            self.slug, name="EOS Worldwide (brand)", feed_url=FEED_A, category="industry"
        )
        self.assertEqual(first.id, again.id)
        self.assertEqual(again.category, "industry")
        self.assertEqual(len(self.store.list_alerts(self.slug)), 1)

    def test_deactivating_keeps_history_and_stops_polling(self):
        alert = self._alert()
        self.store.insert_mentions(self.slug, alert, [_entry("https://x.example/a")])
        self.store.update_alert(alert.id, self.slug, active=False, updated_by="tester")

        self.assertEqual(self.store.list_alerts(self.slug, active_only=True), [])
        self.assertEqual(self.store.count_mentions(self.slug), 1)
        self.assertNotIn(self.slug, self.store.slugs_with_active_alerts())
        # …and the alert cannot be deleted out from under that history.
        with self.assertRaises(ValueError):
            self.store.delete_alert(alert.id, self.slug)
        self.assertEqual(self.store.count_mentions(self.slug), 1)

    def test_an_alert_with_no_history_can_be_deleted(self):
        alert = self._alert()
        self.assertTrue(self.store.delete_alert(alert.id, self.slug))
        self.assertEqual(self.store.list_alerts(self.slug), [])

    def test_the_same_result_is_only_stored_once(self):
        alert = self._alert()
        entries = [
            _entry("https://x.example/a", published=datetime(2026, 8, 26, tzinfo=UTC)),
            _entry("https://x.example/b", title="Another", source="Trade Press"),
        ]
        self.assertEqual(self.store.insert_mentions(self.slug, alert, entries), 2)
        # A refresh re-serving the same two entries adds nothing.
        self.assertEqual(self.store.insert_mentions(self.slug, alert, entries), 0)
        self.assertEqual(self.store.count_mentions(self.slug), 2)

        # A second alert catching the same article is its own mention: two
        # monitored terms were mentioned.
        other = self._alert(name="Ninety", feed=FEED_B, category="competitor")
        self.assertEqual(self.store.insert_mentions(self.slug, other, entries[:1]), 1)

    def test_a_dateless_entry_is_dated_to_discovery_and_flagged(self):
        alert = self._alert()
        self.store.insert_mentions(self.slug, alert, [_entry("https://x.example/nodate")])
        (mention,) = self.store.list_mentions(self.slug)
        self.assertTrue(mention.published_estimated)
        self.assertEqual(mention.mention_date, datetime.now(tz=UTC).date())

    def test_summary_trend_filters_and_share(self):
        today = datetime.now(tz=UTC)
        brand = self._alert(name="EOS Worldwide", category="brand")
        rival = self._alert(name="Ninety", feed=FEED_B, category="competitor")
        self.store.insert_mentions(self.slug, brand, [
            _entry("https://x.example/1", published=today - timedelta(days=1)),
            _entry("https://x.example/2", published=today - timedelta(days=2),
                   source="Trade Press"),
        ])
        self.store.insert_mentions(self.slug, rival, [
            _entry("https://y.example/1", published=today - timedelta(days=1)),
        ])
        start = (today - timedelta(days=7)).date()
        end = today.date()

        totals = self.store.summary(self.slug, start=start, end=end)
        self.assertEqual(totals["total"], 3)
        self.assertEqual(totals["brand"], 2)
        self.assertEqual(totals["competitor"], 1)
        self.assertEqual(totals["sources"], 2)

        self.assertEqual(
            self.store.count_mentions(self.slug, start=start, end=end, category="brand"), 2
        )
        self.assertEqual(
            self.store.count_mentions(self.slug, start=start, end=end, alert_id=rival.id), 1
        )
        self.assertEqual(
            self.store.count_mentions(self.slug, start=start, end=end, source="Trade Press"), 1
        )
        self.assertEqual(sum(r["count"] for r in
                             self.store.daily_counts(self.slug, start=start, end=end)), 3)

        share = {r["subject"]: r["count"] for r in
                 self.store.share_of_mentions(self.slug, start=start, end=end)}
        self.assertEqual(share, {"EOS Worldwide": 2, "Ninety": 1})
        self.assertEqual(self.store.mention_counts_by_alert(self.slug)[brand.id], 2)

    def test_fetch_results_record_last_successful_check(self):
        alert = self._alert()
        self.store.record_fetch_result(alert.id, ok=False, error_message="404")
        failed = self.store.get_alert(alert.id, client_slug=self.slug)
        self.assertIsNotNone(failed.last_checked_at)
        self.assertIsNone(failed.last_success_at)   # a failed poll is not a check-in
        self.assertEqual(failed.consecutive_failures, 1)

        self.store.record_fetch_result(alert.id, ok=True, new_count=3)
        healed = self.store.get_alert(alert.id, client_slug=self.slug)
        self.assertIsNotNone(healed.last_success_at)
        self.assertIsNone(healed.last_error_message)
        self.assertEqual(healed.consecutive_failures, 0)
        self.assertEqual(healed.last_new_count, 3)

    def test_has_alerts_gates_the_sidebar(self):
        self.assertFalse(self.store.has_alerts(self.slug))
        self._alert()
        self.assertTrue(self.store.has_alerts(self.slug))


if __name__ == "__main__":
    unittest.main()
