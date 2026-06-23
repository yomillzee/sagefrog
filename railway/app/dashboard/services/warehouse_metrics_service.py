"""Warehouse sync and daily metrics loading from ad APIs + GA4."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import bigquery_warehouse
import ga4_warehouse_service
import google_ads_service
import linkedin_service
import meta_service
import warehouse
from dashboard.utils.formatting import platform_error
from penn_config import PennDashboardConfig


def totals_from_daily_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Account-level totals summed from metrics_daily rows."""
    return {
        "spend": sum(float(r.get("spend") or 0) for r in rows),
        "clicks": sum(int(r.get("clicks") or 0) for r in rows),
        "impressions": sum(int(r.get("impressions") or 0) for r in rows),
        "conversions": sum(float(r.get("conversions") or 0) for r in rows),
    }


def penn_sync_warehouses(
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
            payload["errors"]["google_sync"] = platform_error(exc)

    if cfg.linkedin_account_id:
        try:
            payload["warehouse_sync"]["linkedin"] = linkedin_service.sync_account_to_warehouse(
                cfg.linkedin_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["linkedin_sync"] = platform_error(exc)

    if cfg.meta_account_id:
        try:
            payload["warehouse_sync"]["meta"] = meta_service.sync_account_to_warehouse(
                cfg.meta_account_id,
                date_range=preset,
            )
        except Exception as exc:
            payload["errors"]["meta_sync"] = platform_error(exc)

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
            payload["errors"]["ga4_sync"] = platform_error(exc)

    return ga4_account


def penn_load_daily_metrics_from_warehouse(
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
        payload["errors"]["warehouse"] = "DATABASE_URL is not set â€” warehouse storage is disabled."
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
                    totals = totals_from_daily_rows(rows)
                    totals["campaign_count"] = 0
                    platform_totals[source] = totals
                continue
            totals = totals_from_daily_rows(rows)
            if source == "ga4":
                totals["campaign_count"] = 0
            platform_totals[source] = totals
        except Exception as exc:
            payload["errors"][f"{source}_daily"] = platform_error(exc)

    if update_platform_totals:
        from dashboard.services.snapshot_metrics_service import aggregated_paid_media

        payload["platform_totals"] = platform_totals
        payload["aggregated_paid_media"] = aggregated_paid_media(platform_totals)
    elif platform_totals != (payload.get("platform_totals") or {}):
        payload["platform_totals"] = platform_totals


def load_mtd_daily_metrics(cfg: PennDashboardConfig) -> dict[str, Any]:
    """Load paid-platform daily metrics for the current calendar month."""
    from dashboard.utils.dates import mtd_calendar_bounds

    month_start, _, today = mtd_calendar_bounds()
    payload: dict[str, Any] = {"daily_metrics": {}, "errors": {}}
    if warehouse.enabled():
        penn_load_daily_metrics_from_warehouse(
            cfg,
            start=month_start,
            end=today,
            payload=payload,
            ga4_account=None,
            update_platform_totals=False,
        )
        return payload.get("daily_metrics") or {}
    return {}


def load_organic_daily_metrics(
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
        totals = totals_from_daily_rows(rows)
        totals["campaign_count"] = 0
        platform_totals = dict(payload.get("platform_totals") or {})
        platform_totals["organic"] = totals
        payload["platform_totals"] = platform_totals
    except Exception as exc:
        payload.setdefault("errors", {})["organic_daily"] = platform_error(exc)


def merge_linkedin_creative_media(
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


def sync_campaign_daily(
    cfg: PennDashboardConfig,
    preset: str,
    payload: dict[str, Any],
) -> None:
    """Fetch per-campaign daily metrics from each ad API and write to campaign_daily."""
    if not warehouse.enabled():
        return
    from dates_util import resolve_date_range

    start, end, _ = resolve_date_range(preset)

    for source, account_id, fetch_fn in (
        ("google", cfg.google_customer_id, google_ads_service.fetch_campaign_daily_metrics),
        ("linkedin", cfg.linkedin_account_id, linkedin_service.fetch_campaign_daily_metrics),
        ("meta", cfg.meta_account_id, meta_service.fetch_campaign_daily_metrics),
    ):
        if not account_id:
            continue
        try:
            rows = fetch_fn(account_id, start=start, end=end)
            warehouse.upsert_campaign_daily_batch(source, account_id, rows)
            try:
                if source == "google":
                    # Google reporting data is owned exclusively by the native
                    # BigQuery transfer.  Legacy Postgres snapshots may remain,
                    # but they must never be promoted into normalized BQ facts.
                    continue
                bigquery_warehouse.mirror_campaign_daily_batch(source, account_id, rows)
                if source == "linkedin":
                    bigquery_warehouse.ensure_linkedin_campaigns_table()
                    bigquery_warehouse.rebuild_linkedin_campaign_daily_mart()
            except Exception as exc:
                # Surface a failed BQ mirror as a refresh warning instead of
                # swallowing it. A silent except here is exactly what made a
                # broken LinkedIn→BQ write look like a dead cron: the Postgres
                # warehouse stayed fresh while BQ silently fell behind.
                payload.setdefault("errors", {})[f"{source}_bq_mirror"] = platform_error(exc)
        except Exception as exc:
            payload.setdefault("errors", {})[f"{source}_campaign_daily_sync"] = platform_error(exc)


def load_campaign_daily_from_warehouse(
    cfg: PennDashboardConfig,
    *,
    start: date,
    end: date,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return campaign totals for [start, end] keyed by {source: {campaign_id: metrics}}.

    Returns {} if warehouse is not enabled or no data exists for the window.
    """
    if not warehouse.enabled():
        return {}
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for source, account_id in (
        ("google", cfg.google_customer_id),
        ("linkedin", cfg.linkedin_account_id),
        ("meta", cfg.meta_account_id),
    ):
        if not account_id:
            continue
        try:
            rows = warehouse.query_campaign_daily(source, account_id, start, end)
            if rows:
                result[source] = {r["campaign_id"]: r for r in rows}
        except Exception:
            pass
    return result


def load_campaign_daily_from_bq(
    *,
    client_key: str | None,
    google_account_id: str | None,
    linkedin_account_id: str | None,
    meta_account_id: str | None,
    start: date,
    end: date,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return campaign totals for [start, end] from BQ mart tables.

    Keyed as {source: {campaign_id: metrics}}. Used as BQ fallback when the
    Postgres warehouse is empty (BigQuery-mode clients only).
    """
    result: dict[str, dict[str, dict[str, Any]]] = {}
    bq_project_id = credentials_env = None
    try:
        import ga4_clients

        target = ga4_clients.resolve_target(client_key=client_key)
        bq_project_id = target.bq_project_id
        credentials_env = target.credentials_env or None
    except Exception:
        pass

    def _group(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        by_cid: dict[str, dict[str, Any]] = {}
        for row in rows:
            cid = str(row.get("campaign_id") or "")
            if not cid:
                continue
            if cid not in by_cid:
                by_cid[cid] = {"campaign_id": cid, "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0}
            by_cid[cid]["spend"] += float(row.get("spend") or 0)
            by_cid[cid]["clicks"] += int(row.get("clicks") or 0)
            by_cid[cid]["impressions"] += int(row.get("impressions") or 0)
            by_cid[cid]["conversions"] += float(row.get("conversions") or 0)
        return by_cid

    import bq_mart_service as _marts
    common = {
        "start": start,
        "end": end,
        "bq_project_id": bq_project_id,
        "credentials_env": credentials_env,
    }
    if google_account_id:
        try:
            rows = _marts.fetch_campaign_daily(**common)
            if grouped := _group(rows):
                result["google"] = grouped
        except Exception:
            pass

    if linkedin_account_id:
        try:
            rows = _marts.fetch_linkedin_campaign_daily(**common)
            if grouped := _group(rows):
                result["linkedin"] = grouped
        except Exception:
            pass

    if meta_account_id:
        try:
            import bq_meta_ads_service as _meta
            with _meta.route(
                bq_project_id=bq_project_id, credentials_env=credentials_env
            ):
                rows = _meta.fetch_meta_campaign_daily(start=start, end=end)
            if grouped := _group(rows):
                result["meta"] = grouped
        except Exception:
            pass

    return result


def sync_meta(trigger: str) -> dict[str, str]:
    return {
        "trigger": trigger,
        "completed_at": datetime.now(tz=UTC).isoformat(),
    }
