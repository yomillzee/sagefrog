"""Consent & Tracking Health routes.

    GET  /dashboard/{slug}/consent          the report page (HTML)
    GET  /dashboard/{slug}/consent.json     the full overview as JSON (API)
    GET  /dashboard/{slug}/consent/status    latest run status (for live polling)
    POST /dashboard/{slug}/consent/scan      trigger a manual scan (background)
    POST /dashboard/{slug}/consent/config    save scan config + optionally scan now
    POST /internal/consent/scan-due          cron: scan every client whose cadence is due

The manual scan mirrors the connector-sync pattern: a run row is created up front
so the UI can immediately show "scanning", then the real work runs as a
BackgroundTask that finishes the row.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import audit_log
import consent_service
import consent_store
import web_auth
import web_users
from consent_scanner import validate_scan_url
from cron_security import require_cron_secret
from dashboard.renderers.consent_renderer import render_consent_page
from dashboard.routes.helpers import validate_client_slug

_log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)


def _label_for(slug: str) -> str:
    try:
        import client_dashboard_config
        cfg = client_dashboard_config.get_config(slug)
        return (getattr(cfg, "label", None) or slug).strip() or slug
    except Exception:
        return slug


def _run_scan_bg(slug: str, run_id: int, *, trigger: str, triggered_by: str | None) -> None:
    try:
        consent_service.run_scan(slug, trigger=trigger, triggered_by=triggered_by, run_id=run_id)
    except Exception:
        _log.exception("consent scan background task failed [%s]", slug)
        try:
            consent_store.finish_run(run_id, status="failed", error_message="Scan crashed.")
        except Exception:
            pass


# ── Report page ─────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/consent", response_class=HTMLResponse)
def consent_page(client_slug: str, request: Request):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    overview = consent_service.get_overview(slug)
    user = auth.user
    return HTMLResponse(render_consent_page(
        client_slug=slug,
        label=_label_for(slug),
        overview=overview,
        access_key=auth.access_key,
        use_session=auth.use_session,
        session_email=user.email if user else None,
        session_is_admin=bool(user and user.role == "admin"),
    ))


@router.get("/dashboard/{client_slug}/consent.json")
def consent_json(client_slug: str, request: Request):
    slug = validate_client_slug(client_slug)
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    return JSONResponse(consent_service.get_overview(slug))


@router.get("/dashboard/{client_slug}/consent/status")
def consent_status(client_slug: str, request: Request):
    """Lightweight latest-run status for the page's polling loop."""
    slug = validate_client_slug(client_slug)
    web_auth.authenticate_dashboard_api(request, client_slug=slug)
    run = consent_store.latest_run_any(slug)
    if not run:
        return JSONResponse({"status": "none"})
    return JSONResponse({
        "status": run.status,
        "health": run.health,
        "run_id": run.id,
        "violation_count": run.violation_count,
        "error_message": run.error_message,
    })


# ── Manual scan ─────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/consent/scan")
def consent_scan(client_slug: str, request: Request, background_tasks: BackgroundTasks):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    email = user.email if user else None

    config = consent_store.get_config(slug)
    if not config or not config.pages:
        return JSONResponse({"ok": False, "error": "Configure pages to scan first."}, status_code=400)

    # Refuse to stack a second scan on top of one already running.
    latest = consent_store.latest_run_any(slug)
    if latest and latest.status == "running":
        return JSONResponse({"ok": True, "run_id": latest.id, "message": "A scan is already running."})

    run_id = consent_store.start_run(slug, trigger="manual", triggered_by=email)
    audit_log.record(
        action="consent.scan_started",
        actor_email=email,
        detail={"client_slug": slug, "pages": len(config.pages), "run_id": run_id},
        **audit_log.request_context(request),
    )
    background_tasks.add_task(_run_scan_bg, slug, run_id, trigger="manual", triggered_by=email)
    return JSONResponse({"ok": True, "run_id": run_id, "message": "Scan started."})


