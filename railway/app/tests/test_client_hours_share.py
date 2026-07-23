from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# client_hours_share only needs the `db` module at import time; stub it so the
# no-DB (graceful-degradation) paths run without a live Postgres, mirroring how
# test_harvest_service stubs its runtime deps.
sys.modules.setdefault("db", types.ModuleType("db"))

import os  # noqa: E402

import client_hours_share  # noqa: E402


class ShareLinkNoDatabaseTest(unittest.TestCase):
    """With DATABASE_URL unset the store degrades gracefully rather than raising
    on reads — and writes fail loudly instead of silently no-op'ing."""

    def setUp(self):
        self._saved = os.environ.pop("DATABASE_URL", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["DATABASE_URL"] = self._saved

    def test_enabled_false_without_database_url(self):
        self.assertFalse(client_hours_share._enabled())

    def test_reads_return_empty_or_none(self):
        self.assertEqual(client_hours_share.list_share_links(), [])
        self.assertIsNone(client_hours_share.resolve_share_token("anything"))

    def test_resolve_blank_token_is_none(self):
        os.environ["DATABASE_URL"] = "postgres://x"  # enabled, but token is blank
        try:
            self.assertIsNone(client_hours_share.resolve_share_token(""))
            self.assertIsNone(client_hours_share.resolve_share_token("   "))
        finally:
            os.environ.pop("DATABASE_URL", None)

    def test_create_requires_database(self):
        with self.assertRaises(RuntimeError):
            client_hours_share.create_share_link(created_by="a@b.c")

    def test_revoke_blank_token_is_false(self):
        # A blank token short-circuits to False before touching the DB.
        self.assertFalse(client_hours_share.revoke_share_link(token=""))


class ShareLinkStoreTest(unittest.TestCase):
    """Create / list / resolve / revoke against an in-memory fake of the one
    table this module owns, exercising the SQL branches without a real Postgres."""

    def setUp(self):
        os.environ["DATABASE_URL"] = "postgres://fake"
        self.rows: dict[str, dict] = {}
        fake_db = sys.modules["db"]
        fake_db.connection = self._fake_connection  # type: ignore[attr-defined]

    def tearDown(self):
        os.environ.pop("DATABASE_URL", None)

    # A tiny context-manager + cursor that understands only the handful of
    # statements client_hours_share issues (matched by substring).
    def _fake_connection(self):
        store = self.rows

        class _Cur:
            def __init__(self):
                self.rowcount = 0
                self._fetch = None

            def fetchone(self):
                return self._fetch

            def fetchall(self):
                return self._fetch or []

        class _Conn:
            def execute(self, sql, params=()):
                cur = _Cur()
                s = " ".join(sql.split())
                if s.startswith("CREATE TABLE"):
                    return cur
                if s.startswith("INSERT INTO client_hours_share_links"):
                    token, label, creator = params
                    store[token] = {
                        "token": token, "label": label, "created_by": creator,
                        "revoked_at": None, "revoked_by": None,
                        "last_viewed_at": None, "view_count": 0,
                    }
                    return cur
                if s.startswith("SELECT") and "ORDER BY created_at DESC" in s:
                    active_only = "revoked_at IS NULL" in s
                    out = []
                    for r in store.values():
                        if active_only and r["revoked_at"] is not None:
                            continue
                        out.append((
                            r["token"], r["label"], r["created_by"], None,
                            r["revoked_at"], r["revoked_by"], r["last_viewed_at"],
                            r["view_count"],
                        ))
                    cur._fetch = out
                    return cur
                if s.startswith("UPDATE") and "view_count = view_count + 1" in s:
                    token = params[0]
                    r = store.get(token)
                    if r and r["revoked_at"] is None:
                        r["view_count"] += 1
                        cur._fetch = (r["token"], r["label"], r["created_by"])
                    return cur
                if s.startswith("SELECT") and "WHERE token" in s and "revoked_at IS NULL" in s:
                    token = params[0]
                    r = store.get(token)
                    if r and r["revoked_at"] is None:
                        cur._fetch = (r["token"], r["label"], r["created_by"])
                    return cur
                if s.startswith("UPDATE") and "revoked_at = NOW()" in s:
                    revoked_by, token = params
                    r = store.get(token)
                    if r and r["revoked_at"] is None:
                        r["revoked_at"] = "now"
                        r["revoked_by"] = revoked_by
                        cur.rowcount = 1
                    return cur
                raise AssertionError(f"Unexpected SQL: {s}")

        class _CM:
            def __enter__(self_inner):
                return _Conn()

            def __exit__(self_inner, *a):
                return False

        return _CM()

    def test_create_then_list_and_resolve(self):
        link = client_hours_share.create_share_link(created_by="a@b.c", label="Board")
        self.assertTrue(link["active"])
        self.assertEqual(link["label"], "Board")
        token = link["token"]
        self.assertTrue(token)

        links = client_hours_share.list_share_links()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["token"], token)

        # Resolving with record_view bumps the counter; validate-only does not.
        self.assertIsNotNone(client_hours_share.resolve_share_token(token))
        self.assertEqual(self.rows[token]["view_count"], 1)
        self.assertIsNotNone(
            client_hours_share.resolve_share_token(token, record_view=False)
        )
        self.assertEqual(self.rows[token]["view_count"], 1)

    def test_revoke_disables_resolution(self):
        token = client_hours_share.create_share_link(created_by="a@b.c")["token"]
        self.assertTrue(client_hours_share.revoke_share_link(token=token, revoked_by="a@b.c"))
        # Revoked links no longer resolve and drop out of the active list.
        self.assertIsNone(client_hours_share.resolve_share_token(token))
        self.assertEqual(client_hours_share.list_share_links(), [])
        # Revoking again is a no-op (idempotent → False).
        self.assertFalse(client_hours_share.revoke_share_link(token=token))

    def test_label_trimmed_and_capped(self):
        link = client_hours_share.create_share_link(created_by="a@b.c", label="  " + "x" * 200)
        self.assertEqual(len(link["label"]), 120)


