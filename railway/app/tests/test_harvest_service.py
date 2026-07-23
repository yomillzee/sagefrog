from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# harvest_service imports httpx + the DB/oauth modules at import time; stub the
# ones that aren't needed for the pure-logic tests so this runs without the full
# runtime dependency set (mirrors how the other service tests stub google.cloud).
for _name in ("httpx", "db", "db_cache", "oauth_flows", "oauth_store"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

import harvest_service  # noqa: E402


class MonthBoundsTest(unittest.TestCase):
    def test_mid_month(self):
        first, days_in_month, elapsed = harvest_service._month_bounds(date(2026, 7, 21))
        self.assertEqual(first, date(2026, 7, 1))
        self.assertEqual(days_in_month, 31)
        self.assertEqual(elapsed, 21)

    def test_february_leap_year(self):
        first, days_in_month, elapsed = harvest_service._month_bounds(date(2024, 2, 10))
        self.assertEqual(first, date(2024, 2, 1))
        self.assertEqual(days_in_month, 29)
        self.assertEqual(elapsed, 10)


class CumulativeByClientTest(unittest.TestCase):
    def _entries(self):
        return [
            {"client": {"id": 1, "name": "ILC Dover"}, "project": {"id": 11, "name": "Retainer"},
             "spent_date": "2026-07-02", "hours": 5, "billable": True},
            {"client": {"id": 1, "name": "ILC Dover"}, "project": {"id": 11, "name": "Retainer"},
             "spent_date": "2026-07-02", "hours": 3, "billable": False},
            {"client": {"id": 1, "name": "ILC Dover"}, "project": {"id": 12, "name": "Website Rebuild"},
             "spent_date": "2026-07-05", "hours": 4, "billable": True},
            {"client": {"id": 2, "name": "EOS Worldwide"}, "project": {"id": 21, "name": "SEO"},
             "spent_date": "2026-07-03", "hours": 10, "billable": True},
        ]

    def test_hours_bucketed_by_client_and_project(self):
        by = harvest_service._cumulative_by_client(
            self._entries(), first=date(2026, 7, 1), days_elapsed=21
        )
        self.assertEqual(by["1"]["name"], "ILC Dover")
        # ILC Dover has two projects; the retainer got both day-2 entries.
        self.assertEqual(by["1"]["projects"]["11"]["daily"], {2: 8.0})
        self.assertEqual(by["1"]["projects"]["12"]["daily"], {5: 4.0})
        self.assertEqual(by["2"]["projects"]["21"]["daily"], {3: 10.0})

    def test_billable_hours_tracked_separately_per_project(self):
        by = harvest_service._cumulative_by_client(
            self._entries(), first=date(2026, 7, 1), days_elapsed=21
        )
        # Retainer day 2: 5 billable + 3 non-billable → total 8, billable 5.
        self.assertEqual(by["1"]["projects"]["11"]["daily"], {2: 8.0})
        self.assertEqual(by["1"]["projects"]["11"]["daily_billable"], {2: 5.0})

    def test_future_and_prior_month_entries_excluded(self):
        entries = self._entries() + [
            {"client": {"id": 1, "name": "ILC Dover"}, "project": {"id": 11, "name": "Retainer"},
             "spent_date": "2026-07-25", "hours": 99},
            {"client": {"id": 1, "name": "ILC Dover"}, "project": {"id": 11, "name": "Retainer"},
             "spent_date": "2026-06-30", "hours": 42},
        ]
        by = harvest_service._cumulative_by_client(
            entries, first=date(2026, 7, 1), days_elapsed=21
        )
        # Day 25 is after the elapsed cutoff; June 30 is before the month start.
        self.assertEqual(by["1"]["projects"]["11"]["daily"], {2: 8.0})

    def test_missing_client_and_project_fall_back_to_zero_bucket(self):
        entries = [{"client": {}, "spent_date": "2026-07-04", "hours": 2}]
        by = harvest_service._cumulative_by_client(
            entries, first=date(2026, 7, 1), days_elapsed=21
        )
        self.assertEqual(by["0"]["name"], "(No client)")
        self.assertEqual(by["0"]["projects"]["0"]["daily"], {4: 2.0})
        self.assertEqual(by["0"]["projects"]["0"]["name"], "(No project)")


class CacheBehaviourTest(unittest.TestCase):
    """The Harvest API pull must be served from cache within the TTL (so repeated
    loads don't hammer Harvest), while goals are always applied fresh on top."""

    def _cached_clients(self):
        return [
            {"harvest_client_id": "1", "name": "ILC Dover", "projects": [
                {"project_id": "11", "name": "Retainer", "series": [8.0, 8.0], "series_billable": [5.0, 5.0], "total_hours": 8.0},
                {"project_id": "12", "name": "Website", "series": [0.0, 4.0], "series_billable": [0.0, 4.0], "total_hours": 4.0},
            ]},
            {"harvest_client_id": "2", "name": "EOS Worldwide", "projects": [
                {"project_id": "21", "name": "SEO", "series": [10.0, 30.0], "series_billable": [10.0, 30.0], "total_hours": 30.0},
            ]},
        ]

    def test_cache_hit_skips_harvest_pull_and_applies_goals_and_tags(self):
        hit = SimpleNamespace(
            response_json=self._cached_clients(),
            created_at=datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
        )
        with patch.object(harvest_service, "is_connected", return_value=True), \
             patch.object(harvest_service, "_account_id", return_value="acct-1"), \
             patch.object(harvest_service.oauth_store, "public_status",
                          return_value=SimpleNamespace(metadata={"account_name": "Sagefrog"}), create=True), \
             patch.object(harvest_service.db_cache, "get_cached", return_value=hit, create=True) as m_get, \
             patch.object(harvest_service, "_build_client_series",
                          side_effect=AssertionError("must not pull Harvest on a cache hit")), \
             patch.object(harvest_service, "get_goals",
                          return_value={"1": {"min": 80.0, "max": 100.0}}), \
             patch.object(harvest_service, "get_project_tags",
                          return_value={"11": "retainer", "12": "project"}):
            out = harvest_service.build_client_hours_overview(today=date(2026, 7, 21))

        m_get.assert_called_once()
        self.assertIsNone(out.get("error"))
        by_id = {c["harvest_client_id"]: c for c in out["clients"]}
        # Range goal layered on the cached hours series, fresh.
        self.assertEqual(by_id["1"]["goal_min"], 80.0)
        self.assertEqual(by_id["1"]["goal_max"], 100.0)
        self.assertEqual(by_id["1"]["goal_label"], "80–100h")
        self.assertIsNone(by_id["2"]["goal_min"])       # no goal set
        # Tags applied fresh onto the cached projects.
        proj = {p["project_id"]: p for p in by_id["1"]["projects"]}
        self.assertEqual(proj["11"]["tag"], "retainer")
        self.assertEqual(proj["12"]["tag"], "project")
        self.assertEqual(out["totals"]["total_hours"], 42.0)  # 8 + 4 + 30
        self.assertEqual(out["totals"]["goal_min"], 80.0)
        self.assertEqual(out["refreshed_at"], "2026-07-21T12:00:00+00:00")

    def test_owner_applied_fresh_onto_each_client(self):
        hit = SimpleNamespace(
            response_json=self._cached_clients(),
            created_at=datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
        )
        with patch.object(harvest_service, "is_connected", return_value=True), \
             patch.object(harvest_service, "_account_id", return_value="acct-1"), \
             patch.object(harvest_service.oauth_store, "public_status",
                          return_value=SimpleNamespace(metadata={}), create=True), \
             patch.object(harvest_service.db_cache, "get_cached", return_value=hit, create=True), \
             patch.object(harvest_service, "get_goals", return_value={}), \
             patch.object(harvest_service, "get_project_tags", return_value={}), \
             patch.object(harvest_service, "get_client_owners", return_value={"1": "Sam"}):
            out = harvest_service.build_client_hours_overview(today=date(2026, 7, 21))

        by_id = {c["harvest_client_id"]: c for c in out["clients"]}
        self.assertEqual(by_id["1"]["owner"], "Sam")   # owner attached
        self.assertIsNone(by_id["2"]["owner"])          # no owner set

    def test_force_refresh_bypasses_cache(self):
        fresh = self._cached_clients()
        with patch.object(harvest_service, "is_connected", return_value=True), \
             patch.object(harvest_service, "get_project_tags", return_value={}), \
             patch.object(harvest_service, "_account_id", return_value="acct-1"), \
             patch.object(harvest_service.oauth_store, "public_status",
                          return_value=SimpleNamespace(metadata={}), create=True), \
             patch.object(harvest_service.db_cache, "get_cached",
                          side_effect=AssertionError("force refresh must not read cache"), create=True), \
             patch.object(harvest_service.db_cache, "put_cached", create=True) as m_put, \
             patch.object(harvest_service, "_build_client_series", return_value=fresh) as m_build, \
             patch.object(harvest_service, "get_goals", return_value={}):
            out = harvest_service.build_client_hours_overview(today=date(2026, 7, 21), use_cache=False)

        m_build.assert_called_once()
        m_put.assert_called_once()  # fresh pull is written back to the cache
        self.assertEqual(out["totals"]["total_hours"], 42.0)


class ClientOwnerRosterTest(unittest.TestCase):
    def test_roster_is_unique_and_case_insensitive(self):
        self.assertEqual(len(harvest_service.CLIENT_OWNERS),
                         len(set(harvest_service.CLIENT_OWNERS)))
        self.assertIn("Sam", harvest_service.VALID_OWNERS)
        # Any casing maps back to the canonical spelling.
        self.assertEqual(harvest_service._OWNER_BY_LOWER.get("sam"), "Sam")
        self.assertEqual(harvest_service._OWNER_BY_LOWER.get("ALYSSA".lower()), "Alyssa")
        self.assertIsNone(harvest_service._OWNER_BY_LOWER.get("nobody"))


class ParseGoalTextTest(unittest.TestCase):
    def test_single_value(self):
        self.assertEqual(harvest_service.parse_goal_text("80"), (80.0, 80.0))

    def test_range(self):
        self.assertEqual(harvest_service.parse_goal_text("80-100"), (80.0, 100.0))

    def test_range_en_dash_and_spaces(self):
        self.assertEqual(harvest_service.parse_goal_text(" 80 – 100 "), (80.0, 100.0))

    def test_range_reversed_is_normalized(self):
        self.assertEqual(harvest_service.parse_goal_text("100-80"), (80.0, 100.0))

    def test_open_ended_floor(self):
        self.assertEqual(harvest_service.parse_goal_text("160+"), (160.0, None))

    def test_blank_clears(self):
        self.assertEqual(harvest_service.parse_goal_text("  "), (None, None))

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            harvest_service.parse_goal_text("-5")

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            harvest_service.parse_goal_text("abc")


class FormatGoalTest(unittest.TestCase):
    def test_single(self):
        self.assertEqual(harvest_service.format_goal(80, 80), "80h")

    def test_range(self):
        self.assertEqual(harvest_service.format_goal(80, 100), "80–100h")

    def test_open_ended(self):
        self.assertEqual(harvest_service.format_goal(160, None), "160h+")

    def test_unset(self):
        self.assertEqual(harvest_service.format_goal(None, None), "")


if __name__ == "__main__":
    unittest.main()
