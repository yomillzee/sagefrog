"""Slack feature-request notifications: config gating, message shape, best-effort.

Covers both notices — the "someone wants X" ask and the "it's done" close-out that
replies in the ask's thread.

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

SLACK_ENV = {"SLACK_BOT_TOKEN": "xoxb-1", "SLACK_FEATURE_REQUEST_CHANNEL": "C123"}


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


def _posted(channel: str = "C123", ts: str = "1721952000.000100"):
    return slack_service.PostedMessage(channel=channel, ts=ts)


def _blocks_text(blocks) -> str:
    """Every mrkdwn/plain_text string in a block list, joined — for loose asserts."""
    out = []
    for block in blocks:
        text = block.get("text")
        if isinstance(text, dict):
            out.append(text.get("text", ""))
        for element in block.get("elements", []):
            out.append(element.get("text", ""))
    return "\n".join(out)


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
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True):
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
            self.assertIsNone(slack_service.notify_feature_request(_req()))

    def test_posts_to_configured_channel_and_returns_the_message(self) -> None:
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.object(
            slack_service, "_post_message", return_value=_posted()
        ) as post:
            posted = slack_service.notify_feature_request(_req())
        self.assertEqual(posted, _posted())
        self.assertEqual(post.call_args.kwargs["channel"], "C123")
        self.assertTrue(post.call_args.kwargs["blocks"])
        # The ask itself starts a thread; it is never a reply.
        self.assertIsNone(post.call_args.kwargs["thread_ts"])

    def test_notify_never_raises(self) -> None:
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.object(
            slack_service, "_post_message", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(slack_service.notify_feature_request(_req()))


class DoneBlockBuildingTests(unittest.TestCase):
    def _done_req(self, **over):
        base = dict(status="done", resolved_by="dev@agency.com")
        base.update(over)
        return _req(**base)

    def test_threaded_reply_is_just_the_close_out(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            fallback, blocks = slack_service._feature_request_done_blocks(
                self._done_req(), threaded=True
            )
        self.assertIn("done", fallback.lower())
        text = _blocks_text(blocks)
        self.assertIn("Feature request done", text)
        self.assertIn("dev@agency.com", text)
        # The ask is the parent message, so it isn't repeated in the reply — and
        # neither is the client/page trail that came with it.
        self.assertNotIn("Add a dark mode toggle.", text)
        self.assertNotIn("/dashboard/penn", text)

    def test_standalone_quotes_the_ask_and_names_the_resolver(self) -> None:
        with mock.patch.dict(
            "os.environ", {"PUBLIC_BASE_URL": "https://app.example.com"}, clear=True
        ):
            fallback, blocks = slack_service._feature_request_done_blocks(
                self._done_req()
            )
        self.assertIn("Penn Community Bank", fallback)
        text = _blocks_text(blocks)
        # No parent to point at, so the message has to carry the ask itself.
        self.assertIn("> Add a dark mode toggle.", text)
        self.assertIn("Penn Community Bank", text)
        self.assertIn("dev@agency.com", text)
        self.assertIn("https://app.example.com/admin#feature-requests", text)

    def test_standalone_quotes_every_line(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            _fallback, blocks = slack_service._feature_request_done_blocks(
                self._done_req(body="First line.\nSecond line.")
            )
        self.assertIn("> First line.\n> Second line.", _blocks_text(blocks))

    def test_standalone_survives_an_empty_body(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            _fallback, blocks = slack_service._feature_request_done_blocks(
                self._done_req(body="   ")
            )
        self.assertIn("> _(no description)_", _blocks_text(blocks))

    def test_standalone_body_is_truncated(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            _fallback, blocks = slack_service._feature_request_done_blocks(
                self._done_req(body="x" * 5000)
            )
        quote = next(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and b["text"]["text"].startswith(">")
        )
        self.assertLessEqual(len(quote), slack_service._MAX_BODY_CHARS + 2)
        self.assertTrue(quote.endswith("…"))


class NotifyDoneTests(unittest.TestCase):
    def _done_req(self, **over):
        base = dict(status="done", resolved_by="dev@agency.com")
        base.update(over)
        return _req(**base)

    def test_no_op_when_channel_unset(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(slack_service.notify_feature_request_done(_req()))

    def test_replies_in_the_asks_thread(self) -> None:
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.object(
            slack_service, "_post_message", return_value=_posted(ts="1721952100.5")
        ) as post:
            posted = slack_service.notify_feature_request_done(
                self._done_req(), channel="C999", thread_ts="1721952000.000100"
            )
        self.assertTrue(posted)
        self.assertEqual(post.call_args.kwargs["thread_ts"], "1721952000.000100")
        # The ts is only valid in the channel it came from, not wherever the env
        # var points now.
        self.assertEqual(post.call_args.kwargs["channel"], "C999")

    def test_without_a_thread_falls_back_to_the_configured_channel(self) -> None:
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.object(
            slack_service, "_post_message", return_value=_posted()
        ) as post:
            slack_service.notify_feature_request_done(self._done_req())
        self.assertEqual(post.call_args.kwargs["channel"], "C123")
        self.assertIsNone(post.call_args.kwargs["thread_ts"])
        # Standalone, so it has to quote the ask.
        self.assertIn("> Add a dark mode toggle.", _blocks_text(post.call_args.kwargs["blocks"]))

    def test_a_stale_channel_alone_is_ignored(self) -> None:
        # A recorded channel with no ts can't be threaded, so the current config
        # wins rather than posting into a channel for no reason.
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.object(
            slack_service, "_post_message", return_value=_posted()
        ) as post:
            slack_service.notify_feature_request_done(self._done_req(), channel="C999")
        self.assertEqual(post.call_args.kwargs["channel"], "C123")

    def test_notify_done_never_raises(self) -> None:
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.object(
            slack_service, "_post_message", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(slack_service.notify_feature_request_done(_req()))


class PostMessageTests(unittest.TestCase):
    """``_post_message`` maps Slack's response onto ``PostedMessage``."""

    def _post(self, *, json_body, status=200, **kwargs):
        resp = mock.Mock(status_code=status, text="")
        resp.json.return_value = json_body
        client = mock.MagicMock()
        client.__enter__.return_value.post.return_value = resp
        httpx = types.ModuleType("httpx")
        httpx.Client = mock.Mock(return_value=client)
        httpx.HTTPError = Exception
        with mock.patch.dict("os.environ", SLACK_ENV, clear=True), mock.patch.dict(
            sys.modules, {"httpx": httpx}
        ):
            result = slack_service._post_message(
                channel="C123", text="hi", blocks=[], **kwargs
            )
        payload = client.__enter__.return_value.post.call_args.kwargs["json"]
        return result, payload

    def test_returns_slacks_canonical_channel_and_ts(self) -> None:
        result, payload = self._post(
            json_body={"ok": True, "ts": "1721952000.000100", "channel": "C0CANON"}
        )
        self.assertEqual(result, _posted(channel="C0CANON", ts="1721952000.000100"))
        # No thread_ts key at all unless we're replying.
        self.assertNotIn("thread_ts", payload)

    def test_thread_ts_is_forwarded(self) -> None:
        _result, payload = self._post(
            json_body={"ok": True, "ts": "2.0", "channel": "C123"},
            thread_ts="1721952000.000100",
        )
        self.assertEqual(payload["thread_ts"], "1721952000.000100")

    def test_ok_without_a_ts_is_treated_as_unposted(self) -> None:
        result, _payload = self._post(json_body={"ok": True})
        self.assertIsNone(result)

    def test_slack_error_returns_none(self) -> None:
        result, _payload = self._post(json_body={"ok": False, "error": "not_in_channel"})
        self.assertIsNone(result)


