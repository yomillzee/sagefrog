from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import linkedin_organic_service as organic
from dashboard.renderers import linkedin_organic_renderer as R
from linkedin_organic_report_service import LinkedInOrganicReport


def _report() -> LinkedInOrganicReport:
    r = LinkedInOrganicReport(configured=True, org_id="777")
    r.total_followers = 2467
    r.follower_gain = 27
    r.total_impressions = 3528
    r.total_likes = 169
    r.total_comments = 2
    r.total_page_views = 1132
    r.total_unique_visitors = 684
    r.post_count = 2
    r.follower_series = [
        {"metric_date": "2026-07-01", "total_follower_gain": 4, "organic_follower_gain": 4,
         "paid_follower_gain": 0, "total_followers": 0},
        {"metric_date": "2026-07-02", "total_follower_gain": 6, "organic_follower_gain": 5,
         "paid_follower_gain": 1, "total_followers": 2467},
    ]
    r.page_series = [
        {"metric_date": "2026-07-01", "page_views": 50, "unique_visitors": 30},
        {"metric_date": "2026-07-02", "page_views": 80, "unique_visitors": 45},
    ]
    r.top_posts = [
        {"post_id": "1", "title": "First <b>post</b>", "post_type": "image",
         "published_at": "2026-07-01", "impressions": 2000, "clicks": 10,
         "likes": 100, "comments": 1, "shares": 3, "engagement_rate": 0.11},
        {"post_id": "2", "title": "Second post", "post_type": "video",
         "published_at": "2026-07-02", "impressions": 1528, "clicks": 5,
         "likes": 69, "comments": 1, "shares": 2, "engagement_rate": 0.09},
    ]
    return r


