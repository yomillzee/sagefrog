from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import marketing_service as ms

# The Website Analytics page-path scope (set by an admin from the analytics page)
# is applied server-side: the page-path fetch functions add a CONTAINS_SUBSTR
# clause so their BigQuery reads only return matching paths. A row is kept when
# ANY pattern matches; an empty scope adds no clause (whole site).

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 31)


class ParseFilterTests(unittest.TestCase):
    def test_parse_drops_blanks_and_comments(self) -> None:
        self.assertEqual(
            ms.parse_page_path_filter("/careers\n# note\n\n  /jobs  "),
            ["/careers", "/jobs"],
        )

    def test_parse_empty(self) -> None:
        self.assertEqual(ms.parse_page_path_filter(""), [])
        self.assertEqual(ms.parse_page_path_filter(None), [])


class ClauseBuilderTests(unittest.TestCase):
    def test_builds_or_of_contains_substr(self) -> None:
        params: dict = {}
        clause = ms._page_path_filter_clause(
            ["/careers", "/jobs"], column="page_path", params=params
        )
        self.assertEqual(
            clause,
            " AND (CONTAINS_SUBSTR(page_path, @pp0) OR CONTAINS_SUBSTR(page_path, @pp1))",
        )
        self.assertEqual(params["pp0"].value, "/careers")
        self.assertEqual(params["pp1"].value, "/jobs")

    def test_column_is_honored(self) -> None:
        params: dict = {}
        clause = ms._page_path_filter_clause(
            ["/careers"], column="landing_page", params=params
        )
        self.assertIn("CONTAINS_SUBSTR(landing_page, @pp0)", clause)

    def test_empty_adds_nothing(self) -> None:
        params: dict = {}
        self.assertEqual(
            ms._page_path_filter_clause([], column="page_path", params=params), ""
        )
        self.assertEqual(params, {})
        # whitespace-only patterns are dropped, too
        self.assertEqual(
            ms._page_path_filter_clause(["  ", ""], column="page_path", params={}), ""
        )


