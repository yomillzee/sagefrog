"""Meta "Results" reconstruction: warehouse conversions should match Ads Manager.

Reproduces the real discrepancy that motivated this: a single lead that Meta
reports under four redundant action buckets, alongside unrelated pixel events,
was being summed into ~28 "conversions" while Ads Manager showed 1 Result.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import meta_service  # noqa: E402


def _old_style_sum(actions: list[dict], values=None) -> float:
    """The pre-fix logic: sum every conversion-hint bucket, minus excludes.

    Kept in the test only, to prove the payload really did inflate to ~28 under
    the behavior we replaced.
    """
    conv_hints = (
        "purchase", "lead", "complete_registration", "submit_application",
        "offsite_conversion", "omni_purchase", "omni_lead", "onsite_conversion",
    )
    excl = (
        "link_click", "landing_page_view", "page_engagement", "post_engagement",
        "video_view", "post_reaction", "comment", "like",
    )
    total = 0.0
    for item in actions or []:
        at = str(item.get("action_type") or "").lower()
        if any(x in at for x in excl):
            continue
        if any(x in at for x in conv_hints):
            total += float(item.get("value") or 0)
    return total


# One real lead, reported by Meta under four redundant buckets, plus unrelated
# offsite pixel events (view_content/add_to_cart/search) and pure-engagement
# actions. This is the shape that produced "28".
ONE_LEAD_ACTIONS = [
    {"action_type": "lead", "value": "1"},
    {"action_type": "onsite_conversion.lead_grouped", "value": "1"},
    {"action_type": "offsite_conversion.fb_pixel_lead", "value": "1"},
    {"action_type": "omni_lead", "value": "1"},
    {"action_type": "offsite_conversion.fb_pixel_view_content", "value": "14"},
    {"action_type": "offsite_conversion.fb_pixel_add_to_cart", "value": "6"},
    {"action_type": "offsite_conversion.fb_pixel_search", "value": "4"},
    {"action_type": "link_click", "value": "40"},
    {"action_type": "landing_page_view", "value": "25"},
    {"action_type": "post_engagement", "value": "80"},
]


class ResultTokenResolutionTests(unittest.TestCase):
    def test_offsite_conversions_lead_maps_to_lead_token(self) -> None:
        tokens = meta_service._result_tokens_for_adset(
            "OFFSITE_CONVERSIONS", {"custom_event_type": "LEAD", "pixel_id": "1"}
        )
        self.assertEqual(tokens, {"lead"})

    def test_lead_generation_goal_maps_to_lead_token(self) -> None:
        self.assertEqual(
            meta_service._result_tokens_for_adset("LEAD_GENERATION", None), {"lead"}
        )

    def test_offsite_conversions_purchase_maps_to_purchase_token(self) -> None:
        self.assertEqual(
            meta_service._result_tokens_for_adset(
                "OFFSITE_CONVERSIONS", {"custom_event_type": "PURCHASE"}
            ),
            {"purchase"},
        )

    def test_awareness_goal_has_no_result_token(self) -> None:
        self.assertEqual(meta_service._result_tokens_for_adset("REACH", None), set())

    def test_link_clicks_goal_maps_to_link_click(self) -> None:
        self.assertEqual(
            meta_service._result_tokens_for_adset("LINK_CLICKS", None), {"link_click"}
        )

    def test_unknown_goal_falls_back(self) -> None:
        self.assertEqual(
            meta_service._result_tokens_for_adset("SOMETHING_NEW", None),
            set(meta_service._FALLBACK_TOKENS),
        )

    def test_custom_conversion_without_standard_event_falls_back(self) -> None:
        # custom_event_type OTHER → a custom pixel conversion we can't pin to a
        # standard event → de-duplicated fallback rather than a wrong guess.
        self.assertEqual(
            meta_service._result_tokens_for_adset(
                "OFFSITE_CONVERSIONS", {"custom_event_type": "OTHER"}
            ),
            set(meta_service._FALLBACK_TOKENS),
        )


class ResultCountingTests(unittest.TestCase):
    def test_old_logic_inflated_one_lead_to_28(self) -> None:
        # Guardrail: prove the payload is the "28 vs 1" case we're fixing.
        self.assertEqual(_old_style_sum(ONE_LEAD_ACTIONS), 28.0)

    def test_lead_token_counts_one_result(self) -> None:
        parsed = meta_service._parse_insight_row(
            {"actions": ONE_LEAD_ACTIONS}, result_tokens={"lead"}
        )
        self.assertEqual(parsed["conversions"], 1.0)

    def test_awareness_adset_reports_zero(self) -> None:
        parsed = meta_service._parse_insight_row(
            {"actions": ONE_LEAD_ACTIONS}, result_tokens=set()
        )
        self.assertEqual(parsed["conversions"], 0.0)

    def test_distinct_tokens_are_summed(self) -> None:
        # A campaign optimizing both leads and purchases: results = leads + purchases,
        # each de-duplicated within its own bucket family.
        actions = [
            {"action_type": "offsite_conversion.fb_pixel_lead", "value": "2"},
            {"action_type": "omni_lead", "value": "2"},  # dup of the same 2 leads
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
            {"action_type": "omni_purchase", "value": "3"},  # dup of the same 3
        ]
        parsed = meta_service._parse_insight_row(
            {"actions": actions}, result_tokens={"lead", "purchase"}
        )
        self.assertEqual(parsed["conversions"], 5.0)

    def test_fallback_dedupes_but_may_include_other_events(self) -> None:
        # With no resolver we can't know the goal, so the fallback de-duplicates
        # each event (lead counted once, not 4x) but still picks up add_to_cart.
        # It is NOT the old 28, and NOT the exact 1 — which is why sync resolves
        # the real optimization goal instead of relying on this path.
        parsed = meta_service._parse_insight_row({"actions": ONE_LEAD_ACTIONS})
        self.assertEqual(parsed["conversions"], 7.0)  # 1 lead + 6 add_to_cart

    def test_conversion_value_uses_same_tokens(self) -> None:
        row = {
            "actions": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "2"}],
            "action_values": [
                {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "150.0"},
                {"action_type": "offsite_conversion.fb_pixel_view_content", "value": "999.0"},
            ],
        }
        parsed = meta_service._parse_insight_row(row, result_tokens={"purchase"})
        self.assertEqual(parsed["conversions"], 2.0)
        self.assertEqual(parsed["conversion_value"], 150.0)


class ResultResolverTests(unittest.TestCase):
    def test_resolver_builds_adset_and_campaign_maps(self) -> None:
        adsets = [
            {"id": "as1", "campaign_id": "c1", "optimization_goal": "OFFSITE_CONVERSIONS",
             "promoted_object": {"custom_event_type": "LEAD"}},
            {"id": "as2", "campaign_id": "c1", "optimization_goal": "OFFSITE_CONVERSIONS",
             "promoted_object": {"custom_event_type": "PURCHASE"}},
            {"id": "as3", "campaign_id": "c2", "optimization_goal": "REACH"},
        ]
        with patch.object(meta_service, "_graph_get_all", return_value=adsets), \
                patch.object(meta_service, "load_meta_env") as env:
            env.return_value.access_token = "tok"
            resolver = meta_service.fetch_result_resolver("act_1", access_token="tok")

        self.assertEqual(resolver.adset_tokens("as1"), {"lead"})
        self.assertEqual(resolver.adset_tokens("as2"), {"purchase"})
        self.assertEqual(resolver.adset_tokens("as3"), set())
        # Campaign c1 spans a lead ad set and a purchase ad set → union.
        self.assertEqual(resolver.campaign_tokens("c1"), {"lead", "purchase"})
        self.assertEqual(resolver.campaign_tokens("c2"), set())

    def test_unknown_ids_fall_back_not_zeroed(self) -> None:
        resolver = meta_service.ResultResolver({}, {})
        self.assertEqual(resolver.adset_tokens("missing"), set(meta_service._FALLBACK_TOKENS))
        self.assertEqual(resolver.campaign_tokens("missing"), set(meta_service._FALLBACK_TOKENS))


if __name__ == "__main__":
    unittest.main()
