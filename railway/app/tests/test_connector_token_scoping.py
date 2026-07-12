"""Client-scoped OAuth tokens must reach the sync path.

Meta and LinkedIn connectors resolve a client-scoped token and pass it down,
but the service layer used to force a GLOBAL token lookup
(load_meta_env / load_linkedin_env), which raises when no global token exists —
so a client whose token lives under its own slug could never sync. These tests
lock in that an explicitly supplied token bypasses the global resolver.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import linkedin_auth  # noqa: E402
import meta_auth  # noqa: E402
import oauth_store  # noqa: E402

_META_ENV = {"META_APP_ID": "app", "META_APP_SECRET": "secret", "META_BUSINESS_ID": "biz"}
_LI_ENV = {"LINKEDIN_CLIENT_ID": "cid", "LINKEDIN_CLIENT_SECRET": "csecret"}


class MetaEnvTokenTests(unittest.TestCase):
    def test_explicit_token_bypasses_global_resolver(self):
        with mock.patch.dict(os.environ, _META_ENV, clear=False), \
                mock.patch.object(meta_auth, "_resolve_access_token") as resolve:
            env = meta_auth.load_meta_env(access_token="client-scoped-tok")
            self.assertEqual(env.access_token, "client-scoped-tok")
            resolve.assert_not_called()

    def test_no_token_uses_global_resolver(self):
        with mock.patch.dict(os.environ, _META_ENV, clear=False), \
                mock.patch.object(meta_auth, "_resolve_access_token", return_value="global-tok") as resolve:
            env = meta_auth.load_meta_env()
            self.assertEqual(env.access_token, "global-tok")
            resolve.assert_called_once()

    def test_no_global_token_still_raises(self):
        with mock.patch.dict(os.environ, _META_ENV, clear=False), \
                mock.patch.object(oauth_store, "get_access_token", return_value=None):
            with self.assertRaises(RuntimeError):
                meta_auth.load_meta_env()


class LinkedInEnvTokenTests(unittest.TestCase):
    def test_require_token_false_does_not_raise_when_global_absent(self):
        with mock.patch.dict(os.environ, _LI_ENV, clear=False), \
                mock.patch.object(oauth_store, "get_refresh_token", return_value=None):
            env = linkedin_auth.load_linkedin_env(require_token=False)
            self.assertEqual(env.refresh_token, "")

    def test_require_token_true_raises_when_global_absent(self):
        with mock.patch.dict(os.environ, _LI_ENV, clear=False), \
                mock.patch.object(oauth_store, "get_refresh_token", return_value=None):
            with self.assertRaises(RuntimeError):
                linkedin_auth.load_linkedin_env(require_token=True)

    def test_global_token_used_when_present(self):
        with mock.patch.dict(os.environ, _LI_ENV, clear=False), \
                mock.patch.object(oauth_store, "get_refresh_token", return_value="global-refresh"):
            env = linkedin_auth.load_linkedin_env()
            self.assertEqual(env.refresh_token, "global-refresh")


if __name__ == "__main__":
    unittest.main()
