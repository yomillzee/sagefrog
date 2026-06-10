"""Refresh Penn dashboard data and render HTML from Postgres snapshots."""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

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
import client_dashboard_config
import dashboard_features
from penn_business_lines import (
    PLATFORM_LABELS,
    active_client_product_line_catalog,
    active_client_segment_catalog,
    active_platform_catalog,
    build_client_segment_campaigns,
    client_filter_profile,
    client_has_product_line_filters,
    client_has_segment_filters,
    platform_catalog,
    platforms_present_in_snapshot,
    segment_column_label,
    segment_filter_label,
    _breakdown_has_platform_data,
    _totals_have_metrics,
)
from dashboard.utils.auth import (
    can_edit_penn_insights,
    configured_dashboard_secret,
    min_refresh_seconds,
    refresh_cooldown_status,
    verify_dashboard_key,
)
from dashboard.utils.dates import (
    mtd_calendar_bounds as _mtd_calendar_bounds,
    paid_daily_spend_map as _paid_daily_spend_map,
    parse_monthly_budget_input,
)
from dashboard.utils.formatting import (
    entity_level_label as _entity_level_label,
    esc as _esc,
    file_type_icon_html as _file_type_icon_html,
    folder_icon_html as _folder_icon_html,
    fmt_cpa as _fmt_cpa,
    fmt_file_size as _fmt_file_size,
    fmt_int as _fmt_int,
    fmt_money as _fmt_money,
    fmt_pct as _fmt_pct,
    fmt_short_date as _fmt_short_date,
    json_for_html_script as _json_for_html_script,
    platform_error as _platform_error,
    platform_title_html as _platform_title_html,
)
from dashboard.utils.urls import (
    client_switch_target_url as _client_switch_target_url,
    dashboard_page_url as _dashboard_page_url,
    files_page_url as _files_page_url,
    insight_document_delete_url as _insight_document_delete_url,
    insight_document_download_url as _insight_document_download_url,
    insight_document_move_url as _insight_document_move_url,
    insight_documents_action_url as _insight_documents_action_url,
    insight_folder_action_url as _insight_folder_action_url,
    insight_folder_delete_url as _insight_folder_delete_url,
    insights_action_url as _insights_action_url,
    insights_upload_page_url as _insights_upload_page_url,
    refresh_action_url as _refresh_action_url,
    settings_page_url as _settings_page_url,
    time_tracking_page_url as _time_tracking_page_url,
)




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




def _account_totals(perf: dict[str, Any]) -> dict[str, Any]:
    totals = dict(perf.get("totals") or {})
    totals.setdefault("spend", 0.0)
    totals.setdefault("clicks", 0)
    totals.setdefault("impressions", 0)
    totals.setdefault("conversions", 0.0)
    return totals


