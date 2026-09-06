from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import google_ads_service  # noqa: E402
import marketing_service  # noqa: E402
import meta_service  # noqa: E402
from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402

# The Campaign Explorer's `Conv.` column is each platform's own pre-summed
# conversion count. A selector in its header narrows it to ONE conversion action
# ("Contact form", "Leads", a Microsoft goal).
#
# The thing that makes this more than a dropdown is that the platforms do not
# report the split at the same grain:
#
#   Google / Meta  -> per ad. Every level of the tree can answer.
#   Microsoft      -> per ad GROUP (Goals and Funnels has no AdId column), so
#                     the value lands on the ad-group node and the ad rows
#                     underneath show "—" instead of an invented per-ad split.
#   LinkedIn       -> nothing. Its analytics pivot by one dimension at a time,
#                     so a conversion pivot cannot also name the campaign.
#
# Everything below exists to pin that: a dash means "this platform does not
# report that far down", never "zero".


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


class GoogleFetchShapeTests(unittest.TestCase):
    def test_conversion_action_id_is_the_resource_tail(self):
        self.assertEqual(
            google_ads_service._conversion_action_id(
                "customers/1234567890/conversionActions/987654"
            ),
            "987654",
        )

    def test_conversion_action_id_tolerates_junk(self):
        self.assertEqual(google_ads_service._conversion_action_id(None), "")
        self.assertEqual(google_ads_service._conversion_action_id(""), "")


class MetaActionBreakdownTests(unittest.TestCase):
    """Meta reports one event under several redundant buckets."""

    ROW = {
        "actions": [
            {"action_type": "offsite_conversion.fb_pixel_lead", "value": "2"},
            {"action_type": "omni_lead", "value": "2"},
            {"action_type": "lead", "value": "2"},
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
            {"action_type": "omni_purchase", "value": "3"},
            {"action_type": "link_click", "value": "40"},
        ],
        "action_values": [
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "150.0"},
        ],
    }

    def _by_token(self, tokens=None):
        rows = meta_service._action_breakdown(self.ROW, result_tokens=tokens)
        return {r["action_token"]: r for r in rows}

    def test_redundant_buckets_count_once(self):
        # Two leads reported under three action_types is two leads, not six —
        # the same first-candidate-wins rule the headline Conv. number uses.
        by = self._by_token({"lead"})
        self.assertEqual(by["lead"]["conversions"], 2.0)
        self.assertEqual(by["purchase"]["conversions"], 3.0)

    def test_breakdown_agrees_with_the_headline_conversions(self):
        # The selected action can never exceed the number it is a slice of.
        by = self._by_token({"lead"})
        headline = meta_service._parse_conversions(self.ROW["actions"], {"lead"})
        self.assertEqual(by["lead"]["conversions"], headline)

    def test_is_result_marks_only_the_optimization_goal(self):
        by = self._by_token({"lead"})
        self.assertTrue(by["lead"]["is_result"])
        self.assertFalse(by["purchase"]["is_result"])
        self.assertFalse(by["link_click"]["is_result"])

    def test_non_result_events_are_still_offered(self):
        # Link clicks are not this ad set's result, but they are a real thing to
        # slice by, so the selector should be able to see them.
        self.assertIn("link_click", self._by_token({"lead"}))

    def test_values_ride_along_with_their_action(self):
        self.assertEqual(self._by_token({"purchase"})["purchase"]["conversion_value"], 150.0)

    def test_a_row_with_no_actions_yields_nothing(self):
        self.assertEqual(meta_service._action_breakdown({}, result_tokens={"lead"}), [])

    def test_ad_daily_wrapper_still_returns_plain_rows(self):
        # fetch_ad_daily_metrics kept its old list-of-rows contract; only the
        # new *_with_actions variant returns the pair.
        self.assertIn("AdDailyFetch", meta_service.__dict__)
        self.assertEqual(meta_service.AdDailyFetch._fields, ("rows", "action_rows"))


