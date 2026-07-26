"""Slack notifications: config gating, channel routing, send safety, builders.

No network: httpx is faked and the off-thread dispatch is run inline so the
send path is exercised synchronously. Covers the two guarantees that matter —
silent when unconfigured, and never raising on a Slack failure — plus the
per-event message shape.
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

import slack_notify  # noqa: E402


class _FakeResponse:
    def __init__(self, *, payload=None, status_code=200, text="") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """Stand-in for ``httpx.Client`` that records the POST and returns a canned reply."""

    calls: list[dict] = []

    def __init__(self, *, response=None, raise_exc=None) -> None:
        self._response = response or _FakeResponse(payload={"ok": True})
        self._raise = raise_exc

    def __call__(self, *args, **kwargs):  # httpx.Client(timeout=...)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, *, headers=None, json=None):
        _FakeClient.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        if self._raise is not None:
            raise self._raise
        return self._response


def _fake_httpx(*, response=None, raise_exc=None) -> types.SimpleNamespace:
    client = _FakeClient(response=response, raise_exc=raise_exc)
    return types.SimpleNamespace(Client=client)


_ENABLED_ENV = {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_DEFAULT_CHANNEL": "C_DEFAULT"}


class EnabledTests(unittest.TestCase):
    def test_disabled_without_config(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(slack_notify.enabled())

    def test_disabled_with_token_but_no_channel(self) -> None:
        with mock.patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-x"}, clear=True):
            self.assertFalse(slack_notify.enabled())

    def test_enabled_with_both(self) -> None:
        with mock.patch.dict("os.environ", _ENABLED_ENV, clear=True):
            self.assertTrue(slack_notify.enabled())


class ChannelRoutingTests(unittest.TestCase):
    def test_default_when_no_override(self) -> None:
        with mock.patch.dict("os.environ", _ENABLED_ENV, clear=True):
            self.assertEqual(slack_notify._channel_for("default"), "C_DEFAULT")
            self.assertEqual(slack_notify._channel_for("alerts"), "C_DEFAULT")

    def test_category_override_wins(self) -> None:
        env = {**_ENABLED_ENV, "SLACK_CHANNEL_ALERTS": "C_ALERTS"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(slack_notify._channel_for("alerts"), "C_ALERTS")
            # Other categories still fall back to the default channel.
            self.assertEqual(slack_notify._channel_for("default"), "C_DEFAULT")


class PostMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeClient.calls = []

    def test_noop_when_disabled(self) -> None:
        fake = _fake_httpx()
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.dict(
            "sys.modules", {"httpx": fake}
        ):
            slack_notify.post_message(text="hi", blocks=[{"x": 1}])
        self.assertEqual(_FakeClient.calls, [])

    def test_success_posts_expected_payload(self) -> None:
        fake = _fake_httpx(response=_FakeResponse(payload={"ok": True}))
        with mock.patch.dict("os.environ", _ENABLED_ENV, clear=True), mock.patch.dict(
            "sys.modules", {"httpx": fake}
        ):
            slack_notify.post_message(category="default", text="hello", blocks=[{"type": "x"}])
        self.assertEqual(len(_FakeClient.calls), 1)
        call = _FakeClient.calls[0]
        self.assertEqual(call["url"], slack_notify._POST_MESSAGE_URL)
        self.assertEqual(call["headers"]["Authorization"], "Bearer xoxb-test")
        self.assertEqual(call["json"]["channel"], "C_DEFAULT")
        self.assertEqual(call["json"]["text"], "hello")
        self.assertEqual(call["json"]["blocks"], [{"type": "x"}])

    def test_http_error_is_swallowed(self) -> None:
        fake = _fake_httpx(raise_exc=RuntimeError("network down"))
        with mock.patch.dict("os.environ", _ENABLED_ENV, clear=True), mock.patch.dict(
            "sys.modules", {"httpx": fake}
        ):
            # Must not raise.
            slack_notify.post_message(text="hello")

    def test_ok_false_is_swallowed(self) -> None:
        fake = _fake_httpx(response=_FakeResponse(payload={"ok": False, "error": "channel_not_found"}))
        with mock.patch.dict("os.environ", _ENABLED_ENV, clear=True), mock.patch.dict(
            "sys.modules", {"httpx": fake}
        ):
            slack_notify.post_message(text="hello")  # no raise


class BuilderTests(unittest.TestCase):
    """The notify_* helpers build the right message and dispatch it once."""

    def setUp(self) -> None:
        self.sent: list[dict] = []

    def _capture(self, **kwargs) -> None:
        self.sent.append(kwargs)

    def _run(self, fn, arg) -> None:
        # Run the daemon-thread dispatch inline and capture the post payload.
        with mock.patch.dict("os.environ", _ENABLED_ENV, clear=True), mock.patch.object(
            slack_notify, "_dispatch", lambda f: f()
        ), mock.patch.object(slack_notify, "post_message", self._capture):
            fn(arg)

    def test_feature_request_message(self) -> None:
        req = types.SimpleNamespace(
            client_slug="penn",
            page_label="Penn Community Bank",
            page_path="/dashboard/penn",
            created_by="who@agency.com",
            body="Add a dark mode toggle.",
        )
        self._run(slack_notify.notify_feature_request, req)
        self.assertEqual(len(self.sent), 1)
        msg = self.sent[0]
        self.assertEqual(msg["category"], "default")
        self.assertIn("feature request", msg["text"].lower())
        self.assertIn("penn", msg["text"])
        body_texts = [b.get("text", {}).get("text", "") for b in msg["blocks"]]
        self.assertTrue(any("dark mode toggle" in t for t in body_texts))

    def test_client_note_message(self) -> None:
        pad = types.SimpleNamespace(
            client_slug="nixon",
            title="Kickoff talking points",
            created_by="who@agency.com",
            body="Lead with the LinkedIn wins.",
        )
        self._run(slack_notify.notify_client_note, pad)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("client note", self.sent[0]["text"].lower())

    def test_dev_note_message(self) -> None:
        note = types.SimpleNamespace(
            title="Shipped pacing v2",
            category="feature",
            created_by="dev@agency.com",
            body="Rolled out the new budget pacing card.",
        )
        self._run(slack_notify.notify_dev_note, note)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("dev note", self.sent[0]["text"].lower())

    def test_builders_are_noop_when_disabled(self) -> None:
        req = types.SimpleNamespace(client_slug="penn", body="x", created_by=None,
                                    page_label=None, page_path=None)
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
            slack_notify, "_dispatch", lambda f: f()
        ), mock.patch.object(slack_notify, "post_message", self._capture):
            slack_notify.notify_feature_request(req)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