def _hydrate_platform_totals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Fill missing per-platform totals from daily_metrics warehouse rows."""
    totals = dict(snapshot.get("platform_totals") or {})
    daily = snapshot.get("daily_metrics") or {}
    for platform in ("google", "linkedin", "meta", "organic"):
        if totals.get(platform):
            continue
        rows = daily.get(platform) or []
        if rows:
            totals[platform] = _totals_from_daily_rows(rows)
    return totals


def _platforms_with_summary_data(
    totals: dict[str, Any],
    daily_metrics: dict[str, Any],
    breakdowns: dict[str, Any],
) -> list[str]:
    """Platforms that should render a summary card (data present, not config-only)."""
    present: list[str] = []
    for platform in ("google", "linkedin", "meta"):
        if _totals_have_metrics(totals.get(platform)):
            present.append(platform)
        elif daily_metrics.get(platform):
            present.append(platform)
        elif _breakdown_has_platform_data(breakdowns.get(platform)):
            present.append(platform)
    return present




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


def _load_mtd_daily_metrics(cfg: PennDashboardConfig) -> dict[str, Any]:
    """Load paid-platform daily metrics for the current calendar month."""
    month_start, _, today = _mtd_calendar_bounds()
    payload: dict[str, Any] = {"daily_metrics": {}, "errors": {}}
    if warehouse.enabled():
        _penn_load_daily_metrics_from_warehouse(
            cfg,
            start=month_start,
            end=today,
            payload=payload,
            ga4_account=None,
            update_platform_totals=False,
        )
        return payload.get("daily_metrics") or {}
    return {}


def _build_budget_pacing_payload(
    cfg: PennDashboardConfig,
    *,
    snapshot_daily_metrics: dict[str, Any] | None = None,
    monthly_budget: float | None = None,
) -> dict[str, Any]:
    month_start, month_end, today = _mtd_calendar_bounds()
    daily_metrics = _load_mtd_daily_metrics(cfg)
    if not any(daily_metrics.get(p) for p in ("google", "linkedin", "meta")):
        daily_metrics = snapshot_daily_metrics or {}
    spend_by_day = _paid_daily_spend_map(
        daily_metrics,
        month_start=month_start,
        through=today,
    )
    daily_spend_by_platform: dict[str, dict[str, float]] = {}
    start_key = month_start.isoformat()
    end_key = today.isoformat()
    for platform in ("google", "linkedin", "meta"):
        platform_map: dict[str, float] = {}
        for row in daily_metrics.get(platform) or []:
            key = str(row.get("metric_date") or "")[:10]
            if not key or key < start_key or key > end_key:
                continue
            platform_map[key] = platform_map.get(key, 0.0) + float(row.get("spend") or 0)
        daily_spend_by_platform[platform] = platform_map

    labels: list[str] = []
    cursor = month_start
    while cursor <= today:
        labels.append(cursor.isoformat())
        cursor += timedelta(days=1)

    cumulative: list[float] = []
    running = 0.0
    for day_key in labels:
        running += spend_by_day.get(day_key, 0.0)
        cumulative.append(round(running, 2))

    cumulative_by_platform: dict[str, list[float]] = {}
    for pid in ("google", "linkedin", "meta"):
        platform_running = 0.0
        platform_series: list[float] = []
        for day_key in labels:
            platform_running += daily_spend_by_platform.get(pid, {}).get(day_key, 0.0)
            platform_series.append(round(platform_running, 2))
        cumulative_by_platform[pid] = platform_series

    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
    budget = float(monthly_budget if monthly_budget is not None else (cfg.monthly_budget_usd or 0))
    pace_line = [
        round(budget * ((idx + 1) / days_in_month), 2) if budget > 0 else 0.0
        for idx in range(len(labels))
    ]
    mtd_spend = cumulative[-1] if cumulative else 0.0
    expected_pace = round(budget * (len(labels) / days_in_month), 2) if budget > 0 else 0.0
    pct_month = round(100 * len(labels) / days_in_month, 1)
    pct_budget = round(100 * mtd_spend / budget, 1) if budget > 0 else None
    pct_vs_pace = (
        round(100 * (mtd_spend - expected_pace) / expected_pace, 1)
        if expected_pace > 0
        else None
    )
    days_elapsed = len(labels)
    days_remaining = max(0, days_in_month - days_elapsed)
    remaining_budget = round(budget - mtd_spend, 2) if budget > 0 else 0.0
    avg_daily = round(mtd_spend / days_elapsed, 2) if days_elapsed > 0 else 0.0
    required_daily = (
        round(remaining_budget / days_remaining, 2)
        if budget > 0 and days_remaining > 0
        else None
    )
    daily_adjustment = (
        round(required_daily - avg_daily, 2)
        if required_daily is not None and days_elapsed > 0
        else None
    )

    pacing_platforms = [
        {"id": pid, "label": PLATFORM_LABELS[pid]}
        for pid in ("google", "linkedin", "meta")
        if daily_spend_by_platform.get(pid)
        or (
            pid == "google"
            and cfg.google_customer_id
            or pid == "linkedin"
            and cfg.linkedin_account_id
            or pid == "meta"
            and cfg.meta_account_id
        )
    ]

    return {
        "labels": labels,
        "cumulative_spend": cumulative,
        "cumulative_by_platform": cumulative_by_platform,
        "pace_line": pace_line,
        "daily_spend_by_platform": daily_spend_by_platform,
        "platforms": pacing_platforms,
        "monthly_budget": budget,
        "month_label": month_start.strftime("%B %Y"),
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "month_end": month_end.isoformat(),
        "mtd_spend": mtd_spend,
        "expected_pace_today": expected_pace,
        "pct_month_elapsed": pct_month,
        "pct_budget_spent": pct_budget,
        "pct_vs_pace": pct_vs_pace,
        "remaining_budget": remaining_budget,
        "avg_daily_spend": avg_daily,
        "required_daily_spend": required_daily,
        "daily_adjustment": daily_adjustment,
    }




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
) -> str | None:
    """Attach thumbnail/image metadata to LinkedIn creative rows when available."""
    if not creatives or not account_id:
        return None
    list_video_rows = 0
    try:
        media_data = linkedin_service.list_video_creatives(account_id, videos_only=False)
        list_video_rows = int(media_data.get("row_count") or 0)
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
            for key in (
                "thumbnail_url",
                "image_url",
                "video_url",
                "media_type",
                "creative_name",
            ):
                val = str(media.get(key) or "").strip()
                if val and not str(creative.get(key) or "").strip():
                    creative[key] = val
    except Exception:
        pass
    enrich_stats: dict[str, int] = {}
    try:
        enrich_stats = linkedin_service.enrich_creative_rows_with_media(creatives, account_id)
    except Exception:
        enrich_stats = {}
    missing = sum(
        1
        for creative in creatives
        if not str(creative.get("thumbnail_url") or creative.get("image_url") or "").strip()
    )
    if missing:
        enriched = int(enrich_stats.get("enriched") or 0)
        account_videos = int(enrich_stats.get("account_videos") or 0)
        sponsored_posts = int(enrich_stats.get("sponsored_posts") or 0)
        return (
            f"{missing} of {len(creatives)} LinkedIn ad rows are missing creative thumbnails "
            f"after refresh ({enriched} newly enriched; {account_videos} account videos and "
            f"{sponsored_posts} sponsored posts indexed; {list_video_rows} preview rows from "
            f"creative listing). Reconnect LinkedIn in Settings if this persists."
        )
    return None


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
            thumb_warning = _merge_linkedin_creative_media(
                li_creatives_raw, cfg.linkedin_account_id
            )
            if thumb_warning:
                payload["errors"]["linkedin_creative_thumbnails"] = thumb_warning
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
    payload["business_line_campaigns"] = build_client_segment_campaigns(
        breakdowns,
        client_slug=cfg.client_key,
        filter_profile=client_filter_profile(cfg.client_key, cfg=cfg),
    )

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
                client_slug=cfg.client_key,
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


def patch_snapshot_from_config(cfg: PennDashboardConfig) -> None:
    """Sync label and account IDs onto an existing snapshot after settings save."""
    if not dashboard_snapshots.enabled():
        return
    existing = dashboard_snapshots.get_snapshot(cfg.client_key)
    if not existing:
        return
    accounts = dict(existing.get("accounts") or {})
    accounts.update(
        {
            "google": cfg.google_customer_id,
            "linkedin": cfg.linkedin_account_id,
            "meta": cfg.meta_account_id,
            "ga4_client_key": cfg.ga4_client_key,
            "harvest_project_id": cfg.harvest_project_id,
        }
    )
    existing["label"] = cfg.label
    existing["accounts"] = accounts
    dashboard_snapshots.save_snapshot(cfg.client_key, existing, touch_refreshed_at=False)


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
    accounts = dict(existing.get("accounts") or {})
    accounts.update(
        {
            "google": cfg.google_customer_id,
            "linkedin": cfg.linkedin_account_id,
            "meta": cfg.meta_account_id,
            "ga4_client_key": cfg.ga4_client_key,
            "harvest_project_id": cfg.harvest_project_id,
        }
    )

    payload: dict[str, Any] = {
        "client_key": cfg.client_key,
        "label": cfg.label,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "accounts": accounts,
        "warehouse_sync": {},
        "daily_metrics": {},
        "breakdowns": breakdowns,
        "platform_totals": {},
        "aggregated_paid_media": {},
        "errors": {},
        "ga4_attribution": existing.get("ga4_attribution"),
        "ga4_pages": existing.get("ga4_pages"),
        "business_line_campaigns": build_client_segment_campaigns(
            breakdowns,
            client_slug=cfg.client_key,
            filter_profile=client_filter_profile(cfg.client_key, cfg=cfg),
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




























































def _business_line_campaigns_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive campaign rows from breakdowns when not stored on the snapshot."""
    client_key = str(snapshot.get("client_key") or "penn")
    breakdowns = _breakdowns_from_snapshot(snapshot)
    accounts = snapshot.get("accounts") or {}
    filter_profile = client_filter_profile(
        client_key,
        label=str(snapshot.get("label") or ""),
        ga4_client_key=str(accounts.get("ga4_client_key") or ""),
    )
    if filter_profile:
        return build_client_segment_campaigns(
            breakdowns,
            client_slug=client_key,
            filter_profile=filter_profile,
        )
    stored = snapshot.get("business_line_campaigns") or []
    if stored:
        return stored
    return build_client_segment_campaigns(breakdowns, client_slug=client_key)


