"""LinkedIn Ads member demographics — who saw and clicked the ads.

The behaviour worth pinning down here is not "does it fetch rows" but the three
places this data refuses to behave like the rest of the paid-media pipeline:

  * demographic pivots have **no date dimension**, so a stored row is a total for
    a fixed window and the read path serves a whole window rather than slicing to
    the dashboard's date range;
  * LinkedIn **rejects metric projections** it doesn't support for a pivot, per
    pivot and per API version, so the fetch degrades instead of guessing — and a
    metric that came back refused is None, never 0;
  * pivot values arrive as **URNs**, resolved from static taxonomies where
    possible and cached reference lookups otherwise.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import linkedin_service  # noqa: E402
import linkedin_taxonomy  # noqa: E402
from linkedin_auth import LinkedInEnv  # noqa: E402

_ENV = LinkedInEnv(
    client_id="cid", client_secret="sec", refresh_token="rt", version="202509"
)


def _element(urn: str, impressions: int, clicks: int, **extra) -> dict:
    return {"pivotValues": [urn], "impressions": impressions, "clicks": clicks, **extra}


class FakeApi:
    """Stand-in for _linkedin_get: records paths, replays canned payloads.

    ``reject`` names fields whose presence in the projection makes the call fail
    the way LinkedIn does — a 400 naming the projected field. ``org_lookup_fails``
    reproduces the 403 a token without admin rights gets from organization reads.
    """

    def __init__(self, analytics: dict[str, list[dict]], reject: tuple[str, ...] = (),
                 org_lookup_fails: bool = False):
        self.analytics = analytics
        self.reject = reject
        self.org_lookup_fails = org_lookup_fails
        self.paths: list[str] = []

    def __call__(self, path: str, *, access_token: str, env=None, **kwargs) -> dict:
        self.paths.append(path)
        if path.startswith("/adAnalytics"):
            for field in self.reject:
                if f"{field}," in path or path.endswith(field):
                    raise RuntimeError(
                        f'LinkedIn API error 400 on {path}: Projected field "{field}" '
                        "is not valid for this pivot"
                    )
            pivot = path.split("pivot=", 1)[1].split("&", 1)[0]
            return {"elements": self.analytics.get(pivot, [])}
        if path.startswith("/organizationsLookup"):
            if self.org_lookup_fails:
                raise RuntimeError("LinkedIn API error 403: ACCESS_DENIED")
            ids = path.split("List(", 1)[1].rstrip(")").split(",")
            return {"results": {i: {"localizedName": f"Acme {i}"} for i in ids if i}}
        # Reference-data lookups: /titles/123, /industries/7
        return {"localizedName": f"Resolved {path.rsplit('/', 1)[-1]}"}


class DemographicFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_get = linkedin_service._linkedin_get
        self._orig_versioned = linkedin_service._linkedin_get_with_versions

    def tearDown(self) -> None:
        linkedin_service._linkedin_get = self._orig_get
        linkedin_service._linkedin_get_with_versions = self._orig_versioned

    def _fetch(self, api: FakeApi, **kwargs):
        # Analytics goes through _linkedin_get; reference lookups go through the
        # version-tolerant wrapper. One fake serves both.
        linkedin_service._linkedin_get = api
        linkedin_service._linkedin_get_with_versions = api
        return linkedin_service.fetch_ads_demographics(
            "512345678",
            start=date(2026, 7, 16),
            end=date(2026, 8, 14),
            access_token="tok",
            env=_ENV,
            **kwargs,
        )

    def test_static_taxonomies_resolve_without_an_api_call(self) -> None:
        api = FakeApi({"MEMBER_SENIORITY": [
            _element("urn:li:seniority:8", 900, 12, costInUsd="120.5", conversions="2"),
            _element("urn:li:seniority:6", 400, 5, costInUsd="60", conversions="1"),
        ]})
        rows = self._fetch(api, dimensions=["seniority"])
        self.assertEqual([r["category"] for r in rows], ["CXO", "Director"])
        # Seniority is a closed local taxonomy — no reference lookup should fire.
        self.assertTrue(all(p.startswith("/adAnalytics") for p in api.paths))

    def test_company_size_enum_is_humanized_not_urn_split(self) -> None:
        # MEMBER_COMPANY_SIZE returns a bare enum, not a URN. Splitting on ':'
        # must be a no-op for it rather than mangling the value.
        api = FakeApi({"MEMBER_COMPANY_SIZE": [
            _element("SIZE_51_TO_200", 500, 8, costInUsd="40"),
            _element("SIZE_10001_OR_MORE", 300, 4, costInUsd="25"),
        ]})
        rows = self._fetch(api, dimensions=["company_size"])
        self.assertEqual([r["category"] for r in rows], ["51-200", "10,001+"])

    def test_reference_lookups_are_cached_per_sync(self) -> None:
        api = FakeApi({"MEMBER_JOB_TITLE": [
            _element("urn:li:title:100", 900, 9),
            _element("urn:li:title:200", 400, 4),
            _element("urn:li:title:100", 100, 1),  # repeat id, same window
        ]})
        rows = self._fetch(api, dimensions=["job_title"])
        self.assertEqual(rows[0]["category"], "Resolved 100")
        lookups = [p for p in api.paths if p.startswith("/titles/")]
        # Two distinct ids -> two lookups, even though three rows referenced them.
        self.assertEqual(sorted(lookups), ["/titles/100", "/titles/200"])

    def test_projection_degrades_when_conversions_is_rejected(self) -> None:
        api = FakeApi(
            {"MEMBER_INDUSTRY": [_element("urn:li:industry:96", 700, 7, costInUsd="55")]},
            reject=("conversions",),
        )
        rows = self._fetch(api, dimensions=["industry"])
        self.assertEqual(len(rows), 1)
        # Spend survived the retry; conversions is None (refused), never 0.
        self.assertEqual(rows[0]["spend"], 55.0)
        self.assertIsNone(rows[0]["conversions"])
        analytics = [p for p in api.paths if p.startswith("/adAnalytics")]
        self.assertEqual(len(analytics), 2)  # first attempt + degraded retry

    def test_companies_resolve_in_one_batched_lookup(self) -> None:
        # /organizations/{id} requires the ADMINISTRATOR role on that org, which
        # nobody holds for the third-party companies in ad demographics — so
        # companies go through organizationsLookup, batched, never one per row.
        api = FakeApi({"MEMBER_COMPANY": [
            _element(f"urn:li:organization:{i}", 500 - i, 5) for i in range(1, 6)
        ]})
        rows = self._fetch(api, dimensions=["company"])
        self.assertEqual(rows[0]["category"], "Acme 1")
        lookups = [p for p in api.paths if p.startswith("/organizationsLookup")]
        self.assertEqual(len(lookups), 1)
        self.assertIn("List(1,2,3,4,5)", lookups[0])
        # The per-entity endpoint is never touched.
        self.assertFalse([p for p in api.paths if p.startswith("/organizations/")])

    def test_company_lookup_failure_degrades_to_ids_without_retrying(self) -> None:
        api = FakeApi(
            {"MEMBER_COMPANY": [
                _element("urn:li:organization:77", 900, 9),
                _element("urn:li:organization:88", 400, 4),
            ]},
            org_lookup_fails=True,
        )
        rows = self._fetch(api, dimensions=["company"])
        self.assertEqual([r["category"] for r in rows], ["Company 77", "Company 88"])
        # One failed batch, not one failed call per company.
        self.assertEqual(len([p for p in api.paths if "organizationsLookup" in p]), 1)

    def test_projection_degrades_all_the_way_to_impressions_and_clicks(self) -> None:
        api = FakeApi(
            {"MEMBER_COMPANY": [_element("urn:li:organization:42", 600, 6)]},
            reject=("conversions", "costInUsd"),
        )
        rows = self._fetch(api, dimensions=["company"])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["spend"])
        self.assertIsNone(rows[0]["conversions"])
        self.assertEqual(rows[0]["impressions"], 600)

    def test_a_non_projection_error_propagates(self) -> None:
        # A permissions/auth failure must not be silently retried away as if it
        # were an unsupported-metric response.
        def boom(path, **kwargs):
            raise RuntimeError("LinkedIn API error 403: not authorized")

        linkedin_service._linkedin_get = boom
        rows = linkedin_service.fetch_ads_demographics(
            "512345678", start=date(2026, 7, 16), end=date(2026, 8, 14),
            dimensions=["seniority"], access_token="tok", env=_ENV,
        )
        # The dimension is skipped (one bad pivot never loses the others), but no
        # fabricated rows appear either.
        self.assertEqual(rows, [])

    def test_rows_are_capped_and_sorted_by_impressions(self) -> None:
        api = FakeApi({"MEMBER_SENIORITY": [
            _element(f"urn:li:seniority:{i}", i * 10, i) for i in range(1, 9)
        ]})
        rows = self._fetch(api, dimensions=["seniority"], top_n=3)
        self.assertEqual([r["impressions"] for r in rows], [80, 70, 60])

    def test_zero_activity_rows_are_dropped(self) -> None:
        api = FakeApi({"MEMBER_SENIORITY": [
            _element("urn:li:seniority:4", 0, 0),
            _element("urn:li:seniority:5", 10, 0),
        ]})
        rows = self._fetch(api, dimensions=["seniority"])
        self.assertEqual([r["category"] for r in rows], ["Manager"])

    def test_every_dimension_maps_to_a_member_pivot(self) -> None:
        for key, pivot in linkedin_service.DEMOGRAPHIC_PIVOTS.items():
            self.assertTrue(pivot.startswith("MEMBER_"), f"{key} -> {pivot}")
            self.assertIn(key, linkedin_service.DEMOGRAPHIC_LABELS)

    def test_request_asks_for_the_whole_window_not_a_daily_series(self) -> None:
        # Demographic pivots have no date breakdown; asking for DAILY would just
        # re-return the same totals after a wasted round trip.
        api = FakeApi({"MEMBER_SENIORITY": [_element("urn:li:seniority:4", 5, 1)]})
        self._fetch(api, dimensions=["seniority"])
        self.assertIn("timeGranularity=ALL", api.paths[0])
        self.assertNotIn("timeGranularity=DAILY", api.paths[0])


class WindowSelectionTests(unittest.TestCase):
    """Which synced window a requested date range resolves to."""

    def _rows(self) -> list[dict]:
        return [
            {"window_key": "LAST_30_DAYS", "window_start": "2026-07-16", "window_end": "2026-08-14"},
            {"window_key": "LAST_90_DAYS", "window_start": "2026-05-17", "window_end": "2026-08-14"},
        ]

    def test_short_range_lands_on_the_nearest_window(self) -> None:
        from marketing_service import pick_demographics_window

        chosen = pick_demographics_window(
            self._rows(), start_date=date(2026, 8, 8), end_date=date(2026, 8, 14)
        )
        self.assertEqual(chosen, "LAST_30_DAYS")

    def test_long_range_lands_on_the_long_window(self) -> None:
        from marketing_service import pick_demographics_window

        chosen = pick_demographics_window(
            self._rows(), start_date=date(2026, 5, 1), end_date=date(2026, 8, 14)
        )
        self.assertEqual(chosen, "LAST_90_DAYS")

    def test_explicit_window_wins_when_it_was_synced(self) -> None:
        from marketing_service import pick_demographics_window

        chosen = pick_demographics_window(
            self._rows(), start_date=date(2026, 8, 8), end_date=date(2026, 8, 14),
            window="last_90_days",
        )
        self.assertEqual(chosen, "LAST_90_DAYS")

    def test_unsynced_window_request_falls_back_to_nearest(self) -> None:
        from marketing_service import pick_demographics_window

        chosen = pick_demographics_window(
            self._rows(), start_date=date(2026, 8, 8), end_date=date(2026, 8, 14),
            window="LAST_365_DAYS",
        )
        self.assertEqual(chosen, "LAST_30_DAYS")

    def test_no_rows_means_no_window(self) -> None:
        from marketing_service import pick_demographics_window

        self.assertEqual(
            pick_demographics_window([], start_date=date(2026, 8, 1), end_date=date(2026, 8, 14)),
            "",
        )


class TaxonomyTests(unittest.TestCase):
    def test_organic_service_shares_the_same_taxonomies(self) -> None:
        # Ads and organic demographics resolve seniority/function identically —
        # the tables moved to linkedin_taxonomy rather than being duplicated.
        import linkedin_organic_service as organic

        self.assertIs(organic._SENIORITY_LABELS, linkedin_taxonomy.SENIORITY_LABELS)
        self.assertIs(organic._FUNCTION_LABELS, linkedin_taxonomy.FUNCTION_LABELS)

    def test_failed_lookup_caches_its_fallback(self) -> None:
        calls: list[str] = []

        def failing(path: str) -> dict:
            calls.append(path)
            raise RuntimeError("404")

        cache: dict[str, str] = {}
        for _ in range(3):
            label = linkedin_taxonomy.resolve_reference_label(
                "title", "77", get=failing, cache=cache
            )
        self.assertEqual(label, "Job title 77")
        # The id is immutable — a second attempt would fail identically and only
        # cost another call against a rate-limited API.
        self.assertEqual(len(calls), 1)

    def test_reference_endpoints_are_not_naive_plurals(self) -> None:
        self.assertEqual(linkedin_taxonomy.REFERENCE_ENDPOINTS["industry"], "industries")
        self.assertEqual(linkedin_taxonomy.REFERENCE_ENDPOINTS["geo"], "geo")
        self.assertEqual(linkedin_taxonomy.REFERENCE_ENDPOINTS["organization"], "organizations")

    def test_original_industry_codes_resolve_locally(self) -> None:
        def _boom(path: str) -> dict:
            raise AssertionError(f"no lookup expected, got {path}")

        cache: dict[str, str] = {}
        label = linkedin_taxonomy.resolve_reference_label(
            "industry", "11", get=_boom, cache=cache
        )
        self.assertEqual(label, "Management Consulting")
        self.assertEqual(cache["industry:11"], "Management Consulting")

    def test_four_digit_industry_ids_still_ask_the_api(self) -> None:
        calls: list[str] = []

        def _get(path: str) -> dict:
            calls.append(path)
            return {"localizedName": "Business Consulting and Services"}

        label = linkedin_taxonomy.resolve_reference_label(
            "industry", "1862", get=_get, cache={}
        )
        # The newer taxonomy ids are not in the local table.
        self.assertEqual(calls, ["/industries/1862"])
        self.assertEqual(label, "Business Consulting and Services")

    def test_placeholder_labels_are_recognised_as_unresolved(self) -> None:
        for label in ("Region 90000070", "Industry 1862", "Job title 77", "Company 5"):
            self.assertTrue(linkedin_taxonomy.is_unresolved_label(label), label)
        for label in ("Management Consulting", "51-200", "CXO", "Region One"):
            self.assertFalse(linkedin_taxonomy.is_unresolved_label(label), label)

    def test_relabel_upgrades_a_stored_placeholder(self) -> None:
        # A label stored by an older sync, re-labelled from the URN kept next to
        # it — no re-sync needed.
        self.assertEqual(
            linkedin_taxonomy.relabel_demographic(
                "industry", "Industry 105", "urn:li:industry:105"
            ),
            "Professional Training & Coaching",
        )
        # A resolved label is never second-guessed...
        self.assertEqual(
            linkedin_taxonomy.relabel_demographic(
                "industry", "Hospitals & Health Care", "urn:li:industry:2063"
            ),
            "Hospitals & Health Care",
        )
        # ...and a geo id no local table knows stays a placeholder.
        self.assertEqual(
            linkedin_taxonomy.relabel_demographic(
                "region", "Region 90000070", "urn:li:geo:90000070"
            ),
            "Region 90000070",
        )

    def test_standardized_data_lookups_use_the_v2_base(self) -> None:
        seen: dict[str, Any] = {}

        def _get(path, *, access_token, env=None, **kw):
            seen["path"] = path
            seen["base"] = kw.get("base")
            return {"localizedName": "Marketing Manager"}

        orig = linkedin_service._linkedin_get
        linkedin_service._linkedin_get = _get
        try:
            payload = linkedin_service._reference_data_get(
                "/titles/77", access_token="tok", env=_ENV
            )
        finally:
            linkedin_service._linkedin_get = orig
        self.assertEqual(payload["localizedName"], "Marketing Manager")
        self.assertEqual(seen["base"], linkedin_service.LINKEDIN_API_V2_BASE)

    def test_v2_miss_retries_the_versioned_base(self) -> None:
        # Belt and braces: if an app is provisioned the other way around, a
        # "no virtual resource" answer from /v2 shouldn't cost us the label.
        def _no_resource(path, *, access_token, env=None, **kw):
            raise RuntimeError(
                f"LinkedIn API error 404 on {path}: No virtual resource found for: titles"
            )

        calls: list[str] = []

        def _versioned(path, *, access_token, env=None, **kw):
            calls.append(path)
            return {"localizedName": "Marketing Manager"}

        orig_get = linkedin_service._linkedin_get
        orig_versioned = linkedin_service._linkedin_get_with_versions
        linkedin_service._linkedin_get = _no_resource
        linkedin_service._linkedin_get_with_versions = _versioned
        try:
            payload = linkedin_service._reference_data_get(
                "/titles/77", access_token="tok", env=_ENV
            )
        finally:
            linkedin_service._linkedin_get = orig_get
            linkedin_service._linkedin_get_with_versions = orig_versioned
        self.assertEqual(calls, ["/titles/77"])
        self.assertEqual(payload["localizedName"], "Marketing Manager")


class SyncWindowTests(unittest.TestCase):
    def test_each_window_is_stored_and_deleted_independently(self) -> None:
        import bq_linkedin_ads_service as svc

        seen: list[tuple[str, int]] = []

        class FakeWarehouse:
            def mirror_linkedin_ads_demographics(self, account_id, window_key, rows):
                seen.append((window_key, len(rows)))
                # Every row carries the window it totals over, so the mirror can
                # scope its replace to this window without touching the other.
                for row in rows:
                    assert row["window_start"] and row["window_end"]
                return {"rows_upserted": len(rows)}

            def create_linkedin_demographics_mart_view(self):
                return {"enabled": True}

        class FakeLinkedIn:
            def fetch_ads_demographics(self, account_id, *, start, end, access_token=None):
                return [{"dimension": "seniority", "category_urn": "urn:li:seniority:4",
                         "category": "Senior", "impressions": 10, "clicks": 1,
                         "spend": 2.0, "conversions": None}]

        orig_wh = sys.modules.get("bigquery_warehouse")
        orig_li = sys.modules.get("linkedin_service")
        sys.modules["bigquery_warehouse"] = FakeWarehouse()
        sys.modules["linkedin_service"] = FakeLinkedIn()
        try:
            out = svc.sync_linkedin_demographics(account_id="512345678")
        finally:
            if orig_wh is not None:
                sys.modules["bigquery_warehouse"] = orig_wh
            if orig_li is not None:
                sys.modules["linkedin_service"] = orig_li

        self.assertEqual([w for w, _ in seen], list(svc.DEMOGRAPHIC_WINDOWS))
        self.assertEqual(out["rows_upserted"], 2)
        self.assertEqual(set(out["windows"]), set(svc.DEMOGRAPHIC_WINDOWS))

    def test_one_window_failing_keeps_the_other(self) -> None:
        import bq_linkedin_ads_service as svc

        class FakeWarehouse:
            def mirror_linkedin_ads_demographics(self, account_id, window_key, rows):
                return {"rows_upserted": len(rows)}

            def create_linkedin_demographics_mart_view(self):
                return {"enabled": True}

        class FlakyLinkedIn:
            def __init__(self):
                self.calls = 0

            def fetch_ads_demographics(self, account_id, *, start, end, access_token=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("rate limited")
                return [{"dimension": "seniority", "category_urn": "urn:li:seniority:4",
                         "category": "Senior", "impressions": 10, "clicks": 1,
                         "spend": None, "conversions": None}]

        orig_wh = sys.modules.get("bigquery_warehouse")
        orig_li = sys.modules.get("linkedin_service")
        sys.modules["bigquery_warehouse"] = FakeWarehouse()
        sys.modules["linkedin_service"] = FlakyLinkedIn()
        try:
            out = svc.sync_linkedin_demographics(account_id="512345678")
        finally:
            if orig_wh is not None:
                sys.modules["bigquery_warehouse"] = orig_wh
            if orig_li is not None:
                sys.modules["linkedin_service"] = orig_li

        windows = out["windows"]
        self.assertIn("error", windows[svc.DEMOGRAPHIC_WINDOWS[0]])
        self.assertEqual(windows[svc.DEMOGRAPHIC_WINDOWS[1]]["rows_upserted"], 1)
        self.assertEqual(out["rows_upserted"], 1)


class DemoDataTests(unittest.TestCase):
    def test_demo_serves_a_window_not_the_requested_range(self) -> None:
        import demo_data

        out = demo_data.generate(
            "explorer.linkedin_demographics", {"start": "2026-08-08", "end": "2026-08-14"}
        )
        self.assertEqual(out["window"], "LAST_30_DAYS")
        # A 7-day request still gets a 30-day window back — the panel labels it.
        self.assertNotEqual(out["window_start"], out["date_range"]["start_date"])
        self.assertTrue(set(out["by_dimension"]) <= set(linkedin_service.DEMOGRAPHIC_PIVOTS))

    def test_demo_rows_are_sorted_by_impressions(self) -> None:
        import demo_data

        out = demo_data.generate("explorer.linkedin_demographics", {})
        for rows in out["by_dimension"].values():
            self.assertEqual(
                [r["impressions"] for r in rows],
                sorted((r["impressions"] for r in rows), reverse=True),
            )


class PanelRenderTests(unittest.TestCase):
    """The panel's markup, rendered for real.

    Config and theme reads are stubbed rather than left to hit Postgres, so the
    render is hermetic — the assertions here are about panel markup, which is the
    same for every client.
    """

    def setUp(self) -> None:
        import client_dashboard_config as cdc
        import types

        self._orig_config = cdc.get_config
        self._orig_theme = cdc.get_theme
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=None,
            card_layouts={},
        )
        cdc.get_theme = lambda slug: None

    def tearDown(self) -> None:
        import client_dashboard_config as cdc

        cdc.get_config = self._orig_config
        cdc.get_theme = self._orig_theme

    def _html(self) -> str:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_email="t@e.com",
        )

    def test_panel_and_endpoint_are_wired_into_the_explorer_tab(self) -> None:
        html = self._html()
        self.assertIn('id="sec-lidemo"', html)
        self.assertIn("/api/clients/test/linkedin/demographics", html)
        self.assertIn("loadLinkedinDemographics", html)

    def test_panel_is_a_registered_explorer_layout_card(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import EXPLORER_LAYOUT_CARDS

        self.assertIn("lidemo", EXPLORER_LAYOUT_CARDS)

    def test_panel_starts_hidden_until_data_arrives(self) -> None:
        # A client with no LinkedIn demographics must not get an empty panel.
        self.assertIn('id="sec-lidemo" style="display:none"', self._html())

    def test_panel_states_the_fixed_window_caveat(self) -> None:
        html = self._html()
        self.assertIn("not the date range selected above", html)
        self.assertIn("approximate", html)


if __name__ == "__main__":
    unittest.main()
