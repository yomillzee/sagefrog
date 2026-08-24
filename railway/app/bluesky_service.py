"""Bluesky (AT Protocol) API client for organic social metrics.

Reads a public account's profile and post history through the AppView's XRPC
API. Two things about the protocol shape this module:

*   **Reads need no credentials.** ``app.bsky.actor.getProfile`` and
    ``app.bsky.feed.getAuthorFeed`` are served unauthenticated by the public
    AppView (``public.api.bsky.app``), so a client only has to tell us their
    handle — there is no OAuth dance and no app password to store. Set
    ``BLUESKY_HANDLE`` + ``BLUESKY_APP_PASSWORD`` to sign in anyway (an
    agency-level login that lifts the anonymous IP rate limit and can read
    accounts that require auth); when they're absent every call goes out
    anonymously.

*   **There are no impressions.** Bluesky publishes likes, reposts, replies,
    quotes and follower totals, and nothing else — no reach, no impressions, no
    clicks. Anything shaped like "impressions" on a Bluesky panel would be
    invented, so this module doesn't produce one.

The counters it does return are *cumulative to now*, not per-day, which is why
the BigQuery layer snapshots them daily (see ``bq_bluesky_service``).

Optional env vars:
    BLUESKY_HANDLE        — agency account handle, for authenticated reads
    BLUESKY_APP_PASSWORD  — app password for that handle (NOT the real password)
    BLUESKY_PDS_URL       — PDS to authenticate against (default bsky.social)
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# Unauthenticated AppView. Serves every read this module makes.
_PUBLIC_BASE = "https://public.api.bsky.app/xrpc"
_DEFAULT_PDS = "https://bsky.social"

# getAuthorFeed's per-page ceiling, and how many pages we'll walk before giving
# up on reaching the requested start date (100 x 25 = 2,500 posts).
_PAGE_LIMIT = 100
_MAX_PAGES = 25

# Session JWTs are short-lived; re-mint well inside their lifetime.
_SESSION_TTL_SECONDS = 45 * 60
_session_cache: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Handles and identifiers
# ---------------------------------------------------------------------------

def normalize_handle(raw: str) -> str:
    """Accept anything a person is likely to paste and return a bare handle.

    ``@sagefrog.bsky.social``, ``https://bsky.app/profile/sagefrog.bsky.social``
    and ``sagefrog.bsky.social`` all normalize to the same string. A raw DID
    (``did:plc:...``) is passed through untouched — it's already an actor id.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith("did:"):
        return value
    value = re.sub(r"^https?://(?:www\.)?bsky\.app/profile/", "", value, flags=re.I)
    value = value.split("/")[0].split("?")[0]
    return value.lstrip("@").strip().lower()


def post_url(handle: str, uri: str) -> str:
    """Turn an ``at://did/app.bsky.feed.post/<rkey>`` URI into a web link."""
    rkey = (uri or "").rsplit("/", 1)[-1]
    if not rkey or not handle:
        return ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _agency_credentials() -> tuple[str, str]:
    return (
        normalize_handle(os.getenv("BLUESKY_HANDLE") or ""),
        (os.getenv("BLUESKY_APP_PASSWORD") or "").strip(),
    )


def _pds_base() -> str:
    return ((os.getenv("BLUESKY_PDS_URL") or "").strip() or _DEFAULT_PDS).rstrip("/")


