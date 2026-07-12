"""OAuth token encryption: legacy-key read fallback + accurate diagnostics.

Covers the app-wide connector failure mode where a changed
OAUTH_TOKEN_ENCRYPTION_KEY leaves every stored OAuth token undecryptable while a
service-account connector (GSC) keeps working. The store must:
  - decrypt tokens minted under a historical key (AUTH_SESSION_SECRET/CRON_SECRET)
    and re-encrypt them under the current key (self-healing migration), and
  - distinguish "token absent" from "token present but undecryptable" so callers
    can show an accurate message instead of a misleading "reconnect" prompt.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import oauth_store  # noqa: E402


def _encrypt_under(secret: str, plaintext: str) -> str:
    """Encrypt as an OLD deployment would have, using `secret` as the key seed."""
    from cryptography.fernet import Fernet

    return Fernet(oauth_store._fernet_key(secret)).encrypt(plaintext.encode()).decode("ascii")


class DecryptFallbackTests(unittest.TestCase):
    def test_primary_key_roundtrip(self):
        with mock.patch.dict(os.environ, {"OAUTH_TOKEN_ENCRYPTION_KEY": "primary-secret"}, clear=False):
            enc = oauth_store._encrypt("hello-token")
            result, used_legacy = oauth_store._decrypt_ex(enc)
            self.assertEqual(result, "hello-token")
            self.assertFalse(used_legacy)

    def test_legacy_key_decrypts_and_flags_migration(self):
        # Token minted under the historical AUTH_SESSION_SECRET-derived key, but
        # the deployment now encrypts under a fresh OAUTH_TOKEN_ENCRYPTION_KEY.
        old = _encrypt_under("historical-session-secret", "legacy-token")
        env = {
            "OAUTH_TOKEN_ENCRYPTION_KEY": "brand-new-key",
            "AUTH_SESSION_SECRET": "historical-session-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            result, used_legacy = oauth_store._decrypt_ex(old)
            self.assertEqual(result, "legacy-token")
            self.assertTrue(used_legacy)
            # Back-compat wrapper still returns the plaintext transparently.
            self.assertEqual(oauth_store._decrypt(old), "legacy-token")

    def test_undecryptable_is_distinct_from_absent(self):
        # Ciphertext exists but no configured secret can decrypt it.
        orphan = _encrypt_under("a-secret-no-longer-in-the-env", "lost-token")
        env = {"OAUTH_TOKEN_ENCRYPTION_KEY": "current-key"}
        # Ensure no legacy secrets happen to match.
        for name in ("AUTH_SESSION_SECRET", "CRON_SECRET", "API_KEY"):
            env[name] = "unrelated-" + name
        with mock.patch.dict(os.environ, env, clear=False):
            result, used_legacy = oauth_store._decrypt_ex(orphan)
            self.assertIs(result, oauth_store._UNDECRYPTABLE)
            self.assertFalse(used_legacy)
            # Back-compat wrapper collapses undecryptable to None (as before).
            self.assertIsNone(oauth_store._decrypt(orphan))

    def test_no_ciphertext_returns_none(self):
        with mock.patch.dict(os.environ, {"OAUTH_TOKEN_ENCRYPTION_KEY": "k"}, clear=False):
            self.assertEqual(oauth_store._decrypt_ex(None), (None, False))
            self.assertEqual(oauth_store._decrypt_ex(""), (None, False))


class TokenHealthAndErrorTests(unittest.TestCase):
    def _patch_row(self, row):
        return mock.patch.object(oauth_store, "_get_row", lambda platform, client_slug="": row)

    def test_token_health_ok(self):
        env = {"OAUTH_TOKEN_ENCRYPTION_KEY": "k"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(oauth_store, "enabled", lambda: True):
            enc = oauth_store._encrypt("tok")
            with self._patch_row({"refresh_token_enc": enc, "access_token_enc": None}):
                self.assertEqual(oauth_store.token_health("meta"), "ok")

    def test_token_health_undecryptable(self):
        orphan = _encrypt_under("gone-secret", "tok")
        env = {"OAUTH_TOKEN_ENCRYPTION_KEY": "current", "AUTH_SESSION_SECRET": "x",
               "CRON_SECRET": "y", "API_KEY": "z"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(oauth_store, "enabled", lambda: True):
            with self._patch_row({"refresh_token_enc": orphan, "access_token_enc": None}):
                self.assertEqual(oauth_store.token_health("meta"), "undecryptable")

    def test_token_health_missing(self):
        with mock.patch.dict(os.environ, {"OAUTH_TOKEN_ENCRYPTION_KEY": "k"}, clear=False), \
                mock.patch.object(oauth_store, "enabled", lambda: True):
            with self._patch_row(None):
                self.assertEqual(oauth_store.token_health("meta"), "missing")

    def test_token_error_message_switches_on_health(self):
        orphan = _encrypt_under("gone-secret", "tok")
        env = {"OAUTH_TOKEN_ENCRYPTION_KEY": "current", "AUTH_SESSION_SECRET": "x",
               "CRON_SECRET": "y", "API_KEY": "z"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(oauth_store, "enabled", lambda: True):
            with self._patch_row({"refresh_token_enc": orphan, "access_token_enc": None}):
                msg = oauth_store.token_error("meta", client_slug="acme", missing="No Meta token.")
                self.assertIn("could not be decrypted", msg)
                self.assertIn("OAUTH_TOKEN_ENCRYPTION_KEY", msg)
            with self._patch_row(None):
                msg = oauth_store.token_error("meta", client_slug="acme", missing="No Meta token.")
                self.assertEqual(msg, "No Meta token.")


class PublicStatusTests(unittest.TestCase):
    def test_undecryptable_row_reports_decrypt_error_not_connected(self):
        orphan = _encrypt_under("gone-secret", "tok")
        env = {"OAUTH_TOKEN_ENCRYPTION_KEY": "current", "AUTH_SESSION_SECRET": "x",
               "CRON_SECRET": "y", "API_KEY": "z"}
        with mock.patch.dict(os.environ, env, clear=False):
            row = {"refresh_token_enc": orphan, "access_token_enc": None,
                   "connected_by": None, "connected_at": None, "updated_at": None,
                   "scopes": None, "metadata_json": {}}
            status = oauth_store._public_status_from_row("meta", row)
            self.assertFalse(status.connected)
            self.assertTrue(status.decrypt_error)


if __name__ == "__main__":
    unittest.main()
