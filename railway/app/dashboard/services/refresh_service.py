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
    totals_from_daily_rows,
)


def _apply_mart_google_daily(
    cfg: PennDashboardConfig,
    payload: dict[str, Any],
    *,
    start,
    end,
) -> None:
    """Prefer the BigQuery campaign mart for the Google paid daily series + totals.

    The account-level Google Ads API daily (Postgres metrics_daily) sums every
    row including non-campaign / adjustment spend, so it can overstate the
    dashboard's Google total â€” e.g. Penn showed $8,238 unfiltered vs the mart's
    correct $6,475 (fact_google_ads_campaign_daily). The mart is the source of
    truth, so overwrite daily_metrics["google"] + platform_totals["google"] with
    it when available. Falls back silently to the API daily when the client has
    no mart project configured or the table isn't populated yet.
    """
    if not cfg.google_customer_id:
        return
    try:
        import bq_mart_service
    except Exception:
        return

    # Prefer this client's own gcp_project_id (client_dashboard_config) — the
    # ga4_clients registry lookup below is a legacy fallback for clients that
    # predate it. Resolving via ga4_clients alone and silently continuing with
    # bq_project_id=None on failure previously meant a resolution failure fell
    # through to bq_mart_service's own hardcoded default project (Penn's) --
    # i.e. this client's dashboard would silently read Penn's Google Ads mart
    # data. Returning early here instead of proceeding with an unresolved
    # project is what actually delivers the "skip silently" behavior this
    # function's docstring already promises.
    bq_project_id = credentials_env = None
    try:
        import client_dashboard_config as _cdc
        db_cfg = _cdc.get_config(cfg.client_key)
        bq_project_id = (db_cfg.gcp_project_id if db_cfg else None) or None
    except Exception:
        pass
    if not bq_project_id:
        try:
            import ga4_clients
            target = ga4_clients.resolve_target(client_key=cfg.ga4_client_key or cfg.client_key)
            bq_project_id = target.bq_project_id or None
            credentials_env = target.credentials_env or None
        except Exception:
            pass
    if not bq_project_id:
        return

    try:
        series = bq_mart_service.google_daily_series(
            start=start,
            end=end,
            bq_project_id=bq_project_id,
            credentials_env=credentials_env,
        )
    except Exception as exc:
        # Source set up but mart not built yet â†’ expected; fall back to API daily
        # silently. Only real failures (auth, permission, SQL) surface as warnings.
        if not bq_mart_service._is_table_not_found(exc):
            payload.setdefault("errors", {})["google_mart_daily"] = platform_error(exc)
        return

    if not series:
        return

    payload.setdefault("daily_metrics", {})["google"] = series["daily"]
    totals = dict(payload.get("platform_totals") or {})
    existing = dict(totals.get("google") or {})
    existing.update(series["totals"])
    totals["google"] = existing
    payload["platform_totals"] = totals
    payload.setdefault("data_sources", {})["google"] = "bigquery_mart"


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
            _ga4_pages_by_preset = ga4_page_service.fetch_pages_for_all_presets(
                client_key=cfg.ga4_client_key,
                client_slug=cfg.client_key,
            )
            payload["ga4_pages_by_preset"] = _ga4_pages_by_preset
            payload["ga4_pages"] = _ga4_pages_by_preset.get(preset) or _ga4_pages_by_preset.get("LAST_30_DAYS")
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
    _apply_mart_google_daily(cfg, payload, start=start, end=end)
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
    _apply_mart_google_daily(cfg, payload, start=start, end=end)
    payload["aggregated_paid_media"] = aggregated_paid_media(payload.get("platform_totals") or {})
    payload["sync_meta"] = sync_meta(sync_trigger)

    dashboard_snapshots.save_snapshot(cfg.client_key, payload)
    return payload


def refresh_penn_quick(*, date_range: str = "LAST_30_DAYS", sync_trigger: str = "manual_quick") -> dict[str, Any]:
    return refresh_client_quick(client_slug="penn", date_range=date_range, sync_trigger=sync_trigger)