class FetchInjectionTests(unittest.TestCase):
    """Each page-path fetch threads the scope into its SQL on the right column."""

    def setUp(self) -> None:
        self._orig = ms._run_query
        self._sql: list[str] = []

        def _capture(sql, params=None, max_rows=None):
            self._sql.append(sql)
            return []

        ms._run_query = _capture
        self._ctx = ms.route(client_key="nixon-hr", project_id="p", mart_dataset_id="d")
        self._ctx.__enter__()

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)
        ms._run_query = self._orig

    def test_pages_top_scoped_on_page_path(self) -> None:
        ms.fetch_pages_top(start_date=START, end_date=END, page_path_filter=["/careers"])
        self.assertIn("CONTAINS_SUBSTR(page_path, @pp0)", self._sql[0])

    def test_pages_top_unscoped_by_default(self) -> None:
        ms.fetch_pages_top(start_date=START, end_date=END)
        self.assertNotIn("CONTAINS_SUBSTR", self._sql[0])

    def test_landing_scoped_on_landing_page(self) -> None:
        ms.fetch_landing_pages(
            start_date=START, end_date=END, page_path_filter=["/careers", "/jobs"]
        )
        self.assertIn("CONTAINS_SUBSTR(landing_page, @pp0)", self._sql[0])
        self.assertIn("CONTAINS_SUBSTR(landing_page, @pp1)", self._sql[0])

    def test_pages_sources_scoped(self) -> None:
        ms.fetch_pages_sources(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertIn("CONTAINS_SUBSTR(page_path, @pp0)", self._sql[0])

    def test_ai_traffic_daily_scoped(self) -> None:
        ms.fetch_ai_traffic_daily(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertIn("CONTAINS_SUBSTR(page_path, @pp0)", self._sql[0])

    def test_page_key_events_scopes_both_queries(self) -> None:
        ms.fetch_page_key_events(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertTrue(all("CONTAINS_SUBSTR(page_path, @pp0)" in s for s in self._sql))

    def test_landing_page_events_scopes_both_queries(self) -> None:
        ms.fetch_landing_page_events(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertTrue(
            all("CONTAINS_SUBSTR(landing_page, @pp0)" in s for s in self._sql)
        )


class TrafficAcquisitionScopeTests(unittest.TestCase):
    """The Sessions-over-time series is page-scopeable even though its usual
    source (vw_ga4_traffic_acq_daily) has no page_path: under a scope the daily
    series is read from vw_page_path_daily instead. The channel and source/medium
    breakdowns have no page grain, so they stay site-wide."""

    def setUp(self) -> None:
        self._orig = ms._run_query
        self._captured: list[tuple[str, dict]] = []

        def _capture(sql, params=None, max_rows=None):
            self._captured.append((sql, dict(params or {})))
            return []

        ms._run_query = _capture
        self._ctx = ms.route(client_key="nixon-hr", project_id="p", mart_dataset_id="d")
        self._ctx.__enter__()

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)
        ms._run_query = self._orig

    def _run(self, **kw):
        ms.fetch_traffic_acquisition(start_date=START, end_date=END, **kw)
        # Query order: by_channel, daily, by_source.
        return {
            "by_channel": self._captured[0],
            "daily": self._captured[1],
            "by_source": self._captured[2],
        }

    def test_daily_series_is_page_scoped(self) -> None:
        q = self._run(page_path_filter=["/careers"])
        sql, params = q["daily"]
        self.assertIn("vw_page_path_daily", sql)
        self.assertIn("CONTAINS_SUBSTR(page_path, @pp0)", sql)
        self.assertIn("pp0", params)
        # Still the shape the trend chart consumes.
        self.assertIn("engaged_sessions", sql)

    def test_daily_series_unscoped_by_default(self) -> None:
        sql, params = self._run()["daily"]
        self.assertIn("vw_ga4_traffic_acq_daily", sql)
        self.assertNotIn("CONTAINS_SUBSTR", sql)
        self.assertNotIn("pp0", params)

    def test_breakdowns_stay_site_wide_and_unpolluted(self) -> None:
        q = self._run(page_path_filter=["/careers"])
        for key in ("by_channel", "by_source"):
            sql, params = q[key]
            self.assertIn("vw_ga4_traffic_acq_daily", sql)
            self.assertNotIn("CONTAINS_SUBSTR", sql)
            # The scope params belong to the daily query only.
            self.assertNotIn("pp0", params)


class DemographicsScopeTests(unittest.TestCase):
    """Geography is page-scopeable — vw_ga4_geo_page_daily carries a page_path,
    so under a scope the map and cities read from it instead of the site-wide
    vw_ga4_geo_daily. Age/gender are user-scoped in GA4 with no page dimension,
    so a scope drops them rather than narrowing them."""

    def setUp(self) -> None:
        self._orig = ms._run_query
        self._captured: list[tuple[str, dict]] = []
        self._raise_missing = False

        def _capture(sql, params=None, max_rows=None):
            self._captured.append((sql, dict(params or {})))
            if self._raise_missing and "vw_ga4_geo_page_daily" in sql:
                raise RuntimeError(
                    "404 Not found: Table p:d.vw_ga4_geo_page_daily was not found"
                )
            return []

        ms._run_query = _capture
        self._ctx = ms.route(client_key="nixon-hr", project_id="p", mart_dataset_id="d")
        self._ctx.__enter__()

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)
        ms._run_query = self._orig

    def _sql(self) -> str:
        return "\n".join(s for s, _ in self._captured)

    def test_scoped_geo_reads_the_page_grained_view(self) -> None:
        out = ms.fetch_demographics(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        sql = self._sql()
        self.assertIn("vw_ga4_geo_page_daily", sql)
        self.assertNotIn("vw_ga4_geo_daily`", sql)
        # Both geography queries (cities + the state rollup) carry the scope.
        geo_queries = [s for s, _ in self._captured if "vw_ga4_geo_page_daily" in s]
        self.assertEqual(len(geo_queries), 2)
        self.assertTrue(
            all("CONTAINS_SUBSTR(page_path, @pp0)" in s for s in geo_queries)
        )
        self.assertTrue(out["scoped"])
        self.assertTrue(out["geo_scope_available"])

    def test_scope_drops_age_and_gender_without_querying_them(self) -> None:
        out = ms.fetch_demographics(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertEqual(out["by_age"], [])
        self.assertEqual(out["by_gender"], [])
        self.assertFalse(out["user_scoped_available"])
        # No point spending a query on data the panel won't show.
        self.assertNotIn("vw_ga4_demographics_daily", self._sql())

    def test_unscoped_is_unchanged(self) -> None:
        out = ms.fetch_demographics(start_date=START, end_date=END)
        sql = self._sql()
        self.assertIn("vw_ga4_geo_daily", sql)
        self.assertNotIn("vw_ga4_geo_page_daily", sql)
        self.assertNotIn("CONTAINS_SUBSTR", sql)
        self.assertIn("vw_ga4_demographics_daily", sql)
        self.assertFalse(out["scoped"])
        self.assertTrue(out["user_scoped_available"])

    def test_missing_scoped_view_degrades_instead_of_falling_back(self) -> None:
        # The page-grained view only exists after a sync writes it. Until then
        # the panel must report "no scoped geography" — serving the site-wide
        # table here would present whole-site geography as page-scoped.
        self._raise_missing = True
        out = ms.fetch_demographics(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertFalse(out["geo_scope_available"])
        self.assertEqual(out["by_city"], [])
        self.assertEqual(out["by_region"], [])
        self.assertNotIn("vw_ga4_geo_daily`", self._sql())

    def test_other_query_errors_still_raise(self) -> None:
        def _boom(sql, params=None, max_rows=None):
            raise RuntimeError("403 Access Denied: Table p:d.vw_ga4_geo_page_daily")

        ms._run_query = _boom
        with self.assertRaises(RuntimeError):
            ms.fetch_demographics(
                start_date=START, end_date=END, page_path_filter=["/careers"]
            )


if __name__ == "__main__":
    unittest.main()
