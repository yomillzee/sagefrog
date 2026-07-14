"""Tests for GA4 -> Meta ID-first attribution matching at campaign/adset/ad level.

Covers build_ga4_level_index, which joins GA4 onsite session rows to ad-platform
entities by exact object id (Meta stamps campaign/adset/ad ids into
utm_id/utm_term/utm_content). Guards: exact-id matching, aggregation across rows
sharing an id, dropping untagged/unknown ids, and engagement_rate math.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ga4_attribution_service as svc  # noqa: E402


class BuildGa4LevelIndexTest(unittest.TestCase):
    def test_adset_level_matches_by_id_and_aggregates(self):
        # Two GA4 entity rows share the same ad set id (different ads) -> summed.
        ga4_rows = [
            {"campaign_id": "C1", "adset_id": "AS1", "ad_id": "AD1",
             "sessions": 10, "engaged_sessions": 6, "key_events": 2, "page_views": 20},
            {"campaign_id": "C1", "adset_id": "AS1", "ad_id": "AD2",
             "sessions": 5, "engaged_sessions": 3, "key_events": 1, "page_views": 8},
        ]
        adsets = [{"id": "AS1", "name": "Ad set one"}]
        index = svc.build_ga4_level_index(ga4_rows, adsets, level="adset")
        self.assertIn("AS1", index)
        self.assertEqual(index["AS1"]["sessions"], 15)
        self.assertEqual(index["AS1"]["key_events"], 3)
        self.assertEqual(index["AS1"]["page_views"], 28)
        self.assertEqual(index["AS1"]["engagement_rate"], round(9 / 15, 4))

    def test_ad_level_keys_on_ad_id(self):
        ga4_rows = [
            {"campaign_id": "C1", "adset_id": "AS1", "ad_id": "AD1",
             "sessions": 4, "engaged_sessions": 2, "key_events": 1, "page_views": 4},
        ]
        ads = [{"id": "AD1", "name": "Ad one"}]
        index = svc.build_ga4_level_index(ga4_rows, ads, level="ad")
        self.assertEqual(list(index.keys()), ["AD1"])
        self.assertEqual(index["AD1"]["key_events"], 1)

    def test_unknown_or_untagged_ids_are_dropped(self):
        # AS9 has no matching ad entity; empty adset_id row is untagged. Both dropped.
        ga4_rows = [
            {"campaign_id": "C1", "adset_id": "AS9", "ad_id": "AD9",
             "sessions": 3, "engaged_sessions": 1, "key_events": 5, "page_views": 3},
            {"campaign_id": "C1", "adset_id": "", "ad_id": "",
             "sessions": 7, "engaged_sessions": 4, "key_events": 9, "page_views": 7},
        ]
        adsets = [{"id": "AS1", "name": "Only known adset"}]
        index = svc.build_ga4_level_index(ga4_rows, adsets, level="adset")
        self.assertEqual(index, {})

    def test_zero_sessions_yields_zero_engagement_rate(self):
        ga4_rows = [
            {"campaign_id": "C1", "adset_id": "AS1", "ad_id": "AD1",
             "sessions": 0, "engaged_sessions": 0, "key_events": 0, "page_views": 0},
        ]
        adsets = [{"id": "AS1"}]
        index = svc.build_ga4_level_index(ga4_rows, adsets, level="adset")
        self.assertEqual(index["AS1"]["engagement_rate"], 0.0)

    def test_unknown_level_raises(self):
        with self.assertRaises(ValueError):
            svc.build_ga4_level_index([], [], level="keyword")


if __name__ == "__main__":
    unittest.main()
