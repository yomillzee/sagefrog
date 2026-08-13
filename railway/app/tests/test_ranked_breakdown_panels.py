from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import demo_data  # noqa: E402
import marketing_service as ms  # noqa: E402

# The Website Analytics tab carries two admin-only tabbed breakdown panels:
# Channels / Sources / Campaigns, and Top pages / Entry pages / Bounce rate.
# Acquisition has its own endpoint because each dimension needs its own
# GROUP BY -- the Traffic table's source rows are source x medium, a grain that
# cannot be rolled up to source without losing the tail behind its LIMIT.

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 31)


class AcquisitionBreakdownSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = ms._run_query
        self._sql: list[str] = []

        def _capture(sql, params=None, max_rows=None):
            self._sql.append(sql)
            return []

        ms._run_query = _capture
        self._ctx = ms.route(client_key="acme", project_id="p", mart_dataset_id="d")
        self._ctx.__enter__()

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)
        ms._run_query = self._orig

    def test_groups_each_dimension_separately(self) -> None:
        ms.fetch_acquisition_breakdown(start_date=START, end_date=END)
        self.assertEqual(len(self._sql), 3)
        channel, source, campaign = self._sql
        self.assertIn("TRIM(default_channel_group)", channel)
        self.assertIn("TRIM(source)", source)
        self.assertIn("TRIM(campaign)", campaign)
        # Every dimension collapses to a `label` column so one renderer serves all three.
        for sql in self._sql:
            self.assertIn("AS label", sql)
            self.assertIn("GROUP BY label", sql)
            self.assertIn("vw_ga4_traffic_acq_daily", sql)

    def test_source_is_not_grouped_with_medium(self) -> None:
        # The whole reason this is separate from fetch_traffic_acquisition.
        ms.fetch_acquisition_breakdown(start_date=START, end_date=END)
        self.assertNotIn("medium", self._sql[1])

    def test_campaign_drops_ga4_placeholder_labels(self) -> None:
        ms.fetch_acquisition_breakdown(start_date=START, end_date=END)
        campaign = self._sql[2]
        self.assertIn("HAVING label NOT IN", campaign)
        for placeholder in ("(not set)", "(direct)", "(none)"):
            self.assertIn(f"'{placeholder}'", campaign)
        # Only the campaign list filters -- a channel legitimately is "(other)".
        self.assertNotIn("HAVING", self._sql[0])
        self.assertNotIn("HAVING", self._sql[1])

    def test_pages_top_carries_bounce_rate(self) -> None:
        # GA4's Data API has no exits metric, so the third Pages tab ranks on
        # bounce rate -- 1 - engagement rate, GA4's own definition.
        ms.fetch_pages_top(start_date=START, end_date=END)
        sql = self._sql[0]
        self.assertIn("AS bounce_rate", sql)
        self.assertIn("SUM(engaged_sessions)", sql)


class AcquisitionBreakdownDemoTests(unittest.TestCase):
    """The demo client never touches BigQuery, so it needs its own sample."""

    PAYLOAD = {"start": "2024-01-01", "end": "2024-01-31"}

    def test_shape_matches_the_live_payload(self) -> None:
        out = demo_data.generate("pages.acquisition_breakdown", self.PAYLOAD)
        for key in ("by_channel", "by_source", "by_campaign"):
            self.assertTrue(out[key], f"{key} should not be empty")
            row = out[key][0]
            self.assertIn("label", row)
            self.assertIn("sessions", row)

    def test_rows_are_sorted_by_sessions_desc(self) -> None:
        out = demo_data.generate("pages.acquisition_breakdown", self.PAYLOAD)
        for key in ("by_channel", "by_source", "by_campaign"):
            sessions = [r["sessions"] for r in out[key]]
            self.assertEqual(sessions, sorted(sessions, reverse=True), key)

    def test_deterministic(self) -> None:
        self.assertEqual(
            demo_data.generate("pages.acquisition_breakdown", self.PAYLOAD),
            demo_data.generate("pages.acquisition_breakdown", self.PAYLOAD),
        )

    def test_pages_top_sample_carries_path_and_bounce_rate(self) -> None:
        row = demo_data.generate("pages.top", self.PAYLOAD)["rows"][0]
        self.assertTrue(row["page_path"].startswith("/"))
        self.assertGreaterEqual(row["bounce_rate"], 0)
        self.assertLessEqual(row["bounce_rate"], 100)


class RankedPanelGatingTests(unittest.TestCase):
    """Clients must not receive the preview markup at all -- not hidden, absent."""

    def setUp(self) -> None:
        # render_sidebar loads the per-client theme from Postgres. Other tests in
        # the suite patch web_users.enabled to True without a DATABASE_URL, which
        # sends that lookup at a database that isn't there -- so whether this
        # renders at all depends on test order. The gate under test is pure
        # markup, so stub the theme out.
        #
        # Patched through base_layout's own reference rather than by importing
        # dashboard_theme here: the suite ends up with two module objects for
        # these (the app dir is importable under more than one path), and only
        # the copy base_layout holds is the one that actually runs.
        from dashboard.renderers import base_layout

        self._theme_mod = base_layout.dashboard_theme
        self._orig_loader = self._theme_mod.load_client_theme
        self._theme_mod.load_client_theme = lambda slug: dict(self._theme_mod.DEFAULT_THEME)

    def tearDown(self) -> None:
        self._theme_mod.load_client_theme = self._orig_loader

    @staticmethod
    def _render(is_admin: bool) -> str:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )

        return render_bigquery_dashboard_page(
            client_slug="demo", api_client_key="demo", label="Demo",
            use_session=True, session_email="t@e.com", session_is_admin=is_admin,
        )

    def test_admin_gets_both_panels(self) -> None:
        html = self._render(True)
        self.assertIn('id="rankedPanels"', html)
        self.assertEqual(html.count('class="rk-panel"'), 2)
        # Three acquisition tabs + four page tabs.
        self.assertEqual(html.count('class="rk-tab"'), 7)

    def test_converting_tab_is_present_and_reads_key_events(self) -> None:
        html = self._render(True)
        self.assertIn('data-key="convert"', html)
        # It must recompute against the key-event selector rather than trusting
        # the server's key_events column, or it would contradict the Pages table.
        self.assertIn("applyPageEvents(pagesTopRows", html)
        # ...and re-render whenever that selection changes.
        self.assertIn("rkState.pg.page=1; renderRankedPages();", html)

    def test_client_gets_no_panel_markup(self) -> None:
        html = self._render(False)
        self.assertNotIn('id="rankedPanels"', html)
        self.assertNotIn('class="rk-panel"', html)
        self.assertNotIn('class="rk-tab"', html)


if __name__ == "__main__":
    unittest.main()
