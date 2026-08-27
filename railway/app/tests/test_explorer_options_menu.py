"""Campaign explorer head: the options kebab and the GA4-verified switch.

One promise a reader of the page would notice breaking: everything that
*configures* the explorer (the admin Campaigns allowlist and filter-chip
editors) plus the GA4-verified switch sits behind one kebab, so the section head
keeps only the Platform chips and the row count. The switch is for every viewer
— it decides whether the GA4 column and card are drawn, which is a reading
preference, not client config — while the two editors stay admin-only.

The multi-select those chips used to test now lives on the explorer's summary
cards; see test_explorer_trend_chart.py.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _explorer_head(html: str) -> str:
    """The Campaign explorer section head, up to its cards container."""
    head = html.split("<h2>Campaign explorer</h2>", 1)[1]
    return head.split('id="explorerSummaryCards"', 1)[0]


class ExplorerOptionsMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type="ga4", status="connected"),
            types.SimpleNamespace(connector_type="google_ads", status="connected"),
        ]
        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=None,
            card_layouts={},
        )

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg

    def _html(self, *, admin: bool) -> str:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=admin,
        )

    # ---- The kebab ----

    def test_head_keeps_only_the_platform_filter_and_the_status(self) -> None:
        head = _explorer_head(self._html(admin=True))
        self.assertIn('id="explorerPlatformChips"', head)
        self.assertIn('id="explorerStatus"', head)
        self.assertIn('id="explorerAdvBtn"', head)
        # The editors are no longer loose in the head — they are menu rows.
        menu = head.split('id="explorerAdvPop"', 1)[1]
        self.assertIn('id="ecEditBtn"', menu)
        self.assertIn('id="efEditBtn"', menu)

    def test_the_admin_editors_are_still_admin_only(self) -> None:
        head = _explorer_head(self._html(admin=False))
        # A client keeps the kebab (the GA4 switch lives there) but gets none of
        # the configuration rows.
        self.assertIn('id="explorerAdvBtn"', head)
        self.assertIn('id="explorerVerifiedToggle"', head)
        self.assertNotIn('id="ecEditBtn"', head)
        self.assertNotIn('id="efEditBtn"', head)

    def test_the_menu_closes_on_outside_click_and_escape(self) -> None:
        html = self._html(admin=True)
        self.assertIn("btn.addEventListener('click',(e)=>{ e.stopPropagation(); setOpen(pop.hidden); });", html)
        self.assertIn("if (e.key==='Escape') setOpen(false);", html)

    # ---- GA4-verified switch ----

    def test_the_switch_drops_the_column_from_every_row_level(self) -> None:
        html = self._html(admin=False)
        # One column list feeds the header, every row level and the footer, so
        # turning the switch off cannot leave a table with a ragged column count.
        self.assertIn(
            "return showVerifiedConv ? METRIC_COLS : METRIC_COLS.filter(c=>c.key!=='verified_sel');",
            html,
        )
        self.assertIn("return metricCols().map(c=>{", html)
        self.assertIn("${metricCols().map(c=>`<th class=\"expl-sort", html)

    def test_the_switch_drops_the_summary_card_too(self) -> None:
        html = self._html(admin=False)
        self.assertIn(
            "+ (showVerifiedConv ? (()=>{",
            html,
        )
        self.assertIn(
            "<div class=\"card-title\">Verified conv. (GA4)</div>",
            html,
        )

    def test_the_switch_is_remembered_per_browser_and_defaults_on(self) -> None:
        html = self._html(admin=False)
        self.assertIn("const EXPLORER_VERIFIED_PREF_KEY = 'sf.explorer.verifiedConv';", html)
        self.assertIn("showVerifiedConv = localStorage.getItem(EXPLORER_VERIFIED_PREF_KEY) !== '0';", html)

    def test_hiding_the_column_gives_up_sorting_by_it(self) -> None:
        # Otherwise the sort arrow points at a column that is no longer drawn.
        html = self._html(admin=False)
        self.assertIn(
            "if (!showVerifiedConv && explorerSort.key==='verified_sel') explorerSort={key:'spend',dir:'desc'};",
            html,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
