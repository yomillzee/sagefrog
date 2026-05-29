from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import httpx

from dates_util import resolve_date_range
from meta_auth import MetaEnv, load_meta_env

_ACCOUNT_FIELDS = "id,account_id,name,account_status,currency,business_name"
_INSIGHT_FIELDS = (
    "spend,impressions,clicks,actions,action_values,"
    "campaign_id,campaign_name,date_start,date_stop"
)
_ADSET_INSIGHT_FIELDS = (
    "spend,impressions,clicks,actions,action_values,"
    "adset_id,adset_name,campaign_id,campaign_name,date_start,date_stop"
)
_ACCOUNT_STATUS = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
}
_CONVERSION_ACTION_HINTS = (
    "purchase",
    "lead",
    "complete_registration",
    "submit_application",
    "offsite_conversion",
    "omni_purchase",
    "omni_lead",
    "onsite_conversion",
)
_EXCLUDE_ACTION_HINTS = (
    "link_click",
    "landing_page_view",
    "page_engagement",
    "post_engagement",
    "video_view",
    "post_reaction",
    "comment",
    "like",
)


def _normalize_account_id(account_id: str) -> str:
    raw = str(account_id or "").strip()
    if raw.lower().startswith("act_"):
        raw = raw[4:]
    return raw.split(":")[-1]


def _act_id(account_id: str) -> str:
    clean = _normalize_account_id(account_id)
    return f"act_{clean}"


def _graph_base(env: MetaEnv) -> str:
    version = env.api_version.strip()
    if not version.startswith("v"):
        version = f"v{version}"
    return f"https://graph.facebook.com/{version}"


def _client_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def _graph_get(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    env = env or load_meta_env()
    query = dict(params or {})
    query["access_token"] = access_token
    url = path if path.startswith("http") else f"{_graph_base(env)}{path}"
    with httpx.Client(timeout=120.0) as client:
        response = client.get(url, params=query, headers=_client_headers())
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json()
        except Exception:
            pass
        raise RuntimeError(f"Meta Graph API error {response.status_code} on {path}: {detail}")
    return response.json()


def _is_meta_ads_read_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "ads_read" in msg
        or "ads_management" in msg
        or "has not grant" in msg
        or "not grant ads" in msg
    )


def _meta_ads_read_help(account_id: str) -> str:
    act = _act_id(account_id)
    return (
        f"metaVideos requires ads_read on ad account {account_id} ({act}). "
        "metaPerformance only needs read_insights, so campaign metrics can work while "
        "video creatives fail. In Meta Business Manager: (1) confirm the system user token "
        "includes ads_read, (2) assign that system user to this ad account with at least "
        "View performance, or (3) for client accounts, have the client grant your Business "
        f"Manager access to {act}. Then call GET /meta/test-ads-access?account_id={account_id}."
    )


def _graph_get_all(
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    payload = _graph_get(path, access_token=access_token, params=params, env=env)
    rows = list(payload.get("data") or [])
    while payload.get("paging", {}).get("next"):
        payload = _graph_get(payload["paging"]["next"], access_token=access_token, env=env)
        rows.extend(payload.get("data") or [])
    return rows


def _account_status_label(value: Any) -> str:
    try:
        return _ACCOUNT_STATUS.get(int(value), str(value or ""))
    except (TypeError, ValueError):
        return str(value or "")


def _parse_conversions(actions: list[dict[str, Any]] | None) -> float:
    total = 0.0
    for item in actions or []:
        action_type = str(item.get("action_type") or "").lower()
        if any(x in action_type for x in _EXCLUDE_ACTION_HINTS):
            continue
        if any(x in action_type for x in _CONVERSION_ACTION_HINTS):
            total += float(item.get("value") or 0)
    return total


def _parse_conversion_value(action_values: list[dict[str, Any]] | None) -> float:
    total = 0.0
    for item in action_values or []:
        action_type = str(item.get("action_type") or "").lower()
        if any(x in action_type for x in _EXCLUDE_ACTION_HINTS):
            continue
        if any(x in action_type for x in _CONVERSION_ACTION_HINTS):
            total += float(item.get("value") or 0)
    return total


def _parse_insight_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "spend": float(row.get("spend") or 0),
        "clicks": int(float(row.get("clicks") or 0)),
        "impressions": int(float(row.get("impressions") or 0)),
        "conversions": _parse_conversions(row.get("actions")),
        "conversion_value": _parse_conversion_value(row.get("action_values")),
    }


