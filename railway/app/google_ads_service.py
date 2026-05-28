from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from google.ads.googleads.client import GoogleAdsClient
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.protobuf.json_format import MessageToDict

from auth import GoogleAdsEnv, load_google_ads_env
from dates_util import resolve_date_range

_YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def build_client(env: GoogleAdsEnv | None = None) -> GoogleAdsClient:
    """
    Build a GoogleAdsClient using OAuth refresh-token flow.

    Expected env vars (Railway variables):
    - GOOGLE_ADS_DEVELOPER_TOKEN
    - GOOGLE_ADS_CLIENT_ID
    - GOOGLE_ADS_CLIENT_SECRET
    - GOOGLE_ADS_REFRESH_TOKEN
    - GOOGLE_ADS_LOGIN_CUSTOMER_ID (optional)
    """
    env = env or load_google_ads_env()
    cfg: dict[str, Any] = {
        "developer_token": env.developer_token,
        "client_id": env.client_id,
        "client_secret": env.client_secret,
        "refresh_token": env.refresh_token,
        "use_proto_plus": True,
    }
    if env.login_customer_id:
        cfg["login_customer_id"] = env.login_customer_id
    return GoogleAdsClient.load_from_dict(cfg)


def test_refresh_token(env: GoogleAdsEnv | None = None) -> dict[str, Any]:
    """
    Exchange refresh token for a short-lived access token (no GAQL / Ads API call).
    Use this to debug invalid_grant vs developer-token / customer issues.
    """
    env = env or load_google_ads_env()
    try:
        creds = Credentials(
            token=None,
            refresh_token=env.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=env.client_id,
            client_secret=env.client_secret,
        )
        creds.refresh(Request())
        expires = creds.expiry.isoformat() if creds.expiry else None
        return {
            "ok": True,
            "message": "OAuth refresh succeeded. Client ID, secret, and refresh token match.",
            "token_expires_at": expires,
            "error": None,
        }
    except Exception as e:
        err = str(e)
        hint = (
            "Usually: refresh token revoked, wrong client secret, token from a different "
            "OAuth client, or missing https://www.googleapis.com/auth/adwords scope."
        )
        if "invalid_grant" in err.lower():
            hint = (
                "invalid_grant: regenerate refresh token with the same GOOGLE_CLIENT_ID / "
                "GOOGLE_CLIENT_SECRET in Railway, using scope adwords."
            )
        return {
            "ok": False,
            "message": "OAuth refresh failed.",
            "token_expires_at": None,
            "error": f"{err} — {hint}",
        }


