from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# dashboard.utils.auth imports web_users (which pulls the DB driver). Only stub
# it when the real module can't be imported, so this test runs standalone
# without disturbing other tests that use the real web_users.
try:  # pragma: no cover - environment dependent
    import web_users  # noqa: F401
except Exception:  # pragma: no cover
    import types

    _stub = types.ModuleType("web_users")
    _stub.enabled = lambda: False  # type: ignore[attr-defined]
    sys.modules["web_users"] = _stub

import security  # noqa: E402
from dashboard.utils import auth as dashboard_auth  # noqa: E402

# The full set of secret-carrying env vars, so each test starts from a known,
# empty baseline regardless of the ambient environment.
_SECRET_ENV = {
    "AUTH_SESSION_SECRET": "",
    "CRON_SECRET": "",
    "API_KEY": "",
    "DASHBOARD_SECRET": "",
    "RAILWAY_ENVIRONMENT": "",
    "RAILWAY_PUBLIC_DOMAIN": "",
}


def _env(**overrides: str) -> dict[str, str]:
    env = dict(_SECRET_ENV)
    env.update(overrides)
    return env


class SessionSigningSecretTests(unittest.TestCase):
    def test_prefers_dedicated_secret(self) -> None:
        with patch.dict("os.environ", _env(AUTH_SESSION_SECRET="dedicated", CRON_SECRET="cron"),
                        clear=True):
            self.assertEqual(security.session_signing_secret(), "dedicated")

    def test_production_fails_closed_without_dedicated(self) -> None:
        # Only CRON_SECRET / API_KEY set, in production -> must NOT fall back.
        with patch.dict("os.environ",
                        _env(CRON_SECRET="cron", API_KEY="apikey", RAILWAY_ENVIRONMENT="production"),
                        clear=True):
            with self.assertRaises(RuntimeError):
                security.session_signing_secret()
            self.assertFalse(security.session_signing_secret_configured())

    def test_dev_falls_back_to_cron_then_api_key(self) -> None:
        with patch.dict("os.environ", _env(CRON_SECRET="cron"), clear=True):
            self.assertEqual(security.session_signing_secret(), "cron")
        with patch.dict("os.environ", _env(API_KEY="apikey"), clear=True):
            self.assertEqual(security.session_signing_secret(), "apikey")

    def test_dev_without_any_secret_raises(self) -> None:
        with patch.dict("os.environ", _env(), clear=True):
            with self.assertRaises(RuntimeError):
                security.session_signing_secret()
            self.assertFalse(security.session_signing_secret_configured())

    def test_configured_true_with_dedicated(self) -> None:
        with patch.dict("os.environ", _env(AUTH_SESSION_SECRET="x"), clear=True):
            self.assertTrue(security.session_signing_secret_configured())


class DashboardSecretTests(unittest.TestCase):
    def test_prefers_dashboard_secret(self) -> None:
        with patch.dict("os.environ", _env(DASHBOARD_SECRET="dash", CRON_SECRET="cron"), clear=True):
            self.assertEqual(dashboard_auth.configured_dashboard_secret(), "dash")

    def test_production_no_cron_fallback(self) -> None:
        with patch.dict("os.environ",
                        _env(CRON_SECRET="cron", RAILWAY_PUBLIC_DOMAIN="app.example.com"),
                        clear=True):
            self.assertIsNone(dashboard_auth.configured_dashboard_secret())

    def test_dev_cron_fallback(self) -> None:
        with patch.dict("os.environ", _env(CRON_SECRET="cron"), clear=True):
            self.assertEqual(dashboard_auth.configured_dashboard_secret(), "cron")

    def test_dev_none_when_unset(self) -> None:
        with patch.dict("os.environ", _env(), clear=True):
            self.assertIsNone(dashboard_auth.configured_dashboard_secret())


class DeOverloadInvariantTests(unittest.TestCase):
    """In production, CRON_SECRET alone must not unlock sessions or ?key=."""

    def test_cron_secret_alone_unlocks_nothing_in_production(self) -> None:
        with patch.dict("os.environ",
                        _env(CRON_SECRET="only-cron", RAILWAY_ENVIRONMENT="production"),
                        clear=True):
            self.assertFalse(security.session_signing_secret_configured())
            self.assertIsNone(dashboard_auth.configured_dashboard_secret())


if __name__ == "__main__":
    unittest.main()
