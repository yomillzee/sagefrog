"""Files browser renderer."""

from __future__ import annotations

from typing import Any

from dashboard.renderers.base_layout import (
    render_client_shell_page,
)
from dashboard.utils.auth import can_edit_penn_insights
from dashboard.utils.formatting import (
    esc as _esc,
    file_type_icon_html as _file_type_icon_html,
    folder_icon_html as _folder_icon_html,
    fmt_file_size as _fmt_file_size,
    fmt_short_date as _fmt_short_date,
)
from dashboard.utils.urls import (
    files_page_url as _files_page_url,
    insight_document_delete_url as _insight_document_delete_url,
    insight_document_download_url as _insight_document_download_url,
    insight_document_move_url as _insight_document_move_url,
    insight_documents_action_url as _insight_documents_action_url,
    insight_folder_action_url as _insight_folder_action_url,
    insight_folder_delete_url as _insight_folder_delete_url,
)

def files_breadcrumb_html(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
    breadcrumb: list[Any],
) -> str:
    root_url = _files_page_url(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
    ) or "#"
    parts = [
        f'<a href="{root_url}" class="files-crumb-link" data-drop-target="root">All files</a>',
    ]
    for folder in breadcrumb:
        url = _files_page_url(
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
            folder_id=folder.id,
        ) or "#"
        parts.append('<span class="files-crumb-sep" aria-hidden="true">/</span>')
        if folder.id == breadcrumb[-1].id:
            parts.append(f'<span class="files-crumb-current">{_esc(folder.name)}</span>')
        else:
            parts.append(f'<a href="{url}" class="files-crumb-link">{_esc(folder.name)}</a>')
    return f'<nav class="files-breadcrumb" aria-label="Folder path">{"".join(parts)}</nav>'