def row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a GAQL result row to a JSON-serializable dict (snake_case keys)."""
    pb = getattr(row, "_pb", None)
    if pb is None:
        return {"row": str(row)}
    return MessageToDict(pb, preserving_proto_field_name=True)


def search(customer_id: str, query: str, *, client: GoogleAdsClient | None = None) -> list[dict]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    # Newer google-ads Python clients don't accept page_size here.
    resp = ga_service.search(customer_id=customer_id, query=query)
    return [row_to_dict(row) for row in resp]


def _dig(data: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def youtube_urls(video_id: str) -> dict[str, str]:
    vid = video_id.strip()
    return {
        "youtube_video_id": vid,
        "youtube_watch_url": f"https://www.youtube.com/watch?v={vid}",
        "youtube_embed_url": f"https://www.youtube.com/embed/{vid}",
        "youtube_thumbnail_url": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
    }


def _normalize_youtube_id(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if _YOUTUBE_ID_RE.match(text):
        return text
    m = re.search(
        r"(?:youtube\.com/(?:watch\?.*v=|embed/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
        text,
    )
    return m.group(1) if m else None


def _video_row_key(row: dict[str, Any]) -> tuple[str, ...]:
    ad_id = _dig(row, "ad_group_ad", "ad", "id")
    asset_id = _dig(row, "asset", "id")
    video_id = _normalize_youtube_id(_dig(row, "asset", "youtube_video_asset", "youtube_video_id"))
    return (str(row.get("source") or ""), str(ad_id or ""), str(asset_id or ""), str(video_id or ""))


def _flatten_ad_asset_view_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    video_id = _normalize_youtube_id(_dig(raw, "asset", "youtube_video_asset", "youtube_video_id"))
    if not video_id:
        return None
    out: dict[str, Any] = {
        "source": "ad_group_ad_asset_view",
        "campaign_id": _dig(raw, "campaign", "id"),
        "campaign_name": _dig(raw, "campaign", "name"),
        "ad_group_id": _dig(raw, "ad_group", "id"),
        "ad_group_name": _dig(raw, "ad_group", "name"),
        "ad_id": _dig(raw, "ad_group_ad", "ad", "id"),
        "ad_name": _dig(raw, "ad_group_ad", "ad", "name"),
        "ad_status": _dig(raw, "ad_group_ad", "status"),
        "asset_id": _dig(raw, "asset", "id"),
        "asset_name": _dig(raw, "asset", "name"),
        "asset_field_type": _dig(raw, "ad_group_ad_asset_view", "field_type"),
        "youtube_video_title": _dig(raw, "asset", "youtube_video_asset", "youtube_video_title"),
        **youtube_urls(video_id),
    }
    if raw.get("metrics"):
        out["metrics"] = raw["metrics"]
    return out


def _flatten_asset_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    video_id = _normalize_youtube_id(_dig(raw, "asset", "youtube_video_asset", "youtube_video_id"))
    if not video_id:
        return None
    return {
        "source": "asset",
        "campaign_id": None,
        "campaign_name": None,
        "ad_group_id": None,
        "ad_group_name": None,
        "ad_id": None,
        "ad_name": None,
        "ad_status": None,
        "asset_id": _dig(raw, "asset", "id"),
        "asset_name": _dig(raw, "asset", "name"),
        "asset_field_type": None,
        "youtube_video_title": _dig(raw, "asset", "youtube_video_asset", "youtube_video_title"),
        **youtube_urls(video_id),
    }


def _asset_resource_tail(resource_name: str | None) -> str | None:
    if not resource_name:
        return None
    return resource_name.split("/")[-1].strip() or None


def list_youtube_videos(
    customer_id: str,
    *,
    include_account_assets: bool = True,
    include_metrics: bool = False,
    date_range: str = "LAST_30_DAYS",
    client: GoogleAdsClient | None = None,
) -> list[dict[str, Any]]:
    """
    Return YouTube watch/embed URLs for video assets linked to ads (and optionally all
    YOUTUBE_VIDEO assets in the account). Merges ad_group_ad_asset_view (Demand Gen, etc.)
    with classic VIDEO ad asset references.
    """
    allowed_ranges = {"LAST_7_DAYS", "LAST_30_DAYS", "THIS_MONTH", "LAST_MONTH"}
    if date_range not in allowed_ranges:
        raise ValueError(f"Invalid date_range: {date_range}")

    metrics_select = ""
    metrics_where = ""
    if include_metrics:
        metrics_select = """
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
        """
        metrics_where = f" AND segments.date DURING {date_range}"

    ad_asset_query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group_ad.ad.id,
          ad_group_ad.ad.name,
          ad_group_ad.status,
          ad_group_ad_asset_view.field_type,
          asset.id,
          asset.name,
          asset.youtube_video_asset.youtube_video_id,
          asset.youtube_video_asset.youtube_video_title
          {metrics_select}
        FROM ad_group_ad_asset_view
        WHERE ad_group_ad.status != 'REMOVED'
          AND ad_group_ad_asset_view.field_type IN ('YOUTUBE_VIDEO', 'VIDEO')
          AND asset.youtube_video_asset.youtube_video_id != ''
          {metrics_where}
    """

    video_ad_query = """
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group_ad.ad.id,
          ad_group_ad.ad.name,
          ad_group_ad.status,
          ad_group_ad.ad.video_ad.video.asset
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
          AND ad_group_ad.ad.type = 'VIDEO_AD'
    """

    asset_query = """
        SELECT
          asset.id,
          asset.name,
          asset.resource_name,
          asset.youtube_video_asset.youtube_video_id,
          asset.youtube_video_asset.youtube_video_title
        FROM asset
        WHERE asset.type = 'YOUTUBE_VIDEO'
          AND asset.youtube_video_asset.youtube_video_id != ''
    """

    merged: dict[tuple[str, ...], dict[str, Any]] = {}

    def add_row(flat: dict[str, Any] | None) -> None:
        if not flat:
            return
        key = _video_row_key(flat)
        if key in merged:
            prev = merged[key]
            for field, value in flat.items():
                if value is not None and prev.get(field) in (None, ""):
                    prev[field] = value
        else:
            merged[key] = flat

    for raw in search(customer_id, ad_asset_query, client=client):
        add_row(_flatten_ad_asset_view_row(raw))

    video_ad_rows = search(customer_id, video_ad_query, client=client)
    asset_ids: set[str] = set()
    video_ad_meta: dict[str, dict[str, Any]] = {}
    for raw in video_ad_rows:
        asset_resource = _dig(raw, "ad_group_ad", "ad", "video_ad", "video", "asset")
        asset_id = _asset_resource_tail(asset_resource if isinstance(asset_resource, str) else None)
        if not asset_id:
            continue
        asset_ids.add(asset_id)
        video_ad_meta[asset_id] = raw

    if asset_ids:
        id_list = ", ".join(asset_ids)
        asset_lookup_query = f"""
            SELECT
              asset.id,
              asset.name,
              asset.youtube_video_asset.youtube_video_id,
              asset.youtube_video_asset.youtube_video_title
            FROM asset
            WHERE asset.id IN ({id_list})
        """
        for raw in search(customer_id, asset_lookup_query, client=client):
            asset_id = str(_dig(raw, "asset", "id") or "")
            video_id = _normalize_youtube_id(_dig(raw, "asset", "youtube_video_asset", "youtube_video_id"))
            if not video_id:
                continue
            parent = video_ad_meta.get(asset_id, {})
            flat = {
                "source": "video_ad",
                "campaign_id": _dig(parent, "campaign", "id"),
                "campaign_name": _dig(parent, "campaign", "name"),
                "ad_group_id": _dig(parent, "ad_group", "id"),
                "ad_group_name": _dig(parent, "ad_group", "name"),
                "ad_id": _dig(parent, "ad_group_ad", "ad", "id"),
                "ad_name": _dig(parent, "ad_group_ad", "ad", "name"),
                "ad_status": _dig(parent, "ad_group_ad", "status"),
                "asset_id": asset_id,
                "asset_name": _dig(raw, "asset", "name"),
                "asset_field_type": "VIDEO",
                "youtube_video_title": _dig(raw, "asset", "youtube_video_asset", "youtube_video_title"),
                **youtube_urls(video_id),
            }
            add_row(flat)

    if include_account_assets:
        for raw in search(customer_id, asset_query, client=client):
            flat = _flatten_asset_row(raw)
            if flat:
                add_row(flat)

    rows = list(merged.values())
    rows.sort(
        key=lambda r: (
            str(r.get("campaign_name") or ""),
            str(r.get("ad_group_name") or ""),
            str(r.get("ad_name") or ""),
            str(r.get("youtube_video_id") or ""),
        )
    )
    return rows


