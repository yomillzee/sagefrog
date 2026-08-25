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
from datetime import date
from pathlib import Path
from types import SimpleNamespace

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

        def _fetch_traffic(*, start_date, end_date, page_path_filter=None):
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

    def test_warm_follows_the_clients_pinned_default_preset(self) -> None:
        """A client whose dashboard lands on a pinned preset is warmed for *that*
        window, not Last 30 days.

        An admin can pin any Range preset as a client's default, and the page then
        opens on it. The warmer used to assume Last 30 days for everybody, so those
        clients were warmed on a window nothing ever requested — every first load
        after a sync ran cold. This asserts the endpoint the page actually calls
        (the pinned window) is served from cache.
        """
        original_cfg = api_routes._load_bq_test_config
        api_routes._load_bq_test_config = lambda slug: ("proj", "ds")

        import client_dashboard_config as cdc
        original_get = cdc.get_config
        cdc.get_config = lambda slug: SimpleNamespace(default_date_preset="last_month")
        try:
            report = dashboard_warm_service.warm_client_cache("pinned")
            self.assertEqual(report["preset"], "last_month")
            self.assertEqual(sorted(report["warmed"]), _EXPECTED_WARMED)

            (cur_s, cur_e), (cmp_s, cmp_e) = dashboard_warm_service._overview_windows(
                preset="last_month"
            )
            # Sanity: the pinned window is genuinely a different cache key than
            # the last_30 one the warmer used to assume — otherwise this test
            # would pass even with the bug present.
            self.assertNotEqual((cur_s, cur_e), (_CUR_START, _CUR_END))

            req = _DummyRequest()
            before = dict(self._calls)
            self.assertEqual(
                api_routes.client_summary("pinned", req, start_date=cur_s, end_date=cur_e)["kind"],
                "summary",
            )
            self.assertEqual(
                api_routes.client_traffic_acquisition(
                    "pinned", req, start_date=cur_s, end_date=cur_e)["kind"],
                "traffic",
            )
            self.assertEqual(
                api_routes.client_ai_traffic_daily(
                    "pinned", req, start_date=cmp_s, end_date=cmp_e)["kind"],
                "ai",
            )
            self.assertEqual(
                self._calls, before,
                "endpoint re-fetched — the warmer did not follow the pinned preset",
            )
        finally:
            cdc.get_config = original_get
            api_routes._load_bq_test_config = original_cfg

    def test_unknown_stored_preset_falls_back_to_last_30(self) -> None:
        # A stale/garbage stored value must not warm a nonsense window; the
        # renderer falls back to last_30 for these, so the warmer has to as well.
        import client_dashboard_config as cdc
        original_cfg = api_routes._load_bq_test_config
        original_get = cdc.get_config
        api_routes._load_bq_test_config = lambda slug: ("proj", "ds")
        cdc.get_config = lambda slug: SimpleNamespace(default_date_preset="since_forever")
        try:
            report = dashboard_warm_service.warm_client_cache("stale")
            self.assertEqual(report["preset"], "last_30")
            req = _DummyRequest()
            before = dict(self._calls)
            self.assertEqual(
                api_routes.client_summary(
                    "stale", req, start_date=_CUR_START, end_date=_CUR_END)["kind"],
                "summary",
            )
            self.assertEqual(self._calls, before)
        finally:
            cdc.get_config = original_get
            api_routes._load_bq_test_config = original_cfg


class OverviewWindowTests(unittest.TestCase):
    """``_overview_windows`` mirrors the page's ``applyPreset`` branch for branch.

    A window off by one day is a different cache key, so these pin the exact
    dates for a fixed "today" — Fri 2026-08-14, mid-month and mid-quarter.
    """

    TODAY = date(2026, 8, 14)

    def _win(self, preset: str):
        (cs, ce), (ps, pe) = dashboard_warm_service._overview_windows(
            today=self.TODAY, preset=preset
        )
        return (cs.isoformat(), ce.isoformat(), ps.isoformat(), pe.isoformat())

    def test_trailing_presets_end_yesterday(self) -> None:
        # lastN(n): current ends yesterday and spans n days; comparison is the
        # equal-length period immediately before it.
        self.assertEqual(
            self._win("last_7"), ("2026-08-07", "2026-08-13", "2026-07-31", "2026-08-06")
        )
        self.assertEqual(
            self._win("last_30"), ("2026-07-15", "2026-08-13", "2026-06-15", "2026-07-14")
        )
        self.assertEqual(
            self._win("last_90"), ("2026-05-16", "2026-08-13", "2026-02-15", "2026-05-15")
        )

    def test_calendar_presets(self) -> None:
        # this_week: Monday-to-yesterday, compared with the same span a week back.
        self.assertEqual(
            self._win("this_week"), ("2026-08-10", "2026-08-13", "2026-08-03", "2026-08-06")
        )
        self.assertEqual(
            self._win("last_week"), ("2026-08-03", "2026-08-09", "2026-07-27", "2026-08-02")
        )
        # this_month: month-to-yesterday vs the same day-of-month a month back.
        self.assertEqual(
            self._win("this_month"), ("2026-08-01", "2026-08-13", "2026-07-01", "2026-07-13")
        )
        self.assertEqual(
            self._win("last_month"), ("2026-07-01", "2026-07-31", "2026-06-01", "2026-06-30")
        )
        # this_quarter: quarter-to-yesterday vs the same span into the prior one.
        self.assertEqual(
            self._win("this_quarter"), ("2026-07-01", "2026-08-13", "2026-04-01", "2026-05-14")
        )
        self.assertEqual(
            self._win("last_quarter"), ("2026-04-01", "2026-06-30", "2026-01-01", "2026-03-31")
        )
        # this_year: year-to-yesterday vs the same span into the prior year.
        self.assertEqual(
            self._win("this_year"), ("2026-01-01", "2026-08-13", "2025-01-01", "2025-08-13")
        )

    def test_this_month_comparison_clamps_to_a_shorter_month(self) -> None:
        # March 30th has no counterpart in February, so the comparison end clamps
        # to the month's last day rather than rolling into March.
        def cmp_window(today: date):
            (_, _), (cmp_start, cmp_end) = dashboard_warm_service._overview_windows(
                today=today, preset="this_month"
            )
            return cmp_start.isoformat(), cmp_end.isoformat()

        self.assertEqual(cmp_window(date(2026, 3, 31)), ("2026-02-01", "2026-02-28"))
        # ...and to the 29th in a leap year.
        self.assertEqual(cmp_window(date(2024, 3, 31)), ("2024-02-01", "2024-02-29"))

    def test_this_presets_clamp_to_their_period_start(self) -> None:
        # On the 1st, "yesterday" is in the previous month — the page clamps the
        # window to the period start rather than letting it run backwards.
        (cur_start, cur_end), _ = dashboard_warm_service._overview_windows(
            today=date(2026, 7, 1), preset="this_month"
        )
        self.assertEqual((cur_start.isoformat(), cur_end.isoformat()),
                         ("2026-07-01", "2026-07-01"))

    def test_unknown_preset_falls_back_to_last_30(self) -> None:
        self.assertEqual(self._win("nonsense"), self._win("last_30"))


if __name__ == "__main__":
    unittest.main()
