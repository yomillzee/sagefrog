"""Google Business Profile client — the response shapes and the access gate.

The one that matters most in practice is the access gate: Google ships these
APIs with a quota of zero until it approves an access application, and every
call until then fails with **429**. Reading that as ordinary rate limiting means
retrying a quota that will never refill, so the distinction is tested here
rather than left to whoever next reads a stuck sync.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import google_business_service as gbs  # noqa: E402


def _resp(status: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=json_body if json_body is not None else None,
        text=None if json_body is not None else text,
        request=httpx.Request("GET", "https://example.test"),
    )


class AccessGateTests(unittest.TestCase):
    def test_zero_quota_429_is_recognised_as_unapproved_access(self) -> None:
        resp = _resp(429, {"error": {"message":
            "Quota exceeded for quota metric 'Requests' and limit 'Requests per minute' "
            "of service 'mybusinessaccountmanagement.googleapis.com'."}})
        self.assertTrue(gbs._is_access_not_approved(resp))

    def test_api_disabled_message_also_counts(self) -> None:
        resp = _resp(429, {"error": {"message":
            "My Business Account Management API has not been used in project 123 before "
            "or it is disabled."}})
        self.assertTrue(gbs._is_access_not_approved(resp))

    def test_a_non_429_is_never_an_access_problem(self) -> None:
        self.assertFalse(gbs._is_access_not_approved(_resp(500, {"error": {"message": "quota"}})))

    def test_get_raises_the_dedicated_error_with_actionable_text(self) -> None:
        resp = _resp(429, {"error": {"message": "Quota exceeded for quota metric"}})
        with patch.object(httpx.Client, "get", return_value=resp):
            with httpx.Client() as http:
                with self.assertRaises(gbs.GoogleBusinessAccessNotApproved) as ctx:
                    gbs._get(http, "https://example.test", "token")
        # The message has to name the fix, since the HTTP status suggests the
        # wrong one.
        self.assertIn("access request", str(ctx.exception))

    def test_403_is_a_permission_error_not_an_access_gate(self) -> None:
        with patch.object(httpx.Client, "get", return_value=_resp(403, text="Forbidden")):
            with httpx.Client() as http:
                with self.assertRaises(PermissionError):
                    gbs._get(http, "https://example.test", "token")


class DailyMetricsTests(unittest.TestCase):
    PAYLOAD = {
        "multiDailyMetricTimeSeries": [{
            "dailyMetricTimeSeries": [
                {
                    "dailyMetric": "CALL_CLICKS",
                    "timeSeries": {"datedValues": [
                        {"date": {"year": 2026, "month": 3, "day": 1}, "value": "7"},
                        # Google omits `value` entirely on a zero day.
                        {"date": {"year": 2026, "month": 3, "day": 2}},
                    ]},
                },
                {
                    "dailyMetric": "WEBSITE_CLICKS",
                    "timeSeries": {"datedValues": [
                        {"date": {"year": 2026, "month": 3, "day": 1}, "value": "12"},
                        {"date": {"year": 2026, "month": 3, "day": 2}, "value": "9"},
                    ]},
                },
            ],
        }],
    }

    def _fetch(self, payload):
        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", return_value=payload):
            return gbs.fetch_daily_metrics(
                "refresh", "locations/1",
                start=date(2026, 3, 1), end=date(2026, 3, 2),
            )

    def test_pivots_nested_series_into_one_row_per_date(self) -> None:
        rows = self._fetch(self.PAYLOAD)
        self.assertEqual([r["metric_date"] for r in rows], ["2026-03-01", "2026-03-02"])
        self.assertEqual(rows[0]["call_clicks"], 7)
        self.assertEqual(rows[0]["website_clicks"], 12)

    def test_int64_arrives_as_a_string(self) -> None:
        rows = self._fetch(self.PAYLOAD)
        self.assertIsInstance(rows[0]["call_clicks"], int)

    def test_omitted_value_is_a_real_zero(self) -> None:
        """An absent value means "nothing happened that day", not "unknown" —
        the location was live, it just had no calls."""
        rows = self._fetch(self.PAYLOAD)
        self.assertEqual(rows[1]["call_clicks"], 0)

    def test_every_metric_column_is_present_on_every_row(self) -> None:
        rows = self._fetch(self.PAYLOAD)
        for row in rows:
            for col in gbs.METRIC_COLUMNS:
                self.assertIn(col, row)
            self.assertEqual(row["bookings"], 0)

    def test_requests_every_metric_and_a_dated_range(self) -> None:
        seen: dict = {}

        def _capture(http, url, token, params=None):
            seen["url"] = url
            seen["params"] = params
            return {"multiDailyMetricTimeSeries": []}

        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", side_effect=_capture):
            gbs.fetch_daily_metrics("refresh", "locations/9",
                                    start=date(2026, 3, 1), end=date(2026, 3, 31))

        self.assertIn("locations/9:fetchMultiDailyMetricsTimeSeries", seen["url"])
        params = dict(seen["params"])
        self.assertEqual(params["dailyRange.startDate.year"], 2026)
        self.assertEqual(params["dailyRange.endDate.day"], 31)
        metrics = [v for k, v in seen["params"] if k == "dailyMetrics"]
        self.assertEqual(len(metrics), len(gbs.DAILY_METRICS))
        self.assertIn("CALL_CLICKS", metrics)

    def test_over_long_range_is_clamped_not_sent(self) -> None:
        """Google errors on a range beyond 18 months instead of clamping it."""
        seen: dict = {}

        def _capture(http, url, token, params=None):
            seen["params"] = dict(params)
            return {"multiDailyMetricTimeSeries": []}

        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", side_effect=_capture):
            gbs.fetch_daily_metrics("refresh", "locations/1",
                                    start=date(2015, 1, 1), end=date.today())

        earliest = date.today() - timedelta(days=gbs.MAX_LOOKBACK_DAYS)
        self.assertEqual(seen["params"]["dailyRange.startDate.year"], earliest.year)

    def test_reversed_dates_are_swapped(self) -> None:
        seen: dict = {}

        def _capture(http, url, token, params=None):
            seen["params"] = dict(params)
            return {"multiDailyMetricTimeSeries": []}

        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", side_effect=_capture):
            gbs.fetch_daily_metrics("refresh", "locations/1",
                                    start=date(2026, 3, 31), end=date(2026, 3, 1))

        self.assertEqual(seen["params"]["dailyRange.startDate.day"], 1)
        self.assertEqual(seen["params"]["dailyRange.endDate.day"], 31)


class LocationTests(unittest.TestCase):
    def test_read_mask_is_always_sent(self) -> None:
        """locations.list is a 400 without readMask — there is no default."""
        seen: dict = {}

        def _capture(http, url, token, params=None):
            seen.update(params or {})
            return {"locations": []}

        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", side_effect=_capture):
            gbs.list_locations("refresh", "accounts/1")

        self.assertIn("readMask", seen)
        self.assertIn("title", seen["readMask"])

    def test_location_address_is_flattened_for_display(self) -> None:
        payload = {"locations": [{
            "name": "locations/55",
            "title": "Downtown Clinic",
            "storefrontAddress": {
                "addressLines": ["100 Main St"], "locality": "Doylestown",
                "administrativeArea": "PA",
            },
            "websiteUri": "https://example.com",
        }]}
        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", return_value=payload):
            locs = gbs.list_locations("refresh", "accounts/1")

        self.assertEqual(locs[0]["id"], "locations/55")
        self.assertEqual(locs[0]["name"], "Downtown Clinic")
        self.assertEqual(locs[0]["address"], "100 Main St, Doylestown, PA")

    def test_pagination_follows_next_page_token(self) -> None:
        pages = [
            {"locations": [{"name": "locations/1", "title": "A"}], "nextPageToken": "t2"},
            {"locations": [{"name": "locations/2", "title": "B"}]},
        ]
        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", side_effect=pages):
            locs = gbs.list_locations("refresh", "accounts/1")

        self.assertEqual([loc["name"] for loc in locs], ["A", "B"])


class ReviewTests(unittest.TestCase):
    PAYLOAD = {
        "reviews": [
            {
                "reviewId": "r1", "starRating": "FIVE", "comment": "Great",
                "reviewer": {"displayName": "Sam"},
                "createTime": "2026-02-01T10:00:00Z",
                "reviewReply": {"comment": "Thanks!", "updateTime": "2026-02-02T10:00:00Z"},
            },
            {
                "reviewId": "r2", "starRating": "TWO", "comment": "Slow",
                "reviewer": {"isAnonymous": True, "displayName": "ignored"},
                "createTime": "2026-01-01T10:00:00Z",
            },
        ],
        "averageRating": 3.5,
        "totalReviewCount": 2,
    }

    def _fetch(self):
        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", return_value=self.PAYLOAD):
            return gbs.fetch_reviews("refresh", "accounts/7", "locations/9")

    def test_star_words_become_numbers(self) -> None:
        out = self._fetch()
        self.assertEqual(out["reviews"][0]["star_rating"], 5)
        self.assertEqual(out["reviews"][1]["star_rating"], 2)

    def test_anonymous_reviewer_is_labelled_not_named(self) -> None:
        out = self._fetch()
        self.assertEqual(out["reviews"][1]["reviewer_name"], "Anonymous")

    def test_reply_presence_is_captured(self) -> None:
        out = self._fetch()
        self.assertEqual(out["reviews"][0]["reply_comment"], "Thanks!")
        self.assertEqual(out["reviews"][1]["reply_comment"], "")

    def test_summary_fields_come_through(self) -> None:
        out = self._fetch()
        self.assertEqual(out["average_rating"], 3.5)
        self.assertEqual(out["total_review_count"], 2)

    def test_legacy_v4_path_nests_location_under_account(self) -> None:
        seen: dict = {}

        def _capture(http, url, token, params=None):
            seen["url"] = url
            return {"reviews": []}

        with patch.object(gbs, "_access_token", return_value="tok"), \
             patch.object(gbs, "_get", side_effect=_capture):
            gbs.fetch_reviews("refresh", "accounts/7", "locations/9")

        self.assertTrue(seen["url"].endswith("/accounts/7/locations/9/reviews"))
        self.assertIn("/v4/", seen["url"])


class MetricGroupingTests(unittest.TestCase):
    def test_impression_and_action_columns_are_all_real_columns(self) -> None:
        """The dashboard sums these two groups; a typo would silently drop a
        metric from the headline rather than error."""
        for col in (*gbs.IMPRESSION_COLUMNS, *gbs.ACTION_COLUMNS):
            self.assertIn(col, gbs.METRIC_COLUMNS, col)

    def test_the_two_groups_cover_every_metric_exactly_once(self) -> None:
        grouped = [*gbs.IMPRESSION_COLUMNS, *gbs.ACTION_COLUMNS]
        self.assertEqual(sorted(grouped), sorted(gbs.METRIC_COLUMNS))
        self.assertEqual(len(grouped), len(set(grouped)))


if __name__ == "__main__":
    unittest.main()
