"""Dashboard settings page routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import audit_log
import client_dashboard_config
import dashboard_service
import dashboard_theme
import web_auth
import web_users
from dashboard.routes.helpers import dashboard_settings_session_kwargs, validate_client_slug
from dashboard.services import kpi_registry

router = APIRouter(include_in_schema=False)


def _render_bq_nixon_settings(slug: str, db_cfg, *, flash, flash_err, html_kw) -> HTMLResponse:
    from dashboard.renderers.bigquery_settings_renderer import render_bigquery_settings_page

    label = (getattr(db_cfg, "label", None) or slug).strip() or slug
    return HTMLResponse(
        render_bigquery_settings_page(
            client_slug=slug,
            api_client_key=slug,
            label=label,
            flash=flash,
            flash_error=flash_err,
            show_linkedin_backfill=False,
            explorer_filters=(getattr(db_cfg, "explorer_filters", None) or ""),
            monthly_budget=getattr(db_cfg, "monthly_budget_usd", None),
            budget_tracker_enabled=bool(getattr(db_cfg, "explorer_budget_tracker", True)),
            consent_sidebar_enabled=bool(getattr(db_cfg, "consent_sidebar_enabled", False)),
            primary_kpi=getattr(db_cfg, "primary_kpi", None),
            segment_filter_profile=getattr(db_cfg, "segment_filter_profile", None),
            metric_goals=getattr(db_cfg, "metric_goals", None),
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
    "/dashboard/{client_slug}/pacing-active-days",
    summary="Set a client's active ad days for budget pacing (JSON)",
    include_in_schema=False,
)
def dashboard_client_pacing_active_days(
    client_slug: str,
    request: Request,
    active_weekdays: str = Form(""),
):
    """Admin-only override of which weekdays a client runs ads, for budget pacing.

    ``active_weekdays`` is an ISO-weekday CSV (Mon=1..Sun=7); empty clears the
    override so pacing auto-detects the rhythm from spend history."""
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
    try:
        saved = client_dashboard_config.save_pacing_active_weekdays(
            slug, active_weekdays, updated_by=session_email or "dashboard_key",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.pacing_active_days_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "pacing_active_weekdays": saved.pacing_active_weekdays},
        **audit_log.request_context(request),
    )
    return JSONResponse(
        {"ok": True, "pacing_active_weekdays": saved.pacing_active_weekdays}
    )


@router.post(
    "/dashboard/{client_slug}/primary-kpi",
    summary="Set a client's headline KPI for the HQ overview (JSON)",
    include_in_schema=False,
)
def dashboard_client_primary_kpi_save(
    client_slug: str,
    request: Request,
    type: str = Form(""),
    label: str = Form(""),
    goal: str = Form(""),
):
    """Admin-only: persist (or clear) the client's headline KPI spec shown on HQ.

    An empty ``type`` clears the KPI. ``goal`` is optional; a blank or unparseable
    value stores no goal (the KPI then shows its value without a progress bar)."""
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can set the primary KPI."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save the KPI."},
            status_code=503,
        )

    kpi_type = str(type or "").strip()
    if kpi_type and kpi_type not in kpi_registry.KPI_TYPES:
        return JSONResponse({"ok": False, "error": "Unknown KPI type."}, status_code=400)

    goal_raw = str(goal or "").strip()
    goal_val: float | None = None
    if goal_raw:
        try:
            goal_val = float(goal_raw)
        except ValueError:
            return JSONResponse({"ok": False, "error": "Goal must be a number."}, status_code=400)
        if goal_val < 0:
            return JSONResponse({"ok": False, "error": "Goal must be zero or greater."}, status_code=400)

    spec = None if not kpi_type else {
        "type": kpi_type,
        "label": str(label or "").strip() or None,
        "goal": goal_val,
    }
    try:
        saved = client_dashboard_config.save_primary_kpi(
            slug, spec, updated_by=session_email or "dashboard_key",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.primary_kpi_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "primary_kpi": saved.primary_kpi},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "primary_kpi": saved.primary_kpi})


@router.post(
    "/dashboard/{client_slug}/metric-goals",
    summary="Set a client's per-metric targets for the summary cards (JSON)",
    include_in_schema=False,
)
def dashboard_client_metric_goals_save(
    client_slug: str,
    request: Request,
    goals: str = Form(""),
):
    """Admin-only: replace the client's per-metric targets.

    ``goals`` is a JSON object of ``{metric_key: number}``. The submitted map
    replaces the stored one wholesale — a metric the editor left blank is absent
    here and so its target is cleared. Unknown keys and non-positive numbers are
    dropped by ``metric_goals.normalize_goals`` rather than rejected, so one
    stray field cannot block a save; the response echoes what was actually kept
    so the editor can report the real count."""
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can set metric goals."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save goals."},
            status_code=503,
        )

    raw = str(goals or "").strip()
    payload: object = {}
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse(
                {"ok": False, "error": "Goals must be a JSON object."}, status_code=400
            )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"ok": False, "error": "Goals must be a JSON object."}, status_code=400
        )

    try:
        saved = client_dashboard_config.save_metric_goals(
            slug, payload, updated_by=session_email or "dashboard_key",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.metric_goals_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "metric_goals": saved.metric_goals},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "goals": saved.metric_goals})


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
    "/dashboard/{client_slug}/segment-filter-profile",
    summary="Save the client's segment filter profile (JSON)",
    include_in_schema=False,
)
def dashboard_client_segment_filter_profile(
    client_slug: str,
    request: Request,
    profile: str = Form(""),
):
    """Admin-only: set how this client's campaigns/pages are grouped in the
    Campaign Explorer + Website Analytics filters ('business_lines', 'regions',
    or empty for none). Replaces the old slug/label inference."""
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
    try:
        saved = client_dashboard_config.save_segment_filter_profile(
            slug, profile, updated_by=session_email or "dashboard_key",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.segment_filter_profile_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "segment_filter_profile": saved.segment_filter_profile},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "segment_filter_profile": saved.segment_filter_profile})


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


@router.post(
    "/dashboard/{client_slug}/sidebar-tabs",
    summary="Save which client-facing sidebar tabs are hidden for this client (JSON)",
    include_in_schema=False,
)
def dashboard_client_sidebar_tabs(
    client_slug: str,
    request: Request,
    hidden: str = Form(""),
):
    """Admin-only: set which core sidebar section tabs are hidden for this client.

    ``hidden`` is a comma-separated list of tab keys to hide (any subset of
    ``client_dashboard_config.SIDEBAR_TOGGLEABLE_TABS``; unknown keys are
    dropped). Server-side + per client, so the choice sticks for every user of
    the client's portal in every browser — replacing the old per-browser
    localStorage toggle."""
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    session_email = user.email if user else None

    if web_users.enabled() and not session_is_admin:
        return JSONResponse(
            {"ok": False, "error": "Only admins can change sidebar tabs."},
            status_code=403,
        )
    if not client_dashboard_config.enabled():
        return JSONResponse(
            {"ok": False, "error": "DATABASE_URL is required to save this setting."},
            status_code=503,
        )
    hidden_keys = [part.strip() for part in str(hidden or "").split(",") if part.strip()]
    try:
        saved = client_dashboard_config.save_sidebar_hidden_tabs(
            slug, hidden_keys, updated_by=session_email or "dashboard_key",
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="dashboard.sidebar_tabs_saved",
        actor_email=session_email,
        detail={"client_slug": slug, "sidebar_hidden_tabs": list(saved.sidebar_hidden_tabs)},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "hidden": list(saved.sidebar_hidden_tabs)})


# ──────────────────────────────────────────────────────────────────────────────
# Admin surface: tool tabs (Advanced / View As)
# ──────────────────────────────────────────────────────────────────────────────
# The per-client Admin page is a strip of tabs across the top. Connectors,
# Insights, and Consent Health are their own existing pages; these two are
# lightweight tool pages that share the same shell + tab strip.
_ADMIN_TOOL_TABS = ("sidebar", "view-as")


@router.get(
    "/dashboard/{client_slug}/admin",
    include_in_schema=False,
    response_class=HTMLResponse,
)
def admin_tools_home(client_slug: str, request: Request):
    """Admin landing → the first tab (Connectors)."""
    slug = validate_client_slug(client_slug)
    return RedirectResponse(url=f"/dashboard/{slug}/connectors", status_code=307)


@router.get(
    "/dashboard/{client_slug}/admin/{tab}",
    include_in_schema=False,
    response_class=HTMLResponse,
)
def admin_tools_tab(client_slug: str, request: Request, tab: str):
    """One of the Admin tool tabs (admin only)."""
    slug = validate_client_slug(client_slug)
    if tab == "bq":
        # The BQ Connection tab was retired: it only mirrored the destination the
        # Connectors page already owns (project + datasets are set there, and its
        # destination strip carries the same Verify access check).
        return RedirectResponse(url=f"/dashboard/{slug}/connectors", status_code=308)
    if tab not in _ADMIN_TOOL_TABS:
        raise HTTPException(status_code=404, detail=f"Unknown admin tab '{tab}'.")
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    user = auth.user
    session_is_admin = bool(user and user.role == "admin")
    if web_users.enabled() and not session_is_admin:
        # The admin tools are agency-only; bounce non-admins back to the dashboard.
        return RedirectResponse(url=f"/dashboard/{slug}", status_code=303)

    import client_config
    from dashboard.renderers.base_layout import render_admin_tools_page

    cfg = client_config.load_client_config(slug)
    label = cfg.label if cfg else slug
    return HTMLResponse(
        render_admin_tools_page(
            client_slug=slug,
            label=label,
            tab=tab,
            **dashboard_settings_session_kwargs(auth),
        )
    )
