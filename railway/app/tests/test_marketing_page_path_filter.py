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


if __name__ == "__main__":
    unittest.main()
