"""Quarter presets + the "compare against" period picker.

Covers the three layers the feature spans: the preset allowlist a client
default is validated against, the dashboard markup/JS the picker is built from,
and the Search Console service, which is the only place a comparison window is
resolved server-side.
"""
from __future__ import annotations

import re
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# bq_gsc_service imports google.cloud.bigquery at module load. The helpers under
# test here are pure date math that never reaches BigQuery, so a stub lets the
# import tree load without the SDK (same pattern as test_agency_trends_service).
if "google.cloud.bigquery" not in sys.modules:
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _FakeQueryJobConfig:
        def __init__(self, dry_run=False):
            self.dry_run = dry_run
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig
    service_account_mod.Credentials = _FakeCredentials
    cloud_mod.bigquery = bigquery_mod
    google_mod.cloud = cloud_mod
    google_mod.oauth2 = oauth2_mod
    oauth2_mod.service_account = service_account_mod
    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.service_account"] = service_account_mod

import bq_gsc_service  # noqa: E402
import client_dashboard_config as cdc  # noqa: E402
from dashboard.renderers.bigquery_dashboard_renderer import (  # noqa: E402
    render_bigquery_dashboard_page,
)


def _render(**kwargs) -> str:
    base = dict(
        client_slug="demo", api_client_key="demo", label="Demo",
        use_session=True, session_email="t@e.com",
    )
    base.update(kwargs)
    return render_bigquery_dashboard_page(**base)


class QuarterPresetsTest(unittest.TestCase):
    def test_quarter_presets_are_savable_defaults(self):
        for preset in ("this_quarter", "last_quarter"):
            self.assertIn(preset, cdc.DATE_RANGE_PRESETS)
            self.assertEqual(cdc._normalize_date_preset(preset), preset)

    def test_renderer_offers_quarter_rows(self):
        html = _render()
        for preset, label in (("this_quarter", "This quarter"), ("last_quarter", "Last quarter")):
            self.assertIn(f'data-preset="{preset}"', html)
            self.assertIn(label, html)

    def test_stored_quarter_default_lands_and_labels_the_toggle(self):
        cfg = cdc.ClientConfigRow(
            client_slug="demo", label="Demo",
            google_customer_id=None, linkedin_account_id=None,
            meta_account_id=None, ga4_client_key=None,
            default_date_preset="last_quarter",
        )
        with patch.object(cdc, "get_config", return_value=cfg):
            html = _render()
        self.assertIn('const DEFAULT_DATE_PRESET = "last_quarter"', html)
        self.assertIn('<span id="rangeToggleLabel">Last quarter</span>', html)
        self.assertIn('class="range-opt active" role="option" data-preset="last_quarter"', html)

    def test_every_renderer_preset_is_a_savable_default(self):
        """The picker and the allowlist must not drift apart -- a preset the
        dropdown offers but the API rejects breaks "Make default"."""
        html = _render()
        offered = set(re.findall(r'data-preset="([a-z0-9_]+)"', html))
        self.assertTrue(offered)
        self.assertEqual(offered - set(cdc.DATE_RANGE_PRESETS), set())


class ComparePickerTest(unittest.TestCase):
    def test_picker_offers_both_modes_and_defaults_to_previous_period(self):
        html = _render()
        self.assertIn('data-cmp="prev_period"', html)
        self.assertIn('data-cmp="prev_year"', html)
        self.assertIn('<span id="compareToggleLabel">Previous period</span>', html)
        # Previous period is the pre-existing behaviour, so it starts selected.
        self.assertIn('class="range-opt active" role="option" data-cmp="prev_period"', html)
        self.assertIn("let compareMode='prev_period';", html)

    def test_mode_is_remembered_per_client(self):
        self.assertIn("'sf.compareMode.demo'", _render())
        self.assertIn("'sf.compareMode.acme'", _render(client_slug="acme"))

    def test_previous_year_shifts_the_selected_range_back_a_year(self):
        html = _render()
        self.assertIn("compareStart=shiftYears(currentStart,1); compareEnd=shiftYears(currentEnd,1);", html)

    def test_delta_wording_follows_the_selected_mode(self):
        """No hardcoded "vs prior period" left to contradict the picker."""
        html = _render()
        self.assertIn("function cmpNoun()", html)
        self.assertNotIn("vs prior period<", html)
        self.assertNotIn('title="vs previous period"', html)

    def test_gsc_endpoints_receive_the_comparison_window(self):
        """Search Console computes its deltas server-side, so the window has to
        travel with the request or the tab contradicts every other panel."""
        html = _render()
        self.assertIn("compare_start_date=", html)
        self.assertIn("withCompare(withDates(GSC_API))", html)
        self.assertIn("withCompare(withDates(GSC_KEYWORD_MATCHES_API))", html)

    def test_backfill_notice_is_rendered_and_wired(self):
        html = _render()
        self.assertIn('id="compareNotice"', html)
        self.assertIn("function syncCompareNotice()", html)
        self.assertIn("backfilled", html)


class GscComparePeriodTest(unittest.TestCase):
    def test_defaults_to_the_preceding_same_length_window(self):
        # 91 days ending the day before the range starts -- day count, not
        # calendar quarter, which is what _prior_period has always returned.
        start, end = date(2026, 4, 1), date(2026, 6, 30)
        self.assertEqual(
            bq_gsc_service._compare_period(start, end, None, None),
            (date(2025, 12, 31), date(2026, 3, 31)),
        )

    def test_explicit_window_wins(self):
        start, end = date(2026, 4, 1), date(2026, 6, 30)
        self.assertEqual(
            bq_gsc_service._compare_period(start, end, date(2025, 4, 1), date(2025, 6, 30)),
            (date(2025, 4, 1), date(2025, 6, 30)),
        )

    def test_inverted_window_falls_back_rather_than_querying_backwards(self):
        start, end = date(2026, 4, 1), date(2026, 6, 30)
        self.assertEqual(
            bq_gsc_service._compare_period(start, end, date(2025, 6, 30), date(2025, 4, 1)),
            (date(2025, 12, 31), date(2026, 3, 31)),
        )

    def test_contiguous_windows_scan_as_one_range(self):
        start, end = date(2026, 4, 1), date(2026, 6, 30)
        ps, pe = bq_gsc_service._compare_period(start, end, None, None)
        sql = bq_gsc_service._two_window_filter(start, end, ps, pe)
        self.assertEqual(sql, "q.date BETWEEN '2025-12-31' AND '2026-06-30'")

    def test_year_apart_windows_skip_the_gap_between_them(self):
        """A previous-year comparison leaves ~9 months of data nobody asked
        for between the windows; scanning it would be pure BigQuery spend."""
        sql = bq_gsc_service._two_window_filter(
            date(2026, 4, 1), date(2026, 6, 30), date(2025, 4, 1), date(2025, 6, 30),
        )
        self.assertEqual(
            sql,
            "(q.date BETWEEN '2025-04-01' AND '2025-06-30'"
            " OR q.date BETWEEN '2026-04-01' AND '2026-06-30')",
        )
        self.assertNotIn("2025-09", sql)


if __name__ == "__main__":
    unittest.main()
