from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_industries


class TaxonomyShapeTests(unittest.TestCase):
    """The list is edited by hand; these guard the invariants that edit can break."""

    def test_keys_are_unique(self):
        keys = client_industries.keys()
        self.assertEqual(len(keys), len(set(keys)))

    def test_labels_are_unique(self):
        labels = [label for _key, label in client_industries.choices()]
        self.assertEqual(len(labels), len(set(labels)))

    def test_bucket_count_stays_browsable(self):
        # The taxonomy is meant to be a dozen-ish recognizable buckets someone can
        # scan in a dropdown — not a full NAICS tree.
        self.assertGreaterEqual(len(client_industries.INDUSTRIES), 12)
        self.assertLessEqual(len(client_industries.INDUSTRIES), 18)

    def test_keys_are_storage_safe(self):
        # Keys land in Postgres and in URLs/JSON; keep them lowercase snake_case.
        for key in client_industries.keys():
            self.assertRegex(key, r"^[a-z][a-z0-9_]*$")

    def test_other_bucket_exists_and_is_last(self):
        # "Other" is the explicit catch-all, and reads last in a dropdown.
        self.assertEqual(client_industries.INDUSTRIES[-1][0], "other")


class NormalizeTests(unittest.TestCase):
    def test_known_key_round_trips(self):
        self.assertEqual(
            client_industries.normalize("industrial_manufacturing"),
            "industrial_manufacturing",
        )

    def test_case_and_whitespace_are_forgiven(self):
        self.assertEqual(
            client_industries.normalize("  Industrial_Manufacturing "),
            "industrial_manufacturing",
        )

    def test_unknown_and_empty_normalize_to_none(self):
        # A retired key must degrade to "unassigned", not raise in the admin panel.
        for raw in ("", "   ", None, "widgets", "healthcare"):
            self.assertIsNone(client_industries.normalize(raw))

    def test_label_for_unknown_reads_unassigned(self):
        self.assertEqual(
            client_industries.label_for("nope"), client_industries.UNASSIGNED_LABEL
        )
        self.assertEqual(
            client_industries.label_for(None), client_industries.UNASSIGNED_LABEL
        )

    def test_unassigned_is_not_a_bucket_label(self):
        # "Unassigned" (no tag) and "Other" (deliberately bucketed) must stay
        # distinct — coverage reporting depends on it.
        labels = {label for _k, label in client_industries.choices()}
        self.assertNotIn(client_industries.UNASSIGNED_LABEL, labels)

    def test_is_valid(self):
        self.assertTrue(client_industries.is_valid("other"))
        self.assertFalse(client_industries.is_valid(""))
        self.assertFalse(client_industries.is_valid("nope"))


if __name__ == "__main__":
    unittest.main()