def _session() -> dict[str, Any] | None:
    """Access token for the agency account, or None when running anonymously.

    A failed sign-in is logged and swallowed: anonymous reads still work, so a
    stale app password degrades the rate limit rather than breaking the sync.
    """
    handle, app_password = _agency_credentials()
    if not handle or not app_password:
        return None

    cached = _session_cache.get("session")
    if cached and (time.monotonic() - cached["minted_at"]) < _SESSION_TTL_SECONDS:
        return cached

    try:
        resp = httpx.post(
            f"{_pds_base()}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.warning("Bluesky sign-in failed for %s (falling back to anonymous reads): %s", handle, exc)
        _session_cache.pop("session", None)
        return None

    session = {
        "access_jwt": data.get("accessJwt") or "",
        "did": data.get("did") or "",
        "minted_at": time.monotonic(),
    }
    if not session["access_jwt"]:
        return None
    _session_cache["session"] = session
    return session


def _get(method: str, params: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    """GET an XRPC method, authenticated when possible. Raises on failure.

    Authenticated calls go to the account's PDS (which proxies to the AppView);
    anonymous ones go straight to the public AppView.
    """
    session = _session()
    if session:
        base = f"{_pds_base()}/xrpc"
        headers = {"Authorization": f"Bearer {session['access_jwt']}"}
    else:
        base = _PUBLIC_BASE
        headers = {}

    try:
        resp = httpx.get(f"{base}/{method}", params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json() or {}
    except httpx.HTTPStatusError as exc:
        try:
            payload = exc.response.json()
            body = str(payload.get("message") or payload.get("error") or "")
        except Exception:
            body = (exc.response.text or "")[:200]
        status = exc.response.status_code
        if status == 400 and "not found" in body.lower():
            raise RuntimeError(f"Bluesky couldn't find that account: {body}") from exc
        if status == 429:
            raise RuntimeError(
                "Bluesky rate-limited this sync. Set BLUESKY_HANDLE and "
                "BLUESKY_APP_PASSWORD for a higher authenticated limit, or retry later."
            ) from exc
        raise RuntimeError(f"Bluesky API error {status} on {method}: {body}") from exc


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def resolve_handle(handle: str) -> str:
    """Handle -> DID. A value that's already a DID is returned unchanged."""
    actor = normalize_handle(handle)
    if not actor:
        raise ValueError("A Bluesky handle is required.")
    if actor.startswith("did:"):
        return actor
    data = _get("com.atproto.identity.resolveHandle", {"handle": actor})
    did = data.get("did") or ""
    if not did:
        raise RuntimeError(f"Bluesky returned no DID for handle '{actor}'.")
    return did


def fetch_profile(actor: str) -> dict[str, Any]:
    """Profile snapshot: identity plus the three counters Bluesky exposes."""
    handle = normalize_handle(actor)
    data = _get("app.bsky.actor.getProfile", {"actor": handle})
    return {
        "did": data.get("did") or "",
        "handle": data.get("handle") or handle,
        "display_name": data.get("displayName") or "",
        "description": data.get("description") or "",
        "followers_count": _int(data.get("followersCount")),
        "follows_count": _int(data.get("followsCount")),
        "posts_count": _int(data.get("postsCount")),
        "account_created_at": data.get("createdAt") or None,
    }


def fetch_author_feed(
    actor: str,
    *,
    since: date | None = None,
    until: date | None = None,
    max_pages: int = _MAX_PAGES,
) -> list[dict[str, Any]]:
    """Every post the account authored between ``since`` and ``until``.

    The feed is reverse-chronological, so we page until we run past ``since``.
    Reposts of *other* people's posts appear in the feed with a ``reason`` block
    and someone else's author DID — they carry that author's engagement, not the
    client's, so they're dropped.
    """
    handle = normalize_handle(actor)
    cursor: str | None = None
    posts: list[dict[str, Any]] = []
    own_did: str | None = None

    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "actor": handle,
            "limit": _PAGE_LIMIT,
            "filter": "posts_with_replies",
        }
        if cursor:
            params["cursor"] = cursor
        page = _get("app.bsky.feed.getAuthorFeed", params)
        items = page.get("feed") or []
        if not items:
            break

        exhausted = False
        for item in items:
            post = item.get("post") or {}
            author = post.get("author") or {}
            if own_did is None and not item.get("reason"):
                own_did = author.get("did") or None
            # Reposts and other people's posts in a thread — not this account's.
            if item.get("reason") or (own_did and author.get("did") != own_did):
                continue

            parsed = _parse_post(post, handle)
            created = parsed.get("created_date")
            if since and created and created < since:
                # Past the window; everything after this is older still.
                exhausted = True
                break
            if until and created and created > until:
                continue
            posts.append(parsed)

        cursor = page.get("cursor")
        if exhausted or not cursor:
            break

    return posts


def build_bluesky_snapshot(
    handle: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    """Profile + posts in one call, never raising.

    Mirrors ``semrush_service.build_semrush_snapshot``: a hard failure (bad
    handle) comes back as ``error``; a partial one (profile fine, feed failed)
    comes back as ``errors`` with whatever did load.
    """
    actor = normalize_handle(handle)
    if not actor:
        return {"error": "A Bluesky handle is required.", "profile": {}, "posts": []}

    errors: dict[str, str] = {}
    try:
        profile = fetch_profile(actor)
    except Exception as exc:
        _log.warning("Bluesky profile fetch failed [%s]: %s", actor, exc)
        return {"error": str(exc)[:300], "profile": {}, "posts": []}

    try:
        posts = fetch_author_feed(actor, since=since, until=until)
    except Exception as exc:
        _log.warning("Bluesky feed fetch failed [%s]: %s", actor, exc)
        posts = []
        errors["feed"] = str(exc)[:300]

    return {
        "handle": profile.get("handle") or actor,
        "did": profile.get("did") or "",
        "profile": profile,
        "posts": posts,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _embed_type(post: dict[str, Any]) -> str:
    """``app.bsky.embed.images#view`` -> ``images``; no embed -> ``text``."""
    raw = str(((post.get("embed") or {}).get("$type")) or "")
    if not raw:
        return "text"
    return raw.split("#")[0].rsplit(".", 1)[-1] or "text"


def _parse_post(post: dict[str, Any], handle: str) -> dict[str, Any]:
    record = post.get("record") or {}
    created = _parse_ts(record.get("createdAt")) or _parse_ts(post.get("indexedAt"))
    likes = _int(post.get("likeCount"))
    reposts = _int(post.get("repostCount"))
    replies = _int(post.get("replyCount"))
    quotes = _int(post.get("quoteCount"))
    return {
        "uri": post.get("uri") or "",
        "cid": post.get("cid") or "",
        "url": post_url(handle, post.get("uri") or ""),
        "text": (record.get("text") or "")[:2000],
        "created_at": created,
        "created_date": created.date() if created else None,
        "is_reply": bool(record.get("reply")),
        "embed_type": _embed_type(post),
        "langs": ",".join(record.get("langs") or []) or None,
        "like_count": likes,
        "repost_count": reposts,
        "reply_count": replies,
        "quote_count": quotes,
        # Bluesky has no impressions, so "engagements" is the whole story here.
        "engagements": likes + reposts + replies + quotes,
    }
