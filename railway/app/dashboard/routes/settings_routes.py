"""Dashboard settings page routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import audit_log
import business_line_rules
import client_config
import client_dashboard_config
import dashboard_features
import dashboard_service
import dashboard_settings
import dashboard_theme
import ga4_credentials
import railway_api
import web_auth
import web_users
from dashboard.routes.helpers import dashboard_settings_session_kwargs, validate_client_slug

router = APIRouter(include_in_schema=False)


def _config_updated_at(client_slug: str) -> str | None:
    try:
        row = client_dashboard_config.get_config(client_slug)
    except Exception:
        return None
    return row.updated_at if row else None


@router.get(
    "/dashboard/{client_slug}/settings",
    summary="Client dashboard settings (HTML)",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_client_settings(
    client_slug: str,
    request: Request,
    key: str | None = None,
    saved: str | None = None,
    bl_rules_saved: str | None = None,
    theme_saved: str | None = None,
    sections_saved: str | None = None,
    bq_error: str | None = None,
    cred_saved: str | None = None,
    cred_error: str | None = None,
):
    slug = validate_client_slug(client_slug)
    flash = (
        cred_saved
        if cred_saved
        else "Settings saved."
        if saved
        else "Brand colors saved."
        if theme_saved
        else "Dashboard section visibility saved."
        if sections_saved
        else (
            "Business line rules saved. Run a full refresh to re-classify campaigns."
            if bl_rules_saved
            else None
        )
    )
    flash_err = cred_error or (
        f"Settings saved, but BigQuery setup needs attention: {bq_error}" if bq_error else None
    )
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        cfg = dashboard_settings.load_settings_config(slug)
        return HTMLResponse(
            dashboard_settings.render_settings_html(
                client_slug=slug,
                cfg=cfg,
                flash_message=flash,
                flash_error=flash_err,
                db_config_updated_at=_config_updated_at(slug),
                **dashboard_settings_session_kwargs(auth),
            )
        )
    dashboard_service.verify_dashboard_key(key)
    cfg = dashboard_settings.load_settings_config(slug)
    return HTMLResponse(
        dashboard_settings.render_settings_html(
            client_slug=slug,
            cfg=cfg,
            access_key=key,
            flash_message=flash,
            flash_error=flash_err,
            db_config_updated_at=_config_updated_at(slug),
        )
    )


@router.post(
    "/dashboard/{client_slug}/gcp-credentials",
    summary="Upload a service-account JSON and set this client's Railway credential var",
    include_in_schema=False,
)
async def dashboard_client_gcp_credentials(
    client_slug: str,
    request: Request,
    key: str | None = None,
    env_var: str = Form(""),
    credentials_file: UploadFile = File(...),
):
    slug = validate_client_slug(client_slug)
    access_key = key
    use_session = False
    session_is_admin = False
    session_email = None
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        session_is_admin = bool(auth.user and auth.user.role == "admin")
        session_email = auth.user.email if auth.user else None
    else:
        dashboard_service.verify_dashboard_key(key)

    def _back(param: str) -> RedirectResponse:
        if use_session:
            return RedirectResponse(url=f"/dashboard/{slug}/settings?{param}", status_code=303)
        return RedirectResponse(
            url=f"/dashboard/{slug}/settings?key={quote(access_key or '', safe='')}&{param}",
            status_code=303,
        )

    if web_users.enabled() and not session_is_admin:
        return _back("cred_error=" + quote("Only admins can upload credentials."))

    name = (env_var or "").strip() or ga4_credentials.default_client_credentials_env(slug)
    if not ga4_credentials.is_allowed_credentials_env_var(name):
        return _back("cred_error=" + quote(
            "Variable must be GCP_SERVICE_ACCOUNT_JSON or GCP_CREDS_<CLIENT>_BASE64."
        ))
    if not railway_api.enabled():
        return _back("cred_error=" + quote(
            "Railway API is not configured (RAILWAY_API_TOKEN/PROJECT_ID/ENVIRONMENT_ID/SERVICE_ID)."
        ))
    try:
        raw = (await credentials_file.read()).decode("utf-8")
        encoded, client_email = ga4_credentials.validate_and_encode_service_account(raw)
        railway_api.set_variable(name, encoded)
    except Exception as exc:
        return _back("cred_error=" + quote(f"Upload failed: {str(exc)[:200]}"))

    audit_log.record(
        action="dashboard.gcp_credentials_set",
        actor_email=session_email,
        detail={"client_slug": slug, "env_var": name, "service_account": client_email},
        **audit_log.request_context(request),
    )
    return _back("cred_saved=" + quote(
        f"Set {name} for {client_email}. Railway is redeploying — live in ~1–2 min."
    ))


@router.post(
    "/dashboard/{client_slug}/settings",
    summary="Save client dashboard settings",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_client_settings_post(
    client_slug: str,
    request: Request,
    action: str = Form("save"),
    key: str | None = None,
    label: str = Form(""),
    google_customer_id: str = Form(""),
    linkedin_account_id: str = Form(""),
    meta_account_id: str = Form(""),
    ga4_client_key: str = Form(""),
    monthly_budget_usd: str = Form(""),
    gsc_site_url: str = Form(""),
    semrush_domain: str = Form(""),
    business_line_rules_text: str = Form("", alias="business_line_rules"),
    sidebar_from: str = Form(""),
    sidebar_to: str = Form(""),
    google: str = Form(""),
    google_bg: str = Form(""),
    linkedin: str = Form(""),
    linkedin_bg: str = Form(""),
    meta: str = Form(""),
    meta_bg: str = Form(""),
    organic: str = Form(""),
    organic_bg: str = Form(""),
    business_line: str = Form(""),
    business_line_bg: str = Form(""),
    feature_overview: str = Form(""),
    feature_budget_pacing: str = Form(""),
    feature_performance_trend: str = Form(""),
    feature_campaign_explorer: str = Form(""),
    feature_website_analytics: str = Form(""),
    feature_segment_filters: str = Form(""),
    feature_product_line_filters: str = Form(""),
    feature_organic_channel: str = Form(""),
):
    slug = validate_client_slug(client_slug)
    act = (action or "save").strip().lower()
    auth = None
    access_key = key
    use_session = False
    session_is_admin = False
    session_email = None

    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug, key=key)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        session_is_admin = bool(user and user.role == "admin")
        session_email = user.email if user else None
    else:
        dashboard_service.verify_dashboard_key(key)

    session_kw = (
        dashboard_settings_session_kwargs(auth)
        if web_users.enabled() and auth
        else {"access_key": access_key, "use_session": use_session}
    )

    if act == "save":
        if web_users.enabled() and not session_is_admin:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="Only admins can save client settings.",
                    **session_kw,
                ),
                status_code=403,
            )
        if not client_dashboard_config.enabled():
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="DATABASE_URL is required to save settings.",
                    **session_kw,
                ),
                status_code=503,
            )
        try:
            existing = client_dashboard_config.get_config(slug)
            saved_row = client_dashboard_config.save_config(
                slug,
                label=label,
                google_customer_id=(google_customer_id.replace("-", "").strip() or None),
                linkedin_account_id=linkedin_account_id,
                meta_account_id=meta_account_id,
                ga4_client_key=ga4_client_key,
                updated_by=session_email or "dashboard_key",
                dashboard_mode="bigquery",
                gsc_site_url=gsc_site_url.strip() or None,
                semrush_domain=semrush_domain.strip() or None,
            )
            budget = dashboard_service.parse_monthly_budget_input(monthly_budget_usd)
            client_dashboard_config.save_monthly_budget(
                slug, budget, updated_by=session_email or "dashboard_key",
            )
            bq_setup_error: str | None = None
            try:
                import client_bigquery_setup

                client_bigquery_setup.ensure_client_bq_resources(
                    client_key=ga4_client_key or slug,
                    needs_google=bool(google_customer_id.strip()),
                    needs_linkedin=bool(linkedin_account_id.strip()),
                    needs_meta=bool(meta_account_id.strip()),
                    needs_mart=bool(
                        google_customer_id.strip()
                        or linkedin_account_id.strip()
                        or meta_account_id.strip()
                    ),
                )
            except Exception as bq_exc:
                bq_setup_error = str(bq_exc)[:300]
            cfg = client_config.load_client_config(slug)
            dashboard_service.patch_snapshot_from_config(cfg)
            audit_log.record(
                action="dashboard.config_saved",
                actor_email=session_email,
                detail={"client_slug": slug, **client_dashboard_config.as_dict(saved_row)},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error=str(exc)[:300],
                    **session_kw,
                ),
                status_code=400,
            )
        bq_error_param = f"&bq_error={quote(bq_setup_error, safe='')}" if bq_setup_error else ""
        if use_session:
            return RedirectResponse(
                url=f"/dashboard/{slug}/settings?saved=1{bq_error_param}", status_code=303
            )
        return RedirectResponse(
            url=f"/dashboard/{slug}/settings?key={quote(access_key or '', safe='')}&saved=1{bq_error_param}",
            status_code=303,
        )

    if act == "save_budget":
        if web_users.enabled() and not session_is_admin:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="Only admins can save the monthly budget.",
                    **session_kw,
                ),
                status_code=403,
            )
        if not client_dashboard_config.enabled():
            raise HTTPException(status_code=503, detail="DATABASE_URL is required to save budget.")
        try:
            budget = dashboard_service.parse_monthly_budget_input(monthly_budget_usd)
            saved_b = client_dashboard_config.save_monthly_budget(
                slug, budget, updated_by=session_email or "dashboard_key",
            )
            audit_log.record(
                action="dashboard.budget_saved",
                actor_email=session_email,
                detail={"client_slug": slug, "monthly_budget_usd": saved_b.monthly_budget_usd},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error=str(exc)[:300],
                    **session_kw,
                ),
                status_code=400,
            )
        dash_url = f"/dashboard/{slug}"
        if not use_session and access_key:
            dash_url = f"{dash_url}?key={quote(access_key, safe='')}"
        return RedirectResponse(url=f"{dash_url}?budget_saved=1", status_code=303)

    if act == "save_business_line_rules":
        if web_users.enabled() and not session_is_admin:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="Only admins can save business line rules.",
                    **session_kw,
                ),
                status_code=403,
            )
        if not business_line_rules.enabled():
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="DATABASE_URL is required to save business line rules.",
                    **session_kw,
                ),
                status_code=503,
            )
        try:
            business_line_rules.save_rules(
                slug, business_line_rules_text, updated_by=session_email or "dashboard_key",
            )
            audit_log.record(
                action="dashboard.business_line_rules_saved",
                actor_email=session_email,
                detail={"client_slug": slug},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error=str(exc)[:300],
                    **session_kw,
                ),
                status_code=400,
            )
        if use_session:
            return RedirectResponse(
                url=f"/dashboard/{slug}/settings?bl_rules_saved=1", status_code=303,
            )
        return RedirectResponse(
            url=f"/dashboard/{slug}/settings?key={quote(access_key or '', safe='')}&bl_rules_saved=1",
            status_code=303,
        )

    if act == "save_theme":
        if slug != "penn":
            raise HTTPException(status_code=400, detail="Brand colors are only available for Penn.")
        if web_users.enabled() and not session_is_admin:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="Only admins can save brand colors.",
                    **session_kw,
                ),
                status_code=403,
            )
        if not client_dashboard_config.enabled():
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="DATABASE_URL is required to save brand colors.",
                    **session_kw,
                ),
                status_code=503,
            )
        try:
            theme = dashboard_theme.parse_theme_form(
                sidebar_from=sidebar_from, sidebar_to=sidebar_to,
                google=google, google_bg=google_bg,
                linkedin=linkedin, linkedin_bg=linkedin_bg,
                meta=meta, meta_bg=meta_bg,
                organic=organic, organic_bg=organic_bg,
                business_line=business_line, business_line_bg=business_line_bg,
            )
            saved_theme = client_dashboard_config.save_theme(
                slug, theme, updated_by=session_email or "dashboard_key",
            )
            audit_log.record(
                action="dashboard.theme_saved",
                actor_email=session_email,
                detail={"client_slug": slug, "theme": saved_theme},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error=str(exc)[:300],
                    **session_kw,
                ),
                status_code=400,
            )
        if use_session:
            return RedirectResponse(url=f"/dashboard/{slug}/settings?theme_saved=1", status_code=303)
        return RedirectResponse(
            url=f"/dashboard/{slug}/settings?key={quote(access_key or '', safe='')}&theme_saved=1",
            status_code=303,
        )

    if act == "save_dashboard_sections":
        if web_users.enabled() and not session_is_admin:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error="Only admins can change dashboard sections.",
                    **session_kw,
                ),
                status_code=403,
            )
        if not client_dashboard_config.enabled():
            raise HTTPException(status_code=503, detail="DATABASE_URL is required to save dashboard sections.")
        try:
            dashboard_features.save_features(
                slug,
                dashboard_features.features_from_form(
                    overview=feature_overview,
                    budget_pacing=feature_budget_pacing,
                    performance_trend=feature_performance_trend,
                    campaign_explorer=feature_campaign_explorer,
                    website_analytics=feature_website_analytics,
                    segment_filters=feature_segment_filters,
                    product_line_filters=feature_product_line_filters,
                    organic_channel=feature_organic_channel,
                ),
                updated_by=session_email or "dashboard_key",
            )
            audit_log.record(
                action="dashboard.sections_saved",
                actor_email=session_email,
                detail={"client_slug": slug},
                **audit_log.request_context(request),
            )
        except Exception as exc:
            cfg = dashboard_settings.load_settings_config(slug)
            return HTMLResponse(
                dashboard_settings.render_settings_html(
                    client_slug=slug,
                    cfg=cfg,
                    flash_error=str(exc)[:300],
                    **session_kw,
                ),
                status_code=400,
            )
        if use_session:
            return RedirectResponse(url=f"/dashboard/{slug}/settings?sections_saved=1", status_code=303)
        return RedirectResponse(
            url=f"/dashboard/{slug}/settings?key={quote(access_key or '', safe='')}&sections_saved=1",
            status_code=303,
        )

    if act == "gsc_sync":
        flash_msg = flash_err_msg = None
        try:
            import gsc_sync_service
            db_cfg_for_sync = client_dashboard_config.get_config(slug)
            _gsc_url = (db_cfg_for_sync.gsc_site_url if db_cfg_for_sync else None) or None
            result = gsc_sync_service.sync_for_refresh(site_url=_gsc_url, client_slug=slug)
            status = result.get("status", "")
            if status == "up_to_date":
                flash_msg = "GSC tables are already up to date. No sync needed."
            elif status == "backfill_started":
                days = result.get("days_missing", "")
                flash_msg = (
                    f"GSC backfill started in the background ({days} days missing). "
                    "Data will appear after ~15 minutes â€” then click Refresh on the dashboard."
                )
            elif status == "synced":
                q = result.get("query_rows", 0)
                p = result.get("page_rows", 0)
                d = result.get("days_synced", 0)
                flash_msg = f"GSC sync complete: {d} day(s), {q:,} query rows, {p:,} page rows written."
            elif not result.get("ok"):
                flash_err_msg = f"GSC sync error: {result.get('error', 'unknown error')}"
            else:
                flash_msg = f"GSC sync finished. Status: {status}"
        except Exception as exc:
            flash_err_msg = f"GSC sync failed: {str(exc)[:200]}"

        cfg = dashboard_settings.load_settings_config(slug)
        return HTMLResponse(
            dashboard_settings.render_settings_html(
                client_slug=slug,
                cfg=cfg,
                flash_message=flash_msg,
                flash_error=flash_err_msg,
                db_config_updated_at=_config_updated_at(slug),
                **session_kw,
            )
        )

    raise HTTPException(status_code=400, detail="Unknown action.")
