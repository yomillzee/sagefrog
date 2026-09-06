"""Campaign Explorer panels: the trend chart, keyword scoping, LinkedIn audience.

Three separate promises, all of them things a reader of the page would notice
going wrong rather than implementation detail:

* The explorer's **metrics over time** chart draws only the timeline events
  scoped to ads, so a site migration marked "analytics trends" never turns up
  explaining a spend line. It is the pane's only trend chart — the stand-alone
  "Paid trends" panel that used to duplicate it is gone.
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
        from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=True,
        )

    # ---- Metrics over time ----

    def test_the_trend_chart_lives_between_the_cards_and_the_tree(self) -> None:
        pane = _explorer_pane(self._html())
        cards = pane.index('id="explorerSummaryCards"')
        chart = pane.index('id="explorerTrendChart"')
        table = pane.index('id="explorerTable"')
        self.assertLess(cards, chart)
        self.assertLess(chart, table)

    def test_the_pane_carries_no_second_trend_chart(self) -> None:
        # "Paid trends" was a second answer to the same question, on the same
        # pane, free to disagree with this one. It is gone.
        pane = _explorer_pane(self._html())
        self.assertNotIn("Paid trends", pane)
        self.assertNotIn('id="sec-paid_trends"', pane)
        self.assertNotIn("paidTrendChart", pane)

    def test_the_trend_chart_plots_the_metrics_the_summary_cards_show(self) -> None:
        html = self._html()
        for metric in ("spend", "impressions", "clicks", "ctr", "conversions", "verified"):
            self.assertIn(f"key:'{metric}'", html, metric)

    def test_the_trend_chart_draws_only_the_ads_scoped_timeline_events(self) -> None:
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
        self.assertIn("renderExplorerTrend();", html)

    def test_keyword_panel_has_no_insight_banner(self) -> None:
        # The banner restated what the table already showed, so it was dropped;
        # the row count under the table still reads the same scoped rows.
        html = self._html()
        self.assertNotIn('id="keywordInsight"', html)
        self.assertNotIn("kw-insight", html)
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

    def test_linkedin_audience_panel_has_no_summary_cards(self) -> None:
        # The "top company / most clicks / best CTR" cards are gone: the table
        # already ranks by impressions, so they only restated its first row.
        html = self._html()
        self.assertNotIn('id="lidemoCards"', html)
        self.assertNotIn("renderLidemoCards", html)

    def test_demographic_tables_show_ten_rows_with_a_show_all_toggle(self) -> None:
        pane = _explorer_pane(self._html())
        for host in ('id="lidemoMore"', 'id="gdemoMore"'):
            self.assertIn(host, pane)
        html = self._html()
        self.assertIn("const TABLE_TOP_N=10;", html)
        self.assertIn("function tableVisibleRows(", html)
        # Both demographic tables render through the limiter and own a toggle.
        for table in ("'lidemoTable'", "'gdemoTable'"):
            self.assertIn(f"tableVisibleRows({table},rows)", html, table)
        self.assertIn("renderTableMore('lidemoMore','lidemoTable'", html)
        self.assertIn("renderTableMore('gdemoMore','gdemoTable'", html)


if __name__ == "__main__":
    unittest.main()
