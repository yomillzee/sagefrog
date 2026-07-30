"""Unit tests for linkedin_organic_service parsing + fetch shaping.

Exercises the pure transforms (URN/date helpers, stats parsing, window
filtering) against recorded-shape payloads by patching the transport helpers, so
no network or LinkedIn SDK is required.
"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# linkedin_service (imported by linkedin_organic_service) imports httpx at module
# load; stub it since it isn't installed in CI.
if "httpx" not in sys.modules:
    sys.modules["httpx"] = types.ModuleType("httpx")

import linkedin_organic_service as organic  # noqa: E402


class OrganicHelpersTests(unittest.TestCase):
    def test_org_urn_and_ids(self) -> None:
        self.assertEqual(organic._org_urn("123"), "urn:li:organization:123")
        self.assertEqual(organic._org_urn("urn:li:organization:123"), "urn:li:organization:123")
        self.assertEqual(organic._urn_id("urn:li:share:99"), "99")
        self.assertEqual(organic._urn_type("urn:li:ugcPost:5"), "ugcPost")
        self.assertEqual(organic._urn_type("urn:li:share:5"), "share")

    def test_epoch_roundtrip(self) -> None:
        d = date(2026, 3, 15)
        self.assertEqual(organic._date_from_epoch_ms(organic._epoch_ms(d)), d)
        self.assertIsNone(organic._date_from_epoch_ms(None))
        self.assertIsNone(organic._date_from_epoch_ms(0))

    def test_parse_share_stats_uses_reported_engagement(self) -> None:
        out = organic._parse_share_stats(
            {
                "impressionCount": 1000,
                "clickCount": 20,
                "likeCount": 30,
                "commentCount": 5,
                "shareCount": 3,
                "engagement": 0.058,
            }
        )
        self.assertEqual(out["impressions"], 1000)
        self.assertEqual(out["likes"], 30)
        self.assertAlmostEqual(out["engagement_rate"], 0.058)

    def test_parse_share_stats_computes_engagement_when_absent(self) -> None:
        out = organic._parse_share_stats(
            {"impressionCount": 100, "clickCount": 2, "likeCount": 5, "commentCount": 2, "shareCount": 1}
        )
        # (5 + 2 + 1 + 2) / 100
        self.assertAlmostEqual(out["engagement_rate"], 0.10)

    def test_sum_page_views_across_sections(self) -> None:
        views = {
            "allPageViews": {"pageViews": 40, "uniquePageViews": 30},
            "aboutPageViews": {"pageViews": 10, "uniquePageViews": 8},
        }
        self.assertEqual(organic._sum_page_views(views), (50, 38))


class OrganicFetchTests(unittest.TestCase):
    # These tests patch module-level transport/helper functions on ``organic``.
    # Snapshot and restore them so the suite stays order-independent (otherwise a
    # leaked ``_total_followers`` stub breaks the fallback tests elsewhere).
    _PATCHED = (
        "_linkedin_get", "_linkedin_get_with_versions", "_list_org_posts",
        "_share_stats_by_urn", "_total_followers", "_reactions_by_urn",
    )

    def setUp(self) -> None:
        self._orig = {name: getattr(organic, name) for name in self._PATCHED}

    def tearDown(self) -> None:
        for name, fn in self._orig.items():
            setattr(organic, name, fn)

    def test_list_organizations(self) -> None:
        acls = {
            "elements": [
                {"role": "ADMINISTRATOR", "organizationalTarget": "urn:li:organization:777"},
                {"role": "ADMINISTRATOR", "organizationalTarget": "urn:li:organization:777"},  # dup
            ]
        }
        organic._linkedin_get = lambda path, **kw: acls  # type: ignore
        organic._linkedin_get_with_versions = lambda path, **kw: {  # type: ignore
            "id": 777,
            "localizedName": "Acme Corp",
            "status": "OPERATING",
        }
        try:
            orgs = organic.list_organizations(access_token="TOK")
        finally:
            pass
        self.assertEqual(len(orgs), 1)
        self.assertEqual(orgs[0], {"id": "777", "name": "Acme Corp", "status": "OPERATING"})

    def test_list_organizations_raises_on_acl_failure(self) -> None:
        # A hard API failure (missing scope / revoked token) must surface, not be
        # swallowed into an empty list that the wizard shows as "No accounts found".
        def _boom(path, **kw):
            raise RuntimeError("LinkedIn API error 403 on /organizationAcls: ACCESS_DENIED")

        organic._linkedin_get = _boom  # type: ignore
        with self.assertRaises(RuntimeError) as ctx:
            organic.list_organizations(access_token="TOK")
        self.assertIn("organization lookup failed", str(ctx.exception))

    def test_list_organizations_empty_when_no_pages(self) -> None:
        # An authenticated member who administers no pages is a legitimate empty,
        # not an error.
        organic._linkedin_get = lambda path, **kw: {"elements": []}  # type: ignore
        self.assertEqual(organic.list_organizations(access_token="TOK"), [])

    def test_list_organizations_lists_non_admin_roles(self) -> None:
        # Discovery must not filter to role=ADMINISTRATOR: a Content Admin / Analyst
        # grant should still surface the page. The query carries no role facet.
        seen_paths: list[str] = []

        def _get(path, **kw):
            seen_paths.append(path)
            return {"elements": [
                {"role": "ANALYST", "state": "APPROVED",
                 "organizationalTarget": "urn:li:organization:999"},
            ]}

        organic._linkedin_get = _get  # type: ignore
        organic._linkedin_get_with_versions = lambda path, **kw: {  # type: ignore
            "localizedName": "Apex Co", "status": "OPERATING",
        }
        orgs = organic.list_organizations(access_token="TOK")
        self.assertEqual([o["id"] for o in orgs], ["999"])
        self.assertTrue(all("role=" not in p for p in seen_paths))

    def test_fetch_posts_with_stats_filters_window_and_joins_stats(self) -> None:
        in_window = date(2026, 6, 10)
        out_window = date(2026, 1, 1)
        posts = [
            {"id": "urn:li:share:1", "commentary": "In window", "createdAt": organic._epoch_ms(in_window)},
            {"id": "urn:li:ugcPost:2", "commentary": "Out of window", "createdAt": organic._epoch_ms(out_window)},
        ]
        organic._list_org_posts = lambda org_id, **kw: posts  # type: ignore
        organic._share_stats_by_urn = lambda org_id, urns, **kw: {  # type: ignore
            "urn:li:share:1": {"impressionCount": 500, "likeCount": 12, "clickCount": 4, "commentCount": 1, "shareCount": 2},
        }
        rows = organic.fetch_posts_with_stats(
            "777", start=date(2026, 6, 1), end=date(2026, 6, 30), access_token="TOK"
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["post_id"], "1")
        self.assertEqual(row["impressions"], 500)
        self.assertEqual(row["likes"], 12)
        self.assertEqual(row["published_at"], in_window.isoformat())
        self.assertEqual(row["org_id"], "777")

    def test_fetch_follower_daily_parses_gains_and_total(self) -> None:
        day = date(2026, 6, 5)
        payload = {
            "elements": [
                {
                    "timeRange": {"start": organic._epoch_ms(day)},
                    "followerGains": {"organicFollowerGain": 7, "paidFollowerGain": 3},
                }
            ]
        }
        organic._linkedin_get_with_versions = lambda path, **kw: payload  # type: ignore
        organic._total_followers = lambda org_id, **kw: 12345  # type: ignore
        rows = organic.fetch_follower_daily(
            "777", start=date(2026, 6, 1), end=date(2026, 6, 30), access_token="TOK"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["organic_follower_gain"], 7)
        self.assertEqual(rows[0]["paid_follower_gain"], 3)
        self.assertEqual(rows[0]["total_follower_gain"], 10)
        self.assertEqual(rows[0]["total_followers"], 12345)

    def test_fetch_page_daily_sums_views(self) -> None:
        day = date(2026, 6, 5)
        payload = {
            "elements": [
                {
                    "timeRange": {"start": organic._epoch_ms(day)},
                    "totalPageStatistics": {
                        "views": {
                            "allPageViews": {"pageViews": 40, "uniquePageViews": 30},
                            "jobsPageViews": {"pageViews": 10, "uniquePageViews": 5},
                        }
                    },
                }
            ]
        }
        organic._linkedin_get_with_versions = lambda path, **kw: payload  # type: ignore
        rows = organic.fetch_page_daily(
            "777", start=date(2026, 6, 1), end=date(2026, 6, 30), access_token="TOK"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["page_views"], 50)
        self.assertEqual(rows[0]["unique_visitors"], 35)

    def test_parse_share_stats_includes_reach(self) -> None:
        out = organic._parse_share_stats(
            {"impressionCount": 100, "uniqueImpressionsCount": 80, "likeCount": 5}
        )
        self.assertEqual(out["impressions"], 100)
        self.assertEqual(out["unique_impressions"], 80)

    def test_fetch_page_daily_breakdown_columns(self) -> None:
        day = date(2026, 6, 5)
        payload = {
            "elements": [
                {
                    "timeRange": {"start": organic._epoch_ms(day)},
                    "totalPageStatistics": {
                        "views": {
                            "allDesktopPageViews": {"pageViews": 30, "uniquePageViews": 20},
                            "allMobilePageViews": {"pageViews": 20, "uniquePageViews": 15},
                            "careersPageViews": {"pageViews": 12, "uniquePageViews": 9},
                        }
                    },
                }
            ]
        }
        organic._linkedin_get_with_versions = lambda path, **kw: payload  # type: ignore
        rows = organic.fetch_page_daily(
            "777", start=date(2026, 6, 1), end=date(2026, 6, 30), access_token="TOK"
        )
        self.assertEqual(rows[0]["desktop_page_views"], 30)
        self.assertEqual(rows[0]["mobile_page_views"], 20)
        self.assertEqual(rows[0]["careers_page_views"], 12)
        self.assertEqual(rows[0]["overview_page_views"], 0)

    def test_humanize_staff_range(self) -> None:
        self.assertEqual(organic._humanize_staff_range("SIZE_51_TO_200"), "51-200")
        self.assertEqual(organic._humanize_staff_range("SIZE_10001_OR_MORE"), "10,001+")

    def test_fetch_follower_demographics_parses_and_labels(self) -> None:
        payload = {"elements": [{
            "followerCountsBySeniority": [
                {"seniority": "urn:li:seniority:4",
                 "followerCounts": {"organicFollowerCount": 6, "paidFollowerCount": 1}},
                {"seniority": "urn:li:seniority:3",
                 "followerCounts": {"organicFollowerCount": 2, "paidFollowerCount": 0}},
            ],
            "followerCountsByStaffCountRange": [
                {"staffCountRange": "SIZE_51_TO_200",
                 "followerCounts": {"organicFollowerCount": 4, "paidFollowerCount": 0}},
            ],
        }]}
        organic._linkedin_get_with_versions = lambda path, **kw: payload  # type: ignore
        rows = organic.fetch_follower_demographics("777", access_token="TOK")
        seniority = [r for r in rows if r["dimension"] == "seniority"]
        # Sorted by total desc: Senior (7) before Entry (2).
        self.assertEqual([r["category"] for r in seniority], ["Senior", "Entry"])
        self.assertEqual(seniority[0]["total_followers"], 7)
        company = [r for r in rows if r["dimension"] == "company_size"]
        self.assertEqual(company[0]["category"], "51-200")

    def test_fetch_engagement_daily_parses_totals(self) -> None:
        day = date(2026, 6, 7)
        payload = {"elements": [{
            "timeRange": {"start": organic._epoch_ms(day)},
            "totalShareStatistics": {
                "impressionCount": 400, "uniqueImpressionsCount": 320,
                "clickCount": 8, "likeCount": 15, "commentCount": 2,
                "shareCount": 3, "engagement": 0.07,
            },
        }]}
        organic._linkedin_get_with_versions = lambda path, **kw: payload  # type: ignore
        rows = organic.fetch_engagement_daily(
            "777", start=date(2026, 6, 1), end=date(2026, 6, 30), access_token="TOK"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["impressions"], 400)
        self.assertEqual(rows[0]["unique_impressions"], 320)
        self.assertAlmostEqual(rows[0]["engagement_rate"], 0.07)

    def test_reference_label_uses_correct_endpoint(self) -> None:
        seen = {}

        def _get(path, **kw):
            seen["path"] = path
            return {"name": {"localized": {"en_US": "Hospitals & Health Care"}}}

        organic._linkedin_get_with_versions = _get  # type: ignore
        label = organic._resolve_reference_label(
            "industry", "2063", access_token="TOK", env=None, cache={}  # type: ignore
        )
        self.assertEqual(seen["path"], "/industries/2063")  # not "/industrys/"
        self.assertEqual(label, "Hospitals & Health Care")

    def test_reference_label_falls_back_readably(self) -> None:
        def _boom(path, **kw):
            raise RuntimeError("404")

        organic._linkedin_get_with_versions = _boom  # type: ignore
        label = organic._resolve_reference_label(
            "geo", "90000077", access_token="TOK", env=None, cache={}  # type: ignore
        )
        self.assertEqual(label, "Region 90000077")

    def test_reactions_by_urn_parses_summaries(self) -> None:
        organic._linkedin_get_with_versions = lambda path, **kw: {  # type: ignore
            "reactionSummaries": {
                "LIKE": {"count": 10, "reactionType": "LIKE"},
                "PRAISE": {"count": 3},
                "EMPATHY": {"count": 0},
            }
        }
        out = organic._reactions_by_urn(
            ["urn:li:share:1"], access_token="TOK", env=None  # type: ignore
        )
        self.assertEqual(out["urn:li:share:1"], {"LIKE": 10, "PRAISE": 3})


if __name__ == "__main__":
    unittest.main()
