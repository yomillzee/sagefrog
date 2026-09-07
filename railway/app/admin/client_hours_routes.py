"""Admin routes for client hours — the Harvest time-tracking view.

Lifted out of main.py, which had grown to 3,700 lines with every non-dashboard
route in it. These are the /admin/client-hours/* endpoints: the page itself, its
JSON feed, and the forms that set goals, tags, splits, owners, preferences and
share links.

Registered by admin.register_admin_routes(); see admin/__init__.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import audit_log
import oauth_flows
import web_auth
import web_users

router = APIRouter(include_in_schema=False)


@router.get("/admin/client-hours", include_in_schema=False, response_class=HTMLResponse)
def admin_client_hours(request: Request):
    """Admin-only 'Client Hours': a Harvest burn-up chart (hours vs monthly goal)
    for every client this month. Data loads from …/data."""
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/admin/client-hours")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    from dashboard.renderers.client_hours_renderer import render_client_hours_page

    return HTMLResponse(render_client_hours_page(user_email=user.email))

@router.get("/admin/client-hours/data")
def admin_client_hours_data(
    refresh: int = 0,
    user: web_users.WebUser = Depends(web_auth.require_admin),
) -> dict:
    """JSON feed for the Client Hours page: current-month cumulative hours per
    client (cached; live from the Harvest API on a cache miss) plus each client's
    monthly goal. ``?refresh=1`` forces a fresh Harvest pull, bypassing the cache."""
    import harvest_service

    return harvest_service.build_client_hours_overview(use_cache=not bool(refresh))

@router.post("/admin/client-hours/goal")
def admin_client_hours_goal(
    request: Request,
    harvest_client_id: str = Form(...),
    goal: str = Form(""),
    hard_ceiling: str = Form(""),
    client_name: str = Form(""),
    harvest_project_id: str = Form(""),
    project_name: str = Form(""),
):
    """Set one card's monthly hours goal. ``goal`` accepts a single number
    ("80"), a range ("80-100"), or an open-ended floor ("80+"); blank clears it.
    ``hard_ceiling`` (truthy) marks goal_max as a hard cap the team stops at.

    Passing ``harvest_project_id`` writes the goal of a project tracked on its own
    card instead of the client's — the client's own goal is left untouched."""
    user = web_auth.require_admin(request)
    import harvest_service

    hard = (hard_ceiling or "").strip().lower() in ("1", "true", "on", "yes")
    try:
        goal_min, goal_max = harvest_service.parse_goal_text(goal)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    # A hard ceiling only means something when there's a ceiling to stop at.
    hard = hard and goal_max is not None
    pid = (harvest_project_id or "").strip()
    try:
        if pid:
            harvest_service.set_project_goal(
                harvest_project_id=pid,
                goal_min=goal_min,
                goal_max=goal_max,
                hard_ceiling=hard,
                project_name=project_name,
                client_name=client_name,
                updated_by=user.email,
            )
        else:
            harvest_service.set_goal(
                harvest_client_id=harvest_client_id,
                goal_min=goal_min,
                goal_max=goal_max,
                hard_ceiling=hard,
                client_name=client_name,
                updated_by=user.email,
            )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.goal_set",
        actor_email=user.email,
        detail={"harvest_client_id": harvest_client_id, "harvest_project_id": pid or None,
                "goal_min": goal_min, "goal_max": goal_max, "hard_ceiling": hard},
        **audit_log.request_context(request),
    )
    return JSONResponse({
        "ok": True,
        "goal_min": goal_min,
        "goal_max": goal_max,
        "goal_hard": hard,
        "goal_label": harvest_service.format_goal(goal_min, goal_max),
    })

@router.post("/admin/client-hours/project-tag")
def admin_client_hours_project_tag(
    request: Request,
    harvest_project_id: str = Form(...),
    tag: str = Form(""),
    project_name: str = Form(""),
    client_name: str = Form(""),
):
    """Tag one Harvest project as 'retainer' or 'project' (blank clears the tag,
    so it counts only under the 'All' scope)."""
    user = web_auth.require_admin(request)
    import harvest_service

    try:
        harvest_service.set_project_tag(
            harvest_project_id=harvest_project_id,
            tag=tag,
            project_name=project_name,
            client_name=client_name,
            updated_by=user.email,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.project_tagged",
        actor_email=user.email,
        detail={"harvest_project_id": harvest_project_id, "tag": (tag or "").strip().lower() or None},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "tag": (tag or "").strip().lower() or None})

@router.post("/admin/client-hours/project-split")
def admin_client_hours_project_split(
    request: Request,
    harvest_project_id: str = Form(...),
    separate: str = Form(""),
    project_name: str = Form(""),
    client_name: str = Form(""),
):
    """Track one Harvest project on its own card (``separate`` truthy), or fold it
    back into its client's aggregate card. Use this when a client runs two distinct
    lines of work — say a main retainer and a separate HR retainer — that shouldn't
    be paced as one combined total. Folding a project back drops its own goal."""
    user = web_auth.require_admin(request)
    import harvest_service

    want = (separate or "").strip().lower() in ("1", "true", "on", "yes")
    try:
        stored = harvest_service.set_project_separate(
            harvest_project_id=harvest_project_id,
            separate=want,
            project_name=project_name,
            client_name=client_name,
            updated_by=user.email,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.project_split",
        actor_email=user.email,
        detail={"harvest_project_id": harvest_project_id, "separate": stored},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "separate": stored})

@router.post("/admin/client-hours/owner")
def admin_client_hours_owner(
    request: Request,
    harvest_client_id: str = Form(...),
    owner: str = Form(""),
    client_name: str = Form(""),
):
    """Set one client's account owner (a team member's name), or clear it when
    ``owner`` is blank. The owner is a label/filter on the Client Hours page."""
    user = web_auth.require_admin(request)
    import harvest_service

    try:
        stored = harvest_service.set_client_owner(
            harvest_client_id=harvest_client_id,
            owner=owner,
            client_name=client_name,
            updated_by=user.email,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="harvest.owner_set",
        actor_email=user.email,
        detail={"harvest_client_id": harvest_client_id, "owner": stored},
        **audit_log.request_context(request),
    )
    return JSONResponse({"ok": True, "owner": stored})

@router.post("/admin/client-hours/prefs")
def admin_client_hours_prefs(
    request: Request,
    show_billing: str | None = Form(None),
):
    """Save one signed-in user's Client Hours view preferences — which optional
    sections they keep open. Only the fields present in the request are changed,
    and the preference is scoped to this user, not the page."""
    user = web_auth.require_admin(request)
    import harvest_service

    truthy = ("1", "true", "on", "yes")
    updates: dict[str, bool] = {}
    if show_billing is not None:
        updates["show_billing"] = (show_billing or "").strip().lower() in truthy
    try:
        prefs = harvest_service.set_user_prefs(user_email=user.email, updates=updates)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    return JSONResponse({"ok": True, "prefs": prefs})

@router.get("/admin/client-hours/shares")
def admin_client_hours_shares(request: Request) -> JSONResponse:
    """List the active read-only share links for the Client Hours page."""
    web_auth.require_admin(request)
    import client_hours_share

    return JSONResponse({"ok": True, "links": client_hours_share.list_share_links()})

@router.post("/admin/client-hours/share")
def admin_client_hours_share_create(
    request: Request,
    label: str = Form(""),
) -> JSONResponse:
    """Mint a new read-only share link. Returns the token + its full URL."""
    user = web_auth.require_admin(request)
    import client_hours_share

    try:
        link = client_hours_share.create_share_link(created_by=user.email, label=label)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    audit_log.record(
        action="client_hours.share_created",
        actor_email=user.email,
        detail={"token": link["token"][:8] + "…", "label": link.get("label")},
        **audit_log.request_context(request),
    )
    link["url"] = f"{oauth_flows.public_base_url()}/share/client-hours/{link['token']}"
    return JSONResponse({"ok": True, "link": link})

@router.post("/admin/client-hours/share/revoke")
def admin_client_hours_share_revoke(
    request: Request,
    token: str = Form(...),
) -> JSONResponse:
    """Revoke a read-only share link so it can no longer be viewed."""
    user = web_auth.require_admin(request)
    import client_hours_share

    try:
        revoked = client_hours_share.revoke_share_link(token=token, revoked_by=user.email)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)
    if revoked:
        audit_log.record(
            action="client_hours.share_revoked",
            actor_email=user.email,
            detail={"token": (token or "")[:8] + "…"},
            **audit_log.request_context(request),
        )
    return JSONResponse({"ok": True, "revoked": revoked})
