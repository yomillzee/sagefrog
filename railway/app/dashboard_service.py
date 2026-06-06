"""Refresh Penn dashboard data and render HTML from Postgres snapshots."""

from __future__ import annotations

import html
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import dashboard_snapshots
import dashboard_theme
import web_users
import ga4_warehouse_service
import ga4_attribution_service
import ga4_page_service
from ga4_attribution_service import (
    METHODOLOGY,
    PLATFORM_TIER_LABELS,
    build_ga4_campaign_index,
    match_ga4_campaigns_to_ads,
)
import google_ads_service
import linkedin_service
import meta_service
import warehouse
from dates_util import resolve_date_range
from penn_config import PennDashboardConfig, load_penn_config
import client_config
from penn_business_lines import (
    active_business_line_catalog,
    active_platform_catalog,
    build_business_line_campaigns,
    platform_catalog,
)


def configured_dashboard_secret() -> str | None:
    secret = (os.getenv("DASHBOARD_SECRET") or os.getenv("CRON_SECRET") or "").strip()
    return secret or None


def _favicon_head_html() -> str:
    return """
  <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
  <link rel="manifest" href="/static/site.webmanifest">
  <meta name="theme-color" content="#0a2540">"""


def min_refresh_seconds(*, quick: bool = False) -> int:
    """Minimum seconds between manual dashboard refreshes. Default 0 (no cooldown)."""
    env_key = "DASHBOARD_MIN_QUICK_REFRESH_SECONDS" if quick else "DASHBOARD_MIN_REFRESH_SECONDS"
    raw = (os.getenv(env_key) or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _parse_refreshed_at(snapshot: dict[str, Any] | None) -> datetime | None:
    raw = (snapshot or {}).get("refreshed_at")
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def refresh_cooldown_status(
    snapshot: dict[str, Any] | None, *, quick: bool = False
) -> tuple[bool, int]:
    """Return (allowed_now, seconds_remaining)."""
    wait = min_refresh_seconds(quick=quick)
    if wait <= 0:
        return True, 0
    last = _parse_refreshed_at(snapshot)
    if not last:
        return True, 0
    elapsed = (datetime.now(tz=UTC) - last).total_seconds()
    if elapsed >= wait:
        return True, 0
    return False, int(wait - elapsed)


def verify_dashboard_key(key: str | None) -> None:
    from fastapi import HTTPException

    expected = configured_dashboard_secret()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Dashboard access is not configured (set CRON_SECRET or DASHBOARD_SECRET).",
        )
    if not key or key.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing key query parameter.")


def _platform_error(exc: Exception) -> str:
    return str(exc)[:500]


def _normalize_entity_row(row: dict[str, Any]) -> dict[str, Any]:
    entity = str(row.get("entity_level") or "campaign")
    if entity == "adset":
        parent_id = str(row.get("campaign_id") or "")
        parent_name = str(row.get("campaign_name") or "")
    elif entity == "ad":
        parent_id = str(row.get("adset_id") or row.get("ad_group_id") or "")
        parent_name = str(row.get("adset_name") or row.get("ad_group_name") or "")
    elif entity == "ad_group":
        parent_id = str(row.get("campaign_id") or "")
        parent_name = str(row.get("campaign_name") or "")
    elif entity == "creative":
        parent_id = str(row.get("campaign_id") or "")
        parent_name = str(row.get("campaign_name") or "")
    elif entity == "campaign":
        parent_id = str(row.get("campaign_group_id") or "")
        parent_name = str(row.get("campaign_group_name") or "")
    else:
        parent_id = ""
        parent_name = ""
    out: dict[str, Any] = {
        "id": str(
            row.get("id")
            or row.get("campaign_id")
            or row.get("adset_id")
            or row.get("ad_group_id")
            or row.get("ad_id")
            or ""
        ),
        "name": str(
            row.get("name")
            or row.get("campaign_name")
            or row.get("adset_name")
            or row.get("ad_name")
            or "—"
        ),
        "entity_level": entity,
        "spend": float(row.get("spend") or 0),
        "clicks": int(row.get("clicks") or 0),
        "impressions": int(row.get("impressions") or 0),
        "conversions": float(row.get("conversions") or 0),
        "parent_id": parent_id,
        "parent_name": parent_name,
    }
    for key in (
        "campaign_group_id",
        "campaign_group_name",
        "campaign_id",
        "campaign_name",
    ):
        val = row.get(key)
        if val:
            out[key] = str(val)
    for key in ("thumbnail_url", "image_url", "media_type", "creative_name", "video_url", "youtube_embed_url", "youtube_watch_url"):
        val = row.get(key)
        if val:
            out[key] = str(val)
    return out


def _rows_for_display(rows: list[dict[str, Any]], *, min_spend: float = 0.01) -> list[dict[str, Any]]:
    """Hide zero-spend rows so inactive Google campaigns do not clutter the table."""
    visible = [r for r in rows if float(r.get("spend") or 0) >= min_spend]
    return visible if visible else rows


def _account_totals(perf: dict[str, Any]) -> dict[str, Any]:
    totals = dict(perf.get("totals") or {})
    totals.setdefault("spend", 0.0)
    totals.setdefault("clicks", 0)
    totals.setdefault("impressions", 0)
    totals.setdefault("conversions", 0.0)
    return totals


def _aggregated_paid_media(platform_totals: dict[str, Any]) -> dict[str, Any]:
    spend = clicks = impressions = conversions = 0.0
    for key in ("google", "linkedin", "meta"):
        t = platform_totals.get(key) or {}
        spend += float(t.get("spend") or 0)
        clicks += int(t.get("clicks") or 0)
        impressions += int(t.get("impressions") or 0)
        conversions += float(t.get("conversions") or 0)
    return {
        "spend": spend,
        "clicks": int(clicks),
        "impressions": int(impressions),
        "conversions": conversions,
    }