class PayloadEnvelopeTests(unittest.TestCase):
    """All three platform reads return one envelope so the UI has one path."""

    ROWS = [
        {"ad_id": "a1", "name": "Contact form", "conversions": 3.0},
        {"ad_id": "a1", "name": "Phone call", "conversions": 1.0},
        {"ad_id": "a2", "name": "Contact form", "conversions": 7.0},
    ]

    def _payload(self):
        return marketing_service._conversion_action_payload(
            self.ROWS,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            grain="ad",
            entity_key="ad_id",
            name_key="name",
        )

    def test_actions_are_ordered_by_total_not_alphabetically(self):
        names = [a["name"] for a in self._payload()["actions"]]
        self.assertEqual(names, ["Contact form", "Phone call"])

    def test_per_entity_map_keeps_each_entity_separate(self):
        by = self._payload()["by_entity"]
        self.assertEqual(by["a1"], {"Contact form": 3.0, "Phone call": 1.0})
        self.assertEqual(by["a2"], {"Contact form": 7.0})

    def test_totals_are_rounded_for_display(self):
        # Google reports fractional conversions; 3.0000000000000004 in a
        # dropdown is how a real number starts looking like a bug.
        rows = [{"ad_id": "a", "name": "X", "conversions": 0.1} for _ in range(3)]
        payload = marketing_service._conversion_action_payload(
            rows, start_date=date(2026, 1, 1), end_date=date(2026, 1, 2),
            grain="ad", entity_key="ad_id", name_key="name",
        )
        self.assertEqual(payload["actions"][0]["total"], 0.3)

    def test_grain_travels_with_the_payload(self):
        self.assertEqual(self._payload()["grain"], "ad")
        empty = marketing_service._empty_conversion_actions(
            date(2026, 1, 1), date(2026, 1, 2), "ad_group"
        )
        self.assertEqual(empty["grain"], "ad_group")

    def test_a_missing_mart_view_is_an_empty_catalog_not_an_error(self):
        # A client synced before this shipped has no breakdown view. The
        # selector simply doesn't render; the column keeps working.
        empty = marketing_service._empty_conversion_actions(
            date(2026, 1, 1), date(2026, 1, 2), "ad"
        )
        self.assertEqual(empty["actions"], [])
        self.assertEqual(empty["by_entity"], {})


class DemoPayloadTests(unittest.TestCase):
    """The demo client exercises the same UI, so its numbers have to hold up."""

    PAYLOAD = {"start_date": "2026-07-01", "end_date": "2026-07-31"}

    def test_google_split_sums_to_the_ad_conversions(self):
        import demo_data

        split = demo_data.generate("explorer.google_conversion_actions", self.PAYLOAD)
        rows = demo_data.generate("explorer.google_ads", self.PAYLOAD)["rows"]
        for row in rows[:5]:
            got = sum(split["by_entity"][row["ad_id"]].values())
            self.assertAlmostEqual(got, row["conversions"], places=1)

    def test_microsoft_demo_is_ad_group_grain(self):
        import demo_data

        split = demo_data.generate("explorer.microsoft_conversion_actions", self.PAYLOAD)
        self.assertEqual(split["grain"], "ad_group")
        rows = demo_data.generate("explorer.microsoft_ads", self.PAYLOAD)["rows"]
        self.assertTrue(set(split["by_entity"]) <= {r["ad_group_id"] for r in rows})


class RendererWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = _render()

    def test_conv_column_declares_the_selector(self):
        self.assertIn("key:'conversions',label:'Conv.',format:count,convSelect:true", self.html)

    def test_selector_is_navy_not_gold(self):
        # The gold accent means "this number came from GA4". A second gold
        # selector on a platform-reported column would erase that distinction.
        from dashboard.assets import dashboard_css

        self.assertIn("#explorerTable th .cv-select", dashboard_css()[1])
        self.assertIn('class="cv-select"', self.html)

    def test_all_three_breakdowns_are_fetched_with_the_explorer(self):
        for api in (
            "GOOGLE_CONV_ACTIONS_API",
            "META_CONV_ACTIONS_API",
            "MICROSOFT_CONV_ACTIONS_API",
        ):
            self.assertIn(f"getJson(withDates({api}))", self.html)

    def test_selection_survives_a_reload(self):
        self.assertIn("localStorage.setItem(CONV_STORAGE_KEY, selectedConvAction)", self.html)

    def test_a_stale_selection_falls_back_to_all(self):
        # An action with no data in the new window would otherwise dash the whole
        # column with no way back short of reloading.
        self.assertIn("convActionList.indexOf(selectedConvAction)<0", self.html)

    def test_sorting_follows_the_selected_action(self):
        val = _js_block(self.html, "function explorerMetricVal(")
        self.assertIn("convSelectionActive()", val)
        self.assertIn("m.conversions_sel", val)

    def test_selected_action_drops_the_vs_previous_chip(self):
        # The breakdown is fetched for the current window only, so there is no
        # prior-period figure to diff against — showing the unfiltered delta
        # next to a filtered number would read as a collapse.
        cells = _js_block(self.html, "function metricCells(")
        self.assertIn("c.key==='conversions' && convSelectionActive()", cells)
        self.assertNotIn(
            "summaryDeltaHtml", cells[cells.index("c.key==='conversions'") : cells.index("const cell=c.format")]
        )