# ── Save config ─────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/consent/config")
async def consent_config_save(client_slug: str, request: Request, background_tasks: BackgroundTasks):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    email = user.email if user else None
    is_admin = bool(user and user.role == "admin")

    # Config changes (which pages we scan, what we expect) are an admin action,
    # consistent with the platform's other client-settings writes.
    if web_users.enabled() and not is_admin:
        return JSONResponse({"ok": False, "error": "Only admins can change scan settings."}, status_code=403)
    if not consent_store.enabled():
        return JSONResponse({"ok": False, "error": "DATABASE_URL is required to save settings."}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    raw_pages = body.get("pages") or []
    if isinstance(raw_pages, str):
        raw_pages = [p for p in raw_pages.splitlines()]
    pages: list[str] = []
    errors: list[str] = []
    for p in raw_pages:
        ok, normalized_or_err = validate_scan_url(str(p))
        if ok:
            if normalized_or_err not in pages:
                pages.append(normalized_or_err)
        elif str(p).strip():
            errors.append(normalized_or_err)
    if errors:
        return JSONResponse({"ok": False, "error": errors[0]}, status_code=400)
    if not pages:
        return JSONResponse({"ok": False, "error": "Add at least one page URL to scan."}, status_code=400)

    import consent_evaluator as ev
    cats = body.get("require_consent_categories")
    expectations = ev.normalize_expectations({
        "require_consent_categories": cats if isinstance(cats, list) else None,
        "allow_consent_mode_pings": bool(body.get("allow_consent_mode_pings", True)),
        "banner_required": bool(body.get("banner_required", True)),
        "strict_reject": bool(body.get("strict_reject", True)),
    })
    frequency = str(body.get("scan_frequency") or "weekly").strip().lower()
    scan_enabled = bool(body.get("scan_enabled"))

    try:
        consent_store.save_config(
            slug,
            label=_label_for(slug),
            pages=pages,
            expectations=expectations,
            scan_options={},
            scan_enabled=scan_enabled,
            scan_frequency=frequency,
            updated_by=email,
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)

    audit_log.record(
        action="consent.config_saved",
        actor_email=email,
        detail={"client_slug": slug, "pages": len(pages), "scan_enabled": scan_enabled,
                "scan_frequency": frequency},
        **audit_log.request_context(request),
    )

    run_id = None
    if body.get("run_now"):
        latest = consent_store.latest_run_any(slug)
        if not (latest and latest.status == "running"):
            run_id = consent_store.start_run(slug, trigger="manual", triggered_by=email)
            background_tasks.add_task(_run_scan_bg, slug, run_id, trigger="manual", triggered_by=email)

    return JSONResponse({"ok": True, "pages": pages, "run_id": run_id})


# ── Scheduled scanning (cron) ───────────────────────────────────────────────

_SCAN_DUE_LOCK = "consent-scan-due"


@router.post("/internal/consent/scan-due", dependencies=[Depends(require_cron_secret)])
def internal_consent_scan_due(background_tasks: BackgroundTasks) -> dict:
    """Scan every client whose scheduled cadence is due. Hands-off, like sync-bq-all.

    Returns immediately; the scans run as a bounded background batch. A Postgres
    lock prevents a slow run from stacking on the next cron tick.
    """
    import cron_locks

    if not consent_store.enabled():
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")

    slugs = consent_store.clients_due_for_scan()
    if not slugs:
        return {"status": "ok", "due": 0, "slugs": []}
    if not cron_locks.try_acquire(_SCAN_DUE_LOCK, ttl_seconds=2 * 3600, locked_by="consent-scan-due"):
        return {"status": "skipped", "reason": "previous run still in progress", "slugs": slugs}

    def _run_all() -> None:
        try:
            for slug in slugs:
                try:
                    run_id = consent_store.start_run(slug, trigger="scheduled", triggered_by="cron")
                    consent_service.run_scan(slug, trigger="scheduled", triggered_by="cron", run_id=run_id)
                except Exception:
                    _log.exception("scheduled consent scan failed [%s]", slug)
        finally:
            cron_locks.release(_SCAN_DUE_LOCK)

    background_tasks.add_task(_run_all)
    return {"status": "started", "due": len(slugs), "slugs": slugs}
