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


class CustomRangeTest(unittest.TestCase):
    def test_picker_offers_a_custom_range_row_and_form(self):
        html = _render()
        self.assertIn('id="rangeCustomOpen"', html)
        self.assertIn("Custom range", html)
        self.assertIn('<input type="date" id="rangeCustomStart">', html)
        self.assertIn('<input type="date" id="rangeCustomEnd">', html)
        self.assertIn('id="rangeCustomApply"', html)

    def test_custom_row_is_not_offered_as_a_savable_preset(self):
        """It carries no data-preset, so it can't reach the default-range API
        (which only accepts the named presets) via "Make default"."""
        html = _render()
        self.assertNotIn('data-preset="custom"', html)
        self.assertNotIn("custom", cdc.DATE_RANGE_PRESETS)
        self.assertIn("chk.disabled = name === 'custom';", html)

    def test_custom_range_sets_the_window_and_its_comparison_period(self):
        html = _render()
        self.assertIn("function applyCustomRange(startIso, endIso)", html)
        # Same-length window immediately before the picked one, so every
        # "vs previous" figure keeps working on a hand-picked range.
        self.assertIn("const days=Math.round((e-s)/86400000)+1;", html)
        self.assertIn("resolveCompare();", html)
        self.assertIn("syncRangeUI('custom');", html)

    def test_custom_dates_are_parsed_as_local_not_utc(self):
        """new Date('2026-08-01') is UTC midnight -- west of Greenwich that is
        July 31 locally, which would shift the whole window by a day."""
        html = _render()
        self.assertIn("function parseIsoDate(iso)", html)
        self.assertIn("new Date(p[0], p[1]-1, p[2])", html)

    def test_toggle_label_spells_out_a_custom_window(self):
        html = _render()
        self.assertIn("if (name === 'custom') lbl.textContent =", html)


class CompareSwitchTest(unittest.TestCase):
    def test_comparison_is_one_switch_and_starts_off(self):
        """A delta under every number is noise until someone asks for it, so the
        page ships comparisons off and offers one control to turn them on."""
        html = _render()
        self.assertIn('<input type="checkbox" id="compareSwitch" role="switch"', html)
        self.assertIn(
            '<span class="cmp-switch-text" id="compareSwitchLabel">Compare to previous period</span>',
            html,
        )
        self.assertIn("let compareOn=false;", html)
        # Off blanks the window, which is what every delta and every
        # comparison-period fetch on the page is gated on.
        self.assertIn("if (!compareOn) {", html)
        # The retired dropdown is gone, not just hidden.
        self.assertNotIn('id="compareDropdown"', html)
        self.assertNotIn('id="compareToggleLabel"', html)

    def test_window_choice_is_secondary_and_hidden_until_comparing(self):
        """Previous year stays available, as a quiet action next to the switch
        that names the window it moves to rather than the one in use."""
        html = _render()
        self.assertIn(
            '<button type="button" class="cmp-window" id="compareWindowBtn" hidden>Use previous year</button>',
            html,
        )
        self.assertIn("win.hidden=!compareOn;", html)
        self.assertIn("setCompareMode(compareMode==='prev_year'?'prev_period':'prev_year')", html)
        # The switch's own label follows the window, so it can never claim to be
        # comparing against the period while comparing against last year.
        self.assertIn("'Compare to previous year' : 'Compare to previous period'", html)

    def test_both_choices_are_remembered_per_client(self):
        for slug in ("demo", "acme"):
            html = _render(client_slug=slug)
            self.assertIn("'sf.compareMode.%s'" % slug, html)
            self.assertIn("'sf.compareOn.%s'" % slug, html)

    def test_the_retired_no_comparison_mode_reads_as_off(self):
        """Whoever picked "No comparison" on the old picker gets what they asked
        for, not a window built out of the string 'none'."""
        html = _render()
        self.assertIn("if (savedMode==='none') compareOn=false;", html)
        self.assertNotIn('data-cmp="none"', html)

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


class MeaningfulMovementTest(unittest.TestCase):
    def test_colour_is_reserved_for_movement_worth_reacting_to(self):
        """Every delta keeps its arrow; only a big enough move on a metric with a
        good direction earns red or green."""
        html = _render()
        self.assertIn("const MEANINGFUL_DELTA_PCT = 10;", html)
        self.assertIn("const meaningful=Math.abs(ch)>=MEANINGFUL_DELTA_PCT;", html)
        self.assertIn("if (meaningful) {", html)

    def test_ctr_is_never_coloured_by_direction(self):
        """A CTR dip is what broadening reach looks like -- fine if conversions
        came with it -- so CTR is reported, not judged."""
        html = _render()
        self.assertIn("['ctr','CTR',pct,'neutral']", html)
        self.assertIn("ctr:'neutral'", html)
        self.assertNotIn("ctr:'up'", html)

    def test_row_deltas_are_smaller_than_the_figures_they_qualify(self):
        html = _render()
        self.assertIn("#explorerTable .expl-row-delta .cmp-delta { font-size:.62rem;", html)


class ExplorerHierarchyTest(unittest.TestCase):
    """Three levels have to stay legible whether the tree is collapsed or fully
    expanded, so depth is carried by four cues rather than font weight alone."""

    def test_each_level_indents_further(self):
        html = _render()
        self.assertIn(".indent1 { display:inline-block; width:24px; }", html)
        self.assertIn(".indent2 { display:inline-block; width:48px; }", html)

    def test_type_gets_smaller_and_quieter_going_down(self):
        html = _render()
        self.assertIn(".lvl-campaign .tree-name { font-weight:800; font-size:.85rem;", html)
        self.assertIn(".lvl-group .tree-name { font-weight:700; font-size:.78rem;", html)
        self.assertIn(".lvl-ad > td { font-size:.74rem; }", html)

    def test_nested_rows_carry_a_depth_rail(self):
        html = _render()
        self.assertIn(".lvl-group > td.left { box-shadow:inset 3px 0 0", html)
        self.assertIn(".lvl-ad > td.left { color:var(--muted); box-shadow:inset 3px 0 0", html)

    def test_only_a_campaign_after_a_nested_row_gets_a_separator(self):
        """In the default all-collapsed view every row is a campaign; a rule
        between each of them would just be a heavier grid."""
        html = _render()
        self.assertIn(
            ".tree-row:not(.lvl-campaign) + .lvl-campaign > td { border-top:2px solid var(--line); }",
            html,
        )


class MovementColumnsFollowTheSwitchTest(unittest.TestCase):
    """Search Console measures movement server-side, so with comparison off
    those columns have nothing to show -- and a column of dashes is worse than
    no column."""

    def test_gsc_delta_column_only_exists_while_comparing(self):
        html = _render()
        self.assertIn("(which==='pages' || !compareStart) ? GSC_SORT_COLS", html)
        # Sorting by a column that just disappeared falls back to the default.
        self.assertIn(
            "if (!cols.some(c => c.key === st.sortKey)) { st.sortKey='clicks'; st.sortDir='desc'; }",
            html,
        )

    def test_watchlist_and_keyword_leaders_hide_theirs_too(self):
        html = _render()
        self.assertIn("+ (compareStart ? th('delta_position','\u0394 Pos'", html)
        self.assertIn("${compareStart?'<th>Movement</th>':''}", html)


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