def list_accessible_customer_ids(*, client: GoogleAdsClient | None = None) -> list[str]:
    client = client or build_client()
    customer_service = client.get_service("CustomerService")
    resp = customer_service.list_accessible_customers()
    ids: list[str] = []
    for resource_name in resp.resource_names:
        # Format is usually "customers/1234567890"
        cid = resource_name.split("/")[-1].strip()
        if cid:
            ids.append(cid)
    return sorted(set(ids))


def get_account_metadata(customer_id: str, *, client: GoogleAdsClient | None = None) -> dict[str, Any]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    query = (
        "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
        "customer.time_zone, customer.status FROM customer LIMIT 1"
    )
    resp = ga_service.search(customer_id=customer_id, query=query)
    first = next(iter(resp), None)
    if first is None:
        return {
            "customer_id": customer_id,
            "resource_name": f"customers/{customer_id}",
            "descriptive_name": None,
            "currency_code": None,
            "time_zone": None,
            "status": "ok",
            "error": None,
        }
    customer = first.customer
    return {
        "customer_id": str(customer.id),
        "resource_name": customer.resource_name,
        "descriptive_name": getattr(customer, "descriptive_name", None),
        "currency_code": getattr(customer, "currency_code", None),
        "time_zone": getattr(customer, "time_zone", None),
        "status": "ok",
        "error": None,
    }