def client_files_browser_html(
    *,
    client_slug: str,
    access_key: str | None,
    use_session: bool,
    can_manage: bool,
    folder_id: int | None = None,
) -> str:
    """Dropbox-style folder browser with upload and folder creation."""
    import client_insight_documents as docs

    if not docs.enabled():
        return (
            '<p class="files-empty muted">DATABASE_URL is required to store shared files.</p>'
        )

    breadcrumb = docs.folder_breadcrumb(client_slug, folder_id)
    if folder_id is not None and not breadcrumb:
        return (
            '<p class="files-empty muted">Folder not found. '
            f'<a class="dash-link" href="{_files_page_url(client_slug=client_slug, access_key=access_key, use_session=use_session) or "#"}">'
            "Go to All files</a></p>"
        )

    folders = docs.list_folders(client_slug, parent_id=folder_id)
    files = docs.list_documents(client_slug, folder_id=folder_id)
    breadcrumb_html = files_breadcrumb_html(
        client_slug=client_slug,
        access_key=access_key,
        use_session=use_session,
        breadcrumb=breadcrumb,
    )

    toolbar_actions = ""
    if can_manage:
        upload_action = _insight_documents_action_url(
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
        )
        folder_action = _insight_folder_action_url(
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
        )
        folder_field = (
            f'<input type="hidden" name="parent_id" value="{int(folder_id)}">'
            if folder_id is not None
            else ""
        )
        upload_folder_field = (
            f'<input type="hidden" name="folder_id" value="{int(folder_id)}">'
            if folder_id is not None
            else ""
        )
        toolbar_actions = f"""
        <div class="files-toolbar-actions">
          <button type="button" class="files-btn files-btn--secondary" id="newFolderBtn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
            New folder
          </button>
          <form method="post" action="{upload_action or "#"}" enctype="multipart/form-data" class="files-upload-form">
            {upload_folder_field}
            <label class="files-btn files-btn--primary files-upload-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16"/></svg>
              Upload
              <input type="file" name="file" class="files-upload-input" accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required>
            </label>
          </form>
        </div>
        <dialog class="files-dialog" id="newFolderDialog">
          <form method="post" action="{folder_action or "#"}" class="files-dialog-form">
            {folder_field}
            <h3 class="files-dialog-title">Create a folder</h3>
            <label for="newFolderName" class="files-dialog-label">Folder name</label>
            <input id="newFolderName" name="name" type="text" maxlength="200" required
              placeholder="e.g. Monthly reports" class="files-dialog-input" autocomplete="off">
            <div class="files-dialog-actions">
              <button type="button" class="files-btn files-btn--secondary" id="newFolderCancel">Cancel</button>
              <button type="submit" class="files-btn files-btn--primary">Create</button>
            </div>
          </form>
        </dialog>"""

    rows: list[str] = []
    for folder in folders:
        folder_url = _files_page_url(
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
            folder_id=folder.id,
        ) or "#"
        delete_cell = ""
        if can_manage:
            delete_url = _insight_folder_delete_url(
                client_slug=client_slug,
                folder_id=folder.id,
                access_key=access_key,
                use_session=use_session,
            )
            delete_cell = f"""
              <form method="post" action="{delete_url}" class="files-row-action-form"
                onsubmit="return confirm('Delete this empty folder?');">
                <button type="submit" class="files-row-action files-row-action--danger" title="Delete folder">Delete</button>
              </form>"""
        rows.append(
            f"""
            <tr class="files-row files-row--folder" data-folder-id="{int(folder.id)}" data-drop-target="folder">
              <td class="files-col-name">
                <a href="{folder_url}" class="files-name-link">
                  {_folder_icon_html()}
                  <span class="files-name-text">{_esc(folder.name)}</span>
                </a>
              </td>
              <td class="files-col-modified">{_fmt_short_date(folder.created_at)}</td>
              <td class="files-col-size muted">—</td>
              <td class="files-col-actions">{delete_cell}</td>
            </tr>"""
        )

    for row in files:
        download_url = _insight_document_download_url(
            client_slug=client_slug,
            doc_id=row.id,
            access_key=access_key,
            use_session=use_session,
        )
        delete_cell = ""
        if can_manage:
            delete_url = _insight_document_delete_url(
                client_slug=client_slug,
                doc_id=row.id,
                access_key=access_key,
                use_session=use_session,
            )
            redirect_folder = (
                f'<input type="hidden" name="folder_id" value="{int(folder_id)}">'
                if folder_id is not None
                else ""
            )
            delete_cell = f"""
              <form method="post" action="{delete_url}" class="files-row-action-form"
                onsubmit="return confirm('Delete this file?');">
                {redirect_folder}
                <button type="submit" class="files-row-action files-row-action--danger" title="Delete file">Delete</button>
              </form>"""
        display_name = row.title or row.original_filename
        preview_url = _insight_document_download_url(
            client_slug=client_slug,
            doc_id=row.id,
            access_key=access_key,
            use_session=use_session,
            inline=True,
        )
        is_pdf = (row.original_filename or "").lower().endswith(".pdf")
        drag_attrs = ""
        if can_manage:
            drag_attrs = (
                f' draggable="true" data-doc-id="{int(row.id)}"'
                f' data-move-url="{_esc(_insight_document_move_url(client_slug=client_slug, doc_id=row.id, access_key=access_key, use_session=use_session))}"'
            )
        rows.append(
            f"""
            <tr class="files-row files-row--file"{drag_attrs}>
              <td class="files-col-name">
                <button type="button" class="files-name-link files-name-btn"
                  data-preview-url="{_esc(preview_url)}"
                  data-download-url="{_esc(download_url)}"
                  data-file-name="{_esc(display_name)}"
                  data-file-type="{'pdf' if is_pdf else 'other'}">
                  {_file_type_icon_html(row.original_filename)}
                  <span class="files-name-text">{_esc(display_name)}</span>
                  <span class="files-name-sub muted">{_esc(docs.file_type_label(row.original_filename))}</span>
                </button>
              </td>
              <td class="files-col-modified">{_fmt_short_date(row.uploaded_at)}</td>
              <td class="files-col-size">{_esc(_fmt_file_size(row.file_size))}</td>
              <td class="files-col-actions">
                <a href="{download_url}" class="files-row-action">Download</a>
                {delete_cell}
              </td>
            </tr>"""
        )

    if not rows:
        empty_msg = "This folder is empty."
        if can_manage:
            empty_msg = "Drag files here to upload, drag file rows into folders to move them, or use Upload."
        table_body = f'<tr><td colspan="4" class="files-empty-cell muted">{empty_msg}</td></tr>'
    else:
        table_body = "".join(rows)

    upload_action = ""
    if can_manage:
        upload_action = _insight_documents_action_url(
            client_slug=client_slug,
            access_key=access_key,
            use_session=use_session,
        ) or ""

    browser_attrs = ""
    if can_manage and upload_action:
        folder_attr = str(int(folder_id)) if folder_id is not None else ""
        browser_attrs = (
            f' data-upload-url="{_esc(upload_action)}"'
            f' data-folder-id="{folder_attr}"'
            ' data-can-upload="1" data-can-move="1"'
        )

    drop_overlay = ""
    if can_manage and upload_action:
        drop_overlay = """
      <div class="files-drop-overlay" id="filesDropOverlay" hidden aria-hidden="true">
        <div class="files-drop-overlay-inner">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
            <path d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16"/>
          </svg>
          <p class="files-drop-title">Drop files to upload</p>
          <p class="files-drop-sub">.docx and .pdf up to 25 MB</p>
        </div>
      </div>"""

    return f"""
    <div class="files-browser"{browser_attrs}>
      <div class="files-toolbar">
        {breadcrumb_html}
        {toolbar_actions}
      </div>
      <div class="files-table-wrap" id="filesDropZone">
        {drop_overlay}
        <table class="files-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Modified</th>
              <th scope="col">Size</th>
              <th scope="col"><span class="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {table_body}
          </tbody>
        </table>
      </div>
    </div>
    <dialog class="files-preview-dialog" id="filePreviewDialog">
      <div class="files-preview-head">
        <span class="files-preview-title" id="filePreviewTitle"></span>
        <div class="files-preview-head-actions">
          <a class="files-btn files-btn--primary" id="filePreviewDownload" href="#" download>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16"/></svg>
            Download
          </a>
          <button type="button" class="files-preview-close" id="filePreviewClose" aria-label="Close preview">&times;</button>
        </div>
      </div>
      <div class="files-preview-body" id="filePreviewBody"></div>
    </dialog>"""


