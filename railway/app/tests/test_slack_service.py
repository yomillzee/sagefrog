"""Slack feature-request notifications: config gating, message shape, best-effort.

No network here — the Slack POST is stubbed so we assert on the payload we'd send
and on the fail-safe behavior (never raises, honors ``enabled()``). Env vars are
set/cleared per-test so the module's config reads stay deterministic.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _ensure_stub(name: str, build) -> None:
    try:
        __import__(name)
    except Exception:
        sys.modules[name] = build()


def _db_stub() -> types.ModuleType:
    m = types.ModuleType("db")
    m.connection = None
    return m


def _web_users_stub() -> types.ModuleType:
    m = types.ModuleType("web_users")
    m.enabled = lambda: True
    return m


_ensure_stub("db", _db_stub)
_ensure_stub("web_users", _web_users_stub)

import feature_requests  # noqa: E402
import slack_service  # noqa: E402


def _req(**over) -> feature_requests.FeatureRequest:
    base = dict(
        id=7,
        client_slug="penn",
        page_path="/dashboard/penn",
        page_label="Penn Community Bank",
        body="Add a dark mode toggle.",
        scope="global",
        status="new",
        created_at="2026-07-26T00:00:00+00:00",
        created_by="who@agency.com",
        resolved_at=None,
        resolved_by=None,
    )
    base.update(over)
    return feature_requests.FeatureRequest(**base)


class EnabledTests(unittest.TestCase):
    def test_disabled_without_config(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(slack_service.enabled())

    def test_needs_both_token_and_channel(self) -> None:
        with mock.patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-1"}, clear=True):
            self.assertFalse(slack_service.enabled())
        with mock.patch.dict(
            "os.environ", {"SLACK_FEATURE_REQUEST_CHANNEL": "#fr"}, clear=True
        ):
            self.assertFalse(slack_service.enabled())

    def test_enabled_with_both(self) -> None:
        env = {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_FEATURE_REQUEST_CHANNEL": "#fr"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertTrue(slack_service.enabled())


class BlockBuildingTests(unittest.TestCase):
    def test_blocks_carry_body_and_context(self) -> None:
        with mock.patch.dict(
            "os.environ", {"PUBLIC_BASE_URL": "https://app.example.com/"}, clear=True
        ):
            fallback, blocks = slack_service._feature_request_blocks(_req())
        self.assertIn("Penn Community Bank", fallback)
        section = next(b for b in blocks if b["type"] == "section")
        self.assertEqual(section["text"]["text"], "Add a dark mode toggle.")
        context = next(b for b in blocks if b["type"] == "context")
        ctext = context["elements"][0]["text"]
        self.assertIn("Penn Community Bank", ctext)
        self.assertIn("/dashboard/penn", ctext)
        self.assertIn("who@agency.com", ctext)
        # Links into the admin inbox anchor when a public base URL is set.
        self.assertIn("https://app.example.com/admin#feature-requests", ctext)

    def test_empty_body_has_placeholder(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            _fallback, blocks = slack_service._feature_request_blocks(_req(body="   "))
        section = next(b for b in blocks if b["type"] == "section")
        self.assertEqual(section["text"]["text"], "_(no description)_")

    def test_long_body_is_truncated(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            _fallback, blocks = slack_service._feature_request_blocks(
                _req(body="x" * 5000)
            )
        section = next(b for b in blocks if b["type"] == "section")
        self.assertLessEqual(len(section["text"]["text"]), slack_service._MAX_BODY_CHARS)
        self.assertTrue(section["text"]["text"].endswith("…"))


class NotifyTests(unittest.TestCase):
    def test_no_op_when_channel_unset(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(slack_service.notify_feature_request(_req()))

    def test_posts_to_configured_channel(self) -> None:
        env = {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_FEATURE_REQUEST_CHANNEL": "C123"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
            slack_service, "_post_message", return_value=True
        ) as post:
            ok = slack_service.notify_feature_request(_req())
        self.assertTrue(ok)
        self.assertEqual(post.call_args.kwargs["channel"], "C123")
        self.assertTrue(post.call_args.kwargs["blocks"])

    def test_notify_never_raises(self) -> None:
        env = {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_FEATURE_REQUEST_CHANNEL": "C123"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(
            slack_service, "_post_message", side_effect=RuntimeError("boom")
        ):
            self.assertFalse(slack_service.notify_feature_request(_req()))


if __name__ == "__main__":
    unittest.main()
