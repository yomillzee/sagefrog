"""Persistence round-trip for consent_store. Runs only when DATABASE_URL is set
(a throwaway Postgres); skipped otherwise so the pure suite stays hermetic."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires a Postgres DATABASE_URL")
class ConsentStoreTests(unittest.TestCase):
    def setUp(self):
        import consent_store
        self.store = consent_store
        self.store.ensure_schema()
        self.slug = "unittest-consent-client"
        with __import__("db").connection() as conn:
            conn.execute("DELETE FROM consent_scan_runs WHERE client_slug = %s", (self.slug,))
            conn.execute("DELETE FROM consent_scan_configs WHERE client_slug = %s", (self.slug,))

    def test_config_round_trip(self):
        self.store.save_config(
            self.slug, label="UT", pages=["https://a.example/", "https://a.example/x"],
            expectations={"banner_required": False}, scan_options={"foo": "bar"},
            scan_enabled=True, scan_frequency="daily", updated_by="tester")
        cfg = self.store.get_config(self.slug)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.pages, ["https://a.example/", "https://a.example/x"])
        self.assertTrue(cfg.scan_enabled)
        self.assertEqual(cfg.scan_frequency, "daily")
        self.assertEqual(cfg.expectations.get("banner_required"), False)

    def test_run_lifecycle_and_previous(self):
        self.store.save_config(self.slug, label="UT", pages=["https://a.example/"])
        rid1 = self.store.start_run(self.slug, trigger="manual", triggered_by="t")
        self.assertTrue(rid1)
        self.store.finish_run(rid1, status="completed", health="fail",
                              result={"health": "fail", "top_findings": []},
                              summary={"pages_scanned": 1}, pages_scanned=1,
                              vendor_count=2, violation_count=3, identifiers_before_consent=1)
        latest = self.store.latest_completed_run(self.slug)
        self.assertEqual(latest.health, "fail")
        self.assertEqual(latest.violation_count, 3)
        self.assertEqual(latest.result["health"], "fail")

        rid2 = self.store.start_run(self.slug, trigger="scheduled")
        prev = self.store.previous_completed_run(self.slug, rid2)
        self.assertEqual(prev.id, rid1)

    def test_due_and_orphan(self):
        self.store.save_config(self.slug, label="UT", pages=["https://a.example/"],
                               scan_enabled=True, scan_frequency="weekly")
        self.assertIn(self.slug, self.store.clients_due_for_scan())  # never scanned -> due
        rid = self.store.start_run(self.slug)  # leave running
        closed = self.store.fail_orphaned_runs(older_than_minutes=0)
        self.assertGreaterEqual(closed, 1)
        run = self.store.get_run(rid)
        self.assertEqual(run.status, "failed")


if __name__ == "__main__":
    unittest.main()
