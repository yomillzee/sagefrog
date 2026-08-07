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


if __name__ == "__main__":
    unittest.main()
