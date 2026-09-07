"""Admin routes for creating and maintaining client dashboards.

Lifted out of main.py. These are the /admin/dashboards/* endpoints — create,
rename, set industry/team/mode/logo, delete — plus snapshot deletion, which is
the same lifecycle from the other end.

Registered by admin.register_admin_routes(); see admin/__init__.py.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import audit_log
import dashboard_registry
import web_auth
import web_users
from admin.shared import AVATAR_MAX_CHARS as _AVATAR_MAX_CHARS

router = APIRouter(include_in_schema=False)


@router.post("/admin/dashboards")
def admin_create_dashboard(
    request: Request,
    client_slug: str = Form(...),
    label: str = Form(...),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    ctx = audit_log.request_context(request)
    try:
        created = dashboard_registry.create_client(
            client_slug=client_slug,
            label=label,
            created_by=user.email,
        )
    except ValueError as exc:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                error=str(exc),
                page="clients",
            ),
            status_code=400,
        )
    audit_log.record(
        action="dashboard.created",
        actor_email=user.email,
        detail={"client_slug": created.client_slug, "label": created.label},
        **ctx,
    )
    # NOTE: GSC table provisioning intentionally does NOT happen here. At
    # creation time the client has no BQ registry entry yet, so routing would
    # fall back to the Penn default project and create the tables in the wrong
    # place. Provisioning runs in admin_save_gsc_config(), once the client's
    # BigQuery destination is known.
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+{quote(created.label)}+created",
        status_code=303,
    )

@router.post("/admin/dashboards/{client_slug}/rename")
def admin_rename_dashboard(
    client_slug: str,
    request: Request,
    label: str = Form(...),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Rename a dashboard's display label. The slug is the immutable internal key
    (routes, BigQuery routing, OAuth tokens, config lookups all key on it); only
    the human-facing name changes, so this is safe to expose to any admin."""
    ctx = audit_log.request_context(request)
    try:
        renamed = dashboard_registry.rename_client(
            client_slug=client_slug,
            label=label,
            updated_by=user.email,
        )
    except ValueError as exc:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                error=str(exc),
                page="clients",
            ),
            status_code=400,
        )
    audit_log.record(
        action="dashboard.renamed",
        actor_email=user.email,
        detail={"client_slug": renamed.client_slug, "label": renamed.label},
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+renamed+to+{quote(renamed.label)}",
        status_code=303,
    )

@router.post("/admin/dashboards/{client_slug}/industry")
def admin_set_dashboard_industry(
    client_slug: str,
    request: Request,
    industries: list[str] = Form(default=[]),
    industry: str = Form(""),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Tag an account with its industry buckets (or clear them by ticking none).

    An account can sit in several buckets — a clinical-workflow SaaS vendor is
    both health and software — so the form posts one ``industries`` value per
    ticked box. ``industry`` is still accepted for the old single-select form
    (and for anything scripted against it); the two are merged, not ranked.

    Purely descriptive metadata — it changes nothing about the client's own
    dashboard; it only decides which buckets the account contributes to on
    /admin/benchmarks. Any admin can set it.
    """
    import client_industries

    slug = (client_slug or "").strip().lower()
    ctx = audit_log.request_context(request)
    keys = client_industries.normalize_many([*industries, industry])
    try:
        dashboard_registry.set_industries(slug, keys)
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/clients?err=Could+not+set+industry:+{quote(str(exc)[:120])}",
            status_code=303,
        )
    audit_log.record(
        action="dashboard.industry_set",
        actor_email=user.email,
        detail={"client_slug": slug, "industries": list(keys)},
        **ctx,
    )
    label = client_industries.label_list(keys)
    return RedirectResponse(
        url=f"/admin/clients?msg=Industry+set+to+{quote(label)}", status_code=303
    )

@router.post("/admin/dashboards/{client_slug}/team")
def admin_set_dashboard_team(
    client_slug: str,
    request: Request,
    team_emails: list[str] = Form(default=[]),
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Set the Sagefrog team on an account — who a comment on its pages notifies.

    One ``team_emails`` value per ticked box; ticking none is a real answer
    ("nobody"), which is why an empty post clears the list rather than falling
    back to the access-derived default. Non-agency and unknown addresses are
    dropped by ``client_team.set_team`` rather than stored and never delivered.
    """
    import client_team

    slug = (client_slug or "").strip().lower()
    ctx = audit_log.request_context(request)
    try:
        saved = client_team.set_team(slug, team_emails, updated_by=user.email)
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/clients?err=Could+not+set+team:+{quote(str(exc)[:120])}",
            status_code=303,
        )
    audit_log.record(
        action="dashboard.team_set",
        actor_email=user.email,
        detail={"client_slug": slug, "team": list(saved)},
        **ctx,
    )
    count = len(saved)
    msg = "Team cleared" if not count else f"Team set ({count})"
    return RedirectResponse(url=f"/admin/clients?msg={quote(msg)}", status_code=303)

