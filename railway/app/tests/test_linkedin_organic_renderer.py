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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
