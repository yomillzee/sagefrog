"""A GSC sync where the Search Console fetches fail must be reported as failed.

sync_range() puts per-day fetch failures in "errors" (a list) and only its own
setup failures in "error" (a string). The connector used to read just "error",
so a client whose every daily fetch failed (wrong property, revoked OAuth, 403)
logged "completed, 0 rows, no error" in Sync history every day, indefinitely.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from connectors.gsc import GSCConnector, _describe_errors  # noqa: E402


class _Stubs:
    """Stand in for the modules run_sync imports lazily."""

    def __init__(self, refresh_result: dict) -> None:
        self.refresh_result = refresh_result
        self._saved: dict[str, object] = {}

    def __enter__(self):
        cfg = types.SimpleNamespace(
            source_account_id="sc-domain:example.com",
            bq_project_id="example-proj",
            raw_dataset_id="raw_gsc",
        )
        mods = {
            "client_dashboard_config": types.SimpleNamespace(get_config=lambda slug: None),
            "connector_config_store": types.SimpleNamespace(get_config=lambda slug, ctype: cfg),
            "gsc_clients": types.SimpleNamespace(target_from_config=lambda slug, c: object()),
            "gsc_sync_service": types.SimpleNamespace(
                sync_for_refresh=lambda **kw: self.refresh_result
            ),
            # Mart-view provisioning is best-effort and irrelevant here.
            "bq_gsc_service": types.SimpleNamespace(create_gsc_mart_views=lambda **kw: None),
        }
        for name, mod in mods.items():
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = mod
        return self

    def __exit__(self, *exc):
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        return False


def _sync(refresh_result: dict):
    with _Stubs(refresh_result):
        return GSCConnector().run_sync(client_slug="acme")


class GscSyncErrorReportingTests(unittest.TestCase):
    def test_per_day_fetch_failures_are_reported_as_a_failed_run(self):
        res = _sync({
            "ok": False,
            "days_synced": 2,
            "query_rows": 0,
            "page_rows": 0,
            "errors": [
                "2026-08-17 query: HTTP Error 403: Forbidden",
                "2026-08-17 page: HTTP Error 403: Forbidden",
            ],
        })
        self.assertFalse(res.ok)
        self.assertIn("403", res.error)
        self.assertEqual(res.rows_loaded, 0)

    def test_setup_error_string_still_wins(self):
        res = _sync({"ok": False, "error": "BQ setup failed: no dataset"})
        self.assertFalse(res.ok)
        self.assertIn("BQ setup failed", res.error)

    def test_successful_sync_reports_rows_and_no_error(self):
        res = _sync({"ok": True, "query_rows": 900, "page_rows": 100, "errors": []})
        self.assertTrue(res.ok)
        self.assertEqual(res.rows_loaded, 1000)

    def test_up_to_date_noop_is_not_a_failure(self):
        res = _sync({"ok": True, "status": "up_to_date"})
        self.assertTrue(res.ok)
        self.assertEqual(res.rows_loaded, 0)

    def test_describe_errors_summarises_a_long_list(self):
        msg = _describe_errors([f"2026-08-{d:02d} query: boom" for d in range(1, 11)])
        self.assertTrue(msg.startswith("10 day(s) failed:"))
        self.assertIn("(+7 more)", msg)
        self.assertLessEqual(len(msg), 500)

    def test_describe_errors_returns_none_when_clean(self):
        self.assertIsNone(_describe_errors([]))
        self.assertIsNone(_describe_errors(None))


if __name__ == "__main__":
    unittest.main()