def _normalize_account_row(row: dict[str, Any], *, ownership: str) -> dict[str, Any]:
    account_id = _normalize_account_id(str(row.get("account_id") or row.get("id") or ""))
    return {
        "id": account_id,
        "name": row.get("name") or row.get("business_name") or "",
        "status": _account_status_label(row.get("account_status")),
        "currency": row.get("currency") or "",
        "ownership": ownership,
    }


def list_ad_accounts(
    *,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    env = env or load_meta_env()
    access_token = access_token or env.access_token
    business_id = env.business_id

    merged: dict[str, dict[str, Any]] = {}
    for ownership, edge in (("owned", "owned_ad_accounts"), ("client", "client_ad_accounts")):
        rows = _graph_get_all(
            f"/{business_id}/{edge}",
            access_token=access_token,
            params={"fields": _ACCOUNT_FIELDS, "limit": 500},
            env=env,
        )
        for row in rows:
            normalized = _normalize_account_row(row, ownership=ownership)
            if normalized["id"]:
                merged[normalized["id"]] = normalized

    return sorted(merged.values(), key=lambda item: (item.get("name") or item["id"]).lower())


def test_access_token(env: MetaEnv | None = None) -> dict[str, Any]:
    env = env or load_meta_env()
    try:
        accounts = list_ad_accounts(access_token=env.access_token, env=env)
        return {
            "ok": True,
            "message": "Meta access token is valid for this Business Manager.",
            "account_count": len(accounts),
            "error": None,
        }
    except Exception as exc:
        err = str(exc)
        hint = (
            "Usually: expired token, missing ads_read/read_insights/business_management scopes, "
            "or system user not assigned to ad accounts in Business Manager."
        )
        return {
            "ok": False,
            "message": "Meta access token test failed.",
            "account_count": 0,
            "error": f"{err} — {hint}",
        }


def test_ads_read_access(
    account_id: str,
    *,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """Probe whether this token can read ads (required for metaVideos)."""
    env = env or load_meta_env()
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    ads_ok = False
    ads_error: str | None = None
    try:
        _graph_get(
            f"/{_act_id(account_id_clean)}/ads",
            access_token=access_token,
            params={"fields": "id", "limit": 1},
            env=env,
        )
        ads_ok = True
    except Exception as exc:
        ads_error = str(exc)

    insights_ok = False
    insights_error: str | None = None
    try:
        _graph_get(
            f"/{_act_id(account_id_clean)}/insights",
            access_token=access_token,
            params={
                "fields": "impressions",
                "date_preset": "last_7d",
                "level": "account",
                "limit": 1,
            },
            env=env,
        )
        insights_ok = True
    except Exception as exc:
        insights_error = str(exc)

    ok = ads_ok
    if ok:
        message = f"ads_read access confirmed for account {account_id_clean}."
    elif insights_ok:
        message = (
            f"read_insights works for account {account_id_clean}, but ads_read is missing — "
            "metaVideos will fail until ads are readable."
        )
    else:
        message = f"Neither ads_read nor read_insights works for account {account_id_clean}."

    return {
        "ok": ok,
        "account_id": account_id_clean,
        "ads_read": ads_ok,
        "read_insights": insights_ok,
        "message": message,
        "error": ads_error,
        "insights_error": insights_error,
        "help": None if ads_ok else _meta_ads_read_help(account_id_clean),
    }


def _time_range(start: date, end: date) -> str:
    return json.dumps({"since": start.isoformat(), "until": end.isoformat()})


def fetch_daily_metrics(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> list[dict[str, Any]]:
    env = env or load_meta_env()
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)

    rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "time_increment": 1,
            "level": "account",
            "limit": 500,
        },
        env=env,
    )

    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        metric_day = str(row.get("date_start") or "")[:10]
        if not metric_day:
            continue
        parsed = _parse_insight_row(row)
        by_date[metric_day] = {
            "metric_date": metric_day,
            **parsed,
        }

    out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        out.append(
            by_date.get(key)
            or {
                "metric_date": key,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        )
        cursor += timedelta(days=1)
    return out


def sync_account_to_warehouse(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    import warehouse

    if not warehouse.enabled():
        raise RuntimeError("DATABASE_URL is not set — warehouse storage is disabled.")

    start, end, preset = resolve_date_range(date_range)
    daily_rows = fetch_daily_metrics(
        account_id,
        start=start,
        end=end,
        access_token=access_token,
        env=env,
    )
    account_id_clean = _normalize_account_id(account_id)
    written = warehouse.upsert_metrics_daily_batch("meta", account_id_clean, daily_rows)
    coverage = warehouse.account_date_coverage("meta", account_id_clean)
    return {
        "account_id": account_id_clean,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "days_synced": written,
        "coverage": coverage,
    }


def account_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    env = env or load_meta_env()
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)

    account_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "level": "account",
            "limit": 500,
        },
        env=env,
    )
    campaign_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params={
            "fields": _INSIGHT_FIELDS,
            "time_range": _time_range(start, end),
            "level": "campaign",
            "limit": 500,
        },
        env=env,
    )

    account_parsed = _parse_insight_row(account_rows[0]) if account_rows else {
        "spend": 0.0,
        "clicks": 0,
        "impressions": 0,
        "conversions": 0.0,
        "conversion_value": 0.0,
    }
    totals = {
        **account_parsed,
        "campaign_count": 0,
    }

    campaigns_out: list[dict[str, Any]] = []
    for row in campaign_rows:
        parsed = _parse_insight_row(row)
        campaigns_out.append(
            {
                "id": str(row.get("campaign_id") or ""),
                "entity_level": "campaign",
                "name": row.get("campaign_name") or "",
                "status": "",
                **parsed,
            }
        )
    campaigns_out.sort(key=lambda item: item.get("spend", 0), reverse=True)
    totals["campaign_count"] = len(campaigns_out)

    result = {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "campaigns": campaigns_out,
    }

    try:
        import warehouse

        if warehouse.enabled():
            sync_meta = sync_account_to_warehouse(
                account_id_clean,
                date_range=preset,
                access_token=access_token,
                env=env,
            )
            result["warehouse"] = {
                "stored": True,
                "days_synced": sync_meta["days_synced"],
                "coverage": sync_meta["coverage"],
            }
    except Exception as exc:
        result["warehouse"] = {"stored": False, "error": str(exc)[:500]}

    return result


