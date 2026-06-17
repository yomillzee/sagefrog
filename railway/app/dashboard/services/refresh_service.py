"""Dashboard refresh orchestration (full + warehouse-only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import client_config
import dashboard_snapshots
import ga4_attribution_service
import ga4_page_service
import google_ads_service
import linkedin_service
import meta_service
from dates_util import resolve_date_range
from penn_business_lines import build_client_segment_campaigns, client_filter_profile
from penn_config import PennDashboardConfig

from dashboard.utils.formatting import platform_error
from dashboard.services.snapshot_metrics_service import (
    account_totals,
    aggregated_paid_media,
    normalize_entity_row,
)
from dashboard.services.warehouse_metrics_service import (
    load_organic_daily_metrics,
    merge_linkedin_creative_media,
    penn_load_daily_metrics_from_warehouse,
    penn_sync_warehouses,
    sync_campaign_daily,
    sync_meta,
)


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

    ga4_account = penn_sync_warehouses(cfg, preset, payload)
    sync_campaign_daily(cfg, preset, payload)

    if cfg.google_customer_id:
        try:
            perf = google_ads_service.campaign_performance(cfg.google_customer_id, date_range=preset)
            google_campaigns = [normalize_entity_row(c) for c in perf.get("campaigns") or []]
            payload["platform_totals"]["google"] = account_totals(perf)
        except Exception as exc:
            payload["errors"]["google_campaigns"] = platform_error(exc)
            google_campaigns = []
        google_adgroups: list[dict[str, Any]] = []
        google_ads: list[dict[str, Any]] = []
        try:
            ag_perf = google_ads_service.adgroups_performance(
                cfg.google_customer_id, date_range=preset
            )
            google_adgroups = [
                normalize_entity_row(g) for g in ag_perf.get("adgroups") or []
            ]
        except Exception as exc:
            payload["errors"]["google_adgroups"] = platform_error(exc)
        try:
            ads_perf = google_ads_service.ads_performance(
                cfg.google_customer_id, date_range=preset, include_creative=True
            )
            google_ads = [normalize_entity_row(a) for a in ads_perf.get("ads") or []]
        except Exception as exc:
            payload["errors"]["google_ads"] = platform_error(exc)
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
            li_groups = [normalize_entity_row(g) for g in groups_perf.get("campaign_groups") or []]
            li_totals = account_totals(groups_perf)
        except Exception as exc:
            payload["errors"]["linkedin_campaign_groups"] = platform_error(exc)
        try:
            perf = linkedin_service.account_performance(cfg.linkedin_account_id, date_range=preset)
            li_campaigns = [normalize_entity_row(c) for c in perf.get("campaigns") or []]
        except Exception as exc:
            payload["errors"]["linkedin_campaigns"] = platform_error(exc)
        try:
            creatives_perf = linkedin_service.creatives_performance(
                cfg.linkedin_account_id, date_range=preset
            )
            li_creatives_raw = creatives_perf.get("creatives") or []
            thumb_warning = merge_linkedin_creative_media(
                li_creatives_raw, cfg.linkedin_account_id
            )
            if thumb_warning:
                payload["errors"]["linkedin_creative_thumbnails"] = thumb_warning
            li_creatives = [normalize_entity_row(c) for c in li_creatives_raw]
        except Exception as exc:
            payload["errors"]["linkedin_creatives"] = platform_error(exc)
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
            meta_campaigns = [normalize_entity_row(c) for c in perf.get("campaigns") or []]
            meta_totals = account_totals(perf)
        except Exception as exc:
            payload["errors"]["meta_campaigns"] = platform_error(exc)
        try:
            adsets_perf = meta_service.adsets_performance(cfg.meta_account_id, date_range=preset)
            meta_adsets = [normalize_entity_row(a) for a in adsets_perf.get("adsets") or []]
        except Exception as exc:
            payload["errors"]["meta_adsets"] = platform_error(exc)
        try:
            ads_perf = meta_service.ads_performance(cfg.meta_account_id, date_range=preset)
            meta_ads = [normalize_entity_row(a) for a in ads_perf.get("ads") or []]
        except Exception as exc:
            payload["errors"]["meta_ads"] = platform_error(exc)
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
            payload["errors"]["ga4_attribution"] = platform_error(exc)
        try:
            payload["ga4_pages"] = ga4_page_service.fetch_pages_for_dashboard(
                date_range=preset,
                client_key=cfg.ga4_client_key,
                client_slug=cfg.client_key,
            )
        except Exception as exc:
            payload["errors"]["ga4_pages"] = platform_error(exc)

    penn_load_daily_metrics_from_warehouse(
        cfg,
        start=start,
        end=end,
        payload=payload,
        ga4_account=ga4_account or payload["accounts"].get("ga4"),
        update_platform_totals=False,
    )
    load_organic_daily_metrics(cfg, start=start, end=end, payload=payload)
    payload["aggregated_paid_media"] = aggregated_paid_media(payload["platform_totals"])
    payload["refresh_mode"] = "full"
    payload["sync_meta"] = sync_meta(sync_trigger)

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

    ga4_account = penn_sync_warehouses(cfg, preset, payload)
    sync_campaign_daily(cfg, preset, payload)
    penn_load_daily_metrics_from_warehouse(
        cfg,
        start=start,
        end=end,
        payload=payload,
        ga4_account=ga4_account or payload["accounts"].get("ga4"),
        update_platform_totals=True,
    )
    load_organic_daily_metrics(cfg, start=start, end=end, payload=payload)
    payload["sync_meta"] = sync_meta(sync_trigger)

    dashboard_snapshots.save_snapshot(cfg.client_key, payload)
    return payload


def refresh_penn_quick(*, date_range: str = "LAST_30_DAYS", sync_trigger: str = "manual_quick") -> dict[str, Any]:
    return refresh_client_quick(client_slug="penn", date_range=date_range, sync_trigger=sync_trigger)


def refresh_penn_bq_test(*, date_range: str = "LAST_30_DAYS", sync_trigger: str = "manual_full") -> dict[str, Any]:
    """Run all BQ queries for Penn BQ Test and save to Postgres snapshot cache."""
    import bq_gsc_service
    import bq_linkedin_ads_service
    import bq_mart_service
    import bq_meta_ads_service
    import penn_config
    from concurrent.futures import ThreadPoolExecutor
    from dashboard.utils.formatting import platform_error

    start, end, preset = resolve_date_range(date_range)
    cfg = client_config.load_client_config("penn-bq-test")
    penn_cfg = penn_config.load_penn_config()
    linkedin_account_id = cfg.linkedin_account_id or penn_cfg.linkedin_account_id
    meta_account_id = cfg.meta_account_id or penn_cfg.meta_account_id

    # Start GSC in background while paid media queries run
    _gsc_executor = ThreadPoolExecutor(max_workers=1)
    _gsc_fut = _gsc_executor.submit(bq_gsc_service.build_gsc_snapshot, start=start, end=end)

    try:
        snapshot = bq_mart_service.build_snapshot(start=start, end=end, preset=preset)
        snapshot["label"] = cfg.label
        snapshot["date_range"] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        }
        snapshot.setdefault("accounts", {})["linkedin"] = linkedin_account_id
        snapshot.setdefault("accounts", {})["meta"] = meta_account_id
        snapshot.setdefault("data_sources", {})["google"] = "bigquery"

        # LinkedIn: sync metadata + rebuild mart, then override snapshot
        try:
            metadata_sync = bq_linkedin_ads_service.sync_campaign_metadata_and_rebuild_mart(
                account_id=linkedin_account_id,
                start=start,
                end=end,
            )
            snapshot.setdefault("warehouse_sync", {})["linkedin_campaign_metadata"] = metadata_sync

            linkedin_snapshot = bq_linkedin_ads_service.build_snapshot(
                cfg=penn_cfg,
                start=start,
                end=end,
                preset=preset,
            )
            linkedin_daily = (linkedin_snapshot.get("daily_metrics") or {}).get("linkedin", [])
            linkedin_totals = (linkedin_snapshot.get("platform_totals") or {}).get("linkedin", {})
            linkedin_breakdowns = (linkedin_snapshot.get("breakdowns") or {}).get("linkedin", {})

            snapshot.setdefault("daily_metrics", {})["linkedin"] = linkedin_daily
            snapshot.setdefault("platform_totals", {})["linkedin"] = linkedin_totals
            snapshot.setdefault("breakdowns", {})["linkedin"] = linkedin_breakdowns
            snapshot.setdefault("data_sources", {})["linkedin"] = "bigquery"
            creative_meta = linkedin_snapshot.get("creative_metadata") or {}
            snapshot.setdefault("data_sources", {})["linkedin_creative_metadata"] = creative_meta.get("source", "bigquery")
            snapshot["creative_metadata"] = creative_meta or {"source": "bigquery", "merged_rows": 0}
        except Exception as exc:
            message = f"Penn BQ Test LinkedIn BigQuery query failed: {platform_error(exc)}"
            snapshot.setdefault("errors", {})["linkedin_bigquery"] = message
            snapshot.setdefault("data_sources", {})["linkedin"] = "bigquery"
            snapshot.setdefault("data_sources", {})["linkedin_creative_metadata"] = "bigquery"
            snapshot.setdefault("creative_metadata", {"source": "bigquery", "merged_rows": 0})

        # Meta: sync API → BQ, then read from mart views
        if meta_account_id:
            try:
                meta_sync = bq_meta_ads_service.sync_meta_to_bq(
                    meta_account_id,
                    start=start,
                    end=end,
                )
                snapshot.setdefault("warehouse_sync", {})["meta"] = meta_sync

                meta_result = bq_meta_ads_service.build_meta_breakdowns(start=start, end=end)
                snapshot.setdefault("breakdowns", {})["meta"] = meta_result["breakdowns"]
                snapshot.setdefault("platform_totals", {})["meta"] = meta_result["platform_totals"]
                snapshot.setdefault("daily_metrics", {})["meta"] = meta_result.get("daily_metrics", [])
                snapshot.setdefault("data_sources", {})["meta"] = "bigquery"
                if meta_result.get("errors"):
                    snapshot.setdefault("errors", {}).update(meta_result["errors"])
            except Exception as exc:
                message = f"Penn BQ Test Meta BigQuery sync/query failed: {platform_error(exc)}"
                snapshot.setdefault("errors", {})["meta_bigquery"] = message
                snapshot.setdefault("data_sources", {})["meta"] = "bigquery"

        from dashboard.services.snapshot_metrics_service import aggregated_paid_media
        snapshot["aggregated_paid_media"] = aggregated_paid_media(snapshot.get("platform_totals") or {})
    except Exception as exc:
        message = f"Penn BQ Test dashboard failed: {platform_error(exc)}"
        snapshot = {
            "client_key": "penn-bq-test",
            "label": "Penn BQ Test",
            "date_range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "preset": preset,
            },
            "accounts": {"google": None, "linkedin": None, "meta": None},
            "data_sources": {
                "google": "bigquery",
                "linkedin": "bigquery",
                "meta": "bigquery",
                "linkedin_creative_metadata": "postgres",
            },
            "daily_metrics": {},
            "platform_totals": {},
            "breakdowns": {},
            "aggregated_paid_media": {},
            "business_line_campaigns": [],
            "warehouse_sync": {},
            "ga4_attribution": None,
            "ga4_pages": None,
            "creative_metadata": {"source": "postgres", "merged_rows": 0},
            "errors": {"penn_bq_test": message},
            "refresh_mode": "bigquery_linkedin",
        }
    finally:
        try:
            snapshot["gsc"] = _gsc_fut.result(timeout=60)
        except Exception as gsc_exc:
            snapshot.setdefault("errors", {})["gsc"] = str(gsc_exc)[:400]
        finally:
            _gsc_executor.shutdown(wait=False)

    snapshot["sync_meta"] = sync_meta(sync_trigger)
    dashboard_snapshots.save_snapshot("penn-bq-test", snapshot)
    return snapshot


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
