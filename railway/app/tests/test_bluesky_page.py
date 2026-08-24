"""Bluesky dashboard page: renderer output and sidebar/route wiring.

The report service's BigQuery reads aren't exercised here (they need a live
warehouse); what's covered is everything the page decides for itself — the
window comparison badges, the "no impressions" disclosure, escaping, and the
nav/route registration that makes the page reachable at all.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_dashboard_config  # noqa: E402
from bluesky_report_service import BlueskyReport  # noqa: E402
from dashboard.renderers import bluesky_renderer as R  # noqa: E402


def _report() -> BlueskyReport:
    r = BlueskyReport(configured=True, handle="sagefrog.bsky.social", display_name="Sage Frog")
    r.followers = 1234
    r.follows = 210
    r.lifetime_posts = 808
    r.follower_gain = 57
    r.post_count = 12
    r.total_likes = 340
    r.total_reposts = 55
    r.total_replies = 28
    r.total_quotes = 9
    r.total_engagements = 432
    r.prev_post_count = 10
    r.prev_total_likes = 300
    r.prev_total_reposts = 40
    r.prev_total_replies = 20
    r.prev_total_engagements = 365
    r.last_synced = "2026-08-24"
    r.follower_series = [
        {"metric_date": "2026-08-01", "followers": 1180},
        {"metric_date": "2026-08-02", "followers": 1200},
        {"metric_date": "2026-08-03", "followers": 1234},
    ]
    r.engagement_series = [
        {"metric_date": "2026-08-01", "posts": 2, "engagements": 88},
        {"metric_date": "2026-08-03", "posts": 1, "engagements": 190},
    ]
    r.top_posts = [
        {"post_uri": "at://x/app.bsky.feed.post/1",
         "url": "https://bsky.app/profile/sagefrog.bsky.social/post/1",
         "post_date": "2026-08-03", "text": "Rankings <b>are</b> up", "is_reply": False,
         "embed_type": "images", "likes": 150, "reposts": 22, "replies": 14,
         "quotes": 4, "engagements": 190},
    ]
    return r


def _render(report: BlueskyReport, **kw) -> str:
    return R.render_bluesky(
        client_slug="demo", label="Demo", report=report,
        use_session=True, session_email="t@e.com", **kw
    )


class RangeTests(unittest.TestCase):
    def test_presets_are_accepted(self) -> None:
        for value, days, _label in R._RANGE_PRESETS:
            self.assertEqual(R.sanitize_range_days(value), days)

    def test_unknown_range_falls_back(self) -> None:
        # A hand-typed or stale ?range= must not reach the report service.
        for raw in (None, "", "999", "; DROP TABLE", "thirty"):
            self.assertEqual(R.sanitize_range_days(raw), R._DEFAULT_RANGE_DAYS)

    def test_selected_option_reflects_the_window(self) -> None:
        html = _render(_report(), range_days=30)
        self.assertIn('<option value="30" selected>', html)


class UnconfiguredTests(unittest.TestCase):
    def test_shows_the_connector_prompt(self) -> None:
        html = _render(BlueskyReport(configured=False, error="Not configured for this client."))
        self.assertIn("Not configured for this client.", html)
        self.assertIn("Connect the Bluesky connector", html)
        # No charts or table are drawn for a client with nothing behind the page.
        self.assertNotIn("bsPostsTable", html)

    def test_configured_but_empty_says_run_a_sync(self) -> None:
        html = _render(BlueskyReport(configured=True))
        self.assertIn("No Bluesky data has synced yet", html)


class ContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = _render(_report(), range_days=30)

    def test_headline_numbers_render(self) -> None:
        self.assertIn("1,234", self.html)   # followers
        self.assertIn("432", self.html)     # engagements
        self.assertIn("▲ 57 in 30 days", self.html)

    def test_avg_per_post_is_engagements_not_a_rate(self) -> None:
        # 432 / 12 = 36.0. There is no impression denominator on Bluesky, so the
        # card must never render a percentage here.
        self.assertIn("Avg. per post", self.html)
        self.assertIn(">36.0<", self.html)

    def test_period_comparison_badges(self) -> None:
        # Posts 12 vs 10 = +20%; the prior figure rides along as a hover title.
        self.assertIn("20.0%", self.html)
        self.assertIn("in the previous 30 days", self.html)
        self.assertIn("vs previous 30 days", self.html)

    def test_no_comparison_claimed_without_a_prior_window(self) -> None:
        report = _report()
        for field in ("prev_post_count", "prev_total_likes", "prev_total_reposts",
                      "prev_total_replies", "prev_total_engagements"):
            setattr(report, field, 0)
        html = _render(report, range_days=30)
        self.assertNotIn("vs previous 30 days", html)
        self.assertNotIn("in the previous 30 days", html)

    def test_discloses_that_impressions_do_not_exist(self) -> None:
        self.assertIn("no impressions", self.html)
        self.assertIn("Last synced 2026-08-24", self.html)

    def test_post_links_out_to_bluesky(self) -> None:
        self.assertIn("https://bsky.app/profile/sagefrog.bsky.social/post/1", self.html)
        self.assertIn('<span class="bs-chip">Reply</span>', _render(
            _report_with_reply(), range_days=30))

    def test_post_text_is_escaped(self) -> None:
        self.assertIn("Rankings &lt;b&gt;are&lt;/b&gt; up", self.html)
        self.assertNotIn("Rankings <b>are</b> up", self.html)

    def test_charts_are_drawn_from_local_chartjs(self) -> None:
        self.assertIn("/static/vendor/chart.umd.min.js", self.html)
        self.assertIn("bsFollowerChart", self.html)
        self.assertIn("bsEngagementChart", self.html)

    def test_follower_sparkline_needs_two_points(self) -> None:
        # The class name also lives in the stylesheet, so assert on the element.
        self.assertIn('<svg class="bs-spark"', self.html)
        one_point = _report()
        one_point.follower_series = [{"metric_date": "2026-08-01", "followers": 1180}]
        self.assertNotIn('<svg class="bs-spark"', _render(one_point))


def _report_with_reply() -> BlueskyReport:
    r = _report()
    r.top_posts[0]["is_reply"] = True
    return r


class ExcerptTests(unittest.TestCase):
    def test_long_text_is_truncated_with_an_ellipsis(self) -> None:
        text = "word " * 100
        out = R._excerpt(text)
        self.assertLessEqual(len(out), R._TEXT_CAP)
        self.assertTrue(out.endswith("…"))

    def test_whitespace_is_collapsed(self) -> None:
        self.assertEqual(R._excerpt("a\n\n  b\tc"), "a b c")

    def test_empty_text_has_a_placeholder(self) -> None:
        # An image-only post has no text; the row still needs a label.
        self.assertEqual(R._excerpt("  "), "(no text)")


class WiringTests(unittest.TestCase):
    def test_page_url_helper(self) -> None:
        from dashboard.utils.urls import bluesky_page_url

        self.assertEqual(
            bluesky_page_url(client_slug="acme", access_key=None, use_session=True),
            "/dashboard/acme/bluesky",
        )
        self.assertEqual(
            bluesky_page_url(client_slug="acme", access_key="k e y", use_session=False),
            "/dashboard/acme/bluesky?key=k%20e%20y",
        )

    def test_route_is_registered(self) -> None:
        from dashboard.routes import connector_routes

        paths = {getattr(r, "path", "") for r in connector_routes.router.routes}
        self.assertIn("/dashboard/{client_slug}/bluesky", paths)

    def test_sidebar_item_appears_only_when_connected(self) -> None:
        from dashboard.renderers import base_layout

        real = base_layout.platform_nav_flags
        try:
            base_layout.platform_nav_flags = lambda slug: {"_connector_read_ok": True,
                                                           "show_bluesky": True}
            on = base_layout.dashboard_sidebar_view_nav_html(
                client_slug="demo", access_key=None, use_session=True, as_tabs=False)
            base_layout.platform_nav_flags = lambda slug: {"_connector_read_ok": True,
                                                           "show_bluesky": False}
            off = base_layout.dashboard_sidebar_view_nav_html(
                client_slug="demo", access_key=None, use_session=True, as_tabs=False)
        finally:
            base_layout.platform_nav_flags = real

        self.assertIn('data-tab="bluesky"', on)
        self.assertIn("/dashboard/demo/bluesky", on)
        self.assertNotIn('data-tab="bluesky"', off)

    def test_tab_is_toggleable_by_an_admin(self) -> None:
        # Both registries have to know the key, or the Advanced tab's hide
        # control and the sidebar disagree about what "bluesky" means.
        from dashboard.renderers.base_layout import _SIDEBAR_TAB_EDIT_ITEMS

        self.assertIn("bluesky", client_dashboard_config.SIDEBAR_TOGGLEABLE_TABS)
        self.assertIn("bluesky", {key for key, _label in _SIDEBAR_TAB_EDIT_ITEMS})

    def test_nav_flags_always_carry_the_key(self) -> None:
        # The failure fallback must be a COMPLETE flag set; a missing key would
        # drop the tab silently on a transient Postgres blip.
        from dashboard.renderers import base_layout

        import connector_config_store
        real = connector_config_store.list_configs
        try:
            def boom(slug):
                raise RuntimeError("db down")
            connector_config_store.list_configs = boom
            flags = base_layout.platform_nav_flags("demo")
        finally:
            connector_config_store.list_configs = real

        self.assertIn("show_bluesky", flags)
        self.assertFalse(flags["show_bluesky"])
        self.assertFalse(flags["_connector_read_ok"])


if __name__ == "__main__":
    unittest.main()
