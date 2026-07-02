"""Main dashboard, refresh, insights, JSON, and cron sync routes."""

from __future__ import annotations

from urllib.parse import quote

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import dashboard_service
import dashboard_snapshots
import web_auth
import web_users
from cron_security import require_cron_secret
from dashboard.routes.helpers import (
    penn_html_session_kwargs,
    validate_client_slug,
)
from dashboard.utils.dates import WAREHOUSE_DATE_RANGES
from dashboard.utils.formatting import platform_error

router = APIRouter(include_in_schema=False)
LOGGER = logging.getLogger(__name__)

@router.post(
    "/internal/sync-penn",
    summary="Cron: sync Penn warehouse + refresh dashboard snapshot",
    dependencies=[Depends(require_cron_secret)],
)
def internal_sync_penn(date_range: str = "LAST_30_DAYS") -> dict:
    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )
    try:
        return dashboard_service.refresh_penn(date_range=preset, sync_trigger="cron")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/internal/sync-penn-bq-test",
    summary="Cron: refresh Penn BQ Test BigQuery snapshot",
    dependencies=[Depends(require_cron_secret)],
)
def internal_sync_penn_bq_test(date_range: str = "LAST_30_DAYS") -> dict:
    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date_range. Use one of: {', '.join(sorted(WAREHOUSE_DATE_RANGES))}",
        )
    try:
        return dashboard_service.refresh_penn_bq_test(date_range=preset, sync_trigger="cron")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
@router.get(
    "/dashboard/penn-bq-test",
    summary="Penn BQ Test â€” BigQuery mart dashboard (HTML)",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_penn_bq_test(
    request: Request,
    key: str | None = None,
    view_range: str | None = None,
):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="penn-bq-test", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        html_kw = penn_html_session_kwargs(auth)
    else:
        dashboard_service.verify_dashboard_key(key)
        html_kw = {"access_key": key, "use_session": False}

    # Cache-first: read from Postgres snapshot; run live queries only on cache miss
    snapshot = dashboard_snapshots.get_snapshot("penn-bq-test")
    if not snapshot:
        LOGGER.info("Penn BQ Test: no cached snapshot â€” running live BQ queries")
        try:
            snapshot = dashboard_service.refresh_penn_bq_test(
                date_range=view_range or "LAST_30_DAYS",
                sync_trigger="cache_miss",
            )
        except Exception as exc:
            LOGGER.exception("Penn BQ Test dashboard failed before render")
            snapshot = {
                "client_key": "penn-bq-test",
                "label": "Penn BQ Test",
                "errors": {"penn_bq_test": f"Dashboard failed: {platform_error(exc)}"},
            }
    try:
        html = dashboard_service.render_penn_html(
            snapshot,
            client_slug="penn-bq-test",
            view_range=view_range,
            **html_kw,
        )
    except Exception as exc:
        LOGGER.exception("Penn BQ Test dashboard failed during render")
        html = (
            "<h1>Penn BQ Test dashboard error</h1>"
            f"<p>{platform_error(exc)}</p>"
            "<p>Check the application logs for details.</p>"
        )
    return HTMLResponse(html)


@router.post(
    "/internal/sync-hubspot",
    summary="Cron: sync HubSpot MQL/SQL contacts into BigQuery",
    dependencies=[Depends(require_cron_secret)],
)
def internal_sync_hubspot(lookback_days: int = 7) -> dict:
    days = max(1, min(int(lookback_days), 365))
    try:
        import hubspot_sync_service
        result = hubspot_sync_service.sync_hubspot_contacts(lookback_days=days)
        return {"hubspot_sync": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/internal/sync-bq/{client_slug}",
    summary="Cron: refresh any BigQuery-mode client dashboard snapshot",
    dependencies=[Depends(require_cron_secret)],
)
def internal_sync_bq_client(client_slug: str, date_range: str = "LAST_30_DAYS") -> dict:
    slug = validate_client_slug(client_slug)
    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid date_range.")
    try:
        return dashboard_service.refresh_bq_client(slug, date_range=preset, sync_trigger="cron")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/internal/sync-bq-all",
    summary="Cron: refresh every client that has at least one connector configured",
    dependencies=[Depends(require_cron_secret)],
)
def internal_sync_bq_all(date_range: str = "LAST_30_DAYS") -> dict:
    """Hands-off daily sync: no per-client cron provisioning required.

    Connecting a source via the Connectors wizard is enough — this endpoint
    re-derives the client list from connector_configs on every run, so a
    newly connected client is picked up on the next cron tick automatically.
    sync_enabled still gates which connectors within each client actually
    run (bigquery_refresh_orchestrator); a client with zero enabled
    connectors just no-ops for that run.
    """
    import connector_config_store

    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        raise HTTPException(status_code=400, detail="Invalid date_range.")

    slugs = sorted(connector_config_store.client_slugs_with_configs())
    results: dict[str, dict] = {}
    failed: list[str] = []
    for slug in slugs:
        try:
            results[slug] = dashboard_service.refresh_bq_client(
                slug, date_range=preset, sync_trigger="cron"
            )
        except Exception as exc:
            # One client failing (bad credentials, BQ outage, etc.) must not
            # block every other client's daily sync.
            LOGGER.exception("sync-bq-all failed for client=%s", slug)
            failed.append(slug)
            results[slug] = {"status": "failed", "error": str(exc)[:500]}
    return {"clients_synced": len(slugs), "failed": failed, "results": results}


