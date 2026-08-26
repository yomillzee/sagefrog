"""Campaign explorer's "metrics over time" chart: the summary cards drive it.

Since this is now the explorer pane's only trend chart, it carries what the
retired "Paid trends" panel used to: the cards above the campaign tree are its
metric picker, and they multi-select — a card toggles its metric onto or off the
chart rather than replacing the selection, so Spend and Clicks can be read
against each other on one timeline. Three promises a reader would notice
breaking:

* Clicking a second card *adds* a line; clicking the last active one is a no-op
  rather than an empty chart.
* Metrics of different magnitudes each ride their own auto-scaled axis, and the
  legend carries every selected metric's number for the window.
* The chart is simply there — no collapse toggle standing between the cards and
  the line they describe.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class ExplorerTrendChartTests(unittest.TestCase):
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

    def _html(self) -> str:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=True,
        )

    # ---- The cards are the multi-select ----

    def test_the_cards_announce_themselves_as_a_multi_select(self) -> None:
        html = self._html()
        self.assertIn(
            'id="explorerSummaryCards" style="margin-bottom:14px" role="group"',
            html,
        )
        self.assertIn("pick one or more", html)
        # Toggle semantics, not radio: every card reports its own pressed state.
        self.assertIn('aria-pressed="${active?\'true\':\'false\'}"', html)

    def test_a_card_toggles_its_metric_instead_of_replacing_the_selection(self) -> None:
        html = self._html()
        self.assertIn("let explorerTrendMetrics = new Set(['spend']);", html)
        self.assertIn("if (explorerTrendMetrics.size===1) return; explorerTrendMetrics.delete(k);", html)
        self.assertIn("else explorerTrendMetrics.add(k);", html)
        # Every card repaints from the set, so two can read as active at once.
        self.assertIn("const a=explorerTrendMetrics.has(b.dataset.metric);", html)

    def test_the_selection_keeps_the_declared_metric_order(self) -> None:
        # Clicking Clicks then Spend must still draw Spend first, so the series,
        # legend and colours line up with the cards' own left-to-right order.
        html = self._html()
        self.assertIn(
            "const defs=EXPLORER_TREND_METRICS.filter(m=>explorerTrendMetrics.has(m.key));",
            html,
        )
        self.assertIn("return defs.length?defs:[EXPLORER_TREND_METRICS[0]];", html)

    def test_hiding_the_ga4_card_drops_it_from_the_selection(self) -> None:
        # The GA4-verified switch can take that card away; the chart must not
        # keep plotting a metric no card shows as active, and must not end up
        # with an empty selection either.
        html = self._html()
        self.assertIn("if (!showVerifiedConv) explorerTrendMetrics.delete('verified');", html)
        self.assertIn("if (!explorerTrendMetrics.size) explorerTrendMetrics.add('spend');", html)

    # ---- What the chart does with more than one metric ----

    def test_every_selected_metric_gets_its_own_hidden_axis(self) -> None:
        html = self._html()
        trend = html.split("---- Campaign Explorer: metrics over time ----", 1)[1]
        self.assertIn("const axisId=i===0?'y':('y'+i);", trend)
        self.assertIn("if (i>0) extraScales[axisId]={display:false, beginAtZero:true};", trend)
        # Tick labels and the area fill only make sense with one metric drawn.
        self.assertIn("yDisplay: !multi,", trend)
        self.assertIn("fill:!multi,", trend)

    def test_the_legend_totals_every_selected_metric(self) -> None:
        html = self._html()
        trend = html.split("---- Campaign Explorer: metrics over time ----", 1)[1]
        self.assertIn("legend.innerHTML=defs.map(m=>", trend)
        self.assertIn("explorerTrendRoll(rows,m)", trend)
        # CTR is re-derived from the window's parts: the mean of daily CTRs is
        # not the window's CTR.
        self.assertIn("return t.impressions?t.clicks/t.impressions*100:0;", trend)

    # ---- The panel itself ----

    def test_the_chart_is_not_behind_a_collapse_toggle(self) -> None:
        # It reads as one thing with the cards above it, so there is nothing to
        # expand and no remembered collapsed state to come back to.
        html = self._html()
        self.assertIn('<span class="expl-trend-title" id="explorerTrendTitle">', html)
        self.assertNotIn("explorerTrendToggle", html)
        self.assertNotIn("expl-trend-toggle", html)
        self.assertNotIn("aria-controls=\"explorerTrendBody\"", html)
        self.assertNotIn("ce_expl_trend_collapsed", html)

    def test_the_panel_title_follows_the_selection(self) -> None:
        html = self._html()
        self.assertIn("if (defs.length===1) return `${defs[0].label} over time`;", html)
        self.assertIn("if (defs.length<=3) return `${defs.map(m=>m.label).join(' · ')} over time`;", html)
        self.assertIn("return `${defs.length} metrics over time`;", html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