def refresh_bq_client(
    slug: str,
    *,
    date_range: str = "LAST_30_DAYS",
    sync_trigger: str = "manual_full",
) -> dict[str, Any]:
    """Generic BigQuery-sourced refresh for any client with dashboard_mode='bigquery'.

    Reads all configuration (account IDs, GSC URL, SEMrush domain) from the
    client_dashboard_config table.
    """
    import bq_gsc_service
    import bq_linkedin_ads_service
    import bq_mart_service
    import bq_meta_ads_service
    import client_dashboard_config as _cdc
    import gsc_sync_service
    import semrush_service as _smr_svc
    from concurrent.futures import ThreadPoolExecutor
    from dashboard.utils.formatting import platform_error

    start, end, preset = resolve_date_range(date_range)
    cfg = client_config.load_client_config(slug)
    db_cfg = _cdc.get_config(slug)

    linkedin_account_id = cfg.linkedin_account_id
    meta_account_id     = cfg.meta_account_id
    ga4_client_key      = cfg.ga4_client_key or slug
    gsc_site_url        = (db_cfg.gsc_site_url if db_cfg else None) or ""
    semrush_domain      = (db_cfg.semrush_domain if db_cfg else None) or ""
    # GET/cache-miss reads are strictly read-only.  Only cron, onboarding, and
    # an explicit Full Refresh are allowed to run API ingestion.
    from dashboard.services.bigquery_refresh_orchestrator import should_run_ingestion

    run_ingestion = should_run_ingestion(sync_trigger)
    refresh_run: dict[str, Any] | None = None
    if run_ingestion:
        from dashboard.services.bigquery_refresh_orchestrator import (
            run_client_bigquery_refresh,
        )

        refresh_run = run_client_bigquery_refresh(
            client_slug=slug, trigger=sync_trigger
        )
    # Was hardcoded False, so the GSC/LinkedIn/Meta "repair" resyncs below
    # (creative metadata, thumbnails, etc.) were unreachable dead code --
    # nothing ever refreshed LinkedIn's creative_metadata automatically,
    # so its signed thumbnail URLs (which expire) went stale after whatever
    # one-off manual run last populated them. Tie it to the same gate as
    # ingestion itself: only do this heavier work on a real refresh cycle
    # (cron/onboarding/explicit Full Refresh), never on a cache-miss GET.
    repair_bq_sources = run_ingestion
    _bq_setup_error: str | None = None

    # Resolve client-specific BQ project/credentials for mart queries. Prefer
    # this client's own gcp_project_id (client_dashboard_config) -- resolving
    # via the ga4_clients registry alone and silently continuing with
    # bq_project_id=None on failure meant every mart read below
    # (bq_mart_service, bq_linkedin_ads_service, bq_meta_ads_service) fell
    # through to those modules' own hardcoded default project (Penn's), i.e.
    # this client's dashboard could silently render Penn's BigQuery data.
    _mart_bq_project: str | None = (db_cfg.gcp_project_id if db_cfg else None) or None
    _mart_credentials_env: str | None = None
    if not _mart_bq_project:
        try:
            import ga4_clients as _ga4c
            _ga4_target = _ga4c.resolve_target(client_key=ga4_client_key)
            _mart_bq_project = _ga4_target.bq_project_id or None
            _mart_credentials_env = _ga4_target.credentials_env or None
        except Exception:
            pass

    # Reuse cached SEMrush data if it's under 24 hours old â€” SEMrush scores
    # update at most daily and cost API tokens on every call.
    from datetime import datetime, timezone as _tz
    _existing = dashboard_snapshots.get_snapshot(slug) or {}
    _cached_smr = _existing.get("semrush") or {}
    _smr_age_hours: float = 999
    try:
        _fetched = _cached_smr.get("fetched_at")
        if _fetched:
            _smr_age_hours = (
                datetime.now(_tz.utc) - datetime.fromisoformat(_fetched)
            ).total_seconds() / 3600
    except Exception:
        pass
    _smr_fresh = _smr_age_hours < 24

    def _gsc_task():
        from dates_util import resolve_date_range as _resolve
        gsc_sync_result = None
        if repair_bq_sources and gsc_site_url:
            try:
                gsc_sync_result = gsc_sync_service.sync_for_refresh(site_url=gsc_site_url, client_slug=slug)
            except Exception as exc:
                gsc_sync_result = {"ok": False, "error": str(exc)[:300]}
        data = bq_gsc_service.build_gsc_snapshot(start=start, end=end, client_slug=slug)
        if gsc_sync_result:
            data["_sync_result"] = gsc_sync_result

        # Build per-preset snapshots so the renderer can serve alternate date ranges
        # without a live re-query when the user changes the date range toggle.
        by_preset: dict[str, Any] = {preset: data}
        for _p in ("LAST_7_DAYS", "LAST_30_DAYS", "LAST_90_DAYS"):
            if _p == preset:
                continue
            try:
                _s, _e, _ = _resolve(_p)
                by_preset[_p] = bq_gsc_service.build_gsc_snapshot(start=_s, end=_e, client_slug=slug)
            except Exception:
                pass
        return data, by_preset

    _pool = ThreadPoolExecutor(max_workers=2)
    _gsc_fut = _pool.submit(_gsc_task)
    _smr_fut = (
        None if _smr_fresh
        else _pool.submit(_smr_svc.build_semrush_snapshot, semrush_domain or None)
    )

    try:
        snapshot = bq_mart_service.build_snapshot(
            start=start, end=end, preset=preset,
            bq_project_id=_mart_bq_project,
            credentials_env=_mart_credentials_env,
            include_google=bool(cfg.google_customer_id),
            include_linkedin=bool(linkedin_account_id),
        )
        snapshot["label"]      = cfg.label
        snapshot["date_range"] = {"start": start.isoformat(), "end": end.isoformat(), "preset": preset}
        snapshot.setdefault("accounts", {})["google"] = cfg.google_customer_id
        snapshot.setdefault("accounts", {})["linkedin"] = linkedin_account_id
        snapshot.setdefault("accounts", {})["meta"]     = meta_account_id
        snapshot.setdefault("data_sources", {})["google"] = "bigquery"
        if refresh_run:
            snapshot["refresh_run"] = refresh_run
            snapshot["source_freshness"] = refresh_run.get("sources", {})
            snapshot["last_refreshed_at"] = refresh_run.get("last_refreshed_at")
            snapshot["data_through"] = {
                source: details.get("data_through")
                for source, details in (refresh_run.get("sources") or {}).items()
                if details.get("data_through")
            }
            # Surface ingestion failures as dashboard errors. A failed provision
            # step (e.g. the GA4 dataset can't be read to detect its BQ region)
            # bails the whole refresh before any source ingests — without this,
            # the page just looks "unchanged" with no clue why nothing synced.
            _prov = refresh_run.get("provision") or {}
            if _prov.get("status") == "failed":
                snapshot.setdefault("errors", {})["bigquery_provision"] = str(
                    _prov.get("error") or "BigQuery provisioning failed"
                )
            for _src, _det in (refresh_run.get("sources") or {}).items():
                if isinstance(_det, dict) and _det.get("status") == "failed":
                    snapshot.setdefault("errors", {})[f"{_src}_ingest"] = str(
                        _det.get("error") or f"{_src} ingestion failed"
                    )
        if _bq_setup_error:
            snapshot.setdefault("warnings", {})["bigquery_setup"] = _bq_setup_error

        if linkedin_account_id:
            try:
                # Route LinkedIn BQ reads to this client's project (datasets keep
                # the same names across clients). cfg must be passed â€” build_snapshot
                # uses cfg.client_key/label, so omitting it raised AttributeError and
                # silently broke LinkedIn for every generic BQ client.
                with bq_linkedin_ads_service.route(
                    bq_project_id=_mart_bq_project, credentials_env=_mart_credentials_env
                ):
                    if repair_bq_sources:
                        # Thread the client-scoped LinkedIn token so the creative
                        # sync (inside this call) authenticates as this client
                        # instead of the tokenless global env -- otherwise the
                        # creative mart silently never builds for connector clients.
                        _li_token = linkedin_service.resolve_client_access_token(cfg.client_key)
                        meta_sync = bq_linkedin_ads_service.sync_campaign_metadata_and_rebuild_mart(
                            account_id=linkedin_account_id, start=start, end=end,
                            access_token=_li_token,
                        )
                        snapshot.setdefault("warehouse_sync", {})["linkedin_campaign_metadata"] = meta_sync
                    li_snap = bq_linkedin_ads_service.build_snapshot(
                        cfg=cfg, account_id=linkedin_account_id, start=start, end=end, preset=preset,
                    )
                snapshot.setdefault("daily_metrics", {})["linkedin"]  = (li_snap.get("daily_metrics") or {}).get("linkedin", [])
                snapshot.setdefault("platform_totals", {})["linkedin"] = (li_snap.get("platform_totals") or {}).get("linkedin", {})
                snapshot.setdefault("breakdowns", {})["linkedin"]      = (li_snap.get("breakdowns") or {}).get("linkedin", {})
                snapshot.setdefault("data_sources", {})["linkedin"]    = "bigquery"
                creative_meta = li_snap.get("creative_metadata") or {}
                snapshot["creative_metadata"] = creative_meta or {"source": "bigquery", "merged_rows": 0}
            except Exception as exc:
                snapshot.setdefault("errors", {})["linkedin_bigquery"] = platform_error(exc)
                snapshot.setdefault("data_sources", {})["linkedin"] = "bigquery"

        if meta_account_id:
            try:
                with bq_meta_ads_service.route(
                    bq_project_id=_mart_bq_project, credentials_env=_mart_credentials_env
                ):
                    if repair_bq_sources:
                        m_sync = bq_meta_ads_service.sync_meta_to_bq(meta_account_id, start=start, end=end)
                        snapshot.setdefault("warehouse_sync", {})["meta"] = m_sync
                    meta_result = bq_meta_ads_service.build_meta_breakdowns(start=start, end=end)
                snapshot.setdefault("breakdowns", {})["meta"]      = meta_result["breakdowns"]
                snapshot.setdefault("platform_totals", {})["meta"] = meta_result["platform_totals"]
                snapshot.setdefault("daily_metrics", {})["meta"]   = meta_result.get("daily_metrics", [])
                snapshot.setdefault("data_sources", {})["meta"]    = "bigquery"
                if meta_result.get("errors"):
                    snapshot.setdefault("errors", {}).update(meta_result["errors"])
            except Exception as exc:
                snapshot.setdefault("errors", {})["meta_bigquery"] = platform_error(exc)
                snapshot.setdefault("data_sources", {})["meta"] = "bigquery"

        snapshot.setdefault("accounts", {})["ga4_client_key"] = ga4_client_key
        snapshot.setdefault("data_sources", {})["ga4"] = "bigquery"
        try:
            snapshot["ga4_attribution"] = ga4_attribution_service.fetch_attribution_for_dashboard(
                date_range=preset, client_key=ga4_client_key,
            )
        except Exception as exc:
            snapshot.setdefault("errors", {})["ga4_attribution"] = platform_error(exc)
        try:
            _ga4_by_preset = ga4_page_service.fetch_pages_for_all_presets(
                client_key=ga4_client_key, client_slug=slug,
            )
            snapshot["ga4_pages_by_preset"] = _ga4_by_preset
            snapshot["ga4_pages"] = _ga4_by_preset.get(preset) or _ga4_by_preset.get("LAST_30_DAYS")
        except Exception as exc:
            snapshot.setdefault("errors", {})["ga4_pages"] = platform_error(exc)
        try:
            import ga4_warehouse_service
            organic_rows = ga4_warehouse_service.fetch_organic_daily_metrics(
                start=start, end=end, client_key=ga4_client_key,
            )
            snapshot.setdefault("daily_metrics", {})["organic"] = organic_rows
            from dashboard.services.warehouse_metrics_service import totals_from_daily_rows
            organic_totals = totals_from_daily_rows(organic_rows)
            organic_totals["campaign_count"] = 0
            snapshot.setdefault("platform_totals", {})["organic"] = organic_totals
        except Exception as exc:
            snapshot.setdefault("errors", {})["organic_daily"] = platform_error(exc)

        from dashboard.services.snapshot_metrics_service import aggregated_paid_media
        snapshot["aggregated_paid_media"] = aggregated_paid_media(snapshot.get("platform_totals") or {})

    except Exception as exc:
        snapshot = {
            "client_key": slug,
            "label": cfg.label,
            "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
            "accounts": {"ga4_client_key": ga4_client_key},
            "data_sources": {"google": "bigquery", "linkedin": "bigquery", "meta": "bigquery", "ga4": "bigquery"},
            "daily_metrics": {}, "platform_totals": {}, "breakdowns": {},
            "aggregated_paid_media": {}, "warehouse_sync": {},
            "errors": {"bq_client": platform_error(exc)},
        }
        if _bq_setup_error:
            snapshot.setdefault("warnings", {})["bigquery_setup"] = _bq_setup_error
    finally:
        try:
            _gsc_data, _gsc_by_preset = _gsc_fut.result(timeout=60)
            snapshot["gsc"] = _gsc_data
            snapshot["gsc_by_preset"] = _gsc_by_preset
        except Exception as exc:
            snapshot.setdefault("errors", {})["gsc"] = str(exc)[:400]
        if _smr_fresh:
            if _cached_smr:
                snapshot["semrush"] = _cached_smr
        else:
            try:
                smr = _smr_fut.result(timeout=30)
                if smr and not smr.get("error", "").startswith("SEMRUSH_API_KEY"):
                    snapshot["semrush"] = smr
            except Exception as exc:
                snapshot.setdefault("errors", {})["semrush"] = str(exc)[:400]
        _pool.shutdown(wait=False)

    snapshot["sync_meta"] = sync_meta(sync_trigger)
    dashboard_snapshots.save_snapshot(slug, snapshot)
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
