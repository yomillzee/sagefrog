"""LinkedIn *organic* (company-page) data fetchers.

Sibling to ``linkedin_service`` (which is paid/sponsored only). This module reads
organic organization metrics via LinkedIn's Community Management API:

  * posts + per-post engagement (impressions, clicks, likes, comments, shares)
  * follower counts and daily organic/paid follower gains
  * organization page views / unique visitors

It reuses the low-level HTTP + auth helpers from ``linkedin_service`` (same OAuth
token, same version-fallback plumbing) so there is no duplicated transport code.

Scope requirements (see oauth_flows.LINKEDIN_SCOPES):
  * ``r_organization_social``  -> posts + socialActions (likes/comments)
  * ``r_organization_admin``   -> share statistics, follower stats, page stats

NOTE: the exact JSON shapes below follow LinkedIn's published Community
Management API docs. Parsing is deliberately tolerant (``.get`` chains, type
coercion) because LinkedIn versions these payloads; validate against a real sync
in the deployed environment before relying on any single field.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

from dates_util import resolve_date_range
from linkedin_auth import LinkedInEnv, load_linkedin_env
from linkedin_service import (
    _linkedin_get,
    _linkedin_get_with_versions,
    refresh_access_token,
)

_log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# URN / date helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_org_id(org_id: str) -> str:
    """Accept a bare id, ``urn:li:organization:123`` or ``123`` -> ``123``."""
    return str(org_id or "").strip().split(":")[-1]


def _org_urn(org_id: str) -> str:
    return f"urn:li:organization:{_normalize_org_id(org_id)}"


def _urn_id(urn: str) -> str:
    return str(urn or "").strip().split(":")[-1]


def _urn_type(urn: str) -> str:
    """Return the entity type segment of a post URN.

    ``urn:li:share:123`` -> ``share``; ``urn:li:ugcPost:456`` -> ``ugcPost``.
    """
    parts = str(urn or "").strip().split(":")
    return parts[2] if len(parts) >= 4 else ""


def _epoch_ms(value: date) -> int:
    """Midnight-UTC epoch milliseconds for a date (LinkedIn timeRange unit)."""
    dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _date_from_epoch_ms(value: Any) -> date | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def _time_intervals(start: date, end: date) -> str:
    """LinkedIn ``timeIntervals`` param value with DAY granularity."""
    return (
        f"(timeRange:(start:{_epoch_ms(start)},end:{_epoch_ms(end)}),"
        f"timeGranularityType:DAY)"
    )


def _resolve_token(access_token: str | None, env: LinkedInEnv | None) -> tuple[str, LinkedInEnv]:
    if env is not None:
        token = access_token or refresh_access_token(env)["access_token"]
        return token, env
    if access_token:
        # A client-scoped token was already minted (connector path); we only need
        # the API version for headers, not the global client_id/secret. This lets
        # a client whose token lives under its own slug sync even when the global
        # LINKEDIN_CLIENT_ID env is absent.
        from linkedin_auth import _ENV_ALIASES, _get_env
        env = LinkedInEnv(
            client_id="",
            client_secret="",
            refresh_token="",
            version=_get_env(*_ENV_ALIASES["version"]) or "202509",
        )
        return access_token, env
    env = load_linkedin_env(require_token=True)
    return refresh_access_token(env)["access_token"], env


# ──────────────────────────────────────────────────────────────────────────────
# Organization discovery (auto-populates the connector wizard's account picker)
# ──────────────────────────────────────────────────────────────────────────────

def list_organizations(
    *,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """List organization pages the authenticated user has a role on.

    Uses ``/organizationAcls?q=roleAssignee`` and hydrates each organizational
    target with its localized name. Shaped like ``linkedin_service.list_ad_accounts``
    so the connector wizard renders the same account-picker UX.

    Deliberately does NOT filter to ``role=ADMINISTRATOR``: agency staff are often
    added to a client's page as a Content Admin / Analyst (distinct ACL roles), and
    filtering to ADMINISTRATOR would silently hide those pages and surface as an
    empty "No accounts found". We list every page the member holds any approved
    role on; the sync itself degrades gracefully if a given role lacks a stat scope.
    """
    token, env = _resolve_token(access_token, env)

    # Try approved grants first, then fall back to an unfiltered roleAssignee query
    # (covers pages whose ACL state isn't reported as APPROVED). The first query
    # that yields rows wins.
    elements: list[dict[str, Any]] = []
    acl_error: Exception | None = None
    for query in ("q=roleAssignee&state=APPROVED", "q=roleAssignee"):
        try:
            payload = _linkedin_get(
                f"/organizationAcls?{query}", access_token=token, env=env,
            )
            elements = payload.get("elements") or []
        except Exception as exc:  # pragma: no cover - network dependent
            _log.warning("organizationAcls (%s) failed: %s", query, exc)
            acl_error = exc
            continue
        if elements:
            break

    # A hard API failure (missing scope, revoked token, wrong app) must surface in
    # the wizard, not masquerade as "No accounts found" — that empty state is
    # reserved for an authenticated member who genuinely administers no pages.
    if not elements and acl_error is not None:
        raise RuntimeError(f"LinkedIn organization lookup failed: {str(acl_error)[:300]}")

    org_ids: list[str] = []
    seen: set[str] = set()
    for row in elements:
        target = str(
            row.get("organizationalTarget")
            or row.get("organizationalTarget~")
            or row.get("organization")
            or ""
        )
        oid = _urn_id(target)
        if oid and oid not in seen:
            seen.add(oid)
            org_ids.append(oid)
    _log.info(
        "linkedin organizationAcls: %d acl rows, %d distinct orgs", len(elements), len(org_ids)
    )

    accounts: list[dict[str, Any]] = []
    for oid in org_ids:
        name = ""
        status = ""
        try:
            org = _linkedin_get_with_versions(
                f"/organizations/{oid}", access_token=token, env=env
            )
            name = (
                org.get("localizedName")
                or org.get("vanityName")
                or (org.get("name") or {}).get("localized", {}).get("en_US")
                or ""
            )
            status = str(org.get("status") or "")
        except Exception as exc:  # pragma: no cover - network dependent
            _log.warning("organizations/%s lookup failed: %s", oid, exc)
        accounts.append({"id": oid, "name": name or f"Organization {oid}", "status": status})

    accounts.sort(key=lambda a: (a.get("name") or a["id"]).lower())
    return accounts


# ──────────────────────────────────────────────────────────────────────────────
# Posts + per-post engagement
# ──────────────────────────────────────────────────────────────────────────────

def _list_org_posts(
    org_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    count: int = 50,
    max_pages: int = 40,
) -> list[dict[str, Any]]:
    """Follow ``/posts?q=author`` cursor pagination for one organization."""
    org_urn = quote(_org_urn(org_id), safe="")
    base = f"/posts?q=author&author={org_urn}&count={count}"
    out: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(max_pages):
        query = {"pageToken": page_token} if page_token else None
        payload = _linkedin_get_with_versions(
            base, params=query, access_token=access_token, env=env
        )
        batch = payload.get("elements") or []
        out.extend(batch)
        page_token = (payload.get("metadata") or {}).get("nextPageToken") or ""
        if not page_token:
            break
    return out


def _post_title(post: dict[str, Any]) -> str:
    """Best-effort short label for a post (commentary text, trimmed)."""
    text = post.get("commentary")
    if not text:
        specific = post.get("specificContent") or {}
        share = (specific.get("com.linkedin.ugc.ShareContent") or {})
        text = ((share.get("shareCommentary") or {}).get("text")) or ""
    text = str(text or "").strip().replace("\n", " ")
    return text[:200]


def _post_type(post: dict[str, Any]) -> str:
    """Coarse content type: video / image / article / text."""
    content = post.get("content") or {}
    if "media" in content:
        media = content.get("media") or {}
        if "video" in str(media).lower():
            return "video"
        return "image"
    if "article" in content:
        return "article"
    if "multiImage" in content:
        return "image"
    return "text"


def _share_stats_by_urn(
    org_id: str,
    post_urns: list[str],
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, dict[str, Any]]:
    """Fetch lifetime share statistics for the given post URNs.

    Batches by post entity type (``shares`` vs ``ugcPosts`` query params) because
    ``organizationalEntityShareStatistics`` keys them separately. Returns
    ``{post_urn: totalShareStatistics}``.
    """
    org_urn = quote(_org_urn(org_id), safe="")
    by_type: dict[str, list[str]] = {}
    for urn in post_urns:
        t = _urn_type(urn)
        param = {"share": "shares", "ugcPost": "ugcPosts"}.get(t)
        if param:
            by_type.setdefault(param, []).append(urn)

    stats: dict[str, dict[str, Any]] = {}
    chunk_size = 20
    for param, urns in by_type.items():
        for offset in range(0, len(urns), chunk_size):
            chunk = urns[offset : offset + chunk_size]
            list_expr = "List(" + ",".join(quote(u, safe="") for u in chunk) + ")"
            path = (
                f"/organizationalEntityShareStatistics?q=organizationalEntity"
                f"&organizationalEntity={org_urn}&{param}={list_expr}"
            )
            try:
                payload = _linkedin_get_with_versions(
                    path, access_token=access_token, env=env
                )
            except Exception as exc:  # pragma: no cover - network dependent
                _log.warning("share statistics chunk failed (%s): %s", param, exc)
                continue
            for row in payload.get("elements") or []:
                urn = str(row.get("share") or row.get("ugcPost") or "")
                if urn:
                    stats[urn] = row.get("totalShareStatistics") or {}
    return stats


def _parse_share_stats(raw: dict[str, Any]) -> dict[str, Any]:
    impressions = int(raw.get("impressionCount") or 0)
    clicks = int(raw.get("clickCount") or 0)
    likes = int(raw.get("likeCount") or 0)
    comments = int(raw.get("commentCount") or 0)
    shares = int(raw.get("shareCount") or 0)
    # LinkedIn reports engagement as a rate (0..1); fall back to a computed ratio.
    engagement_rate = float(raw.get("engagement") or 0.0)
    if not engagement_rate and impressions:
        engagement_rate = (likes + comments + shares + clicks) / impressions
    return {
        "impressions": impressions,
        "clicks": clicks,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement_rate": engagement_rate,
    }


def fetch_posts_with_stats(
    org_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """Return one row per organization post published within [start, end].

    Grain is *post* (not post-day): LinkedIn exposes per-post engagement as
    lifetime totals, so each sync refreshes the current totals for posts in the
    window. Row shape:
      {post_id, post_urn, org_id, title, post_type, published_at,
       impressions, clicks, likes, comments, shares, engagement_rate}
    """
    token, env = _resolve_token(access_token, env)
    org_id_clean = _normalize_org_id(org_id)
    if not org_id_clean:
        raise ValueError("org_id is required")

    posts = _list_org_posts(org_id_clean, access_token=token, env=env)

    in_window: list[dict[str, Any]] = []
    for post in posts:
        urn = str(post.get("id") or "")
        if not urn:
            continue
        published = _date_from_epoch_ms(
            post.get("createdAt")
            or (post.get("created") or {}).get("time")
            or post.get("firstPublishedAt")
        )
        # Keep posts with no parseable date (metadata gaps) so they still surface;
        # otherwise filter to the requested window.
        if published and not (start <= published <= end):
            continue
        in_window.append({"urn": urn, "published": published, "post": post})

    stats_by_urn = _share_stats_by_urn(
        org_id_clean,
        [p["urn"] for p in in_window],
        access_token=token,
        env=env,
    )

    out: list[dict[str, Any]] = []
    for entry in in_window:
        urn = entry["urn"]
        post = entry["post"]
        parsed = _parse_share_stats(stats_by_urn.get(urn) or {})
        out.append(
            {
                "post_id": _urn_id(urn),
                "post_urn": urn,
                "org_id": org_id_clean,
                "title": _post_title(post),
                "post_type": _post_type(post),
                "published_at": entry["published"].isoformat() if entry["published"] else "",
                **parsed,
            }
        )
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Follower statistics
# ──────────────────────────────────────────────────────────────────────────────

def _total_followers(
    org_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> int:
    """Lifetime follower count via ``/networkSizes/{orgUrn}``."""
    org_urn = quote(_org_urn(org_id), safe="")
    try:
        payload = _linkedin_get_with_versions(
            f"/networkSizes/{org_urn}?edgeType=CompanyFollowedByMember",
            access_token=access_token,
            env=env,
        )
        return int(payload.get("firstDegreeSize") or 0)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("networkSizes lookup failed for %s: %s", org_id, exc)
        return 0


def fetch_follower_daily(
    org_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """One row per day: organic/paid/total follower gains + lifetime total.

    The lifetime total is a point-in-time value (``/networkSizes``) attached to
    the most recent day in the window; earlier days carry 0 (gains are the
    reliable daily series).
    """
    token, env = _resolve_token(access_token, env)
    org_id_clean = _normalize_org_id(org_id)
    org_urn = quote(_org_urn(org_id_clean), safe="")

    path = (
        f"/organizationalEntityFollowerStatistics?q=organizationalEntity"
        f"&organizationalEntity={org_urn}&timeIntervals={_time_intervals(start, end)}"
    )
    try:
        payload = _linkedin_get_with_versions(path, access_token=token, env=env)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("follower statistics failed for %s: %s", org_id_clean, exc)
        payload = {}

    by_date: dict[str, dict[str, Any]] = {}
    for row in payload.get("elements") or []:
        day = _date_from_epoch_ms((row.get("timeRange") or {}).get("start"))
        if not day:
            continue
        gains = row.get("followerGains") or {}
        organic = int(gains.get("organicFollowerGain") or 0)
        paid = int(gains.get("paidFollowerGain") or 0)
        by_date[day.isoformat()] = {
            "metric_date": day.isoformat(),
            "org_id": org_id_clean,
            "organic_follower_gain": organic,
            "paid_follower_gain": paid,
            "total_follower_gain": organic + paid,
            "total_followers": 0,
        }

    rows = [by_date[k] for k in sorted(by_date)]
    if rows:
        rows[-1]["total_followers"] = _total_followers(
            org_id_clean, access_token=token, env=env
        )
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Page / visitor statistics
# ──────────────────────────────────────────────────────────────────────────────

def _sum_page_views(views: dict[str, Any]) -> tuple[int, int]:
    """Return (page_views, unique_visitors) summed across page sections."""
    page_views = 0
    unique = 0
    for section in (views or {}).values():
        if not isinstance(section, dict):
            continue
        page_views += int(section.get("pageViews") or 0)
        unique += int(section.get("uniquePageViews") or 0)
    return page_views, unique


def fetch_page_daily(
    org_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """One row per day: page views + unique visitors for the organization page."""
    token, env = _resolve_token(access_token, env)
    org_id_clean = _normalize_org_id(org_id)
    org_urn = quote(_org_urn(org_id_clean), safe="")

    path = (
        f"/organizationPageStatistics?q=organization"
        f"&organization={org_urn}&timeIntervals={_time_intervals(start, end)}"
    )
    try:
        payload = _linkedin_get_with_versions(path, access_token=token, env=env)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("page statistics failed for %s: %s", org_id_clean, exc)
        payload = {}

    out: list[dict[str, Any]] = []
    for row in payload.get("elements") or []:
        day = _date_from_epoch_ms((row.get("timeRange") or {}).get("start"))
        if not day:
            continue
        total = row.get("totalPageStatistics") or {}
        page_views, unique = _sum_page_views(total.get("views") or {})
        out.append(
            {
                "metric_date": day.isoformat(),
                "org_id": org_id_clean,
                "page_views": page_views,
                "unique_visitors": unique,
            }
        )
    out.sort(key=lambda r: r["metric_date"])
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: verify a live connection (used by the connector's test step)
# ──────────────────────────────────────────────────────────────────────────────

def test_connection(
    *,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> dict[str, Any]:
    try:
        orgs = list_organizations(access_token=access_token, env=env)
        return {
            "ok": True,
            "message": "LinkedIn organic connection succeeded.",
            "organization_count": len(orgs),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": "LinkedIn organic connection failed.",
            "organization_count": 0,
            "error": str(exc)[:300],
        }
