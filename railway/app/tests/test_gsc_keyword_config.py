from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_dashboard_config as cdc  # noqa: E402


class _FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self


class GscKeywordConfigTests(unittest.TestCase):
    def test_update_gsc_keywords_upserts_only_keyword_columns(self):
        fake = _FakeConn()

        @contextmanager
        def fake_connection():
            yield fake

        with patch.object(cdc, "enabled", return_value=True), \
             patch.object(cdc, "ensure_schema", return_value=True), \
             patch.object(cdc.db, "connection", fake_connection):
            cdc.update_gsc_keywords(
                "acme", branded_roots="acme\nacme co",
                target_keywords="running shoes", updated_by="admin@x.com",
            )

        self.assertEqual(len(fake.calls), 1)
        sql, params = fake.calls[0]
        self.assertIn("gsc_branded_roots = EXCLUDED.gsc_branded_roots", sql)
        self.assertIn("gsc_target_keywords = EXCLUDED.gsc_target_keywords", sql)
        # Does NOT touch label/accounts in the UPDATE clause.
        self.assertNotIn("google_customer_id = EXCLUDED", sql)
        self.assertIn("acme\nacme co", params)
        self.assertIn("running shoes", params)

    def test_blank_values_stored_as_null(self):
        fake = _FakeConn()

        @contextmanager
        def fake_connection():
            yield fake

        with patch.object(cdc, "enabled", return_value=True), \
             patch.object(cdc, "ensure_schema", return_value=True), \
             patch.object(cdc.db, "connection", fake_connection):
            cdc.update_gsc_keywords("acme", branded_roots="  ", target_keywords="")

        _sql, params = fake.calls[0]
        # Both cleaned to None (cleared).
        self.assertIn(None, params)

    def test_update_ga4_key_events_upserts_only_that_column(self):
        fake = _FakeConn()

        @contextmanager
        def fake_connection():
            yield fake

        with patch.object(cdc, "enabled", return_value=True), \
             patch.object(cdc, "ensure_schema", return_value=True), \
             patch.object(cdc.db, "connection", fake_connection):
            cdc.update_ga4_key_events("acme", event_names="form_submit\ngenerate_lead")

        sql, params = fake.calls[0]
        self.assertIn("ga4_key_events = EXCLUDED.ga4_key_events", sql)
        self.assertNotIn("gsc_branded_roots", sql)
        self.assertIn("form_submit\ngenerate_lead", params)


if __name__ == "__main__":
    unittest.main()
