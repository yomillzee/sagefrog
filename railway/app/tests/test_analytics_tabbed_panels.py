from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Website Analytics pairs its four breakdown panels into two full-width cards,
# each swapping between two panes on a tab:
#   Pages (default) / Landing Pages
#   Traffic acquisition (default) / New user acquisition
# The panes keep the ids the loaders and the module system already use, so what
# this guards is the wiring: one card per pair, the right tab open first, and
# the module system driving the tabs instead of the panes directly.


def _render(*, page_path_filter: str | None = None, is_admin: bool = True) -> str:
    import connector_config_store as ccs
    orig_list = ccs.list_configs
    ccs.list_configs = lambda slug: []  # GA4-only client, no paid ads

    import client_dashboard_config as cdc
    orig_cfg = cdc.get_config
    cdc.get_config = lambda slug: types.SimpleNamespace(
        dashboard_mode="bigquery_nixon", label="Test Co",
        gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
        gsc_branded_roots=None, gsc_target_keywords=None,
        gsc_branded_exclude=None, gsc_target_exclude=None,
        ga4_key_events=None, explorer_filters=None,
        analytics_page_path_filter=page_path_filter,
        monthly_budget_usd=None, overview_pinned_card=None,
    )
    try:
        from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=is_admin,
        )
    finally:
        ccs.list_configs = orig_list
        cdc.get_config = orig_cfg


def _tab(html: str, pane: str) -> str:
    m = re.search(rf'<button[^>]*id="tab-{re.escape(pane)}"[^>]*>', html)
    assert m, f"tab button for {pane} not found"
    return m.group(0)


def _pane(html: str, pane: str) -> str:
    m = re.search(rf'<div class="pnl-pane" id="{re.escape(pane)}"[^>]*>', html)
    assert m, f"pane {pane} not found"
    return m.group(0)


class AnalyticsTabbedPanelsTests(unittest.TestCase):
    def test_each_pair_ships_as_one_card(self) -> None:
        html = _render()
        for card in ('id="card-pages"', 'id="card-acq"'):
            self.assertIn(card, html)
        # The panes keep their ids — the loaders and MODULE_SECTIONS use them.
        for pane in ("sec-pages", "sec-landing", "sec-traffic", "sec-useracq"):
            self.assertIn(f'id="{pane}"', html)

    def test_default_tab_is_open_and_the_other_pane_is_hidden(self) -> None:
        html = _render()
        for first, second in (("sec-pages", "sec-landing"), ("sec-traffic", "sec-useracq")):
            self.assertIn('aria-selected="true"', _tab(html, first))
            self.assertIn('aria-selected="false"', _tab(html, second))
            self.assertNotIn("hidden", _pane(html, first))
            self.assertIn("hidden", _pane(html, second))

    def test_tabs_are_labelled_the_way_the_panels_were(self) -> None:
        html = _render()
        for label in ("Pages<", "Landing Pages<", "Traffic acquisition<", "New user acquisition<"):
            self.assertIn(label, html)

    def test_only_the_open_pane_shows_its_status(self) -> None:
        # Both statuses live in the shared card head; the second is hidden so the
        # head keeps a single line of status text.
        html = _render()
        self.assertIn('<span class="status" id="pagesStatus"></span>', html)
        self.assertIn('<span class="status" id="landingStatus" hidden></span>', html)
        self.assertIn('<span class="status" id="trafficAcqStatus"></span>', html)
        self.assertIn('<span class="status" id="userAcqStatus" hidden></span>', html)

    def test_pages_pair_is_no_longer_a_half_width_column(self) -> None:
        # Pages and Landing Pages used to sit side by side in a .two-col grid,
        # which is what squeezed the path column. They must not any more.
        html = _render()
        head = html.index('id="card-pages"')
        self.assertNotIn('two-col', html[head:html.index('id="card-acq"')])

    def test_modules_drive_the_tabs_not_the_panes(self) -> None:
        # applyModules hands the four tabbed modules to applyPanelCards, which
        # hides a tab whose module is off, falls back to the surviving pane, and
        # hides the whole card when both are off.
        html = _render()
        self.assertIn("applyPanelCards", html)
        m = re.search(r"const PANEL_CARDS = \{(.+?)const panelOpen", html, re.S)
        self.assertIsNotNone(m, "PANEL_CARDS not found")
        cfg = m.group(1)
        for mod in ("top_pages", "landing", "traffic", "user_acquisition"):
            self.assertIn(f"module:'{mod}'", cfg)


if __name__ == "__main__":
    unittest.main()
