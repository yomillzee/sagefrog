from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The Campaign Explorer gets the same admin layout editor as the Overview home:
# the sidebar kebab's "Edit layout" enters edit mode on #pane-explorer, where the
# panels (Campaign explorer / Keyword Performance / Budget tracking) can be
# hidden, shown, or dragged to reorder. Order + hidden live per-client in
# client_dashboard_config.card_layouts under the "explorer" key, exactly like
# Overview's — with one exception: the budget tracker's visibility stays in its
# own long-standing setting (explorer_budget_tracker, the settings page's "Show
# on Explorer" toggle), so the two stores never disagree about it.


def _explorer_pane(html: str) -> str:
    pane = html.split('id="pane-explorer"', 1)[1]
    return pane.split('id="pane-analytics"', 1)[0]


def _panel_order(html: str) -> list[str]:
    """The stable panel keys, in render order, from the ov-unit wrappers."""
    return re.findall(
        r'class="ov-unit[^"]*" data-ov-card="([^"]+)"', _explorer_pane(html)
    )


class ExplorerCardLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        # A paid-ads client, so the budget tracker is in play alongside the
        # explorer table and the keyword panel.
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type="ga4", status="connected"),
            types.SimpleNamespace(connector_type="google_ads", status="connected"),
        ]
        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg

    def _render(
        self,
        layout: dict | None,
        *,
        is_admin: bool,
        budget_on: bool = True,
    ) -> str:
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=None,
            card_layouts=({"explorer": layout} if layout else {}),
        )
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=is_admin,
            show_budget_tracker=budget_on,
        )

    def test_panels_are_wrapped_in_natural_order(self) -> None:
        order = _panel_order(self._render(None, is_admin=True))
        self.assertEqual(order, ["explorer", "keywords", "lidemo", "budget"])

    def test_wrapper_keys_are_known_panels(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            EXPLORER_LAYOUT_CARDS,
        )
        for key in _panel_order(self._render(None, is_admin=True)):
            self.assertIn(key, EXPLORER_LAYOUT_CARDS)

    def test_stored_order_reorders_panels(self) -> None:
        layout = {"order": ["budget", "keywords", "lidemo", "explorer"], "hidden": []}
        order = _panel_order(self._render(layout, is_admin=True))
        self.assertEqual(order, ["budget", "keywords", "lidemo", "explorer"])

    def test_panel_not_named_in_stored_order_keeps_its_natural_place(self) -> None:
        # A layout saved before the LinkedIn audience panel existed must not drop
        # it: unnamed panels follow the ordered ones rather than disappearing.
        layout = {"order": ["budget", "explorer"], "hidden": []}
        order = _panel_order(self._render(layout, is_admin=True))
        self.assertEqual(order, ["budget", "explorer", "keywords", "lidemo"])

    def test_hidden_panel_absent_for_client(self) -> None:
        html = self._render({"order": [], "hidden": ["keywords"]}, is_admin=False)
        self.assertNotIn('data-ov-card="keywords"', _explorer_pane(html))
        self.assertNotIn("Keyword Performance", _explorer_pane(html))
        # The other panels still render normally.
        self.assertIn('data-ov-card="explorer"', _explorer_pane(html))

    def test_hidden_panel_kept_for_admin_but_greyed(self) -> None:
        html = self._render({"order": [], "hidden": ["keywords"]}, is_admin=True)
        self.assertIn(
            'class="ov-unit ov-unit--hidden" data-ov-card="keywords"',
            _explorer_pane(html),
        )

    def test_unknown_keys_ignored(self) -> None:
        html = self._render({"order": ["nope"], "hidden": ["nope"]}, is_admin=True)
        self.assertEqual(
            _panel_order(html), ["explorer", "keywords", "lidemo", "budget"]
        )
        self.assertNotIn('class="ov-unit ov-unit--hidden"', _explorer_pane(html))

    def test_edit_entry_and_banner_present(self) -> None:
        html = self._render(None, is_admin=True)
        # Edit mode is entered from the sidebar kebab, same as Overview.
        self.assertIn('data-view-item="explorer"', html)
        self.assertIn('data-edit-tab="explorer"', html)
        self.assertIn('data-edit-pane="explorer"', html)
        # The in-edit banner carries this tab's persistence endpoint.
        self.assertIn("/api/clients/test/tabs/explorer/card-layout", html)
        self.assertIn('id="exEditDone"', html)

    # ---- Budget tracker: its own visibility store, ordered with the rest ----

    def test_budget_off_is_hidden_for_admin_and_gone_for_client(self) -> None:
        admin = self._render(None, is_admin=True, budget_on=False)
        # The admin keeps it, flagged hidden, so it can be shown back from edit mode.
        self.assertIn(
            'class="ov-unit ov-unit--hidden" data-ov-card="budget"',
            _explorer_pane(admin),
        )
        client = self._render(None, is_admin=False, budget_on=False)
        self.assertNotIn('data-ov-card="budget"', _explorer_pane(client))
        self.assertNotIn('id="sec-budget"', client)

    def test_budget_setting_beats_a_stale_layout_hidden_entry(self) -> None:
        # A stored hidden entry for "budget" never speaks for it — the setting does.
        html = self._render(
            {"order": [], "hidden": ["budget"]}, is_admin=False, budget_on=True
        )
        self.assertIn('data-ov-card="budget"', _explorer_pane(html))
        self.assertNotIn("ov-unit--hidden", _explorer_pane(html))


class ExplorerLayoutEndpointTests(unittest.TestCase):
    def test_explorer_is_a_layout_editable_tab(self) -> None:
        from dashboard.routes.api_routes import _card_layout_tab_keys
        from dashboard.renderers.bigquery_dashboard_renderer import (
            EXPLORER_LAYOUT_CARDS,
        )
        self.assertEqual(_card_layout_tab_keys("explorer"), set(EXPLORER_LAYOUT_CARDS))
        self.assertIsNone(_card_layout_tab_keys("analytics"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
