from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import marketing_service as ms

# Website Analytics carries an "Average session duration" card and a bar per week
# between Audience and Demographics. GA4 only reports averageSessionDuration next
# to a dimension, so the figure is rebuilt from the landing-page report: each
# page's average is weighted by its sessions, never averaged flat — and the range
# figure is re-weighted from the daily series the same way, so a quiet Sunday
# doesn't pull it around as hard as a busy Tuesday.

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 31)


class FetchSessionDurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = ms._run_query
        self._sql: list[str] = []
        # Two days, deliberately lopsided: a flat mean of the averages reads
        # 120s, the session-weighted one 105s.
        self._rows: list[dict] = [
            {"date": "2024-01-01", "sessions": 900, "avg_session_duration_seconds": 90.0},
            {"date": "2024-01-02", "sessions": 300, "avg_session_duration_seconds": 150.0},
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
        # (900*90 + 300*150) / 1200 = 105.0 — not the 120.0 a flat mean gives.
        self.assertEqual(payload["avg_session_duration_seconds"], 105.0)
        self.assertEqual(payload["sessions"], 1200)
        self.assertEqual(payload["date_range"]["start_date"], "2024-01-01")

    def test_returns_a_day_per_row_for_the_chart(self) -> None:
        payload = ms.fetch_session_duration(start_date=START, end_date=END)
        self.assertIn("GROUP BY date", self._sql[0])
        self.assertIn("ORDER BY date ASC", self._sql[0])
        self.assertEqual(
            [r["date"] for r in payload["daily"]], ["2024-01-01", "2024-01-02"]
        )
        self.assertEqual(payload["daily"][1]["avg_session_duration_seconds"], 150.0)

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
        self.assertEqual(payload["daily"], [])

    def test_a_day_with_no_sessions_does_not_divide_by_zero(self) -> None:
        self._rows = [
            {"date": "2024-01-01", "sessions": 0, "avg_session_duration_seconds": None},
            {"date": "2024-01-02", "sessions": 100, "avg_session_duration_seconds": 60.0},
        ]
        payload = ms.fetch_session_duration(start_date=START, end_date=END)
        self.assertEqual(payload["avg_session_duration_seconds"], 60.0)
        self.assertEqual(payload["sessions"], 100)


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
        # The over-time half: one bar per week, session-weighted. Weekly only --
        # no granularity chips on this card, unlike Sessions over time, because a
        # single day's average swings on a handful of visits.
        self.assertIn('id="avgDurTrendChart"', html)
        self.assertIn("function barChart", html)
        self.assertIn("function avgDurWeekly", html)
        self.assertNotIn("avgDurGranChips", html)
        # Sessions over time keeps its own Daily/Weekly chips.
        self.assertIn('id="sessionsGranChips"', html)
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