def files_page_css() -> str:
    return """
    .dash-flash { background: var(--ok-bg); border: 1px solid #b8dfc8; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; font-size: 0.9rem; color: var(--ok); }
    .files-browser { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow-sm); overflow: hidden; position: relative; }
    .files-browser.files-drag-active { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15), var(--shadow-sm); }
    .files-table-wrap { overflow-x: auto; position: relative; min-height: 200px; }
    .files-drop-overlay { position: absolute; inset: 0; z-index: 20; display: flex; align-items: center; justify-content: center; background: rgba(255, 255, 255, 0.92); border: 2px dashed var(--accent); border-radius: 0 0 var(--radius) var(--radius); pointer-events: none; }
    .files-drop-overlay[hidden] { display: none !important; }
    .files-drop-overlay-inner { text-align: center; color: var(--navy); padding: 24px; }
    .files-drop-overlay-inner svg { width: 48px; height: 48px; color: var(--accent); margin-bottom: 12px; }
    .files-drop-title { margin: 0 0 6px; font-size: 1.05rem; font-weight: 700; }
    .files-drop-sub { margin: 0; font-size: 0.88rem; color: var(--muted); }
    .files-row--folder.files-drop-target { background: rgba(59, 130, 246, 0.12); box-shadow: inset 0 0 0 2px var(--accent); }
    .files-row--folder.files-drop-target .files-name-text { color: var(--accent); font-weight: 700; }
    .files-row--folder .files-name-link { display: flex; align-items: center; gap: 12px; width: 100%; text-decoration: none; color: inherit; }
    .files-row--folder.files-drop-target .files-name-link::after {
      content: 'Drop here';
      margin-left: auto;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      font-size: 0.72rem;
      font-weight: 700;
      flex-shrink: 0;
    }
    .files-crumb-link.files-drop-target { background: var(--accent); color: #fff !important; border-radius: 6px; padding: 2px 8px; text-decoration: none; }
    .files-row--file[draggable="true"] { cursor: grab; }
    .files-row--file.files-row--dragging { opacity: 0.45; cursor: grabbing; }
    .files-name-link--static { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .files-name-btn { appearance: none; border: 0; background: none; font: inherit; text-align: left; cursor: pointer; padding: 0; width: 100%; }
    .files-name-btn:hover .files-name-text { color: var(--accent); text-decoration: underline; }
    .files-name-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
    .files-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding: 16px 20px; border-bottom: 1px solid var(--border); background: linear-gradient(180deg, #fff 0%, var(--surface) 100%); }
    .files-breadcrumb { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; font-size: 0.92rem; min-width: 0; }
    .files-crumb-link { color: var(--accent); text-decoration: none; font-weight: 600; }
    .files-crumb-link:hover { text-decoration: underline; }
    .files-crumb-sep { color: var(--muted); padding: 0 2px; }
    .files-crumb-current { color: var(--navy); font-weight: 650; }
    .files-toolbar-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
    .files-btn { appearance: none; border: 1px solid var(--border); border-radius: 8px; background: #fff; color: var(--navy); font: inherit; font-size: 0.88rem; font-weight: 650; padding: 9px 14px; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; text-decoration: none; transition: background 0.15s, border-color 0.15s, box-shadow 0.15s; }
    .files-btn svg { width: 16px; height: 16px; flex-shrink: 0; }
    .files-btn:hover { background: #f4f7fb; border-color: #b8c4d4; }
    .files-btn--primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .files-btn--primary:hover { filter: brightness(1.05); background: var(--accent); border-color: var(--accent); }
    .files-btn--secondary { background: #fff; }
    .files-upload-label { margin: 0; }
    .files-upload-form { display: inline-flex; margin: 0; }
    .files-upload-input { display: none; }
    .files-table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
    .files-table thead th { text-align: left; padding: 10px 16px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--border); background: var(--surface); }
    .files-table tbody tr { border-bottom: 1px solid var(--border); transition: background 0.12s; }
    .files-table tbody tr:last-child { border-bottom: none; }
    .files-table tbody tr:hover { background: rgba(10, 37, 64, 0.03); }
    .files-col-name { padding: 12px 16px; min-width: 220px; }
    .files-col-modified, .files-col-size { padding: 12px 16px; white-space: nowrap; color: var(--muted); width: 120px; }
    .files-col-actions { padding: 12px 16px; text-align: right; white-space: nowrap; width: 140px; }
    .files-name-link { display: flex; align-items: center; gap: 12px; color: inherit; text-decoration: none; min-width: 0; }
    .files-name-link:hover .files-name-text { color: var(--accent); text-decoration: underline; }
    .files-name-text { font-weight: 600; color: var(--navy); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .files-name-sub { font-size: 0.78rem; margin-left: 6px; flex-shrink: 0; }
    .files-item-icon { width: 28px; height: 28px; flex-shrink: 0; color: var(--muted); }
    .files-item-icon--folder { color: #f5b942; }
    .files-item-icon--pdf { color: #e25555; }
    .files-item-icon--doc { color: #2b6cb0; }
    .files-row-action { appearance: none; border: 0; background: none; color: var(--accent); font-size: 0.82rem; font-weight: 600; cursor: pointer; padding: 0; text-decoration: none; }
    .files-row-action:hover { text-decoration: underline; }
    .files-row-action--danger { color: var(--err); margin-left: 10px; }
    .files-row-action-form { display: inline; }
    .files-empty-cell { padding: 48px 16px; text-align: center; }
    .files-empty { padding: 24px; text-align: center; }
    .files-dialog { border: 0; border-radius: 14px; padding: 0; box-shadow: 0 20px 50px rgba(10, 37, 64, 0.22); max-width: calc(100vw - 32px); width: 420px; }
    .files-dialog::backdrop { background: rgba(10, 37, 64, 0.45); }
    .files-dialog-form { padding: 22px 22px 18px; }
    .files-dialog-title { margin: 0 0 14px; font-size: 1.05rem; color: var(--navy); }
    .files-dialog-label { display: block; font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 6px; }
    .files-dialog-input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; font: inherit; margin-bottom: 16px; }
    .files-dialog-actions { display: flex; justify-content: flex-end; gap: 10px; }
    .visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
    .files-preview-dialog { border: 0; border-radius: 14px; padding: 0; box-shadow: 0 20px 50px rgba(10, 37, 64, 0.28); width: min(960px, calc(100vw - 32px)); max-width: calc(100vw - 32px); height: min(85vh, calc(100vh - 48px)); max-height: calc(100vh - 48px); overflow: hidden; }
    .files-preview-dialog::backdrop { background: rgba(10, 37, 64, 0.55); }
    .files-preview-dialog[open] { display: flex; flex-direction: column; }
    .files-preview-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--surface); flex-shrink: 0; }
    .files-preview-title { font-weight: 700; color: var(--navy); font-size: 0.95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
    .files-preview-head-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
    .files-preview-close { appearance: none; border: 1px solid var(--border); background: #fff; border-radius: 8px; width: 34px; height: 34px; font-size: 1.4rem; line-height: 1; color: var(--muted); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
    .files-preview-close:hover { background: #f4f7fb; color: var(--navy); }
    .files-preview-body { flex: 1; min-height: 0; background: #f4f6fa; position: relative; }
    .files-preview-body iframe { width: 100%; height: 100%; border: 0; display: block; }
    .files-preview-fallback { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; height: 100%; padding: 40px; text-align: center; color: var(--muted); }
    .files-preview-fallback svg { width: 44px; height: 44px; color: var(--muted); }
    .files-preview-fallback p { margin: 0; font-size: 0.95rem; }
    @media (max-width: 720px) {
      .files-col-modified, .files-col-size { display: none; }
      .files-toolbar { flex-direction: column; align-items: stretch; }
      .files-toolbar-actions { justify-content: flex-end; }
    }
    """