class SelectionLogicTests(unittest.TestCase):
    """Run the real applyConvSelection / buildExplorerTree under node."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node:
            return
        html = _render()
        cls.src = (
            "function num(v){const n=Number(v);return isFinite(n)?n:0;}\n"
            "function esc(v){return String(v);}\n"
            "let selectedKeyEvent='__all__';\n"
            "let verifiedByGoogleCampaignId={},verifiedByGoogleCampaignIdEvent={};\n"
            "let verifiedByLinkedinGroup={},verifiedByLinkedinGroupEvent={};\n"
            "let verifiedByMicrosoftCampaign={},verifiedByMicrosoftCampaignEvent={};\n"
            "function normalizeLiName(n){return String(n||'').trim().toLowerCase();}\n"
            "let explorerSort={key:'spend',dir:'desc'};\n"
            + "\n".join(
                _js_block(html, sig)
                for sig in (
                    "function zeroMetrics()",
                    "function addMetrics(",
                    "function withCtr(",
                    "function explorerMetricVal(",
                    "function explorerAdName(",
                    "function convSelectionActive()",
                    "function applyConvSelection(",
                    "function buildExplorerTree(",
                    "function explorerTotals(",
                )
            )
        )

    def _run(self, rows, *, selected, maps=None):
        if not self.node:
            self.skipTest("node not available")
        maps = maps or {}
        script = (
            self.src
            + f"\nlet selectedConvAction={json.dumps(selected)};\n"
            + f"let convByGoogleAdId={json.dumps(maps.get('google', {}))};\n"
            + f"let convByMetaAdId={json.dumps(maps.get('meta', {}))};\n"
            + f"let convByMicrosoftGroupId={json.dumps(maps.get('microsoft', {}))};\n"
            + f"const rows={json.dumps(rows)};\n"
            + "applyConvSelection(rows);\n"
            + "const tree=buildExplorerTree(rows);\n"
            + "const out={rows:rows.map(r=>({id:r.ad_id,v:r.conversions_sel,na:!!r._convSelNa})),"
            "campaigns:[...tree.values()].map(c=>({name:c.name,platform:c.platform,"
            "v:c.metrics.conversions_sel,na:!!c.metrics._convSelNa,"
            "groups:[...c.groups.values()].map(g=>({name:g.name,v:g.metrics.conversions_sel,"
            "na:!!g.metrics._convSelNa}))})),"
            "total:(t=>({v:t.conversions_sel,na:!!t._convSelNa}))(explorerTotals(tree))};\n"
            "console.log(JSON.stringify(out));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sel.js"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [self.node, str(path)], capture_output=True, text=True, check=True
            )
        return json.loads(proc.stdout)

    @staticmethod
    def _row(platform, campaign, group, ad_id, conversions, ad_group_id=""):
        return {
            "platform": platform,
            "campaign_name": campaign,
            "campaign_id": campaign,
            "ad_group_name": group,
            "ad_group_id": ad_group_id,
            "ad_id": ad_id,
            "ad_label": ad_id,
            "spend": 10,
            "impressions": 100,
            "clicks": 5,
            "conversions": conversions,
        }

    GOOGLE_ROWS = [
        _row.__func__("google", "C1", "G1", "ad1", 10),
        _row.__func__("google", "C1", "G1", "ad2", 4),
    ]
    GOOGLE_MAP = {"google": {"ad1": {"Contact form": 6, "Phone call": 4}, "ad2": {"Contact form": 4}}}

    def test_no_selection_leaves_the_column_untouched(self):
        out = self._run(self.GOOGLE_ROWS, selected="__all__", maps=self.GOOGLE_MAP)
        self.assertEqual([r["v"] for r in out["rows"]], [10, 4])
        self.assertEqual(out["campaigns"][0]["v"], 14)
        self.assertFalse(out["total"]["na"])

    def test_selecting_an_action_narrows_every_level(self):
        out = self._run(self.GOOGLE_ROWS, selected="Contact form", maps=self.GOOGLE_MAP)
        self.assertEqual([r["v"] for r in out["rows"]], [6, 4])
        self.assertEqual(out["campaigns"][0]["groups"][0]["v"], 10)
        self.assertEqual(out["campaigns"][0]["v"], 10)
        self.assertEqual(out["total"]["v"], 10)

    def test_an_action_one_ad_never_recorded_is_zero_not_a_dash(self):
        # ad2 has Google data, just none for this action. That is a real zero.
        out = self._run(self.GOOGLE_ROWS, selected="Phone call", maps=self.GOOGLE_MAP)
        self.assertEqual([(r["v"], r["na"]) for r in out["rows"]], [(4, False), (0, False)])

    def test_microsoft_resolves_at_the_ad_group_and_dashes_its_ads(self):
        rows = [
            self._row("microsoft", "M1", "MG1", "mad1", 6, ad_group_id="g100"),
            self._row("microsoft", "M1", "MG1", "mad2", 2, ad_group_id="g100"),
        ]
        out = self._run(
            rows,
            selected="Contact form",
            maps={"microsoft": {"g100": {"Contact form": 5}}},
        )
        # Ads cannot answer — a dash, never an invented per-ad split.
        self.assertTrue(all(r["na"] for r in out["rows"]))
        # The ad group can, and the campaign is its sum.
        self.assertEqual(out["campaigns"][0]["groups"][0]["v"], 5)
        self.assertFalse(out["campaigns"][0]["groups"][0]["na"])
        self.assertEqual(out["campaigns"][0]["v"], 5)
        self.assertEqual(out["total"]["v"], 5)

    def test_microsoft_ad_group_with_no_breakdown_dashes_the_whole_branch(self):
        rows = [self._row("microsoft", "M1", "MG1", "mad1", 6, ad_group_id="g999")]
        out = self._run(rows, selected="Contact form", maps={"microsoft": {"g100": {"Contact form": 5}}})
        self.assertTrue(out["campaigns"][0]["groups"][0]["na"])
        self.assertTrue(out["campaigns"][0]["na"])
        self.assertTrue(out["total"]["na"])

    def test_linkedin_dashes_because_the_api_cannot_answer(self):
        rows = [self._row("linkedin", "L1", "LG1", "", 9)]
        out = self._run(rows, selected="Contact form")
        self.assertTrue(out["rows"][0]["na"])
        self.assertTrue(out["campaigns"][0]["na"])

    def test_the_total_ignores_branches_that_could_not_answer(self):
        # A mixed view must not read the dashes as zeros pulling the total down,
        # nor hide the total because one platform is silent.
        rows = self.GOOGLE_ROWS + [self._row("linkedin", "L1", "LG1", "", 9)]
        out = self._run(rows, selected="Contact form", maps=self.GOOGLE_MAP)
        self.assertEqual(out["total"]["v"], 10)
        self.assertFalse(out["total"]["na"])


if __name__ == "__main__":
    unittest.main()