class SharedRendererTest(unittest.TestCase):
    """The read-only shared page reuses the admin chart JS but drops every
    mutation control; the admin page keeps them."""

    def _admin(self):
        from dashboard.renderers.client_hours_renderer import render_client_hours_page
        return render_client_hours_page(user_email="admin@sagefrog.com")

    def _shared(self, **kw):
        from dashboard.renderers.client_hours_renderer import render_shared_client_hours_page
        return render_shared_client_hours_page(token="TOK123", **kw)

    def test_admin_has_all_controls(self):
        html = self._admin()
        for eid in ('id="chShare"', 'id="chTag"', 'id="chRefresh"',
                    'id="shareModal"', 'id="tagModal"'):
            self.assertIn(eid, html)
        self.assertIn('"readOnly": false', html)
        self.assertIn("/admin/client-hours/data", html)
        self.assertNotIn('<header class="ro-topbar"', html)

    def test_shared_is_read_only(self):
        html = self._shared(label="Q3 leadership")
        self.assertIn('"readOnly": true', html)
        self.assertIn("/share/client-hours/TOK123/data", html)
        self.assertIn('<header class="ro-topbar"', html)
        self.assertIn("Read-only", html)
        self.assertIn("Q3 leadership", html)
        # Search engines shouldn't index a share link.
        self.assertIn("noindex", html)
        # No mutation-control elements leak into the public view. (The shared CSS
        # still defines classes like .goal-btn, but no such element is rendered.)
        for eid in ('id="chShare"', 'id="chTag"', 'id="chRefresh"',
                    'id="shareModal"', 'id="tagModal"'):
            self.assertNotIn(eid, html)
        # And its JS must never call an admin-only endpoint (the goal/tag/share
        # write handlers are gated out entirely, not just disabled).
        self.assertNotIn("/admin/client-hours", html)

    def test_shared_escapes_label(self):
        html = self._shared(label='<script>alert(1)</script>')
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
