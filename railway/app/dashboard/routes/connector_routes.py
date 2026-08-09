"""Connector setup wizard, management, and sync routes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import audit_log
import client_dashboard_config
import connector_config_store
import connectors  # noqa: F401 — triggers handler registration
import oauth_store
import web_auth
from connectors.base import get as get_handler
from dashboard.renderers import connectors_renderer
from dashboard.routes.helpers import validate_client_slug

_log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)


# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers (mirrors settings_routes pattern)
# ──────────────────────────────────────────────────────────────────────────────

def _auth(request: Request, client_slug: str):
    # Session-only since ?key= was retired. access_key/use_session remain in the
    # tuple (always None/True) for the renderer plumbing that still threads them.
    auth = web_auth.authenticate_dashboard(request, client_slug=client_slug)
    if isinstance(auth, RedirectResponse):
        return auth, None, None, None, None
    user = auth.user
    return (
        None,
        auth.access_key,
        auth.use_session,
        user.email if user else None,
        bool(user and user.role == "admin"),
    )


def _session_kw(access_key, use_session, session_email, session_is_admin) -> dict:
    return {
        "access_key": access_key,
        "use_session": use_session,
        "session_email": session_email,
        "session_is_admin": session_is_admin,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Directory page
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/connectors", response_class=HTMLResponse)
def connectors_directory(
    client_slug: str,
    request: Request,
    connected: str | None = None,
    disconnected: str | None = None,
):
    slug = validate_client_slug(client_slug)
    redirect, access_key, use_session, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return redirect

    import client_config
    cfg = client_config.load_client_config(slug)
    label = cfg.label if cfg else slug

    configs = connector_config_store.list_configs(slug)
    flash = None
    if connected:
        flash = f"{connected} connected successfully."
    if disconnected:
        flash = f"{disconnected} disconnected."

    return HTMLResponse(connectors_renderer.render_connectors_directory(
        client_slug=slug,
        label=label,
        configs=configs,
        flash_message=flash,
        **_session_kw(access_key, use_session, session_email, session_is_admin),
    ))


# ──────────────────────────────────────────────────────────────────────────────
# Lead Tracking page (HubSpot reports)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/lead-tracking", response_class=HTMLResponse)
def lead_tracking_page(
    client_slug: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    redirect, access_key, use_session, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return redirect

    import client_config
    import hubspot_reports_service
    from dashboard.renderers import lead_tracking_renderer

    cfg = client_config.load_client_config(slug)
    label = cfg.label if cfg else slug

    report = hubspot_reports_service.build_report(slug)
    return HTMLResponse(lead_tracking_renderer.render_lead_tracking(
        client_slug=slug,
        label=label,
        report=report,
        **_session_kw(access_key, use_session, session_email, session_is_admin),
    ))


# ──────────────────────────────────────────────────────────────────────────────
# Email Performance page (HubSpot marketing emails)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/email-performance", response_class=HTMLResponse)
def email_performance_page(
    client_slug: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    redirect, access_key, use_session, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return redirect

    import client_config
    import hubspot_reports_service
    from dashboard.renderers import email_performance_renderer

    cfg = client_config.load_client_config(slug)
    label = cfg.label if cfg else slug

    report = hubspot_reports_service.fetch_email_performance(slug)
    cfg_row = client_dashboard_config.get_config(slug)
    saved_selection = list(cfg_row.email_performance_selection) if cfg_row else []
    return HTMLResponse(email_performance_renderer.render_email_performance(
        client_slug=slug,
        label=label,
        report=report,
        saved_selection=saved_selection,
        **_session_kw(access_key, use_session, session_email, session_is_admin),
    ))


# ──────────────────────────────────────────────────────────────────────────────
# LinkedIn Organic page (company-page posts, followers, page analytics)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/linkedin-organic", response_class=HTMLResponse)
def linkedin_organic_page(
    client_slug: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    redirect, access_key, use_session, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return redirect

    import client_config
    import linkedin_organic_report_service
    from dashboard.renderers import linkedin_organic_renderer

    cfg = client_config.load_client_config(slug)
    label = cfg.label if cfg else slug

    range_days = linkedin_organic_renderer.sanitize_range_days(
        request.query_params.get("range")
    )
    report = linkedin_organic_report_service.build_report(slug, days=range_days)
    return HTMLResponse(linkedin_organic_renderer.render_linkedin_organic(
        client_slug=slug,
        label=label,
        report=report,
        range_days=range_days,
        **_session_kw(access_key, use_session, session_email, session_is_admin),
    ))


# ──────────────────────────────────────────────────────────────────────────────
# Connector detail page (wizard or management)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/connectors/{connector_type}/reauth", response_class=HTMLResponse)
def connector_reauth(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    """Redirect straight to the OAuth consent screen, bypassing the wizard.
    Useful when a token needs refreshing without changing the connected account."""
    from urllib.parse import quote as _q
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Unknown connector '{connector_type}'.")
    if handler.no_oauth:
        raise HTTPException(status_code=400, detail="This connector does not use OAuth.")

    redirect, *_ = _auth(request, slug)
    if redirect:
        return redirect

    return_to = f"/dashboard/{slug}/connectors/{ctype}?reauth_done=1"
    oauth_start = (
        f"/oauth/{handler.oauth_platform}/connect"
        f"?return_to={_q(return_to, safe='')}"
        f"&client={_q(slug, safe='')}"
    )
    return RedirectResponse(url=oauth_start, status_code=302)


@router.get("/dashboard/{client_slug}/connectors/{connector_type}", response_class=HTMLResponse)
def connector_detail(
    client_slug: str,
    connector_type: str,
    request: Request,
    oauth_done: str | None = None,
    oauth_error: str | None = None,
    connected: str | None = None,
    reauth_done: str | None = None,
    reconfigure: str | None = None,
    flash: str | None = None,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Unknown connector '{connector_type}'.")

    redirect, access_key, use_session, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return redirect

    import client_config
    cfg_obj = client_config.load_client_config(slug)
    label = cfg_obj.label if cfg_obj else slug
    db_config = client_dashboard_config.get_config(slug)
    config = connector_config_store.get_config(slug, ctype)
    runs = connector_config_store.list_sync_runs(config.id) if config else []

    flash_message = flash
    if connected:
        flash_message = f"{handler.display_name} is now connected and syncing."
    elif reauth_done:
        flash_message = f"{handler.display_name} re-authorized successfully."

    return HTMLResponse(connectors_renderer.render_connector_detail(
        client_slug=slug,
        label=label,
        connector_type=ctype,
        handler=handler,
        config=config,
        sync_runs=runs,
        db_config=db_config,
        oauth_done=bool(oauth_done),
        oauth_error=(oauth_error or "").strip()[:300] or None,
        flash_message=flash_message,
        force_wizard=bool(reconfigure),
        **_session_kw(access_key, use_session, session_email, session_is_admin),
    ))


# ──────────────────────────────────────────────────────────────────────────────
# Account list (JSON)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/{client_slug}/connectors/{connector_type}/accounts")
async def connector_accounts(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        return JSONResponse({"ok": False, "error": f"Unknown connector '{connector_type}'."}, status_code=404)

    redirect, *_ = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)

    try:
        accounts = handler.list_accounts(client_slug=slug)
        return JSONResponse({"ok": True, "accounts": accounts})
    except Exception as exc:
        _log.warning("connector accounts error [%s/%s]: %s", slug, ctype, exc)
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)


# ──────────────────────────────────────────────────────────────────────────────
# Configure (step-by-step JSON POST)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/configure")
async def connector_configure(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        return JSONResponse({"ok": False, "error": f"Unknown connector."}, status_code=404)

    redirect, access_key, use_session, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)

    try:
        kwargs: dict = {
            "oauth_platform": handler.oauth_platform,
            "oauth_client_slug": slug,
        }
        if "source_account_id" in body:
            kwargs["source_account_id"] = str(body["source_account_id"]).strip() or None
        if "source_account_name" in body:
            kwargs["source_account_name"] = str(body["source_account_name"]).strip() or None
        if "bq_project_id" in body:
            kwargs["bq_project_id"] = str(body["bq_project_id"]).strip() or None
        if "raw_dataset_id" in body:
            kwargs["raw_dataset_id"] = str(body["raw_dataset_id"]).strip() or handler.default_raw_dataset
        if "mart_dataset_id" in body:
            kwargs["mart_dataset_id"] = str(body["mart_dataset_id"]).strip() or handler.default_mart_dataset
        if "sync_enabled" in body:
            kwargs["sync_enabled"] = bool(body["sync_enabled"])
        if "status" in body:
            kwargs["status"] = str(body["status"])

        connector_config_store.upsert_config(slug, ctype, **kwargs)

        # Keep client_dashboard_config.gcp_project_id synced with whatever a
        # connector's destination step last saved, so a brand-new client needs
        # no separate "register this client's GCP project" admin step — the
        # orchestrator's provisioning gate and every dashboard read resolve
        # gcp_project_id directly (see bigquery_refresh_orchestrator and
        # settings_routes._bq_nixon_routing). Deliberately not a one-time
        # backfill: if an admin fixes a typo'd bq_project_id later by re-saving
        # a connector's destination step, this must propagate too, or the
        # dashboard silently keeps reading the stale/wrong project forever.
        if kwargs.get("bq_project_id"):
            try:
                import client_dashboard_config as _cdc
                existing = _cdc.get_config(slug)
                if not existing or existing.gcp_project_id != kwargs["bq_project_id"]:
                    _cdc.save_config(
                        slug,
                        label=(existing.label if existing else slug),
                        google_customer_id=existing.google_customer_id if existing else None,
                        linkedin_account_id=existing.linkedin_account_id if existing else None,
                        meta_account_id=existing.meta_account_id if existing else None,
                        ga4_client_key=existing.ga4_client_key if existing else None,
                        gcp_project_id=kwargs["bq_project_id"],
                    )
            except Exception:
                _log.warning("gcp_project_id sync failed [%s/%s]", slug, ctype, exc_info=True)

            # Provision + verify BigQuery up front, on the destination step,
            # instead of deferring to the first sync. The GCP *project* must
            # already exist (created out-of-band with the shared service account
            # granted access -- the app can't create projects); this creates the
            # datasets inside it and confirms the service account can write, so
            # a missing/inaccessible project fails fast here with a clear,
            # actionable message rather than a silent sync failure later.
            # Idempotent (create_dataset exists_ok=True), so re-running is cheap.
            try:
                import client_bigquery_setup
                client_bigquery_setup.ensure_client_datasets(project_id=kwargs["bq_project_id"])
            except Exception as exc:
                _log.warning("BQ provisioning failed on configure [%s/%s]: %s", slug, ctype, exc)
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            f"BigQuery setup check failed: {str(exc)[:200]} — make sure the GCP "
                            f"project '{kwargs['bq_project_id']}' exists and "
                            "marketing-data-reader@sagefrog.iam.gserviceaccount.com has BOTH the "
                            "'BigQuery Data Editor' and 'BigQuery Job User' roles on it (Job User "
                            "is what grants permission to run queries), then try again."
                        ),
                    },
                    status_code=400,
                )

        return JSONResponse({"ok": True})
    except Exception as exc:
        _log.warning("connector configure error [%s/%s]: %s", slug, ctype, exc)
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)


# ──────────────────────────────────────────────────────────────────────────────
# Test connection (JSON POST)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/test")
async def connector_test(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        return JSONResponse({"ok": False, "error": "Unknown connector."}, status_code=404)

    redirect, *_ = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)

    try:
        label = handler.test_connection(client_slug=slug)
        config = connector_config_store.get_config(slug, ctype)
        acct_name = (config and config.source_account_name) or label
        return JSONResponse({
            "ok": True,
            "message": f"Connection verified. Account: {acct_name}" if acct_name else "Connection verified.",
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)


# ──────────────────────────────────────────────────────────────────────────────
# Manual sync (JSON POST)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/sync")
async def connector_sync(
    client_slug: str,
    connector_type: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        return JSONResponse({"ok": False, "error": "Unknown connector."}, status_code=404)

    redirect, _ak, _us, session_email, _is_admin = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)

    config = connector_config_store.get_config(slug, ctype)
    if not config:
        return JSONResponse({"ok": False, "error": "Connector not configured."}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    date_range: str = str(body.get("date_range") or "LAST_5_DAYS").strip().upper()

    run_id = connector_config_store.start_sync_run(
        config.id, run_type="manual", triggered_by=session_email or "user"
    )
    connector_config_store.update_sync_timestamps(slug, ctype, started=True)

    def _run():
        try:
            result = handler.run_sync(client_slug=slug, date_range=date_range)
            connector_config_store.finish_sync_run(
                run_id,
                status="completed" if result.ok else "failed",
                rows_loaded=result.rows_loaded,
                error_message=result.error,
            )
            connector_config_store.update_sync_timestamps(
                slug, ctype,
                completed=True,
                success=result.ok,
                error=result.error,
                range_start=result.range_start,
                range_end=result.range_end,
                rows_loaded=result.rows_loaded,
            )
            if result.ok:
                try:
                    import db_cache
                    db_cache.invalidate_prefix(f"{slug}.")
                    if slug.startswith("nixon"):
                        db_cache.invalidate_prefix("nixon.")
                except Exception:
                    _log.warning("cache invalidation failed [%s/%s]", slug, ctype, exc_info=True)
                # Re-warm the core card caches we just invalidated so the next
                # dashboard load is served warm rather than paying a cold
                # BigQuery query per card. Best-effort — never affects the sync.
                try:
                    from dashboard.services.dashboard_warm_service import warm_client_cache
                    warm_client_cache("nixon" if slug.startswith("nixon") else slug)
                except Exception:
                    _log.warning("cache warm failed [%s/%s]", slug, ctype, exc_info=True)
        except Exception as exc:
            err = str(exc)[:500]
            connector_config_store.finish_sync_run(run_id, status="failed", error_message=err)
            connector_config_store.update_sync_timestamps(slug, ctype, completed=True, error=err)

    background_tasks.add_task(_run)
    return JSONResponse({"ok": True, "message": "Sync started in the background.", "run_id": run_id})


# ──────────────────────────────────────────────────────────────────────────────
# Save connector sync options (e.g. HubSpot lifecycle stage + backfill window)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/sync-options")
async def connector_sync_options(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    import json as _json
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    redirect, _ak, _us, _email, _is_admin = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)
    config = connector_config_store.get_config(slug, ctype)
    if not config:
        return JSONResponse({"ok": False, "error": "Connector not configured."}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        body = {}

    options: dict = {}
    if ctype == "hubspot":
        import hubspot_sync_service
        stage = hubspot_sync_service.normalize_stage(str(body.get("lifecycle_stage") or ""))
        try:
            lookback = max(1, min(int(body.get("lookback_days") or 90), 3650))
        except Exception:
            lookback = 90
        # Which objects to pull. Saving an empty selection would silently turn the
        # connector into a no-op, so it's rejected rather than stored.
        objects = hubspot_sync_service.normalize_objects(body.get("sync_objects"))
        if not any(objects.values()):
            return JSONResponse(
                {"ok": False, "error": "Select at least one type of HubSpot data to sync."},
                status_code=400,
            )
        options = {"lifecycle_stage": stage, "lookback_days": lookback, "sync_objects": objects}
    else:
        # Generic: store whatever JSON-able options were posted.
        options = {k: v for k, v in (body or {}).items()}

    connector_config_store.set_sync_options(slug, ctype, _json.dumps(options))
    return JSONResponse({"ok": True, "sync_options": options})


# ──────────────────────────────────────────────────────────────────────────────
# Generate a signed, no-login connect link for one client (e.g. HubSpot portal)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/connect-link")
async def connector_connect_link(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    import oauth_flows
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    redirect, _ak, _us, _email, _is_admin = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)
    if ctype not in oauth_flows.PLATFORMS:
        return JSONResponse({"ok": False, "error": "This connector doesn't support connect links."}, status_code=400)
    try:
        token = oauth_flows.sign_connect_state(slug, ctype)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=500)
    link = f"{oauth_flows.public_base_url()}/connect/{ctype}/{slug}?t={token}"
    return JSONResponse({"ok": True, "link": link, "expires_days": 7})


# ──────────────────────────────────────────────────────────────────────────────
# Cancel a running sync
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/sync/{run_id}/cancel")
async def connector_sync_cancel(
    client_slug: str,
    connector_type: str,
    run_id: int,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    redirect, *_ = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)

    connector_config_store.cancel_sync_run(run_id)
    _log.info("sync run %s cancelled by user [%s/%s]", run_id, slug, connector_type)
    return JSONResponse({"ok": True, "message": "Sync cancelled."})


# ──────────────────────────────────────────────────────────────────────────────
# Disconnect (form POST)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/disconnect")
async def connector_disconnect(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        raise HTTPException(status_code=404, detail="Unknown connector.")

    redirect, _ak, _us, session_email, *_ = _auth(request, slug)
    if redirect:
        return redirect

    config = connector_config_store.get_config(slug, ctype)
    if config:
        # Revoke/remove the client-scoped OAuth token (best-effort)
        try:
            oauth_store.delete_platform(handler.oauth_platform, client_slug=slug)
        except Exception as exc:
            _log.warning("OAuth token deletion failed [%s/%s]: %s", slug, ctype, exc)

        connector_config_store.update_status(
            slug, ctype,
            status="disconnected",
            sync_enabled=False,
            disconnected_at=datetime.now(tz=UTC),
        )
        audit_log.record(
            action="connector.disconnected",
            actor_email=session_email,
            detail={"client_slug": slug, "connector_type": ctype},
            **audit_log.request_context(request),
        )

    dest = f"/dashboard/{slug}/connectors"
    return RedirectResponse(url=dest + "?" + f"disconnected={handler.display_name}", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Delete data (destructive — requires confirmation token in body)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/dashboard/{client_slug}/connectors/{connector_type}/delete-data")
async def connector_delete_data(
    client_slug: str,
    connector_type: str,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    ctype = connector_type.strip().lower()
    handler = get_handler(ctype)
    if not handler:
        return JSONResponse({"ok": False, "error": "Unknown connector."}, status_code=404)

    redirect, _ak, _us, session_email, session_is_admin = _auth(request, slug)
    if redirect:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)
    if not session_is_admin:
        return JSONResponse({"ok": False, "error": "Admin access required."}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)

    if body.get("confirmation") != "DELETE":
        return JSONResponse({"ok": False, "error": "Type DELETE to confirm data deletion."}, status_code=400)

    # Hard-delete the connector config row
    config = connector_config_store.get_config(slug, ctype)
    if config:
        try:
            import db
            with db.connection() as conn:
                conn.execute(
                    "DELETE FROM connector_sync_runs WHERE connector_config_id = %s",
                    (config.id,),
                )
                conn.execute(
                    "DELETE FROM connector_configs WHERE id = %s",
                    (config.id,),
                )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"DB deletion failed: {exc}"}, status_code=500)

    audit_log.record(
        action="connector.data_deleted",
        actor_email=session_email,
        detail={"client_slug": slug, "connector_type": ctype},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "message": f"{handler.display_name} connector data deleted."})
