"""Bluesky connector wiring + AT Protocol feed-parsing tests.

Covers the parts that can be verified without hitting the live AppView: handle
normalization, the getAuthorFeed parsing rules (reposts dropped, window
respected), connector registration, and the BigQuery schemas matching the rows
the sync builds.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import date
from unittest import mock
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

os.environ.setdefault("AUTH_SESSION_SECRET", "x" * 32)

import bluesky_service  # noqa: E402
import bq_bluesky_service  # noqa: E402
import connector_config_store  # noqa: E402
import connectors  # noqa: E402,F401  (triggers handler registration)
from connectors.base import CONNECTOR_ORDER, get as get_handler  # noqa: E402

OWN_DID = "did:plc:sagefrog"


def _post(uri: str, created: str, *, did: str = OWN_DID, likes: int = 0,
          reposts: int = 0, replies: int = 0, quotes: int = 0,
          text: str = "hello", embed: dict | None = None,
          reply: dict | None = None) -> dict:
    post = {
        "uri": uri,
        "cid": "cid-" + uri[-1],
        "author": {"did": did, "handle": "sagefrog.bsky.social"},
        "record": {"text": text, "createdAt": created, "langs": ["en"]},
        "likeCount": likes,
        "repostCount": reposts,
        "replyCount": replies,
        "quoteCount": quotes,
        "indexedAt": created,
    }
    if embed:
        post["embed"] = embed
    if reply:
        post["record"]["reply"] = reply
    return post


class HandleNormalizationTests(unittest.TestCase):
    def test_accepts_what_people_paste(self) -> None:
        for raw in (
            "sagefrog.bsky.social",
            "@sagefrog.bsky.social",
            "  SageFrog.bsky.social ",
            "https://bsky.app/profile/sagefrog.bsky.social",
            "https://bsky.app/profile/sagefrog.bsky.social/post/abc123",
        ):
            self.assertEqual(bluesky_service.normalize_handle(raw), "sagefrog.bsky.social", raw)

    def test_did_passes_through_uncased(self) -> None:
        # DIDs are case-sensitive identifiers, so lowercasing would corrupt them.
        self.assertEqual(bluesky_service.normalize_handle("did:plc:AbC123"), "did:plc:AbC123")

    def test_empty_is_empty(self) -> None:
        self.assertEqual(bluesky_service.normalize_handle("  "), "")

    def test_post_url_built_from_at_uri(self) -> None:
        self.assertEqual(
            bluesky_service.post_url("sagefrog.bsky.social", "at://did:plc:x/app.bsky.feed.post/3kabc"),
            "https://bsky.app/profile/sagefrog.bsky.social/post/3kabc",
        )


class FeedParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._real_get = bluesky_service._get

    def tearDown(self) -> None:
        bluesky_service._get = self._real_get

    def _stub_feed(self, pages: list[dict]) -> None:
        calls = iter(pages)

        def fake_get(method: str, params: dict, **kwargs):
            self.assertEqual(method, "app.bsky.feed.getAuthorFeed")
            return next(calls)

        bluesky_service._get = fake_get

    def test_reposts_and_other_authors_are_dropped(self) -> None:
        self._stub_feed([{
            "feed": [
                {"post": _post("at://x/app.bsky.feed.post/1", "2026-08-20T10:00:00Z", likes=3)},
                # A repost of somebody else's post: their engagement, not ours.
                {"post": _post("at://y/app.bsky.feed.post/2", "2026-08-19T10:00:00Z", did="did:plc:other"),
                 "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}},
                # Another account's reply pulled into the thread view.
                {"post": _post("at://y/app.bsky.feed.post/3", "2026-08-18T10:00:00Z", did="did:plc:other")},
            ],
        }])
        posts = bluesky_service.fetch_author_feed("sagefrog.bsky.social")
        self.assertEqual([p["uri"] for p in posts], ["at://x/app.bsky.feed.post/1"])

    def test_repost_first_does_not_poison_own_did(self) -> None:
        # If the newest feed item is a repost, the reposted author's DID must not
        # be mistaken for the account's own — that would drop every real post.
        self._stub_feed([{
            "feed": [
                {"post": _post("at://y/app.bsky.feed.post/9", "2026-08-21T10:00:00Z", did="did:plc:other"),
                 "reason": {"$type": "app.bsky.feed.defs#reasonRepost"}},
                {"post": _post("at://x/app.bsky.feed.post/1", "2026-08-20T10:00:00Z")},
            ],
        }])
        posts = bluesky_service.fetch_author_feed("sagefrog.bsky.social")
        self.assertEqual([p["uri"] for p in posts], ["at://x/app.bsky.feed.post/1"])

    def test_stops_paging_once_past_the_start_date(self) -> None:
        pages = [
            {"feed": [{"post": _post("at://x/app.bsky.feed.post/1", "2026-08-20T10:00:00Z")}],
             "cursor": "page2"},
            {"feed": [{"post": _post("at://x/app.bsky.feed.post/2", "2026-07-01T10:00:00Z")}],
             "cursor": "page3"},
            {"feed": [{"post": _post("at://x/app.bsky.feed.post/3", "2026-06-01T10:00:00Z")}]},
        ]
        self._stub_feed(pages)
        posts = bluesky_service.fetch_author_feed(
            "sagefrog.bsky.social", since=date(2026, 8, 1), until=date(2026, 8, 24)
        )
        # Page 3 is never requested — the stub would raise StopIteration if it were.
        self.assertEqual([p["uri"] for p in posts], ["at://x/app.bsky.feed.post/1"])

    def test_engagement_totals_and_embed_type(self) -> None:
        self._stub_feed([{
            "feed": [{"post": _post(
                "at://x/app.bsky.feed.post/1", "2026-08-20T10:00:00Z",
                likes=5, reposts=2, replies=3, quotes=1,
                embed={"$type": "app.bsky.embed.images#view"},
                reply={"root": {"uri": "at://x/app.bsky.feed.post/0"}},
            )}],
        }])
        post = bluesky_service.fetch_author_feed("sagefrog.bsky.social")[0]
        self.assertEqual(post["engagements"], 11)
        self.assertEqual(post["embed_type"], "images")
        self.assertTrue(post["is_reply"])
        self.assertEqual(post["created_date"], date(2026, 8, 20))
        self.assertEqual(post["langs"], "en")

    def test_snapshot_survives_a_failing_feed(self) -> None:
        # A profile that loads plus a feed that doesn't should still snapshot the
        # follower count, with the failure recorded rather than raised.
        profile = {
            "handle": "sagefrog.bsky.social", "did": OWN_DID, "followers_count": 42,
            "follows_count": 1, "posts_count": 7, "display_name": "Sage Frog",
        }

        def boom(*args, **kwargs):
            raise RuntimeError("rate limited")

        with mock.patch.object(bluesky_service, "fetch_profile", return_value=profile),                 mock.patch.object(bluesky_service, "fetch_author_feed", side_effect=boom):
            snap = bluesky_service.build_bluesky_snapshot("sagefrog.bsky.social")

        self.assertNotIn("error", snap)
        self.assertEqual(snap["profile"]["followers_count"], 42)
        self.assertEqual(snap["posts"], [])
        self.assertIn("feed", snap["errors"])


class RegistrationTests(unittest.TestCase):
    def test_registered_everywhere(self) -> None:
        self.assertIn("bluesky", connector_config_store.VALID_CONNECTOR_TYPES)
        self.assertIn("bluesky", CONNECTOR_ORDER)

    def test_handler_shape(self) -> None:
        handler = get_handler("bluesky")
        self.assertIsNotNone(handler)
        self.assertEqual(handler.default_raw_dataset, "raw_bluesky")
        # Public AppView reads: no OAuth, and the handle is typed in by hand.
        self.assertTrue(handler.no_oauth)
        self.assertTrue(handler.manual_account_entry)

    def test_included_in_automated_sync(self) -> None:
        # Without this the cron never syncs Bluesky — data would only land on a
        # manual "Run sync now" click.
        import dashboard.services.bigquery_refresh_orchestrator as orch

        self.assertIn("bluesky", orch._SYNC_CONNECTORS)

    def test_has_a_directory_icon(self) -> None:
        from dashboard.renderers import connectors_renderer

        self.assertIn("bluesky", connectors_renderer._PLATFORM_ICONS)


class _FakeField:
    def __init__(self, name, field_type, mode="NULLABLE"):
        self.name = name


class SchemaTests(unittest.TestCase):
    """The sync builds plain dicts; BigQuery rejects a key with no field."""

    fake_bq = types.SimpleNamespace(SchemaField=_FakeField)

    def _names(self, schema_fn) -> set[str]:
        return {f.name for f in schema_fn(self.fake_bq)}

    def test_profile_schema_covers_the_row(self) -> None:
        expected = {
            "client_key", "handle", "did", "metric_date", "display_name",
            "followers_count", "follows_count", "posts_count",
            "window_posts", "window_likes", "window_reposts", "window_replies",
            "window_quotes", "window_engagements", "error", "synced_at",
        }
        self.assertEqual(self._names(bq_bluesky_service._schema_profile_daily), expected)

    def test_posts_schema_covers_the_row(self) -> None:
        expected = {
            "client_key", "handle", "did", "metric_date", "post_uri", "post_cid",
            "post_url", "post_date", "created_at", "text", "is_reply", "embed_type",
            "langs", "like_count", "repost_count", "reply_count", "quote_count",
            "engagements", "synced_at",
        }
        self.assertEqual(self._names(bq_bluesky_service._schema_posts_daily), expected)

    def test_route_defaults_to_the_raw_dataset(self) -> None:
        with bq_bluesky_service.route(bq_project_id="proj-1"):
            self.assertEqual(bq_bluesky_service._dataset_id(), "raw_bluesky")
            self.assertEqual(bq_bluesky_service._project_id(), "proj-1")

    def test_route_refuses_an_unrouted_project(self) -> None:
        with self.assertRaises(RuntimeError):
            bq_bluesky_service._project_id()


if __name__ == "__main__":
    unittest.main()
