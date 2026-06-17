"""Main dashboard, refresh, insights, JSON, and cron sync routes."""

from __future__ import annotations

from urllib.parse import quote

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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

router = APIRouter(include_in_schema=False)
LOGGER = logging.getLogger(__name__)
PENN_BQ_TEST_LINKEDIN_SOURCE = "bigquery"

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
@router.get(
    "/dashboard/penn-bq-test",
    summary="Penn BQ Test — BigQuery mart dashboard (HTML)",
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

    from dates_util import resolve_date_range
    import bq_linkedin_ads_service
    import bq_mart_service
    import client_config
    from dashboard.services.snapshot_metrics_service import aggregated_paid_media
    from dashboard.utils.formatting import platform_error

    cfg = client_config.load_client_config("penn-bq-test")
    if PENN_BQ_TEST_LINKEDIN_SOURCE != "bigquery":
        raise HTTPException(status_code=500, detail="Penn BQ Test is not configured for LinkedIn BigQuery.")
    start, end, preset = resolve_date_range(view_range or "LAST_30_DAYS")
    LOGGER.info("LinkedIn source: BigQuery.")
    snapshot = bq_mart_service.build_snapshot(start=start, end=end, preset=preset)
    snapshot["label"] = cfg.label
    snapshot["date_range"] = {"start": start.isoformat(), "end": end.isoformat(), "preset": preset}
    snapshot.setdefault("data_sources", {})["google"] = "bigquery"
    try:
        linkedin_snapshot = bq_linkedin_ads_service.build_snapshot(
            cfg=cfg,
            start=start,
            end=end,
            preset=preset,
        )
        snapshot.setdefault("daily_metrics", {})["linkedin"] = (
            linkedin_snapshot.get("daily_metrics") or {}
        ).get("linkedin", [])
        snapshot.setdefault("platform_totals", {})["linkedin"] = (
            linkedin_snapshot.get("platform_totals") or {}
        ).get("linkedin", {})
        snapshot.setdefault("breakdowns", {})["linkedin"] = (
            linkedin_snapshot.get("breakdowns") or {}
        ).get("linkedin", {})
        snapshot.setdefault("data_sources", {})["linkedin"] = "bigquery"
        snapshot.setdefault("data_sources", {})["linkedin_creative_metadata"] = "postgres"
        snapshot["creative_metadata"] = linkedin_snapshot.get("creative_metadata") or {
            "source": "postgres",
            "merged_rows": 0,
        }
    except Exception as exc:
        message = f"Penn BQ Test LinkedIn BigQuery query failed: {platform_error(exc)}"
        snapshot.setdefault("errors", {})["linkedin_bigquery"] = message
        snapshot.setdefault("data_sources", {})["linkedin"] = "bigquery"
        snapshot.setdefault("data_sources", {})["linkedin_creative_metadata"] = "postgres"
        snapshot.setdefault("creative_metadata", {"source": "postgres", "merged_rows": 0})
    snapshot["aggregated_paid_media"] = aggregated_paid_media(snapshot.get("platform_totals") or {})
    return HTMLResponse(
        dashboard_service.render_penn_html(
            snapshot,
            client_slug="penn-bq-test",
            view_range=view_range,
            **html_kw,
        )
    )


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
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        snapshot = dashboard_snapshots.get_snapshot(slug)
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
    return HTMLResponse(
        dashboard_service.render_penn_html(
            snapshot, client_slug=slug, access_key=key, flash_message=flash, view_range=view_range
        )
    )
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
    preset = (date_range or "LAST_30_DAYS").strip().upper().replace("-", "_")
    if preset not in WAREHOUSE_DATE_RANGES:
        preset = "LAST_30_DAYS"
    try:
        if is_quick:
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
