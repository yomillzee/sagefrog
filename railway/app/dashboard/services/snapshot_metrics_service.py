"""Snapshot data helpers: totals, breakdowns, budget pacing."""

from __future__ import annotations

import calendar
from datetime import timedelta
from typing import Any

from dashboard.utils.dates import mtd_calendar_bounds, paid_daily_spend_map
from penn_business_lines import (
    PLATFORM_LABELS,
    _breakdown_has_platform_data,
    _totals_have_metrics,
    build_client_segment_campaigns,
    client_filter_profile,
)
from dashboard_config import DashboardConfig

from dashboard.services.warehouse_metrics_service import totals_from_daily_rows


def normalize_entity_row(row: dict[str, Any]) -> dict[str, Any]:
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
    for key in (
        "thumbnail_url",
        "image_url",
        "media_type",
        "creative_name",
        "video_url",
        "youtube_embed_url",
        "youtube_watch_url",
    ):
        val = row.get(key)
        if val:
            out[key] = str(val)
    headlines = row.get("headlines")
    if isinstance(headlines, list):
        cleaned = [str(h).strip() for h in headlines if str(h).strip()]
        if cleaned:
            out["headlines"] = cleaned
    return out


def account_totals(perf: dict[str, Any]) -> dict[str, Any]:
    totals = dict(perf.get("totals") or {})
    totals.setdefault("spend", 0.0)
    totals.setdefault("clicks", 0)
    totals.setdefault("impressions", 0)
    totals.setdefault("conversions", 0.0)
    return totals


def hydrate_platform_totals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Fill missing per-platform totals from daily_metrics warehouse rows."""
    totals = dict(snapshot.get("platform_totals") or {})
    daily = snapshot.get("daily_metrics") or {}
    for platform in ("google", "linkedin", "meta", "organic"):
        if totals.get(platform):
            continue
        rows = daily.get(platform) or []
        if rows:
            totals[platform] = totals_from_daily_rows(rows)
    return totals


def platforms_with_summary_data(
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


def aggregated_paid_media(platform_totals: dict[str, Any]) -> dict[str, Any]:
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


def build_budget_pacing_payload(
    cfg: DashboardConfig,
    *,
    snapshot_daily_metrics: dict[str, Any] | None = None,
    monthly_budget: float | None = None,
) -> dict[str, Any]:
    from dashboard.services.warehouse_metrics_service import load_mtd_daily_metrics

    month_start, month_end, today = mtd_calendar_bounds()
    daily_metrics = load_mtd_daily_metrics(cfg)
    if not any(daily_metrics.get(p) for p in ("google", "linkedin", "meta")):
        daily_metrics = snapshot_daily_metrics or {}
    spend_by_day = paid_daily_spend_map(
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


def breakdowns_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    breakdowns = snapshot.get("breakdowns")
    if breakdowns:
        return breakdowns
    legacy = snapshot.get("campaigns") or {}
    return {platform: {"campaign": rows} for platform, rows in legacy.items()}


def business_line_campaigns_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive campaign rows from breakdowns when not stored on the snapshot."""
    client_key = str(snapshot.get("client_key") or "")
    breakdowns = breakdowns_from_snapshot(snapshot)
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
