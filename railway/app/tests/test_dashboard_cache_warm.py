"""Cache-warming drift guard.

``dashboard_warm_service.warm_client_cache`` pre-populates the same ``api_cache``
entries the dashboard Overview card endpoints read. The whole scheme only works
if a warmed entry is keyed *identically* to what the endpoint later looks up — if
the two ever diverge (a source name, a payload, or the date window), warming
silently stops helping.

These tests prove parity end-to-end: warm a client, then call the real endpoint
functions **with the exact window the Overview page requests** (the ``Last 30
days`` preset's current + prior-period windows) and assert they are served from
the warmed cache — the underlying fetch is NOT called a second time. Passing the
frontend's real window (not ``None``) is deliberate: the page always sends
explicit dates, so a warmer keyed to any other window would miss in production
even while a ``None``-date test passed.
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

# The exact windows the Overview page requests on load (see _overview_windows,
# which mirrors the page's applyPreset('last_30')). Both the warmer and these
# tests derive their dates from here, and the endpoints are called with them.
(_CUR_START, _CUR_END), (_CMP_START, _CMP_END) = dashboard_warm_service._overview_windows()

# Every Overview card the warmer is expected to populate.
_EXPECTED_WARMED = [
    "ai_traffic",
    "ai_traffic_prev",
    "health",
    "summary",
    "summary_prev",
    "traffic_acquisition",
    "traffic_acquisition_prev",
]


class _DummyRequest:
    """Stand-in for a FastAPI Request; only ever passed to (patched) auth."""


class CacheWarmDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self._store: dict[str, object] = {}
        self._calls: dict[str, int] = {"summary": 0, "health": 0, "traffic": 0, "ai": 0}

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

        def _fetch_traffic(*, start_date, end_date):
            self._calls["traffic"] += 1
            return {"kind": "traffic"}

        def _fetch_ai(*, start_date, end_date, page_path_filter=None):
            self._calls["ai"] += 1
            return {"kind": "ai"}

        self._patches = [
            (db_cache, "get_cached", _get_cached),
            (db_cache, "put_cached", _put_cached),
            (marketing_service, "fetch_summary", _fetch_summary),
            (marketing_service, "fetch_marketing_health", _fetch_health),
            (marketing_service, "fetch_traffic_acquisition", _fetch_traffic),
            (marketing_service, "fetch_ai_traffic_daily", _fetch_ai),
            (web_auth, "authenticate_dashboard_api", lambda *a, **k: None),
            (web_auth, "authenticate_dashboard_api_any", lambda *a, **k: None),
        ]
        self._saved = [(obj, name, getattr(obj, name)) for obj, name, _ in self._patches]
        for obj, name, repl in self._patches:
            setattr(obj, name, repl)

    def tearDown(self) -> None:
        for obj, name, original in self._saved:
            setattr(obj, name, original)

    def test_nixon_warm_then_overview_endpoints_hit_cache(self) -> None:
        report = dashboard_warm_service.warm_client_cache("nixon")
        self.assertEqual(sorted(report["warmed"]), _EXPECTED_WARMED)
        # One fetch per (card, window): summary + traffic + ai each warmed for
        # the current and prior windows; health once.
        self.assertEqual(self._calls, {"summary": 2, "health": 1, "traffic": 2, "ai": 2})

        req = _DummyRequest()
        # Each Overview endpoint, called with the *page's* window, must be served
        # from the warmed cache — no second fetch.
        self.assertEqual(
            api_routes.client_summary("nixon", req, start_date=_CUR_START, end_date=_CUR_END)["kind"],
            "summary",
        )
        self.assertEqual(
            api_routes.client_summary("nixon", req, start_date=_CMP_START, end_date=_CMP_END)["kind"],
            "summary",
        )
        self.assertEqual(api_routes.client_health("nixon", req, limit=100)["kind"], "health")
        self.assertEqual(
            api_routes.nixon_traffic_acquisition(req, start_date=_CUR_START, end_date=_CUR_END)["kind"],
            "traffic",
        )
        self.assertEqual(
            api_routes.nixon_ai_traffic_daily(req, start_date=_CUR_START, end_date=_CUR_END)["kind"],
            "ai",
        )
        self.assertEqual(
            self._calls, {"summary": 2, "health": 1, "traffic": 2, "ai": 2},
            "endpoint re-fetched — warmed cache key drifted from the endpoint's",
        )

    def test_generic_client_warm_then_overview_endpoints_hit_cache(self) -> None:
        # Generic BQ clients resolve a project/dataset; stub it so no real config
        # is needed. Both warmer and endpoint go through this same helper.
        original = api_routes._load_bq_test_config
        api_routes._load_bq_test_config = lambda slug: ("proj", "ds")
        try:
            report = dashboard_warm_service.warm_client_cache("acme")
            self.assertEqual(sorted(report["warmed"]), _EXPECTED_WARMED)
            self.assertEqual(self._calls, {"summary": 2, "health": 1, "traffic": 2, "ai": 2})

            req = _DummyRequest()
            self.assertEqual(
                api_routes.client_summary("acme", req, start_date=_CUR_START, end_date=_CUR_END)["kind"],
                "summary",
            )
            self.assertEqual(api_routes.client_health("acme", req, limit=100)["kind"], "health")
            self.assertEqual(
                api_routes.client_traffic_acquisition("acme", req, start_date=_CUR_START, end_date=_CUR_END)["kind"],
                "traffic",
            )
            self.assertEqual(
                api_routes.client_ai_traffic_daily("acme", req, start_date=_CMP_START, end_date=_CMP_END)["kind"],
                "ai",
            )
            self.assertEqual(
                self._calls, {"summary": 2, "health": 1, "traffic": 2, "ai": 2},
                "generic endpoint re-fetched — warmed cache key drifted",
            )
        finally:
            api_routes._load_bq_test_config = original

    def test_marketing_top_campaigns_not_warmed(self) -> None:
        # The Explorer 'top campaigns' card is intentionally no longer warmed —
        # only Overview cards are. Its fetch stub must never be touched.
        marketing_called = {"n": 0}

        def _fetch_marketing(*, start_date, end_date, top_limit):
            marketing_called["n"] += 1
            return {"kind": "marketing"}

        saved = marketing_service.fetch_marketing
        marketing_service.fetch_marketing = _fetch_marketing
        try:
            report = dashboard_warm_service.warm_client_cache("nixon")
            self.assertNotIn("marketing", report["warmed"])
            self.assertEqual(marketing_called["n"], 0)
        finally:
            marketing_service.fetch_marketing = saved

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
            self.assertEqual(self._calls, {"summary": 0, "health": 0, "traffic": 0, "ai": 0})
        finally:
            api_routes._load_bq_test_config = original


if __name__ == "__main__":
    unittest.main()