class RendererTests(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate content generation from the DB-backed page shell.
        self._orig_shell = R.render_client_shell_page
        R.render_client_shell_page = lambda **kw: kw["content_html"]  # type: ignore

    def tearDown(self) -> None:
        R.render_client_shell_page = self._orig_shell  # type: ignore

    def _html(self) -> str:
        return R.render_linkedin_organic(
            client_slug="nixon", label="Nixon Medical", report=_report(),
            use_session=True,
        )

    def test_uses_chartjs_not_svg(self) -> None:
        html = self._html()
        self.assertIn("/static/vendor/chart.umd.min.js", html)
        self.assertIn('id="loFollowerChart"', html)
        self.assertIn('id="loPageChart"', html)
        self.assertIn("new Chart(", html)
        # The old inline-SVG bar chart is gone.
        self.assertNotIn("<rect", html)

    def test_badge_removed(self) -> None:
        html = self._html()
        self.assertNotIn("lo-tag", html)

    def test_sortable_posts_table(self) -> None:
        html = self._html()
        self.assertIn('id="loPostsTable"', html)
        self.assertIn('data-sort="num"', html)
        self.assertIn('data-sort="text"', html)
        # Cells carry raw values for numeric sorting.
        self.assertIn('data-val="2000"', html)
        self.assertIn("initSort", html)

    def test_escapes_post_title(self) -> None:
        html = self._html()
        self.assertNotIn("<b>post</b>", html)
        self.assertIn("&lt;b&gt;post&lt;/b&gt;", html)

    def test_post_column_resizable(self) -> None:
        html = self._html()
        # A drag handle rides the Post header and the column width is a CSS var
        # the resizer JS updates.
        self.assertIn("lo-col-resizer", html)
        self.assertIn("initResize", html)
        self.assertIn("--lo-post-w", html)

    def test_post_title_hover_shows_full_text(self) -> None:
        html = self._html()
        # The truncated post title carries a title= tooltip with the full text.
        self.assertIn('class="lo-post-title" title=', html)


class NewPanelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_shell = R.render_client_shell_page
        R.render_client_shell_page = lambda **kw: kw["content_html"]  # type: ignore

    def tearDown(self) -> None:
        R.render_client_shell_page = self._orig_shell  # type: ignore

    def _report_with_extras(self) -> LinkedInOrganicReport:
        r = _report()
        r.total_unique_impressions = 2600
        r.top_posts[0]["unique_impressions"] = 1500
        r.top_posts[0]["reactions"] = {"LIKE": 90, "PRAISE": 10}
        r.follower_demographics = {
            "seniority": [
                {"category": "Senior", "total_followers": 700, "organic_followers": 690, "paid_followers": 10},
                {"category": "Manager", "total_followers": 300, "organic_followers": 300, "paid_followers": 0},
            ],
            "industry": [
                {"category": "Hospitals & Health Care", "total_followers": 800,
                 "organic_followers": 800, "paid_followers": 0},
            ],
        }
        r.engagement_series = [
            {"metric_date": "2026-07-01", "impressions": 400, "unique_impressions": 320,
             "clicks": 8, "likes": 15, "comments": 2, "shares": 3, "engagement_rate": 0.07},
            {"metric_date": "2026-07-02", "impressions": 520, "unique_impressions": 410,
             "clicks": 9, "likes": 18, "comments": 1, "shares": 2, "engagement_rate": 0.06},
        ]
        r.page_desktop_views = 700
        r.page_mobile_views = 432
        r.page_sections = [{"label": "Careers", "views": 210}, {"label": "Jobs", "views": 120}]
        return r

    def _html(self) -> str:
        return R.render_linkedin_organic(
            client_slug="nixon", label="Nixon Medical",
            report=self._report_with_extras(), use_session=True,
        )

    def test_reach_tile_and_column(self) -> None:
        html = self._html()
        self.assertIn(">Reach<", html)
        self.assertIn("2,600", html)  # reach KPI tile
        self.assertIn("1,500", html)  # per-post reach cell

    def test_demographics_panels(self) -> None:
        html = self._html()
        self.assertIn("Follower demographics", html)
        self.assertIn("By seniority", html)
        self.assertIn("Hospitals &amp; Health Care", html)

    def test_engagement_chart_present(self) -> None:
        html = self._html()
        self.assertIn('id="loEngagementChart"', html)
        self.assertIn("drawLine", html)

    def test_visitor_split(self) -> None:
        html = self._html()
        self.assertIn("Page visitors", html)
        self.assertIn("Desktop vs. mobile", html)
        self.assertIn("Careers", html)

    def test_device_split_is_pie(self) -> None:
        html = self._html()
        # Desktop vs. mobile now renders as a Chart.js doughnut, not a bar.
        self.assertIn('id="loDeviceChart"', html)
        self.assertIn("drawPie", html)
        self.assertIn("doughnut", html)
        self.assertNotIn("lo-split-seg", html)

    def test_reaction_breakdown_tooltip(self) -> None:
        html = self._html()
        # Reaction split surfaces as a hover title on the reactions cell.
        self.assertIn("Celebrate 10", html)

    def test_extras_absent_when_no_data(self) -> None:
        # A bare report (no demographics/engagement/splits) omits the new panels.
        html = R.render_linkedin_organic(
            client_slug="nixon", label="Nixon Medical", report=_report(),
            use_session=True,
        )
        self.assertNotIn("Follower demographics", html)
        self.assertNotIn("Page visitors", html)
        self.assertNotIn('id="loEngagementChart"', html)


class DateRangeFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_shell = R.render_client_shell_page
        R.render_client_shell_page = lambda **kw: kw["content_html"]  # type: ignore

    def tearDown(self) -> None:
        R.render_client_shell_page = self._orig_shell  # type: ignore

    def _html(self, range_days) -> str:
        return R.render_linkedin_organic(
            client_slug="nixon", label="Nixon Medical", report=_report(),
            range_days=range_days, use_session=True,
        )

    def test_picker_present_with_all_presets(self) -> None:
        html = self._html(90)
        self.assertIn('id="loRange"', html)
        self.assertIn("Date range", html)
        for value, _days, label in R._RANGE_PRESETS:
            self.assertIn(f'value="{value}"', html)
            self.assertIn(label, html)

    def test_reload_wired_via_script_not_inline_onchange(self) -> None:
        # The select changes the page via an addEventListener block (like the
        # page's other controls), not an inline onchange attribute that some
        # environments won't execute.
        html = self._html(90)
        self.assertNotIn("onchange", html)
        self.assertIn("getElementById('loRange')", html)
        self.assertIn("addEventListener('change'", html)
        self.assertIn("searchParams.set('range'", html)

    def test_picker_lives_in_sticky_filter_bar(self) -> None:
        # The date range sits in a sticky top filter bar (Overview .date-bar
        # look), with the page title, and the old .lo-head layout is gone.
        html = self._html(90)
        self.assertIn('class="lo-bar"', html)
        self.assertIn('<h1>LinkedIn Organic</h1>', html)
        self.assertNotIn('class="lo-head"', html)
        # The range control is nested inside the bar.
        bar = html.split('class="lo-bar"', 1)[1].split("</div></div></div>", 1)[0]
        self.assertIn('id="loRange"', bar)

    def test_selected_option_matches_range(self) -> None:
        self.assertIn('<option value="30" selected>', self._html(30))
        self.assertIn('<option value="365" selected>', self._html(365))

    def test_window_labels_track_selected_range(self) -> None:
        # The "last N days" status text reflects the chosen window, not a constant.
        self.assertIn("last 7 days", self._html(7))
        self.assertIn("in 7 days", self._html(7))  # follower delta badge
        self.assertNotIn("last 90 days", self._html(7))

    def test_default_and_bogus_fall_back_to_90(self) -> None:
        for bad in ("bogus", 999, None):
            self.assertIn('<option value="90" selected>', self._html(bad))

    def test_sanitize_range_days(self) -> None:
        self.assertEqual(R.sanitize_range_days("30"), 30)
        self.assertEqual(R.sanitize_range_days(180), 180)
        self.assertEqual(R.sanitize_range_days("999"), 90)
        self.assertEqual(R.sanitize_range_days(None), 90)


class FollowerTotalFallbackTests(unittest.TestCase):
    def test_sums_largest_segment_dimension(self) -> None:
        payload = {"elements": [{
            "followerCountsByAssociationType": [
                {"followerCounts": {"organicFollowerCount": 2000, "paidFollowerCount": 100}},
                {"followerCounts": {"organicFollowerCount": 300, "paidFollowerCount": 67}},
            ],
            "followerCountsBySeniority": [
                {"followerCounts": {"organicFollowerCount": 500, "paidFollowerCount": 0}},
            ],
        }]}
        organic._linkedin_get_with_versions = lambda path, **kw: payload  # type: ignore
        total = organic._lifetime_followers_from_stats(
            "777", access_token="TOK", env=None  # type: ignore
        )
        self.assertEqual(total, 2467)

    def test_total_followers_prefers_networksizes(self) -> None:
        organic._linkedin_get_with_versions = lambda path, **kw: (  # type: ignore
            {"firstDegreeSize": 2467} if "networkSizes" in path else {"elements": []}
        )
        self.assertEqual(
            organic._total_followers("777", access_token="TOK", env=None),  # type: ignore
            2467,
        )

    def test_total_followers_falls_back_when_networksizes_zero(self) -> None:
        payload = {"elements": [{
            "followerCountsByAssociationType": [
                {"followerCounts": {"organicFollowerCount": 2467, "paidFollowerCount": 0}},
            ],
        }]}

        def _get(path, **kw):
            return {"firstDegreeSize": 0} if "networkSizes" in path else payload

        organic._linkedin_get_with_versions = _get  # type: ignore
        self.assertEqual(
            organic._total_followers("777", access_token="TOK", env=None),  # type: ignore
            2467,
        )


class PostVolumeAndStandoutsTest(unittest.TestCase):
    """What the engagement chart plots for a window's posts."""

    ENGAGEMENT = [
        {"metric_date": "2026-06-%02d" % d, "impressions": 10 * d, "unique_impressions": d}
        for d in range(1, 21)
    ]

    @staticmethod
    def _posts(spec):
        return [{"published_at": day, "title": title, "post_type": "TEXT",
                 "impressions": impressions, "likes": 3, "comments": 1,
                 "shares": 2, "clicks": 4, "engagement_rate": 0.0125}
                for day, title, impressions in spec]

    def test_volume_counts_posts_per_column(self) -> None:
        marks = R._post_markers(
            self._posts([("2026-06-02", "a", 10), ("2026-06-02", "b", 10),
                         ("2026-06-05", "c", 10)]),
            self.ENGAGEMENT,
        )
        self.assertEqual(marks["volume"][1], 2)
        self.assertEqual(marks["volume"][4], 1)
        # Days without a post stay null so Chart.js draws no bar at all.
        self.assertIsNone(marks["volume"][0])
        self.assertEqual(marks["posts"], 3)
        self.assertEqual(marks["postDays"], 2)

    def test_a_daily_poster_gets_bars_not_a_mark_per_day(self) -> None:
        """The regression that prompted this: 3-6 posts a day filled the plot."""
        posts = []
        for d in range(1, 21):
            for k in range(4):
                posts.append({"published_at": "2026-06-%02d" % d, "title": f"p{d}-{k}",
                              "post_type": "", "impressions": 500})
        marks = R._post_markers(posts, self.ENGAGEMENT)
        self.assertEqual(marks["volume"], [4] * 20)
        # Uniform performance means nothing is worth calling out by name.
        self.assertEqual(marks["standouts"], [])

    def test_standouts_are_the_posts_that_beat_the_field(self) -> None:
        posts = [("2026-06-%02d" % d, "ordinary %d" % d, 500) for d in range(1, 13)]
        posts.append(("2026-06-04", "the hit", 9000))
        marks = R._post_markers(self._posts(posts), self.ENGAGEMENT)
        self.assertEqual([s["copy"] for s in marks["standouts"]], ["the hit"])
        self.assertEqual(marks["standouts"][0]["i"], 3)

    def test_standouts_are_capped_and_ranked(self) -> None:
        posts = [("2026-06-%02d" % d, "p%d" % d, 100) for d in range(1, 13)]
        posts += [("2026-06-%02d" % d, "big%d" % d, 10000 + d) for d in range(13, 21)]
        marks = R._post_markers(self._posts(posts), self.ENGAGEMENT)
        self.assertEqual(len(marks["standouts"]), R._STANDOUT_CAP)
        impressions = [s["impressions"] for s in marks["standouts"]]
        self.assertEqual(impressions, sorted(impressions, reverse=True))

    def test_every_post_is_named_when_there_are_only_a_few(self) -> None:
        """With four posts in a window there is no field to stand out from."""
        marks = R._post_markers(
            self._posts([("2026-06-02", "a", 900), ("2026-06-09", "b", 1100),
                         ("2026-06-14", "c", 950)]),
            self.ENGAGEMENT,
        )
        self.assertEqual(len(marks["standouts"]), 3)

    def test_posts_outside_the_chart_window_are_dropped(self) -> None:
        marks = R._post_markers(
            self._posts([("2020-01-01", "ancient", 5000)]), self.ENGAGEMENT)
        self.assertEqual(marks["standouts"], [])
        self.assertEqual(marks["posts"], 0)

    def test_tooltip_titles_are_capped_but_the_count_is_not(self) -> None:
        posts = [("2026-06-03", "post %d" % i, 100)
                 for i in range(R._MARKER_TITLE_CAP + 4)]
        marks = R._post_markers(self._posts(posts), self.ENGAGEMENT)
        self.assertEqual(len(marks["byLabel"]["2026-06-03"]), R._MARKER_TITLE_CAP)
        self.assertEqual(marks["counts"]["2026-06-03"], R._MARKER_TITLE_CAP + 4)

    def test_a_named_post_carries_its_own_copy_and_numbers(self) -> None:
        """The pin is what a reader hovers, so it has to hold the whole answer."""
        posts = [("2026-06-%02d" % d, "ordinary %d" % d, 500) for d in range(1, 13)]
        posts.append(("2026-06-04", "the hit", 9000))
        pin = R._post_markers(self._posts(posts), self.ENGAGEMENT)["standouts"][0]
        self.assertEqual(pin["copy"], "the hit")
        self.assertEqual(pin["day"], "2026-06-04")
        self.assertEqual(pin["kind"], "TEXT")
        self.assertEqual(pin["likes"], 3)
        self.assertEqual(pin["comments"], 1)
        self.assertEqual(pin["clicks"], 4)
        # Stored as a fraction, shown as a percentage.
        self.assertEqual(pin["rate"], 1.25)

    def test_page_ships_the_pins_without_a_toggle_or_a_list(self) -> None:
        r = _report()
        r.engagement_series = list(self.ENGAGEMENT)
        r.post_markers = self._posts(
            [("2026-06-%02d" % d, "ordinary %d" % d, 500) for d in range(1, 13)]
            + [("2026-06-04", "Launch day", 9000)])
        html = R.render_linkedin_organic(
            client_slug="demo", label="Demo", report=r,
            use_session=True, session_email="t@e.com",
        )
        self.assertIn("Launch day", html)
        self.assertIn("13 posts", html)
        self.assertIn("Posts / day", html)
        # The pins speak for themselves now — no checkbox, no key above the chart.
        self.assertNotIn("loMarkerToggle", html)
        self.assertNotIn("lo-mk-list", html)

    def test_nothing_is_pinned_when_nothing_stands_out(self) -> None:
        r = _report()
        r.engagement_series = list(self.ENGAGEMENT)
        r.post_markers = self._posts(
            [("2026-06-%02d" % d, "ordinary %d" % d, 500) for d in range(1, 13)])
        html = R.render_linkedin_organic(
            client_slug="demo", label="Demo", report=r,
            use_session=True, session_email="t@e.com",
        )
        self.assertEqual(
            R._post_markers(r.post_markers, r.engagement_series)["standouts"], [])
        # The volume bars are still there — cadence is worth seeing regardless.
        self.assertIn("Posts / day", html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
