from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Search Console gets the same admin layout editor as the Overview home and the
# Campaign Explorer: the sidebar kebab's "Edit layout" enters edit mode on
# #pane-gsc, where the panels can be hidden, shown or dragged to reorder. Order +
# hidden live per-client in client_dashboard_config.card_layouts under "gsc".
#
# The tab also folds Branded and Target queries into one tabbed card (the same
# .pnl-head / .pnl-tabs card the Website Analytics tab uses for Pages / Landing
# Pages), so each group reads at full width instead of half.


def _gsc_pane(html: str) -> str:
    pane = html.split('id="pane-gsc"', 1)[1]
    return pane.split("<!-- /pane-gsc -->", 1)[0]


def _panel_order(html: str) -> list[str]:
    """The stable panel keys, in render order, from the ov-unit wrappers."""
    return re.findall(r'class="ov-unit[^"]*" data-ov-card="([^"]+)"', _gsc_pane(html))


class GscCardLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type="ga4", status="connected"),
            types.SimpleNamespace(connector_type="gsc", status="connected"),
        ]
        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg

    def _render(self, layout: dict | None, *, is_admin: bool) -> str:
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=None,
            card_layouts=({"gsc": layout} if layout else {}),
        )
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=is_admin,
        )

    # ---- Layout editor ----

    def test_panels_are_wrapped_in_natural_order(self) -> None:
        order = _panel_order(self._render(None, is_admin=True))
        self.assertEqual(order[-4:], ["kpis", "tables", "watchlist", "keywords"])

    def test_wrapper_keys_are_known_panels(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import GSC_LAYOUT_CARDS
        for key in _panel_order(self._render(None, is_admin=True)):
            self.assertIn(key, GSC_LAYOUT_CARDS)

    def test_stored_order_reorders_panels(self) -> None:
        layout = {"order": ["keywords", "watchlist", "tables", "kpis"], "hidden": []}
        order = _panel_order(self._render(layout, is_admin=True))
        self.assertEqual(order[:4], ["keywords", "watchlist", "tables", "kpis"])

    def test_panel_not_named_in_stored_order_keeps_its_natural_place(self) -> None:
        layout = {"order": ["watchlist"], "hidden": []}
        order = _panel_order(self._render(layout, is_admin=True))
        self.assertEqual(order[0], "watchlist")
        self.assertEqual(sorted(order[1:]), sorted(set(order) - {"watchlist"}))

    def test_hidden_panel_absent_for_client(self) -> None:
        pane = _gsc_pane(self._render({"order": [], "hidden": ["watchlist"]}, is_admin=False))
        self.assertNotIn('data-ov-card="watchlist"', pane)
        self.assertNotIn('id="gscWatchTable"', pane)
        self.assertIn('data-ov-card="tables"', pane)

    def test_hidden_panel_kept_for_admin_but_greyed(self) -> None:
        pane = _gsc_pane(self._render({"order": [], "hidden": ["watchlist"]}, is_admin=True))
        self.assertIn('class="ov-unit ov-unit--hidden" data-ov-card="watchlist"', pane)

    def test_unknown_keys_ignored(self) -> None:
        html = self._render({"order": ["nope"], "hidden": ["nope"]}, is_admin=True)
        self.assertEqual(_panel_order(html)[-4:], ["kpis", "tables", "watchlist", "keywords"])
        self.assertNotIn('class="ov-unit ov-unit--hidden"', _gsc_pane(html))

    def test_edit_entry_and_banner_present(self) -> None:
        html = self._render(None, is_admin=True)
        self.assertIn('data-view-item="gsc"', html)
        self.assertIn('data-edit-tab="gsc"', html)
        self.assertIn('data-edit-pane="gsc"', html)
        self.assertIn("/api/clients/test/tabs/gsc/card-layout", html)
        self.assertIn('id="gscEditDone"', html)

    def test_api_accepts_the_gsc_tab(self) -> None:
        from dashboard.routes.api_routes import _card_layout_tab_keys
        from dashboard.renderers.bigquery_dashboard_renderer import GSC_LAYOUT_CARDS
        self.assertEqual(_card_layout_tab_keys("gsc"), set(GSC_LAYOUT_CARDS))


class GscKeywordTabsTests(unittest.TestCase):
    """Branded / Target queries share one tabbed card."""

    def setUp(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        self.html = render_bigquery_dashboard_page(
            client_slug="demo", api_client_key="demo", label="Demo",
            use_session=True, session_email="t@e.com",
        )

    def test_one_card_with_two_tabs(self) -> None:
        pane = _gsc_pane(self.html)
        self.assertIn('id="card-gsc-kw"', pane)
        self.assertIn('data-pnl="gsckw" data-pane="sec-gsc-branded"', pane)
        self.assertIn('data-pnl="gsckw" data-pane="sec-gsc-target"', pane)
        # Branded opens; Target waits behind its tab.
        self.assertIn('id="sec-gsc-branded" role="tabpanel"', pane)
        self.assertIn('id="sec-gsc-target" role="tabpanel"', pane)
        self.assertRegex(pane, r'id="sec-gsc-target"[^>]*hidden')

    def test_tables_charts_and_editors_kept_their_ids(self) -> None:
        pane = _gsc_pane(self.html)
        for el in (
            "gscBrandedTable", "gscTargetTable", "gscBrandedPager", "gscTargetPager",
            "gscBrandedTrendChart", "gscTargetTrendChart",
            "gscBrandedTags", "gscTargetTags",
            "gscBrandedExcludeTags", "gscTargetExcludeTags",
            "gscBrandedCount", "gscTargetCount", "gscKwStatus",
        ):
            self.assertIn(f'id="{el}"', pane)

    def test_edit_button_follows_the_open_tab(self) -> None:
        pane = _gsc_pane(self.html)
        self.assertIn('data-pnl-for="sec-gsc-branded" data-kw-edit="gscBrandedEditors"', pane)
        self.assertIn('data-pnl-for="sec-gsc-target" data-kw-edit="gscTargetEditors"', pane)

    def test_the_two_column_split_is_gone(self) -> None:
        self.assertNotIn('id="sec-gsc-keywords"', self.html)


if __name__ == "__main__":
    unittest.main()
