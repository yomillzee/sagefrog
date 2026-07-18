"""Client-specific presenter notepad routes (agency-only).

A small JSON CRUD API plus a standalone popup page, all under
``/dashboard/{client_slug}/notes``. Notes are client-scoped and internal to the
agency: every endpoint requires a signed-in *agency* user (admin or standard).
A ``client``-role user is 403'd — they never see the floating pad, and this is
what "can only be updated by the Sagefrog user" enforces on the server side.

CSRF on the POST writes is handled by the app-wide middleware (cookie-authed
unsafe requests must carry the token, which the injected fetch wrapper adds
automatically), so nothing extra is needed here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import client_notes
import web_auth
from dashboard.renderers import notes_widget
from dashboard.routes.helpers import validate_client_slug

router = APIRouter(include_in_schema=False)
LOGGER = logging.getLogger(__name__)

_AGENCY_ROLES = ("admin", "standard")


def _require_agency(auth: web_auth.DashboardAuth) -> web_auth.WebUser:
    """Presenter notes are internal — only agency users may read or write them."""
    user = auth.user
    if not (user and getattr(user, "role", None) in _AGENCY_ROLES):
        raise HTTPException(status_code=403, detail="Notes are available to agency users only.")
    return user


def _author(user: web_auth.WebUser) -> str | None:
    return getattr(user, "email", None)


@router.get("/dashboard/{client_slug}/notes", summary="List client presenter notepads")
def list_client_notes(client_slug: str, request: Request) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    _require_agency(auth)
    pads = client_notes.list_notepads(slug)
    return JSONResponse({"ok": True, "notepads": [p.summary() for p in pads]})


@router.get(
    "/dashboard/{client_slug}/notes/window",
    summary="Standalone presenter-notes popup window",
    response_class=HTMLResponse,
    response_model=None,
)
def client_notes_window(client_slug: str, request: Request):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth
    _require_agency(auth)
    import client_config

    try:
        label = client_config.client_label(slug)
    except Exception:
        label = slug.replace("-", " ").title()
    return HTMLResponse(notes_widget.window_page_html(client_slug=slug, label=label))


@router.get(
    "/dashboard/{client_slug}/notes/{notepad_id:int}",
    summary="Read one client presenter notepad",
)
def get_client_note(client_slug: str, notepad_id: int, request: Request) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    _require_agency(auth)
    pad = client_notes.get_notepad(slug, notepad_id)
    if not pad:
        raise HTTPException(status_code=404, detail="Note not found.")
    return JSONResponse({"ok": True, "notepad": pad.to_dict()})


@router.post("/dashboard/{client_slug}/notes", summary="Create a client presenter notepad")
def create_client_note(
    client_slug: str,
    request: Request,
    title: str = Form(""),
    body: str = Form(""),
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = _require_agency(auth)
    try:
        pad = client_notes.create_notepad(
            slug, title=title, body=body, created_by=_author(user)
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "notepad": pad.to_dict()}, status_code=201)


@router.post(
    "/dashboard/{client_slug}/notes/{notepad_id:int}",
    summary="Update a client presenter notepad",
)
def update_client_note(
    client_slug: str,
    notepad_id: int,
    request: Request,
    title: str = Form(""),
    body: str = Form(""),
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = _require_agency(auth)
    try:
        pad = client_notes.update_notepad(
            slug, notepad_id, title=title, body=body, updated_by=_author(user)
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "notepad": pad.to_dict()})


@router.post(
    "/dashboard/{client_slug}/notes/{notepad_id:int}/delete",
    summary="Delete a client presenter notepad",
)
def delete_client_note(
    client_slug: str, notepad_id: int, request: Request
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    _require_agency(auth)
    deleted = client_notes.delete_notepad(slug, notepad_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found.")
    return JSONResponse({"ok": True, "deleted": True})
