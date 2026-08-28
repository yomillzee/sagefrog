from __future__ import annotations

import json
import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The Events dropdown on Website Analytics decides which GA4 events count as
# "key events" for Pages / Landing pages / Traffic / User acquisition. The
# selection is saved per client (admin-only "Save as default"), read back on the
# next page load, and falls back to GA4's own key-event flags when empty.


def _render(*, ga4_key_events: str | None, is_admin: bool) -> str:
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
        ga4_key_events=ga4_key_events, explorer_filters=None,
        analytics_page_path_filter=None,
        monthly_budget_usd=None, overview_pinned_card=None,
    )
    try:
        from dashboard.renderers.bigquery_dashboard_renderer import (
            render_bigquery_dashboard_page,
        )
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True, session_is_admin=is_admin,
        )
    finally:
        ccs.list_configs = orig_list
        cdc.get_config = orig_cfg


def _saved(html: str) -> list:
    m = re.search(r"const GA4_KEY_EVENTS_SAVED = (\[.*?\]);", html)
    assert m, "GA4_KEY_EVENTS_SAVED constant not found"
    return json.loads(m.group(1))


class KeyEventDefaultTests(unittest.TestCase):
    def test_saved_events_seed_the_selection(self) -> None:
        html = _render(ga4_key_events="form_submit\ngenerate_lead", is_admin=False)
        self.assertEqual(_saved(html), ["form_submit", "generate_lead"])
        # The dropdown starts from the saved set rather than GA4's own flags.
        self.assertIn("selectedKeyEvents=new Set(GA4_KEY_EVENTS_SAVED)", html)

    def test_no_saved_events_falls_back_to_ga4_key_events(self) -> None:
        html = _render(ga4_key_events=None, is_admin=True)
        self.assertEqual(_saved(html), [])
        self.assertIn("!GA4_KEY_EVENTS_SAVED.length && keyEventKeys.size", html)

    def test_admin_sees_save_button_client_does_not(self) -> None:
        admin = _render(ga4_key_events=None, is_admin=True)
        self.assertIn('id="keyEventSaveDefault"', admin)
        self.assertIn("/ga4/key-events", admin)
        # Newline-joined names, which is how the endpoint stores the list.
        self.assertIn(r"names.join('\n')", admin)

        client = _render(ga4_key_events=None, is_admin=False)
        self.assertNotIn('id="keyEventSaveDefault"', client)
        # The dropdown itself stays — a client can still filter their own view.
        self.assertIn('id="keyEventDropdown"', client)


if __name__ == "__main__":
    unittest.main()