class _FakeDB:
    """Stands in for ``db.connection()``, recording every statement executed."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.statements: list[tuple[str, tuple]] = []

    def connection(self):
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = self
        return ctx

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        cursor = mock.Mock()
        cursor.fetchone.return_value = self.rows.pop(0) if self.rows else None
        return cursor

    def sql_containing(self, needle: str):
        return [s for s in self.statements if needle in s[0]]


def _row(**over):
    base = dict(
        id=9,
        client_slug="penn",
        page_path="/dashboard/penn",
        page_label="Penn Community Bank",
        body="Add a dark mode toggle.",
        scope="global",
        status="new",
        created_at=None,
        created_by="who@agency.com",
        resolved_at=None,
        resolved_by=None,
        slack_channel=None,
        slack_thread_ts=None,
    )
    base.update(over)
    return tuple(base.values())


class CreateRequestRecordsThreadTests(unittest.TestCase):
    """A new ask stores the Slack message it created, so it can be replied to."""

    def _create(self, posted):
        fake = _FakeDB([_row()])
        with mock.patch.object(feature_requests, "enabled", return_value=True), \
                mock.patch.object(feature_requests, "ensure_schema"), \
                mock.patch.object(feature_requests.db, "connection", fake.connection), \
                mock.patch.object(
                    feature_requests.slack_service, "enabled", return_value=True
                ), \
                mock.patch.object(
                    feature_requests.slack_service,
                    "notify_feature_request",
                    return_value=posted,
                ):
            request = feature_requests.create_request(body="Add a dark mode toggle.")
        return request, fake

    def test_thread_is_saved_on_the_row(self) -> None:
        request, fake = self._create(_posted(channel="C0CANON", ts="1721952000.000100"))
        updates = fake.sql_containing("slack_thread_ts = %s")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1], ("C0CANON", "1721952000.000100", 9))
        # The returned request carries it too, without a re-read.
        self.assertEqual(request.slack_channel, "C0CANON")
        self.assertEqual(request.slack_thread_ts, "1721952000.000100")

    def test_nothing_saved_when_slack_did_not_post(self) -> None:
        request, fake = self._create(None)
        self.assertEqual(fake.sql_containing("slack_thread_ts = %s"), [])
        self.assertIsNone(request.slack_thread_ts)

    def test_thread_is_not_exposed_in_the_api_payload(self) -> None:
        request, _fake = self._create(_posted())
        self.assertNotIn("slack_thread_ts", request.to_dict())
        self.assertNotIn("slack_channel", request.to_dict())


class MarkDonePingsSlackTests(unittest.TestCase):
    """``mark_done`` replies in the thread of the row it actually updated."""

    def _mark_done(self, row):
        fake = _FakeDB([row] if row else [])
        with mock.patch.object(feature_requests, "enabled", return_value=True), \
                mock.patch.object(feature_requests, "ensure_schema"), \
                mock.patch.object(feature_requests.db, "connection", fake.connection), \
                mock.patch.object(
                    feature_requests.slack_service, "enabled", return_value=True
                ), \
                mock.patch.object(
                    feature_requests.slack_service, "notify_feature_request_done"
                ) as notify:
            result = feature_requests.mark_done(9, resolved_by="dev@agency.com")
        return result, notify

    def test_marking_done_replies_in_the_thread(self) -> None:
        result, notify = self._mark_done(
            _row(
                status="done",
                resolved_by="dev@agency.com",
                slack_channel="C0CANON",
                slack_thread_ts="1721952000.000100",
            )
        )
        self.assertTrue(result)
        notify.assert_called_once()
        req = notify.call_args.args[0]
        self.assertEqual(req.id, 9)
        self.assertEqual(req.status, "done")
        self.assertEqual(req.resolved_by, "dev@agency.com")
        self.assertEqual(notify.call_args.kwargs["channel"], "C0CANON")
        self.assertEqual(notify.call_args.kwargs["thread_ts"], "1721952000.000100")

    def test_a_request_with_no_recorded_thread_still_pings(self) -> None:
        # Raised before we started recording threads: no parent, but the team
        # still hears about it.
        result, notify = self._mark_done(_row(status="done", resolved_by="dev@agency.com"))
        self.assertTrue(result)
        self.assertIsNone(notify.call_args.kwargs["thread_ts"])

    def test_already_done_pings_nothing(self) -> None:
        # The ``status <> 'done'`` guard updates no row on a second click.
        result, notify = self._mark_done(None)
        self.assertFalse(result)
        notify.assert_not_called()

    def test_slack_failure_does_not_fail_the_update(self) -> None:
        fake = _FakeDB([_row(status="done", resolved_by="dev@agency.com")])
        with mock.patch.object(feature_requests, "enabled", return_value=True), \
                mock.patch.object(feature_requests, "ensure_schema"), \
                mock.patch.object(feature_requests.db, "connection", fake.connection), \
                mock.patch.object(
                    feature_requests.slack_service, "enabled", return_value=True
                ), \
                mock.patch.object(
                    feature_requests.slack_service,
                    "notify_feature_request_done",
                    side_effect=RuntimeError("boom"),
                ):
            self.assertTrue(feature_requests.mark_done(9, resolved_by="dev@agency.com"))


if __name__ == "__main__":
    unittest.main()
