from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# On the BigQuery-mart dashboard's Overview home, admins reorder cards with
# drag-and-drop in edit mode (see test_overview_card_layout). The older
# per-client overview_pinned_card is kept as a UI-less ordering fallback: when a
# client has a stored pin but no explicit card_layouts order, that card is still
# floated to the top on render. These tests cover that fallback (no pin control
# is rendered anymore).


def _overview_headings(html: str) -> list[str]:
    """Headings of the Overview panels, in render order."""
    pane = html.split('id="pane-overview"', 1)[1]
    pane = pane.split('id="pane-explorer"', 1)[0] if 'id="pane-explorer"' in pane else pane
    # <h2> may lead with a decorative <span> (e.g. the MQL dot); strip it. The
    # first-run onboarding call-to-action is not a pinnable card, so drop it.
    heads = [h.strip() for h in re.findall(r"<h2>(?:<span[^>]*></span>)?([^<]+)</h2>", pane)]
    return [h for h in heads if h != "Get your dashboard connected"]


class OverviewPinnedCardTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        ccs.list_configs = lambda slug: []  # GA4/GSC-only client, no paid ads

        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        self._pinned: str | None = None
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=self._pinned,
        )

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg

    def _render(self, pinned: str | None) -> str:
        self._pinned = pinned
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=pinned,
        )
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=True,
        )

    def test_natural_order_when_nothing_pinned(self) -> None:
        heads = _overview_headings(self._render(None))
        self.assertEqual(heads[:3], ["Website analytics", "AI traffic", "Search Console"])
        self.assertNotIn("ov-pin is-pinned", self._render(None))

    def test_pinned_card_moves_to_top(self) -> None:
        html = self._render("gsc")
        heads = _overview_headings(html)
        self.assertEqual(heads[0], "Search Console")
        # The pin is now a UI-less legacy fallback (drag-and-drop replaced the pin
        # button), so it only reorders — there is no pin control in the markup.
        self.assertNotIn("data-ov-pin", html)

    def test_unknown_pinned_key_is_ignored(self) -> None:
        html = self._render("does-not-exist")
        heads = _overview_headings(html)
        self.assertEqual(heads[:3], ["Website analytics", "AI traffic", "Search Console"])
        self.assertNotIn("ov-pin is-pinned", html)

    def test_pin_controls_only_target_known_cards(self) -> None:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            OVERVIEW_PINNABLE_CARDS,
        )
        html = self._render(None)
        for key in re.findall(r'data-ov-pin="([^"]+)"', html):
            self.assertIn(key, OVERVIEW_PINNABLE_CARDS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
