from __future__ import annotations

import base64
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import oauth_flows  # noqa: E402

# oauth_flows sat at 29% covered, and the untested part included the three
# things that decide whether a request is allowed to do something:
#
#   * validate_return_to  — where a user is sent after signing in
#   * sign/verify_connect_state — the token that lets somebody authorize a
#     connector for one client without holding a portal login at all
#   * store/pop_oauth_state — the CSRF state tying an OAuth callback to the
#     browser that started the flow
#
# Those are cheap to test properly (no network, no database) and expensive to
# get wrong, so they are what this file covers. The provider-specific code
# exchanges that make up the rest of the uncovered lines are mostly HTTP
# plumbing and are better served by their own tests.

TEST_SECRET = "test-signing-secret-value-32-chars!!"


def _with_secret():
    return mock.patch("security.session_signing_secret", return_value=TEST_SECRET)


class ValidateReturnToTest(unittest.TestCase):
    """`/login?next=…` carries an arbitrary value from an unauthenticated
    request through to the redirect issued after a successful sign-in, so this
    function is the only thing standing between a crafted link and a
    convincing phishing hop: the victim sees the real portal on the real
    domain, signs in, and is sent wherever the link says."""

    def test_ordinary_paths_are_left_alone(self):
        for path in ("/admin", "/dashboards", "/dashboard/acme", "/a/b?x=1#frag"):
            with self.subTest(path=path):
                self.assertEqual(oauth_flows.validate_return_to(path), path)

    def test_absolute_urls_are_refused(self):
        for path in ("https://evil.example", "http://evil.example", "//evil.example"):
            with self.subTest(path=path):
                self.assertEqual(oauth_flows.validate_return_to(path), "/admin")

    def test_a_backslash_cannot_smuggle_a_second_leading_slash(self):
        r"""Browsers resolve URLs by the WHATWG rules, where a backslash counts
        as a slash in this position: `/\evil.example` reaches evil.example just
        as `//evil.example` does, while looking like an ordinary path. This is
        the case the original check missed."""
        for path in (r"/\evil.example", "/\\/evil.example", "/ok\\bad"):
            with self.subTest(path=path):
                self.assertEqual(oauth_flows.validate_return_to(path), "/admin")

    def test_control_characters_are_refused(self):
        """Browsers strip these before resolving, so a value containing one can
        present a different string to the check than to the browser."""
        for path in ("/\x00//evil.example", "/\x7f/evil.example", "/\x1f//evil.example"):
            with self.subTest(path=repr(path)):
                self.assertEqual(oauth_flows.validate_return_to(path), "/admin")

    def test_leading_whitespace_does_not_hide_a_second_slash(self):
        self.assertEqual(oauth_flows.validate_return_to("  //evil.example"), "/admin")
        self.assertEqual(oauth_flows.validate_return_to("\t//evil.example"), "/admin")

    def test_empty_and_missing_values_fall_back(self):
        for path in ("", "   ", None):
            with self.subTest(path=repr(path)):
                self.assertEqual(oauth_flows.validate_return_to(path), "/admin")

    def test_nothing_it_returns_can_leave_the_origin(self):
        """The property that matters, stated directly: whatever comes back is
        either the fallback or a path that stays on this site."""
        hostile = [
            "//evil.example", r"/\evil.example", "\\\\evil.example", "https://evil.example",
            "/\x00//evil.example", "  //evil.example", "///evil.example", r"/\\evil.example",
            "javascript:alert(1)", "/legit", "/legit/deeper?a=b",
        ]
        for path in hostile:
            with self.subTest(path=repr(path)):
                result = oauth_flows.validate_return_to(path)
                self.assertTrue(result.startswith("/"), result)
                self.assertFalse(
                    len(result) > 1 and result[1] in "/\\",
                    f"{path!r} produced {result!r}, which resolves to another origin",
                )


