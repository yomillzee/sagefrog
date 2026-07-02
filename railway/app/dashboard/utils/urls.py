"""Dashboard URL builders (session vs legacy key query param)."""

from __future__ import annotations

from urllib.parse import quote


def dashboard_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
    tab: str | None = None,
) -> str:
    base = f"/dashboard/{client_slug}"
    if tab:
        base = f"{base}?tab={quote(tab, safe='')}"
    if use_session:
        return base
    if access_key:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}key={quote(access_key, safe='')}"
    return base


def settings_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/settings"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def connectors_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/connectors"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def lead_tracking_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/lead-tracking"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def gtm_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/gtm"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def files_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
    folder_id: int | None = None,
) -> str | None:
    base = f"/dashboard/{client_slug}/files"
    params: list[str] = []
    if folder_id is not None:
        params.append(f"folder={int(folder_id)}")
    if not use_session and access_key:
        params.append(f"key={quote(access_key, safe='')}")
    if params:
        return f"{base}?{'&'.join(params)}"
    return base


def insights_upload_page_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    """Deprecated — use files_page_url."""
    return files_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    )


def refresh_action_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/refresh"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def insights_action_url(
    *,
    client_slug: str = "penn",
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/insights"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def insight_documents_action_url(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/insight-documents"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def insight_document_download_url(
    *,
    client_slug: str,
    doc_id: int,
    access_key: str | None,
    use_session: bool,
) -> str:
    base = f"/dashboard/{client_slug}/insight-documents/{int(doc_id)}"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def insight_document_delete_url(
    *,
    client_slug: str,
    doc_id: int,
    access_key: str | None,
    use_session: bool,
) -> str:
    base = f"/dashboard/{client_slug}/insight-documents/{int(doc_id)}/delete"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def insight_document_move_url(
    *,
    client_slug: str,
    doc_id: int,
    access_key: str | None,
    use_session: bool,
) -> str:
    base = f"/dashboard/{client_slug}/insight-documents/{int(doc_id)}/move"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def insight_folder_action_url(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
) -> str | None:
    base = f"/dashboard/{client_slug}/insight-folders"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return None


def insight_folder_delete_url(
    *,
    client_slug: str,
    folder_id: int,
    access_key: str | None,
    use_session: bool,
) -> str:
    base = f"/dashboard/{client_slug}/insight-folders/{int(folder_id)}/delete"
    if use_session:
        return base
    if access_key:
        return f"{base}?key={quote(access_key, safe='')}"
    return base


def client_switch_target_url(
    *,
    client_slug: str,
    active_nav: str,
    access_key: str | None,
    use_session: bool,
) -> str:
    slug = (client_slug or "penn").strip().lower()
    nav = (active_nav or "overview").strip().lower()
    if nav == "settings":
        return settings_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{slug}/settings"
    if nav == "connectors":
        return connectors_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{slug}/connectors"
    if nav == "files":
        return files_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{slug}/files"
    if nav == "insights-upload":
        return files_page_url(
            client_slug=slug, access_key=access_key, use_session=use_session
        ) or f"/dashboard/{slug}/files"
    return dashboard_page_url(
        client_slug=slug, access_key=access_key, use_session=use_session
    )