def _breakdowns_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    breakdowns = snapshot.get("breakdowns")
    if breakdowns:
        return breakdowns
    legacy = snapshot.get("campaigns") or {}
    return {platform: {"campaign": rows} for platform, rows in legacy.items()}

# --- Pass 2 renderers (re-exported for backward compatibility) ---
from dashboard.renderers.base_layout import (
    DASH_TOPBAR_CSS as _DASH_TOPBAR_CSS,
    dash_top_header_html as _dash_top_header_html,
    dashboard_topbar_js as _dashboard_topbar_js,
    dashboard_view_tabs_html as _dashboard_view_tabs_html,
    favicon_head_html as _favicon_head_html,
    refresh_toolbar as _refresh_toolbar,
    render_client_shell_page,
    session_account_html as _session_account_html,
    topbar_client_selector_html as _topbar_client_selector_html,
)
from dashboard.renderers.cards_renderer import (
    aggregated_card as _aggregated_card,
    budget_pacing_panel_html as _budget_pacing_panel_html,
    ga4_paid_key_events as _ga4_paid_key_events,
    paid_ad_overview_html as _paid_ad_overview_html,
    paid_ad_overview_metrics as _paid_ad_overview_metrics,
    summary_card as _summary_card,
    summary_cards_html as _summary_cards_html,
)
from dashboard.renderers.dashboard_renderer import render_penn_html
from dashboard.renderers.files_renderer import (
    client_files_browser_html as _client_files_browser_html,
    files_breadcrumb_html as _files_breadcrumb_html,
    files_page_css as _files_page_css,
    files_page_js as _files_page_js,
    render_files_page,
    render_insights_upload_page,
    render_time_tracking_page,
    time_tracking_page_css as _time_tracking_page_css,
    time_tracking_page_js as _time_tracking_page_js,
)
from dashboard.renderers.settings_renderer import (
    format_insights_body_html as _format_insights_body_html,
    insights_card_html as _insights_card_html,
    insights_editor_html as _insights_editor_html,
    insights_from_snapshot as _insights_from_snapshot,
)
from dashboard.renderers.tables_renderer import (
    GA4_TABLE_HEADERS as _GA4_TABLE_HEADERS,
    drillable_table as _drillable_table,
    entity_table as _entity_table,
    ga4_platform_reports as _ga4_platform_reports,
    ga4_row_cells as _ga4_row_cells,
    platform_breakdown_html as _platform_breakdown_html,
    platform_site_impact_html as _platform_site_impact_html,
    rows_for_display as _rows_for_display,
)