def fetch_daily_metrics(
    customer_id: str,
    *,
    start: date,
    end: date,
    client: GoogleAdsClient | None = None,
) -> list[dict[str, Any]]:
    """Account-level Google Ads metrics per day (sums all campaigns)."""
    customer_id = str(customer_id).replace("-", "").strip()
    start_key = start.isoformat()
    end_key = end.isoformat()
    query = f"""
        SELECT
          segments.date,
          metrics.impressions,
          metrics.clicks,
          metrics.conversions,
          metrics.conversions_value,
          metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{start_key}' AND '{end_key}'
    """
    rows = search(customer_id, query, client=client)
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = str(_dig(row, "segments", "date") or "").strip()
        if not day:
            continue
        if day not in by_day:
            by_day[day] = {
                "metric_date": day,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        rec = by_day[day]
        rec["spend"] += int(_dig(row, "metrics", "cost_micros") or 0) / 1_000_000
        rec["clicks"] += int(_dig(row, "metrics", "clicks") or 0)
        rec["impressions"] += int(_dig(row, "metrics", "impressions") or 0)
        rec["conversions"] += float(_dig(row, "metrics", "conversions") or 0)
        rec["conversion_value"] += float(_dig(row, "metrics", "conversions_value") or 0)

    out: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        out.append(
            by_day.get(key)
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
    customer_id: str,
    *,
    date_range: str = "LAST_30_DAYS",
    client: GoogleAdsClient | None = None,
) -> dict[str, Any]:
    import warehouse

    if not warehouse.enabled():
        raise RuntimeError("DATABASE_URL is not set — warehouse storage is disabled.")

    start, end, preset = resolve_date_range(date_range)
    daily_rows = fetch_daily_metrics(customer_id, start=start, end=end, client=client)
    customer_id_clean = str(customer_id).replace("-", "").strip()
    written = warehouse.upsert_metrics_daily_batch("google", customer_id_clean, daily_rows)
    coverage = warehouse.account_date_coverage("google", customer_id_clean)
    return {
        "account_id": customer_id_clean,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "days_synced": written,
        "coverage": coverage,
    }


def account_summary(customer_id: str, date_range: str = "LAST_30_DAYS", *, client: GoogleAdsClient | None = None) -> dict[str, Any]:
    client = client or build_client()
    ga_service = client.get_service("GoogleAdsService")
    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          metrics.impressions,
          metrics.clicks,
          metrics.conversions,
          metrics.cost_micros
        FROM customer
        WHERE segments.date DURING {date_range}
    """
    resp = ga_service.search(customer_id=customer_id, query=query)
    impressions = 0
    clicks = 0
    conversions = 0.0
    cost_micros = 0
    name: str | None = None
    for row in resp:
        name = row.customer.descriptive_name or name
        impressions += int(getattr(row.metrics, "impressions", 0) or 0)
        clicks += int(getattr(row.metrics, "clicks", 0) or 0)
        conversions += float(getattr(row.metrics, "conversions", 0.0) or 0.0)
        cost_micros += int(getattr(row.metrics, "cost_micros", 0) or 0)

    spend = cost_micros / 1_000_000
    ctr = (clicks / impressions) if impressions else 0.0
    return {
        "customer_id": customer_id,
        "descriptive_name": name,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "cost_micros": cost_micros,
        "spend": spend,
        "ctr": ctr,
    }
