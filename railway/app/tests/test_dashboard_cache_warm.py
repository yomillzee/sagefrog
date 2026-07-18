"""Cache-warming drift guard.

``dashboard_warm_service.warm_client_cache`` pre-populates the same ``api_cache``
entries the dashboard card endpoints read. The whole scheme only works if a
warmed entry is keyed *identically* to what the endpoint later looks up — if the
two ever diverge, warming silently stops helping.

These tests prove parity end-to-end: warm a client, then call the real endpoint
functions and assert they are served from the warmed cache (the underlying
BigQuery fetch is NOT called a second time). If a source name, payload, or date
window drifts between the warmer and an endpoint, the endpoint would re-fetch and
the assertion fails.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db_cache  # noqa: E402
import marketing_service  # noqa: E402
import web_auth  # noqa: E402
from dashboard.routes import api_routes  # noqa: E402
from dashboard.services import dashboard_warm_service  # noqa: E402


class _DummyRequest:
    """Stand-in for a FastAPI Request; only ever passed to (patched) auth."""


class CacheWarmDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self._store: dict[str, object] = {}
        self._calls: dict[str, int] = {"summary": 0, "health": 0, "marketing": 0}

        # In-memory cache backing, keyed exactly like the real cache.
        def _get_cached(source, payload):
            key = db_cache._hash_key(source, payload)
            hit = self._store.get(key)
            if hit is None:
                return None
            return db_cache.CacheHit(
                row_count=0, response_json=hit, created_at=None, expires_at=None,
            )

        def _put_cached(source, payload, *, response_json, row_count, **kwargs):
            self._store[db_cache._hash_key(source, payload)] = response_json

        # Counting fetch stubs — one increment per real BigQuery read.
        def _fetch_summary(*, start_date, end_date):
            self._calls["summary"] += 1
            return {"kind": "summary"}

        def _fetch_health(*, limit):
            self._calls["health"] += 1
            return {"kind": "health"}

        def _fetch_marketing(*, start_date, end_date, top_limit):
            self._calls["marketing"] += 1
            return {"kind": "marketing"}

        self._patches = [
            (db_cache, "get_cached", _get_cached),
            (db_cache, "put_cached", _put_cached),
            (marketing_service, "fetch_summary", _fetch_summary),
            (marketing_service, "fetch_marketing_health", _fetch_health),
            (marketing_service, "fetch_marketing", _fetch_marketing),
            (web_auth, "authenticate_dashboard_api", lambda *a, **k: None),
            (web_auth, "authenticate_dashboard_api_any", lambda *a, **k: None),
        ]
        self._saved = [(obj, name, getattr(obj, name)) for obj, name, _ in self._patches]
        for obj, name, repl in self._patches:
            setattr(obj, name, repl)

    def tearDown(self) -> None:
        for obj, name, original in self._saved:
            setattr(obj, name, original)

    def test_nixon_warm_then_endpoints_hit_cache(self) -> None:
        report = dashboard_warm_service.warm_client_cache("nixon")
        self.assertEqual(sorted(report["warmed"]), ["health", "marketing", "summary"])
        self.assertEqual(self._calls, {"summary": 1, "health": 1, "marketing": 1})

        req = _DummyRequest()
        # Each endpoint must be served from the warmed cache — no second fetch.
        # (Called directly, so pass the params FastAPI would otherwise inject.)
        self.assertEqual(
            api_routes.client_summary("nixon", req, start_date=None, end_date=None)["kind"],
            "summary",
        )
        self.assertEqual(api_routes.client_health("nixon", req, limit=100)["kind"], "health")
        self.assertEqual(
            api_routes.nixon_marketing(req, start_date=None, end_date=None, top_limit=10)["kind"],
            "marketing",
        )
        self.assertEqual(
            self._calls, {"summary": 1, "health": 1, "marketing": 1},
            "endpoint re-fetched — warmed cache key drifted from the endpoint's",
        )

    def test_generic_client_warm_then_endpoints_hit_cache(self) -> None:
        # Generic BQ clients resolve a project/dataset; stub it so no real config
        # is needed. Both warmer and endpoint go through this same helper.
        original = api_routes._load_bq_test_config
        api_routes._load_bq_test_config = lambda slug: ("proj", "ds")
        try:
            report = dashboard_warm_service.warm_client_cache("acme")
            self.assertEqual(sorted(report["warmed"]), ["health", "summary"])
            self.assertEqual(self._calls["summary"], 1)
            self.assertEqual(self._calls["health"], 1)

            req = _DummyRequest()
            self.assertEqual(
                api_routes.client_summary("acme", req, start_date=None, end_date=None)["kind"],
                "summary",
            )
            self.assertEqual(api_routes.client_health("acme", req, limit=100)["kind"], "health")
            self.assertEqual(
                self._calls["summary"], 1,
                "generic summary endpoint re-fetched — cache key drifted",
            )
            self.assertEqual(
                self._calls["health"], 1,
                "generic health endpoint re-fetched — cache key drifted",
            )
        finally:
            api_routes._load_bq_test_config = original

    def test_warm_unconfigured_client_is_noop(self) -> None:
        # No BQ project configured → the warmer must skip quietly, not raise.
        original = api_routes._load_bq_test_config

        def _raise(slug):
            raise RuntimeError("no project configured")

        api_routes._load_bq_test_config = _raise
        try:
            report = dashboard_warm_service.warm_client_cache("brokenclient")
            self.assertEqual(report["warmed"], [])
            self.assertIn("all", report["skipped"])
            self.assertEqual(self._calls, {"summary": 0, "health": 0, "marketing": 0})
        finally:
            api_routes._load_bq_test_config = original


if __name__ == "__main__":
    unittest.main()
