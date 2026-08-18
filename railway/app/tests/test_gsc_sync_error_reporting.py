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
from datetime import date
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import gsc_sync_service  # noqa: E402
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
            "days_synced": 1,
            "query_rows": 0,
            "page_rows": 0,
            "errors": [
                "2026-08-17 query: HTTP 403 for sc-domain:example.com: denied",
                "2026-08-17 page: HTTP 403 for sc-domain:example.com: denied",
            ],
            "error_count": 2,
            "failed_days": 1,
        })
        self.assertFalse(res.ok)
        self.assertIn("403", res.error)
        self.assertEqual(res.rows_loaded, 0)

    def test_message_reports_real_totals_not_the_capped_list(self):
        """sync_range caps "errors" at 10, and each day contributes two entries,
        so the message must come from failed_days/days_synced -- otherwise a
        180-day backfill that never worked reads as "10 days failed"."""
        res = _sync({
            "ok": False,
            "days_synced": 180,
            "query_rows": 0,
            "page_rows": 0,
            "errors": [
                f"2026-08-{d:02d} query: HTTP 403 for sc-domain:example.com: denied"
                for d in range(6, 16)
            ],
            "error_count": 360,
            "failed_days": 180,
        })
        self.assertIn("180 of 180 days failed", res.error)
        self.assertNotIn("10 day", res.error)

    def test_one_repeated_reason_is_not_repeated_per_day(self):
        res = _sync({
            "ok": False,
            "days_synced": 5,
            "errors": [
                f"2026-08-1{d} {dim}: HTTP 403 for sc-domain:example.com: "
                "User does not have sufficient permission for site"
                for d in range(5) for dim in ("query", "page")
            ],
            "error_count": 10,
            "failed_days": 5,
        })
        self.assertEqual(res.error.count("sufficient permission"), 1)
        self.assertIn("5 of 5 days failed", res.error)

    def test_partial_failure_keeps_the_rows_that_did_land(self):
        res = _sync({
            "ok": False,
            "days_synced": 30,
            "query_rows": 4000,
            "page_rows": 800,
            "errors": ["2026-08-01 query: HTTP 500 for sc-domain:example.com: backend error"],
            "error_count": 1,
            "failed_days": 1,
        })
        self.assertFalse(res.ok)
        self.assertEqual(res.rows_loaded, 4800)
        self.assertIn("1 of 30 days failed", res.error)

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

    def test_describe_errors_falls_back_to_the_list_without_totals(self):
        msg = _describe_errors([f"2026-08-{d:02d} query: boom {d}" for d in range(1, 11)])
        self.assertIn("10 fetch(es) failed", msg)
        self.assertIn("(+8 other reasons)", msg)
        self.assertLessEqual(len(msg), 500)

    def test_describe_errors_caps_the_column_width(self):
        msg = _describe_errors(
            [f"2026-08-{d:02d} query: {'x' * 400} {d}" for d in range(1, 11)],
            failed_days=10, error_count=20, days_synced=180,
        )
        self.assertEqual(len(msg), 500)

    def test_describe_errors_returns_none_when_clean(self):
        self.assertIsNone(_describe_errors([]))
        self.assertIsNone(_describe_errors(None))


class GscListAccountsTests(unittest.TestCase):
    """An empty property list must mean "no properties", not "lookup broke"."""

    def _connector_with(self, side_effect=None, props=None):
        mod = types.SimpleNamespace(
            list_accessible_properties=(
                (lambda: (_ for _ in ()).throw(side_effect)) if side_effect
                else (lambda: props or [])
            )
        )
        saved = sys.modules.get("gsc_sync_service")
        sys.modules["gsc_sync_service"] = mod
        self.addCleanup(
            lambda: sys.modules.__setitem__("gsc_sync_service", saved)
            if saved is not None else sys.modules.pop("gsc_sync_service", None)
        )
        return GSCConnector()

    def test_a_broken_connection_raises_instead_of_looking_empty(self):
        conn = self._connector_with(side_effect=RuntimeError("webmasters API 403: denied"))
        with self.assertRaises(RuntimeError) as caught:
            conn.list_accounts(client_slug="acme")
        msg = str(caught.exception)
        self.assertIn("403", msg)
        # Points at the fix, not just the symptom.
        self.assertIn("Connect Google Search Console", msg)

    def test_test_connection_fails_when_the_lookup_is_broken(self):
        conn = self._connector_with(side_effect=RuntimeError("Token refresh failed"))
        with self.assertRaises(RuntimeError):
            conn.test_connection(client_slug="acme")

    def test_genuinely_empty_list_is_still_an_empty_list(self):
        conn = self._connector_with(props=[])
        self.assertEqual(conn.list_accounts(client_slug="acme"), [])

    def test_properties_map_to_id_and_name(self):
        conn = self._connector_with(props=[
            {"site_url": "sc-domain:example.com", "permission_level": "siteOwner"},
            {"site_url": ""},  # skipped — an empty id is not selectable
        ])
        self.assertEqual(
            conn.list_accounts(client_slug="acme"),
            [{"id": "sc-domain:example.com", "name": "sc-domain:example.com"}],
        )


class GscApiErrorBodyTests(unittest.TestCase):
    """A 403's reason lives in the response body, not in urllib's str()."""

    @staticmethod
    def _http_error(code: int, message: str):
        import io
        import json
        import urllib.error
        body = json.dumps({"error": {"code": code, "message": message}}).encode()
        return urllib.error.HTTPError(
            "https://www.googleapis.com/webmasters/v3/x", code, "Forbidden", {},
            io.BytesIO(body),
        )

    def test_post_carries_googles_reason_and_the_site_url(self):
        err = self._http_error(403, "User does not have sufficient permission for site 'sc-domain:x.com'")
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(gsc_sync_service.GscApiError) as caught:
                gsc_sync_service._gsc_post("tok", "sc-domain:x.com", {})
        msg = str(caught.exception)
        self.assertIn("403", msg)
        self.assertIn("sufficient permission", msg)
        # The site URL we actually asked for -- a mismatch with the verified
        # property is the usual cause, so it has to be in the message.
        self.assertIn("sc-domain:x.com", msg)
        self.assertEqual(caught.exception.code, 403)

    def test_body_that_is_not_json_still_raises_with_the_code(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError("https://x", 500, "Server Error", {}, io.BytesIO(b"<html>nope"))
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(gsc_sync_service.GscApiError) as caught:
                gsc_sync_service._gsc_post("tok", "sc-domain:x.com", {})
        self.assertEqual(caught.exception.code, 500)
        self.assertIn("500", str(caught.exception))

    def test_fetch_day_propagates_the_reason_instead_of_retrying(self):
        """Only 429 retries; a 403 must surface immediately, not spin."""
        err = self._http_error(403, "User does not have sufficient permission for site")
        creds = types.SimpleNamespace(valid=True, token="tok")
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(gsc_sync_service.GscApiError):
                gsc_sync_service._fetch_day(creds, "sc-domain:x.com", date(2026, 8, 15), "query")


if __name__ == "__main__":
    unittest.main()
