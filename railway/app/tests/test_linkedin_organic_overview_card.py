from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The BigQuery-mart dashboard's Overview home carries a server-rendered "LinkedIn
# Followers" card, fed by the linkedin_organic connector's follower series. It
# only appears once the connector is connected, and drops entirely when nothing
# has synced yet — mirroring the HubSpot MQL tracker card next to it.


def _sample_series() -> list[dict]:
    # 4 days of gains; the latest day carries the lifetime total snapshot.
    return [
        {"metric_date": "2026-06-01", "organic_follower_gain": 5,
         "paid_follower_gain": 1, "total_follower_gain": 6, "total_followers": 0},
        {"metric_date": "2026-06-02", "organic_follower_gain": 3,
         "paid_follower_gain": 0, "total_follower_gain": 3, "total_followers": 0},
        {"metric_date": "2026-06-03", "organic_follower_gain": 4,
         "paid_follower_gain": 2, "total_follower_gain": 6, "total_followers": 0},
        {"metric_date": "2026-06-04", "organic_follower_gain": 1,
         "paid_follower_gain": 0, "total_follower_gain": 1, "total_followers": 1200},
    ]


def _report(**over):
    base = dict(
        configured=True,
        total_followers=1200,
        follower_gain=16,
        follower_series=_sample_series(),
    )
    base.update(over)
    return types.SimpleNamespace(**base)


class SparklineTests(unittest.TestCase):
    def test_needs_two_points(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            _li_follower_sparkline,
        )
        self.assertEqual(_li_follower_sparkline([]), "")
        self.assertEqual(
            _li_follower_sparkline([{"metric_date": "2026-06-01",
                                     "total_follower_gain": 3}]),
            "",
        )

    def test_renders_svg_polyline(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            _li_follower_sparkline,
        )
        svg = _li_follower_sparkline(_sample_series())
        self.assertIn("<svg", svg)
        self.assertIn("<polyline", svg)
        self.assertIn('aria-label="Follower growth"', svg)

    def test_unsorted_input_is_ordered(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            _li_follower_sparkline,
        )
        rows = list(reversed(_sample_series()))
        self.assertEqual(
            _li_follower_sparkline(rows), _li_follower_sparkline(_sample_series())
        )


class SectionHtmlTests(unittest.TestCase):
    def test_absent_when_unconfigured(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        self.assertEqual(linkedin_followers_section_html(None, "/x"), "")
        self.assertEqual(
            linkedin_followers_section_html(
                types.SimpleNamespace(configured=False), "/x"
            ),
            "",
        )

    def test_absent_when_nothing_synced(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        empty = _report(total_followers=0, follower_gain=0, follower_series=[])
        self.assertEqual(linkedin_followers_section_html(empty, "/x"), "")

    def test_renders_cards_and_split(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        html = linkedin_followers_section_html(_report(), "/dash/linkedin-organic")
        self.assertIn("LinkedIn Followers", html)
        self.assertIn("Total followers", html)
        self.assertIn("1,200", html)
        self.assertIn("Net new followers", html)
        # organic (13) / paid (3) split summed over the window.
        self.assertIn("13 organic · 3 paid", html)
        # Net gain delta over the window.
        self.assertIn("▲ 16 in 90 days", html)
        # "See more" links to the standalone page.
        self.assertIn('href="/dash/linkedin-organic"', html)

    def test_total_card_dropped_without_lifetime_total(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        # networkSizes can fail → no lifetime total, but daily gains still sync.
        rep = _report(
            total_followers=0,
            follower_series=[
                {"metric_date": "2026-06-01", "organic_follower_gain": 2,
                 "paid_follower_gain": 0, "total_follower_gain": 2,
                 "total_followers": 0},
                {"metric_date": "2026-06-02", "organic_follower_gain": 1,
                 "paid_follower_gain": 0, "total_follower_gain": 1,
                 "total_followers": 0},
            ],
            follower_gain=3,
        )
        html = linkedin_followers_section_html(rep, "/x")
        self.assertNotIn("Total followers", html)
        self.assertIn("Net new followers", html)

    def test_section_leaderboard_column(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        html = linkedin_followers_section_html(
            _report(
                total_page_views=1056,
                page_sections=[
                    {"label": "Overview", "views": 493},
                    {"label": "Careers", "views": 113},
                    {"label": "Jobs", "views": 113},
                    {"label": "People", "views": 337},
                ],
            ),
            "/x",
        )
        self.assertIn("Views by page section", html)
        # Ranked highest-first, not in the report's fixed section order.
        self.assertLess(html.index(">People<"), html.index(">Careers<"))
        # Share of all section views (337 / 1056 sums to the same total).
        self.assertIn("493<span>47%</span>", html)
        self.assertIn("337<span>32%</span>", html)
        # Top bar is full width; the rest scale against it.
        self.assertIn('style="width:100.0%"', html)
        self.assertIn("1,056 page views in 90 days", html)

    def test_section_column_capped_at_four(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        html = linkedin_followers_section_html(
            _report(page_sections=[
                {"label": f"Sec{i}", "views": 100 - i} for i in range(6)
            ]),
            "/x",
        )
        self.assertIn(">Sec3<", html)
        self.assertNotIn(">Sec4<", html)

    def test_section_column_dropped_without_split(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        # Page table synced before the per-section columns existed → no third
        # column, but the follower cards still render.
        html = linkedin_followers_section_html(_report(page_sections=[]), "/x")
        self.assertNotIn("Views by page section", html)
        self.assertIn("Total followers", html)
        # Zero-view sections don't earn a row either.
        empty = linkedin_followers_section_html(
            _report(page_sections=[{"label": "Jobs", "views": 0}]), "/x"
        )
        self.assertNotIn("Views by page section", empty)

    def test_negative_gain_shows_down_delta(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            linkedin_followers_section_html,
        )
        html = linkedin_followers_section_html(_report(follower_gain=-5), "/x")
        self.assertIn("▼ 5 in 90 days", html)


class OverviewIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        import client_dashboard_config as cdc
        import linkedin_organic_report_service as lors
        self._orig_list = ccs.list_configs
        self._orig_cfg = cdc.get_config
        self._orig_report = lors.build_report

    def tearDown(self) -> None:
        import connector_config_store as ccs
        import client_dashboard_config as cdc
        import linkedin_organic_report_service as lors
        ccs.list_configs = self._orig_list
        cdc.get_config = self._orig_cfg
        lors.build_report = self._orig_report

    def _render(self, *, organic_connected: bool) -> str:
        import connector_config_store as ccs
        import client_dashboard_config as cdc
        import linkedin_organic_report_service as lors

        configs = []
        if organic_connected:
            configs = [types.SimpleNamespace(
                connector_type="linkedin_organic", status="connected"
            )]
        ccs.list_configs = lambda slug: configs
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=None,
            card_layouts={},
        )
        lors.build_report = lambda slug, days=90: _report()

        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=False,
        )

    def test_card_present_when_connected(self) -> None:
        html = self._render(organic_connected=True)
        self.assertIn('data-ov-card="linkedin_organic"', html)
        self.assertIn("LinkedIn Followers", html)

    def test_card_absent_when_not_connected(self) -> None:
        html = self._render(organic_connected=False)
        self.assertNotIn('data-ov-card="linkedin_organic"', html)
        self.assertNotIn("LinkedIn Followers", html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
