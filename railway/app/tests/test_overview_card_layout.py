from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# On the BigQuery-mart dashboard's Overview home, an admin can enter "edit mode"
# to hide a card, show a hidden one back, or drag to reorder. The layout is
# stored per-client in client_dashboard_config.card_layouts and applied
# server-side on render. Crucially, a card an admin hides is NOT emitted for a
# client at all — only admins keep it in the DOM (greyed, editable) — so the
# client can't tell anything was hidden.


def _overview_headings(html: str) -> list[str]:
    """Headings of the Overview panels, in render order."""
    pane = html.split('id="pane-overview"', 1)[1]
    pane = pane.split('id="pane-explorer"', 1)[0] if 'id="pane-explorer"' in pane else pane
    heads = [h.strip() for h in re.findall(r"<h2>(?:<span[^>]*></span>)?([^<]+)</h2>", pane)]
    return [h for h in heads if h != "Get your dashboard connected"]


def _card_order(html: str) -> list[str]:
    """The stable card keys, in render order, from the ov-unit wrappers."""
    pane = html.split('id="pane-overview"', 1)[1]
    pane = pane.split('id="pane-explorer"', 1)[0] if 'id="pane-explorer"' in pane else pane
    return re.findall(r'class="ov-unit[^"]*" data-ov-card="([^"]+)"', pane)


class OverviewCardLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        # GA4/GSC-only client, no paid ads. GA4 + GSC are actually connected so
        # the Search Console card is present to order/hide — it's connector-gated
        # now (see dashboard_sidebar_view_nav_html), and an empty list would mean
        # a client with no connectors at all, which doesn't get that card.
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
            card_layouts=({"overview": layout} if layout else {}),
        )
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=is_admin,
        )

    def test_hidden_card_absent_for_client(self) -> None:
        html = self._render({"order": [], "hidden": ["ai_traffic"]}, is_admin=False)
        self.assertNotIn("AI traffic", _overview_headings(html))
        self.assertNotIn('data-ov-card="ai_traffic"', html)
        # The other cards still render normally.
        self.assertIn("Website analytics", _overview_headings(html))

    def test_hidden_card_kept_for_admin_but_greyed(self) -> None:
        html = self._render({"order": [], "hidden": ["ai_traffic"]}, is_admin=True)
        # Admin keeps the card in the DOM so it can be shown back from edit mode.
        self.assertIn('data-ov-card="ai_traffic"', html)
        self.assertIn("ov-unit ov-unit--hidden", html)
        # Exactly the AI traffic unit is flagged hidden.
        self.assertIn('class="ov-unit ov-unit--hidden" data-ov-card="ai_traffic"', html)

    def test_stored_order_reorders_cards(self) -> None:
        layout = {"order": ["gsc", "ai_traffic", "website"], "hidden": []}
        order = _card_order(self._render(layout, is_admin=True))
        self.assertEqual(order[:3], ["gsc", "ai_traffic", "website"])

    def test_unknown_keys_ignored(self) -> None:
        html = self._render({"order": ["nope"], "hidden": ["nope"]}, is_admin=True)
        # Natural order is preserved and nothing is hidden.
        self.assertEqual(
            _overview_headings(html)[:3],
            ["Website analytics", "AI traffic", "Search Console"],
        )
        self.assertNotIn('class="ov-unit ov-unit--hidden"', html)

    def test_edit_entry_and_banner_present(self) -> None:
        html = self._render(None, is_admin=True)
        # Edit mode is entered from the sidebar kebab, not an always-on toolbar.
        self.assertIn('class="dash-view-kebab"', html)
        self.assertIn('data-action="edit-layout"', html)
        # The in-edit banner carries the persistence endpoint.
        self.assertIn('class="ov-editing-banner"', html)
        self.assertIn("/api/clients/test/tabs/overview/card-layout", html)
        self.assertIn('id="ovEditDone"', html)
        # The old pin control is gone.
        self.assertNotIn("data-ov-pin", html)
        self.assertNotIn("ov-pin", html)

    def test_kebab_on_editable_nav_items(self) -> None:
        html = self._render(None, is_admin=True)
        # Overview, Campaign Explorer and Search Console are the editable nav
        # items, each wrapped with a kebab offering the same "Edit layout" entry.
        self.assertIn('data-view-item="overview"', html)
        self.assertIn('data-edit-tab="overview"', html)
        self.assertIn('data-view-item="explorer"', html)
        self.assertIn('data-edit-tab="explorer"', html)
        self.assertIn('data-view-item="gsc"', html)
        self.assertIn('data-edit-tab="gsc"', html)
        self.assertEqual(html.count('data-action="edit-layout"'), 3)
        # The Explorer's old budget-tracker toggle is gone from the menu — the
        # budget panel is hidden/shown from Explorer edit mode instead.
        self.assertNotIn('data-action="toggle-budget"', html)
        # A non-editable tab (Website Analytics) stays a plain nav button.
        self.assertNotIn('data-view-item="analytics"', html)
        self.assertIn('data-tab="analytics"', html)

    def test_wrapper_keys_are_known_cards(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            OVERVIEW_PINNABLE_CARDS,
        )
        html = self._render(None, is_admin=True)
        for key in _card_order(html):
            self.assertIn(key, OVERVIEW_PINNABLE_CARDS)


class NormalizeCardLayoutsTests(unittest.TestCase):
    def test_shapes_and_dedupe(self) -> None:
        from client_dashboard_config import _normalize_card_layouts
        out = _normalize_card_layouts(
            {"overview": {"order": ["a", "a", " b ", ""], "hidden": ["c"]},
             "bogus": "nope", "empty": {"order": [], "hidden": []}}
        )
        self.assertEqual(out["overview"], {"order": ["a", "b"], "hidden": ["c"]})
        self.assertNotIn("bogus", out)
        self.assertNotIn("empty", out)

    def test_json_string_and_garbage(self) -> None:
        from client_dashboard_config import _normalize_card_layouts
        self.assertEqual(_normalize_card_layouts(None), {})
        self.assertEqual(_normalize_card_layouts("not json"), {})
        self.assertEqual(_normalize_card_layouts([1, 2]), {})
        self.assertEqual(
            _normalize_card_layouts('{"overview": {"hidden": ["x"]}}'),
            {"overview": {"order": [], "hidden": ["x"]}},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
