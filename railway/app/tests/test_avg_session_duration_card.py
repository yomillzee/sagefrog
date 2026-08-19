from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import marketing_service as ms

# Website Analytics carries an "Average session duration" card between Audience
# and Demographics. GA4 only reports averageSessionDuration next to a dimension,
# so the figure is rebuilt from the landing-page report: each page's average is
# weighted by its sessions, never averaged flat.

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 31)


class FetchSessionDurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = ms._run_query
        self._sql: list[str] = []
        self._rows: list[dict] = [
            {"sessions": 1200, "avg_session_duration_seconds": 95.4},
        ]

        def _capture(sql, params=None, max_rows=None):
            self._sql.append(sql)
            return self._rows

        ms._run_query = _capture
        self._ctx = ms.route(client_key="acme", project_id="p", mart_dataset_id="d")
        self._ctx.__enter__()

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)
        ms._run_query = self._orig

    def test_weights_the_average_by_sessions(self) -> None:
        payload = ms.fetch_session_duration(start_date=START, end_date=END)
        sql = self._sql[0]
        self.assertIn("SUM(average_session_duration * sessions)", sql)
        self.assertIn("NULLIF(SUM(sessions), 0)", sql)
        self.assertIn("vw_ga4_landing_pages_daily", sql)
        self.assertEqual(payload["avg_session_duration_seconds"], 95.4)
        self.assertEqual(payload["sessions"], 1200)
        self.assertEqual(payload["date_range"]["start_date"], "2024-01-01")

    def test_unscoped_by_default(self) -> None:
        ms.fetch_session_duration(start_date=START, end_date=END)
        self.assertNotIn("CONTAINS_SUBSTR", self._sql[0])

    def test_scoped_on_landing_page(self) -> None:
        ms.fetch_session_duration(
            start_date=START, end_date=END, page_path_filter=["/careers"]
        )
        self.assertIn("CONTAINS_SUBSTR(landing_page, @pp0)", self._sql[0])

    def test_empty_result_reads_as_no_data(self) -> None:
        self._rows = []
        payload = ms.fetch_session_duration(start_date=START, end_date=END)
        self.assertIsNone(payload["avg_session_duration_seconds"])
        self.assertIsNone(payload["sessions"])


class RendererCardTests(unittest.TestCase):
    def test_card_renders_above_demographics(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )

        html = render_bigquery_dashboard_page(
            client_slug="demo", api_client_key="demo", label="Demo",
            use_session=True, session_email="t@e.com",
        )
        self.assertIn('id="sec-avgduration"', html)
        self.assertIn("/analytics/session-duration", html)
        self.assertIn("loadSessionDuration", html)
        # Ordering is the point of the request: the card sits above Demographics.
        self.assertLess(
            html.index('id="sec-avgduration"'), html.index('id="sec-demographics"')
        )
        # And below Audience, which it follows.
        self.assertLess(
            html.index('id="sec-audience"'), html.index('id="sec-avgduration"')
        )


if __name__ == "__main__":
    unittest.main()
