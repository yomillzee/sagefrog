from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import penn_business_lines as pbl  # noqa: E402

# The segment filter profile is config-driven: it comes ONLY from the client's
# stored segment_filter_profile, never from the client's name/slug/label. These
# tests pin that contract so nobody reintroduces client-name branching.


def _cfg(profile):
    return types.SimpleNamespace(segment_filter_profile=profile)


class ClientFilterProfileTests(unittest.TestCase):
    def test_profile_from_cfg_regions(self) -> None:
        self.assertEqual(pbl.client_filter_profile("anyslug", cfg=_cfg("regions")), "regions")

    def test_profile_from_cfg_business_lines(self) -> None:
        self.assertEqual(
            pbl.client_filter_profile("anyslug", cfg=_cfg("business_lines")),
            "business_lines",
        )

    def test_unset_profile_is_none(self) -> None:
        self.assertIsNone(pbl.client_filter_profile("anyslug", cfg=_cfg(None)))

    def test_unknown_profile_value_is_none(self) -> None:
        self.assertIsNone(pbl.client_filter_profile("anyslug", cfg=_cfg("penn")))

    def test_client_name_does_not_imply_profile(self) -> None:
        # Slugs/labels that used to trigger the old inference must now resolve to
        # None unless the stored config says otherwise.
        self.assertIsNone(pbl.client_filter_profile("nixon", cfg=_cfg(None)))
        self.assertIsNone(
            pbl.client_filter_profile(
                "penn", cfg=_cfg(None), label="Penn Community Bank"
            )
        )

    def test_profile_falls_back_to_stored_config(self) -> None:
        import client_dashboard_config as cdc
        orig = cdc.get_config
        cdc.get_config = lambda slug: _cfg("regions") if slug == "acme" else None
        try:
            self.assertEqual(pbl.client_filter_profile("acme"), "regions")
            self.assertIsNone(pbl.client_filter_profile("other"))
        finally:
            cdc.get_config = orig


class SegmentLabelTests(unittest.TestCase):
    def test_regions_label(self) -> None:
        self.assertEqual(pbl.segment_filter_label("x", cfg=_cfg("regions")), "Region")

    def test_business_lines_label(self) -> None:
        self.assertEqual(
            pbl.segment_filter_label("x", cfg=_cfg("business_lines")), "Business line"
        )


class BusinessLineBuiltinGatingTests(unittest.TestCase):
    """use_builtin (the banking keyword defaults) is gated on the business_lines
    profile, not on a client name."""

    def _breakdowns(self):
        return {"google": {"campaign": [{"id": "1", "campaign_name": "Home Equity - Spring"}]}}

    def test_builtin_applies_for_business_lines_profile(self) -> None:
        rows = pbl.build_business_line_campaigns(
            self._breakdowns(), client_slug="acme", filter_profile="business_lines"
        )
        self.assertEqual(rows[0]["business_line"], "home_equity")

    def test_builtin_skipped_without_profile(self) -> None:
        rows = pbl.build_business_line_campaigns(
            self._breakdowns(), client_slug="acme", filter_profile=None
        )
        self.assertEqual(rows[0]["business_line"], "other")


if __name__ == "__main__":
    unittest.main()
