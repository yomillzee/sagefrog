from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import marketing_service as ms

# Website Analytics carries a "Sessions & engagement" module between Audience
# and Demographics: four clickable cards (Total sessions, New users,
# Engagement rate, Avg session duration), each explaining itself on hover, and a
# weekly line chart for whichever one is selected. GA4 only reports averageSessionDuration next to a
# dimension, so that figure is rebuilt from the landing-page report: each
# page's average is weighted by its sessions, never averaged flat — and the
# range figure is re-weighted from the daily series the same way, so a quiet
# Sunday doesn't pull it around as hard as a busy Tuesday. Engagement rate
# isn't in that report either, so it's merged in from a second query, along with
# that query's own session count -- which is what the rate divides by.

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

    def _split_reports(self, landing, engagement):
        """Answer the landing-page query and the engagement query separately."""
        def _capture(sql, params=None, max_rows=None):
            self._sql.append(sql)
            return landing if "vw_ga4_landing_pages_daily" in sql else engagement

        ms._run_query = _capture

    def test_merges_engagement_rate_from_a_second_session_grained_query(self) -> None:
        # Engagement rate isn't in the landing-page report (no engagedSessions
        # next to landingPage), so it comes from a second query against the
        # same table Traffic acquisition uses, and gets merged in by date.
        self._split_reports(
            [
                {"date": "2024-01-01", "sessions": 900, "new_users": 100, "avg_session_duration_seconds": 90.0},
                {"date": "2024-01-02", "sessions": 300, "new_users": 50, "avg_session_duration_seconds": 150.0},
            ],
            [
                {"date": "2024-01-01", "engagement_base_sessions": 1000, "engaged_sessions": 450},
                {"date": "2024-01-02", "engagement_base_sessions": 400, "engaged_sessions": 300},
            ],
        )
        payload = ms.fetch_session_duration(start_date=START, end_date=END)
        self.assertEqual(payload["new_users"], 150)
        self.assertEqual(payload["daily"][0]["engaged_sessions"], 450)
        self.assertEqual(payload["daily"][1]["engaged_sessions"], 300)
        # 750 engaged over the 1,400 sessions that query counted -- NOT over the
        # 1,200 the landing-page report counted. The two reports count a scoped
        # session differently (started on a matching page vs viewed one), so
        # dividing across them can put the rate over 100%.
        self.assertEqual(payload["engagement_rate"], round(750 / 1400 * 100, 1))
        self.assertNotEqual(payload["engagement_rate"], round(750 / 1200 * 100, 1))

    def test_a_date_missing_from_the_engagement_query_reads_as_no_rate(self) -> None:
        # Nothing came back for the date, so there is no rate to quote for it --
        # the card shows a dash rather than claiming 0% engagement.
        self._split_reports(
            [{"date": "2024-01-01", "sessions": 900, "new_users": 100, "avg_session_duration_seconds": 90.0}],
            [],
        )
        payload = ms.fetch_session_duration(start_date=START, end_date=END)
        self.assertEqual(payload["daily"][0]["engaged_sessions"], 0)
        self.assertEqual(payload["daily"][0]["engagement_base_sessions"], 0)
        self.assertIsNone(payload["engagement_rate"])
        # The metrics that do come from the landing-page report are unaffected.
        self.assertEqual(payload["sessions"], 900)
        self.assertEqual(payload["avg_session_duration_seconds"], 90.0)


class RendererCardTests(unittest.TestCase):
    def test_card_leads_the_analytics_pane(self) -> None:
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
        # The over-time half: one line per metric, session-weighted, weekly.
        # No granularity chips on this card, because a single day's average
        # swings on a handful of visits.
        self.assertIn('id="avgDurTrendChart"', html)
        self.assertIn("lineChart('avgDurTrendChart'", html)
        self.assertNotIn("barChart('avgDurTrendChart'", html)
        self.assertIn("function avgDurWeekly", html)
        self.assertNotIn("avgDurGranChips", html)
        # The card row is clickable: Total sessions, New users and Engagement
        # rate now sit alongside Average session duration, all one module.
        self.assertIn("AVG_DUR_METRICS", html)
        self.assertIn("metric-card", html)
        self.assertIn("Total sessions", html)
        self.assertIn("New users", html)
        self.assertIn("Engagement rate", html)
        self.assertIn("Avg session duration", html)
        # Each card explains its own metric on hover, reusing the Site
        # Performance pane's tooltip bubble. There is no longer one shared line
        # under the heading, which could only ever describe the selected card.
        self.assertIn("function avgDurTip", html)
        self.assertIn('data-tip="${tip}"', html)
        self.assertIn("ps-tip ps-tip--wide", html)
        self.assertNotIn('id="avgDurNote"', html)
        # The ? is decoration: a real button there would nest inside the card's
        # own button, which is invalid and unreachable by keyboard.
        self.assertIn('<span class="ps-info" aria-hidden="true">?</span>', html)
        # Under a page-path scope each tooltip says which scope it is reading.
        self.assertIn("scopedTip", html)
        self.assertIn("pathFilterActive() ? (m.scopedTip || m.tip) : m.tip", html)
        self.assertNotIn("adnote", html)
        # Ordering is the point of the request: this card now opens the pane,
        # in place of the Sessions-over-time chart that used to lead it.
        self.assertNotIn('id="sec-sessions"', html)
        self.assertLess(
            html.index('id="sec-avgduration"'), html.index('id="card-pages"')
        )
        self.assertLess(
            html.index('id="sec-avgduration"'), html.index('id="sec-audience"')
        )


if __name__ == "__main__":
    unittest.main()
