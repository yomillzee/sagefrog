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


class MultiIndustryTests(unittest.TestCase):
    """An account can straddle two markets, so a tag is a set of keys."""

    def test_stored_string_parses_back_to_its_keys(self):
        self.assertEqual(
            client_industries.normalize_many(
                "healthcare_life_sciences,technology_software"
            ),
            ("healthcare_life_sciences", "technology_software"),
        )

    def test_a_list_of_form_values_parses_the_same_way(self):
        self.assertEqual(
            client_industries.normalize_many(
                ["technology_software", " Healthcare_Life_Sciences "]
            ),
            ("healthcare_life_sciences", "technology_software"),
        )

    def test_order_is_the_taxonomy_s_not_the_input_s(self):
        # So a tag renders identically however the boxes were ticked, and the
        # stored value does not churn between saves.
        forward = client_industries.normalize_many(["aec", "other"])
        backward = client_industries.normalize_many(["other", "aec"])
        self.assertEqual(forward, backward)
        self.assertEqual(forward, ("aec", "other"))

    def test_duplicates_and_unknowns_drop_out_without_taking_siblings(self):
        self.assertEqual(
            client_industries.normalize_many(["aec", "aec", "widgets", ""]), ("aec",)
        )

    def test_empty_inputs_are_an_empty_tag(self):
        for raw in (None, "", "   ", [], [""], ["widgets"], ","):
            self.assertEqual(client_industries.normalize_many(raw), (), raw)

    def test_serialize_round_trips_through_normalize_many(self):
        keys = ("healthcare_life_sciences", "technology_software")
        stored = client_industries.serialize(keys)
        self.assertEqual(stored, "healthcare_life_sciences,technology_software")
        self.assertEqual(client_industries.normalize_many(stored), keys)

    def test_serialize_of_nothing_is_none_not_empty_string(self):
        # Untagged rows must stay SQL-NULL — the coverage count and the
        # "WHERE industry IS NOT NULL" read both depend on it.
        for raw in (None, "", [], ["widgets"]):
            self.assertIsNone(client_industries.serialize(raw), raw)

    def test_a_single_key_written_before_multi_select_still_reads(self):
        self.assertEqual(
            client_industries.normalize_many("industrial_manufacturing"),
            ("industrial_manufacturing",),
        )

    def test_label_list_joins_labels_and_falls_back_to_unassigned(self):
        self.assertEqual(
            client_industries.label_list(["technology_software", "healthcare_life_sciences"]),
            "Health & Life Sciences · Technology & Software",
        )
        self.assertEqual(
            client_industries.label_list([]), client_industries.UNASSIGNED_LABEL
        )

    def test_labels_for_is_empty_when_untagged(self):
        self.assertEqual(client_industries.labels_for(None), ())
        self.assertEqual(
            client_industries.labels_for("other"), ("Other",)
        )

    def test_separator_is_not_usable_inside_a_key(self):
        # The storage format is comma-joined; a key containing the separator
        # would be unparseable on the way back out.
        for key in client_industries.keys():
            self.assertNotIn(client_industries.SEPARATOR, key)


if __name__ == "__main__":
    unittest.main()
