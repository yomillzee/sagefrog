"""Timeline annotation routes — dated events drawn onto the trend charts.

A small JSON CRUD API under ``/dashboard/{client_slug}/annotations``, shaped like
the presenter-notes API next door.

The read and the write are gated differently on purpose. **Writes are
agency-only**: annotations are the agency's record of what it did, so a
client-role user can never create, edit, or delete one. **Reads are filtered by
audience** rather than blocked: a client sees the annotations someone
deliberately marked ``shared`` and never learns the internal ones exist, because
the filter is applied in SQL (see ``client_annotations.list_annotations``) and
not in the browser.

CSRF on the POST writes is handled by the app-wide middleware, so nothing extra
is needed here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import client_annotations
import web_auth
from dashboard.routes.helpers import validate_client_slug

router = APIRouter(include_in_schema=False)
LOGGER = logging.getLogger(__name__)

_AGENCY_ROLES = ("admin", "standard")


def _audience(auth: web_auth.DashboardAuth) -> str:
    """"agency" for a signed-in agency user, otherwise "client" (fails closed)."""
    user = auth.user
    return "agency" if (user and getattr(user, "role", None) in _AGENCY_ROLES) else "client"


def _require_agency(auth: web_auth.DashboardAuth) -> web_auth.WebUser:
    """Only the agency writes the timeline."""
    user = auth.user
    if not (user and getattr(user, "role", None) in _AGENCY_ROLES):
        raise HTTPException(
            status_code=403, detail="Only agency users can edit the timeline."
        )
    return user


def _author(user: web_auth.WebUser) -> str | None:
    return getattr(user, "email", None)


@router.get(
    "/dashboard/{client_slug}/annotations",
    summary="List timeline annotations visible to the caller",
)
def list_client_annotations(
    client_slug: str,
    request: Request,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    audience = _audience(auth)
    try:
        rows = client_annotations.list_annotations(
            slug, audience=audience, start=start, end=end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        # A chart without its markers is still a chart — degrade to empty rather
        # than failing the dashboard's load.
        LOGGER.exception("Annotation list failed for %s", slug)
        rows = []
    return JSONResponse({
        "ok": True,
        "annotations": [row.to_dict() for row in rows],
        "can_edit": audience == "agency",
        "categories": client_annotations.category_choices(),
        "visibilities": list(client_annotations.VISIBILITIES),
        "chart_scopes": client_annotations.chart_scope_choices(),
    })


@router.post(
    "/dashboard/{client_slug}/annotations",
    summary="Create a timeline annotation",
)
def create_client_annotation(
    client_slug: str,
    request: Request,
    event_date: str = Form(""),
    end_date: str = Form(""),
    title: str = Form(""),
    body: str = Form(""),
    category: str = Form(""),
    visibility: str = Form(""),
    charts: str = Form(""),
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = _require_agency(auth)
    try:
        row = client_annotations.create_annotation(
            slug,
            event_date=event_date,
            end_date=end_date,
            title=title,
            body=body,
            category=category,
            visibility=visibility,
            charts=charts,
            created_by=_author(user),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "annotation": row.to_dict()}, status_code=201)


@router.post(
    "/dashboard/{client_slug}/annotations/{annotation_id:int}",
    summary="Update a timeline annotation",
)
def update_client_annotation(
    client_slug: str,
    annotation_id: int,
    request: Request,
    event_date: str = Form(""),
    end_date: str = Form(""),
    title: str = Form(""),
    body: str = Form(""),
    category: str = Form(""),
    visibility: str = Form(""),
    charts: str = Form(""),
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    user = _require_agency(auth)
    try:
        row = client_annotations.update_annotation(
            slug,
            annotation_id,
            event_date=event_date,
            end_date=end_date,
            title=title,
            body=body,
            category=category,
            visibility=visibility,
            charts=charts,
            updated_by=_author(user),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        # "not found" and "bad date" both land here; the message distinguishes
        # them for the editor, and a missing row is a 404 either way.
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "annotation": row.to_dict()})


@router.post(
    "/dashboard/{client_slug}/annotations/{annotation_id:int}/delete",
    summary="Delete a timeline annotation",
)
def delete_client_annotation(
    client_slug: str,
    annotation_id: int,
    request: Request,
) -> JSONResponse:
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard_api(request, client_slug=slug)
    _require_agency(auth)
    deleted = client_annotations.delete_annotation(slug, annotation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Annotation not found.")
    return JSONResponse({"ok": True})