@router.post("/admin/dashboards/{client_slug}/mode")
def admin_convert_dashboard_mode(
    client_slug: str,
    request: Request,
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    """Convert a legacy dashboard to the connector-driven Nixon template.

    New dashboards get dashboard_mode='bigquery_nixon' at creation; this is the
    one-click equivalent for dashboards created before that default (avoids a
    manual DB update). Preserves all other config fields.
    """
    slug = (client_slug or "").strip().lower()
    ctx = audit_log.request_context(request)
    try:
        import client_dashboard_config as cdc
        existing = cdc.get_config(slug)
        cdc.save_config(
            slug,
            label=(existing.label if existing else slug),
            google_customer_id=existing.google_customer_id if existing else None,
            linkedin_account_id=existing.linkedin_account_id if existing else None,
            meta_account_id=existing.meta_account_id if existing else None,
            ga4_client_key=existing.ga4_client_key if existing else None,
            updated_by=user.email,
            dashboard_mode="bigquery_nixon",
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/clients?msg=Convert+failed:+{quote(str(exc)[:120])}", status_code=303
        )
    audit_log.record(
        action="dashboard.mode_changed",
        actor_email=user.email,
        detail={"client_slug": slug, "dashboard_mode": "bigquery_nixon"},
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+{quote(slug)}+now+uses+the+new+template", status_code=303
    )

@router.post("/admin/dashboards/{client_slug}/logo")
def admin_set_dashboard_logo(
    client_slug: str,
    request: Request,
    logo: str = Form(""),
    admin: web_users.WebUser = Depends(web_auth.require_admin),
) -> JSONResponse:
    """Set (or clear) a dashboard's logo. Expects a resized ``data:image/...`` URI."""
    value = (logo or "").strip()
    if value:
        if not value.startswith("data:image/"):
            return JSONResponse({"ok": False, "error": "Logo must be an image."}, status_code=400)
        if len(value) > _AVATAR_MAX_CHARS:
            return JSONResponse(
                {"ok": False, "error": "Image is too large — try a smaller crop."},
                status_code=413,
            )
    stored = value or None
    if not dashboard_registry.set_logo(client_slug, stored):
        return JSONResponse({"ok": False, "error": "Dashboard not found."}, status_code=404)
    audit_log.record(
        action="dashboard.logo_updated",
        actor_email=admin.email,
        detail={"client_slug": client_slug, "cleared": stored is None},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "logo": stored})

@router.post("/admin/dashboards/{client_slug}/delete")
def admin_delete_dashboard(
    client_slug: str,
    request: Request,
    confirm_label: str = Form(""),
    user: web_users.WebUser = Depends(web_auth.require_super_admin),
):
    ctx = audit_log.request_context(request)
    try:
        deleted = dashboard_registry.delete_client(
            client_slug=client_slug,
            confirm_label=confirm_label,
            deleted_by=user.email,
        )
    except ValueError as exc:
        users = web_users.list_users(include_inactive=False)
        events = audit_log.list_recent(limit=150)
        return HTMLResponse(
            web_auth.render_admin_page(
                user=user,
                users=users,
                audit_events=events,
                error=str(exc),
                page="clients",
            ),
            status_code=400,
        )
    audit_log.record(
        action="dashboard.deleted",
        actor_email=user.email,
        detail=deleted,
        **ctx,
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Dashboard+{quote(deleted['label'])}+deleted",
        status_code=303,
    )

@router.post("/admin/snapshot/{client_slug}/delete")
def admin_delete_snapshot(
    client_slug: str,
    request: Request,
    user: web_users.WebUser = Depends(web_auth.require_admin),
):
    import dashboard_snapshots
    slug = (client_slug or "").strip().lower()
    dashboard_snapshots.delete_snapshot(slug)
    audit_log.record(
        action="dashboard.snapshot.deleted",
        actor_email=user.email,
        detail={"client_slug": slug},
        **audit_log.request_context(request),
    )
    return RedirectResponse(
        url=f"/admin/clients?msg=Snapshot+cleared+for+{quote(slug)}",
        status_code=303,
    )
