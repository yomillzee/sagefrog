from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Reported bug: "Search Console sidebar pulls up regardless of whether the GSC
# connector is set up or not." platform_nav_flags computed show_gsc correctly but
# nothing consumed it -- the tab was hardcoded into the core nav list, so a
# GA4-only client got a Search Console tab that only loaded a table-not-found
# error. The tab (and the Overview panel behind it) is now gated on the connector,
# the same way Site Performance gates on pagespeed.


def _cfg(connector_type: str, status: str):
    return types.SimpleNamespace(connector_type=connector_type, status=status)


def _tabs(nav_html: str) -> list[str]:
    return re.findall(r'data-tab="([a-z_]+)"', nav_html)


class GscSidebarGatingTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs

        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        cdc.get_config = lambda slug: None

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg

    def _nav(self, configs, client_slug: str = "acme") -> str:
        import connector_config_store as ccs
        ccs.list_configs = lambda slug: configs
        from dashboard.renderers.base_layout import dashboard_sidebar_view_nav_html
        return dashboard_sidebar_view_nav_html(
            client_slug=client_slug, access_key="k", use_session=True, as_tabs=True,
        )

    def test_absent_without_the_gsc_connector(self) -> None:
        for label, configs in (
            ("no connectors at all", []),
            ("GA4 only", [_cfg("ga4", "connected")]),
            ("gsc not_connected", [_cfg("ga4", "connected"), _cfg("gsc", "not_connected")]),
            ("gsc disconnected", [_cfg("gsc", "disconnected")]),
        ):
            with self.subTest(label):
                self.assertNotIn("gsc", _tabs(self._nav(configs)))

    def test_present_once_the_gsc_connector_is_live(self) -> None:
        for status in ("connected", "syncing"):
            with self.subTest(status):
                self.assertIn("gsc", _tabs(self._nav([_cfg("gsc", status)])))

    def test_core_tabs_are_unaffected_by_the_gate(self) -> None:
        # Only Search Console moves; the always-present core tabs stay put.
        self.assertEqual(
            _tabs(self._nav([_cfg("ga4", "connected")])),
            ["overview", "explorer", "analytics", "ai_traffic"],
        )

    def test_demo_client_keeps_search_console(self) -> None:
        # The demo seeds no connector rows but serves synthetic gsc.* data
        # (demo_data.py), so it must not lose the tab to the gate.
        import demo_client
        self.assertIn("gsc", _tabs(self._nav([], client_slug=demo_client.DEMO_SLUG)))

    def test_fails_open_when_connector_state_is_unreadable(self) -> None:
        # A transient Postgres blip must not blank a core tab for a client that
        # does have GSC -- better a spurious tab than a vanishing one.
        import connector_config_store as ccs

        def boom(slug):
            raise RuntimeError("DeadlockDetected")

        ccs.list_configs = boom
        from dashboard.renderers.base_layout import dashboard_sidebar_view_nav_html
        nav = dashboard_sidebar_view_nav_html(
            client_slug="acme", access_key="k", use_session=True, as_tabs=True,
        )
        self.assertIn("gsc", _tabs(nav))

    def test_platform_nav_flags_reports_whether_the_read_succeeded(self) -> None:
        import connector_config_store as ccs
        from dashboard.renderers.base_layout import platform_nav_flags

        ccs.list_configs = lambda slug: [_cfg("ga4", "connected")]
        ok = platform_nav_flags("acme")
        self.assertTrue(ok["_connector_read_ok"])
        self.assertFalse(ok["show_gsc"])

        def boom(slug):
            raise RuntimeError("DeadlockDetected")

        ccs.list_configs = boom
        failed = platform_nav_flags("acme")
        self.assertFalse(failed["_connector_read_ok"])
        # The failure path still returns a COMPLETE flag set.
        self.assertEqual(set(failed) - {"_connector_read_ok"}, set(ok) - {"_connector_read_ok"})


class GscOverviewPanelGatingTests(unittest.TestCase):
    """The Overview home's Search Console card follows the same gate."""

    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        cdc.get_config = lambda slug: None

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg

    def _render(self, configs, client_slug: str = "acme") -> str:
        import connector_config_store as ccs
        ccs.list_configs = lambda slug: configs
        from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402
        return render_bigquery_dashboard_page(
            client_slug=client_slug, api_client_key=client_slug, label="Acme",
            use_session=True, session_email="t@e.com",
        )

    def test_overview_card_dropped_without_the_connector(self) -> None:
        html = self._render([_cfg("ga4", "connected")])
        self.assertNotIn('data-ov-card="gsc"', html)
        self.assertNotIn('data-tab="gsc"', html)

    def test_overview_card_present_with_the_connector(self) -> None:
        html = self._render([_cfg("ga4", "connected"), _cfg("gsc", "connected")])
        self.assertIn('data-ov-card="gsc"', html)
        self.assertIn('data-tab="gsc"', html)

    def test_pane_is_still_emitted_either_way(self) -> None:
        # Like Site Performance, the pane always ships; the nav button is the gate.
        # Keeps switchTab and the deep-link fallback from referencing a missing node.
        for configs in ([_cfg("ga4", "connected")],
                        [_cfg("gsc", "connected")]):
            self.assertIn('id="pane-gsc"', self._render(configs))

    def test_deep_link_falls_back_off_an_unreachable_tab(self) -> None:
        # ?view=gsc on a client without GSC must not strand the user on an empty
        # pane with no nav item -- the init checks a nav button actually rendered.
        html = self._render([_cfg("ga4", "connected")])
        self.assertIn(".dash-view-btn[data-tab=\"' + t + '\"]", html)


if __name__ == "__main__":
    unittest.main()