def adsets_performance(
    account_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    campaign_id: str | None = None,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """
    Ad set-level metrics (sub-campaign). Use for ad set dashboards;
    do not roll into campaign totals by name.
    """
    env = env or load_meta_env()
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    start, end, preset = resolve_date_range(date_range)
    filter_campaign = str(campaign_id or "").strip()

    params: dict[str, Any] = {
        "fields": _ADSET_INSIGHT_FIELDS,
        "time_range": _time_range(start, end),
        "level": "adset",
        "limit": 500,
    }
    if filter_campaign:
        params["filtering"] = json.dumps(
            [{"field": "campaign.id", "operator": "EQUAL", "value": filter_campaign}]
        )

    adset_rows = _graph_get_all(
        f"/{_act_id(account_id_clean)}/insights",
        access_token=access_token,
        params=params,
        env=env,
    )

    adsets_out: list[dict[str, Any]] = []
    for row in adset_rows:
        parsed = _parse_insight_row(row)
        cid = str(row.get("campaign_id") or "")
        if filter_campaign and cid != filter_campaign:
            continue
        adsets_out.append(
            {
                "id": str(row.get("adset_id") or ""),
                "entity_level": "adset",
                "name": row.get("adset_name") or "",
                "status": "",
                "campaign_id": cid,
                "campaign_name": row.get("campaign_name") or "",
                **parsed,
            }
        )
    adsets_out.sort(key=lambda item: item.get("spend", 0), reverse=True)

    totals = {
        "spend": sum(a["spend"] for a in adsets_out),
        "clicks": sum(a["clicks"] for a in adsets_out),
        "impressions": sum(a["impressions"] for a in adsets_out),
        "conversions": sum(a["conversions"] for a in adsets_out),
        "conversion_value": sum(a["conversion_value"] for a in adsets_out),
        "adset_count": len(adsets_out),
    }

    return {
        "account_id": account_id_clean,
        "entity_level": "account",
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "totals": totals,
        "adsets": adsets_out,
    }


_AD_MEDIA_FIELDS = (
    "id,name,status,effective_status,"
    "campaign{id,name},adset{id,name},"
    "creative{"
    "id,name,thumbnail_url,image_url,video_id,object_type,"
    "object_story_spec,asset_feed_spec"
    "}"
)
_VIDEO_DETAIL_FIELDS = "id,source,picture,title,permalink_url"


def _dig_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(key)
    return cur if isinstance(cur, dict) else {}


def _extract_creative_media(creative: dict[str, Any]) -> list[dict[str, Any]]:
    """Return zero or more media entries from a Meta ad creative."""
    if not creative:
        return []

    out: list[dict[str, Any]] = []
    base = {
        "creative_id": str(creative.get("id") or ""),
        "creative_name": str(creative.get("name") or ""),
        "thumbnail_url": str(creative.get("thumbnail_url") or ""),
        "image_url": str(creative.get("image_url") or ""),
    }

    video_id = str(creative.get("video_id") or "")
    if video_id:
        out.append({**base, "media_type": "video", "video_id": video_id})
        return out

    story = creative.get("object_story_spec") if isinstance(creative.get("object_story_spec"), dict) else {}
    video_data = _dig_dict(story, "video_data")
    vid = str(video_data.get("video_id") or "")
    if vid:
        thumb = str(video_data.get("image_url") or base["thumbnail_url"])
        out.append(
            {
                **base,
                "media_type": "video",
                "video_id": vid,
                "thumbnail_url": thumb or base["thumbnail_url"],
            }
        )
        return out

    link_data = _dig_dict(story, "link_data")
    for child in link_data.get("child_attachments") or []:
        if not isinstance(child, dict):
            continue
        child_vid = str(child.get("video_id") or "")
        if child_vid:
            out.append(
                {
                    **base,
                    "media_type": "video",
                    "video_id": child_vid,
                    "creative_name": str(child.get("name") or base["creative_name"]),
                    "thumbnail_url": base["thumbnail_url"],
                }
            )

    asset_feed = creative.get("asset_feed_spec") if isinstance(creative.get("asset_feed_spec"), dict) else {}
    for item in asset_feed.get("videos") or []:
        if not isinstance(item, dict):
            continue
        feed_vid = str(item.get("video_id") or "")
        if feed_vid:
            out.append({**base, "media_type": "video", "video_id": feed_vid})

    if out:
        return out

    if base["image_url"] or base["thumbnail_url"]:
        out.append({**base, "media_type": "image", "video_id": ""})

    return out


def _fetch_video_details(
    video_ids: set[str],
    *,
    access_token: str,
    env: MetaEnv,
) -> dict[str, dict[str, str]]:
    details: dict[str, dict[str, str]] = {}
    for video_id in sorted(video_ids):
        if not video_id:
            continue
        try:
            payload = _graph_get(
                f"/{video_id}",
                access_token=access_token,
                params={"fields": _VIDEO_DETAIL_FIELDS},
                env=env,
            )
            details[video_id] = {
                "video_url": str(payload.get("source") or payload.get("permalink_url") or ""),
                "thumbnail_url": str(payload.get("picture") or ""),
                "video_title": str(payload.get("title") or ""),
            }
        except Exception:
            details[video_id] = {"video_url": "", "thumbnail_url": "", "video_title": ""}
    return details


def list_videos(
    account_id: str,
    *,
    campaign_id: str | None = None,
    videos_only: bool = True,
    access_token: str | None = None,
    env: MetaEnv | None = None,
) -> dict[str, Any]:
    """
    Video/image preview URLs for Meta ads via ad creative metadata.
    """
    env = env or load_meta_env()
    access_token = access_token or env.access_token
    account_id_clean = _normalize_account_id(account_id)
    if not account_id_clean:
        raise ValueError("account_id is required")

    filter_campaign = str(campaign_id or "").strip()
    params: dict[str, Any] = {
        "fields": _AD_MEDIA_FIELDS,
        "limit": 500,
        "effective_status": json.dumps(
            ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "PENDING_REVIEW", "DISAPPROVED"]
        ),
    }
    if filter_campaign:
        params["filtering"] = json.dumps(
            [{"field": "campaign.id", "operator": "EQUAL", "value": filter_campaign}]
        )

    try:
        ad_rows = _graph_get_all(
            f"/{_act_id(account_id_clean)}/ads",
            access_token=access_token,
            params=params,
            env=env,
        )
    except Exception as exc:
        if _is_meta_ads_read_error(exc):
            raise ValueError(_meta_ads_read_help(account_id_clean)) from exc
        raise

    pending_video_ids: set[str] = set()
    draft_rows: list[dict[str, Any]] = []

    for ad in ad_rows:
        creative = ad.get("creative") if isinstance(ad.get("creative"), dict) else {}
        campaign = ad.get("campaign") if isinstance(ad.get("campaign"), dict) else {}
        adset = ad.get("adset") if isinstance(ad.get("adset"), dict) else {}
        cid = str(campaign.get("id") or "")
        if filter_campaign and cid != filter_campaign:
            continue

        media_entries = _extract_creative_media(creative)
        if not media_entries:
            continue

        for entry in media_entries:
            if entry.get("media_type") == "video":
                vid = str(entry.get("video_id") or "")
                if vid:
                    pending_video_ids.add(vid)
            elif videos_only:
                continue

            draft_rows.append(
                {
                    "source": "meta_ad",
                    "ad_id": str(ad.get("id") or ""),
                    "ad_name": str(ad.get("name") or ""),
                    "ad_status": str(ad.get("effective_status") or ad.get("status") or ""),
                    "campaign_id": cid,
                    "campaign_name": str(campaign.get("name") or ""),
                    "adset_id": str(adset.get("id") or ""),
                    "adset_name": str(adset.get("name") or ""),
                    **entry,
                    "video_url": "",
                }
            )

    video_details = _fetch_video_details(pending_video_ids, access_token=access_token, env=env)

    videos_out: list[dict[str, Any]] = []
    for row in draft_rows:
        media_type = str(row.get("media_type") or "")
        if videos_only and media_type != "video":
            continue

        video_id = str(row.get("video_id") or "")
        if video_id:
            detail = video_details.get(video_id) or {}
            row["video_url"] = detail.get("video_url") or row.get("video_url") or ""
            if not row.get("thumbnail_url"):
                row["thumbnail_url"] = detail.get("thumbnail_url") or ""
            if not row.get("creative_name") and detail.get("video_title"):
                row["creative_name"] = detail.get("video_title") or ""

        videos_out.append(
            {
                "source": row["source"],
                "ad_id": row["ad_id"],
                "ad_name": row["ad_name"],
                "ad_status": row["ad_status"],
                "campaign_id": row["campaign_id"],
                "campaign_name": row["campaign_name"],
                "adset_id": row["adset_id"],
                "adset_name": row["adset_name"],
                "creative_id": row.get("creative_id") or "",
                "creative_name": row.get("creative_name") or "",
                "media_type": media_type,
                "video_id": video_id,
                "video_url": str(row.get("video_url") or ""),
                "thumbnail_url": str(row.get("thumbnail_url") or ""),
                "image_url": str(row.get("image_url") or ""),
            }
        )

    videos_out.sort(
        key=lambda row: (
            str(row.get("campaign_name") or ""),
            str(row.get("adset_name") or ""),
            str(row.get("ad_name") or ""),
        )
    )

    return {
        "account_id": account_id_clean,
        "row_count": len(videos_out),
        "videos": videos_out,
    }
