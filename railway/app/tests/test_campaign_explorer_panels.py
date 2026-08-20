"""Campaign Explorer panels: paid trends, keyword scoping, LinkedIn audience.

Three separate promises, all of them things a reader of the page would notice
going wrong rather than implementation detail:

* The **paid trends** chart draws only the timeline events scoped to ads, so a
  site migration marked "analytics trends" never turns up explaining a spend
  line.
* **Keyword Performance** is sliced by the same Platform chips, filter-group
  dropdowns and campaign allowlist as the tree above it — the table used to
  contradict the one it sits under — and it names the window it is showing.
* The **LinkedIn audience** panel exports every breakdown in the synced window
  as one CSV and leads with summary cards for the breakdown on screen.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _explorer_pane(html: str) -> str:
    pane = html.split('id="pane-explorer"', 1)[1]
    return pane.split('id="pane-analytics"', 1)[0]


class ExplorerPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type="ga4", status="connected"),
            types.SimpleNamespace(connector_type="google_ads", status="connected"),
            types.SimpleNamespace(connector_type="linkedin_ads", status="connected"),
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

    def _html(self) -> str:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=True,
        )

    # ---- Paid trends ----

    def test_paid_trends_panel_lives_on_the_explorer_pane(self) -> None:
        pane = _explorer_pane(self._html())
        self.assertIn('id="sec-paid_trends"', pane)
        self.assertIn('id="paidTrendChart"', pane)
        self.assertIn('id="paidTrendMetricChips"', pane)
        # Daily/Weekly, same toggle the GA4 trends carry.
        self.assertIn('id="paidTrendGranChips"', pane)

    def test_paid_trends_cycles_the_metrics_the_explorer_tree_shows(self) -> None:
        html = self._html()
        for metric in ("spend", "impressions", "clicks", "conversions", "ctr", "cpc"):
            self.assertIn(f"key:'{metric}'", html, metric)

    def test_paid_trends_draws_only_the_ads_scoped_timeline_events(self) -> None:
        html = self._html()
        self.assertIn("annoScope: 'ads'", html)
        # The chart helpers thread the scope into both halves of a marker (the
        # canvas rule and the DOM pin), or one half would ignore it.
        self.assertIn("annoLinePlugin(opts.dates, opts.annoScope)", html)
        self.assertIn("syncAnnoPins(id, opts.dates, opts.annoScope)", html)

    def test_annotation_scope_defaults_to_showing_everywhere(self) -> None:
        # An event with no stored scope (every event saved before the field
        # existed) has to keep drawing on every chart.
        html = self._html()
        self.assertIn("const on = String(a.charts || 'both');", html)
        self.assertIn("return on === 'both' || on === scope;", html)

    # ---- Keyword Performance ----

    def test_keyword_panel_names_the_window_it_is_showing(self) -> None:
        pane = _explorer_pane(self._html())
        self.assertIn('id="keywordWindow"', pane)

    def test_keyword_rows_are_sliced_by_the_explorer_filters(self) -> None:
        html = self._html()
        # The platform chips, the filter-group dropdowns and the campaign
        # allowlist all reach the keyword table through kwScoped().
        self.assertIn("function kwScoped()", html)
        self.assertIn("function kwPlatformIncluded()", html)
        self.assertIn("function kwCampaignAllowed(r)", html)
        self.assertIn("return kwAllRows.filter(kwCampaignAllowed);", html)
        # And every path that re-renders the tree re-renders the table with it.
        self.assertIn("if (kwAllRows.length) renderKeywords();", html)
        self.assertIn("renderPaidTrends();", html)

    def test_keyword_insight_and_chips_read_the_same_scoped_rows(self) -> None:
        # A banner counting keywords the table is filtering out is worse than no
        # banner: it reads as a contradiction rather than a caveat.
        html = self._html()
        self.assertIn("const bySpend=kwScoped()", html)
        self.assertIn("const wasteful=kwScoped()", html)
        self.assertIn("const total=kwScoped().length;", html)

    # ---- LinkedIn audience ----

    def test_linkedin_audience_panel_offers_a_csv_export(self) -> None:
        pane = _explorer_pane(self._html())
        self.assertIn('id="lidemoExport"', pane)
        html = self._html()
        self.assertIn("function downloadCsv(", html)
        self.assertIn("function lidemoCsvRows()", html)
        # One file, every breakdown, labelled with the window it came from.
        for column in ("Window", "Breakdown", "Category", "Impressions", "CTR %"):
            self.assertIn(f"'{column}'", html, column)

    def test_linkedin_audience_panel_leads_with_summary_cards(self) -> None:
        pane = _explorer_pane(self._html())
        self.assertIn('id="lidemoCards"', pane)
        html = self._html()
        self.assertIn("function renderLidemoCards()", html)
        # The cards name the dimension on screen rather than assuming companies.
        self.assertIn("LIDEMO_SINGULAR", html)
        # A CTR crown needs real reach behind it.
        self.assertIn("LIDEMO_CTR_MIN_IMPRESSIONS", html)


if __name__ == "__main__":
    unittest.main()
