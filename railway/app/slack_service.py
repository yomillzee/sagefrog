"""Slack notifications for agency-facing events (currently: feature requests).

The agency runs a Slack workspace and wants the "someone wants X" signal from the
client dashboards to land in a channel, not just the ``/admin`` inbox badge. This
module wraps a single Slack app (bot token) and posts to a channel via the Web
API ``chat.postMessage`` endpoint.

Config is agency-wide, via environment variables:

- ``SLACK_BOT_TOKEN`` — the app's bot token (``xoxb-...``). Needs the
  ``chat:write`` scope, and the bot must be a member of the target channel.
- ``SLACK_FEATURE_REQUEST_CHANNEL`` — where feature requests post. A channel id
  (``C0123ABCD``, preferred — survives renames) or ``#channel-name``.

Notifications are strictly best-effort: every path is wrapped so a Slack outage,
a bad token, or a missing channel can never break the request that triggered it.
Callers get a ``bool`` (posted or not) and nothing raises. When the token or
channel isn't configured, ``enabled()`` is ``False`` and posts are silent no-ops,
so the feature is off until the agency wires in credentials.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from feature_requests import FeatureRequest

_log = logging.getLogger(__name__)

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_TIMEOUT_SECONDS = 10.0
# Slack section text (mrkdwn) is capped at 3000 chars; keep the body well under so
# the surrounding context still fits.
_MAX_BODY_CHARS = 2500


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


def _post_message(*, channel: str, text: str, blocks: list[dict[str, Any]]) -> bool:
    """POST to chat.postMessage. Returns True only on a confirmed Slack ``ok``.

    Never raises: network, HTTP, and Slack-level errors are logged and swallowed so
    a notification failure can't propagate into the caller's flow.
    """
    token = _bot_token()
    if not token:
        return False
    # Imported lazily so the module (and the tests that import it) load without
    # httpx present; it's a runtime dep only when we actually post.
    import httpx

    payload = {"channel": channel, "text": text, "blocks": blocks}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(_POST_MESSAGE_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        _log.warning("Slack post failed (transport): %s", exc)
        return False
    if resp.status_code >= 400:
        _log.warning("Slack post failed (HTTP %s): %s", resp.status_code, resp.text[:300])
        return False
    try:
        data = resp.json()
    except ValueError:
        _log.warning("Slack post returned non-JSON: %s", resp.text[:300])
        return False
    if not data.get("ok"):
        # Common causes: not_in_channel, channel_not_found, invalid_auth.
        _log.warning("Slack post rejected: %s", data.get("error") or data)
        return False
    return True


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


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

    # Context line: client / page / author, only the parts we actually have.
    context_bits: list[str] = []
    if req.client_slug:
        label = req.page_label or req.client_slug
        context_bits.append(f"*Client:* {label}")
    if req.page_path:
        context_bits.append(f"*Page:* `{req.page_path}`")
    if req.created_by:
        context_bits.append(f"*From:* {req.created_by}")
    inbox_url = _admin_inbox_url()
    if inbox_url:
        context_bits.append(f"<{inbox_url}|Open in admin inbox>")
    if context_bits:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "  ·  ".join(context_bits)}],
            }
        )
    return fallback, blocks


def notify_feature_request(req: FeatureRequest) -> bool:
    """Post a feature request to the configured Slack channel. Best-effort.

    Returns True when Slack confirms the message was posted, False when the
    integration is disabled or anything went wrong (already logged).
    """
    channel = _feature_request_channel()
    if not channel:
        return False
    try:
        fallback, blocks = _feature_request_blocks(req)
        return _post_message(channel=channel, text=fallback, blocks=blocks)
    except Exception:  # noqa: BLE001 — notifications must never break the caller.
        _log.exception("Unexpected error building/sending Slack feature-request notice")
        return False
