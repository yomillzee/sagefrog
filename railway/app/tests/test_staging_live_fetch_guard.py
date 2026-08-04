"""Tests for the staging live-fetch guard (DASH_DISABLE_LIVE_FETCH).

On staging, `_cached_bq_read` must never fall through to a live BigQuery `fetch()`
on a cache miss: it serves seeded/cached data (including stale rows) and otherwise
returns an empty-but-valid response, capping staging's BigQuery spend at ~zero.
In production (flag unset) behavior is unchanged: a miss fetches and caches.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db_cache  # noqa: E402
from dashboard.routes import api_routes  # noqa: E402


class LiveFetchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._store: dict[str, object] = {}
        self.fetch_calls = 0

        def _get_cached(source, payload):
            # Only "fresh" rows live here; get_cached_stale sees the same store.
            hit = self._store.get(db_cache._hash_key(source, payload))
            if hit is None:
                return None
            return db_cache.CacheHit(row_count=0, response_json=hit, created_at=None, expires_at=None)

        def _get_cached_stale(source, payload):
            hit = self._store.get(db_cache._hash_key(source, payload))
            if hit is None:
                return None
            return db_cache.CacheHit(row_count=0, response_json=hit, created_at=None, expires_at=None)

        self._patchers = [
            patch.object(db_cache, "get_cached", _get_cached),
            patch.object(db_cache, "get_cached_stale", _get_cached_stale),
            patch.object(db_cache, "put_cached", lambda *a, **k: None),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()

    def _fetch(self):
        self.fetch_calls += 1
        return {"kind": "live"}

    def test_disabled_flag_blocks_live_fetch_and_returns_empty(self) -> None:
        with patch.dict("os.environ", {"DASH_DISABLE_LIVE_FETCH": "1"}, clear=False):
            self.assertTrue(api_routes._live_fetch_disabled())
            out = api_routes._cached_bq_read("acme.summary", {"a": 1}, ttl_seconds=900, fetch=self._fetch)
        self.assertEqual(out, {})
        self.assertEqual(self.fetch_calls, 0, "BigQuery fetch must not run when live fetch is disabled")

    def test_disabled_flag_serves_stale_cached_row(self) -> None:
        # A stale (TTL-lapsed) row is present: guard should serve it, not fetch.
        self._store[db_cache._hash_key("acme.summary", {"a": 1})] = {"kind": "stale"}
        with patch.dict("os.environ", {"DASH_DISABLE_LIVE_FETCH": "1"}, clear=False):
            out = api_routes._cached_bq_read("acme.summary", {"a": 1}, ttl_seconds=900, fetch=self._fetch)
        self.assertEqual(out, {"kind": "stale"})
        self.assertEqual(self.fetch_calls, 0)

    def test_enabled_flag_absent_still_fetches_on_miss(self) -> None:
        # Production default (flag unset): a miss must fetch live, unchanged.
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("DASH_DISABLE_LIVE_FETCH", None)
            self.assertFalse(api_routes._live_fetch_disabled())
            out = api_routes._cached_bq_read("acme.summary", {"a": 1}, ttl_seconds=900, fetch=self._fetch)
        self.assertEqual(out, {"kind": "live"})
        self.assertEqual(self.fetch_calls, 1)

    def test_flag_parsing_variants(self) -> None:
        import os
        for val, expected in [("1", True), ("true", True), ("YES", True), ("on", True),
                              ("0", False), ("false", False), ("", False)]:
            with patch.dict("os.environ", {"DASH_DISABLE_LIVE_FETCH": val}, clear=False):
                self.assertIs(api_routes._live_fetch_disabled(), expected, f"value={val!r}")
        os.environ.pop("DASH_DISABLE_LIVE_FETCH", None)


if __name__ == "__main__":
    unittest.main()
