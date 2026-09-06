from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# The Overview home is a stack of optional cards: Search Console and Site
# Performance are connector-gated, and an admin can hide any of them. The
# Website analytics + AI traffic charts are NOT optional, so their loader must
# never touch an element belonging to another card. It used to: the single
# loadOverviewHome() wrote a skeleton into #ovGscBrandedLeaders before drawing
# either chart, so a GA4-only client (no Search Console) hit a TypeError there
# and both charts stayed blank.


def _page_script(html: str) -> str:
    """The page's inline JS (all non-src <script> blocks concatenated)."""
    return "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S))


def _fn_body(script: str, name: str) -> str:
    """Source of `function name(...)`, by brace matching from its opening `{`."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", script)
    assert m, f"{name} not found in page script"
    i = script.index("{", m.end())
    depth = 0
    for j in range(i, len(script)):
        if script[j] == "{":
            depth += 1
        elif script[j] == "}":
            depth -= 1
            if depth == 0:
                return script[i : j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


class OverviewLoaderIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        import connector_config_store as ccs
        self._orig_list = ccs.list_configs
        import client_dashboard_config as cdc
        self._orig_cfg = cdc.get_config
        # No DB in tests: the sidebar's theme lookup would otherwise reach for a
        # connection (and blow up if another test in the same run left
        # DATABASE_URL primed). Patched on the module the sidebar actually holds.
        from dashboard.renderers import base_layout
        self._theme_mod = base_layout.dashboard_theme
        self._orig_theme = self._theme_mod.load_client_theme
        self._theme_mod.load_client_theme = lambda slug: self._theme_mod.merge_theme(None)

    def tearDown(self) -> None:
        import connector_config_store as ccs
        ccs.list_configs = self._orig_list
        import client_dashboard_config as cdc
        cdc.get_config = self._orig_cfg
        self._theme_mod.load_client_theme = self._orig_theme

    def _render(self, connectors: list[str]) -> str:
        import connector_config_store as ccs
        ccs.list_configs = lambda slug: [
            types.SimpleNamespace(connector_type=c, status="connected")
            for c in connectors
        ]
        import client_dashboard_config as cdc
        cdc.get_config = lambda slug: types.SimpleNamespace(
            dashboard_mode="bigquery_nixon", label="Test Co",
            gcp_project_id="p", bq_mart_dataset_id="marketing_marts",
            gsc_branded_roots=None, gsc_target_keywords=None,
            gsc_branded_exclude=None, gsc_target_exclude=None,
            ga4_key_events=None, explorer_filters=None,
            monthly_budget_usd=None, overview_pinned_card=None, card_layouts={},
        )
        from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402
        return render_bigquery_dashboard_page(
            client_slug="test", api_client_key="test", label="Test Co",
            use_session=True,
        )

    def test_ga4_only_client_has_no_search_console_card(self) -> None:
        """Precondition for the rest: GSC really is absent without the connector."""
        html = self._render(["ga4"])
        self.assertNotIn('id="ovGscBrandedLeaders"', html)
        self.assertNotIn('id="ovGscTargetLeaders"', html)
        # ...while the two always-on charts are there.
        self.assertIn('id="ovSessionsTrend"', html)
        self.assertIn('id="ovAiTrend"', html)

    def test_trend_loader_touches_no_other_cards_elements(self) -> None:
        """The sessions/AI loader must not reference optional cards' elements."""
        body = _fn_body(_page_script(self._render(["ga4"])), "loadOverviewTrends")
        for foreign in ("ovGsc", "ovPs", "summaryCards", "mql"):
            self.assertNotIn(foreign, body)

    def test_overview_loaders_only_deref_elements_that_exist(self) -> None:
        """
        Every `document.getElementById('x').y` in the Overview loaders must name
        an element this render actually emits — an unguarded deref of a gated
        card's element throws and blanks every chart after it.
        """
        html = self._render(["ga4"])
        script = _page_script(html)
        ids = set(re.findall(r'id="([^"]+)"', html))
        for fn in ("loadOverviewTrends", "loadOverviewGsc", "loadOverviewHome",
                   "loadOverviewPagespeed"):
            body = _fn_body(script, fn)
            derefs = set(re.findall(r"document\.getElementById\('([^']+)'\)\s*\.", body))
            missing = sorted(d for d in derefs if d not in ids)
            self.assertEqual(missing, [], f"{fn} dereferences absent element(s)")

    def test_gsc_loader_bails_out_when_card_is_absent(self) -> None:
        """No Search Console card means no keyword-match fetch at all."""
        body = _fn_body(_page_script(self._render(["ga4"])), "loadOverviewGsc")
        guard = body.index("if (!brandedEl && !targetEl) return;")
        # The early return precedes every fetch and every element write.
        self.assertLess(guard, body.index("fetchKeywordMatches"))
        self.assertLess(guard, body.index("innerHTML"))

    def test_trend_panels_carry_a_status_element(self) -> None:
        """
        Both charts report "no data"/errors through a status span; without one
        an empty range is indistinguishable from a broken page.
        """
        html = self._render(["ga4"])
        self.assertIn('id="ovSessionsStatus"', html)
        self.assertIn('id="ovAiStatus"', html)

    def test_gsc_client_still_loads_the_search_console_card(self) -> None:
        html = self._render(["ga4", "gsc"])
        self.assertIn('id="ovGscBrandedLeaders"', html)
        self.assertIn('id="ovGscTargetLeaders"', html)
        home = _fn_body(_page_script(html), "loadOverviewHome")
        self.assertIn("loadOverviewTrends()", home)
        self.assertIn("loadOverviewGsc()", home)


if __name__ == "__main__":
    unittest.main()
