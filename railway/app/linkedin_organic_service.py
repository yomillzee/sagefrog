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
from datetime import date, datetime, UTC
from typing import Any
from urllib.parse import quote

import linkedin_taxonomy
from linkedin_auth import LinkedInEnv, load_linkedin_env
from linkedin_service import (
    _linkedin_get,
    _linkedin_get_with_versions,
    _reference_data_get,
    refresh_access_token,
)

_log = logging.getLogger(__name__)

# How many posts get a per-post reaction-type breakdown in one sync. The Top
# posts table lists at most 50, so this covers it with room to spare while
# keeping a long backfill's call count bounded.
_REACTION_POST_LIMIT = 100


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
    dt = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _date_from_epoch_ms(value: Any) -> date | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


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

def _next_page_token(payload: dict[str, Any]) -> str:
    """Cursor for the next page, or "" when the payload doesn't carry one.

    LinkedIn moved cursor pagination between ``metadata`` and ``paging`` across
    API versions, so check both rather than assuming one shape and silently
    stopping after page 1.
    """
    for holder in (payload.get("metadata"), payload.get("paging")):
        if isinstance(holder, dict):
            token = holder.get("nextPageToken") or ""
            if token:
                return str(token)
    return ""


def _list_org_posts(
    org_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
    count: int = 50,
    max_pages: int = 40,
) -> list[dict[str, Any]]:
    """Return every ``/posts?q=author`` post for one organization.

    Pagination is deliberately belt-and-braces, because getting it wrong is
    invisible: the first page still returns 50 good posts, so a sync "succeeds"
    while quietly capping the client's whole post history at one page.

    * Finder params go through ``params`` rather than being baked into the path.
      httpx *replaces* a URL's existing query string when ``params`` is passed,
      so a hand-built ``/posts?q=author&...`` path would lose ``q``/``author``
      the moment a page-2 cursor was added.
    * Cursor pagination is used when the response carries a token; otherwise we
      fall back to index-based ``start`` paging, which is what the versions that
      omit ``nextPageToken`` expect.
    * We stop as soon as a page returns nothing new, so an API that ignores the
      cursor can't spin us through ``max_pages`` of duplicates.
    """
    org_urn = _org_urn(org_id)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_token = ""
    start = 0

    for _ in range(max_pages):
        params: list[tuple[str, Any]] = [
            ("q", "author"),
            ("author", org_urn),
            ("count", count),
        ]
        if page_token:
            params.append(("pageToken", page_token))
        elif start:
            params.append(("start", start))

        payload = _linkedin_get_with_versions(
            "/posts", params=params, access_token=access_token, env=env
        )
        batch = payload.get("elements") or []

        fresh = 0
        for post in batch:
            key = str(post.get("id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(post)
            fresh += 1

        # Nothing new on this page: either the source is exhausted or it ignored
        # our cursor and replayed a page we already have. Either way, stop.
        if not fresh:
            break

        page_token = _next_page_token(payload)
        if not page_token:
            # Index-based fallback. A short page means we reached the end.
            if len(batch) < count:
                break
            start += len(batch)

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
    # Reach: distinct members who saw the post, always <= impressions. Distinct
    # from impressionCount, which counts every view including repeats.
    unique_impressions = int(raw.get("uniqueImpressionsCount") or 0)
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
        "unique_impressions": unique_impressions,
        "clicks": clicks,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement_rate": engagement_rate,
    }


# LinkedIn reaction types (Community Management ``reactionSummaries``). We keep
# the human-facing subset; anything LinkedIn adds later still round-trips through
# the raw key so no reaction is silently dropped.
_REACTION_LABELS = {
    "LIKE": "Like",
    "PRAISE": "Celebrate",
    "APPRECIATION": "Love",
    "EMPATHY": "Support",
    "INTEREST": "Insightful",
    "ENTERTAINMENT": "Funny",
    "MAYBE": "Curious",
}


def _reactions_by_urn(
    post_urns: list[str],
    *,
    access_token: str,
    env: LinkedInEnv,
) -> dict[str, dict[str, int]]:
    """Best-effort per-post reaction-type counts via ``/socialActions/{urn}``.

    Returns ``{post_urn: {REACTION_TYPE: count}}``. LinkedIn exposes reaction
    breakdowns only per entity (not batched), so this is one call per post; each
    is isolated so a single failure (or a version that omits ``reactionSummaries``)
    just yields no breakdown for that post rather than failing the sync.
    """
    out: dict[str, dict[str, int]] = {}
    for urn in post_urns:
        try:
            payload = _linkedin_get_with_versions(
                f"/socialActions/{quote(urn, safe='')}",
                access_token=access_token,
                env=env,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            _log.warning("reaction summary failed for %s: %s", urn, exc)
            continue
        summaries = payload.get("reactionSummaries") or {}
        counts: dict[str, int] = {}
        for rtype, summary in summaries.items():
            if isinstance(summary, dict):
                c = int(summary.get("count") or 0)
            else:
                c = int(summary or 0)
            if c:
                counts[str(rtype).upper()] = c
        if counts:
            out[urn] = counts
    return out


def fetch_posts_with_stats(
    org_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
    with_reactions: bool = True,
) -> list[dict[str, Any]]:
    """Return one row per organization post published within [start, end].

    Grain is *post* (not post-day): LinkedIn exposes per-post engagement as
    lifetime totals, so each sync refreshes the current totals for posts in the
    window. Row shape:
      {post_id, post_urn, org_id, title, post_type, published_at,
       impressions, unique_impressions, clicks, likes, comments, shares,
       engagement_rate, reactions_by_type}
    ``reactions_by_type`` is a ``{REACTION_TYPE: count}`` dict (empty when the
    breakdown is unavailable); pass ``with_reactions=False`` to skip the extra
    per-post reaction calls.
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
    # Reaction breakdowns are one HTTP call *per post* (LinkedIn exposes no batch
    # form), so they are capped at the most-viewed posts rather than run over the
    # whole window. A year-long backfill is ~1k posts; calling for all of them
    # would spend minutes and risk timing the sync out, and the breakdown is only
    # ever surfaced next to the Top-posts table, which shows at most 50 rows.
    reaction_targets = sorted(
        (p["urn"] for p in in_window),
        key=lambda u: int((stats_by_urn.get(u) or {}).get("impressionCount") or 0),
        reverse=True,
    )[:_REACTION_POST_LIMIT]
    reactions_by_urn = (
        _reactions_by_urn(reaction_targets, access_token=token, env=env)
        if with_reactions else {}
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
                "reactions_by_type": reactions_by_urn.get(urn, {}),
                **parsed,
            }
        )
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Follower statistics
# ──────────────────────────────────────────────────────────────────────────────

def _lifetime_followers_from_stats(
    org_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> int:
    """Total followers summed from lifetime follower statistics segments.

    ``organizationalEntityFollowerStatistics`` WITHOUT a timeIntervals param
    returns lifetime counts broken out by several dimensions
    (``followerCountsByAssociationType``, ``…BySeniority``, ``…ByFunction``,
    ``…ByStaffCountRange`` …). Each dimension partitions the whole audience, so
    summing organic+paid across one dimension yields the total. Dimensions differ
    in how many followers they can bucket (some land in no seniority/function),
    so we take the largest dimension sum as the most complete estimate.
    """
    org_urn = quote(_org_urn(org_id), safe="")
    path = (
        f"/organizationalEntityFollowerStatistics?q=organizationalEntity"
        f"&organizationalEntity={org_urn}"
    )
    try:
        payload = _linkedin_get_with_versions(path, access_token=access_token, env=env)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("lifetime follower statistics failed for %s: %s", org_id, exc)
        return 0

    best = 0
    for element in payload.get("elements") or []:
        for key, rows in element.items():
            if not key.startswith("followerCountsBy") or not isinstance(rows, list):
                continue
            dim_total = 0
            for row in rows:
                counts = row.get("followerCounts") or {}
                dim_total += int(counts.get("organicFollowerCount") or 0)
                dim_total += int(counts.get("paidFollowerCount") or 0)
            best = max(best, dim_total)
    return best


# Stable LinkedIn taxonomies — resolved locally so the common demographic
# dimensions need no extra reference-data calls. (Industry and geo are far larger
# and version-dependent, so those resolve against the API with a raw-id fallback.)
# Shared with the *ads* member demographics, so the tables themselves live in
# linkedin_taxonomy; these aliases keep this module's long-standing names.
_SENIORITY_LABELS = linkedin_taxonomy.SENIORITY_LABELS
_FUNCTION_LABELS = linkedin_taxonomy.FUNCTION_LABELS

# followerCountsBy<X> array key -> (dimension id, entry field holding the value).
_FOLLOWER_DIMENSIONS = {
    "followerCountsBySeniority": ("seniority", "seniority"),
    "followerCountsByFunction": ("function", "function"),
    "followerCountsByIndustry": ("industry", "industry"),
    "followerCountsByStaffCountRange": ("company_size", "staffCountRange"),
    "followerCountsByGeoCountry": ("region", "geoCountry"),
    "followerCountsByGeo": ("region", "geo"),
    "followerCountsByRegion": ("region", "region"),
    "followerCountsByAssociationType": ("association", "associationType"),
}


_humanize_staff_range = linkedin_taxonomy.humanize_staff_range
_humanize_enum = linkedin_taxonomy.humanize_enum

# Reference-data endpoint paths (NOT a naive plural of the kind — "industry" ->
# "industries", "geo" has no trailing 's'). A wrong path — or the wrong API base,
# see _reference_data_get — 404s and every label silently falls back to the raw
# id, so keep this mapping explicit.
_REFERENCE_ENDPOINTS = linkedin_taxonomy.REFERENCE_ENDPOINTS
# Friendlier placeholder when a lookup can't resolve, per kind.
_REFERENCE_FALLBACK = linkedin_taxonomy.REFERENCE_FALLBACK


def _resolve_reference_label(
    kind: str, ref_id: str, *, access_token: str, env: LinkedInEnv,
    cache: dict[str, str],
) -> str:
    """Best-effort localized name for an ``industry``/``geo`` id, cached per sync.

    Every kind resolved here is standardized data, so the lookup goes to the v2
    base rather than the versioned one the statistics calls use. Falls back to a
    readable placeholder when the reference endpoint is unavailable or shaped
    differently across API versions."""
    return linkedin_taxonomy.resolve_reference_label(
        kind,
        ref_id,
        get=lambda path: _reference_data_get(path, access_token=access_token, env=env),
        cache=cache,
    )


def fetch_follower_demographics(
    org_id: str,
    *,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
    top_n: int = 25,
) -> list[dict[str, Any]]:
    """Lifetime follower breakdown by seniority, function, industry, company
    size, and region.

    Reads the same ``organizationalEntityFollowerStatistics`` payload the total
    follower count is derived from (no timeIntervals -> lifetime segments), but
    keeps the per-category counts instead of summing them away. Each row:
      {org_id, dimension, category, category_urn,
       organic_followers, paid_followers, total_followers}
    Capped to the ``top_n`` categories per dimension by follower count.
    """
    token, env = _resolve_token(access_token, env)
    org_id_clean = _normalize_org_id(org_id)
    org_urn = quote(_org_urn(org_id_clean), safe="")
    path = (
        f"/organizationalEntityFollowerStatistics?q=organizationalEntity"
        f"&organizationalEntity={org_urn}"
    )
    try:
        payload = _linkedin_get_with_versions(path, access_token=token, env=env)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("follower demographics failed for %s: %s", org_id_clean, exc)
        return []

    label_cache: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for element in payload.get("elements") or []:
        for array_key, (dimension, value_field) in _FOLLOWER_DIMENSIONS.items():
            entries = element.get(array_key)
            if not isinstance(entries, list):
                continue
            dim_rows: list[dict[str, Any]] = []
            for entry in entries:
                raw_value = str(entry.get(value_field) or "")
                if not raw_value:
                    continue
                counts = entry.get("followerCounts") or {}
                organic = int(counts.get("organicFollowerCount") or 0)
                paid = int(counts.get("paidFollowerCount") or 0)
                total = organic + paid
                if total <= 0:
                    continue
                dim_rows.append({
                    "org_id": org_id_clean,
                    "dimension": dimension,
                    "category": _demographic_label(
                        dimension, raw_value, token=token, env=env, cache=label_cache
                    ),
                    "category_urn": raw_value,
                    "organic_followers": organic,
                    "paid_followers": paid,
                    "total_followers": total,
                })
            dim_rows.sort(key=lambda r: r["total_followers"], reverse=True)
            rows.extend(dim_rows[:top_n])
    return rows


def _demographic_label(
    dimension: str, raw_value: str, *, token: str, env: LinkedInEnv,
    cache: dict[str, str],
) -> str:
    """Human label for a follower-demographic category value."""
    ref_id = _urn_id(raw_value)
    if dimension == "seniority":
        return _SENIORITY_LABELS.get(ref_id, f"Seniority {ref_id}")
    if dimension == "function":
        return _FUNCTION_LABELS.get(ref_id, f"Function {ref_id}")
    if dimension == "company_size":
        return _humanize_staff_range(raw_value)
    if dimension == "association":
        return _humanize_enum(raw_value)
    if dimension == "industry":
        return _resolve_reference_label(
            "industry", ref_id, access_token=token, env=env, cache=cache
        )
    if dimension == "region":
        kind = "country" if raw_value.startswith("urn:li:country") else "geo"
        return _resolve_reference_label(
            kind, ref_id, access_token=token, env=env, cache=cache
        )
    return _humanize_enum(raw_value)


def _total_followers(
    org_id: str,
    *,
    access_token: str,
    env: LinkedInEnv,
) -> int:
    """Lifetime follower count.

    Prefers ``/networkSizes/{orgUrn}`` (a single point-in-time number), and falls
    back to summing lifetime follower-statistics segments when networkSizes is
    unavailable or returns 0 — some org/app/version combinations return no
    firstDegreeSize even though the follower-statistics endpoint has the data.
    """
    org_urn = quote(_org_urn(org_id), safe="")
    try:
        payload = _linkedin_get_with_versions(
            f"/networkSizes/{org_urn}?edgeType=CompanyFollowedByMember",
            access_token=access_token,
            env=env,
        )
        size = int(payload.get("firstDegreeSize") or 0)
        if size:
            return size
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("networkSizes lookup failed for %s: %s", org_id, exc)

    return _lifetime_followers_from_stats(org_id, access_token=access_token, env=env)


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


# ``totalPageStatistics.views`` key -> our breakdown column. Device rows split
# desktop vs. mobile; section rows attribute views to a page tab. LinkedIn only
# populates the keys that apply to a given page, so absent buckets stay 0.
_PAGE_VIEW_BUCKETS = {
    "allDesktopPageViews": "desktop_page_views",
    "allMobilePageViews": "mobile_page_views",
    "overviewPageViews": "overview_page_views",
    "careersPageViews": "careers_page_views",
    "jobsPageViews": "jobs_page_views",
    "lifeAtPageViews": "life_page_views",
    "productsPageViews": "products_page_views",
    "peoplePageViews": "people_page_views",
}


def _page_view_breakdown(views: dict[str, Any]) -> dict[str, int]:
    """Pull the desktop/mobile + per-section view counts out of ``views``."""
    out = dict.fromkeys(_PAGE_VIEW_BUCKETS.values(), 0)
    for key, col in _PAGE_VIEW_BUCKETS.items():
        section = (views or {}).get(key)
        if isinstance(section, dict):
            out[col] = int(section.get("pageViews") or 0)
    return out


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
        views = total.get("views") or {}
        page_views, unique = _sum_page_views(views)
        out.append(
            {
                "metric_date": day.isoformat(),
                "org_id": org_id_clean,
                "page_views": page_views,
                "unique_visitors": unique,
                **_page_view_breakdown(views),
            }
        )
    out.sort(key=lambda r: r["metric_date"])
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Organization-level engagement over time (daily aggregate share statistics)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_engagement_daily(
    org_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: LinkedInEnv | None = None,
) -> list[dict[str, Any]]:
    """One row per day of organization-wide engagement.

    Queries ``organizationalEntityShareStatistics`` WITH a day-granular
    timeIntervals param, giving a page-level engagement trend (impressions,
    reach, clicks, reactions, comments, shares, engagement rate) independent of
    the per-post lifetime totals. Row shape:
      {metric_date, org_id, impressions, unique_impressions, clicks,
       likes, comments, shares, engagement_rate}
    """
    token, env = _resolve_token(access_token, env)
    org_id_clean = _normalize_org_id(org_id)
    org_urn = quote(_org_urn(org_id_clean), safe="")
    path = (
        f"/organizationalEntityShareStatistics?q=organizationalEntity"
        f"&organizationalEntity={org_urn}&timeIntervals={_time_intervals(start, end)}"
    )
    try:
        payload = _linkedin_get_with_versions(path, access_token=token, env=env)
    except Exception as exc:  # pragma: no cover - network dependent
        _log.warning("engagement statistics failed for %s: %s", org_id_clean, exc)
        return []

    out: list[dict[str, Any]] = []
    for row in payload.get("elements") or []:
        day = _date_from_epoch_ms((row.get("timeRange") or {}).get("start"))
        if not day:
            continue
        parsed = _parse_share_stats(row.get("totalShareStatistics") or {})
        out.append({
            "metric_date": day.isoformat(),
            "org_id": org_id_clean,
            "impressions": parsed["impressions"],
            "unique_impressions": parsed["unique_impressions"],
            "clicks": parsed["clicks"],
            "likes": parsed["likes"],
            "comments": parsed["comments"],
            "shares": parsed["shares"],
            "engagement_rate": parsed["engagement_rate"],
        })
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