class ConnectStateTokenTest(unittest.TestCase):
    """The connect link lets someone authorize a connector for one client
    without signing in, so its token is the whole access decision: it names the
    client and the platform, and nothing else checks them."""

    def test_a_freshly_signed_token_round_trips(self):
        with _with_secret():
            token = oauth_flows.sign_connect_state("acme", "hubspot")
            self.assertEqual(oauth_flows.verify_connect_state(token), ("acme", "hubspot"))

    def test_the_slug_is_normalised_so_case_cannot_fork_identity(self):
        with _with_secret():
            token = oauth_flows.sign_connect_state("  ACME  ", "hubspot")
            self.assertEqual(oauth_flows.verify_connect_state(token), ("acme", "hubspot"))

    def test_a_tampered_payload_is_refused(self):
        """Swapping the client slug for another one must not verify, or a link
        issued for one client would authorize a connector for a different one."""
        with _with_secret():
            token = oauth_flows.sign_connect_state("acme", "hubspot")
            raw, sig = token.rsplit(".", 1)
            payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            payload["c"] = "victim"
            forged_raw = base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            ).decode().rstrip("=")
            self.assertIsNone(oauth_flows.verify_connect_state(f"{forged_raw}.{sig}"))

    def test_a_tampered_signature_is_refused(self):
        with _with_secret():
            token = oauth_flows.sign_connect_state("acme", "hubspot")
            raw, sig = token.rsplit(".", 1)
            flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
            self.assertIsNone(oauth_flows.verify_connect_state(f"{raw}.{flipped}"))

    def test_a_token_signed_with_another_secret_is_refused(self):
        with mock.patch("security.session_signing_secret", return_value="a-different-secret-value-32ch!!"):
            token = oauth_flows.sign_connect_state("acme", "hubspot")
        with _with_secret():
            self.assertIsNone(oauth_flows.verify_connect_state(token))

    def test_an_expired_token_is_refused(self):
        with _with_secret():
            token = oauth_flows.sign_connect_state("acme", "hubspot", ttl_seconds=1)
            with mock.patch.object(oauth_flows._time, "time", return_value=time.time() + 3600):
                self.assertIsNone(oauth_flows.verify_connect_state(token))

    def test_a_token_still_inside_its_window_is_accepted(self):
        with _with_secret():
            token = oauth_flows.sign_connect_state("acme", "hubspot", ttl_seconds=600)
            with mock.patch.object(oauth_flows._time, "time", return_value=time.time() + 60):
                self.assertEqual(oauth_flows.verify_connect_state(token), ("acme", "hubspot"))

    def test_an_unknown_platform_is_refused(self):
        """A signed token naming a platform the app does not have must not
        verify, so a stale or hand-made token cannot reach an unexpected flow."""
        with _with_secret():
            payload = {"c": "acme", "p": "not_a_platform", "exp": int(time.time()) + 600}
            raw = base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            ).decode().rstrip("=")
            import hashlib
            import hmac
            sig = hmac.new(TEST_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
            self.assertIsNone(oauth_flows.verify_connect_state(f"{raw}.{sig}"))

    def test_malformed_tokens_are_refused_rather_than_raising(self):
        with _with_secret():
            for token in ("", "no-dot", "a.b", "....", "!!!.!!!", None):
                with self.subTest(token=repr(token)):
                    self.assertIsNone(oauth_flows.verify_connect_state(token))


class _FakeRequest:
    """Just the session dict — that is all the state helpers touch."""

    def __init__(self):
        self.session: dict = {}


class OAuthStateTest(unittest.TestCase):
    """The `state` value ties a provider's callback to the browser that started
    the flow. Popping it is what stops a callback replayed by someone else from
    attaching their account."""

    def test_state_round_trips_through_the_session(self):
        request = _FakeRequest()
        oauth_flows.store_oauth_state(
            request, platform="hubspot", state="abc123", return_to="/admin/advanced", client_slug="acme"
        )
        self.assertEqual(
            oauth_flows.pop_oauth_state(request, platform="hubspot"),
            ("abc123", "/admin/advanced", "acme"),
        )

    def test_popping_consumes_the_state(self):
        """Second use must come back empty, so a replayed callback cannot match."""
        request = _FakeRequest()
        oauth_flows.store_oauth_state(request, platform="hubspot", state="abc123", return_to="/admin")
        oauth_flows.pop_oauth_state(request, platform="hubspot")
        state, _return_to, _slug = oauth_flows.pop_oauth_state(request, platform="hubspot")
        self.assertIsNone(state)

    def test_state_is_scoped_per_platform(self):
        """Starting a HubSpot connect must not satisfy a Google callback."""
        request = _FakeRequest()
        oauth_flows.store_oauth_state(request, platform="hubspot", state="hs-state", return_to="/admin")
        state, _return_to, _slug = oauth_flows.pop_oauth_state(request, platform="google_ads")
        self.assertIsNone(state)

    def test_an_unknown_platform_is_rejected_rather_than_stored_under_a_junk_key(self):
        """These helpers do not normalise hyphens — the routes do that before
        calling in (`platform.replace("-", "_")`), because redirect URIs are
        often registered hyphenated. Down here an unrecognised platform raises
        instead of silently creating a session key nothing will ever pop, which
        would strand the flow with no state to match against."""
        request = _FakeRequest()
        with self.assertRaises(ValueError):
            oauth_flows.store_oauth_state(
                request, platform="microsoft-ads", state="s", return_to="/admin"
            )
        self.assertEqual(request.session, {})

    def test_the_platform_key_is_case_and_whitespace_insensitive(self):
        request = _FakeRequest()
        oauth_flows.store_oauth_state(request, platform="  HubSpot ", state="s", return_to="/admin")
        state, _return_to, _slug = oauth_flows.pop_oauth_state(request, platform="hubspot")
        self.assertEqual(state, "s")

    def test_an_empty_client_slug_means_an_agency_wide_token(self):
        request = _FakeRequest()
        oauth_flows.store_oauth_state(request, platform="hubspot", state="s", return_to="/admin")
        _state, _return_to, client_slug = oauth_flows.pop_oauth_state(request, platform="hubspot")
        self.assertEqual(client_slug, "")

    def test_a_blank_return_to_falls_back_rather_than_redirecting_nowhere(self):
        request = _FakeRequest()
        oauth_flows.store_oauth_state(request, platform="hubspot", state="s", return_to="")
        _state, return_to, _slug = oauth_flows.pop_oauth_state(request, platform="hubspot")
        self.assertEqual(return_to, "/admin")


if __name__ == "__main__":
    unittest.main()
