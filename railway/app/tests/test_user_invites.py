"""Behavioral tests for invite ("magic") links.

Covers the security contract of ``user_invites`` — tokens are stored only as
hashes, links are single-use, expiring, revocable, and bound to a live account —
plus the ``web_users`` side (an invited account has no usable password until the
link is redeemed) and the two rendered surfaces.

Uses an in-memory fake connection that models just the statements the module
issues, so the whole thing runs without a Postgres.
"""

from __future__ import annotations

import contextlib
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
import login_rate_limit  # noqa: E402
import user_invites  # noqa: E402
import web_auth  # noqa: E402
import web_users  # noqa: E402


# ── in-memory stand-in for the invite tables ────────────────────────────────
class _Result:
    def __init__(self, rows):
        self._rows = list(rows)
        self.rowcount = len(self._rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Store:
    """Holds invite rows and user password hashes for the fake connection."""

    def __init__(self):
        self.invites: list[dict] = []
        self.users: dict[int, dict] = {}
        self._next_id = 1

    def add_user(self, user_id: int, *, active: bool = True, pw: str = "hash") -> None:
        self.users[user_id] = {"is_active": active, "password_hash": pw}

    def next_id(self) -> int:
        out = self._next_id
        self._next_id += 1
        return out

    def live(self, now: datetime) -> list[dict]:
        """Invites that resolve: unaccepted, unrevoked, unexpired, user active."""
        return [
            i
            for i in self.invites
            if i["accepted_at"] is None
            and i["revoked_at"] is None
            and i["expires_at"] > now
            and self.users.get(i["user_id"], {}).get("is_active")
        ]


class _FakeConn:
    def __init__(self, store: _Store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):  # noqa: C901 - a flat statement router
        s = " ".join(str(sql).split())
        now = datetime.now(tz=UTC)
        st = self.store

        if s.startswith("UPDATE user_invites SET revoked_at"):
            hit = [
                i
                for i in st.invites
                if i["user_id"] == int(params[0])
                and i["accepted_at"] is None
                and i["revoked_at"] is None
            ]
            for i in hit:
                i["revoked_at"] = now
            return _Result([(1,)] * len(hit))

        if s.startswith("INSERT INTO user_invites"):
            token_hash, user_id, email, created_by, expires_at = params
            st.invites.append(
                {
                    "id": st.next_id(),
                    "token_hash": token_hash,
                    "user_id": int(user_id),
                    "email": email,
                    "created_by": created_by,
                    "expires_at": expires_at,
                    "accepted_at": None,
                    "revoked_at": None,
                }
            )
            return _Result([])

        if s.startswith("SELECT i.id, i.user_id, i.email, i.expires_at"):
            match = [i for i in st.live(now) if i["token_hash"] == params[0]]
            return _Result(
                [(i["id"], i["user_id"], i["email"], i["expires_at"]) for i in match[:1]]
            )

        if s.startswith("UPDATE user_invites i SET accepted_at"):
            match = [i for i in st.live(now) if i["token_hash"] == params[0]]
            if not match:
                return _Result([])
            i = match[0]
            i["accepted_at"] = now
            return _Result([(i["id"], i["user_id"], i["email"], i["expires_at"])])

        if s.startswith("UPDATE web_users SET password_hash"):
            pw_hash, user_id = params
            row = st.users.get(int(user_id))
            if row and row["is_active"]:
                row["password_hash"] = pw_hash
                return _Result([(1,)])
            return _Result([])

        if s.startswith("SELECT user_id, MAX(expires_at)"):
            seen: dict[int, datetime] = {}
            for i in st.live(now):
                uid = i["user_id"]
                if uid not in seen or i["expires_at"] > seen[uid]:
                    seen[uid] = i["expires_at"]
            return _Result(sorted(seen.items()))

        raise AssertionError(f"unexpected SQL in test fake: {s}")


@contextlib.contextmanager
def _fake_db(store: _Store):
    """Point db.connection() and the enabled() gate at the in-memory store."""
    real_conn, real_enabled = db.connection, web_users.enabled

    @contextlib.contextmanager
    def fake_connection():
        yield _FakeConn(store)

    db.connection = fake_connection
    web_users.enabled = lambda: True
    try:
        yield
    finally:
        db.connection = real_conn
        web_users.enabled = real_enabled


# ── token handling ──────────────────────────────────────────────────────────
class TokenTests(unittest.TestCase):
    def test_hash_is_stable_and_distinct(self) -> None:
        self.assertEqual(user_invites.hash_token("abc"), user_invites.hash_token("abc"))
        self.assertNotEqual(user_invites.hash_token("abc"), user_invites.hash_token("abd"))
        self.assertEqual(len(user_invites.hash_token("abc")), 64)

    def test_raw_token_is_never_persisted(self) -> None:
        """The whole point: a DB dump must not yield a working link."""
        store = _Store()
        store.add_user(7)
        with _fake_db(store):
            token, _ = user_invites.mint_invite(user_id=7, email="a@b.com")
        self.assertTrue(token)
        row = store.invites[0]
        self.assertNotIn(token, str(row))
        self.assertEqual(row["token_hash"], user_invites.hash_token(token))

    def test_ttl_is_clamped(self) -> None:
        import os

        original = os.environ.get("AUTH_INVITE_TTL_HOURS")
        try:
            for raw, expected in (("0", 1), ("99999", 720), ("48", 48), ("garbage", 72)):
                os.environ["AUTH_INVITE_TTL_HOURS"] = raw
                self.assertEqual(user_invites.ttl_hours(), expected, raw)
            os.environ.pop("AUTH_INVITE_TTL_HOURS")
            self.assertEqual(user_invites.ttl_hours(), 72)
        finally:
            if original is None:
                os.environ.pop("AUTH_INVITE_TTL_HOURS", None)
            else:
                os.environ["AUTH_INVITE_TTL_HOURS"] = original

    def test_invite_path_and_url(self) -> None:
        self.assertEqual(user_invites.invite_path("tok"), "/invite/tok")
        self.assertEqual(
            user_invites.invite_url("tok", base_url="https://x.test/"), "https://x.test/invite/tok"
        )
        self.assertEqual(user_invites.invite_url("tok"), "/invite/tok")


# ── lifecycle ───────────────────────────────────────────────────────────────
class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _Store()
        self.store.add_user(1)

    def test_mint_then_resolve(self) -> None:
        with _fake_db(self.store):
            token, expires_at = user_invites.mint_invite(user_id=1, email="New@B.com")
            invite = user_invites.resolve_invite(token)
        self.assertIsNotNone(invite)
        self.assertEqual(invite.user_id, 1)
        # Email is normalized on the way in so the landing page shows it cleanly.
        self.assertEqual(invite.email, "new@b.com")
        self.assertGreater(expires_at, datetime.now(tz=UTC))

    def test_unknown_token_does_not_resolve(self) -> None:
        with _fake_db(self.store):
            user_invites.mint_invite(user_id=1, email="a@b.com")
            self.assertIsNone(user_invites.resolve_invite("not-a-real-token"))
            self.assertIsNone(user_invites.resolve_invite(""))

    def test_minting_again_revokes_the_previous_link(self) -> None:
        with _fake_db(self.store):
            first, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            second, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            self.assertIsNone(user_invites.resolve_invite(first))
            self.assertIsNotNone(user_invites.resolve_invite(second))

    def test_expired_link_does_not_resolve(self) -> None:
        with _fake_db(self.store):
            token, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            self.store.invites[0]["expires_at"] = datetime.now(tz=UTC) - timedelta(minutes=1)
            self.assertIsNone(user_invites.resolve_invite(token))

    def test_link_dies_with_the_account(self) -> None:
        with _fake_db(self.store):
            token, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            self.store.users[1]["is_active"] = False
            self.assertIsNone(user_invites.resolve_invite(token))

    def test_revoke_for_user(self) -> None:
        with _fake_db(self.store):
            token, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            self.assertEqual(user_invites.revoke_for_user(1), 1)
            self.assertIsNone(user_invites.resolve_invite(token))
            # Idempotent: nothing outstanding left to revoke.
            self.assertEqual(user_invites.revoke_for_user(1), 0)

    def test_accept_sets_password_and_burns_the_token(self) -> None:
        with _fake_db(self.store):
            token, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            accepted = user_invites.accept_invite(token, "a-good-password")
            self.assertIsNotNone(accepted)
            self.assertEqual(accepted.user_id, 1)
            # Password now verifies, and the sentinel is gone.
            stored = self.store.users[1]["password_hash"]
            self.assertTrue(web_users.verify_password("a-good-password", stored))
            self.assertFalse(web_users.is_invite_pending_hash(stored))
            # Single use: the same link cannot be replayed.
            self.assertIsNone(user_invites.resolve_invite(token))
            self.assertIsNone(user_invites.accept_invite(token, "another-password"))
        # And the replay did not overwrite the password it set.
        self.assertTrue(
            web_users.verify_password("a-good-password", self.store.users[1]["password_hash"])
        )

    def test_accept_rejects_a_short_password_without_burning_the_token(self) -> None:
        with _fake_db(self.store):
            token, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            with self.assertRaises(ValueError):
                user_invites.accept_invite(token, "short")
            # A typo must not cost the user their invite.
            self.assertIsNotNone(user_invites.resolve_invite(token))

    def test_accept_rejects_an_expired_token(self) -> None:
        with _fake_db(self.store):
            token, _ = user_invites.mint_invite(user_id=1, email="a@b.com")
            self.store.invites[0]["expires_at"] = datetime.now(tz=UTC) - timedelta(minutes=1)
            self.assertIsNone(user_invites.accept_invite(token, "a-good-password"))
        # The password was left untouched.
        self.assertEqual(self.store.users[1]["password_hash"], "hash")

    def test_pending_by_user(self) -> None:
        self.store.add_user(2)
        with _fake_db(self.store):
            user_invites.mint_invite(user_id=1, email="a@b.com")
            token2, _ = user_invites.mint_invite(user_id=2, email="c@d.com")
            user_invites.accept_invite(token2, "a-good-password")
            pending = user_invites.pending_by_user()
        self.assertIn(1, pending)
        self.assertNotIn(2, pending)


# ── invite-pending accounts cannot sign in ──────────────────────────────────
class InvitePendingPasswordTests(unittest.TestCase):
    def test_sentinel_is_not_a_verifiable_password(self) -> None:
        sentinel = web_users.INVITE_PENDING_HASH
        for attempt in ("", "password", sentinel, "a-good-password"):
            self.assertFalse(web_users.verify_password(attempt, sentinel), attempt)

    def test_authenticate_refuses_an_invite_pending_account(self) -> None:
        real = web_users.get_password_hash
        web_users.get_password_hash = lambda email: web_users.INVITE_PENDING_HASH
        try:
            self.assertIsNone(web_users.authenticate("a@b.com", "anything-at-all"))
            self.assertIsNone(web_users.authenticate("a@b.com", web_users.INVITE_PENDING_HASH))
        finally:
            web_users.get_password_hash = real

    def test_is_invite_pending_hash(self) -> None:
        self.assertTrue(web_users.is_invite_pending_hash(web_users.INVITE_PENDING_HASH))
        self.assertFalse(web_users.is_invite_pending_hash(None))
        self.assertFalse(web_users.is_invite_pending_hash(""))
        self.assertFalse(web_users.is_invite_pending_hash(web_users.hash_password("hunter2hunter2")))


# ── rate limiting ───────────────────────────────────────────────────────────
class RateLimitTests(unittest.TestCase):
    def test_failure_without_an_email_only_buckets_the_ip(self) -> None:
        """An invalid invite token names no account, so it must not be able to
        lock out an unrelated address via a shared blank-email bucket."""
        recorded: list[str] = []
        real_enabled, real_schema, real_conn = (
            login_rate_limit.enabled,
            login_rate_limit.ensure_schema,
            db.connection,
        )
        login_rate_limit.enabled = lambda: True
        login_rate_limit.ensure_schema = lambda: True

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                if params and isinstance(params[0], str) and ":" in params[0]:
                    recorded.append(params[0])
                return _Result([])

        @contextlib.contextmanager
        def fake_connection():
            yield _C()

        db.connection = fake_connection
        try:
            login_rate_limit.record_login_failure(ip="1.2.3.4")
            self.assertTrue(any(k.startswith("ip:") for k in recorded))
            self.assertFalse(any(k.startswith("email:") for k in recorded))

            recorded.clear()
            login_rate_limit.record_login_failure(ip="1.2.3.4", email="a@b.com")
            self.assertTrue(any(k.startswith("email:") for k in recorded))
        finally:
            login_rate_limit.enabled = real_enabled
            login_rate_limit.ensure_schema = real_schema
            db.connection = real_conn


# ── rendered surfaces ───────────────────────────────────────────────────────
class RenderTests(unittest.TestCase):
    def test_invite_page_posts_back_to_its_own_token(self) -> None:
        html = web_auth.render_invite_page(token="TOK", email="new@c.com")
        self.assertIn('action="/invite/TOK"', html)
        self.assertIn("new@c.com", html)
        self.assertIn('name="password"', html)
        self.assertIn('name="confirm"', html)
        # Keep the token out of Referer headers and out of search results.
        self.assertIn('name="referrer" content="no-referrer"', html)
        self.assertIn("noindex", html)

    def test_invite_page_escapes_its_inputs(self) -> None:
        html = web_auth.render_invite_page(token='"><script>x</script>', email="<b>@c.com")
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("<b>@c.com", html)

    def test_expired_state_offers_no_form_and_no_reason(self) -> None:
        html = web_auth.render_invite_page(token="TOK", email="", expired=True)
        self.assertNotIn("<form", html)
        self.assertIn("expired", html.lower())
        self.assertIn('href="/login"', html)

    def _admin(self) -> web_users.WebUser:
        return web_users.WebUser(
            id=1, email="admin@sagefrog.com", role="admin", client_slug=None, is_active=True
        )

    def _rows(self) -> list[dict]:
        return [
            {
                "id": 2,
                "email": "new@c.com",
                "role": "client",
                "client_slug": "penn",
                "is_active": True,
                "invite_pending": True,
            }
        ]

    def test_users_page_shows_the_link_panel_when_given_one(self) -> None:
        html = web_auth.render_admin_page(
            user=self._admin(),
            users=self._rows(),
            page="users",
            invite_link="https://x.test/invite/TOK123",
            invite_email="new@c.com",
            invite_expires_in="3 days",
        )
        self.assertIn('<div class="invite-panel">', html)
        self.assertIn("https://x.test/invite/TOK123", html)
        self.assertIn("3 days", html)

    def test_users_page_omits_the_panel_by_default(self) -> None:
        html = web_auth.render_admin_page(user=self._admin(), users=self._rows(), page="users")
        self.assertNotIn('<div class="invite-panel">', html)
        self.assertNotIn('id="inviteLink"', html)

    def test_pending_users_are_badged_and_re_invitable(self) -> None:
        html = web_auth.render_admin_page(user=self._admin(), users=self._rows(), page="users")
        self.assertIn("Invite pending", html)
        self.assertIn('action="/admin/users/2/invite"', html)

    def test_active_user_is_not_badged_as_pending(self) -> None:
        rows = self._rows()
        rows[0]["invite_pending"] = False
        html = web_auth.render_admin_page(user=self._admin(), users=rows, page="users")
        self.assertNotIn("Invite pending", html)
        self.assertIn(">Active<", html)

    def test_create_form_defaults_to_the_invite_flow(self) -> None:
        html = web_auth.render_admin_page(user=self._admin(), users=[], page="users")
        self.assertIn('name="setup_mode"', html)
        # 'invite' must be the first option so it is the default with JS off.
        setup = html.split('name="setup_mode"', 1)[1]
        self.assertLess(setup.index('value="invite"'), setup.index('value="password"'))
        # The password input must not be `required`, or invite mode would be
        # unsubmittable for anyone without JS.
        pw_input = setup.split('id="password"', 1)[1].split(">", 1)[0]
        self.assertNotIn("required", pw_input)


if __name__ == "__main__":
    unittest.main()
