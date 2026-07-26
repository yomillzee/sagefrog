"""Dashboard read-cache TTL floor: resolution precedence and enforcement.

The floor a super admin sets in the Admin page (app_settings) wins over the
DASH_CACHE_TTL_SECONDS env var, which wins over "no floor". And whatever value
resolves must actually raise a short per-endpoint TTL inside
``api_routes._cached_bq_read``.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app_settings  # noqa: E402
import db_cache  # noqa: E402
from dashboard.routes import api_routes  # noqa: E402


class DashboardCacheTtlResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate from any real Postgres / env: stub the raw getter and clear env.
        self._stored: str | None = None
        self._saved_get = app_settings.get

        def _fake_get(key, default=None):
            if key == app_settings.DASHBOARD_CACHE_TTL_KEY:
                return self._stored if self._stored is not None else default
            return self._saved_get(key, default)

        app_settings.get = _fake_get
        self._saved_env = os.environ.pop("DASH_CACHE_TTL_SECONDS", None)

    def tearDown(self) -> None:
        app_settings.get = self._saved_get
        if self._saved_env is not None:
            os.environ["DASH_CACHE_TTL_SECONDS"] = self._saved_env
        else:
            os.environ.pop("DASH_CACHE_TTL_SECONDS", None)

    def test_unset_everywhere_is_zero(self) -> None:
        self.assertEqual(app_settings.dashboard_cache_ttl_seconds(), 0)

    def test_env_used_when_no_setting(self) -> None:
        os.environ["DASH_CACHE_TTL_SECONDS"] = "3600"
        self.assertEqual(app_settings.dashboard_cache_ttl_seconds(), 3600)

    def test_setting_overrides_env(self) -> None:
        os.environ["DASH_CACHE_TTL_SECONDS"] = "3600"
        self._stored = "86400"
        self.assertEqual(app_settings.dashboard_cache_ttl_seconds(), 86400)

    def test_explicit_zero_setting_disables_env_floor(self) -> None:
        # An admin who explicitly picks "Default" (0) must not be silently
        # overridden by a lingering env var.
        os.environ["DASH_CACHE_TTL_SECONDS"] = "3600"
        self._stored = "0"
        self.assertEqual(app_settings.dashboard_cache_ttl_seconds(), 0)

    def test_bad_setting_falls_back_to_env(self) -> None:
        os.environ["DASH_CACHE_TTL_SECONDS"] = "3600"
        self._stored = "not-a-number"
        self.assertEqual(app_settings.dashboard_cache_ttl_seconds(), 3600)


class CachedBqReadHonorsFloorTests(unittest.TestCase):
    """_cached_bq_read must persist with max(per-call TTL, resolved floor)."""

    def setUp(self) -> None:
        self._put_ttls: list[int] = []
        self._saved_get_cached = db_cache.get_cached
        self._saved_put_cached = db_cache.put_cached
        self._saved_floor = app_settings.dashboard_cache_ttl_seconds

        db_cache.get_cached = lambda source, payload: None  # always a miss

        def _put_cached(source, payload, *, response_json, row_count, ttl_seconds, **kw):
            self._put_ttls.append(ttl_seconds)

        db_cache.put_cached = _put_cached

    def tearDown(self) -> None:
        db_cache.get_cached = self._saved_get_cached
        db_cache.put_cached = self._saved_put_cached
        app_settings.dashboard_cache_ttl_seconds = self._saved_floor

    def test_floor_raises_short_ttl(self) -> None:
        app_settings.dashboard_cache_ttl_seconds = lambda: 86400
        api_routes._cached_bq_read("test.card", {"k": 1}, ttl_seconds=900, fetch=lambda: {"ok": True})
        self.assertEqual(self._put_ttls, [86400])

    def test_no_floor_keeps_per_call_ttl(self) -> None:
        app_settings.dashboard_cache_ttl_seconds = lambda: 0
        api_routes._cached_bq_read("test.card", {"k": 2}, ttl_seconds=900, fetch=lambda: {"ok": True})
        self.assertEqual(self._put_ttls, [900])

    def test_floor_never_lowers_a_longer_ttl(self) -> None:
        # A 6h snapshot TTL must not be shortened by a 1h floor.
        app_settings.dashboard_cache_ttl_seconds = lambda: 3600
        api_routes._cached_bq_read("test.snap", {}, ttl_seconds=6 * 3600, fetch=lambda: {"ok": True})
        self.assertEqual(self._put_ttls, [6 * 3600])


if __name__ == "__main__":
    unittest.main()
