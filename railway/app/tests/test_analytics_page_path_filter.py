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

# The Website Analytics (GA4) view supports an admin-set, persisted page-path
# scope (e.g. a Nixon HR portal limited to /careers pages). When set, the shared
# renderer:
#   - shows the admin-only "Edit page filter" editor (hidden from clients),
#   - injects the patterns as ANALYTICS_PATH_FILTER for the page JS, and
#   - lists the site-wide modules the JS force-hides while a scope is active.
# The server-side scoping of the BigQuery reads is covered in
# test_marketing_page_path_filter.


def _render(*, page_path_filter: str | None, is_admin: bool) -> str:
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


def _injected_filter(html: str) -> list:
    m = re.search(r"const ANALYTICS_PATH_FILTER = (\[.*?\]);", html)
    assert m, "ANALYTICS_PATH_FILTER constant not found"
    return json.loads(m.group(1))


class AnalyticsPagePathFilterTests(unittest.TestCase):
    def test_admin_sees_editor_client_does_not(self) -> None:
        admin = _render(page_path_filter=None, is_admin=True)
        client = _render(page_path_filter=None, is_admin=False)
        # The editable control element is admin-only...
        self.assertIn('id="pfEditBtn"', admin)
        self.assertNotIn('id="pfEditBtn"', client)
        # ...but the JS constant + wiring ship to everyone so the scope applies.
        self.assertIn("const ANALYTICS_PATH_FILTER", admin)
        self.assertIn("const ANALYTICS_PATH_FILTER", client)

    def test_no_filter_injects_empty_list(self) -> None:
        self.assertEqual(_injected_filter(_render(page_path_filter="", is_admin=True)), [])
        self.assertEqual(_injected_filter(_render(page_path_filter=None, is_admin=True)), [])

    def test_configured_patterns_are_parsed_and_injected(self) -> None:
        html = _render(page_path_filter="/careers\n# comment\n\n  /jobs  ", is_admin=True)
        self.assertEqual(_injected_filter(html), ["/careers", "/jobs"])
        # The raw config prefills the editor textarea for editing.
        self.assertIn("/careers", html)

    def test_site_wide_modules_are_listed_for_hiding(self) -> None:
        html = _render(page_path_filter="/careers", is_admin=True)
        m = re.search(r"const PATH_FILTER_HIDDEN_MODULES = (\[[^\]]*\]);", html)
        self.assertIsNotNone(m, "PATH_FILTER_HIDDEN_MODULES not found")
        # The value is a JS array literal (single quotes); pull out the names.
        hidden = set(re.findall(r"'([^']+)'", m.group(1)))
        # Only the panels with no page_path to scope by are hidden.
        self.assertEqual(hidden, {"traffic", "audience", "user_acquisition"})
        # These are served page-scoped, so they stay visible under a filter.
        # Demographics stays for its geography half (vw_ga4_geo_page_daily);
        # only its age/gender sub-panels hide, via demoUserPanels.
        for kept in ("sessions", "top_pages", "landing", "demographics"):
            self.assertNotIn(kept, hidden)

    def test_demographics_becomes_geography_only_under_a_scope(self) -> None:
        # The map/cities are page-scoped server-side, so the section stays — but
        # age/gender are user-scoped in GA4 and get hidden, and the heading and
        # note have to say the section is now geography only.
        scoped = _render(page_path_filter="/careers", is_admin=True)
        self.assertIn('id="demoUserPanels"', scoped)
        self.assertIn("dpanels.hidden = true", scoped)
        self.assertIn("'Geography'", scoped)
        self.assertIn("demoScopeNote", scoped)
        # Whole-site view keeps the full Demographics section.
        plain = _render(page_path_filter=None, is_admin=True)
        self.assertIn('id="demoSectionTitle">Demographics<', plain)

    def test_no_sessions_over_time_chart(self) -> None:
        # The Sessions-over-time chart is gone — Sessions & engagement, whose
        # cards name their own scope, opens the pane instead.
        for html in (_render(page_path_filter="/careers", is_admin=True),
                     _render(page_path_filter=None, is_admin=True)):
            self.assertNotIn('id="sec-sessions"', html)
            self.assertNotIn("sessionsScopeNote", html)
            self.assertNotIn("Sessions on matching pages", html)

    def test_filter_html_escaped_in_editor(self) -> None:
        # A stray "<" in a pattern must not break out of the textarea/script.
        html = _render(page_path_filter="/careers<script>", is_admin=True)
        self.assertNotIn("/careers<script>", html)
        self.assertIn("/careers&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
