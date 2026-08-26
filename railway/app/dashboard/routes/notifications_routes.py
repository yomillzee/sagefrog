"""The per-user notification inbox at ``/notifications`` (agency users only).

Not under ``/dashboard`` — a notification belongs to a person, not to a client —
but it ships with the dashboard router because that is where the thing that
raises notifications lives (the FAB's comments, see ``notes_routes``).

Every endpoint is scoped to the signed-in person: the read/clear writes filter on
the recipient in SQL, so a guessed id can never touch someone else's inbox.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import notifications
import web_auth
from dashboard.renderers import notifications_page

router = APIRouter(include_in_schema=False)

_AGENCY_ROLES = ("admin", "standard")


def _agency_user(request: Request):
    """The signed-in agency user, or None (not signed in / a client login)."""
    user = web_auth.get_current_user(request)
    if not user or getattr(user, "role", None) not in _AGENCY_ROLES:
        return None
    return user


@router.get("/notifications", response_class=HTMLResponse, response_model=None)
def notifications_inbox(request: Request):
    user = web_auth.get_current_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/notifications")
    if user.role not in _AGENCY_ROLES:
        # Signed in as a client login: 403 rather than a bounce to /login, which
        # would ping-pong (the login page forwards an authenticated user to
        # `next`). Comments are internal, so there is nothing here for them.
        raise HTTPException(status_code=403, detail="Notifications are for agency users.")
    items = notifications.list_for(user.email, limit=100)
    unread = sum(1 for n in items if n.is_unread)
    return HTMLResponse(
        notifications_page.render_notifications_page(
            email=user.email,
            is_admin=user.role == "admin",
            items=items,
            unread=unread,
        )
    )


@router.get("/notifications/{notification_id:int}/open", response_model=None)
def open_notification(notification_id: int, request: Request):
    """Mark one read, then forward to the page it is about.

    A GET that writes, deliberately: clicking the row *is* reading it, and the
    write is idempotent and scoped to the signed-in recipient, so a prefetch or a
    double click costs nothing.
    """
    user = _agency_user(request)
    if not user:
        return web_auth.redirect_to_login(request, next_path="/notifications")
    note = notifications.get_for(notification_id, email=user.email)
    target = "/notifications"
    if note:
        target = note.page_path or (
            f"/dashboard/{note.client_slug}" if note.client_slug else "/notifications"
        )
        notifications.mark_read(notification_id, email=user.email)
    # Only ever forward within the portal — a stored path can't become an
    # off-site redirect.
    if not target.startswith("/") or target.startswith("//"):
        target = "/notifications"
    return RedirectResponse(url=target, status_code=303)


@router.post("/notifications/read-all", response_model=None)
def read_all_notifications(request: Request):
    user = _agency_user(request)
    if not user:
        raise HTTPException(status_code=403, detail="Notifications are for agency users.")
    notifications.mark_all_read(user.email)
    return RedirectResponse(url="/notifications", status_code=303)
