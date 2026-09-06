from __future__ import annotations

import json
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


def _fake_row(**overrides):
    base = {
        "client_slug": "acme", "label": "Acme",
        "google_customer_id": None, "linkedin_account_id": None,
        "meta_account_id": None, "ga4_client_key": None,
    }
    base.update(overrides)
    return cdc.ClientConfigRow(**base)


@contextmanager
def _patched(fake, saved_row):
    """Patch the DB layer + get_config so save_* runs against a fake conn.

    save_* functions call get_config twice (existing label + reload); returning
    a stable fake row keeps them out of the real database."""
    with patch.object(cdc, "enabled", return_value=True), \
         patch.object(cdc, "ensure_schema", return_value=True), \
         patch.object(cdc, "get_config", return_value=saved_row), \
         patch.object(cdc.db, "connection", lambda: _ctx(fake)):
        yield


@contextmanager
def _ctx(fake):
    yield fake


class DefaultDatePresetTests(unittest.TestCase):
    def test_normalize_date_preset(self):
        self.assertEqual(cdc._normalize_date_preset("last_month"), "last_month")
        self.assertEqual(cdc._normalize_date_preset(" Last_Month "), "last_month")
        self.assertIsNone(cdc._normalize_date_preset("bogus"))
        self.assertIsNone(cdc._normalize_date_preset(""))
        self.assertIsNone(cdc._normalize_date_preset(None))

    def test_save_upserts_only_that_column(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row(default_date_preset="last_month")):
            cdc.save_default_date_preset("acme", "last_month", updated_by="admin@x.com")
        self.assertEqual(len(fake.calls), 1)
        sql, params = fake.calls[0]
        self.assertIn("default_date_preset = EXCLUDED.default_date_preset", sql)
        self.assertNotIn("explorer_filters", sql)
        self.assertIn("last_month", params)

    def test_save_clears_on_empty(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row()):
            cdc.save_default_date_preset("acme", "", updated_by="admin@x.com")
        _sql, params = fake.calls[0]
        self.assertIn(None, params)  # normalized empty -> NULL

    def test_save_rejects_unknown_preset(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row()):
            with self.assertRaises(ValueError):
                cdc.save_default_date_preset("acme", "since_forever")
        self.assertEqual(fake.calls, [])  # nothing written


class CampaignAllowlistTests(unittest.TestCase):
    def test_normalize_allowlist(self):
        self.assertEqual(
            cdc._normalize_campaign_allowlist(["A", " B ", "A", "", "C"]),
            ("A", "B", "C"),
        )
        self.assertEqual(cdc._normalize_campaign_allowlist('["X","Y"]'), ("X", "Y"))
        self.assertEqual(cdc._normalize_campaign_allowlist(None), ())
        self.assertEqual(cdc._normalize_campaign_allowlist("not json"), ())

    def test_save_upserts_only_that_column_as_json(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row(explorer_campaign_allowlist=("Brand - Search",))):
            cdc.save_explorer_campaign_allowlist(
                "acme", ["Brand - Search", " Brand - Search ", "LinkedIn - ABM"],
            )
        self.assertEqual(len(fake.calls), 1)
        sql, params = fake.calls[0]
        self.assertIn(
            "explorer_campaign_allowlist = EXCLUDED.explorer_campaign_allowlist", sql
        )
        self.assertNotIn("explorer_filters =", sql)
        # Stored as a JSON array, deduped/trimmed.
        payload = next(p for p in params if isinstance(p, str) and p.startswith("["))
        self.assertEqual(json.loads(payload), ["Brand - Search", "LinkedIn - ABM"])

    def test_save_empty_clears_restriction(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row()):
            cdc.save_explorer_campaign_allowlist("acme", [])
        _sql, params = fake.calls[0]
        payload = next(p for p in params if isinstance(p, str) and p.startswith("["))
        self.assertEqual(json.loads(payload), [])


class EmailPerformanceSelectionTests(unittest.TestCase):
    def test_normalize_id_list(self):
        self.assertEqual(
            cdc._normalize_id_list(["1", " 2 ", "1", "", "3"]),
            ("1", "2", "3"),
        )
        self.assertEqual(cdc._normalize_id_list('["7","8"]'), ("7", "8"))
        self.assertEqual(cdc._normalize_id_list([42, 7]), ("42", "7"))
        self.assertEqual(cdc._normalize_id_list(None), ())
        self.assertEqual(cdc._normalize_id_list("not json"), ())

    def test_save_upserts_only_that_column_as_json(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row(email_performance_selection=("12", "34"))):
            cdc.save_email_performance_selection(
                "acme", ["12", " 12 ", "34"], updated_by="admin@x.com",
            )
        self.assertEqual(len(fake.calls), 1)
        sql, params = fake.calls[0]
        self.assertIn(
            "email_performance_selection = EXCLUDED.email_performance_selection", sql
        )
        self.assertNotIn("explorer_campaign_allowlist =", sql)
        # Stored as a JSON array, deduped/trimmed, order preserved.
        payload = next(p for p in params if isinstance(p, str) and p.startswith("["))
        self.assertEqual(json.loads(payload), ["12", "34"])

    def test_save_empty_clears_selection(self):
        fake = _FakeConn()
        with _patched(fake, _fake_row()):
            cdc.save_email_performance_selection("acme", [])
        _sql, params = fake.calls[0]
        payload = next(p for p in params if isinstance(p, str) and p.startswith("["))
        self.assertEqual(json.loads(payload), [])


if __name__ == "__main__":
    unittest.main()
