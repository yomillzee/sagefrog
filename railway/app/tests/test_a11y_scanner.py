"""Tests for the axe-core accessibility scanner and its scoping roll-up.

Two layers, mirroring the consent-scanner suite:

* Pure tests (always run) — the vendored axe engine is present and readable, and
  the effort/aggregation math in ``scripts/a11y_audit.py`` behaves.
* Browser tests (skipped unless Playwright + a Chromium binary are available) —
  inject axe into a page with a *known* violation and assert axe flags it, so a
  broken injection or a bad tag set is caught.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
SCRIPTS_DIR = APP_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import a11y_scanner as ax  # noqa: E402
import consent_scanner as sc  # noqa: E402
import a11y_audit  # noqa: E402

_BROWSER_READY = sc.playwright_available() and bool(sc._chromium_executable())

# A single <img> with no alt text — axe's "image-alt" rule (a WCAG 2.0 A / critical
# failure) must fire. Kept deliberately minimal so the assertion is unambiguous.
_MISSING_ALT_HTML = """
<!doctype html><html lang="en"><head><title>T</title></head>
<body><main><h1>Hi</h1><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></main></body></html>
"""

_CLEAN_HTML = """
<!doctype html><html lang="en"><head><title>Accessible</title></head>
<body><main><h1>Hello</h1><p>Plain accessible text.</p>
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="a dot"></main></body></html>
"""


class PureTests(unittest.TestCase):
    def test_vendored_axe_present_and_versioned(self):
        self.assertTrue(ax._AXE_PATH.exists(), "vendored axe.min.js must be committed")
        src = ax._load_axe_source()
        self.assertTrue(src and "axe.run" in src, "axe source should define axe.run")
        ver = ax.axe_version()
        self.assertIsNotNone(ver, "axe version string should be readable")
        self.assertRegex(ver, r"^\d+\.\d+\.\d+$")

    def test_no_urls_reports_in_band_not_raise(self):
        res = ax.scan_pages([])
        self.assertTrue(res["available"])
        self.assertEqual(res["pages"], [])
        self.assertIsNotNone(res["error"])

    def test_invalid_url_is_rejected_by_ssrf_guard(self):
        # A non-http(s) scheme is rejected regardless of the private-host env knob.
        res = ax.scan_pages(["ftp://example.com/file"])
        self.assertTrue(res.get("rejected"))
        self.assertEqual(res["pages"], [])

    def test_aggregate_and_effort_math(self):
        # Two pages: page A fails one serious rule on 3 nodes + one moderate rule;
        # page B fails the same serious rule (so it spans 2 pages) on 1 node.
        scan = {
            "pages": [
                {
                    "url": "https://x/a", "scanned": True, "incomplete_count": 2,
                    "by_impact": {"critical": 0, "serious": 1, "moderate": 1, "minor": 0},
                    "violations": [
                        {"id": "color-contrast", "impact": "serious", "help": "h", "helpUrl": "u",
                         "nodes": [{}, {}, {}]},
                        {"id": "region", "impact": "moderate", "help": "h", "helpUrl": "u",
                         "nodes": [{}]},
                    ],
                },
                {
                    "url": "https://x/b", "scanned": True, "incomplete_count": 0,
                    "by_impact": {"critical": 0, "serious": 1, "moderate": 0, "minor": 0},
                    "violations": [
                        {"id": "color-contrast", "impact": "serious", "help": "h", "helpUrl": "u",
                         "nodes": [{}]},
                    ],
                },
            ],
        }
        agg = a11y_audit._aggregate(scan)
        self.assertEqual(agg["totals_by_impact"]["serious"], 2)   # rule instances
        self.assertEqual(agg["totals_by_impact"]["moderate"], 1)
        self.assertEqual(agg["total_nodes"], 5)
        self.assertEqual(agg["incomplete_total"], 2)
        # color-contrast recurs on both pages -> should sort first and span 2 pages.
        self.assertEqual(agg["rules"][0]["id"], "color-contrast")
        self.assertEqual(agg["rules"][0]["page_count"], 2)
        self.assertEqual(agg["rules"][0]["nodes"], 4)
        # Effort: 2 serious * 1.5 + 1 moderate * 0.75 = 3.75, rounded to 1 dp.
        self.assertAlmostEqual(agg["est_dev_hours"], 3.8, places=1)
        self.assertEqual(a11y_audit._band(agg), "Small")

    def test_slugify(self):
        self.assertEqual(a11y_audit._slugify("Penn Community Bank!"), "penn-community-bank")
        self.assertEqual(a11y_audit._slugify("   "), "client")

    def test_public_aggregate_matches_cli_wrapper(self):
        # The CLI now delegates to a11y_scanner.aggregate — same numbers either way.
        scan = {"pages": [{
            "url": "https://x/", "scanned": True, "incomplete_count": 0,
            "violations": [{"id": "image-alt", "impact": "critical", "help": "h", "helpUrl": "u",
                            "nodes": [{}]}],
        }]}
        self.assertEqual(a11y_audit._aggregate(scan), ax.aggregate(scan))

    def test_size_band_thresholds(self):
        def agg(crit=0, serious=0, moderate=0, minor=0):
            by = {"critical": crit, "serious": serious, "moderate": moderate, "minor": minor}
            return {"totals_by_impact": by, "total_violations": sum(by.values())}
        self.assertEqual(ax.size_band(agg()), "Clean")
        self.assertEqual(ax.size_band(agg(minor=2)), "Small")
        self.assertEqual(ax.size_band(agg(crit=3)), "Medium")
        self.assertEqual(ax.size_band(agg(moderate=10)), "Medium")
        self.assertEqual(ax.size_band(agg(crit=8)), "Large")
        self.assertEqual(ax.size_band(agg(minor=25)), "Large")

    def test_component_key_collapses_repeated_instances(self):
        # Same component, different sibling indices and leaf depths -> one key.
        k1 = ax._component_key("html > body > nav.main > ul.menu > li:nth-child(9) > a")
        k2 = ax._component_key("nav.main > ul.menu > li:nth-child(2)")
        k3 = ax._component_key("nav.main > ul.menu")
        self.assertEqual(k1, "nav.main > ul.menu")
        self.assertEqual(k1, k2)
        self.assertEqual(k1, k3)

    def test_cluster_collapses_navigation_defect(self):
        # 102 empty links + 102 mis-parented <li> + 1 invalid <ul>, across 3 rules,
        # plus two unrelated one-off contrast issues.
        def n(fmt, count):
            return [{"target": [fmt.format(i=i)]} for i in range(1, count + 1)]
        scan = {"pages": [{"url": "https://x/", "scanned": True, "violations": [
            {"id": "link-name", "impact": "serious", "help": "h", "helpUrl": "u",
             "nodes": n("nav#nav > ul.menu > li:nth-child({i}) > a", 102)},
            {"id": "listitem", "impact": "serious", "help": "h", "helpUrl": "u",
             "nodes": n("nav#nav > ul.menu > li:nth-child({i})", 102)},
            {"id": "list", "impact": "serious", "help": "h", "helpUrl": "u",
             "nodes": [{"target": ["nav#nav > ul.menu"]}]},
            {"id": "color-contrast", "impact": "minor", "help": "h", "helpUrl": "u",
             "nodes": [{"target": ["main > h1"]}, {"target": ["footer > p"]}]},
        ]}]}
        clusters = ax.cluster_components(scan)
        top = clusters[0]
        self.assertEqual(top["key"], "nav#nav > ul.menu")
        self.assertEqual(top["element_count"], 205)
        self.assertEqual(top["rule_count"], 3)
        self.assertTrue(top["is_component"])
        self.assertEqual(top["label"], "Navigation")
        # The two contrast elements are separate, one-off (not flagged as a component).
        others = [c for c in clusters if not c["is_component"]]
        self.assertEqual(sum(c["element_count"] for c in others), 2)

    def test_cluster_small_counts_are_not_components(self):
        scan = {"pages": [{"url": "https://x/", "scanned": True, "violations": [
            {"id": "image-alt", "impact": "critical", "help": "h", "helpUrl": "u",
             "nodes": [{"target": ["main > img"]}]},
        ]}]}
        clusters = ax.cluster_components(scan)
        self.assertEqual(len(clusters), 1)
        self.assertFalse(clusters[0]["is_component"])


@unittest.skipUnless(_BROWSER_READY, "requires Playwright + a Chromium binary")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(**sc._launch_kwargs())
        cls._axe_src = ax._load_axe_source()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._browser.close()
        finally:
            cls._pw.stop()

    def _run_axe(self, html: str):
        ctx = self._browser.new_context()
        try:
            page = ctx.new_page()
            page.set_content(html, wait_until="load")
            page.evaluate(self._axe_src)
            return page.evaluate(ax._AXE_RUN_JS, list(ax._DEFAULT_TAGS)) or {}
        finally:
            ctx.close()

    def test_missing_alt_is_flagged(self):
        res = self._run_axe(_MISSING_ALT_HTML)
        ids = {v["id"] for v in res.get("violations") or []}
        self.assertIn("image-alt", ids, "img without alt must be reported by axe")

    def test_clean_page_has_no_image_alt_violation(self):
        res = self._run_axe(_CLEAN_HTML)
        ids = {v["id"] for v in res.get("violations") or []}
        self.assertNotIn("image-alt", ids)


if __name__ == "__main__":
    unittest.main()
