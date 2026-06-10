"""Shared helpers for dashboard HTTP routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

import client_config
import web_auth

def penn_html_session_kwargs(auth: web_auth.DashboardAuth) -> dict:
    user = auth.user
    return {
        "access_key": auth.access_key,
        "use_session": auth.use_session,
        "session_email": user.email if auth.use_session and user else None,
        "session_is_admin": bool(user and user.role == "admin") if auth.use_session else False,
    }


def validate_client_slug(client_slug: str) -> str:
    slug = (client_slug or "").strip().lower()
    known = client_config.list_client_slugs()
    if slug not in known:
        raise HTTPException(status_code=404, detail=f"Unknown client dashboard '{client_slug}'.")
    return slug


def dashboard_settings_session_kwargs(auth: web_auth.DashboardAuth) -> dict:
    return penn_html_session_kwargs(auth)


def files_page_flash_message(
    *,
    doc_uploaded: str | None,
    doc_deleted: str | None,
    doc_moved: str | None,
    folder_created: str | None,
    folder_deleted: str | None,
    doc_error: str | None,
) -> str | None:
    if doc_error:
        return str(doc_error).strip()[:300]
    if doc_uploaded:
        return "File uploaded."
    if doc_deleted:
        return "File deleted."
    if doc_moved:
        return "File moved."
    if folder_created:
        return "Folder created."
    if folder_deleted:
        return "Folder deleted."
    return None


def files_api_request(request: Request) -> bool:
    return (
        request.headers.get("X-Files-Api") == "1"
        or "application/json" in (request.headers.get("accept") or "").lower()
    )


def files_api_ok(**payload: object) -> JSONResponse:
    body: dict[str, object] = {"ok": True}
    body.update(payload)
    return JSONResponse(body)


def files_api_error(message: str, *, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(message).strip()[:300]}, status_code=status_code)


def files_page_query_params(
    *,
    key: str | None,
    folder: str | None = None,
    doc_uploaded: str | None = None,
    doc_deleted: str | None = None,
    doc_moved: str | None = None,
    folder_created: str | None = None,
    folder_deleted: str | None = None,
    doc_error: str | None = None,
) -> dict[str, str]:
    params: dict[str, str] = {}
    if key:
        params["key"] = key
    if folder:
        params["folder"] = str(folder).strip()
    if doc_error:
        params["doc_error"] = str(doc_error).strip()[:300]
    elif doc_uploaded:
        params["doc_uploaded"] = "1"
    elif doc_deleted:
        params["doc_deleted"] = "1"
    elif doc_moved:
        params["doc_moved"] = "1"
    elif folder_created:
        params["folder_created"] = "1"
    elif folder_deleted:
        params["folder_deleted"] = "1"
    return params


def parse_folder_id(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        folder_id = int(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid folder id.") from exc
    if folder_id < 1:
        raise HTTPException(status_code=400, detail="Invalid folder id.")
    return folder_id
def dashboard_flash_redirect(
    *,
    client_slug: str,
    use_session: bool,
    access_key: str | None,
    **query: str,
) -> RedirectResponse:
    params = {k: v for k, v in query.items() if v}
    if not use_session:
        params["key"] = access_key or ""
    dest = f"/dashboard/{client_slug}"
    if params:
        q = "&".join(
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in params.items()
        )
        dest = f"{dest}?{q}"
    return RedirectResponse(url=dest, status_code=303)


def files_flash_redirect(
    *,
    client_slug: str,
    use_session: bool,
    access_key: str | None,
    folder_id: int | None = None,
    **query: str,
) -> RedirectResponse:
    params = {k: v for k, v in query.items() if v}
    if folder_id is not None:
        params["folder"] = str(int(folder_id))
    if not use_session:
        params["key"] = access_key or ""
    dest = f"/dashboard/{client_slug}/files"
    if params:
        q = "&".join(
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in params.items()
        )
        dest = f"{dest}?{q}"
    return RedirectResponse(url=dest, status_code=303)