def _totals_from_daily_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Account-level totals summed from metrics_daily rows."""
    return {
        "spend": sum(float(r.get("spend") or 0) for r in rows),
        "clicks": sum(int(r.get("clicks") or 0) for r in rows),
        "impressions": sum(int(r.get("impressions") or 0) for r in rows),
        "conversions": sum(float(r.get("conversions") or 0) for r in rows),
    }


def _penn_sync_warehouses(
    cfg: PennDashboardConfig,
    preset: str,
    payload: dict[str, Any],
) -> str | None:
    """Pull account daily metrics from ad APIs + GA4 BQ into Postgres metrics_daily."""
    ga4_account: str | None = (payload.get("accounts") or {}).get("ga4")

    if cfg.google_customer_id:
        try:
            payload["warehouse_sync"]["google"] = google_ads_service.sync_account_to_warehouse(
                cfg.google_customer_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["google_sync"] = _platform_error(exc)

    if cfg.linkedin_account_id:
        try:
            payload["warehouse_sync"]["linkedin"] = linkedin_service.sync_account_to_warehouse(
                cfg.linkedin_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["linkedin_sync"] = _platform_error(exc)

    if cfg.meta_account_id:
        try:
            payload["warehouse_sync"]["meta"] = meta_service.sync_account_to_warehouse(
                cfg.meta_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["meta_sync"] = _platform_error(exc)

    if cfg.ga4_client_key:
        try:
            payload["warehouse_sync"]["ga4"] = ga4_warehouse_service.sync_to_warehouse(
                date_range=preset,
                client_key=cfg.ga4_client_key,
            )
            ga4_account = payload["warehouse_sync"]["ga4"].get("account_id")
            accounts = dict(payload.get("accounts") or {})
            accounts["ga4"] = ga4_account
            payload["accounts"] = accounts
        except Exception as exc:
            payload["errors"]["ga4_sync"] = _platform_error(exc)

    return ga4_account


def _penn_load_daily_metrics_from_warehouse(
    cfg: PennDashboardConfig,
    *,
    start: date,
    end: date,
    payload: dict[str, Any],
    ga4_account: str | None,
    update_platform_totals: bool = True,
) -> None:
    """Read metrics_daily into snapshot daily_metrics (and optionally platform_totals)."""
    if not warehouse.enabled():
        payload["errors"]["warehouse"] = "DATABASE_URL is not set — warehouse storage is disabled."
        return

    platform_totals: dict[str, Any] = dict(payload.get("platform_totals") or {})
    for source, account_id in (
        ("google", cfg.google_customer_id),
        ("linkedin", cfg.linkedin_account_id),
        ("meta", cfg.meta_account_id),
        ("ga4", ga4_account),
    ):
        if not account_id:
            continue
        try:
            rows = warehouse.query_metrics(
                source=source,
                account_id=str(account_id),
                from_date=start,
                to_date=end,
                limit=5000,
            )
            payload["daily_metrics"][source] = rows
            if not update_platform_totals:
                if source == "ga4" and source not in platform_totals:
                    totals = _totals_from_daily_rows(rows)
                    totals["campaign_count"] = 0
                    platform_totals[source] = totals
                continue
            totals = _totals_from_daily_rows(rows)
            if source == "ga4":
                totals["campaign_count"] = 0
            platform_totals[source] = totals
        except Exception as exc:
            payload["errors"][f"{source}_daily"] = _platform_error(exc)

    if update_platform_totals:
        payload["platform_totals"] = platform_totals
        payload["aggregated_paid_media"] = _aggregated_paid_media(platform_totals)
    elif platform_totals != (payload.get("platform_totals") or {}):
        payload["platform_totals"] = platform_totals


def _load_organic_daily_metrics(
    cfg: PennDashboardConfig,
    *,
    start: date,
    end: date,
    payload: dict[str, Any],
) -> None:
    """Fetch GA4 organic sessions from BigQuery into daily_metrics / platform_totals."""
    if not cfg.ga4_client_key:
        return
    try:
        rows = ga4_warehouse_service.fetch_organic_daily_metrics(
            start=start,
            end=end,
            client_key=cfg.ga4_client_key,
        )
        payload.setdefault("daily_metrics", {})["organic"] = rows
        totals = _totals_from_daily_rows(rows)
        totals["campaign_count"] = 0
        platform_totals = dict(payload.get("platform_totals") or {})
        platform_totals["organic"] = totals
        payload["platform_totals"] = platform_totals
    except Exception as exc:
        payload.setdefault("errors", {})["organic_daily"] = _platform_error(exc)


def _merge_linkedin_creative_media(
    creatives: list[dict[str, Any]], account_id: str
) -> None:
    """Attach thumbnail/image metadata to LinkedIn creative rows when available."""
    if not creatives or not account_id:
        return
    try:
        media_data = linkedin_service.list_video_creatives(account_id, videos_only=False)
        by_id: dict[str, dict[str, Any]] = {}
        for row in media_data.get("videos") or []:
            cid = str(row.get("creative_id") or "")
            if not cid:
                continue
            existing = by_id.get(cid)
            thumb = str(row.get("thumbnail_url") or row.get("image_url") or "")
            if existing and (existing.get("thumbnail_url") or not thumb):
                continue
            by_id[cid] = row
        for creative in creatives:
            media = by_id.get(str(creative.get("id") or ""))
            if not media:
                continue
            thumb = str(media.get("thumbnail_url") or media.get("image_url") or "")
            if thumb:
                creative["thumbnail_url"] = thumb
            image_url = str(media.get("image_url") or "")
            if image_url:
                creative["image_url"] = image_url
            video_url = str(media.get("video_url") or "")
            if video_url:
                creative["video_url"] = video_url
            media_type = str(media.get("media_type") or "")
            if media_type:
                creative["media_type"] = media_type
            creative_name = str(media.get("creative_name") or "")
            if creative_name:
                creative["creative_name"] = creative_name
    except Exception:
        pass


def _sync_meta(trigger: str) -> dict[str, str]:
    return {
        "trigger": trigger,
        "completed_at": datetime.now(tz=UTC).isoformat(),
    }


def refresh_client(
    *,
    client_slug: str,
    date_range: str = "LAST_30_DAYS",
    sync_trigger: str = "manual_full",
) -> dict[str, Any]:
    cfg = client_config.load_client_config(client_slug)
    start, end, preset = resolve_date_range(date_range)

    payload: dict[str, Any] = {
        "client_key": cfg.client_key,
        "label": cfg.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "accounts": {
            "google": cfg.google_customer_id,
            "linkedin": cfg.linkedin_account_id,
            "meta": cfg.meta_account_id,
            "ga4_client_key": cfg.ga4_client_key,
        },
        "warehouse_sync": {},
        "daily_metrics": {},
        "breakdowns": {},
        "platform_totals": {},
        "aggregated_paid_media": {},
        "errors": {},
    }

    breakdowns: dict[str, dict[str, list[dict[str, Any]]]] = {}

    ga4_account = _penn_sync_warehouses(cfg, preset, payload)

    if cfg.google_customer_id:
        try:
            perf = google_ads_service.campaign_performance(cfg.google_customer_id, date_range=preset)
            google_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
            payload["platform_totals"]["google"] = _account_totals(perf)
        except Exception as exc:
            payload["errors"]["google_campaigns"] = _platform_error(exc)
            google_campaigns = []
        google_adgroups: list[dict[str, Any]] = []
        google_ads: list[dict[str, Any]] = []
        try:
            ag_perf = google_ads_service.adgroups_performance(
                cfg.google_customer_id, date_range=preset
            )
            google_adgroups = [
                _normalize_entity_row(g) for g in ag_perf.get("adgroups") or []
            ]
        except Exception as exc:
            payload["errors"]["google_adgroups"] = _platform_error(exc)
        try:
            ads_perf = google_ads_service.ads_performance(
                cfg.google_customer_id, date_range=preset, include_creative=True
            )
            google_ads = [_normalize_entity_row(a) for a in ads_perf.get("ads") or []]
        except Exception as exc:
            payload["errors"]["google_ads"] = _platform_error(exc)
        if google_campaigns or google_adgroups or google_ads:
            breakdowns["google"] = {
                "campaign": google_campaigns,
                "ad_group": google_adgroups,
                "ad": google_ads,
            }

    if cfg.linkedin_account_id:
        li_groups: list[dict[str, Any]] = []
        li_campaigns: list[dict[str, Any]] = []
        li_creatives: list[dict[str, Any]] = []
        li_totals: dict[str, Any] | None = None
        try:
            groups_perf = linkedin_service.campaign_groups_performance(
                cfg.linkedin_account_id, date_range=preset
            )
            li_groups = [_normalize_entity_row(g) for g in groups_perf.get("campaign_groups") or []]
            li_totals = _account_totals(groups_perf)
        except Exception as exc:
            payload["errors"]["linkedin_campaign_groups"] = _platform_error(exc)
        try:
            perf = linkedin_service.account_performance(cfg.linkedin_account_id, date_range=preset)
            li_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
        except Exception as exc:
            payload["errors"]["linkedin_campaigns"] = _platform_error(exc)
        try:
            creatives_perf = linkedin_service.creatives_performance(
                cfg.linkedin_account_id, date_range=preset
            )
            li_creatives_raw = creatives_perf.get("creatives") or []
            _merge_linkedin_creative_media(li_creatives_raw, cfg.linkedin_account_id)
            li_creatives = [_normalize_entity_row(c) for c in li_creatives_raw]
        except Exception as exc:
            payload["errors"]["linkedin_creatives"] = _platform_error(exc)
        if li_groups or li_campaigns or li_creatives:
            breakdowns["linkedin"] = {
                "campaign_group": li_groups,
                "campaign": li_campaigns,
                "creative": li_creatives,
            }
        if li_totals:
            payload["platform_totals"]["linkedin"] = li_totals

    if cfg.meta_account_id:
        meta_campaigns: list[dict[str, Any]] = []
        meta_adsets: list[dict[str, Any]] = []
        meta_ads: list[dict[str, Any]] = []
        meta_totals: dict[str, Any] | None = None
        try:
            perf = meta_service.account_performance(cfg.meta_account_id, date_range=preset)
            meta_campaigns = [_normalize_entity_row(c) for c in perf.get("campaigns") or []]
            meta_totals = _account_totals(perf)
        except Exception as exc:
            payload["errors"]["meta_campaigns"] = _platform_error(exc)
        try:
            adsets_perf = meta_service.adsets_performance(cfg.meta_account_id, date_range=preset)
            meta_adsets = [_normalize_entity_row(a) for a in adsets_perf.get("adsets") or []]
        except Exception as exc:
            payload["errors"]["meta_adsets"] = _platform_error(exc)
        try:
            ads_perf = meta_service.ads_performance(cfg.meta_account_id, date_range=preset)
            meta_ads = [_normalize_entity_row(a) for a in ads_perf.get("ads") or []]
        except Exception as exc:
            payload["errors"]["meta_ads"] = _platform_error(exc)
        if meta_campaigns or meta_adsets or meta_ads:
            breakdowns["meta"] = {
                "campaign": meta_campaigns,
                "adset": meta_adsets,
                "ad": meta_ads,
            }
        if meta_totals:
            payload["platform_totals"]["meta"] = meta_totals

    payload["breakdowns"] = breakdowns
    if cfg.client_key == "penn":
        payload["business_line_campaigns"] = build_business_line_campaigns(
            breakdowns,
            client_slug=cfg.client_key,
        )
    else:
        payload["business_line_campaigns"] = []

    if cfg.ga4_client_key:
        try:
            payload["ga4_attribution"] = ga4_attribution_service.fetch_attribution_for_dashboard(
                date_range=preset,
                client_key=cfg.ga4_client_key,
            )
        except Exception as exc:
            payload["errors"]["ga4_attribution"] = _platform_error(exc)
        try:
            payload["ga4_pages"] = ga4_page_service.fetch_pages_for_dashboard(
                date_range=preset,
                client_key=cfg.ga4_client_key,
            )
        except Exception as exc:
            payload["errors"]["ga4_pages"] = _platform_error(exc)

    _penn_load_daily_metrics_from_warehouse(
        cfg,
        start=start,
        end=end,
        payload=payload,
        ga4_account=ga4_account or payload["accounts"].get("ga4"),
        update_platform_totals=False,
    )
    _load_organic_daily_metrics(cfg, start=start, end=end, payload=payload)
    payload["aggregated_paid_media"] = _aggregated_paid_media(payload["platform_totals"])
    payload["refresh_mode"] = "full"
    payload["sync_meta"] = _sync_meta(sync_trigger)

    prior = dashboard_snapshots.get_snapshot(cfg.client_key)
    if prior and prior.get("insights"):
        payload["insights"] = prior["insights"]

    dashboard_snapshots.save_snapshot(cfg.client_key, payload)
    return payload


def refresh_penn(*, date_range: str = "LAST_30_DAYS", sync_trigger: str = "manual_full") -> dict[str, Any]:
    return refresh_client(client_slug="penn", date_range=date_range, sync_trigger=sync_trigger)


def refresh_client_quick(
    *,
    client_slug: str,
    date_range: str = "LAST_30_DAYS",
    sync_trigger: str = "manual_quick",
) -> dict[str, Any]:
    """
    Warehouse-only refresh: sync metrics_daily from ad APIs + GA4 BQ, update charts and summary cards.
    Keeps campaign/ad breakdowns and GA4 attribution from the last full refresh.
    """
    cfg = client_config.load_client_config(client_slug)
    start, end, preset = resolve_date_range(date_range)
    existing = dashboard_snapshots.get_snapshot(cfg.client_key) or {}
    breakdowns = existing.get("breakdowns") or {}

    payload: dict[str, Any] = {
        "client_key": cfg.client_key,
        "label": existing.get("label") or cfg.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "accounts": existing.get("accounts")
        or {
            "google": cfg.google_customer_id,
            "linkedin": cfg.linkedin_account_id,
            "meta": cfg.meta_account_id,
            "ga4_client_key": cfg.ga4_client_key,
        },
        "warehouse_sync": {},
        "daily_metrics": {},
        "breakdowns": breakdowns,
        "platform_totals": {},
        "aggregated_paid_media": {},
        "errors": {},
        "ga4_attribution": existing.get("ga4_attribution"),
        "ga4_pages": existing.get("ga4_pages"),
        "business_line_campaigns": (
            build_business_line_campaigns(breakdowns, client_slug=cfg.client_key)
            if cfg.client_key == "penn"
            else []
        ),
        "refresh_mode": "warehouse",
    }
    if existing.get("insights"):
        payload["insights"] = existing["insights"]

    ga4_account = _penn_sync_warehouses(cfg, preset, payload)
    _penn_load_daily_metrics_from_warehouse(
        cfg,
        start=start,
        end=end,
        payload=payload,
        ga4_account=ga4_account or payload["accounts"].get("ga4"),
        update_platform_totals=True,
    )
    _load_organic_daily_metrics(cfg, start=start, end=end, payload=payload)
    payload["sync_meta"] = _sync_meta(sync_trigger)

    dashboard_snapshots.save_snapshot(cfg.client_key, payload)
    return payload


def refresh_penn_quick(*, date_range: str = "LAST_30_DAYS", sync_trigger: str = "manual_quick") -> dict[str, Any]:
    return refresh_client_quick(client_slug="penn", date_range=date_range, sync_trigger=sync_trigger)


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def _fmt_pct(num: float, den: float) -> str:
    if not den:
        return "—"
    return f"{100.0 * num / den:.2f}%"


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


_PLATFORM_FAVICONS: dict[str, str] = {
    "google": "https://www.google.com/s2/favicons?domain=google.com&sz=32",
    "linkedin": "https://www.google.com/s2/favicons?domain=linkedin.com&sz=32",
    "meta": "https://www.google.com/s2/favicons?domain=facebook.com&sz=32",
}


def _platform_title_html(platform: str, label: str) -> str:
    icon = _PLATFORM_FAVICONS.get(platform, "")
    icon_html = (
        f'<img class="platform-favicon" src="{icon}" alt="" width="20" height="20" loading="lazy" />'
        if icon
        else ""
    )
    return (
        f'<span class="platform-title">'
        f"{icon_html}"
        f'<span>{_esc(label)}</span></span>'
    )


def _json_for_html_script(data: Any) -> str:
    """Embed JSON in HTML without breaking out of a script tag."""
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _entity_level_label(level: str, *, platform: str | None = None) -> str:
    """Human label for an entity row; LinkedIn uses Campaign Manager UI names (2025+)."""
    if platform == "linkedin":
        linkedin_labels = {
            "campaign_group": "campaign group",
            "campaign": "ad set",
            "creative": "ad",
        }
        if level in linkedin_labels:
            return linkedin_labels[level]
    labels = {
        "campaign": "campaign",
        "campaign_group": "campaign group",
        "ad_group": "ad group",
        "adset": "ad set",
        "ad": "ad",
        "creative": "creative",
    }
    return labels.get(level, level.replace("_", " "))


_GA4_TABLE_HEADERS = """
              <th class="ga4-col" title="GA4 attributed sessions">Sess.</th>
              <th class="ga4-col" title="GA4 engagement rate">Eng.</th>
              <th class="ga4-col" title="GA4 key events">Events</th>"""


def _ga4_row_cells(
    campaign_id: str,
    ga4_by_campaign: dict[str, dict[str, Any]] | None,
    *,
    is_campaign_row: bool,
) -> str:
    if not ga4_by_campaign:
        return ""
    if not is_campaign_row:
        return (
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
        )
    metrics = ga4_by_campaign.get(str(campaign_id or "")) or {}
    sessions = int(metrics.get("sessions") or 0)
    if not sessions:
        return (
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
            '<td class="num ga4-col muted">—</td>'
        )
    engaged = int(metrics.get("engaged_sessions") or 0)
    key_events = int(metrics.get("key_events") or 0)
    return (
        f'<td class="num ga4-col">{_fmt_int(sessions)}</td>'
        f'<td class="num ga4-col">{_fmt_pct(engaged, sessions)}</td>'
        f'<td class="num ga4-col">{_fmt_int(key_events)}</td>'
    )


def _drillable_table(
    platform: str,
    title_html: str,
    rows: list[dict[str, Any]],
    *,
    entity_level: str,
    site_footer: str = "",
    ga4_by_campaign: dict[str, dict[str, Any]] | None = None,
) -> str:
    rows = _rows_for_display(rows)
    level_badge = _entity_level_label(entity_level, platform=platform)
    expandable = (
        (platform == "google" and entity_level in ("campaign", "ad_group"))
        or (
            platform in ("linkedin", "meta")
            and entity_level in ("campaign_group", "campaign")
        )
    )
    if not rows:
        return f"""
        <section class="panel platform-panel platform-{platform}" data-platform="{platform}">
          <div class="panel-head">
            <h2>{title_html}</h2>
            <span class="badge">{level_badge} · 0 rows</span>
          </div>
          <p class="muted">No {_esc(level_badge)} data for this period.</p>
          {site_footer}
        </section>
        """
    rows_html = []
    for row in sorted(rows, key=lambda c: c.get("spend", 0), reverse=True):
        spend = float(row.get("spend") or 0)
        clicks = int(row.get("clicks") or 0)
        impressions = int(row.get("impressions") or 0)
        conv = float(row.get("conversions") or 0)
        cpc = _fmt_money(spend / clicks) if clicks else "—"
        expand_class = " tree-expandable" if expandable else ""
        chevron = (
            '<span class="tree-chevron" aria-hidden="true">▸</span>'
            if expandable
            else '<span class="tree-chevron leaf"></span>'
        )
        row_attrs = (
            f'data-platform="{_esc(platform)}" '
            f'data-level="{_esc(entity_level)}" '
            f'data-id="{_esc(row.get("id"))}" '
            f'data-depth="0"'
        )
        if expandable:
            row_attrs += (
                f' tabindex="0" role="button" '
                f'aria-expanded="false" '
                f'aria-label="Expand {_esc(row.get("name"))}"'
            )
        ga4_cells = _ga4_row_cells(
            str(row.get("id") or ""),
            ga4_by_campaign,
            is_campaign_row=(entity_level == "campaign"),
        )
        rows_html.append(
            f"""<tr class="tree-row tree-depth-0{expand_class}" {row_attrs}>
              <td class="chevron-col">{chevron}</td>
              <td class="name">{_esc(row.get("name"))}</td>
              <td class="num">{_fmt_money(spend)}</td>
              <td class="num">{_fmt_int(clicks)}</td>
              <td class="num">{_fmt_int(impressions)}</td>
              <td class="num">{_fmt_pct(clicks, impressions or 1)}</td>
              <td class="num">{_fmt_int(conv)}</td>
              <td class="num">{cpc}</td>
              {ga4_cells}
            </tr>"""
        )
    chevron_th = '<th class="chevron-col"></th>'
    ga4_headers = _GA4_TABLE_HEADERS if ga4_by_campaign is not None else ""
    return f"""
    <section class="panel platform-panel platform-{platform}" data-platform="{platform}">
      <div class="panel-head">
        <h2>{title_html}</h2>
        <span class="badge">{level_badge} · {len(rows)} rows</span>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              {chevron_th}
              <th>Name</th>
              <th>Spend</th>
              <th>Clicks</th>
              <th>Impressions</th>
              <th>CTR</th>
              <th>Conv.</th>
              <th>CPC</th>
              {ga4_headers}
            </tr>
          </thead>
          <tbody class="tree-table" data-platform="{_esc(platform)}">
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
      {site_footer}
    </section>
    """


def _entity_table(
    title: str,
    rows: list[dict[str, Any]],
    *,
    entity_level: str,
    parent_header: str | None = None,
    note: str = "",
) -> str:
    """Non-drillable table (Google Ads)."""
    rows = _rows_for_display(rows)
    if not rows:
        return f"""
        <section class="panel platform-panel platform-google">
          <div class="panel-head">
            <h2>{_esc(title)}</h2>
          </div>
          <p class="muted">No {_esc(_entity_level_label(entity_level))} data for this period.</p>
        </section>
        """
    level_badge = _entity_level_label(entity_level)
    rows_html = []
    for row in sorted(rows, key=lambda c: c.get("spend", 0), reverse=True):
        spend = float(row.get("spend") or 0)
        clicks = int(row.get("clicks") or 0)
        cpc = _fmt_money(spend / clicks) if clicks else "—"
        rows_html.append(
            f"""<tr>
              <td class="name">{_esc(row.get("name"))}</td>
              <td class="num">{_fmt_money(spend)}</td>
              <td class="num">{_fmt_int(clicks)}</td>
              <td class="num">{_fmt_int(row.get("impressions") or 0)}</td>
              <td class="num">{_fmt_pct(clicks, float(row.get("impressions") or 1))}</td>
              <td class="num">{_fmt_int(row.get("conversions") or 0)}</td>
              <td class="num">{cpc}</td>
            </tr>"""
        )
    note_html = f'<p class="table-note">{_esc(note)}</p>' if note else ""
    return f"""
    <section class="panel platform-panel platform-google">
      <div class="panel-head">
        <h2>{_esc(title)} <span class="badge">{level_badge} · {len(rows)} rows</span></h2>
      </div>
      {note_html}
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Spend</th>
              <th>Clicks</th>
              <th>Impressions</th>
              <th>CTR</th>
              <th>Conv.</th>
              <th>CPC</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
    </section>
    """


def _summary_card(
    label: str,
    totals: dict[str, Any] | None,
    *,
    platform: str | None = None,
) -> str:
    label_html = _platform_title_html(platform, label) if platform else _esc(label)
    if not totals:
        return f"""
        <div class="card card-empty">
          <div class="card-label">{label_html}</div>
          <div class="card-value muted">No data</div>
        </div>
        """
    spend = float(totals.get("spend") or 0)
    clicks = int(totals.get("clicks") or 0)
    impressions = int(totals.get("impressions") or 0)
    conversions = float(totals.get("conversions") or 0)
    if platform == "organic":
        return f"""
    <div class="card" data-platform="organic">
      <div class="card-label">{label_html}</div>
      <div class="card-value">{_fmt_int(clicks)}</div>
      <div class="card-stats">
        <span>{_fmt_int(impressions)} page views</span>
        <span>{_fmt_int(conversions)} key events</span>
        <span class="muted">GA4 organic search</span>
      </div>
    </div>
    """
    return f"""
    <div class="card" data-platform="{_esc(platform or '')}">
      <div class="card-label">{label_html}</div>
      <div class="card-value">{_fmt_money(spend)}</div>
      <div class="card-stats">
        <span>{_fmt_int(clicks)} clicks</span>
        <span>{_fmt_int(impressions)} impr.</span>
        <span>{_fmt_int(conversions)} conv.</span>
      </div>
    </div>
    """


def _dashboard_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
    tab: str | None = None,
) -> str:
    base = f"/dashboard/{client_slug}"
    if tab:
        base = f"{base}?tab={quote(tab, safe='')}"
    if use_session:
        return base
    if access_key:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}key={quote(access_key, safe='')}"
    return base


def _settings_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/settings"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def _insights_upload_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/insights-upload"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def _refresh_action_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/refresh"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def _refresh_toolbar(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool = False,
    snapshot: dict[str, Any] | None,
    flash_message: str | None = None,
) -> str:
    refresh_url = _refresh_action_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    if not refresh_url:
        return ""
    quick_allowed, quick_remaining = refresh_cooldown_status(snapshot, quick=True)
    full_allowed, full_remaining = refresh_cooldown_status(snapshot, quick=False)
    notice = ""
    if flash_message:
        notice = f'<div class="notice">{_esc(flash_message)}</div>'
    elif min_refresh_seconds(quick=False) > 0 and not quick_allowed and not full_allowed:
        mins = max(1, (min(quick_remaining, full_remaining) + 59) // 60)
        notice = f'<div class="notice muted">Refresh available in ~{mins} min.</div>'
    if quick_allowed:
        quick_btn = (
            f'<form method="post" action="{refresh_url}" class="refresh-form">'
            f'<input type="hidden" name="quick" value="1">'
            f'<button type="submit" class="refresh-btn">Quick refresh</button></form>'
        )
    else:
        quick_btn = '<button type="button" class="refresh-btn" disabled>Quick refresh</button>'
    if full_allowed:
        full_btn = (
            f'<form method="post" action="{refresh_url}" class="refresh-form">'
            f'<button type="submit" class="refresh-btn refresh-btn--secondary">'
            f"Full refresh</button></form>"
        )
    else:
        full_btn = (
            '<button type="button" class="refresh-btn refresh-btn--secondary" disabled>'
            "Full refresh</button>"
        )
    buttons = f'<div class="refresh-actions">{quick_btn}{full_btn}</div>'
    return f'<div class="refresh-bar">{notice}{buttons}</div>'


def _session_account_html(*, email: str | None, is_admin: bool) -> str:
    """Signed-in user: email, admin link, sign out (sidebar footer)."""
    if not email:
        return ""
    admin_link = (
        '<a class="account-link" href="/admin">Admin</a><span class="account-sep">·</span>'
        if is_admin
        else ""
    )
    return f"""
    <div class="account-nav">
      <span class="account-email" title="{_esc(email)}">{_esc(email)}</span>
      <div class="account-actions">
        {admin_link}
        <form method="post" action="/logout" class="account-logout-form">
          <button type="submit" class="account-link">Sign out</button>
        </form>
      </div>
    </div>
    """


def can_edit_penn_insights(*, session_is_admin: bool, access_key: str | None) -> bool:
    """Admins (session) or legacy shared-key mode may edit insights text."""
    if session_is_admin:
        return True
    if not web_users.enabled() and access_key:
        return True
    return False


def _insights_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    raw = (snapshot or {}).get("insights")
    if isinstance(raw, str):
        return {"body": raw.strip(), "updated_at": None, "updated_by": None}
    if isinstance(raw, dict):
        return {
            "body": str(raw.get("body") or "").strip(),
            "updated_at": raw.get("updated_at"),
            "updated_by": raw.get("updated_by"),
        }
    return {"body": "", "updated_at": None, "updated_by": None}


def _format_insights_body_html(body: str) -> str:
    """Turn pasted GPT bullets into compact HTML."""
    text = str(body or "").strip()
    if not text:
        return ""
    blocks: list[str] = []
    list_items: list[str] = []
    bullet_re = re.compile(r"^[\s]*(?:[-*•]|\d+[.)])\s+")

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            items = "".join(f"<li>{_esc(line)}</li>" for line in list_items)
            blocks.append(f'<ul class="insights-list">{items}</ul>')
            list_items = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        if bullet_re.match(stripped):
            list_items.append(bullet_re.sub("", stripped, count=1).strip())
        else:
            flush_list()
            blocks.append(f'<p class="insights-para">{_esc(stripped)}</p>')
    flush_list()
    return "\n".join(blocks)


def save_penn_insights(
    body: str,
    *,
    updated_by: str | None = None,
    client_key: str = "penn",
) -> dict[str, Any]:
    """Persist insights on the dashboard snapshot without bumping data refresh time."""
    key = (client_key or "penn").strip().lower()
    cfg = client_config.load_client_config(key)
    existing = dashboard_snapshots.get_snapshot(key) or {
        "client_key": key,
        "label": cfg.label,
    }
    insights = {
        "body": str(body or "").strip()[:8000],
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "updated_by": (updated_by or "").strip() or None,
    }
    existing["insights"] = insights
    dashboard_snapshots.save_snapshot(key, existing, touch_refreshed_at=False)
    return insights


def _insights_action_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/insights"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def _insights_editor_html(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
    snapshot: dict[str, Any] | None,
) -> str:
    action = _insights_action_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    if not action:
        return ""
    insights = _insights_from_snapshot(snapshot)
    body = insights.get("body") or ""
    updated = insights.get("updated_at")
    meta = ""
    if updated:
        meta = f'<p class="insights-editor-meta muted">Last saved {_esc(str(updated)[:19])} UTC</p>'
    return f"""
    <section class="insights-editor">
      <h3 class="insights-editor-title">Insights</h3>
      <p class="muted insights-editor-hint">Paste short, actionable notes from your Custom GPT (bullets work well).</p>
      {meta}
      <form method="post" action="{action}" class="insights-editor-form">
        <textarea name="body" class="insights-textarea" rows="7" maxlength="8000"
          placeholder="• Shift budget to …&#10;• Pause ad set …&#10;• Test landing page …">{_esc(body)}</textarea>
        <button type="submit" class="refresh-btn insights-save-btn">Save insights</button>
      </form>
    </section>"""


def _insights_card_html(snapshot: dict[str, Any] | None) -> str:
    insights = _insights_from_snapshot(snapshot)
    body = insights.get("body") or ""
    if body:
        content = _format_insights_body_html(body)
        updated = insights.get("updated_at")
        foot = ""
        if updated:
            foot = (
                f'<p class="insights-foot muted">Updated {_esc(str(updated)[:10])}</p>'
            )
        inner = f'<div class="insights-body">{content}</div>{foot}'
    else:
        inner = (
            '<p class="insights-empty muted">Add insights in Settings — paste weekly notes '
            "from your Custom GPT.</p>"
        )
    return f"""
    <section class="panel insights-panel" aria-label="Insights">
      <div class="insights-head">
        <h2 class="insights-title">Insights</h2>
        <button type="button" class="info-tip info-tip--light"
          data-tip="Short, actionable takeaways. AI-generated summaries coming later; edit in Settings for now."
          aria-label="About insights">i</button>
      </div>
      {inner}
    </section>"""


def _ga4_paid_key_events(ga4_attr: dict[str, Any] | None) -> int:
    """Sum GA4 key events attributed to paid platforms (for verified CPA)."""
    if not ga4_attr:
        return 0
    platforms = ga4_attr.get("platforms")
    if platforms:
        total = 0
        for platform in ("google", "linkedin", "meta"):
            platform_totals = (platforms.get(platform) or {}).get("totals") or {}
            total += int(platform_totals.get("key_events") or 0)
        return total
    return int((ga4_attr.get("totals") or {}).get("key_events") or 0)


def _paid_ad_overview_metrics(
    aggregated: dict[str, Any],
    ga4_attr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spend = float(aggregated.get("spend") or 0)
    clicks = int(aggregated.get("clicks") or 0)
    impressions = int(aggregated.get("impressions") or 0)
    conversions = float(aggregated.get("conversions") or 0)
    ga4_key_events = _ga4_paid_key_events(ga4_attr)
    return {
        "spend": spend,
        "clicks": clicks,
        "impressions": impressions,
        "conversions": conversions,
        "ctr": (clicks / impressions) if impressions else None,
        "cpc": (spend / clicks) if clicks else None,
        "reported_cpa": (spend / conversions) if conversions else None,
        "verified_cpa": (spend / ga4_key_events) if ga4_key_events else None,
        "ga4_key_events": ga4_key_events,
    }


def _fmt_cpa(spend: float, count: float | int) -> str:
    if not count:
        return "—"
    return _fmt_money(spend / float(count))


def _paid_ad_overview_html(
    aggregated: dict[str, Any],
    ga4_attr: dict[str, Any] | None = None,
) -> str:
    if not aggregated:
        return ""
    m = _paid_ad_overview_metrics(aggregated, ga4_attr)
    ga4_events = int(m["ga4_key_events"] or 0)
    verified_sub = (
        f"Using {_fmt_int(ga4_events)} paid GA4 TY event{'s' if ga4_events != 1 else ''}"
        if ga4_events
        else "No paid GA4 TY events in range"
    )
    return f"""
    <section class="paid-ad-overview" aria-label="Paid Ad Overview">
      <div class="paid-ad-overview-heading">
        <span class="paid-ad-overview-pill">Paid Ad Overview</span>
      </div>
      <div class="paid-ad-metrics-grid">
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Spend</div>
          <div class="ga4-metric-value" id="heroSpend">{_fmt_money(m["spend"])}</div>
          <div class="ga4-metric-sub">Platform spend</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Impressions</div>
          <div class="ga4-metric-value" id="heroImpressions">{_fmt_int(m["impressions"])}</div>
          <div class="ga4-metric-sub">Paid delivery</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Clicks</div>
          <div class="ga4-metric-value" id="heroClicks">{_fmt_int(m["clicks"])}</div>
          <div class="ga4-metric-sub">Platform clicks</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">CTR</div>
          <div class="ga4-metric-value" id="heroCtr">{_fmt_pct(m["clicks"], m["impressions"]) if m["impressions"] else "—"}</div>
          <div class="ga4-metric-sub">Clicks ÷ impressions</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">CPC</div>
          <div class="ga4-metric-value" id="heroCpc">{_fmt_cpa(m["spend"], m["clicks"]) if m["clicks"] else "—"}</div>
          <div class="ga4-metric-sub">Spend ÷ clicks</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Reported conversions</div>
          <div class="ga4-metric-value" id="heroConversions">{_fmt_int(m["conversions"])}</div>
          <div class="ga4-metric-sub">Platform-defined</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Reported CPA</div>
          <div class="ga4-metric-value" id="heroReportedCpa">{_fmt_cpa(m["spend"], m["conversions"]) if m["conversions"] else "—"}</div>
          <div class="ga4-metric-sub">Platform basis</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Verified CPA</div>
          <div class="ga4-metric-value" id="heroVerifiedCpa">{_fmt_cpa(m["spend"], ga4_events) if ga4_events else "—"}</div>
          <div class="ga4-metric-sub" id="heroVerifiedCpaSub">{verified_sub}</div>
        </div>
      </div>
    </section>"""


def _overview_hero_row_html(
    client_insights_html: str = "",
) -> str:
    if not client_insights_html:
        return ""
    return f'<div class="overview-hero-row overview-hero-row--insights">{client_insights_html}</div>'


def _fmt_file_size(num_bytes: int) -> str:
    size = int(num_bytes or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{round(size / (1024 * 1024), 1)} MB"


def _insight_documents_action_url(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/insight-documents"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def _insight_document_download_url(
    *,
    client_slug: str,
    doc_id: int,
    access_key: str | None,
    use_session: bool,
) -> str:
    base = f"/dashboard/{client_slug}/insight-documents/{int(doc_id)}"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def _insight_document_delete_url(
    *,
    client_slug: str,
    doc_id: int,
    access_key: str | None,
    use_session: bool,
) -> str:
    base = f"/dashboard/{client_slug}/insight-documents/{int(doc_id)}/delete"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def _client_insights_doc_rows_html(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
    can_manage: bool,
) -> tuple[str, int]:
    """Build document list rows; returns (html, count)."""
    import client_insight_documents as docs

    rows = docs.list_documents(client_slug)
    if not rows:
        return "", 0

    body_rows = []
    for row in rows:
        download_url = _insight_document_download_url(
            client_slug=client_slug,
            doc_id=row.id,
            access_key=access_key,
            use_session=use_session,
        )
        delete_cell = ""
        if can_manage:
            delete_url = _insight_document_delete_url(
                client_slug=client_slug,
                doc_id=row.id,
                access_key=access_key,
                use_session=use_session,
            )
            delete_cell = f"""
                <form method="post" action="{delete_url}" class="client-insights-delete-form"
                  onsubmit="return confirm('Delete this document?');">
                  <button type="submit" class="client-insights-delete-btn">Delete</button>
                </form>"""
        body_rows.append(
            f"""
                <tr>
                  <td class="client-insights-file">
                    <div class="client-insights-file-title">{_esc(row.title)}</div>
                    <div class="client-insights-file-meta muted">Word document · {_esc(_fmt_file_size(row.file_size))}</div>
                  </td>
                  <td class="client-insights-period">{_esc(docs.period_label(row.period_year, row.period_month))}</td>
                  <td class="client-insights-actions">
                    <a class="client-insights-download-btn" href="{download_url}">Download DOCX</a>
                    {delete_cell}
                  </td>
                </tr>"""
        )
    table_html = f"""
        <div class="client-insights-table-wrap">
          <table class="client-insights-table">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Period</th>
                <th scope="col">Download</th>
              </tr>
            </thead>
            <tbody>
              {''.join(body_rows)}
            </tbody>
          </table>
        </div>"""
    return table_html, len(rows)


def _client_insights_list_html(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
) -> str:
    """Overview: running list of uploaded insight documents (download only)."""
    import client_insight_documents as docs

    rows = docs.list_documents(client_slug)
    if rows:
        cards = []
        for row in rows:
            download_url = _insight_document_download_url(
                client_slug=client_slug,
                doc_id=row.id,
                access_key=access_key,
                use_session=use_session,
            )
            period = docs.period_label(row.period_year, row.period_month)
            cards.append(
                f"""
        <a class="client-insights-doc-card" href="{download_url}">
          <div class="client-insights-doc-period">{_esc(period)}</div>
          <div class="client-insights-doc-title">{_esc(row.title)}</div>
          <div class="client-insights-doc-meta">Word · {_esc(_fmt_file_size(row.file_size))}</div>
        </a>"""
            )
        list_html = f'<div class="client-insights-doc-grid">{"".join(cards)}</div>'
    else:
        list_html = (
            '<p class="client-insights-empty muted">'
            "Monthly insight documents will appear here when uploaded."
            "</p>"
        )

    count_badge = ""
    if rows:
        count_badge = f'<span class="client-insights-count">{len(rows)}</span>'

    return f"""
    <section class="client-insights-section" aria-label="Client Insights">
      <div class="paid-ad-overview-heading client-insights-heading">
        <span class="paid-ad-overview-pill client-insights-pill">Client Insights{count_badge}</span>
      </div>
      {list_html}
    </section>"""


def _client_insights_upload_page_html(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
    can_upload: bool,
) -> str:
    """Admin upload page: form + full document table with delete."""
    import client_insight_documents as docs

    upload_html = ""
    upload_action = _insight_documents_action_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    if can_upload and upload_action and docs.enabled():
        upload_html = f"""
        <form method="post" action="{upload_action}" enctype="multipart/form-data" class="client-insights-upload">
          <div class="client-insights-upload-grid">
            <div>
              <label for="insightDocTitle">Title</label>
              <input id="insightDocTitle" name="title" type="text" maxlength="200"
                placeholder="May 2026 Paid Digital Reporting">
            </div>
            <div>
              <label for="insightDocPeriod">Period</label>
              <input id="insightDocPeriod" name="period" type="month" required>
            </div>
            <div>
              <label for="insightDocFile">Word document</label>
              <input id="insightDocFile" name="file" type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required>
            </div>
          </div>
          <button type="submit" class="client-insights-upload-btn">Upload DOCX</button>
        </form>"""
    elif not docs.enabled():
        upload_html = (
            '<p class="client-insights-empty muted">'
            "DATABASE_URL is required to store insight documents."
            "</p>"
        )

    table_html, _ = _client_insights_doc_rows_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
        can_manage=can_upload,
    )
    if not table_html:
        table_html = (
            '<p class="client-insights-empty muted">No insight documents uploaded yet.</p>'
        )

    return f"""
    <section class="client-insights-panel client-insights-panel--upload" aria-label="Insights upload">
      <div class="client-insights-head">
        <div>
          <h2 class="client-insights-title">Upload insight documents</h2>
          <p class="client-insights-subtitle muted">
            Upload monthly Word documents with client-ready insights. Clients see downloads on the Overview.
          </p>
        </div>
      </div>
      {upload_html}
      {table_html}
    </section>"""


def render_insights_upload_page(
    *,
    client_slug: str,
    label: str,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
) -> str:
    slug = (client_slug or "penn").strip().lower()
    can_upload = can_edit_penn_insights(
        session_is_admin=session_is_admin,
        access_key=access_key,
    )
    flash_html = ""
    if flash_message:
        flash_html = f'<div class="dash-flash">{_esc(flash_message)}</div>'
    body = flash_html + _client_insights_upload_page_html(
        client_slug=slug,
        access_key=access_key,
        use_session=use_session,
        can_upload=can_upload,
    )
    return render_client_shell_page(
        client_slug=slug,
        label=label,
        active_nav="insights-upload",
        page_title="Insights Upload",
        page_subtitle=f"{label} · Manage insight documents",
        content_html=body,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        show_insights_upload=can_upload,
        extra_css="""
    .dash-flash { background: var(--ok-bg); border: 1px solid #b8dfc8; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; font-size: 0.9rem; color: var(--ok); }
    .dash-flash--error { background: var(--err-bg); border-color: #f5c2c0; color: var(--err); }
    .client-insights-panel { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 0; overflow: hidden; box-shadow: var(--shadow-sm); }
    .client-insights-head { padding: 22px 24px 0; }
    .client-insights-title { margin: 0; font-size: 1.15rem; font-weight: 700; color: var(--navy); }
    .client-insights-subtitle { margin: 6px 0 0; font-size: 0.9rem; line-height: 1.45; }
    .client-insights-upload { margin: 18px 24px 0; padding: 18px; border: 1px dashed var(--border); border-radius: 12px; background: var(--surface); }
    .client-insights-upload-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px; }
    @media (max-width: 900px) { .client-insights-upload-grid { grid-template-columns: 1fr; } }
    .client-insights-upload label { display: block; font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 6px; }
    .client-insights-upload input { width: 100%; padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; font: inherit; background: #fff; }
    .client-insights-upload-btn { appearance: none; border: 0; border-radius: 8px; background: var(--accent); color: #fff; font-weight: 650; padding: 10px 16px; cursor: pointer; }
    .client-insights-table-wrap { padding: 18px 24px 24px; overflow-x: auto; }
    .client-insights-table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
    .client-insights-table th { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
    .client-insights-table td { padding: 14px 8px; border-bottom: 1px solid var(--border); vertical-align: middle; }
    .client-insights-table tr:last-child td { border-bottom: 0; }
    .client-insights-file-title { font-weight: 650; color: var(--navy); margin-bottom: 2px; }
    .client-insights-file-meta { font-size: 0.82rem; }
    .client-insights-period { white-space: nowrap; color: var(--navy); font-weight: 600; }
    .client-insights-download-btn { display: inline-block; padding: 8px 14px; border: 1px solid var(--border); border-radius: 8px; background: #fff; color: var(--navy); font-weight: 600; font-size: 0.86rem; text-decoration: none; }
    .client-insights-download-btn:hover { background: var(--surface); border-color: #b8c4d4; }
    .client-insights-delete-form { display: inline-block; margin-left: 8px; }
    .client-insights-delete-btn { appearance: none; border: 0; background: none; color: var(--err); font-size: 0.82rem; font-weight: 600; cursor: pointer; padding: 0; }
    .client-insights-empty { margin: 0; padding: 0 24px 24px; font-size: 0.9rem; }
        """,
    )


def _ga4_website_search_html(*, has_pages: bool) -> str:
    if not has_pages:
        return ""
    return """
        <div class="website-search-bar">
          <label for="ga4PageSearch" class="filter-label">Search pages</label>
          <input type="search" id="ga4PageSearch" class="ga4-pages-search"
            placeholder="Filter by page path or title…" autocomplete="off">
          <span class="badge" id="ga4PagesCount">0 pages</span>
        </div>"""


def _ga4_metrics_summary_html(*, has_summary: bool) -> str:
    if not has_summary:
        return ""
    return """
        <section class="ga4-metrics-section" id="ga4MetricsSection" aria-label="GA4 metrics summary">
          <div class="ga4-metrics-heading">
            <span class="ga4-metrics-pill">GA4 METRICS</span>
          </div>
          <div class="ga4-metrics-grid" id="ga4MetricsGrid"></div>
        </section>"""


def _ga4_website_content_html(ga4_pages: dict[str, Any] | None) -> str:
    pages = (ga4_pages or {}).get("pages") or []
    dr = (ga4_pages or {}).get("date_range") or {}
    range_label = ""
    if dr.get("start") and dr.get("end"):
        range_label = f"{dr.get('start')} → {dr.get('end')}"
    if not pages:
        return (
            '<p class="ga4-pages-empty muted">No page data yet. Run a <strong>full refresh</strong> '
            "from Settings after GA4 BigQuery is connected.</p>"
        )
    return f"""
        <p class="table-note muted">Site-wide GA4 metrics{f' for {range_label}' if range_label else ''}. Use the search bar above to filter paths and titles.</p>
        <div class="table-wrap">
          <table class="data-table ga4-pages-table" id="ga4PagesTable">
            <thead>
              <tr>
                <th class="sortable" data-sort="page_path" scope="col" aria-sort="none">Page path<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable" data-sort="page_title" scope="col" aria-sort="none">Page title<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="sessions" scope="col" aria-sort="descending">Sessions<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="page_views" scope="col" aria-sort="none">Page views<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="users" scope="col" aria-sort="none">Users<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="engaged_sessions" scope="col" aria-sort="none">Engaged<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="engagement_rate" scope="col" aria-sort="none">Eng. rate<span class="sort-icon" aria-hidden="true"></span></th>
                <th class="sortable num" data-sort="key_events" scope="col" aria-sort="none">Key events<span class="sort-icon" aria-hidden="true"></span></th>
              </tr>
            </thead>
            <tbody id="ga4PagesBody"></tbody>
          </table>
        </div>
        <div class="ga4-pages-pagination" id="ga4PagesPagination" hidden></div>"""


def _ga4_pages_panel_html(ga4_pages: dict[str, Any] | None) -> str:
    """Legacy wrapper — website content lives on the Website Analytics view."""
    body = _ga4_website_content_html(ga4_pages)
    return f"""
    <section class="panel ga4-pages-panel" aria-label="GA4 pages">
      <div class="panel-head"><h2>GA4 Pages</h2></div>
      {body}
    </section>"""


def _dashboard_view_tabs_html(*, show_website: bool) -> str:
    website_tab = ""
    if show_website:
        website_tab = (
            '<button type="button" class="dash-view-btn" data-view="website" role="tab" '
            'aria-selected="false">Website Analytics</button>'
        )
    return f"""
      <nav class="dash-view-nav" role="tablist" aria-label="Dashboard views">
        <button type="button" class="dash-view-btn active" data-view="overview" role="tab" aria-selected="true">Overview</button>
        <button type="button" class="dash-view-btn" data-view="campaigns" role="tab" aria-selected="false">Campaign Explorer</button>
        {website_tab}
      </nav>"""


def _global_filters_bar_html(
    *,
    show_business_line: bool,
    show_channel_filters: bool,
) -> str:
    if not show_business_line and not show_channel_filters:
        return ""
    bl_row = ""
    if show_business_line:
        bl_row = """
          <div class="filter-row">
            <span class="filter-label">Business line</span>
            <div id="blFilters" class="filter-toggles" role="group" aria-label="Business line"></div>
          </div>"""
    channel_row = ""
    if show_channel_filters:
        channel_row = """
          <div class="filter-row">
            <span class="filter-label">Channel</span>
            <div id="channelFilters" class="filter-toggles" role="group" aria-label="Channel"></div>
          </div>"""
    zero_spend = ""
    if show_business_line:
        zero_spend = """
            <label class="filter-zero-spend">
              <input type="checkbox" id="showZeroSpend">
              Show inactive / $0 spend
            </label>"""
    return f"""
      <div class="dash-filters-bar" id="dashFiltersBar">
        <section class="filter-panel global-filters" aria-label="Dashboard filters">
          <div class="global-filter-rows">
            {bl_row}
            {channel_row}
          </div>
          <div class="bl-filter-footer">
            {zero_spend}
            <div class="filter-status" id="filterStatus"></div>
          </div>
        </section>
      </div>"""


def _client_switch_target_url(
    *,
    client_slug: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
) -> str:
    slug = (client_slug or "penn").strip().lower()
    nav = (active_nav or "overview").strip().lower()
    if nav == "settings":
        return _settings_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{slug}/settings"
    if nav == "insights-upload":
        return _insights_upload_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{slug}/insights-upload"
    return _dashboard_page_url(
        client_slug=slug, access_key=access_key, use_session=use_session
    )


def _sidebar_client_header_html(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
    session_is_admin: bool,
    client_meta_tip: str = "",
) -> str:
    tip = client_meta_tip or _esc(label)
    tip_btn = (
        f'<button type="button" class="info-tip" data-tip="{tip}" '
        f'aria-label="Client info">i</button>'
    )
    if session_is_admin and use_session:
        import client_config

        options = []
        current = (client_slug or "").strip().lower()
        for slug, client_label in client_config.list_dashboard_clients():
            selected = " selected" if slug == current else ""
            dest = _client_switch_target_url(
                client_slug=slug,
                active_nav=active_nav,
                access_key=access_key,
                use_session=use_session,
            )
            options.append(
                f'<option value="{_esc(dest)}"{selected}>{_esc(client_label)}</option>'
            )
        control = f"""
          <label class="sr-only" for="clientSwitcher">Client dashboard</label>
          <select id="clientSwitcher" class="client-switcher" aria-label="Switch client dashboard">
            {"".join(options)}
          </select>"""
    else:
        control = f'<span class="client-name" id="sidebarClientHead">{_esc(label)}</span>'
    return f"""
      <div class="sidebar-client-head">
        {control}
        {tip_btn}
      </div>"""


_SIDEBAR_CLIENT_CSS = """
    .sidebar-client-head {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1;
      min-width: 0;
    }
    .sidebar-client-head .client-name {
      font-size: 0.95rem;
      font-weight: 700;
      color: #fff;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      line-height: 1.25;
    }
    .client-switcher {
      flex: 1;
      min-width: 0;
      appearance: none;
      border: 1px solid rgba(255,255,255,0.22);
      border-radius: 10px;
      background: rgba(255,255,255,0.1)
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")
        no-repeat right 10px center;
      color: #fff;
      font: inherit;
      font-size: 0.92rem;
      font-weight: 650;
      padding: 9px 32px 9px 11px;
      cursor: pointer;
    }
    .client-switcher:hover,
    .client-switcher:focus-visible {
      border-color: rgba(255,255,255,0.38);
      background-color: rgba(255,255,255,0.14);
      outline: none;
    }
    .client-switcher option {
      color: #0f172a;
      background: #fff;
    }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
"""


def _sidebar_nav_html(
    *,
    client_slug: str,
    active: str,
    access_key: str | None,
    use_session: bool,
    show_business_line: bool = True,
    show_insights_upload: bool = False,
) -> str:
    """Sidebar nav: Overview, optional Insights Upload (admin), and Settings."""
    del show_business_line  # business line filters live on the overview page
    settings_url = _settings_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    overview_url = _dashboard_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    insights_upload_url = _insights_upload_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    icon_overview = '<svg class="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>'
    icon_upload = '<svg class="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 16V4m0 0l-4 4m4-4l4 4"/><path d="M4 20h16"/></svg>'
    icon_settings = '<svg class="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'

    if active == "overview":
        overview_el = f'<span class="sidebar-nav-btn active" aria-current="page">{icon_overview}Overview</span>'
    else:
        overview_el = f'<a href="{overview_url}" class="sidebar-nav-btn sidebar-nav-link">{icon_overview}Overview</a>'

    insights_el = ""
    if show_insights_upload:
        if active == "insights-upload":
            insights_el = (
                f'<span class="sidebar-nav-btn active" aria-current="page">'
                f"{icon_upload}Insights Upload</span>"
            )
        else:
            insights_el = (
                f'<a href="{insights_upload_url or "#"}" class="sidebar-nav-btn sidebar-nav-link">'
                f"{icon_upload}Insights Upload</a>"
            )

    if active == "settings":
        settings_el = f'<span class="sidebar-nav-btn active" aria-current="page">{icon_settings}Settings</span>'
    else:
        settings_el = f'<a href="{settings_url or "#"}" class="sidebar-nav-btn sidebar-nav-link">{icon_settings}Settings</a>'

    return f"""
      <nav class="sidebar-nav" role="navigation" aria-label="Dashboard views">
        {overview_el}
        {insights_el}
        {settings_el}
      </nav>"""


def _dashboard_shell_sidebar_js() -> str:
    return """
    const MOBILE_SIDEBAR_MQ = window.matchMedia('(max-width: 960px)');
    function isMobileSidebar() { return MOBILE_SIDEBAR_MQ.matches; }
    function setSidebarOpen(open) {
      document.body.classList.toggle('sidebar-open', open);
      document.getElementById('sidebarOpen')?.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    function closeSidebar() { setSidebarOpen(false); }
    function toggleSidebar() { setSidebarOpen(!document.body.classList.contains('sidebar-open')); }
    document.getElementById('sidebarOpen')?.addEventListener('click', toggleSidebar);
    document.getElementById('sidebarClose')?.addEventListener('click', closeSidebar);
    document.getElementById('sidebarBackdrop')?.addEventListener('click', closeSidebar);
    MOBILE_SIDEBAR_MQ.addEventListener('change', () => { if (!isMobileSidebar()) closeSidebar(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) closeSidebar();
    });
    document.getElementById('clientSwitcher')?.addEventListener('change', (e) => {
      const url = e.target.value;
      if (url) window.location.href = url;
    });
    """


def render_client_shell_page(
    *,
    client_slug: str,
    label: str,
    active_nav: str,
    page_title: str,
    page_subtitle: str,
    content_html: str,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    client_meta_tip: str = "",
    extra_css: str = "",
    show_business_line: bool | None = None,
    show_insights_upload: bool | None = None,
) -> str:
    """Shared dashboard chrome for settings and other child pages."""
    theme = dashboard_theme.load_client_theme(client_slug)
    account_nav = _session_account_html(email=session_email, is_admin=session_is_admin)
    if show_business_line is None:
        show_business_line = client_slug.strip().lower() == "penn"
    if show_insights_upload is None:
        show_insights_upload = can_edit_penn_insights(
            session_is_admin=session_is_admin,
            access_key=access_key,
        )
    sidebar_nav = _sidebar_nav_html(
        client_slug=client_slug,
        active=active_nav,
        access_key=access_key,
        use_session=use_session,
        show_business_line=show_business_line,
        show_insights_upload=show_insights_upload,
    )
    client_header = _sidebar_client_header_html(
        client_slug=client_slug,
        label=label,
        active_nav=active_nav,
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        client_meta_tip=client_meta_tip,
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — {_esc(page_title)}</title>
  {_favicon_head_html()}
  <style>
    {dashboard_theme.root_css_block(theme)}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); }}
    .app-shell {{ display: flex; min-height: 100vh; }}
    .dash-sidebar {{
      width: 248px; background: linear-gradient(180deg, var(--sidebar-from), var(--sidebar-to)); color: #fff;
      display: flex; flex-direction: column; padding: 16px 12px; flex-shrink: 0;
    }}
    .sidebar-top {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 18px; min-height: 36px; }}
    {_SIDEBAR_CLIENT_CSS}
    .sidebar-nav {{ display: flex; flex-direction: column; gap: 6px; }}
    .sidebar-nav-btn, .sidebar-nav-link {{
      appearance: none; border: none; background: transparent; color: rgba(255,255,255,0.72);
      padding: 11px 12px; border-radius: 10px; font-size: 0.9rem; font-weight: 600; text-align: left;
      cursor: pointer; display: flex; align-items: center; gap: 10px; text-decoration: none;
      transition: background 0.15s, color 0.15s;
    }}
    .sidebar-nav-btn:hover, .sidebar-nav-link:hover {{ background: rgba(255,255,255,0.08); color: #fff; }}
    .sidebar-nav-btn.active, .sidebar-nav-link.active {{
      background: rgba(255,255,255,0.14); color: #fff; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.1);
    }}
    .sidebar-nav-icon {{ width: 18px; height: 18px; opacity: 0.85; flex-shrink: 0; }}
    .sidebar-footer {{ margin-top: auto; padding-top: 14px; border-top: 1px solid rgba(255,255,255,0.1); }}
    .info-tip {{
      appearance: none; border: 1px solid rgba(255,255,255,0.22); background: rgba(255,255,255,0.08);
      color: rgba(255,255,255,0.85); width: 22px; height: 22px; border-radius: 999px; font-size: 0.72rem;
      font-weight: 700; cursor: help; flex-shrink: 0; position: relative;
    }}
    .info-tip::after {{
      content: attr(data-tip); position: absolute; right: 0; bottom: calc(100% + 8px); width: max-content;
      max-width: 240px; padding: 10px 12px; border-radius: 10px; background: #fff; color: var(--text);
      font-size: 0.76rem; font-weight: 500; line-height: 1.45; white-space: pre-line;
      box-shadow: var(--shadow); border: 1px solid var(--border); opacity: 0; visibility: hidden;
      transform: translateY(4px); transition: opacity 0.15s, transform 0.15s; pointer-events: none; z-index: 60;
    }}
    .info-tip:hover::after, .info-tip:focus-visible::after {{ opacity: 1; visibility: visible; transform: translateY(0); }}
    .account-nav {{ display: flex; flex-direction: column; gap: 6px; padding-bottom: 10px; margin-bottom: 2px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
    .account-email {{ font-size: 0.78rem; color: rgba(255,255,255,0.72); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .account-actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; font-size: 0.8rem; }}
    .account-sep {{ color: rgba(255,255,255,0.35); }}
    .account-link {{ color: rgba(255,255,255,0.92); text-decoration: none; background: none; border: 0; padding: 0; font: inherit; cursor: pointer; }}
    .account-logout-form {{ display: inline; margin: 0; }}
    .account-link:hover {{ text-decoration: underline; }}
    .dash-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; }}
    .dash-chrome-bar {{
      display: none;
      align-items: center;
      gap: 12px;
      padding: 12px 20px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
    }}
    .dash-content {{ flex: 1; padding: 24px; overflow: auto; }}
    .wrap {{ max-width: 920px; }}
    .sidebar-menu-btn, .sidebar-close {{
      appearance: none; border: 1px solid var(--border); background: var(--panel); color: var(--navy);
      width: 40px; height: 40px; border-radius: 10px; cursor: pointer; display: none; align-items: center; justify-content: center;
    }}
    .sidebar-menu-btn svg, .sidebar-close svg {{ width: 20px; height: 20px; }}
    .sidebar-backdrop {{
      position: fixed; inset: 0; background: rgba(10, 37, 64, 0.45); z-index: 90; opacity: 0; visibility: hidden;
      pointer-events: none; border: none; padding: 0; cursor: pointer;
    }}
    @media (max-width: 960px) {{
      .dash-chrome-bar {{ display: flex; }}
      .dash-sidebar {{
        position: fixed; left: 0; top: 0; bottom: 0; z-index: 100; transform: translateX(-100%);
        transition: transform 0.24s ease;
      }}
      body.sidebar-open .dash-sidebar {{ transform: translateX(0); }}
      body.sidebar-open .sidebar-backdrop {{ opacity: 1; visibility: visible; pointer-events: auto; }}
      .sidebar-menu-btn, .sidebar-close {{ display: flex; }}
    }}
    {extra_css}
  </style>
</head>
<body>
  <button type="button" class="sidebar-backdrop" id="sidebarBackdrop" aria-label="Close menu" tabindex="-1"></button>
  <div class="app-shell">
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Dashboard navigation">
      <div class="sidebar-top">
        {client_header}
        <button type="button" class="sidebar-close" id="sidebarClose" aria-label="Close menu">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>
      {sidebar_nav}
      <div class="sidebar-footer">
        {account_nav}
      </div>
    </aside>
    <div class="dash-main">
      <div class="dash-chrome-bar">
        <button type="button" class="sidebar-menu-btn" id="sidebarOpen" aria-label="Open menu" aria-controls="dashSidebar">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
        </button>
      </div>
      <div class="dash-content">
        <div class="wrap">
          {content_html}
        </div>
      </div>
    </div>
  </div>
  <script>{_dashboard_shell_sidebar_js()}</script>
</body>
</html>"""


def _aggregated_card(totals: dict[str, Any]) -> str:
    """Deprecated — use _paid_ad_overview_html."""
    return _paid_ad_overview_html(totals)


def _platform_breakdown_html(
    breakdowns: dict[str, Any],
    *,
    ga4_attr: dict[str, Any] | None = None,
    platform_totals: dict[str, Any] | None = None,
) -> str:
    """Render per-platform tables at the correct entity levels."""
    ga4_platforms = _ga4_platform_reports(ga4_attr)
    ad_totals = platform_totals or {}

    def ga4_index(platform: str) -> dict[str, dict[str, Any]]:
        campaigns = (breakdowns.get(platform) or {}).get("campaign") or []
        report = ga4_platforms.get(platform) or {}
        if not campaigns or not report:
            return {}
        return build_ga4_campaign_index(report.get("by_campaign") or [], campaigns)

    def site_block(platform: str) -> str:
        return _platform_site_impact_html(
            platform,
            ga4_platforms.get(platform) or {},
            ad_totals=ad_totals.get(platform),
        )

    parts: list[str] = []
    google = breakdowns.get("google") or {}
    google_campaigns = google.get("campaign") or []
    parts.append(
        _drillable_table(
            "google",
            _platform_title_html("google", "Google Ads"),
            google_campaigns,
            entity_level="campaign",
            site_footer=site_block("google"),
            ga4_by_campaign=ga4_index("google"),
        )
    )

    linkedin = breakdowns.get("linkedin") or {}
    groups = linkedin.get("campaign_group") or []
    if groups:
        parts.append(
            _drillable_table(
                "linkedin",
                _platform_title_html("linkedin", "LinkedIn"),
                groups,
                entity_level="campaign_group",
                site_footer=site_block("linkedin"),
                ga4_by_campaign=ga4_index("linkedin"),
            )
        )
    else:
        parts.append(
            f"""
        <section class="panel platform-panel platform-linkedin">
          <div class="panel-head"><h2>{_platform_title_html("linkedin", "LinkedIn")}</h2></div>
          <p class="muted">No campaign group data — click Refresh now.</p>
          {site_block("linkedin")}
        </section>
        """
        )

    meta = breakdowns.get("meta") or {}
    meta_campaigns = meta.get("campaign") or []
    parts.append(
        _drillable_table(
            "meta",
            _platform_title_html("meta", "Meta"),
            meta_campaigns,
            entity_level="campaign",
            site_footer=site_block("meta"),
            ga4_by_campaign=ga4_index("meta"),
        )
    )
    return "\n".join(parts)


def _ga4_platform_reports(ga4_attr: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not ga4_attr:
        return {}
    platforms = ga4_attr.get("platforms")
    if platforms:
        return platforms
    return {"google": ga4_attr}


def _platform_site_impact_html(
    platform: str,
    report: dict[str, Any],
    *,
    ad_totals: dict[str, Any] | None,
) -> str:
    """Compact on-site GA4 block embedded under each platform panel."""
    totals = report.get("totals") or {}
    ga_sessions = int(totals.get("sessions") or 0)
    if not ga_sessions and not report:
        return ""

    ga_engaged = int(totals.get("engaged_sessions") or 0)
    key_events = int(totals.get("key_events") or 0)
    page_views = int(totals.get("page_views") or 0)
    ad_clicks = int((ad_totals or {}).get("clicks") or 0)
    by_tier = totals.get("by_tier") or {}
    tier_labels = PLATFORM_TIER_LABELS.get(platform) or {}

    tier_rows = []
    for key, tier_label in tier_labels.items():
        t = by_tier.get(key) or {}
        sessions = int(t.get("sessions") or 0)
        if not sessions:
            continue
        engaged = int(t.get("engaged_sessions") or 0)
        tier_rows.append(
            f"""<tr>
              <td>{_esc(tier_label)}</td>
              <td class="num">{_fmt_int(sessions)}</td>
              <td class="num">{_fmt_pct(engaged, sessions)}</td>
              <td class="num">{_fmt_int(t.get("key_events") or 0)}</td>
            </tr>"""
        )

    top_events = report.get("top_events") or []
    event_rows = "".join(
        f"""<tr>
          <td class="name">{_esc(ev.get("event_name"))}</td>
          <td class="num">{_fmt_int(ev.get("event_count") or 0)}</td>
        </tr>"""
        for ev in top_events[:8]
    )

    click_span = ""
    if ad_clicks and ga_sessions:
        click_span = (
            f'<span class="muted">{_fmt_int(ad_clicks)} ad clicks · '
            f"{ga_sessions / ad_clicks:.2f} sess/click</span>"
        )

    if not ga_sessions:
        return """
      <div class="site-impact site-impact-empty">
        <span class="site-impact-label">On-site (GA4)</span>
        <span class="muted">No attributed sessions this period</span>
      </div>"""

    details_inner = ""
    if tier_rows:
        details_inner += f"""
          <p class="table-note muted">{_esc(METHODOLOGY.get(platform, report.get("methodology") or ""))}</p>
          <div class="table-wrap">
            <table class="data-table compact">
              <thead><tr><th>Match tier</th><th>Sessions</th><th>Eng. rate</th><th>Key events</th></tr></thead>
              <tbody>{''.join(tier_rows)}</tbody>
            </table>
          </div>"""
    if event_rows:
        details_inner += f"""
          <div class="table-wrap">
            <table class="data-table compact">
              <thead><tr><th>Top event</th><th>Count</th></tr></thead>
              <tbody>{event_rows}</tbody>
            </table>
          </div>"""

    details_block = ""
    if details_inner:
        details_block = f"""
      <details class="site-impact-details">
        <summary>Attribution detail &amp; top events</summary>
        {details_inner}
      </details>"""

    return f"""
      <div class="site-impact">
        <div class="site-impact-bar">
          <span class="site-impact-label">On-site (GA4)</span>
          <span><strong>{_fmt_int(ga_sessions)}</strong> sessions</span>
          <span>{_fmt_pct(ga_engaged, ga_sessions)} engaged</span>
          <span>{_fmt_int(key_events)} key events</span>
          <span>{_fmt_int(page_views)} page views</span>
          {click_span}
        </div>
        {details_block}
      </div>"""


def _business_line_campaigns_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Always derive business-line rows from breakdowns (LinkedIn hierarchy changes need fresh mapping)."""
    client_key = str(snapshot.get("client_key") or "penn")
    if client_key != "penn":
        return snapshot.get("business_line_campaigns") or []
    return build_business_line_campaigns(
        _breakdowns_from_snapshot(snapshot),
        client_slug=client_key,
    )


def _breakdowns_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    breakdowns = snapshot.get("breakdowns")
    if breakdowns:
        return breakdowns
    legacy = snapshot.get("campaigns") or {}
    return {platform: {"campaign": rows} for platform, rows in legacy.items()}


def _campaign_explorer_content_html(*, show_business_line: bool, platform_breakdown_html: str) -> str:
    if show_business_line:
        return """
            <section class="campaign-explorer-section" aria-label="Campaign performance">
              <div class="bl-summary" id="blSummary"></div>
              <section class="panel platform-panel">
                <div class="panel-head">
                  <h2>Campaign performance</h2>
                  <span class="badge" id="blRowCount">0 rows</span>
                </div>
                <p class="table-note">Use the filters at the top to narrow business lines and channels — all are included by default. LinkedIn rows start at campaign group (Campaign Manager “Campaign”); drill down to ad sets and ads. Google and Meta drill to ad groups/ad sets and ads.</p>
                <div class="table-wrap">
                  <table class="data-table" id="blTable">
                    <thead>
                      <tr>
                        <th class="chevron-col"></th>
                        <th class="sortable" data-sort="platform" scope="col" aria-sort="none">Platform<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="business_line" scope="col" aria-sort="none">Business line<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="name" scope="col" aria-sort="none">Campaign / group<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="spend" scope="col" aria-sort="none">Spend<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="clicks" scope="col" aria-sort="none">Clicks<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="impressions" scope="col" aria-sort="none">Impressions<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="ctr" scope="col" aria-sort="none">CTR<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="conversions" scope="col" aria-sort="none">Conv.<span class="sort-icon" aria-hidden="true"></span></th>
                        <th class="sortable" data-sort="cpc" scope="col" aria-sort="none">CPC<span class="sort-icon" aria-hidden="true"></span></th>
                      </tr>
                    </thead>
                    <tbody id="blTableBody" class="tree-table"></tbody>
                  </table>
                </div>
              </section>
            </section>"""
    return platform_breakdown_html or '<p class="muted">No campaign data for this period.</p>'


def _business_line_merged_section_html() -> str:
    """Deprecated — use _campaign_explorer_content_html with filters in _global_filters_bar_html."""
    return _campaign_explorer_content_html(show_business_line=True, platform_breakdown_html="")


def render_penn_html(
    snapshot: dict[str, Any] | None,
    *,
    client_slug: str = "penn",
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
) -> str:
    """use_session: refresh forms omit ?key= (cookie auth). access_key: legacy shared secret."""
    slug = (client_slug or "penn").strip().lower()
    theme = dashboard_theme.load_client_theme(slug)
    show_business_line = slug == "penn"
    account_nav = _session_account_html(email=session_email, is_admin=session_is_admin)
    if not snapshot:
        try:
            label = client_config.client_label(slug)
        except ValueError:
            label = slug.replace("-", " ").title()
        settings_page = _settings_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        )
        can_upload_docs = can_edit_penn_insights(
            session_is_admin=session_is_admin,
            access_key=access_key,
        )
        client_insights_html = _client_insights_list_html(
            client_slug=slug,
            access_key=access_key,
            use_session=use_session,
        )
        flash_html = ""
        if flash_message:
            flash_html = f'<div class="dash-flash">{_esc(flash_message)}</div>'
        empty_body = f"""
        {flash_html}
        {client_insights_html}
        <section class="panel">
          <h2>No data yet</h2>
          <p class="muted">Connect your ad platforms and run a refresh from Settings.</p>
          <p><a class="dash-link" href="{settings_page}">Go to Settings →</a></p>
        </section>"""
        return render_client_shell_page(
            client_slug=slug,
            label=label,
            active_nav="overview",
            page_title="Overview",
            page_subtitle=f"{label} · Paid media performance",
            content_html=empty_body,
            access_key=access_key,
            use_session=use_session,
            session_email=session_email,
            session_is_admin=session_is_admin,
            show_business_line=show_business_line,
            show_insights_upload=can_upload_docs,
            extra_css="""
    .panel { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow-sm); }
    .panel h2 { margin: 0 0 10px; font-size: 1.05rem; color: var(--navy); }
    .muted { color: var(--muted); }
    .dash-link { color: var(--accent); font-weight: 600; text-decoration: none; }
    .dash-link:hover { text-decoration: underline; }
    .client-insights-section { margin-bottom: 20px; }
    .client-insights-doc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
    .client-insights-doc-card { display: block; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; text-decoration: none; color: inherit; box-shadow: var(--shadow-sm); transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s; }
    .client-insights-doc-card:hover { border-color: var(--accent); box-shadow: var(--shadow); transform: translateY(-1px); }
    .client-insights-doc-period { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
    .client-insights-doc-title { font-size: 0.92rem; font-weight: 650; color: var(--navy); line-height: 1.35; margin-bottom: 6px; }
    .client-insights-doc-meta { font-size: 0.78rem; color: var(--muted); }
    .client-insights-empty { margin: 0; font-size: 0.9rem; }
    .paid-ad-overview-pill { display: inline-flex; align-items: center; gap: 0; background: var(--navy); color: #fff; font-size: 0.68rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; padding: 6px 14px 6px 0; border-radius: 999px; overflow: hidden; }
    .paid-ad-overview-pill::before { content: ''; display: inline-block; width: 4px; align-self: stretch; background: var(--gold); margin-right: 10px; border-radius: 999px 0 0 999px; }
    .client-insights-pill::before { background: #22c55e; }
    .client-insights-count { margin-left: 8px; padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.16); font-size: 0.62rem; letter-spacing: 0.04em; }
    .dash-flash { background: var(--ok-bg); border: 1px solid #b8dfc8; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; font-size: 0.9rem; color: var(--ok); }
            """,
        )

    label = snapshot.get("label") or client_config.client_label(slug)
    client_slug = str(snapshot.get("client_key") or slug)
    show_business_line = client_slug == "penn"
    dr = snapshot.get("date_range") or {}
    refreshed = snapshot.get("refreshed_at") or "—"
    preset = dr.get("preset") or ""
    range_label = f"{dr.get('start', '')} → {dr.get('end', '')} ({preset})"

    totals = snapshot.get("platform_totals") or {}
    errors = snapshot.get("errors") or {}
    error_html = ""
    if errors:
        items = "".join(f"<li><strong>{_esc(k)}</strong>: {_esc(v)}</li>" for k, v in errors.items())
        error_html = f'<div class="errors"><strong>Partial refresh warnings</strong><ul>{items}</ul></div>'
    flash_html = ""
    if flash_message:
        flash_html = f'<div class="dash-flash">{_esc(flash_message)}</div>'

    chart_data = snapshot.get("daily_metrics") or {}
    dates_set: set[str] = set()
    for rows in chart_data.values():
        for row in rows:
            dates_set.add(str(row.get("metric_date") or "")[:10])
    dates = sorted(d for d in dates_set if d)

    def platform_metric_series(source: str, field: str) -> list[float]:
        by_date = {
            str(r.get("metric_date") or "")[:10]: float(r.get(field) or 0)
            for r in chart_data.get(source, [])
        }
        return [by_date.get(d, 0.0) for d in dates]

    accounts_early = snapshot.get("accounts") or {}
    has_ga4_config = bool(accounts_early.get("ga4_client_key") or totals.get("organic"))
    chart_platform_ids = ("google", "linkedin", "meta") + (
        ("organic",) if has_ga4_config else ()
    )

    performance_chart_json = _json_for_html_script(
        {
            "labels": dates,
            "metrics": {
                metric: {
                    platform: platform_metric_series(platform, metric)
                    for platform in chart_platform_ids
                }
                for metric in ("spend", "clicks", "impressions", "conversions")
            },
        }
    )

    breakdowns = _breakdowns_from_snapshot(snapshot)
    accounts = accounts_early
    ga4_attr = snapshot.get("ga4_attribution")
    ga4_platforms = _ga4_platform_reports(ga4_attr)
    aggregated = snapshot.get("aggregated_paid_media") or _aggregated_paid_media(totals)
    breakdown_html = _platform_breakdown_html(
        breakdowns,
        ga4_attr=ga4_attr,
        platform_totals=totals,
    )
    breakdowns_json = _json_for_html_script(breakdowns)
    ga4_campaign_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for platform in ("google", "linkedin", "meta"):
        campaigns = (breakdowns.get(platform) or {}).get("campaign") or []
        report = ga4_platforms.get(platform) or {}
        ga4_campaign_metrics[platform] = (
            build_ga4_campaign_index(report.get("by_campaign") or [], campaigns)
            if campaigns and report
            else {}
        )
    ga4_campaign_metrics_json = _json_for_html_script(ga4_campaign_metrics)
    bl_campaigns = _business_line_campaigns_from_snapshot(snapshot)
    bl_campaigns_json = _json_for_html_script(bl_campaigns)
    bl_catalog_json = _json_for_html_script(active_business_line_catalog(bl_campaigns))
    platform_catalog_list = active_platform_catalog(
        bl_campaigns,
        include_organic=has_ga4_config,
    )
    if not platform_catalog_list:
        present = {p for p in ("google", "linkedin", "meta") if totals.get(p)}
        platform_catalog_list = [
            item for item in platform_catalog(include_organic=has_ga4_config) if item["id"] in present
        ]
        if has_ga4_config and not any(p["id"] == "organic" for p in platform_catalog_list):
            platform_catalog_list.append({"id": "organic", "label": "Organic"})
    platform_catalog_json = _json_for_html_script(platform_catalog_list)
    sidebar_nav = _sidebar_nav_html(
        client_slug=client_slug,
        active="overview",
        access_key=access_key,
        use_session=use_session,
        show_business_line=show_business_line,
        show_insights_upload=can_edit_penn_insights(
            session_is_admin=session_is_admin,
            access_key=access_key,
        ),
    )
    client_meta_tip = _esc(f"Date range: {range_label}\nLast refreshed: {refreshed} UTC")
    client_header = _sidebar_client_header_html(
        client_slug=client_slug,
        label=label,
        active_nav="overview",
        access_key=access_key,
        use_session=use_session,
        session_is_admin=session_is_admin,
        client_meta_tip=client_meta_tip,
    )
    client_insights_html = _client_insights_list_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )
    overview_paid_html = _paid_ad_overview_html(aggregated, ga4_attr)
    paid_overview_metrics_json = _json_for_html_script(
        _paid_ad_overview_metrics(aggregated, ga4_attr)
    )
    ga4_pages_report = snapshot.get("ga4_pages")
    accounts = snapshot.get("accounts") or {}
    has_ga4 = bool(accounts.get("ga4_client_key") or (ga4_pages_report or {}).get("pages"))
    has_ga4_pages = bool((ga4_pages_report or {}).get("pages"))
    has_ga4_summary = bool((ga4_pages_report or {}).get("summary"))
    view_tabs_html = _dashboard_view_tabs_html(show_website=has_ga4)
    filters_bar_html = _global_filters_bar_html(
        show_business_line=show_business_line,
        show_channel_filters=bool(platform_catalog_list),
    )
    campaign_explorer_html = _campaign_explorer_content_html(
        show_business_line=show_business_line,
        platform_breakdown_html=breakdown_html,
    )
    website_analytics_html = _ga4_website_content_html(ga4_pages_report if has_ga4 else None)
    website_search_html = _ga4_website_search_html(has_pages=has_ga4_pages)
    ga4_metrics_html = _ga4_metrics_summary_html(has_summary=has_ga4_summary)
    website_tab_panel = ""
    if has_ga4:
        website_tab_panel = f"""
          <div id="view-website" class="view-panel" role="tabpanel" hidden>
            <section class="panel ga4-pages-panel" aria-label="Website analytics">
              {ga4_metrics_html}
              <div class="panel-head"><h2>Page performance</h2></div>
              {website_search_html}
              {website_analytics_html}
            </section>
          </div>"""
    ga4_pages_json = _json_for_html_script((ga4_pages_report or {}).get("pages") or [])
    ga4_summary_json = _json_for_html_script((ga4_pages_report or {}).get("summary") or {})
    metric_defs_json = _json_for_html_script(dashboard_theme.chart_metric_defs(theme))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(label)} — Ads Dashboard</title>{_favicon_head_html()}
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    {dashboard_theme.root_css_block(theme)}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    .app-shell {{
      display: flex;
      min-height: 100vh;
    }}
    .dash-sidebar {{
      width: 248px;
      flex-shrink: 0;
      background: linear-gradient(180deg, var(--sidebar-from) 0%, var(--sidebar-to) 100%);
      color: #fff;
      display: flex;
      flex-direction: column;
      padding: 14px 10px 16px;
      border-right: 1px solid rgba(255,255,255,0.06);
      box-shadow: 4px 0 24px rgba(10, 37, 64, 0.12);
      overflow: visible;
      transition: transform 0.24s ease, box-shadow 0.24s ease;
      position: sticky;
      top: 0;
      height: 100vh;
      z-index: 55;
    }}
    .sidebar-backdrop {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(10, 37, 64, 0.45);
      z-index: 90;
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transition: opacity 0.24s ease, visibility 0.24s ease;
      border: none;
      padding: 0;
      cursor: pointer;
    }}
    .sidebar-menu-btn,
    .sidebar-close {{
      appearance: none;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--navy);
      width: 40px;
      height: 40px;
      border-radius: 10px;
      cursor: pointer;
      display: none;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: background 0.15s, border-color 0.15s;
    }}
    .sidebar-menu-btn:hover,
    .sidebar-close:hover {{
      background: #f4f7fb;
      border-color: #b8c4d4;
    }}
    .sidebar-menu-btn svg,
    .sidebar-close svg {{
      width: 20px;
      height: 20px;
    }}
    .sidebar-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 18px;
      min-height: 36px;
    }}
    {_SIDEBAR_CLIENT_CSS}
    .sidebar-nav {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .sidebar-nav-btn {{
      appearance: none;
      border: none;
      background: transparent;
      color: rgba(255,255,255,0.72);
      padding: 11px 12px;
      border-radius: 10px;
      font-size: 0.9rem;
      font-weight: 600;
      text-align: left;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 10px;
      transition: background 0.15s, color 0.15s;
      white-space: nowrap;
      text-decoration: none;
    }}
    a.sidebar-nav-link {{ text-decoration: none; }}
    .sidebar-nav-btn:hover {{
      background: rgba(255,255,255,0.08);
      color: #fff;
    }}
    .sidebar-nav-btn.active {{
      background: rgba(255,255,255,0.14);
      color: #fff;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.1);
    }}
    .sidebar-nav-icon {{
      width: 18px;
      height: 18px;
      opacity: 0.85;
      flex-shrink: 0;
    }}
    .sidebar-footer {{
      margin-top: auto;
      padding-top: 14px;
      border-top: 1px solid rgba(255,255,255,0.1);
      display: flex;
      flex-direction: column;
      gap: 10px;
      position: relative;
      overflow: visible;
    }}
    .info-tip {{
      appearance: none;
      border: 1px solid rgba(255,255,255,0.22);
      background: rgba(255,255,255,0.08);
      color: rgba(255,255,255,0.85);
      width: 22px;
      height: 22px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      cursor: help;
      flex-shrink: 0;
      line-height: 1;
      position: relative;
    }}
    .info-tip::after {{
      content: attr(data-tip);
      position: absolute;
      right: 0;
      left: auto;
      bottom: calc(100% + 8px);
      width: max-content;
      max-width: 240px;
      padding: 10px 12px;
      border-radius: 10px;
      background: #fff;
      color: var(--text);
      font-size: 0.76rem;
      font-weight: 500;
      line-height: 1.45;
      white-space: pre-line;
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
      opacity: 0;
      visibility: hidden;
      transform: translateY(4px);
      transition: opacity 0.15s, transform 0.15s, visibility 0.15s;
      pointer-events: none;
      z-index: 60;
    }}
    .info-tip:hover::after,
    .info-tip:focus-visible::after {{
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }}
    .info-tip--light {{
      border-color: var(--border);
      background: #f8fafc;
      color: var(--muted);
    }}
    .info-tip--light::after {{
      left: auto;
      right: 0;
      bottom: calc(100% + 8px);
    }}
    .account-nav {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding-bottom: 10px;
      margin-bottom: 2px;
      border-bottom: 1px solid rgba(255,255,255,0.1);
    }}
    .account-email {{
      font-size: 0.78rem;
      color: rgba(255,255,255,0.72);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .account-actions {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 4px 6px;
      font-size: 0.8rem;
    }}
    .account-sep {{ color: rgba(255,255,255,0.35); }}
    .account-link {{
      color: rgba(255,255,255,0.92);
      text-decoration: none;
      background: none;
      border: 0;
      padding: 0;
      font: inherit;
      cursor: pointer;
    }}
    .account-link:hover {{ text-decoration: underline; color: #fff; }}
    .account-logout-form {{ display: inline; margin: 0; }}
    .settings-wrap {{ position: relative; }}
    .settings-btn {{
      appearance: none;
      width: 100%;
      border: 1px solid rgba(255,255,255,0.18);
      background: rgba(255,255,255,0.07);
      color: #fff;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 0.86rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: background 0.15s;
    }}
    .settings-btn:hover {{ background: rgba(255,255,255,0.12); }}
    .settings-btn svg {{ width: 16px; height: 16px; opacity: 0.9; }}
    .settings-popover {{
      position: absolute;
      left: 0;
      right: 0;
      bottom: calc(100% + 10px);
      min-width: 280px;
      max-height: min(85vh, 520px);
      overflow-y: auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: var(--shadow);
      color: var(--text);
      z-index: 50;
    }}
    .settings-popover[hidden] {{ display: none; }}
    .settings-popover__head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      background: #f8fafc;
    }}
    .settings-popover__title {{
      font-size: 0.88rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .settings-popover__close {{
      appearance: none;
      border: none;
      background: none;
      font-size: 1.25rem;
      line-height: 1;
      color: var(--muted);
      cursor: pointer;
      padding: 2px 6px;
    }}
    .settings-popover__body {{ padding: 14px; }}
    .settings-popover__hint {{
      margin: 0 0 12px;
      font-size: 0.82rem;
      line-height: 1.4;
    }}
    .settings-page-link {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}
    .settings-page-link:hover {{ text-decoration: underline; }}
    .dash-main {{
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
    }}
    .dash-chrome-bar {{
      display: none;
      align-items: center;
      gap: 12px;
      max-width: 1280px;
      margin: 0 auto;
      width: 100%;
      padding: 10px 24px 0;
      background: transparent;
    }}
    .dash-content {{
      flex: 1;
      max-width: 1280px;
      width: 100%;
      margin: 0 auto;
      padding: 24px 24px 48px;
    }}
    .wrap {{ min-width: 0; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px;
      box-shadow: var(--shadow-sm);
      transition: box-shadow 0.2s, transform 0.2s;
    }}
    .card:hover {{ box-shadow: var(--shadow); transform: translateY(-1px); }}
    .card-empty {{ opacity: 0.85; }}
    .card-label {{
      font-size: 0.78rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .06em;
      font-weight: 600;
    }}
    .card-label .platform-title {{
      text-transform: none;
      letter-spacing: -0.01em;
      font-size: 0.92rem;
      font-weight: 650;
      color: var(--navy);
    }}
    .platform-title {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .platform-favicon {{
      width: 20px;
      height: 20px;
      border-radius: 4px;
      flex-shrink: 0;
      object-fit: contain;
    }}
    .panel-head h2 .platform-title {{
      font-size: 1.12rem;
      font-weight: 650;
      color: var(--navy);
    }}
    .paid-ad-overview {{
      margin-bottom: 16px;
    }}
    .paid-ad-overview-heading {{
      margin-bottom: 12px;
    }}
    .paid-ad-overview-pill {{
      display: inline-flex;
      align-items: center;
      gap: 0;
      background: var(--navy);
      color: #fff;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      padding: 6px 14px 6px 0;
      border-radius: 999px;
      overflow: hidden;
    }}
    .paid-ad-overview-pill::before {{
      content: '';
      display: inline-block;
      width: 4px;
      align-self: stretch;
      background: var(--gold);
      margin-right: 10px;
      border-radius: 999px 0 0 999px;
    }}
    .paid-ad-metrics-grid {{
      display: grid;
      grid-template-columns: repeat(8, minmax(0, 1fr));
      gap: 12px;
    }}
    @media (max-width: 1400px) {{
      .paid-ad-metrics-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    }}
    @media (max-width: 900px) {{
      .paid-ad-metrics-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    .client-insights-section {{
      margin-top: 16px;
      margin-bottom: 16px;
    }}
    .client-insights-heading {{
      margin-bottom: 12px;
    }}
    .client-insights-pill::before {{
      background: #22c55e;
    }}
    .client-insights-count {{
      margin-left: 8px;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.16);
      font-size: 0.62rem;
      letter-spacing: 0.04em;
    }}
    .client-insights-doc-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;
    }}
    .client-insights-doc-card {{
      display: block;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      text-decoration: none;
      color: inherit;
      box-shadow: var(--shadow-sm);
      transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
    }}
    .client-insights-doc-card:hover {{
      border-color: var(--accent);
      box-shadow: var(--shadow);
      transform: translateY(-1px);
    }}
    .client-insights-doc-period {{
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .client-insights-doc-title {{
      font-size: 0.92rem;
      font-weight: 650;
      color: var(--navy);
      line-height: 1.35;
      margin-bottom: 6px;
    }}
    .client-insights-doc-meta {{
      font-size: 0.78rem;
      color: var(--muted);
    }}
    .client-insights-empty {{
      margin: 0;
      font-size: 0.9rem;
    }}
    .ga4-pages-pagination {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px 16px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .ga4-pages-pagination-info {{
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .ga4-pages-pagination-controls {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .ga4-pages-page-btn {{
      appearance: none;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--navy);
      border-radius: 8px;
      padding: 6px 12px;
      font: inherit;
      font-size: 0.84rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .ga4-pages-page-btn:hover:not(:disabled) {{
      border-color: var(--accent);
      color: var(--accent);
    }}
    .ga4-pages-page-btn:disabled {{
      opacity: 0.45;
      cursor: not-allowed;
    }}
    .ga4-pages-page-btn.active {{
      background: var(--navy);
      border-color: var(--navy);
      color: #fff;
    }}
    .insights-panel {{
      background: linear-gradient(135deg, #fff 0%, #fffbf5 100%);
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      padding: 20px 22px;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }}
    .insights-head {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .insights-title {{
      margin: 0;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .insights-body {{
      flex: 1;
      font-size: 0.9rem;
      line-height: 1.55;
      color: var(--text);
      overflow: auto;
      max-height: 220px;
    }}
    .insights-list {{
      margin: 0;
      padding-left: 1.15rem;
    }}
    .insights-list li {{
      margin: 0 0 0.45rem;
    }}
    .insights-list li:last-child {{ margin-bottom: 0; }}
    .insights-para {{
      margin: 0 0 0.5rem;
    }}
    .insights-para:last-child {{ margin-bottom: 0; }}
    .insights-empty {{
      margin: 0;
      font-size: 0.88rem;
      line-height: 1.5;
    }}
    .insights-foot {{
      margin: 12px 0 0;
      font-size: 0.72rem;
    }}
    .insights-editor {{
      margin-top: 20px;
      padding-top: 16px;
      border-top: 1px solid var(--border);
    }}
    .insights-editor-title {{
      margin: 0 0 6px;
      font-size: 0.95rem;
      font-weight: 650;
      color: var(--navy);
    }}
    .insights-editor-hint {{
      margin: 0 0 10px;
      font-size: 0.82rem;
    }}
    .insights-editor-meta {{
      margin: 0 0 8px;
      font-size: 0.78rem;
    }}
    .insights-textarea {{
      width: 100%;
      min-height: 120px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font: inherit;
      font-size: 0.86rem;
      line-height: 1.45;
      resize: vertical;
      margin-bottom: 10px;
    }}
    .insights-save-btn {{
      width: 100%;
    }}
    .ga4-pages-panel {{
      margin-bottom: 20px;
    }}
    .ga4-pages-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 0 22px;
      margin-bottom: 8px;
    }}
    .ga4-pages-search-wrap {{
      flex: 1;
      min-width: 220px;
      max-width: 440px;
    }}
    .ga4-pages-search {{
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font: inherit;
      background: #fff;
    }}
    .ga4-pages-search:focus {{
      outline: 2px solid rgba(11, 92, 171, 0.25);
      border-color: var(--accent);
    }}
    .ga4-pages-panel .table-note {{
      padding: 0 22px;
    }}
    .ga4-pages-panel .table-wrap {{
      padding: 0 22px 22px;
    }}
    .ga4-pages-table td.page-path {{
      max-width: 300px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.8rem;
    }}
    .ga4-pages-table td.page-title {{
      max-width: 260px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .ga4-pages-empty {{
      padding: 0 22px 22px;
      margin: 0;
    }}
    .visually-hidden {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
    .total-spend-panel {{
      background: linear-gradient(135deg, #fff 0%, #f8fbff 100%);
      border: 1px solid var(--border);
      border-left: 4px solid var(--gold);
      padding: 24px 28px;
    }}
    .total-spend-head {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }}
    .total-spend-title {{
      margin: 0;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .total-spend-hero {{
      font-size: clamp(2.25rem, 4vw, 3rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      color: var(--navy);
      line-height: 1.1;
      margin: 8px 0 20px;
      font-variant-numeric: tabular-nums;
    }}
    .total-spend-metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px 28px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }}
    .total-spend-metric {{
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 88px;
    }}
    .total-spend-metric__val {{
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--navy);
      font-variant-numeric: tabular-nums;
    }}
    .total-spend-metric__lbl {{
      font-size: 0.72rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .card-value {{ font-size: 1.75rem; font-weight: 700; margin: 8px 0 4px; letter-spacing: -0.02em; }}
    .card-stats {{ display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 0.84rem; color: var(--muted); }}
    .chart-stack {{ display: flex; flex-direction: column; gap: 24px; }}
    .chart-legend-note {{
      margin: 12px 0 0;
      font-size: 0.78rem;
      color: var(--muted);
    }}
    .site-impact {{
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .site-impact-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 16px;
      font-size: 0.84rem;
    }}
    .site-impact-label {{
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
    }}
    .site-impact-empty {{ font-size: 0.84rem; }}
    .site-impact-details {{
      margin-top: 10px;
      font-size: 0.84rem;
    }}
    .site-impact-details summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 600;
      user-select: none;
    }}
    .site-impact-details .table-wrap {{ margin-top: 10px; }}
    .data-table.compact th, .data-table.compact td {{ padding: 8px 10px; font-size: 0.82rem; }}
    .chart-stack {{ display: flex; flex-direction: column; gap: 24px; }}
    .performance-trend-panel {{
      border-top: 3px solid var(--organic);
    }}
    .performance-trend-head {{
      align-items: flex-start;
      gap: 16px;
    }}
    .performance-trend-head .panel-head-text {{
      flex: 1;
      min-width: 200px;
    }}
    .performance-trend-head h2 {{
      margin: 0 0 6px;
    }}
    .performance-trend-desc {{
      margin: 0;
      font-size: 0.84rem;
      line-height: 1.45;
      max-width: 640px;
    }}
    .performance-trend-controls {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
      gap: 10px 12px;
      flex-shrink: 0;
    }}
    .metric-toggle-group {{
      display: inline-flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
    }}
    .metric-toggle {{
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--muted);
      padding: 7px 14px;
      border-radius: 999px;
      font-size: 0.84rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s, box-shadow 0.12s;
      white-space: nowrap;
    }}
    .metric-toggle:hover {{
      border-color: #b8c4d4;
      color: var(--navy);
    }}
    .metric-toggle.active {{
      font-weight: 600;
      box-shadow: 0 1px 3px rgba(10, 37, 64, 0.1);
    }}
    .metric-toggle[data-metric="spend"].active {{
      background: #eef1f5;
      border-color: var(--navy);
      color: var(--navy);
    }}
    .metric-toggle[data-metric="clicks"].active {{
      background: var(--google-bg);
      border-color: var(--google);
      color: var(--google);
    }}
    .metric-toggle[data-metric="impressions"].active {{
      background: var(--linkedin-bg);
      border-color: var(--linkedin);
      color: var(--linkedin);
    }}
    .metric-toggle[data-metric="conversions"].active {{
      background: var(--organic-bg);
      border-color: var(--organic);
      color: var(--organic);
    }}
    .performance-trend-chart-wrap {{
      position: relative;
      min-height: 320px;
    }}
    .performance-trend-empty {{
      display: none;
      padding: 48px 16px;
      text-align: center;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .performance-trend-empty.show {{
      display: block;
    }}
    .chart-subhead {{
      margin: 0 0 8px;
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--navy);
    }}
    .chart-period-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 0;
      padding: 3px;
      background: #eef3f9;
      border: 1px solid var(--border);
      border-radius: 10px;
    }}
    .chart-period-btn {{
      appearance: none;
      border: none;
      background: transparent;
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 8px;
      cursor: pointer;
      transition: background 0.12s, color 0.12s, box-shadow 0.12s;
    }}
    .chart-period-btn:hover {{
      color: var(--navy);
    }}
    .chart-period-btn.active {{
      background: #fff;
      color: var(--navy);
      box-shadow: 0 1px 3px rgba(10, 37, 64, 0.1);
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 22px 24px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-sm);
    }}
    .panel.aggregated {{
      background: linear-gradient(to right, #fafbfd, var(--panel));
      border-left: 4px solid var(--gold);
    }}
    .panel-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 16px;
      margin-bottom: 12px;
    }}
    h2 {{ margin: 0; font-size: 1.12rem; font-weight: 650; letter-spacing: -0.01em; }}
    .platform-panel {{ border-left: 4px solid var(--border); }}
    .platform-google {{ border-left-color: var(--google); }}
    .platform-linkedin {{ border-left-color: var(--linkedin); }}
    .platform-meta {{ border-left-color: var(--meta); }}
    .badge {{
      font-size: 0.72rem;
      font-weight: 600;
      background: #eef3f9;
      color: var(--accent);
      padding: 3px 10px;
      border-radius: 999px;
      white-space: nowrap;
    }}
    .table-wrap {{ overflow-x: auto; margin: 0 -4px; }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    .data-table th, .data-table td {{ padding: 11px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
    .data-table th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      background: #f8fafc;
      position: sticky;
      top: 0;
    }}
    .data-table th.sortable {{
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      transition: color 0.12s;
    }}
    .data-table th.sortable:hover {{ color: var(--accent); }}
    .data-table th.sortable.sort-active {{ color: var(--accent); }}
    .sort-icon {{
      display: inline-block;
      width: 0.9em;
      margin-left: 2px;
      opacity: 0.35;
      font-size: 0.85em;
    }}
    .data-table th.sortable.sort-active .sort-icon {{ opacity: 1; }}
    .data-table th.sort-active[data-sort-dir="asc"] .sort-icon::before {{ content: '↑'; }}
    .data-table th.sort-active[data-sort-dir="desc"] .sort-icon::before {{ content: '↓'; }}
    .data-table th.sortable:not(.sort-active) .sort-icon::before {{ content: '↕'; }}
    .data-table tbody tr:last-child td {{ border-bottom: none; }}
    .data-table th.ga4-col, .data-table td.ga4-col {{
      border-left: 1px solid var(--border);
      background: rgba(184, 146, 46, 0.04);
    }}
    .data-table thead th.ga4-col {{
      color: #8a6d1f;
      font-size: 0.78rem;
      white-space: nowrap;
    }}
    .data-table td.ga4-col.muted {{ color: #9aa3ad; }}
    td.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    td.name {{ max-width: 340px; font-weight: 500; }}
    td.chevron, th.chevron-col {{
      width: 28px;
      padding-right: 0;
      color: var(--muted);
      font-size: 1.1rem;
    }}
    tr.tree-row {{ transition: background 0.12s; }}
    tr.tree-expandable {{ cursor: pointer; }}
    tr.tree-expandable:hover {{ background: #f0f5fb; }}
    tr.tree-row.expanded {{ background: #e8f0fa; }}
    tr.tree-row.expanded > td {{ border-bottom-color: #c5d9ef; }}
    tr.tree-empty {{ background: #fafbfc; }}
    tr.tree-empty td {{ font-size: 0.84rem; font-style: italic; }}
    tr.tree-depth-1 {{ background: #fafcfe; }}
    tr.tree-depth-2 {{ background: #f5f9fd; }}
    tr.tree-depth-3 {{ background: #f0f6fc; }}
    .tree-chevron {{
      display: inline-block;
      width: 1em;
      color: var(--accent);
      font-size: 0.85rem;
      font-weight: 700;
      transition: transform 0.15s;
    }}
    .tree-chevron.leaf {{ visibility: hidden; }}
    tr.expanded .tree-chevron {{ transform: rotate(90deg); }}
    td.name {{ max-width: 420px; }}
    .name-inner {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .ad-thumb {{
      width: 40px;
      height: 40px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--border);
      flex-shrink: 0;
      background: #f0f4f8;
      display: block;
    }}
    .ad-thumb-btn {{
      position: relative;
      appearance: none;
      border: none;
      padding: 0;
      background: none;
      cursor: pointer;
      flex-shrink: 0;
      border-radius: 8px;
      line-height: 0;
    }}
    .ad-thumb-btn:hover .ad-thumb {{ box-shadow: 0 0 0 2px var(--accent); }}
    .ad-thumb-btn.has-video .ad-play-icon {{
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.65rem;
      color: #fff;
      background: rgba(0,0,0,0.45);
      border-radius: 8px;
      pointer-events: none;
    }}
    .creative-preview {{
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .creative-preview[hidden] {{ display: none !important; }}
    .creative-preview-backdrop {{
      position: absolute;
      inset: 0;
      background: rgba(10, 37, 64, 0.72);
    }}
    .creative-preview-dialog {{
      position: relative;
      background: #fff;
      border-radius: 14px;
      max-width: min(720px, 96vw);
      max-height: 90vh;
      overflow: hidden;
      box-shadow: var(--shadow);
      width: 100%;
    }}
    .creative-preview-close {{
      position: absolute;
      top: 8px;
      right: 10px;
      z-index: 2;
      appearance: none;
      border: none;
      background: rgba(0,0,0,0.55);
      color: #fff;
      width: 32px;
      height: 32px;
      border-radius: 999px;
      font-size: 1.25rem;
      line-height: 1;
      cursor: pointer;
    }}
    .creative-preview-body {{
      background: #000;
      min-height: 200px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .creative-preview-body iframe,
    .creative-preview-body video,
    .creative-preview-body img {{
      display: block;
      max-width: 100%;
      max-height: 80vh;
      width: 100%;
    }}
    .creative-preview-body img {{ background: #fff; }}
    .creative-preview-caption {{
      padding: 12px 16px;
      font-size: 0.84rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
    }}
    .ad-creative-sub {{
      display: block;
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 2px;
    }}
    .entity-tag {{
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
      margin-right: 6px;
    }}
    .drill-hint {{
      margin: 0 0 12px;
      font-size: 0.82rem;
      color: var(--accent);
      font-weight: 500;
    }}
    .table-note {{ margin: 0 0 10px; font-size: 0.82rem; color: var(--muted); line-height: 1.4; }}
    .aggregated-stats {{ display: flex; flex-wrap: wrap; gap: 16px 28px; font-size: 1.02rem; margin-top: 4px; }}
    .aggregated-stats strong {{ font-size: 1.35rem; color: var(--navy); }}
    .errors {{
      background: #fff8e6;
      border: 1px solid #f0d080;
      border-radius: var(--radius);
      padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 0.88rem;
    }}
    .errors ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .dash-flash {{
      background: var(--ok-bg);
      border: 1px solid #b8dfc8;
      border-radius: var(--radius);
      padding: 12px 14px;
      margin-bottom: 16px;
      font-size: 0.9rem;
      color: var(--ok);
    }}
    canvas {{ max-height: 280px; }}
    .muted {{ color: var(--muted); }}
    .refresh-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }}
    .refresh-actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .refresh-form {{ margin: 0; }}
    .refresh-btn {{
      padding: 9px 18px;
      border-radius: 9px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font-size: 0.88rem;
      font-weight: 600;
      transition: background 0.15s;
    }}
    .refresh-btn:hover:not(:disabled) {{ filter: brightness(1.08); }}
    .refresh-btn:disabled {{ opacity: 0.45; cursor: not-allowed; background: #94a3b8; border-color: #94a3b8; }}
    .refresh-btn--secondary {{
      background: #fff; color: var(--accent); border-color: var(--accent);
    }}
    .refresh-btn--secondary:hover:not(:disabled) {{ background: #f0f7ff; filter: none; }}
    .notice {{ font-size: 0.86rem; color: var(--muted); }}

    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .view-panel {{ display: none; }}
    .view-panel.active {{ display: block; }}
    .dash-sticky-chrome {{
      position: sticky;
      top: 0;
      z-index: 45;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      box-shadow: 0 2px 12px rgba(10, 37, 64, 0.04);
    }}
    .dash-page-header {{
      max-width: 1280px;
      margin: 0 auto;
      width: 100%;
      padding: 0 24px;
    }}
    .dash-view-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding: 8px 0 0;
      background: transparent;
      border-bottom: 1px solid var(--border);
    }}
    .dash-view-btn {{
      appearance: none;
      border: none;
      background: transparent;
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 650;
      padding: 10px 14px 12px;
      border-radius: 8px 8px 0 0;
      cursor: pointer;
      border-bottom: 3px solid transparent;
      margin-bottom: -1px;
      transition: color 0.15s, border-color 0.15s, background 0.15s;
    }}
    .dash-view-btn:hover {{
      color: var(--navy);
      background: var(--surface);
    }}
    .dash-view-btn.active {{
      color: var(--accent);
      border-bottom-color: var(--accent);
      background: transparent;
    }}
    .dash-filters-bar {{
      padding: 16px 0 18px;
      background: transparent;
    }}
    .dash-filters-bar:empty {{
      display: none;
    }}
    .global-filters {{
      padding: 14px 16px;
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: none;
      background: var(--surface);
    }}
    .global-filter-rows {{
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px 14px;
    }}
    .filter-row .filter-label {{
      min-width: 92px;
      margin: 0;
      flex-shrink: 0;
    }}
    .filter-toggles {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      flex: 1;
      min-width: 0;
    }}
    .website-search-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px 16px;
      margin-bottom: 16px;
      padding: 12px 14px;
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 12px;
    }}
    .website-search-bar .filter-label {{
      min-width: auto;
      margin: 0;
    }}
    .website-search-bar .ga4-pages-search {{
      flex: 1;
      min-width: 220px;
      max-width: 520px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font: inherit;
      background: #fff;
    }}
    .ga4-metrics-section {{
      margin-bottom: 20px;
    }}
    .ga4-metrics-heading {{
      margin-bottom: 12px;
    }}
    .ga4-metrics-pill {{
      display: inline-flex;
      align-items: center;
      gap: 0;
      background: var(--navy);
      color: #fff;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      padding: 6px 14px 6px 0;
      border-radius: 999px;
      overflow: hidden;
    }}
    .ga4-metrics-pill::before {{
      content: '';
      display: inline-block;
      width: 4px;
      align-self: stretch;
      background: #22c55e;
      margin-right: 10px;
      border-radius: 999px 0 0 999px;
    }}
    .ga4-metrics-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
    }}
    @media (max-width: 1200px) {{
      .ga4-metrics-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .ga4-metrics-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    .ga4-metric-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: var(--shadow-sm);
      min-width: 0;
    }}
    .ga4-metric-label {{
      font-size: 0.68rem;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 8px;
    }}
    .ga4-metric-value {{
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--navy);
      line-height: 1.15;
    }}
    .ga4-metric-sub {{
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 8px;
    }}

    .filter-panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 18px;
      box-shadow: var(--shadow-sm);
    }}
    .bl-merged-section {{
      margin-top: 0;
    }}
    .campaign-explorer-section {{
      margin-top: 0;
    }}
    .bl-filter-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px 24px;
    }}
    @media (max-width: 960px) {{
      .bl-filter-grid {{ grid-template-columns: 1fr; }}
    }}
    .bl-filter-footer {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px 16px;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
    }}
    .bl-filter-footer .filter-zero-spend {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }}
    .bl-filter-footer .filter-status {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
      flex: 1;
      min-width: 200px;
      text-align: right;
    }}
    @media (max-width: 720px) {{
      .bl-filter-footer .filter-status {{ text-align: left; }}
    }}
    .filter-checks--wrap {{
      flex-direction: row;
      flex-wrap: wrap;
      gap: 6px 10px;
      max-height: 120px;
      overflow-y: auto;
      padding-right: 4px;
    }}
    .filter-group-head {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .filter-group-head .filter-label {{
      min-width: 0;
      flex: 1;
    }}
    .filter-link {{
      appearance: none;
      border: none;
      background: none;
      color: var(--accent);
      font-size: 0.76rem;
      font-weight: 600;
      cursor: pointer;
      padding: 2px 4px;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .filter-link:hover {{ color: var(--navy); }}
    .filter-checks {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      max-height: none;
      overflow: visible;
      padding-right: 0;
    }}
    .filter-check {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.86rem;
      cursor: pointer;
      user-select: none;
      padding: 4px 6px;
      border-radius: 8px;
    }}
    .filter-check:hover {{ background: #f4f7fb; }}
    .filter-check input {{
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
      flex-shrink: 0;
    }}
    .filter-zero-spend {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      font-size: 0.84rem;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
    }}
    .filter-zero-spend input {{
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
    }}
    .filter-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px 20px;
    }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 10px;
      flex: 1;
      min-width: 260px;
    }}
    .filter-label {{
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--muted);
      min-width: 88px;
    }}
    .filter-toggle {{
      appearance: none;
      border: 1.5px solid var(--border);
      background: #fff;
      color: var(--text);
      padding: 7px 14px;
      border-radius: 999px;
      font-size: 0.84rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s, box-shadow 0.12s;
      white-space: nowrap;
    }}
    .filter-toggle:hover {{ border-color: #b8c4d4; background: #f8fafc; }}
    .filter-toggle.active {{
      color: #fff;
      font-weight: 600;
      box-shadow: 0 1px 3px rgba(10, 37, 64, 0.12);
    }}
    .filter-toggle--all {{
      border-color: var(--navy);
      color: var(--navy);
      font-weight: 600;
    }}
    .filter-toggle--all:hover {{
      background: #eef1f5;
      border-color: var(--navy);
    }}
    .filter-toggle--all.active {{
      background: var(--navy);
      border-color: var(--navy);
      color: #fff;
    }}
    .filter-toggle.t-google {{
      border-color: color-mix(in srgb, var(--google) 55%, var(--border));
      color: var(--google);
    }}
    .filter-toggle.t-google:hover {{
      background: #eef4ff;
      border-color: var(--google);
    }}
    .filter-toggle.t-google.active {{
      background: var(--google);
      border-color: var(--google);
      color: #fff;
    }}
    .filter-toggle.t-linkedin {{
      border-color: color-mix(in srgb, var(--linkedin) 55%, var(--border));
      color: var(--linkedin);
    }}
    .filter-toggle.t-linkedin:hover {{
      background: var(--linkedin-bg);
      border-color: var(--linkedin);
    }}
    .filter-toggle.t-linkedin.active {{
      background: var(--linkedin);
      border-color: var(--linkedin);
      color: #fff;
    }}
    .filter-toggle.t-meta {{
      border-color: color-mix(in srgb, var(--meta) 55%, var(--border));
      color: var(--meta);
    }}
    .filter-toggle.t-meta:hover {{
      background: var(--meta-bg);
      border-color: var(--meta);
    }}
    .filter-toggle.t-meta.active {{
      background: var(--meta);
      border-color: var(--meta);
      color: #fff;
    }}
    .filter-toggle.t-organic {{
      border-color: color-mix(in srgb, var(--organic) 55%, var(--border));
      color: var(--organic);
    }}
    .filter-toggle.t-organic:hover {{
      background: var(--organic-bg);
      border-color: var(--organic);
    }}
    .filter-toggle.t-organic.active {{
      background: var(--organic);
      border-color: var(--organic);
      color: #fff;
    }}
    .filter-toggle.t-bl {{
      border-color: color-mix(in srgb, var(--business-line) 50%, var(--border));
      color: var(--business-line);
    }}
    .filter-toggle.t-bl:hover {{
      background: var(--business-line-bg);
      border-color: var(--business-line);
    }}
    .filter-toggle.t-bl.active {{
      background: var(--business-line);
      border-color: var(--business-line);
      color: #fff;
    }}
    .filter-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }}
    .filter-reset {{
      appearance: none;
      border: none;
      background: none;
      color: var(--accent);
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      padding: 6px 4px;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .filter-reset:hover {{ color: var(--navy); }}
    .filter-status {{
      font-size: 0.82rem;
      color: var(--muted);
    }}
    .bl-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .bl-stat {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      box-shadow: var(--shadow-sm);
    }}
    .bl-stat-val {{ font-size: 1.35rem; font-weight: 700; }}
    .bl-stat-lbl {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin-top: 4px; }}
    .attr-platform-section {{ margin-top: 8px; }}
    .attr-platform-summary {{ margin: 16px 0; }}
    .attr-subhead {{
      margin: 20px 0 10px;
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .platform-pill {{
      display: inline-block;
      font-size: 0.68rem;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 999px;
      text-transform: uppercase;
      letter-spacing: .03em;
    }}
    .platform-pill.google {{ background: var(--google-bg); color: var(--google); }}
    .platform-pill.meta {{ background: var(--meta-bg); color: var(--meta); }}
    .platform-pill.linkedin {{ background: var(--linkedin-bg); color: var(--linkedin); }}
    .platform-pill.organic {{ background: var(--organic-bg); color: var(--organic); }}
    .bl-tag {{
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--business-line);
      background: var(--business-line-bg);
      padding: 2px 8px;
      border-radius: 6px;
      white-space: nowrap;
    }}
    .bl-filters h3 {{
      margin: 0 0 14px;
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--navy);
    }}
    .bl-filters .filter-group + .filter-group {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }}
    .andre-toast {{
      position: fixed;
      bottom: 28px;
      left: 50%;
      transform: translateX(-50%) translateY(12px);
      background: var(--navy);
      color: #fff;
      padding: 12px 20px;
      border-radius: 999px;
      font-size: 0.92rem;
      font-weight: 600;
      box-shadow: 0 8px 32px rgba(10, 37, 64, 0.28);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.35s ease, transform 0.35s ease;
      z-index: 200;
    }}
    .andre-toast.show {{
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }}
    @media (max-width: 900px) {{
      .app-shell {{ display: block; }}
      .dash-page-header {{ padding: 0 16px; }}
      .dash-view-nav {{ padding-top: 6px; }}
      .dash-filters-bar {{ padding: 14px 0 16px; }}
      .sidebar-menu-btn {{ display: flex; }}
      .sidebar-close {{ display: flex; }}
      .sidebar-backdrop {{
        display: block;
      }}
      body.sidebar-open .sidebar-backdrop {{
        opacity: 1;
        visibility: visible;
        pointer-events: auto;
      }}
      body.sidebar-open {{
        overflow: hidden;
      }}
      .dash-sidebar {{
        position: fixed;
        left: 0;
        top: 0;
        height: 100%;
        height: 100dvh;
        width: min(88vw, 280px);
        transform: translateX(-105%);
        z-index: 110;
        box-shadow: 8px 0 40px rgba(10, 37, 64, 0.25);
        pointer-events: none;
      }}
      body.sidebar-open .dash-sidebar {{
        transform: translateX(0);
        pointer-events: auto;
      }}
      .dash-main {{
        width: 100%;
        min-width: 0;
      }}
      .dash-content {{ padding: 18px 16px 40px; }}
      .dash-chrome-bar {{ display: flex; }}
    }}
  </style>
</head>
<body>
  <button type="button" class="sidebar-backdrop" id="sidebarBackdrop" aria-label="Close menu" tabindex="-1"></button>
  <div class="app-shell">
    <aside class="dash-sidebar" id="dashSidebar" aria-label="Dashboard navigation">
      <div class="sidebar-top">
        {client_header}
        <button type="button" class="sidebar-close" id="sidebarClose" aria-label="Close menu">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>
      {sidebar_nav}
      <div class="sidebar-footer">
        {account_nav}
      </div>
    </aside>

    <div class="dash-main">
      <div class="dash-sticky-chrome">
        <div class="dash-chrome-bar">
          <button type="button" class="sidebar-menu-btn" id="sidebarOpen" aria-label="Open menu" aria-expanded="false" aria-controls="dashSidebar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
          </button>
        </div>
        <div class="dash-page-header">
          {view_tabs_html}
          {filters_bar_html}
        </div>
      </div>

      <div class="dash-content">
        <div class="wrap">
          {flash_html}
          {error_html}

          <div id="view-overview" class="view-panel active" role="tabpanel">
            {overview_paid_html}
            {client_insights_html}

            <div class="cards">
              {_summary_card("Google Ads", totals.get("google"), platform="google")}
              {_summary_card("LinkedIn", totals.get("linkedin"), platform="linkedin")}
              {_summary_card("Meta", totals.get("meta"), platform="meta")}
            </div>

            <section class="panel performance-trend-panel">
              <div class="panel-head performance-trend-head">
                <div class="panel-head-text">
                  <h2>Daily Paid Performance Trend</h2>
                  <p class="performance-trend-desc muted">Daily combined paid-media totals across selected channels. Choose one metric at a time.</p>
                </div>
                <div class="performance-trend-controls">
                  <div class="metric-toggle-group" role="group" aria-label="Chart metric">
                    <button type="button" class="metric-toggle active" data-metric="spend" aria-pressed="true">Spend</button>
                    <button type="button" class="metric-toggle" data-metric="clicks" aria-pressed="false">Clicks</button>
                    <button type="button" class="metric-toggle" data-metric="impressions" aria-pressed="false">Impressions</button>
                    <button type="button" class="metric-toggle" data-metric="conversions" aria-pressed="false">Conversions</button>
                  </div>
                </div>
              </div>
              <div class="performance-trend-chart-wrap">
                <p class="performance-trend-empty" id="performanceTrendEmpty">Choose a metric to display the chart.</p>
                <canvas id="performanceTrendChart"></canvas>
              </div>
            </section>
          </div>

          <div id="view-campaigns" class="view-panel" role="tabpanel" hidden>
            {campaign_explorer_html}
          </div>

          {website_tab_panel}
        </div>
      </div>
    </div>
  </div>
  <div id="creativePreview" class="creative-preview" hidden>
    <div class="creative-preview-backdrop" data-close-preview></div>
    <div class="creative-preview-dialog" role="dialog" aria-modal="true" aria-label="Creative preview">
      <button type="button" class="creative-preview-close" data-close-preview aria-label="Close">×</button>
      <div class="creative-preview-body" id="creativePreviewBody"></div>
      <div class="creative-preview-caption" id="creativePreviewCaption"></div>
    </div>
  </div>
  <div class="andre-toast" id="andreToast" role="status" aria-live="polite" hidden>Hello Andre</div>
  <script type="application/json" id="performance-chart-data">{performance_chart_json}</script>
  <script type="application/json" id="metric-defs-data">{metric_defs_json}</script>
  <script type="application/json" id="paid-overview-metrics-data">{paid_overview_metrics_json}</script>
  <script type="application/json" id="breakdowns-data">{breakdowns_json}</script>
  <script type="application/json" id="ga4-campaign-metrics">{ga4_campaign_metrics_json}</script>
  <script type="application/json" id="bl-campaigns-data">{bl_campaigns_json}</script>
  <script type="application/json" id="bl-catalog-data">{bl_catalog_json}</script>
  <script type="application/json" id="platform-catalog-data">{platform_catalog_json}</script>
  <script type="application/json" id="ga4-pages-data">{ga4_pages_json}</script>
  <script type="application/json" id="ga4-summary-data">{ga4_summary_json}</script>
  <script>
    function readJson(id, fallback) {{
      const el = document.getElementById(id);
      if (!el) return fallback;
      try {{
        return JSON.parse(el.textContent || 'null') ?? fallback;
      }} catch (err) {{
        console.error('Failed to parse', id, err);
        return fallback;
      }}
    }}

    const SHOW_BUSINESS_LINE = {'true' if show_business_line else 'false'};

    const breakdowns = readJson('breakdowns-data', {{}});
    const ga4CampaignMetrics = readJson('ga4-campaign-metrics', {{}});
    const blCampaigns = readJson('bl-campaigns-data', []);
    const blCatalog = readJson('bl-catalog-data', []);
    const platformCatalog = readJson('platform-catalog-data', []);
    const ga4Pages = readJson('ga4-pages-data', []);
    const ga4SiteSummary = readJson('ga4-summary-data', {{}});

    const channelState = new Set();
    const blState = new Set();

    function isAllSelected(state, catalog) {{
      return !catalog.length || state.size === 0 || state.size === catalog.length;
    }}

    function effectiveFilterIds(state, catalog) {{
      if (isAllSelected(state, catalog)) return catalog.map(item => item.id);
      return [...state];
    }}

    function channelFilterRestricts() {{
      return channelState.size > 0 && channelState.size < platformCatalog.length;
    }}

    function activeChartPlatforms() {{
      const ids = effectiveFilterIds(channelState, platformCatalog);
      const fromMetrics = performanceChartRaw.metrics?.clicks
        ? Object.keys(performanceChartRaw.metrics.clicks)
        : [];
      if (!fromMetrics.length) return ids;
      return ids.filter(id => fromMetrics.includes(id));
    }}

    const METRIC_DEFS = readJson('metric-defs-data', []);
    let activeMetricId = 'spend';

    const fmtMoney = n => '$' + Number(n || 0).toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    const fmtInt = n => Number(n || 0).toLocaleString();
    const fmtPct = (n, d) => d ? (100 * n / d).toFixed(2) + '%' : '—';
    const escHtml = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    const performanceChartRaw = readJson('performance-chart-data', {{ labels: [], metrics: {{}} }});
    let performanceChartInstance = null;

    function formatDailyLabel(dateStr) {{
      const s = String(dateStr).slice(0, 10);
      if (s.length < 10) return s;
      return `${{s.slice(5, 7)}}-${{s.slice(8, 10)}}`;
    }}

    function combineMetricSeries(metricId) {{
      const platforms = activeChartPlatforms();
      const byPlatform = performanceChartRaw.metrics?.[metricId] || {{}};
      const len = performanceChartRaw.labels?.length || 0;
      const out = new Array(len).fill(0);
      for (const platform of platforms) {{
        const series = byPlatform[platform] || [];
        for (let i = 0; i < len; i += 1) {{
          out[i] += Number(series[i] || 0);
        }}
      }}
      return out;
    }}

    function buildPerformancePayload() {{
      const def = METRIC_DEFS.find(m => m.id === activeMetricId) || METRIC_DEFS[0];
      const labels = [...(performanceChartRaw.labels || [])].map(formatDailyLabel);
      return {{
        labels,
        datasets: [{{
          label: def.label,
          data: combineMetricSeries(def.id),
          borderColor: def.color,
          backgroundColor: def.fill || def.color,
          fill: !!def.fill,
          yAxisID: def.yAxisID,
          tension: 0.35,
          borderWidth: 2.5,
          pointRadius: 2,
          pointHoverRadius: 4,
        }}],
      }};
    }}

    function performanceChartScales() {{
      const def = METRIC_DEFS.find(m => m.id === activeMetricId) || METRIC_DEFS[0];
      const ticks = def.format === 'money'
        ? {{ callback: v => '$' + Number(v).toLocaleString() }}
        : {{ callback: v => Number(v).toLocaleString() }};
      return {{
        x: {{ grid: {{ display: false }} }},
        y: {{
          type: 'linear',
          position: 'left',
          beginAtZero: true,
          ticks,
          title: {{ display: true, text: def.label }},
        }},
      }};
    }}

    function formatTooltipValue(def, raw) {{
      if (def.format === 'money') return fmtMoney(raw);
      if (def.id === 'conversions') {{
        const n = Number(raw || 0);
        return Number.isInteger(n) ? fmtInt(n) : n.toLocaleString(undefined, {{ maximumFractionDigits: 2 }});
      }}
      return fmtInt(raw);
    }}

    function refreshCharts() {{
      const emptyEl = document.getElementById('performanceTrendEmpty');
      const canvas = document.getElementById('performanceTrendChart');
      if (!canvas) return;

      if (performanceChartInstance) {{
        performanceChartInstance.destroy();
        performanceChartInstance = null;
      }}

      const hasMetrics = !!activeMetricId;
      const hasData = (performanceChartRaw.labels || []).length > 0;
      if (emptyEl) {{
        emptyEl.classList.toggle('show', !hasMetrics || !hasData);
        emptyEl.textContent = !hasMetrics
          ? 'Choose a metric to display the chart.'
          : 'No daily performance data for this period.';
      }}
      canvas.hidden = !hasMetrics || !hasData;
      if (!hasMetrics || !hasData) return;

      const chartData = buildPerformancePayload();
      performanceChartInstance = new Chart(canvas, {{
        type: 'line',
        data: chartData,
        options: {{
          responsive: true,
          maintainAspectRatio: true,
          interaction: {{ mode: 'index', intersect: false }},
          scales: performanceChartScales(),
          plugins: {{
            legend: {{
              position: 'bottom',
              align: 'start',
              labels: {{
                usePointStyle: true,
                pointStyle: 'circle',
                boxWidth: 8,
                boxHeight: 8,
                padding: 18,
                color: '#64748b',
                font: {{ size: 12, weight: '500', family: "'Segoe UI', system-ui, sans-serif" }},
              }},
            }},
            tooltip: {{
              callbacks: {{
                label(context) {{
                  const def = METRIC_DEFS.find(m => m.label === context.dataset.label);
                  const value = formatTooltipValue(def || {{ format: 'int' }}, context.parsed.y);
                  return `${{context.dataset.label}}: ${{value}}`;
                }},
              }},
            }},
          }},
          elements: {{ line: {{ tension: 0.35, borderWidth: 2 }}, point: {{ radius: 2, hitRadius: 8 }} }},
        }},
      }});
    }}

    function syncMetricToggleButtons() {{
      document.querySelectorAll('.metric-toggle').forEach(btn => {{
        const on = btn.dataset.metric === activeMetricId;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }});
    }}

    document.querySelectorAll('.metric-toggle').forEach(btn => {{
      btn.addEventListener('click', () => {{
        const metric = btn.dataset.metric;
        if (!metric || metric === activeMetricId) return;
        activeMetricId = metric;
        syncMetricToggleButtons();
        refreshCharts();
      }});
    }});
    syncMetricToggleButtons();

    const MOBILE_SIDEBAR_MQ = window.matchMedia('(max-width: 900px)');
    function isMobileSidebar() {{
      return MOBILE_SIDEBAR_MQ.matches;
    }}
    function setSidebarOpen(open) {{
      document.body.classList.toggle('sidebar-open', open);
      document.getElementById('sidebarOpen')?.setAttribute('aria-expanded', open ? 'true' : 'false');
    }}
    function openSidebar() {{
      if (!isMobileSidebar()) return;
      setSidebarOpen(true);
    }}
    function closeSidebar() {{
      setSidebarOpen(false);
    }}
    function toggleSidebar(e) {{
      if (e) {{
        e.preventDefault();
        e.stopPropagation();
      }}
      if (!isMobileSidebar()) return;
      setSidebarOpen(!document.body.classList.contains('sidebar-open'));
    }}
    document.getElementById('sidebarOpen')?.addEventListener('click', toggleSidebar);
    document.getElementById('sidebarClose')?.addEventListener('click', closeSidebar);
    document.getElementById('sidebarBackdrop')?.addEventListener('click', closeSidebar);
    MOBILE_SIDEBAR_MQ.addEventListener('change', () => {{
      if (!isMobileSidebar()) closeSidebar();
    }});
    document.addEventListener('keydown', e => {{
      if (e.key === 'Escape' && document.body.classList.contains('sidebar-open')) {{
        closeSidebar();
      }}
    }});
    document.getElementById('clientSwitcher')?.addEventListener('change', (e) => {{
      const url = e.target.value;
      if (url) window.location.href = url;
    }});

    let andreClicks = 0;
    let andreTimer = null;
    function showAndreToast() {{
      const toast = document.getElementById('andreToast');
      if (!toast) return;
      toast.hidden = false;
      toast.classList.add('show');
      clearTimeout(toast._hideTimer);
      toast._hideTimer = setTimeout(() => {{
        toast.classList.remove('show');
        setTimeout(() => {{ toast.hidden = true; }}, 400);
      }}, 2800);
    }}
    document.getElementById('sidebarClientHead')?.addEventListener('click', () => {{
      andreClicks += 1;
      clearTimeout(andreTimer);
      andreTimer = setTimeout(() => {{ andreClicks = 0; }}, 1600);
      if (andreClicks >= 3) {{
        andreClicks = 0;
        showAndreToast();
      }}
    }});

    const VIEW_LABELS = {{
      overview: 'Overview',
      campaigns: 'Campaign Explorer',
      website: 'Website Analytics',
    }};

    function setActiveView(view) {{
      const allowed = ['overview', 'campaigns', 'website'];
      if (!allowed.includes(view)) view = 'overview';
      if (view === 'website' && !document.querySelector('.dash-view-btn[data-view="website"]')) {{
        view = 'overview';
      }}
      document.querySelectorAll('.dash-view-btn').forEach(btn => {{
        const on = btn.dataset.view === view;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
      }});
      document.querySelectorAll('.view-panel').forEach(panel => {{
        const on = panel.id === 'view-' + view;
        panel.classList.toggle('active', on);
        panel.hidden = !on;
      }});
      const titleSuffix = VIEW_LABELS[view] || 'Dashboard';
      document.title = `${{document.title.split(' — ')[0]}} — ${{titleSuffix}}`;
      try {{
        const url = new URL(window.location.href);
        if (view === 'overview') {{
          url.searchParams.delete('view');
        }} else {{
          url.searchParams.set('view', view);
        }}
        history.replaceState(null, '', url);
      }} catch (err) {{
        /* ignore */
      }}
      closeSidebar();
    }}

    document.querySelectorAll('.dash-view-btn').forEach(btn => {{
      btn.addEventListener('click', () => setActiveView(btn.dataset.view || 'overview'));
    }});

    const initialView = new URLSearchParams(window.location.search).get('view');
    if (initialView && ['overview', 'campaigns', 'website'].includes(initialView)) {{
      setActiveView(initialView);
    }}

    const blSort = {{ key: 'spend', dir: 'desc' }};
    const overviewCardDefaults = new Map();
    document.querySelectorAll('.cards .card[data-platform]').forEach(card => {{
      overviewCardDefaults.set(card.dataset.platform, {{
        valueHtml: card.querySelector('.card-value')?.innerHTML || '',
        statsHtml: card.querySelector('.card-stats')?.innerHTML || '',
      }});
    }});
    const paidOverviewDefaults = readJson('paid-overview-metrics-data', {{}});
    const ga4PaidKeyEvents = Number(paidOverviewDefaults.ga4_key_events || 0);

    function fmtCpa(spend, count) {{
      const n = Number(count || 0);
      if (!n) return '—';
      return fmtMoney(Number(spend || 0) / n);
    }}

    function updatePaidOverviewMetrics(totals) {{
      const spendEl = document.getElementById('heroSpend');
      if (!spendEl) return;
      const m = totals || paidOverviewDefaults;
      const spend = Number(m.spend || 0);
      const clicks = Number(m.clicks || 0);
      const impressions = Number(m.impressions || 0);
      const conversions = Number(m.conversions || 0);
      const ga4Events = totals ? ga4PaidKeyEvents : Number(m.ga4_key_events || ga4PaidKeyEvents);

      spendEl.textContent = fmtMoney(spend);
      const impEl = document.getElementById('heroImpressions');
      const clkEl = document.getElementById('heroClicks');
      const ctrEl = document.getElementById('heroCtr');
      const cpcEl = document.getElementById('heroCpc');
      const convEl = document.getElementById('heroConversions');
      const repCpaEl = document.getElementById('heroReportedCpa');
      const verCpaEl = document.getElementById('heroVerifiedCpa');
      if (impEl) impEl.textContent = fmtInt(impressions);
      if (clkEl) clkEl.textContent = fmtInt(clicks);
      if (ctrEl) ctrEl.textContent = impressions ? fmtPct(clicks, impressions) : '—';
      if (cpcEl) cpcEl.textContent = clicks ? fmtCpa(spend, clicks) : '—';
      if (convEl) convEl.textContent = fmtInt(conversions);
      if (repCpaEl) repCpaEl.textContent = conversions ? fmtCpa(spend, conversions) : '—';
      if (verCpaEl) verCpaEl.textContent = ga4Events ? fmtCpa(spend, ga4Events) : '—';
    }}

    function platformVisible(platformId) {{
      if (platformId === 'organic') {{
        return effectiveFilterIds(channelState, platformCatalog).includes('organic');
      }}
      return effectiveFilterIds(channelState, platformCatalog).includes(platformId);
    }}

    function filteredBlCampaigns() {{
      const showZeroSpend = !!document.getElementById('showZeroSpend')?.checked;
      const channels = effectiveFilterIds(channelState, platformCatalog).filter(p => p !== 'organic');
      const bls = effectiveFilterIds(blState, blCatalog);
      if (!blCatalog.length || !channels.length || !bls.length) return [];
      return blCampaigns.filter(r => {{
        if (!channels.includes(r.platform) || !bls.includes(r.business_line)) return false;
        if (!showZeroSpend && (r.spend || 0) < 0.01) return false;
        return true;
      }});
    }}

    function aggregatePlatformTotals(platformId, rows) {{
      const subset = rows.filter(r => r.platform === platformId);
      if (!subset.length) return null;
      return subset.reduce((acc, r) => ({{
        spend: acc.spend + (r.spend || 0),
        clicks: acc.clicks + (r.clicks || 0),
        impressions: acc.impressions + (r.impressions || 0),
        conversions: acc.conversions + (r.conversions || 0),
      }}), {{ spend: 0, clicks: 0, impressions: 0, conversions: 0 }});
    }}

    function updateOverviewCard(platformId, totals) {{
      const card = document.querySelector(`.cards .card[data-platform="${{platformId}}"]`);
      if (!card) return;
      const defaults = overviewCardDefaults.get(platformId);
      const valueEl = card.querySelector('.card-value');
      const statsEl = card.querySelector('.card-stats');
      if (!valueEl || !statsEl || !defaults) return;
      if (!totals) {{
        valueEl.innerHTML = defaults.valueHtml;
        statsEl.innerHTML = defaults.statsHtml;
        return;
      }}
      valueEl.textContent = fmtMoney(totals.spend);
      statsEl.innerHTML = `
        <span>${{fmtInt(totals.clicks)}} clicks</span>
        <span>${{fmtInt(totals.impressions)}} impr.</span>
        <span>${{fmtInt(totals.conversions)}} conv.</span>`;
    }}

    function updateHeroTotals(totals) {{
      updatePaidOverviewMetrics(totals);
    }}

    function applyOverviewFilters() {{
      const partialBl = SHOW_BUSINESS_LINE && blCatalog.length && !isAllSelected(blState, blCatalog);
      const partialCh = platformCatalog.length && !isAllSelected(channelState, platformCatalog);
      const blFiltered = SHOW_BUSINESS_LINE && (partialBl || partialCh)
        ? filteredBlCampaigns()
        : null;
      let heroTotals = null;

      document.querySelectorAll('.cards .card[data-platform]').forEach(card => {{
        const platformId = card.dataset.platform;
        const visible = platformVisible(platformId);
        card.hidden = !visible;
        card.style.display = visible ? '' : 'none';
        if (!visible) return;
        if (blFiltered) {{
          const totals = aggregatePlatformTotals(platformId, blFiltered);
          updateOverviewCard(platformId, totals);
        }} else {{
          updateOverviewCard(platformId, null);
        }}
      }});

      document.querySelectorAll('.platform-panel[data-platform]').forEach(panel => {{
        const visible = platformVisible(panel.dataset.platform);
        panel.hidden = !visible;
        panel.style.display = visible ? '' : 'none';
      }});

      if (blFiltered) {{
        heroTotals = blFiltered.reduce((acc, r) => ({{
          spend: acc.spend + (r.spend || 0),
          clicks: acc.clicks + (r.clicks || 0),
          impressions: acc.impressions + (r.impressions || 0),
          conversions: acc.conversions + (r.conversions || 0),
        }}), {{ spend: 0, clicks: 0, impressions: 0, conversions: 0 }});
      }}
      updateHeroTotals(heroTotals);
      refreshCharts();
    }}

    let renderGa4Pages = () => {{}};

    function applyGlobalFilters() {{
      applyBlView();
      applyOverviewFilters();
      renderGa4Pages();
    }}

    function blSortValue(r, key) {{
      switch (key) {{
        case 'platform':
          return String(r.platform_label || r.platform || '').toLowerCase();
        case 'business_line':
          return String(r.business_line_label || '').toLowerCase();
        case 'name':
          return String(r.name || '').toLowerCase();
        case 'spend':
          return Number(r.spend || 0);
        case 'clicks':
          return Number(r.clicks || 0);
        case 'impressions':
          return Number(r.impressions || 0);
        case 'ctr':
          return r.impressions ? Number(r.clicks || 0) / Number(r.impressions) : 0;
        case 'conversions':
          return Number(r.conversions || 0);
        case 'cpc':
          return r.clicks ? Number(r.spend || 0) / Number(r.clicks) : 0;
        default:
          return 0;
      }}
    }}

    function sortBlRows(rows) {{
      const {{ key, dir }} = blSort;
      const mul = dir === 'asc' ? 1 : -1;
      const textKeys = new Set(['platform', 'business_line', 'name']);
      return [...rows].sort((a, b) => {{
        const av = blSortValue(a, key);
        const bv = blSortValue(b, key);
        if (textKeys.has(key)) {{
          return mul * String(av).localeCompare(String(bv));
        }}
        return mul * (av - bv);
      }});
    }}

    function updateBlSortHeaders() {{
      document.querySelectorAll('#blTable thead th[data-sort]').forEach(th => {{
        const active = th.dataset.sort === blSort.key;
        th.classList.toggle('sort-active', active);
        if (active) {{
          th.dataset.sortDir = blSort.dir;
          th.setAttribute('aria-sort', blSort.dir === 'asc' ? 'ascending' : 'descending');
        }} else {{
          delete th.dataset.sortDir;
          th.setAttribute('aria-sort', 'none');
        }}
      }});
    }}

    function onBlSortClick(e) {{
      const th = e.target.closest('#blTable thead th[data-sort]');
      if (!th) return;
      const key = th.dataset.sort;
      if (blSort.key === key) {{
        blSort.dir = blSort.dir === 'asc' ? 'desc' : 'asc';
      }} else {{
        blSort.key = key;
        blSort.dir = (key === 'platform' || key === 'business_line' || key === 'name') ? 'asc' : 'desc';
      }}
      applyBlView();
    }}

    function initToggleGroup(wrapId, catalog, state, group) {{
      const wrap = document.getElementById(wrapId);
      if (!wrap) return;
      wrap.innerHTML = '';

      const allBtn = document.createElement('button');
      allBtn.type = 'button';
      allBtn.className = 'filter-toggle filter-toggle--all';
      allBtn.textContent = 'All';
      allBtn.dataset.group = group;
      allBtn.dataset.id = '__all__';
      allBtn.setAttribute('aria-pressed', 'false');
      allBtn.addEventListener('click', () => {{
        const allOn = state.size === catalog.length;
        state.clear();
        if (!allOn) {{
          catalog.forEach(item => state.add(item.id));
        }}
        syncToggleGroup(group);
        applyGlobalFilters();
      }});
      wrap.appendChild(allBtn);

      for (const item of catalog) {{
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'filter-toggle';
        if (group === 'channel') {{
          btn.classList.add(`t-${{item.id}}`);
        }} else {{
          btn.classList.add('t-bl');
        }}
        btn.textContent = item.label;
        btn.dataset.group = group;
        btn.dataset.id = item.id;
        btn.setAttribute('aria-pressed', state.has(item.id) ? 'true' : 'false');
        if (state.has(item.id)) btn.classList.add('active');
        btn.addEventListener('click', () => {{
          if (state.has(item.id)) {{
            state.delete(item.id);
          }} else {{
            state.add(item.id);
          }}
          syncToggleGroup(group);
          applyGlobalFilters();
        }});
        wrap.appendChild(btn);
      }}
      syncToggleGroup(group);
    }}

    function syncToggleGroup(group) {{
      const state = group === 'channel' ? channelState : blState;
      const catalog = group === 'channel' ? platformCatalog : blCatalog;
      const wrapId = group === 'channel' ? 'channelFilters' : 'blFilters';
      const wrap = document.getElementById(wrapId);
      if (!wrap) return;
      wrap.querySelectorAll('.filter-toggle').forEach(btn => {{
        if (btn.dataset.id === '__all__') {{
          const on = isAllSelected(state, catalog);
          btn.classList.toggle('active', on);
          btn.setAttribute('aria-pressed', on ? 'true' : 'false');
          return;
        }}
        const on = state.has(btn.dataset.id);
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }});
    }}

    function setGroupSelection(group, ids) {{
      const state = group === 'channel' ? channelState : blState;
      state.clear();
      ids.forEach(id => state.add(id));
      syncToggleGroup(group);
      applyGlobalFilters();
    }}

    function applyBlView() {{
      const showZeroSpend = !!document.getElementById('showZeroSpend')?.checked;
      const filtered = filteredBlCampaigns();
      const spend = filtered.reduce((s, r) => s + (r.spend || 0), 0);
      const clicks = filtered.reduce((s, r) => s + (r.clicks || 0), 0);
      const impressions = filtered.reduce((s, r) => s + (r.impressions || 0), 0);
      const conv = filtered.reduce((s, r) => s + (r.conversions || 0), 0);
      const cpc = clicks ? spend / clicks : 0;

      const summary = document.getElementById('blSummary');
      if (summary) {{
        summary.innerHTML = `
          <div class="bl-stat"><div class="bl-stat-val">${{fmtMoney(spend)}}</div><div class="bl-stat-lbl">Spend</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(clicks)}}</div><div class="bl-stat-lbl">Clicks</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(impressions)}}</div><div class="bl-stat-lbl">Impressions</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{fmtInt(conv)}}</div><div class="bl-stat-lbl">Conversions</div></div>
          <div class="bl-stat"><div class="bl-stat-val">${{clicks ? fmtMoney(cpc) : '—'}}</div><div class="bl-stat-lbl">CPC</div></div>`;
      }}

      const tbody = document.getElementById('blTableBody');
      if (tbody) {{
        if (!filtered.length) {{
          tbody.innerHTML = '<tr><td colspan="10" class="muted" style="padding:24px;text-align:center">No campaigns match — try other filters or enable $0 spend rows.</td></tr>';
        }} else {{
          const sorted = sortBlRows(filtered);
          tbody.innerHTML = '';
          for (const r of sorted) {{
            tbody.appendChild(buildBlCampaignRow(r));
          }}
        }}
      }}

      const rowCount = document.getElementById('blRowCount');
      if (rowCount) rowCount.textContent = filtered.length + ' row' + (filtered.length === 1 ? '' : 's');

      const status = document.getElementById('filterStatus');
      if (status) {{
        const chLabels = platformCatalog.filter(p => channelState.has(p.id)).map(p => p.label);
        const blLabels = blCatalog.filter(b => blState.has(b.id)).map(b => b.label);
        const allCh = chLabels.length === platformCatalog.length && platformCatalog.length > 0;
        const allBl = blLabels.length === blCatalog.length && blCatalog.length > 0;
        const chText = isAllSelected(channelState, platformCatalog)
          ? 'All channels'
          : (allCh ? 'All channels' : chLabels.join(', '));
        if (SHOW_BUSINESS_LINE && blCatalog.length) {{
          const blText = isAllSelected(blState, blCatalog)
            ? 'All business lines'
            : (allBl ? 'All business lines' : blLabels.join(', '));
          const zeroNote = showZeroSpend ? ' · incl. $0 spend' : '';
          status.textContent = `${{blText}} · ${{chText}} · ${{filtered.length}} campaign${{filtered.length === 1 ? '' : 's'}}${{zeroNote}}`;
        }} else {{
          status.textContent = chText;
        }}
      }}
      updateBlSortHeaders();
    }}

    initToggleGroup('channelFilters', platformCatalog, channelState, 'channel');
    initToggleGroup('blFilters', blCatalog, blState, 'bl');
    document.getElementById('showZeroSpend')?.addEventListener('change', applyGlobalFilters);
    document.querySelector('#blTable thead')?.addEventListener('click', onBlSortClick);

    const ga4PagesBody = document.getElementById('ga4PagesBody');
    const ga4MetricsGrid = document.getElementById('ga4MetricsGrid');

    function fmtGa4Seconds(value) {{
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) return '—';
      return n.toFixed(1) + 's';
    }}

    function fmtGa4Rate(value) {{
      const n = Number(value);
      if (!Number.isFinite(n) || n < 0) return '—';
      return (100 * n).toFixed(2) + '%';
    }}

    function fmtGa4Ratio(value) {{
      const n = Number(value);
      if (!Number.isFinite(n) || n < 0) return '—';
      return n.toFixed(2);
    }}

    function summaryFromPages(rows) {{
      const sessions = rows.reduce((sum, row) => sum + Number(row.sessions || 0), 0);
      const engaged = rows.reduce((sum, row) => sum + Number(row.engaged_sessions || 0), 0);
      const pageViews = rows.reduce((sum, row) => sum + Number(row.page_views || 0), 0);
      const keyEvents = rows.reduce((sum, row) => sum + Number(row.key_events || 0), 0);
      return {{
        sessions,
        engagement_rate: sessions ? engaged / sessions : 0,
        avg_engagement_time_sec: ga4SiteSummary.avg_engagement_time_sec,
        avg_session_duration_sec: ga4SiteSummary.avg_session_duration_sec,
        events_per_session: sessions ? keyEvents / sessions : 0,
        views_per_session: sessions ? pageViews / sessions : 0,
        filtered: true,
      }};
    }}

    function renderGa4MetricsSummary(rows, isFiltered) {{
      if (!ga4MetricsGrid || !ga4SiteSummary || !ga4SiteSummary.sessions) return;
      const metrics = isFiltered ? summaryFromPages(rows) : {{ ...ga4SiteSummary, filtered: false }};
      const sublabel = metrics.filtered ? 'GA4 filtered view' : 'Site-wide GA4';
      ga4MetricsGrid.innerHTML = `
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Sessions</div>
          <div class="ga4-metric-value">${{fmtInt(metrics.sessions)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Engagement rate</div>
          <div class="ga4-metric-value">${{fmtGa4Rate(metrics.engagement_rate)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Avg engagement time</div>
          <div class="ga4-metric-value">${{fmtGa4Seconds(metrics.avg_engagement_time_sec)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Avg session duration</div>
          <div class="ga4-metric-value">${{fmtGa4Seconds(metrics.avg_session_duration_sec)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Events / session</div>
          <div class="ga4-metric-value">${{fmtGa4Ratio(metrics.events_per_session)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>
        <div class="ga4-metric-card">
          <div class="ga4-metric-label">Views / session</div>
          <div class="ga4-metric-value">${{fmtGa4Ratio(metrics.views_per_session)}}</div>
          <div class="ga4-metric-sub">${{sublabel}}</div>
        </div>`;
    }}

    if (ga4PagesBody && ga4Pages.length) {{
      const ga4PageSearch = document.getElementById('ga4PageSearch');
      const ga4PagesCount = document.getElementById('ga4PagesCount');
      const ga4PagesPagination = document.getElementById('ga4PagesPagination');
      const ga4PageSort = {{ key: 'sessions', dir: 'desc' }};
      let ga4PageQuery = '';
      let ga4PageNum = 1;
      const GA4_PAGE_SIZE = 10;

      function ga4PageSortValue(row, key) {{
        if (key === 'page_path' || key === 'page_title') {{
          return String(row[key] || '').toLowerCase();
        }}
        if (key === 'engagement_rate') {{
          return Number(row.engagement_rate || 0);
        }}
        return Number(row[key] || 0);
      }}

      function sortGa4Pages(rows) {{
        const {{ key, dir }} = ga4PageSort;
        const mul = dir === 'asc' ? 1 : -1;
        const textKeys = new Set(['page_path', 'page_title']);
        return [...rows].sort((a, b) => {{
          const av = ga4PageSortValue(a, key);
          const bv = ga4PageSortValue(b, key);
          if (textKeys.has(key)) return mul * String(av).localeCompare(String(bv));
          return mul * (av - bv);
        }});
      }}

      function filterGa4Pages(rows) {{
        const q = ga4PageQuery.trim().toLowerCase();
        if (!q) return rows;
        return rows.filter(row => {{
          const path = String(row.page_path || '').toLowerCase();
          const title = String(row.page_title || '').toLowerCase();
          return path.includes(q) || title.includes(q);
        }});
      }}

      function updateGa4SortHeaders() {{
        document.querySelectorAll('#ga4PagesTable thead th[data-sort]').forEach(th => {{
          const active = th.dataset.sort === ga4PageSort.key;
          th.classList.toggle('sort-active', active);
          if (active) {{
            th.dataset.sortDir = ga4PageSort.dir;
            th.setAttribute('aria-sort', ga4PageSort.dir === 'asc' ? 'ascending' : 'descending');
          }} else {{
            delete th.dataset.sortDir;
            th.setAttribute('aria-sort', 'none');
          }}
        }});
      }}

      function renderGa4Pagination(totalRows, page, pageSize) {{
        if (!ga4PagesPagination) return;
        const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
        if (totalRows <= pageSize) {{
          ga4PagesPagination.hidden = true;
          ga4PagesPagination.innerHTML = '';
          return;
        }}
        ga4PagesPagination.hidden = false;
        const start = (page - 1) * pageSize + 1;
        const end = Math.min(totalRows, page * pageSize);
        const prevDisabled = page <= 1 ? ' disabled' : '';
        const nextDisabled = page >= totalPages ? ' disabled' : '';
        ga4PagesPagination.innerHTML = `
          <span class="ga4-pages-pagination-info">Showing ${{start}}–${{end}} of ${{fmtInt(totalRows)}} pages</span>
          <div class="ga4-pages-pagination-controls">
            <button type="button" class="ga4-pages-page-btn" data-page="prev"${{prevDisabled}} aria-label="Previous page">Previous</button>
            <span class="ga4-pages-pagination-info">Page ${{page}} of ${{totalPages}}</span>
            <button type="button" class="ga4-pages-page-btn" data-page="next"${{nextDisabled}} aria-label="Next page">Next</button>
          </div>`;
      }}

      renderGa4Pages = function() {{
        const filtered = filterGa4Pages(ga4Pages);
        const sorted = sortGa4Pages(filtered);
        const isFiltered = !!ga4PageQuery.trim();
        renderGa4MetricsSummary(filtered, isFiltered);
        const totalPages = Math.max(1, Math.ceil(sorted.length / GA4_PAGE_SIZE));
        if (ga4PageNum > totalPages) ga4PageNum = totalPages;
        if (ga4PageNum < 1) ga4PageNum = 1;
        if (ga4PagesCount) {{
          ga4PagesCount.textContent = sorted.length + ' page' + (sorted.length === 1 ? '' : 's');
        }}
        if (!sorted.length) {{
          ga4PagesBody.innerHTML = '<tr><td colspan="8" class="muted" style="padding:24px;text-align:center">No pages match your search.</td></tr>';
          renderGa4Pagination(0, 1, GA4_PAGE_SIZE);
          updateGa4SortHeaders();
          return;
        }}
        const pageRows = sorted.slice((ga4PageNum - 1) * GA4_PAGE_SIZE, ga4PageNum * GA4_PAGE_SIZE);
        ga4PagesBody.innerHTML = pageRows.map(row => {{
          const sessions = Number(row.sessions || 0);
          const engaged = Number(row.engaged_sessions || 0);
          const engRate = sessions ? (100 * engaged / sessions).toFixed(1) + '%' : '—';
          return `<tr>
            <td class="page-path" title="${{escHtml(row.page_path)}}">${{escHtml(row.page_path)}}</td>
            <td class="page-title" title="${{escHtml(row.page_title)}}">${{escHtml(row.page_title)}}</td>
            <td class="num">${{fmtInt(row.sessions)}}</td>
            <td class="num">${{fmtInt(row.page_views)}}</td>
            <td class="num">${{fmtInt(row.users)}}</td>
            <td class="num">${{fmtInt(row.engaged_sessions)}}</td>
            <td class="num">${{engRate}}</td>
            <td class="num">${{fmtInt(row.key_events)}}</td>
          </tr>`;
        }}).join('');
        renderGa4Pagination(sorted.length, ga4PageNum, GA4_PAGE_SIZE);
        updateGa4SortHeaders();
      }};

      ga4PagesPagination?.addEventListener('click', e => {{
        const btn = e.target.closest('[data-page]');
        if (!btn || btn.disabled) return;
        const total = sortGa4Pages(filterGa4Pages(ga4Pages)).length;
        const totalPages = Math.max(1, Math.ceil(total / GA4_PAGE_SIZE));
        if (btn.dataset.page === 'prev' && ga4PageNum > 1) {{
          ga4PageNum -= 1;
          renderGa4Pages();
        }} else if (btn.dataset.page === 'next' && ga4PageNum < totalPages) {{
          ga4PageNum += 1;
          renderGa4Pages();
        }}
      }});

      ga4PageSearch?.addEventListener('input', e => {{
        ga4PageQuery = e.target.value || '';
        ga4PageNum = 1;
        renderGa4Pages();
      }});
      document.querySelector('#ga4PagesTable thead')?.addEventListener('click', e => {{
        const th = e.target.closest('th[data-sort]');
        if (!th) return;
        const key = th.dataset.sort;
        if (ga4PageSort.key === key) {{
          ga4PageSort.dir = ga4PageSort.dir === 'asc' ? 'desc' : 'asc';
        }} else {{
          ga4PageSort.key = key;
          ga4PageSort.dir = (key === 'page_path' || key === 'page_title') ? 'asc' : 'desc';
        }}
        ga4PageNum = 1;
        renderGa4Pages();
      }});
      renderGa4Pages();
    }} else if (ga4MetricsGrid && ga4SiteSummary && ga4SiteSummary.sessions) {{
      renderGa4MetricsSummary([], false);
    }}

    const DRILL_MAP = {{
      'google:campaign': {{ childLevel: 'ad_group', childLabel: 'Ad group' }},
      'google:ad_group': {{ childLevel: 'ad', childLabel: 'Ad' }},
      'linkedin:campaign_group': {{ childLevel: 'campaign', childLabel: 'Ad set' }},
      'linkedin:campaign': {{ childLevel: 'creative', childLabel: 'Ad' }},
      'meta:campaign': {{ childLevel: 'adset', childLabel: 'Ad set' }},
      'meta:adset': {{ childLevel: 'ad', childLabel: 'Ad' }},
    }};
    const LEVEL_LABELS = {{
      campaign_group: 'Campaign group',
      campaign: 'Campaign',
      ad_group: 'Ad group',
      creative: 'Creative',
      adset: 'Ad set',
      ad: 'Ad',
    }};

    function levelLabel(platform, level) {{
      if (platform === 'linkedin') {{
        if (level === 'campaign_group') return 'Campaign group';
        if (level === 'campaign') return 'Ad set';
        if (level === 'creative') return 'Ad';
      }}
      return LEVEL_LABELS[level] || String(level || '').replace(/_/g, ' ');
    }}

    function childRows(platform, level, parentId) {{
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return [];
      const pool = (breakdowns[platform] || {{}})[rule.childLevel] || [];
      const pid = String(parentId || '');
      return pool.filter(r => String(r.parent_id || '') === pid)
        .sort((a, b) => (b.spend || 0) - (a.spend || 0));
    }}

    function isExpandable(platform, level) {{
      return !!DRILL_MAP[platform + ':' + level];
    }}

    function previewPayload(r) {{
      const embed = r.youtube_embed_url || '';
      if (embed) return {{ type: 'embed', url: embed }};
      const videoUrl = r.video_url || '';
      if (videoUrl && r.media_type === 'video') {{
        const ytMatch = videoUrl.match(/(?:youtube\\.com\\/embed\\/|youtu\\.be\\/)([a-zA-Z0-9_-]{{11}})/);
        if (ytMatch) return {{ type: 'embed', url: 'https://www.youtube.com/embed/' + ytMatch[1] }};
        return {{ type: 'video', url: videoUrl }};
      }}
      const img = r.image_url || r.thumbnail_url || '';
      if (img) return {{ type: 'image', url: img }};
      return null;
    }}

    function buildThumbButton(r) {{
      const thumbUrl = r.thumbnail_url || r.image_url || '';
      if (!thumbUrl) return '';
      const preview = previewPayload(r);
      const playable = preview && (preview.type === 'embed' || preview.type === 'video');
      const cls = 'ad-thumb-btn' + (playable ? ' has-video' : '');
      const attrs = preview
        ? ` data-preview-type="${{escHtml(preview.type)}}" data-preview-url="${{escHtml(preview.url)}}"`
        : ` data-preview-type="image" data-preview-url="${{escHtml(thumbUrl)}}"`;
      const play = playable ? '<span class="ad-play-icon" aria-hidden="true">▶</span>' : '';
      return `<button type="button" class="${{cls}}"${{attrs}} aria-label="Preview creative">${{play}}<img class="ad-thumb" src="${{escHtml(thumbUrl)}}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('button')?.remove()"></button>`;
    }}

    let previewPlayer = null;

    function closeCreativePreview() {{
      const modal = document.getElementById('creativePreview');
      const body = document.getElementById('creativePreviewBody');
      const caption = document.getElementById('creativePreviewCaption');
      if (!modal || !body) return;
      body.innerHTML = '';
      if (caption) caption.textContent = '';
      modal.hidden = true;
      previewPlayer = null;
    }}

    function openCreativePreview(type, url, label) {{
      const modal = document.getElementById('creativePreview');
      const body = document.getElementById('creativePreviewBody');
      const caption = document.getElementById('creativePreviewCaption');
      if (!modal || !body || !url) return;
      body.innerHTML = '';
      if (type === 'embed') {{
        const iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.allow = 'autoplay; encrypted-media; picture-in-picture';
        iframe.allowFullscreen = true;
        iframe.title = label || 'Video preview';
        body.appendChild(iframe);
      }} else if (type === 'video') {{
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        body.appendChild(video);
        previewPlayer = video;
      }} else {{
        const img = document.createElement('img');
        img.src = url;
        img.alt = label || 'Creative preview';
        body.appendChild(img);
      }}
      if (caption) caption.textContent = label || '';
      modal.hidden = false;
    }}

    function buildNameCell(r, platform, level, depth) {{
      const pad = 8 + depth * 20;
      const tag = levelLabel(platform, level)
        ? `<span class="entity-tag">${{escHtml(levelLabel(platform, level))}}</span>` : '';
      let inner = `${{tag}}${{escHtml(r.name || '—')}}`;
      if (level === 'ad' || level === 'creative') {{
        const thumb = buildThumbButton(r);
        const creativeSub = (r.creative_name && r.creative_name !== r.name)
          ? `<span class="ad-creative-sub">${{escHtml(r.creative_name)}}</span>` : '';
        const typeBadge = r.media_type
          ? `<span class="ad-creative-sub">${{escHtml(r.media_type)}}</span>` : '';
        inner = `<div class="name-inner">${{thumb}}<span><span>${{tag}}${{escHtml(r.name || '—')}}</span>${{creativeSub}}${{typeBadge}}</span></div>`;
      }}
      return `<td class="name" style="padding-left:${{pad}}px">${{inner}}</td>`;
    }}

    function buildGa4Cells(platform, level, rowId) {{
      const platformMetrics = ga4CampaignMetrics[platform];
      if (!platformMetrics) return '';
      // LinkedIn GA4 attribution matches API campaigns (UI ad sets), not campaign groups.
      if (level !== 'campaign') {{
        return '<td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td>';
      }}
      const metrics = platformMetrics[String(rowId || '')] || {{}};
      const sessions = metrics.sessions || 0;
      if (!sessions) {{
        return '<td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td><td class="num ga4-col muted">—</td>';
      }}
      const engaged = metrics.engaged_sessions || 0;
      const keyEvents = metrics.key_events || 0;
      return `<td class="num ga4-col">${{fmtInt(sessions)}}</td>`
        + `<td class="num ga4-col">${{fmtPct(engaged, sessions)}}</td>`
        + `<td class="num ga4-col">${{fmtInt(keyEvents)}}</td>`;
    }}

    function treeNameColIndex(table) {{
      return table?.id === 'blTable' ? 3 : 1;
    }}

    function treeEmptyRowCells(table, depth) {{
      const nameIdx = treeNameColIndex(table);
      const cols = table?.querySelectorAll('thead th').length || 8;
      const colspan = cols - nameIdx - 1;
      const beforeName = '<td></td>'.repeat(nameIdx);
      return `${{beforeName}}<td class="name muted" style="padding-left:${{8 + (depth + 1) * 20}}px">No child rows for this period</td><td colspan="${{colspan}}"></td>`;
    }}

    function buildTreeRow(r, platform, level, depth, prefixCellsHtml = '', includeGa4 = true) {{
      const spend = r.spend || 0;
      const clicks = r.clicks || 0;
      const impressions = r.impressions || 0;
      const conv = r.conversions || 0;
      const cpc = clicks ? fmtMoney(spend / clicks) : '—';
      const expandable = isExpandable(platform, level);
      const chevron = expandable
        ? '<span class="tree-chevron" aria-hidden="true">▸</span>'
        : '<span class="tree-chevron leaf"></span>';
      const tr = document.createElement('tr');
      tr.className = `tree-row tree-depth-${{depth}}${{expandable ? ' tree-expandable' : ''}}`;
      tr.dataset.platform = platform;
      tr.dataset.level = level;
      tr.dataset.id = r.id;
      tr.dataset.depth = String(depth);
      if (expandable) {{
        tr.tabIndex = 0;
        tr.setAttribute('role', 'button');
        tr.setAttribute('aria-expanded', 'false');
      }}
      const ga4Cells = includeGa4 ? buildGa4Cells(platform, level, r.id) : '';
      tr.innerHTML = `
        <td class="chevron-col">${{chevron}}</td>
        ${{prefixCellsHtml}}
        ${{buildNameCell(r, platform, level, depth)}}
        <td class="num">${{fmtMoney(spend)}}</td>
        <td class="num">${{fmtInt(clicks)}}</td>
        <td class="num">${{fmtInt(impressions)}}</td>
        <td class="num">${{fmtPct(clicks, impressions)}}</td>
        <td class="num">${{fmtInt(conv)}}</td>
        <td class="num">${{cpc}}</td>
        ${{ga4Cells}}`;
      return tr;
    }}

    function blRootLevel(platform) {{
      return platform === 'linkedin' ? 'campaign_group' : 'campaign';
    }}

    function buildBlCampaignRow(r) {{
      const platform = r.platform;
      const prefixCells = `
        <td><span class="platform-pill ${{escHtml(platform)}}">${{escHtml(r.platform_label)}}</span></td>
        <td><span class="bl-tag">${{escHtml(r.business_line_label)}}</span></td>`;
      const level = r.entity_level || blRootLevel(platform);
      return buildTreeRow(r, platform, level, 0, prefixCells, false);
    }}

    function collapseDescendants(row) {{
      const depth = parseInt(row.dataset.depth || '0', 10);
      let next = row.nextElementSibling;
      while (next && next.classList.contains('tree-row') && parseInt(next.dataset.depth || '0', 10) > depth) {{
        const rm = next;
        next = next.nextElementSibling;
        rm.remove();
      }}
      row.classList.remove('expanded');
      row.setAttribute('aria-expanded', 'false');
    }}

    function toggleTreeRow(row) {{
      if (!row.classList.contains('tree-expandable')) return;
      if (row.classList.contains('expanded')) {{
        collapseDescendants(row);
        return;
      }}
      const platform = row.dataset.platform;
      const level = row.dataset.level;
      const id = row.dataset.id;
      const depth = parseInt(row.dataset.depth || '0', 10);
      const rule = DRILL_MAP[platform + ':' + level];
      if (!rule) return;
      const children = childRows(platform, level, id);
      let insertAfter = row;
      const table = row.closest('table');
      const isBlTable = table?.id === 'blTable';
      const childPrefix = isBlTable ? '<td></td><td></td>' : '';
      if (!children.length) {{
        const empty = document.createElement('tr');
        empty.className = 'tree-row tree-empty';
        empty.dataset.depth = String(depth + 1);
        empty.innerHTML = treeEmptyRowCells(table, depth + 1);
        insertAfter.after(empty);
      }} else {{
        for (const child of children) {{
          const childRow = buildTreeRow(
            child,
            platform,
            rule.childLevel,
            depth + 1,
            childPrefix,
            !isBlTable
          );
          insertAfter.after(childRow);
          insertAfter = childRow;
        }}
      }}
      row.classList.add('expanded');
      row.setAttribute('aria-expanded', 'true');
    }}

    document.querySelectorAll('.tree-table').forEach(tbody => {{
      tbody.addEventListener('click', e => {{
        if (e.target.closest('.ad-thumb-btn')) return;
        const row = e.target.closest('tr.tree-expandable');
        if (!row || !tbody.contains(row)) return;
        e.preventDefault();
        e.stopPropagation();
        toggleTreeRow(row);
      }});
      tbody.addEventListener('keydown', e => {{
        const row = e.target.closest('tr.tree-expandable');
        if (!row || !tbody.contains(row)) return;
        if (e.key === 'Enter' || e.key === ' ') {{
          e.preventDefault();
          toggleTreeRow(row);
        }}
      }});
    }});

    document.body.addEventListener('click', e => {{
      const btn = e.target.closest('.ad-thumb-btn');
      if (btn) {{
        e.preventDefault();
        e.stopPropagation();
        const type = btn.dataset.previewType || 'image';
        const url = btn.dataset.previewUrl || '';
        const label = btn.closest('.name-inner')?.innerText?.trim() || 'Creative preview';
        openCreativePreview(type, url, label);
        return;
      }}
      if (e.target.closest('[data-close-preview]')) {{
        closeCreativePreview();
      }}
    }});
    document.addEventListener('keydown', e => {{
      if (e.key === 'Escape') closeCreativePreview();
    }});

    if ((performanceChartRaw.labels || []).length) {{
      refreshCharts();
    }}

    applyGlobalFilters();
  </script>
</body>
</html>"""