def files_page_js() -> str:
    return """
    (function () {
      const previewDialog = document.getElementById('filePreviewDialog');
      if (previewDialog) {
        const titleEl = document.getElementById('filePreviewTitle');
        const bodyEl = document.getElementById('filePreviewBody');
        const downloadEl = document.getElementById('filePreviewDownload');
        const closeEl = document.getElementById('filePreviewClose');

        function closePreview() {
          previewDialog.close();
        }

        function openPreview(btn) {
          const name = btn.dataset.fileName || 'Document';
          const previewUrl = btn.dataset.previewUrl;
          const downloadUrl = btn.dataset.downloadUrl;
          const fileType = btn.dataset.fileType;
          titleEl.textContent = name;
          if (downloadEl) downloadEl.href = downloadUrl || '#';
          bodyEl.innerHTML = '';
          if (fileType === 'pdf' && previewUrl) {
            const frame = document.createElement('iframe');
            frame.src = previewUrl;
            frame.title = name;
            bodyEl.appendChild(frame);
          } else {
            bodyEl.innerHTML =
              '<div class="files-preview-fallback">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>' +
              '<p>This file type can\\'t be previewed in the browser.</p>' +
              '<p>Use Download to open it.</p>' +
              '</div>';
          }
          previewDialog.showModal();
        }

        document.querySelectorAll('.files-name-btn').forEach((btn) => {
          btn.addEventListener('click', () => openPreview(btn));
        });
        closeEl?.addEventListener('click', closePreview);
        previewDialog.addEventListener('click', (event) => {
          if (event.target === previewDialog) closePreview();
        });
        previewDialog.addEventListener('close', () => { bodyEl.innerHTML = ''; });
      }

      const dialog = document.getElementById('newFolderDialog');
      const openBtn = document.getElementById('newFolderBtn');
      const cancelBtn = document.getElementById('newFolderCancel');
      openBtn?.addEventListener('click', () => {
        dialog?.showModal();
        document.getElementById('newFolderName')?.focus();
      });
      cancelBtn?.addEventListener('click', () => dialog?.close());
      document.querySelectorAll('.files-upload-input').forEach((input) => {
        input.addEventListener('change', () => {
          const form = input.closest('form');
          if (input.files?.length) form?.submit();
        });
      });

      const browser = document.querySelector('.files-browser[data-can-upload]');
      if (!browser) return;

      const uploadUrl = browser.dataset.uploadUrl;
      const defaultFolderId = browser.dataset.folderId || '';
      const overlay = document.getElementById('filesDropOverlay');
      const apiHeaders = { 'Accept': 'application/json', 'X-Files-Api': '1' };
      let dragDepth = 0;
      let activeDropTarget = null;
      let draggingDocId = null;
      let draggingMoveUrl = null;

      function allowedFile(file) {
        const name = (file.name || '').toLowerCase();
        return name.endsWith('.pdf') || name.endsWith('.docx');
      }

      function collectFiles(dataTransfer) {
        return Array.from(dataTransfer?.files || []).filter(allowedFile);
      }

      function isInternalDrag(dataTransfer) {
        if (!dataTransfer) return false;
        return Boolean(draggingDocId) || dataTransfer.types.includes('application/x-files-doc-id');
      }

      function clearDropTarget() {
        activeDropTarget?.classList.remove('files-drop-target');
        activeDropTarget = null;
      }

      function setDropTarget(el) {
        if (activeDropTarget === el) return;
        clearDropTarget();
        activeDropTarget = el;
        activeDropTarget?.classList.add('files-drop-target');
      }

      function showUploadOverlay(title, sub) {
        browser.classList.add('files-drag-active');
        if (!overlay) return;
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        overlay.querySelector('.files-drop-title').textContent = title;
        overlay.querySelector('.files-drop-sub').textContent = sub || '.docx and .pdf up to 25 MB';
      }

      function hideOverlayOnly() {
        browser.classList.remove('files-drag-active');
        if (!overlay) return;
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.querySelector('.files-drop-title').textContent = 'Drop files to upload';
        overlay.querySelector('.files-drop-sub').textContent = '.docx and .pdf up to 25 MB';
      }

      function hideUploadOverlay() {
        dragDepth = 0;
        hideOverlayOnly();
        clearDropTarget();
      }

      function highlightDropTarget(dropTarget) {
        hideOverlayOnly();
        if (dropTarget) {
          setDropTarget(dropTarget);
        } else {
          clearDropTarget();
        }
      }

      async function readJsonResponse(resp) {
        const contentType = resp.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          return resp.json();
        }
        throw new Error('Unexpected server response.');
      }

      async function uploadFiles(files, folderId) {
        if (!files.length) {
          alert('Only .docx and .pdf files can be uploaded.');
          return;
        }
        showUploadOverlay(
          files.length === 1 ? 'Uploading…' : `Uploading ${files.length} files…`,
          'Please wait'
        );
        for (const file of files) {
          const formData = new FormData();
          formData.append('file', file);
          if (folderId) formData.append('folder_id', folderId);
          const resp = await fetch(uploadUrl, {
            method: 'POST',
            body: formData,
            credentials: 'same-origin',
            headers: apiHeaders,
          });
          const data = await readJsonResponse(resp);
          if (!data.ok) {
            alert(data.error || ('Upload failed for ' + file.name + '.'));
            hideUploadOverlay();
            return;
          }
        }
        window.location.reload();
      }

      async function moveDocument(moveUrl, folderId) {
        const formData = new FormData();
        if (folderId) formData.append('folder_id', folderId);
        const resp = await fetch(moveUrl, {
          method: 'POST',
          body: formData,
          credentials: 'same-origin',
          headers: apiHeaders,
        });
        const data = await readJsonResponse(resp);
        if (!data.ok) {
          alert(data.error || 'Move failed.');
          return;
        }
        window.location.reload();
      }

      function dropTargetFromEvent(event) {
        const target = event.target;
        if (!(target instanceof Element)) return null;
        return target.closest('[data-drop-target="folder"], [data-drop-target="root"]');
      }

      document.querySelectorAll('.files-row--file[draggable="true"]').forEach((row) => {
        row.addEventListener('dragstart', (event) => {
          draggingDocId = row.dataset.docId || null;
          draggingMoveUrl = row.dataset.moveUrl || null;
          row.classList.add('files-row--dragging');
          if (event.dataTransfer) {
            event.dataTransfer.setData('application/x-files-doc-id', draggingDocId || '');
            event.dataTransfer.effectAllowed = 'move';
          }
        });
        row.addEventListener('dragend', () => {
          draggingDocId = null;
          draggingMoveUrl = null;
          row.classList.remove('files-row--dragging');
          hideUploadOverlay();
        });
      });

      browser.addEventListener('dragenter', (event) => {
        if (isInternalDrag(event.dataTransfer)) return;
        event.preventDefault();
        dragDepth += 1;
        showUploadOverlay('Drop files to upload');
      });

      browser.addEventListener('dragover', (event) => {
        event.preventDefault();
        const internal = isInternalDrag(event.dataTransfer);
        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = internal ? 'move' : 'copy';
        }

        const dropTarget = dropTargetFromEvent(event);
        if (internal) {
          highlightDropTarget(dropTarget);
          return;
        }

        if (dropTarget) {
          highlightDropTarget(dropTarget);
          return;
        }

        clearDropTarget();
        showUploadOverlay('Drop files to upload');
      });

      browser.addEventListener('dragleave', (event) => {
        if (isInternalDrag(event.dataTransfer)) return;
        event.preventDefault();
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) hideUploadOverlay();
      });

      browser.addEventListener('drop', async (event) => {
        event.preventDefault();
        const dropTarget = dropTargetFromEvent(event);
        const internal = isInternalDrag(event.dataTransfer);
        const docId = draggingDocId || event.dataTransfer?.getData('application/x-files-doc-id');
        const moveUrl = draggingMoveUrl;

        hideUploadOverlay();

        if (internal && docId && moveUrl) {
          if (!dropTarget) return;
          const folderId = dropTarget.dataset.dropTarget === 'root'
            ? ''
            : (dropTarget.dataset.folderId || '');
          await moveDocument(moveUrl, folderId);
          return;
        }

        const folderId = dropTarget?.dataset.dropTarget === 'folder'
          ? (dropTarget.dataset.folderId || '')
          : defaultFolderId;
        const files = collectFiles(event.dataTransfer);
        await uploadFiles(files, folderId);
      });

      window.addEventListener('dragend', hideUploadOverlay);
    })();
    """


