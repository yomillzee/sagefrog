from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# A Nixon-style dashboard must present the identical sidebar on every page --
# same section nav and same footer links -- so items never appear/vanish as the
# user moves between the dashboard, settings, connectors, and files pages.


def _section_items(html: str) -> list[str]:
    m = re.search(r'<nav class="dash-sidebar-nav"[^>]*>(.*?)</nav>', html, re.S)
    assert m, "no section nav found"
    return re.findall(
        r'class="dash-view-btn[^"]*"[^>]*>\s*<svg.*?</svg>\s*<span>([^<]+)</span>',
        m.group(1), re.S,
    )


def _footer_items(html: str) -> list[str]:
    m = re.search(r'<nav class="dash-sidebar-links"[^>]*>(.*?)</nav>', html, re.S)
    assert m, "no footer nav found"
    return re.findall(r'<span>([^<]+)</span>', m.group(1))


class SidebarConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        ccs.list_configs = lambda slug: []  # a fresh client with no connectors

        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
        )

        import client_insight_documents as docs
        self._orig_docs = docs.enabled
        docs.enabled = lambda: True

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg
        import client_insight_documents as docs
        docs.enabled = self._orig_docs

    def _render_all(self) -> dict[str, str]:
        from dashboard.renderers.bigquery_dashboard_renderer import render_bigquery_dashboard_page
        from dashboard.renderers.bigquery_settings_renderer import render_bigquery_settings_page
        from dashboard.renderers.base_layout import render_client_shell_page
        kw = dict(access_key="k", session_is_admin=True)
        return {
            "dashboard": render_bigquery_dashboard_page(
                client_slug="test", api_client_key="test", label="Test Co", **kw),
            "settings": render_bigquery_settings_page(
                client_slug="test", api_client_key="test", label="Test Co",
                routing={"project": "p"}, show_linkedin_backfill=False, **kw),
            "connectors": render_client_shell_page(
                client_slug="test", label="Test Co", active_nav="connectors",
                page_title="C", page_subtitle="", content_html="x", **kw),
            "files": render_client_shell_page(
                client_slug="test", label="Test Co", active_nav="files",
                page_title="F", page_subtitle="", content_html="x", **kw),
        }

    def test_section_nav_identical_on_every_page(self) -> None:
        pages = self._render_all()
        expected = ["Overview", "Campaign Explorer", "Website Analytics", "Search Console"]
        for name, html in pages.items():
            self.assertEqual(_section_items(html), expected, f"section nav differs on {name}")

    def test_footer_nav_identical_on_every_page(self) -> None:
        pages = self._render_all()
        expected = ["Files", "Connectors", "Insights"]
        for name, html in pages.items():
            self.assertEqual(_footer_items(html), expected, f"footer nav differs on {name}")


if __name__ == "__main__":
    unittest.main()
