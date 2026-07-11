"""Files browser and insight document routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import audit_log
import client_config
import client_insight_documents
import dashboard_service
import web_auth
import web_users
from dashboard.routes.helpers import (
    files_api_error,
    files_api_ok,
    files_api_request,
    files_flash_redirect,
    files_page_flash_message,
    files_page_query_params,
    parse_folder_id,
    penn_html_session_kwargs,
    validate_client_slug,
)

router = APIRouter(include_in_schema=False)

@router.get(
    "/dashboard/{client_slug}/files",
    summary="Client file sharing page",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_client_files_page(
    client_slug: str,
    request: Request,
    folder: str | None = None,
    doc_uploaded: str | None = None,
    doc_deleted: str | None = None,
    doc_moved: str | None = None,
    folder_created: str | None = None,
    folder_deleted: str | None = None,
    doc_error: str | None = None,
):
    slug = validate_client_slug(client_slug)
    folder_id = parse_folder_id(folder)
    flash = files_page_flash_message(
        doc_uploaded=doc_uploaded,
        doc_deleted=doc_deleted,
        doc_moved=doc_moved,
        folder_created=folder_created,
        folder_deleted=folder_deleted,
        doc_error=doc_error,
    )

    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug)
        if isinstance(auth, RedirectResponse):
            return auth
        try:
            label = client_config.client_label(slug)
        except ValueError:
            label = slug.replace("-", " ").title()
        return HTMLResponse(
            dashboard_service.render_files_page(
                client_slug=slug,
                label=label,
                flash_message=flash,
                folder_id=folder_id,
                **penn_html_session_kwargs(auth),
            )
        )

    raise HTTPException(status_code=503, detail="Dashboard access requires login.")
@router.get(
    "/dashboard/{client_slug}/insights-upload",
    summary="Legacy redirect to Files page",
    include_in_schema=False,
)
def dashboard_client_insights_upload_page(
    client_slug: str,
    folder: str | None = None,
    doc_uploaded: str | None = None,
    doc_deleted: str | None = None,
    doc_moved: str | None = None,
    folder_created: str | None = None,
    folder_deleted: str | None = None,
    doc_error: str | None = None,
):
    slug = validate_client_slug(client_slug)
    params = files_page_query_params(
        folder=folder,
        doc_uploaded=doc_uploaded,
        doc_deleted=doc_deleted,
        folder_created=folder_created,
        folder_deleted=folder_deleted,
        doc_error=doc_error,
    )
    dest = f"/dashboard/{slug}/files"
    if params:
        q = "&".join(
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in params.items()
        )
        dest = f"{dest}?{q}"
    return RedirectResponse(url=dest, status_code=301)
@router.post(
    "/dashboard/{client_slug}/insight-documents",
    summary="Upload client insight document",
    include_in_schema=False,
)
async def dashboard_client_insight_document_upload(
    client_slug: str,
    request: Request,
    title: str = Form(""),
    period: str = Form(""),
    folder_id: str = Form(""),
    file: UploadFile = File(...),
):
    wants_json = files_api_request(request)
    slug = validate_client_slug(client_slug)
    auth = None
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug)
        if isinstance(auth, RedirectResponse):
            if wants_json:
                return files_api_error("Sign in required.", status_code=401)
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        if not dashboard_service.can_edit_penn_insights(
            session_is_admin=bool(user and user.role == "admin"),
            access_key=access_key,
        ):
            if wants_json:
                return files_api_error("Only admins can upload files.", status_code=403)
            raise HTTPException(status_code=403, detail="Only admins can upload insight documents.")
        uploaded_by = user.email if user else None
    else:
        raise HTTPException(status_code=503, detail="Dashboard access requires login.")
        uploaded_by = "dashboard_key"

    redirect_folder_id: int | None = None
    folder_raw = (folder_id or "").strip()
    if folder_raw:
        try:
            redirect_folder_id = int(folder_raw)
        except ValueError:
            redirect_folder_id = None

    if not client_insight_documents.enabled():
        if wants_json:
            return files_api_error("DATABASE_URL is required to store insight documents.")
        return files_flash_redirect(
            client_slug=slug,
            use_session=use_session,
            access_key=access_key,
            folder_id=redirect_folder_id,
            doc_error="DATABASE_URL is required to store insight documents.",
        )

    try:
        if (period or "").strip():
            period_year, period_month = client_insight_documents.parse_period(period)
        else:
            period_year, period_month = client_insight_documents.current_period()
        raw = await file.read()
        saved = client_insight_documents.save_document(
            slug,
            title=title,
            period_year=period_year,
            period_month=period_month,
            original_filename=file.filename or "report.docx",
            content_type=file.content_type or "",
            file_bytes=raw,
            uploaded_by=uploaded_by,
            folder_id=redirect_folder_id,
        )
        audit_log.record(
            action="insight_document.uploaded",
            actor_email=uploaded_by,
            detail={
                "client_slug": slug,
                "document_id": saved.id,
                "title": saved.title,
                "folder_id": saved.folder_id,
                "period": client_insight_documents.period_label(period_year, period_month),
            },
            **audit_log.request_context(request),
        )
    except Exception as exc:
        if wants_json:
            return files_api_error(str(exc))
        return files_flash_redirect(
            client_slug=slug,
            use_session=use_session,
            access_key=access_key,
            folder_id=redirect_folder_id,
            doc_error=str(exc)[:300],
        )

    if wants_json:
        return files_api_ok(
            document_id=saved.id,
            folder_id=saved.folder_id,
            message="File uploaded.",
        )

    return files_flash_redirect(
        client_slug=slug,
        use_session=use_session,
        access_key=access_key,
        folder_id=redirect_folder_id,
        doc_uploaded="1",
    )
@router.get(
    "/dashboard/{client_slug}/insight-documents/{doc_id}",
    summary="Download client insight document",
    include_in_schema=False,
)
def dashboard_client_insight_document_download(
    client_slug: str,
    doc_id: int,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    auth = web_auth.authenticate_dashboard(request, client_slug=slug)
    if isinstance(auth, RedirectResponse):
        return auth

    payload = client_insight_documents.get_document_bytes(slug, doc_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Document not found.")
    meta, file_bytes = payload
    filename = meta.original_filename or meta.title or "document"
    safe_name = "".join(c for c in filename if c.isalnum() or c in " ._-").strip() or "document"
    return Response(
        content=file_bytes,
        media_type=meta.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
@router.post(
    "/dashboard/{client_slug}/insight-documents/{doc_id}/delete",
    summary="Delete client insight document",
    include_in_schema=False,
)
def dashboard_client_insight_document_delete(
    client_slug: str,
    doc_id: int,
    request: Request,
    folder_id: str = Form(""),
):
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        if not dashboard_service.can_edit_penn_insights(
            session_is_admin=bool(user and user.role == "admin"),
            access_key=access_key,
        ):
            raise HTTPException(status_code=403, detail="Only admins can delete insight documents.")
        actor = user.email if user else None
    else:
        raise HTTPException(status_code=503, detail="Dashboard access requires login.")
        actor = "dashboard_key"

    redirect_folder_id: int | None = None
    folder_raw = (folder_id or "").strip()
    if folder_raw:
        try:
            redirect_folder_id = int(folder_raw)
        except ValueError:
            redirect_folder_id = None
    if redirect_folder_id is None:
        existing = client_insight_documents.get_document(slug, doc_id)
        redirect_folder_id = existing.folder_id if existing else None

    deleted = client_insight_documents.delete_document(slug, doc_id)
    if deleted:
        audit_log.record(
            action="insight_document.deleted",
            actor_email=actor,
            detail={"client_slug": slug, "document_id": int(doc_id)},
            **audit_log.request_context(request),
        )
    return files_flash_redirect(
        client_slug=slug,
        use_session=use_session,
        access_key=access_key,
        folder_id=redirect_folder_id,
        doc_deleted="1" if deleted else None,
        doc_error=None if deleted else "Document not found.",
    )
@router.post(
    "/dashboard/{client_slug}/insight-documents/{doc_id}/move",
    summary="Move client insight document to a folder",
    include_in_schema=False,
)
def dashboard_client_insight_document_move(
    client_slug: str,
    doc_id: int,
    request: Request,
    folder_id: str = Form(""),
):
    wants_json = files_api_request(request)
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug)
        if isinstance(auth, RedirectResponse):
            if wants_json:
                return files_api_error("Sign in required.", status_code=401)
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        if not dashboard_service.can_edit_penn_insights(
            session_is_admin=bool(user and user.role == "admin"),
            access_key=access_key,
        ):
            if wants_json:
                return files_api_error("Only admins can move files.", status_code=403)
            raise HTTPException(status_code=403, detail="Only admins can move insight documents.")
        actor = user.email if user else None
    else:
        raise HTTPException(status_code=503, detail="Dashboard access requires login.")
        actor = "dashboard_key"

    target_folder_id: int | None = None
    folder_raw = (folder_id or "").strip()
    if folder_raw:
        try:
            target_folder_id = int(folder_raw)
        except ValueError:
            if wants_json:
                return files_api_error("Invalid folder id.")
            return files_flash_redirect(
                client_slug=slug,
                use_session=use_session,
                access_key=access_key,
                doc_error="Invalid folder id.",
            )

    existing = client_insight_documents.get_document(slug, doc_id)
    redirect_folder_id = existing.folder_id if existing else None

    try:
        moved = client_insight_documents.move_document(
            slug,
            doc_id,
            folder_id=target_folder_id,
        )
        audit_log.record(
            action="insight_document.moved",
            actor_email=actor,
            detail={
                "client_slug": slug,
                "document_id": int(doc_id),
                "folder_id": moved.folder_id,
            },
            **audit_log.request_context(request),
        )
    except Exception as exc:
        if wants_json:
            return files_api_error(str(exc))
        return files_flash_redirect(
            client_slug=slug,
            use_session=use_session,
            access_key=access_key,
            folder_id=redirect_folder_id,
            doc_error=str(exc)[:300],
        )

    if wants_json:
        return files_api_ok(
            document_id=moved.id,
            folder_id=moved.folder_id,
            message="File moved.",
        )

    return files_flash_redirect(
        client_slug=slug,
        use_session=use_session,
        access_key=access_key,
        folder_id=redirect_folder_id,
        doc_moved="1",
    )
@router.post(
    "/dashboard/{client_slug}/insight-folders",
    summary="Create client insight folder",
    include_in_schema=False,
)
def dashboard_client_insight_folder_create(
    client_slug: str,
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        if not dashboard_service.can_edit_penn_insights(
            session_is_admin=bool(user and user.role == "admin"),
            access_key=access_key,
        ):
            raise HTTPException(status_code=403, detail="Only admins can create folders.")
        actor = user.email if user else None
    else:
        raise HTTPException(status_code=503, detail="Dashboard access requires login.")
        actor = "dashboard_key"

    parent_folder_id: int | None = None
    parent_raw = (parent_id or "").strip()
    if parent_raw:
        try:
            parent_folder_id = int(parent_raw)
        except ValueError:
            parent_folder_id = None

    if not client_insight_documents.enabled():
        return files_flash_redirect(
            client_slug=slug,
            use_session=use_session,
            access_key=access_key,
            folder_id=parent_folder_id,
            doc_error="DATABASE_URL is required to store folders.",
        )

    try:
        saved = client_insight_documents.create_folder(
            slug,
            name=name,
            parent_id=parent_folder_id,
            created_by=actor,
        )
        audit_log.record(
            action="insight_folder.created",
            actor_email=actor,
            detail={
                "client_slug": slug,
                "folder_id": saved.id,
                "name": saved.name,
                "parent_id": saved.parent_id,
            },
            **audit_log.request_context(request),
        )
    except Exception as exc:
        return files_flash_redirect(
            client_slug=slug,
            use_session=use_session,
            access_key=access_key,
            folder_id=parent_folder_id,
            doc_error=str(exc)[:300],
        )

    return files_flash_redirect(
        client_slug=slug,
        use_session=use_session,
        access_key=access_key,
        folder_id=saved.id,
        folder_created="1",
    )
@router.post(
    "/dashboard/{client_slug}/insight-folders/{folder_id}/delete",
    summary="Delete client insight folder",
    include_in_schema=False,
)
def dashboard_client_insight_folder_delete(
    client_slug: str,
    folder_id: int,
    request: Request,
):
    slug = validate_client_slug(client_slug)
    if web_users.enabled():
        auth = web_auth.authenticate_dashboard(request, client_slug=slug)
        if isinstance(auth, RedirectResponse):
            return auth
        access_key = auth.access_key
        use_session = auth.use_session
        user = auth.user
        if not dashboard_service.can_edit_penn_insights(
            session_is_admin=bool(user and user.role == "admin"),
            access_key=access_key,
        ):
            raise HTTPException(status_code=403, detail="Only admins can delete folders.")
        actor = user.email if user else None
    else:
        raise HTTPException(status_code=503, detail="Dashboard access requires login.")
        actor = "dashboard_key"

    existing = client_insight_documents.get_folder(slug, folder_id)
    redirect_folder_id = existing.parent_id if existing else None

    try:
        deleted = client_insight_documents.delete_folder(slug, folder_id)
    except ValueError as exc:
        return files_flash_redirect(
            client_slug=slug,
            use_session=use_session,
            access_key=access_key,
            folder_id=redirect_folder_id,
            doc_error=str(exc)[:300],
        )

    if deleted:
        audit_log.record(
            action="insight_folder.deleted",
            actor_email=actor,
            detail={"client_slug": slug, "folder_id": int(folder_id)},
            **audit_log.request_context(request),
        )
    return files_flash_redirect(
        client_slug=slug,
        use_session=use_session,
        access_key=access_key,
        folder_id=redirect_folder_id,
        folder_deleted="1" if deleted else None,
        doc_error=None if deleted else "Folder not found.",
    )