def render_files_page(
    *,
    client_slug: str,
    label: str,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
    folder_id: int | None = None,
) -> str:
    slug = (client_slug or "penn").strip().lower()
    can_manage = can_edit_penn_insights(
        session_is_admin=session_is_admin,
        access_key=access_key,
    )
    flash_html = ""
    if flash_message:
        flash_html = f'<div class="dash-flash">{_esc(flash_message)}</div>'
    body = flash_html + client_files_browser_html(
        client_slug=slug,
        access_key=access_key,
        use_session=use_session,
        can_manage=can_manage,
        folder_id=folder_id,
    )
    import client_insight_documents as docs

    return render_client_shell_page(
        client_slug=slug,
        label=label,
        active_nav="files",
        page_title="Files",
        page_subtitle=f"{label} · Shared documents",
        content_html=body + f"<script>{files_page_js()}</script>",
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        show_files=docs.enabled(),
        extra_css=files_page_css(),
    )


def render_insights_upload_page(
    *,
    client_slug: str,
    label: str,
    access_key: str | None = None,
    use_session: bool = False,
    session_email: str | None = None,
    session_is_admin: bool = False,
    flash_message: str | None = None,
    folder_id: int | None = None,
) -> str:
    return render_files_page(
        client_slug=client_slug,
        label=label,
        access_key=access_key,
        use_session=use_session,
        session_email=session_email,
        session_is_admin=session_is_admin,
        flash_message=flash_message,
        folder_id=folder_id,
    )






