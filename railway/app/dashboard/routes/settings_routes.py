"""Dashboard settings page routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import audit_log
import client_dashboard_config
import dashboard_service
import dashboard_theme
import web_auth
import web_users
from dashboard.routes.helpers import dashboard_settings_session_kwargs, validate_client_slug

router = APIRouter(include_in_schema=False)


def _bq_nixon_routing(db_cfg) -> dict:
    """BigQuery routing context for the Nixon-style settings page: which GCP
    project + marts dataset this client's dashboard reads from."""
    project = (getattr(db_cfg, "gcp_project_id", None) or "").strip() or "—"
    marts = (getattr(db_cfg, "bq_mart_dataset_id", None) or "").strip() or "marketing_marts"
    return {"project": project, "ga4_dataset": "—", "marts_dataset": marts}


def _render_bq_nixon_settings(slug: str, db_cfg, *, flash, flash_err, html_kw) -> HTMLResponse:
    from dashboard.renderers.bigquery_settings_renderer import render_bigquery_settings_page

    label = (getattr(db_cfg, "label", None) or slug).strip() or slug
    return HTMLResponse(
        render_bigquery_settings_page(
            client_slug=slug,
            api_client_key=slug,
            label=label,
            routing=_bq_nixon_routing(db_cfg),
            flash=flash,
            flash_error=flash_err,
            show_linkedin_backfill=False,
            explorer_filters=(getattr(db_cfg, "explorer_filters", None) or ""),
            monthly_budget=getattr(db_cfg, "monthly_budget_usd", None),
            budget_tracker_enabled=bool(getattr(db_cfg, "explorer_budget_tracker", True)),
            consent_sidebar_enabled=bool(getattr(db_cfg, "consent_sidebar_enabled", False)),
            **html_kw,
        )
    )




@router.get(
    "/dashboard/{client_slug}/settings",
    summary="Client dashboard settings (HTML)",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_client_settings(
    client_slug: str,
    request: Request,
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
    db_cfg = client_dashboard_config.get_config(slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    # Every dashboard uses the BigQuery settings page now that the Penn
    # settings form has been removed.
    return _render_bq_nixon_settings(
        slug, db_cfg, flash=flash, flash_err=flash_err,
        html_kw=dashboard_settings_session_kwargs(auth),
    )



@router.post(
    "/dashboard/{client_slug}/budget",
    summary="Set a client's monthly paid-media budget (JSON, from the dashboard)",
    include_in_schema=False,
)
def dashboard_client_budget_save(
    client_slug: str,
    request: Request,
    monthly_budget_usd: str = Form(""),
):
    """Inline monthly-budget save from the Campaign Explorer budget module.

    Mirrors the Settings page's ``save_budget`` action (admin-only, same
    validation + audit) but returns JSON so the module can update in place
    without a full page reload. Saves under the URL slug, which is the same
    key the Settings page writes and the dashboard reads back.
    """
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can set the monthly budget."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save the budget."},
            status_code=503,
        )
    try:
        budget = dashboard_service.parse_monthly_budget_input(monthly_budget_usd)
        saved = client_dashboard_config.save_monthly_budget(
            slug, budget, updated_by=session_email or "dashboard_key",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.budget_saved",
        actor_email=session_email,
        detail={
            "client_slug": slug,
            "monthly_budget_usd": saved.monthly_budget_usd,
            "source": "explorer_module",
        },
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "monthly_budget_usd": saved.monthly_budget_usd})


@router.post(
    "/dashboard/{client_slug}/budget-visibility",
    summary="Toggle whether the budget tracker shows on the Campaign Explorer (JSON)",
    include_in_schema=False,
)
def dashboard_client_budget_visibility(
    client_slug: str,
    request: Request,
    show: str = Form(""),
):
    """Admin-only toggle: does the budget tracker module appear on the client's
    Campaign Explorer? (It always shows on this settings page.)"""
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can change this setting."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save this setting."},
            status_code=503,
        )
    show_on_explorer = str(show).strip().lower() in ("1", "true", "yes", "on")
    try:
        saved = client_dashboard_config.save_explorer_budget_tracker(
            slug, show_on_explorer, updated_by=session_email or "dashboard_key",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.budget_visibility_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "explorer_budget_tracker": saved.explorer_budget_tracker},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "explorer_budget_tracker": saved.explorer_budget_tracker})


@router.post(
    "/dashboard/{client_slug}/consent-visibility",
    summary="Toggle whether Consent Health shows in the client sidebar (JSON)",
    include_in_schema=False,
)
def dashboard_client_consent_visibility(
    client_slug: str,
    request: Request,
    show: str = Form(""),
):
    """Admin-only toggle: does Consent & Tracking Health appear in this client's
    sidebar? Off by default; admins still reach the page from Settings when off."""
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can change this setting."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save this setting."},
            status_code=503,
        )
    show_in_sidebar = str(show).strip().lower() in ("1", "true", "yes", "on")
    try:
        saved = client_dashboard_config.save_consent_sidebar_enabled(
            slug, show_in_sidebar, updated_by=session_email or "dashboard_key",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.consent_visibility_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "consent_sidebar_enabled": saved.consent_sidebar_enabled},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "consent_sidebar_enabled": saved.consent_sidebar_enabled})


@router.post(
    "/dashboard/{client_slug}/sidebar-theme",
    summary="Save the client's sidebar gradient colors (JSON)",
    include_in_schema=False,
)
def dashboard_client_sidebar_theme(
    client_slug: str,
    request: Request,
    sidebar_from: str = Form(""),
    sidebar_to: str = Form(""),
):
    """Admin-only: set the top/bottom colors of the client sidebar gradient.

    Merges into the existing theme (leaving the platform brand colors untouched),
    so it re-themes the sidebar on every page without disturbing other styling.
    """
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can change the sidebar colors."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save the theme."},
            status_code=503,
        )
    from_hex = dashboard_theme.normalize_hex(sidebar_from)
    to_hex = dashboard_theme.normalize_hex(sidebar_to)
    if not from_hex or not to_hex:
        return JSONResponse(
            {"ok": False, "error": "Enter valid hex colors (e.g. #0a2540)."},
            status_code=400,
        )
    stored = client_dashboard_config.get_theme(slug)
    theme = dict(stored) if isinstance(stored, dict) else {}
    theme["sidebar_from"] = from_hex
    theme["sidebar_to"] = to_hex
    try:
        client_dashboard_config.save_theme(
            slug, theme, updated_by=session_email or "dashboard_key",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.sidebar_theme_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "sidebar_from": from_hex, "sidebar_to": to_hex},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "sidebar_from": from_hex, "sidebar_to": to_hex})