@router.post(
    "/internal/backfill-bq/{client_slug}",
    summary="Onboarding: ingest 180 days for a BigQuery-mode client",
    dependencies=[Depends(require_cron_secret)],
)
def internal_backfill_bq_client(client_slug: str) -> dict:
    slug = validate_client_slug(client_slug)
    try:
        return dashboard_service.refresh_bq_client(
            slug, date_range="LAST_180_DAYS", sync_trigger="onboarding"
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/dashboard/{client_slug}",
    summary="Client ads performance dashboard (HTML)",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_client(
    client_slug: str,
    request: Request,
    key: str | None = None,
    view_range: str | None = None,
    synced: str | None = None,
    budget_saved: str | None = None,
    insights_saved: str | None = None,
    doc_uploaded: str | None = None,
    doc_deleted: str | None = None,
    doc_error: str | None = None,
):
    slug = validate_client_slug(client_slug)
    if doc_error:
        flash = str(doc_error).strip()[:300]
    elif doc_uploaded:
        flash = "Insight document uploaded."
    elif doc_deleted:
        flash = "Insight document deleted."
    elif insights_saved:
        flash = "Insights saved."
    elif synced:
        flash = "Dashboard refreshed."
    elif budget_saved:
        flash = "Monthly budget saved."
    else:
        flash = None
    import client_dashboard_config as _cdc

    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        snapshot = dashboard_snapshots.get_snapshot(slug)
        if not snapshot:
            db_cfg = _cdc.get_config(slug)
            if db_cfg and db_cfg.dashboard_mode == "bigquery":
                try:
                    snapshot = dashboard_service.refresh_bq_client(slug, sync_trigger="cache_miss")
                except Exception as exc:
                    LOGGER.exception("BQ client dashboard cache miss refresh failed: %s", slug)
                    snapshot = {"client_key": slug, "errors": {"bq": platform_error(exc)}}
        return HTMLResponse(
            dashboard_service.render_penn_html(
                snapshot,
                client_slug=slug,
                flash_message=flash,
                view_range=view_range,
                **penn_html_session_kwargs(auth),
            )
        )
    dashboard_service.verify_dashboard_key(key)
    snapshot = dashboard_snapshots.get_snapshot(slug)
    if not snapshot:
        db_cfg = _cdc.get_config(slug)
        if db_cfg and db_cfg.dashboard_mode == "bigquery":
            try:
                snapshot = dashboard_service.refresh_bq_client(slug, sync_trigger="cache_miss")
            except Exception as exc:
                LOGGER.exception("BQ client dashboard cache miss refresh failed: %s", slug)
                snapshot = {"client_key": slug, "errors": {"bq": platform_error(exc)}}
    return HTMLResponse(
        dashboard_service.render_penn_html(
            snapshot, client_slug=slug, access_key=key, flash_message=flash, view_range=view_range
        )
    )
@router.post(
    "/dashboard/penn-bq-test/refresh",
    summary="Refresh Penn BQ Test BigQuery snapshot (rate-limited)",
    response_class=HTMLResponse,
    response_model=None,
    include_in_schema=False,
)
def dashboard_penn_bq_test_refresh(
    request: Request,
    key: str | None = None,
    date_range: str = "LAST_30_DAYS",
):
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug="penn-bq-test", key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        html_kw = penn_html_session_kwargs(auth)
        access_key = auth.access_key
        use_session = auth.use_session
    else:
        dashboard_service.verify_dashboard_key(key)
        html_kw = {"access_key": key, "use_session": False}
        access_key = key
        use_session = False

    snapshot = dashboard_snapshots.get_snapshot("penn-bq-test")
    allowed, remaining = dashboard_service.refresh_cooldown_status(snapshot, quick=False)
    if not allowed:
        mins = max(1, (remaining + 59) // 60)
        return HTMLResponse(
            dashboard_service.render_penn_html(
                snapshot,
                client_slug="penn-bq-test",
                flash_message=f"Please wait ~{mins} minutes before another refresh.",
                **html_kw,
            ),
            status_code=429,
        )
    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        preset = "LAST_30_DAYS"
    try:
        dashboard_service.refresh_penn_bq_test(date_range=preset, sync_trigger="manual_full")
    except Exception as e:
        return HTMLResponse(
            dashboard_service.render_penn_html(
                snapshot,
                client_slug="penn-bq-test",
                flash_message=f"Refresh failed: {str(e)[:200]}",
                **html_kw,
            ),
            status_code=400,
        )
    if use_session:
        return RedirectResponse(url="/dashboard/penn-bq-test?synced=1", status_code=303)
    return RedirectResponse(
        url=f"/dashboard/penn-bq-test?key={quote(access_key or '', safe='')}&synced=1",
        status_code=303,
    )


@router.get(
    "/dashboard/{client_slug}/ga4-backfill",
    summary="GA4 BigQuery export daily coverage (backfill progress)",
    response_model=None,
    include_in_schema=False,
)
def dashboard_ga4_backfill(
    client_slug: str,
    request: Request,
    key: str | None = None,
    days: int = 365,
):
    """Daily GA4 session/page-view counts straight from the BigQuery export.

    Live, on-demand (not from the snapshot) so the Website Analytics tab can
    show how far the GA4 Data Transfer backfill has progressed â€” zero/empty
    days are dates not yet loaded.
    """
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return JSONResponse({"ok": False, "error": "auth_required"}, status_code=401)
    else:
        dashboard_service.verify_dashboard_key(key)

    import client_config
    import ga4_clients
    import ga4_warehouse_service
    from datetime import date, timedelta

    window = max(7, min(int(days or 365), 540))
    end = date.today()
    start = end - timedelta(days=window)
    cfg = client_config.load_client_config(slug)
    ga4_key = cfg.ga4_client_key or slug
    try:
        target = ga4_clients.resolve_target(client_key=ga4_key)
        rows = ga4_warehouse_service.fetch_daily_metrics(start=start, end=end, target=target)
    except Exception as exc:
        LOGGER.exception("GA4 backfill coverage query failed: %s", slug)
        return JSONResponse({"ok": False, "error": platform_error(exc)}, status_code=200)

    days_out = [
        {
            "date": r.get("metric_date"),
            "sessions": int(r.get("clicks") or 0),
            "page_views": int(r.get("impressions") or 0),
        }
        for r in rows
    ]
    covered = [d for d in days_out if d["sessions"] or d["page_views"]]
    return JSONResponse(
        {
            "ok": True,
            "days": days_out,
            "covered_days": len(covered),
            "total_days": len(days_out),
            "first_covered": covered[0]["date"] if covered else None,
            "last_covered": covered[-1]["date"] if covered else None,
        }
    )


@router.get(
    "/dashboard/{client_slug}/data-source-status",
    summary="Per-source BigQuery feed status (settings panel)",
    response_model=None,
    include_in_schema=False,
)
def dashboard_data_source_status(
    client_slug: str,
    request: Request,
    key: str | None = None,
):
    """Read-only health of each data source's BigQuery feed for the settings page."""
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return JSONResponse({"ok": False, "error": "auth_required"}, status_code=401)
    else:
        dashboard_service.verify_dashboard_key(key)
    try:
        import data_source_status
        sources = data_source_status.build_status(slug)
        pipeline = data_source_status.build_pipeline_summary(slug, sources)
        return JSONResponse({"ok": True, "sources": sources, "pipeline": pipeline})
    except Exception as exc:
        LOGGER.exception("data source status failed: %s", slug)
        return JSONResponse({"ok": False, "error": platform_error(exc)}, status_code=200)


@router.post(
    "/dashboard/{client_slug}/refresh",
    summary="Refresh client dashboard snapshot (rate-limited)",
    response_class=HTMLResponse,
    response_model=None,
    include_in_schema=False,
)
def dashboard_client_refresh(
    client_slug: str,
    request: Request,
    key: str | None = None,
    date_range: str = "LAST_30_DAYS",
    quick: str | None = Form(None),
):
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
    else:
        dashboard_service.verify_dashboard_key(key)
        access_key = key
        use_session = False

    snapshot = dashboard_snapshots.get_snapshot(slug)
    is_quick = str(quick or "").strip().lower() in ("1", "true", "yes", "on")
    allowed, remaining = dashboard_service.refresh_cooldown_status(snapshot, quick=is_quick)
    html_kw = (
        penn_html_session_kwargs(auth)
        if web_users.enabled()
        else {"access_key": access_key, "use_session": use_session}
    )
    if not allowed:
        mins = max(1, (remaining + 59) // 60)
        kind = "quick" if is_quick else "full"
        return HTMLResponse(
            dashboard_service.render_penn_html(
                snapshot,
                client_slug=slug,
                flash_message=f"Please wait ~{mins} minutes before another {kind} refresh.",
                **html_kw,
            ),
            status_code=429,
        )
    import client_dashboard_config as _cdc
    db_cfg = _cdc.get_config(slug)
    is_bq_mode = bool(db_cfg and db_cfg.dashboard_mode == "bigquery")

    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        preset = "LAST_30_DAYS"
    try:
        if is_bq_mode:
            dashboard_service.refresh_bq_client(
                slug, date_range=preset, sync_trigger="manual_full"
            )
        elif is_quick:
            dashboard_service.refresh_client_quick(
                client_slug=slug, date_range=preset, sync_trigger="manual_quick"
            )
        else:
            dashboard_service.refresh_client(
                client_slug=slug, date_range=preset, sync_trigger="manual_full"
            )
    except Exception as e:
        return HTMLResponse(
            dashboard_service.render_penn_html(
                snapshot,
                client_slug=slug,
                flash_message=f"Refresh failed: {str(e)[:200]}",
                **html_kw,
            ),
            status_code=400,
        )
    if use_session:
        return RedirectResponse(url=f"/dashboard/{slug}?synced=1", status_code=303)
    return RedirectResponse(
        url=f"/dashboard/{slug}?key={quote(access_key or '', safe='')}&synced=1",
        status_code=303,
    )
@router.post(
    "/dashboard/{client_slug}/insights",
    summary="Save client dashboard insights text",
    response_class=HTMLResponse,
    response_model=None,
    include_in_schema=False,
)
def dashboard_client_insights(
    client_slug: str,
    request: Request,
    body: str = Form(""),
    key: str | None = None,
):
    slug = validate_client_slug(client_slug)
    auth = None
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        if not dashboard_service.can_edit_penn_insights(
            session_is_admin=bool(user and user.role == "admin"),
            access_key=access_key,
        ):
            snapshot = dashboard_snapshots.get_snapshot(slug)
            return HTMLResponse(
                dashboard_service.render_penn_html(
                    snapshot,
                    client_slug=slug,
                    flash_message="Only admins can edit insights.",
                    **penn_html_session_kwargs(auth),
                ),
                status_code=403,
            )
        updated_by = user.email if user else None
    else:
        dashboard_service.verify_dashboard_key(key)
        access_key = key
        use_session = False
        updated_by = "dashboard_key"

    try:
        dashboard_service.save_penn_insights(body, updated_by=updated_by, client_key=slug)
    except Exception as e:
        snapshot = dashboard_snapshots.get_snapshot(slug)
        html_kw = (
            penn_html_session_kwargs(auth)
            if web_users.enabled()
            else {"access_key": access_key, "use_session": use_session}
        )
        return HTMLResponse(
            dashboard_service.render_penn_html(
                snapshot,
                client_slug=slug,
                flash_message=f"Could not save insights: {str(e)[:200]}",
                **html_kw,
            ),
            status_code=400,
        )

    if use_session:
        return RedirectResponse(url=f"/dashboard/{slug}?insights_saved=1", status_code=303)
    return RedirectResponse(
        url=f"/dashboard/{slug}?key={quote(access_key or '', safe='')}&insights_saved=1",
        status_code=303,
    )
@router.get(
    "/dashboard/{client_slug}.json",
    summary="Client dashboard snapshot JSON",
    include_in_schema=False,
)
def dashboard_client_json(client_slug: str, request: Request, key: str | None = None) -> dict:
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        web_auth.authenticate_dashboard_api(request, client_slug=slug, key=key)
    else:
        dashboard_service.verify_dashboard_key(key)
    snapshot = dashboard_snapshots.get_snapshot(slug)
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"No dashboard snapshot yet for '{slug}'. Run a refresh from Settings.",
        )
    return snapshot
@router.get(
    "/dashboard/penn",
    summary="Penn ads performance dashboard (HTML, legacy alias)",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_penn_legacy(
    request: Request,
    key: str | None = None,
    view_range: str | None = None,
    synced: str | None = None,
    insights_saved: str | None = None,
):
    return dashboard_client(
        client_slug="penn",
        request=request,
        key=key,
        view_range=view_range,
        synced=synced,
        insights_saved=insights_saved,
    )
