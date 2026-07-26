"""Slack notifications — one seam for every product event we announce to Slack.

This is the *only* module that talks to Slack. It sends via the Slack Web API
(``chat.postMessage``) using a bot token, and it is built to scale: each event
gets a small ``notify_*`` builder, and every event carries a *category* that
resolves to a channel. Today every category falls back to one default channel,
but pointing a future category (e.g. off-track alerts) at its own channel is a
single env var — no code change.

Design guarantees, mirroring ``audit_log.record``:

* **Never breaks the request.** Every send is wrapped in ``try/except`` and, for
  the ``notify_*`` helpers, handed to a daemon thread so the HTTP round-trip is
  off the caller's thread. A Slack outage, a bad token, or an ``ok:false`` reply
  is logged and swallowed — the feature request / note write always succeeds.
* **Silent when unconfigured.** With ``SLACK_BOT_TOKEN`` /
  ``SLACK_DEFAULT_CHANNEL`` unset, ``enabled()`` is ``False`` and everything is a
  no-op, so local dev and tests stay quiet by default.

Config (see ``.env.example``):
    SLACK_BOT_TOKEN        Bot token (``xoxb-…``), scope ``chat:write``.
    SLACK_DEFAULT_CHANNEL  Channel id (e.g. ``C0123ABC``) or ``#channel-name``.
    SLACK_CHANNEL_<CAT>    Optional per-category override, upper-cased category
                           (e.g. ``SLACK_CHANNEL_ALERTS`` for category "alerts").
"""

from __future__ import annotations

import logging
import os
import threading

_log = logging.getLogger(__name__)

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_TIMEOUT_SECONDS = 5.0

# Slack limits: header plain_text ~150 chars, section mrkdwn ~3000 chars.
_MAX_HEADER = 150
_MAX_SECTION = 2900


def _token() -> str | None:
    return (os.getenv("SLACK_BOT_TOKEN") or "").strip() or None


def _default_channel() -> str | None:
    return (os.getenv("SLACK_DEFAULT_CHANNEL") or "").strip() or None


def enabled() -> bool:
    """True only when a bot token *and* a default channel are configured."""
    return bool(_token() and _default_channel())


def _channel_for(category: str) -> str | None:
    """Resolve an event *category* to a channel.

    Checks ``SLACK_CHANNEL_<CATEGORY>`` first (the "split later" hook), then
    falls back to ``SLACK_DEFAULT_CHANNEL``.
    """
    cat = (category or "default").strip()
    if cat:
        override = (os.getenv(f"SLACK_CHANNEL_{cat.upper()}") or "").strip()
        if override:
            return override
    return _default_channel()


def post_message(*, category: str = "default", text: str, blocks: list | None = None) -> None:
    """Send one message to the channel for ``category``. Synchronous; never raises.

    ``text`` is the notification/fallback string (also what shows in Slack push
    notifications); ``blocks`` is optional Block Kit for the in-channel render.
    No-op when :func:`enabled` is False.
    """
    token = _token()
    if not token:
        return
    channel = _channel_for(category)
    if not channel:
        return
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        import httpx  # lazy: importing this module must not require httpx

        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(
                _POST_MESSAGE_URL,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass
        if not data.get("ok"):
            _log.warning(
                "Slack post failed (category=%s, status=%s): %s",
                category,
                resp.status_code,
                data.get("error") or resp.text[:200],
            )
    except Exception:
        # Fire-and-forget: a Slack failure must never surface to the caller.
        _log.warning("Slack post errored (category=%s)", category, exc_info=True)


def _dispatch(fn) -> None:
    """Run ``fn`` on a daemon thread so the HTTP call never blocks the request."""
    try:
        threading.Thread(target=fn, daemon=True).start()
    except Exception:
        _log.warning("Slack dispatch failed to start", exc_info=True)


def _truncate(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _context_line(pairs: list[tuple[str, str | None]]) -> dict:
    parts = [f"*{label}:* {value}" for label, value in pairs if value]
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": " · ".join(parts) or "—"}],
    }


def _message(*, header: str, context: list[tuple[str, str | None]], body: str | None) -> tuple[str, list]:
    """Build a (fallback_text, blocks) pair shared by every event builder."""
    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": _truncate(header, _MAX_HEADER)}},
        _context_line(context),
    ]
    body_text = _truncate(body, _MAX_SECTION)
    if body_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body_text}})
    fallback = header
    ctx = " · ".join(f"{label}: {value}" for label, value in context if value)
    if ctx:
        fallback = f"{header} — {ctx}"
    return fallback, blocks


# --- Event builders -------------------------------------------------------
# Each is fire-and-forget: builds the message, then dispatches off-thread.
# Adding a new event = one builder here (+ a new category string if it should
# land in a different channel).


def notify_feature_request(req) -> None:
    """A feature request was placed from a client dashboard."""
    if not enabled():
        return
    where = getattr(req, "page_label", None) or getattr(req, "page_path", None)
    text, blocks = _message(
        header="💡 New feature request",
        context=[
            ("Client", getattr(req, "client_slug", None)),
            ("From", getattr(req, "created_by", None)),
            ("Page", where),
        ],
        body=getattr(req, "body", None),
    )
    _dispatch(lambda: post_message(category="default", text=text, blocks=blocks))


def notify_client_note(pad) -> None:
    """A client presenter notepad was created."""
    if not enabled():
        return
    text, blocks = _message(
        header="📝 New client note",
        context=[
            ("Client", getattr(pad, "client_slug", None)),
            ("Title", getattr(pad, "title", None)),
            ("From", getattr(pad, "created_by", None)),
        ],
        body=getattr(pad, "body", None),
    )
    _dispatch(lambda: post_message(category="default", text=text, blocks=blocks))


def notify_dev_note(note) -> None:
    """An internal admin dev note was created."""
    if not enabled():
        return
    text, blocks = _message(
        header="🛠️ New dev note",
        context=[
            ("Title", getattr(note, "title", None)),
            ("Category", getattr(note, "category", None)),
            ("From", getattr(note, "created_by", None)),
        ],
        body=getattr(note, "body", None),
    )
    _dispatch(lambda: post_message(category="default", text=text, blocks=blocks))
