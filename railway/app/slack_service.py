"""Slack notifications for agency-facing events (currently: feature requests).

The agency runs a Slack workspace and wants the "someone wants X" signal from the
client dashboards to land in a channel, not just the ``/admin`` inbox badge. This
module wraps a single Slack app (bot token) and posts to a channel via the Web
API ``chat.postMessage`` endpoint.

Config is agency-wide, via environment variables:

- ``SLACK_BOT_TOKEN`` — the app's bot token (``xoxb-...``). Needs the
  ``chat:write`` scope, and the bot must be a member of the target channel.
- ``SLACK_FEATURE_REQUEST_CHANNEL`` — where feature requests post. A channel id
  (``C0123ABCD``, preferred — survives renames) or ``#channel-name``. Both the
  "someone wants X" notice and the later "it shipped" notice go to this channel;
  the close-out is posted as a *threaded reply* under the original ask, so the two
  stay together instead of drifting apart in the channel.

Notifications are strictly best-effort: every path is wrapped so a Slack outage,
a bad token, or a missing channel can never break the request that triggered it.
Callers get a ``PostedMessage`` (the channel + message ``ts`` Slack assigned) or
``None``, and nothing raises. When the token or channel isn't configured,
``enabled()`` is ``False`` and posts are silent no-ops, so the feature is off
until the agency wires in credentials.

That ``ts`` is what makes threading possible: the caller stores it alongside the
request and hands it back as ``thread_ts`` when the request is marked done. A
``ts`` only resolves in the channel it was issued for, so the channel travels
with it.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from feature_requests import FeatureRequest

_log = logging.getLogger(__name__)

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_TIMEOUT_SECONDS = 10.0
# Slack section text (mrkdwn) is capped at 3000 chars; keep the body well under so
# the surrounding context still fits.
_MAX_BODY_CHARS = 2500


class PostedMessage(NamedTuple):
    """A message Slack accepted: where it landed, and the ``ts`` identifying it.

    Pass ``ts`` back as ``thread_ts`` to reply under it. Slack scopes a ``ts`` to
    its channel, so keep the pair together — ``channel`` is the canonical id from
    Slack's own response, which is what to store even when we posted to
    ``#channel-name``.
    """

    channel: str
    ts: str


def _bot_token() -> str | None:
    return (os.getenv("SLACK_BOT_TOKEN") or "").strip() or None


def _feature_request_channel() -> str | None:
    return (os.getenv("SLACK_FEATURE_REQUEST_CHANNEL") or "").strip() or None


def enabled() -> bool:
    """True when a bot token and a feature-request channel are both configured."""
    return bool(_bot_token() and _feature_request_channel())


def _admin_inbox_url() -> str | None:
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/admin#feature-requests"


def _post_message(
    *,
    channel: str,
    text: str,
    blocks: list[dict[str, Any]],
    thread_ts: str | None = None,
) -> PostedMessage | None:
    """POST to chat.postMessage. Returns the posted message on a Slack ``ok``.

    ``thread_ts`` posts as a reply under that message instead of as a new channel
    message. Returns ``None`` on any failure.

    Never raises: network, HTTP, and Slack-level errors are logged and swallowed so
    a notification failure can't propagate into the caller's flow.
    """
    token = _bot_token()
    if not token:
        return None
    # Imported lazily so the module (and the tests that import it) load without
    # httpx present; it's a runtime dep only when we actually post.
    import httpx

    payload: dict[str, Any] = {"channel": channel, "text": text, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(_POST_MESSAGE_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        _log.warning("Slack post failed (transport): %s", exc)
        return None
    if resp.status_code >= 400:
        _log.warning("Slack post failed (HTTP %s): %s", resp.status_code, resp.text[:300])
        return None
    try:
        data = resp.json()
    except ValueError:
        _log.warning("Slack post returned non-JSON: %s", resp.text[:300])
        return None
    if not data.get("ok"):
        # Common causes: not_in_channel, channel_not_found, invalid_auth, and — for
        # a threaded reply — thread_not_found when the parent has been deleted.
        _log.warning("Slack post rejected: %s", data.get("error") or data)
        return None
    ts = str(data.get("ts") or "").strip()
    if not ts:
        # Posted, but nothing to thread under. Report it and treat as unposted.
        _log.warning("Slack post accepted without a ts: %s", data)
        return None
    return PostedMessage(channel=str(data.get("channel") or channel), ts=ts)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _context_bits(req: FeatureRequest) -> list[str]:
    """The where/who trail under a request — only the parts we actually have."""
    bits: list[str] = []
    if req.client_slug:
        label = req.page_label or req.client_slug
        bits.append(f"*Client:* {label}")
    if req.page_path:
        bits.append(f"*Page:* `{req.page_path}`")
    if req.created_by:
        bits.append(f"*From:* {req.created_by}")
    return bits


def _context_block(bits: list[str]) -> dict[str, Any]:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "  ·  ".join(bits)}],
    }


def _feature_request_blocks(req: FeatureRequest) -> tuple[str, list[dict[str, Any]]]:
    """Build the fallback text + Block Kit blocks for a feature request."""
    where = req.page_label or req.client_slug or "a dashboard"
    fallback = f"New feature request from {where}"

    body = _truncate(req.body.strip(), _MAX_BODY_CHARS) or "_(no description)_"
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "\U0001f4a1 New feature request"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body},
        },
    ]

    context_bits = _context_bits(req)
    inbox_url = _admin_inbox_url()
    if inbox_url:
        context_bits.append(f"<{inbox_url}|Open in admin inbox>")
    if context_bits:
        blocks.append(_context_block(context_bits))
    return fallback, blocks


def _feature_request_done_blocks(
    req: FeatureRequest, *, threaded: bool = False
) -> tuple[str, list[dict[str, Any]]]:
    """Build the fallback text + blocks for a request that's been marked done.

    ``threaded`` says the message will land as a reply under the original ask. In
    that case the ask itself is left out — it's the message directly above — and
    all this adds is the "done" line and who closed it out. Standalone (no parent
    to reply to) it block-quotes the ask, so the channel can still tell *which*
    request shipped without opening the admin inbox.
    """
    where = req.page_label or req.client_slug or "a dashboard"
    fallback = f"Feature request done: {where}"

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\u2705 *Feature request done*"},
        }
    ]
    if not threaded:
        ask = _truncate(req.body.strip(), _MAX_BODY_CHARS) or "_(no description)_"
        # Block-quote the ask so it reads as a callback to the original notice.
        quoted = "\n".join(f"> {line}" for line in ask.splitlines())
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": quoted}})

    # In-thread the client/page trail is already on the parent, so only the new
    # information — who finished it — is worth repeating.
    context_bits = [] if threaded else _context_bits(req)
    if req.resolved_by:
        context_bits.append(f"*Done by:* {req.resolved_by}")
    inbox_url = _admin_inbox_url()
    if inbox_url:
        context_bits.append(f"<{inbox_url}|Open in admin inbox>")
    if context_bits:
        blocks.append(_context_block(context_bits))
    return fallback, blocks


def notify_feature_request(req: FeatureRequest) -> PostedMessage | None:
    """Post a feature request to the configured Slack channel. Best-effort.

    Returns the posted message — store its ``channel``/``ts`` to thread the
    close-out under it later — or ``None`` when the integration is disabled or
    anything went wrong (already logged).
    """
    return _notify(req, _feature_request_blocks, "feature-request")


def notify_feature_request_done(
    req: FeatureRequest,
    *,
    channel: str | None = None,
    thread_ts: str | None = None,
) -> PostedMessage | None:
    """Post the close-out for a request a super admin just marked done.

    With ``thread_ts`` (and the ``channel`` that ``ts`` came from) this replies in
    the original ask's thread, which is the normal case — everyone already in that
    thread gets told it shipped. Without one it falls back to a standalone message
    in the configured channel that quotes the ask, so a request raised before we
    started recording thread ids still gets a close-out. Best-effort.
    """
    threaded = bool(thread_ts)
    return _notify(
        req,
        lambda r: _feature_request_done_blocks(r, threaded=threaded),
        "feature-request-done",
        channel=channel if threaded else None,
        thread_ts=thread_ts,
    )


def _notify(
    req: FeatureRequest,
    build,
    kind: str,
    *,
    channel: str | None = None,
    thread_ts: str | None = None,
) -> PostedMessage | None:
    # A thread_ts only resolves in its own channel, so when one is supplied we post
    # to the channel it came from rather than wherever the env var points now.
    target = (channel or "").strip() or _feature_request_channel()
    if not target:
        return None
    try:
        fallback, blocks = build(req)
        return _post_message(
            channel=target, text=fallback, blocks=blocks, thread_ts=thread_ts
        )
    except Exception:  # noqa: BLE001 — notifications must never break the caller.
        _log.exception("Unexpected error building/sending Slack %s notice", kind)
        return None
