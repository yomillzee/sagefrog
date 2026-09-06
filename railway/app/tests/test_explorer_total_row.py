from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.assets import dashboard_css  # noqa: E402
from dashboard.renderers.bigquery_dashboard_renderer import (  # noqa: E402
    render_bigquery_dashboard_page,
)

# The Campaign explorer table ends in a grand-total row: spend, impressions,
# clicks, CTR, conversions and verified conversions for everything currently in
# view. "In view" is the point — it is summed from the campaign nodes of the
# tree the render just built, so a Platform chip, a filter dropdown, the campaign
# allowlist and the date range all re-total it for free.
#
# Two things it must not get wrong:
#   * CTR is total clicks / total impressions, never the mean of the per-campaign
#     CTRs (which weights a 10-impression campaign like a 10,000-impression one).
#   * Verified conversions are resolved per campaign (Google by campaign id,
#     LinkedIn by group name, Meta on the row), so the total has to come off the
#     campaign nodes — summing raw rows would double count Google and LinkedIn.


def _render() -> str:
    return render_bigquery_dashboard_page(
        client_slug="demo",
        api_client_key="demo",
        label="Demo",
        use_session=True,
        session_email="t@e.com",
    )


def _js_block(html: str, signature: str) -> str:
    """Slice one JS function out of the rendered page by brace matching."""
    start = html.index(signature)
    depth = 0
    for i in range(html.index("{", start), len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


class ExplorerTotalRowMarkupTests(unittest.TestCase):
    """The footer is wired into the same render path as the tree itself."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.html = _render()

    def test_table_render_appends_a_tfoot(self):
        self.assertIn(
            "el.innerHTML=head+`<tbody>${body}</tbody>`+foot;",
            self.html,
        )
        self.assertIn('<tfoot><tr class="expl-total">', self.html)

    def test_totals_come_from_the_rendered_tree(self):
        # Not from explorerRows / filtered — the tree is what the campaign rows
        # were built from, so footer and rows can never disagree.
        self.assertIn("const totals=explorerTotals(tree);", self.html)
        self.assertIn("metricCells(totals,aggPrev)", self.html)

    def test_footer_reuses_the_metric_columns(self):
        # metricCells() walks metricCols() -- METRIC_COLS minus whatever the
        # kebab's switches have turned off -- for every row level (campaign, ad
        # group, ad) and the footer alike, so they all keep the same column
        # count, order and formatting, including ga4-col. Its optional second
        # argument (a comparison-period aggregate) adds a vs-previous delta;
        # the footer passes aggPrev, and so do campaign rows (matched into the
        # comparison-window tree), while ad-group/ad rows omit it.
        totals = _js_block(self.html, "function explorerTotals(")
        self.assertNotIn("METRIC_COLS", totals)
        cells = _js_block(self.html, "function metricCells(")
        self.assertIn("metricCols()", cells)
        self.assertIn("metricCells(camp.metrics, prevCamp?prevCamp.metrics:null)", self.html)

    def test_footer_labels_the_campaign_count(self):
        self.assertIn("${nCamp} campaign${nCamp===1?'':'s'}", self.html)

    def test_footer_is_styled_and_not_expandable(self):
        css = dashboard_css()[1]
        self.assertIn("#explorerTable tfoot td {", css)
        self.assertIn("#explorerTable tfoot td.ga4-col {", css)
        # No data-expandable on the row: clicking the total must not try to open
        # children (the delegated handler keys off tr[data-expandable]).
        foot = self.html.split('<tfoot><tr class="expl-total">', 1)[1][:400]
        self.assertNotIn("data-expandable", foot)

    def test_empty_state_has_no_total_row(self):
        # "No campaigns match these filters." is its own tbody; totalling nothing
        # would just print a row of zeros.
        empty = self.html.split("No campaigns match these filters.", 1)[1][:200]
        self.assertNotIn("expl-total", empty)


class ExplorerTotalRowMathTests(unittest.TestCase):
    """Run the page's own totals JS to check the numbers it produces."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node:
            return
        html = _render()
        cls.src = "function num(v){const n=Number(v);return isFinite(n)?n:0;}\n" + "\n".join(
            _js_block(html, sig)
            for sig in (
                "function zeroMetrics()",
                "function addMetrics(",
                "function withCtr(",
                "function explorerTotals(",
            )
        )

    def _run(self, tree: list[dict]) -> dict:
        if not self.node:
            self.skipTest("node not available")
        script = (
            self.src
            + "\nconst tree=new Map("
            + json.dumps([[str(i), {"metrics": m}] for i, m in enumerate(tree)])
            + ");\nconsole.log(JSON.stringify(withCtr(explorerTotals(tree))));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "totals.js"
            path.write_text(script)
            out = subprocess.run(
                [self.node, str(path)], capture_output=True, text=True, check=True
            )
        return json.loads(out.stdout)

    @staticmethod
    def _m(spend, impressions, clicks, conversions, verified=0, na=False) -> dict:
        m = {
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "verified": verified,
            "verified_sel": verified,
        }
        if na:
            m["_verifiedNa"] = True
        return m

    def test_metrics_add_up(self):
        t = self._run(
            [
                self._m(100.5, 1000, 50, 3, verified=7),
                self._m(49.5, 1000, 30, 2, verified=1),
                self._m(10, 500, 5, 1, na=True),
            ]
        )
        self.assertAlmostEqual(t["spend"], 160.0)
        self.assertEqual(t["impressions"], 2500)
        self.assertEqual(t["clicks"], 85)
        self.assertEqual(t["conversions"], 6)
        self.assertEqual(t["verified_sel"], 8)

    def test_ctr_is_weighted_not_averaged(self):
        # 10/100 = 10% and 10/10000 = 0.1%; the mean would be ~5.05%, the real
        # blended rate is 20/10100 = 0.198%.
        t = self._run([self._m(1, 100, 10, 0), self._m(1, 10000, 10, 0)])
        self.assertAlmostEqual(t["ctr"], 20 / 10100 * 100, places=6)

    def test_ctr_is_zero_when_no_impressions(self):
        t = self._run([self._m(0, 0, 0, 0)])
        self.assertEqual(t["ctr"], 0)

    def test_verified_is_na_only_when_nothing_in_view_has_data(self):
        # Every campaign unmapped (e.g. Microsoft-only view) -> the column shows
        # "—" rather than a misleading 0.
        all_na = self._run([self._m(10, 500, 5, 1, na=True), self._m(5, 50, 2, 0, na=True)])
        self.assertTrue(all_na.get("_verifiedNa"))
        # One mapped campaign is enough to make the total meaningful.
        mixed = self._run([self._m(10, 500, 5, 1, na=True), self._m(5, 50, 2, 0, verified=4)])
        self.assertNotIn("_verifiedNa", mixed)
        self.assertEqual(mixed["verified_sel"], 4)

    def test_empty_tree_totals_to_zero(self):
        t = self._run([])
        self.assertEqual(t["spend"], 0)
        self.assertEqual(t["ctr"], 0)
        self.assertTrue(t.get("_verifiedNa"))


if __name__ == "__main__":
    unittest.main()
