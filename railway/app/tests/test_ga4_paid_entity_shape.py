"""Tests for shaping GA4 paid-entity rows into per-platform by_campaign/by_entity.

Guards: source_platform-first classification, term->ad set / content->ad
mapping, aggregation across source/medium splits of the same entity, and the
by_campaign id/name fallback — the structure that feeds build_ga4_level_index.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ga4_paid_entity_service as svc  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_source_platform_meta_wins(self):
        self.assertEqual(svc._classify_platform("Meta", "an", ""), "meta")

    def test_source_fallback_instagram(self):
        self.assertEqual(svc._classify_platform("", "instagram", "paid_social"), "meta")

    def test_google_ads_platform(self):
        self.assertEqual(svc._classify_platform("Google Ads", "google", "cpc"), "google")

    def test_unknown_returns_none(self):
        self.assertIsNone(svc._classify_platform("", "bing", "organic"))


class ShapeTest(unittest.TestCase):
    def test_aggregates_across_source_splits_and_maps_ids(self):
        # Same Meta ad appears under fb and ig source rows -> summed into one entity.
        raw = [
            {"source_platform": "Meta", "session_source": "facebook", "session_medium": "paid_social",
             "campaign_id": "C1", "campaign_name": "Home Mortgage",
             "manual_term": "AS1", "manual_ad_content": "AD1",
             "sessions": 100, "engaged_sessions": 60, "key_events": 5, "screen_page_views": 200},
            {"source_platform": "Meta", "session_source": "instagram", "session_medium": "paid_social",
             "campaign_id": "C1", "campaign_name": "Home Mortgage",
             "manual_term": "AS1", "manual_ad_content": "AD1",
             "sessions": 40, "engaged_sessions": 20, "key_events": 3, "screen_page_views": 80},
        ]
        out = svc.shape_paid_entity_rows(raw)
        self.assertIn("meta", out)
        ents = out["meta"]["by_entity"]
        self.assertEqual(len(ents), 1)
        e = ents[0]
        self.assertEqual((e["campaign_id"], e["adset_id"], e["ad_id"]), ("C1", "AS1", "AD1"))
        self.assertEqual(e["sessions"], 140)
        self.assertEqual(e["key_events"], 8)
        camps = out["meta"]["by_campaign"]
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0]["campaign_id"], "C1")
        self.assertEqual(camps[0]["key_events"], 8)

    def test_distinct_ads_split_into_entities_but_share_campaign(self):
        raw = [
            {"source_platform": "Meta", "session_source": "facebook", "session_medium": "paid_social",
             "campaign_id": "C1", "campaign_name": "HM", "manual_term": "AS1", "manual_ad_content": "AD1",
             "sessions": 10, "engaged_sessions": 5, "key_events": 1, "screen_page_views": 10},
            {"source_platform": "Meta", "session_source": "facebook", "session_medium": "paid_social",
             "campaign_id": "C1", "campaign_name": "HM", "manual_term": "AS1", "manual_ad_content": "AD2",
             "sessions": 20, "engaged_sessions": 8, "key_events": 4, "screen_page_views": 25},
        ]
        out = svc.shape_paid_entity_rows(raw)
        self.assertEqual(len(out["meta"]["by_entity"]), 2)
        self.assertEqual(len(out["meta"]["by_campaign"]), 1)
        self.assertEqual(out["meta"]["by_campaign"][0]["key_events"], 5)

    def test_unclassified_rows_dropped(self):
        raw = [
            {"source_platform": "", "session_source": "bing", "session_medium": "organic",
             "campaign_id": "C9", "campaign_name": "x", "manual_term": "", "manual_ad_content": "",
             "sessions": 5, "engaged_sessions": 1, "key_events": 9, "screen_page_views": 5},
        ]
        self.assertEqual(svc.shape_paid_entity_rows(raw), {})


if __name__ == "__main__":
    unittest.main()
