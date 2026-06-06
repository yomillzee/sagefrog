"""Per-client insight documents and folders stored in Postgres."""

from __future__ import annotations

import os
import re
from calendar import month_name
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

import web_users

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FOLDER_NAME_LEN = 200
ALLOWED_EXTENSIONS = frozenset({".docx", ".pdf"})
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/pdf",
        "application/octet-stream",
        "application/zip",
    }
)
EXTENSION_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_insight_folders (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      parent_id BIGINT REFERENCES client_insight_folders(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS client_insight_documents (
      id BIGSERIAL PRIMARY KEY,
      client_slug TEXT NOT NULL,
      title TEXT NOT NULL,
      period_year INT NOT NULL,
      period_month INT NOT NULL CHECK (period_month BETWEEN 1 AND 12),
      original_filename TEXT NOT NULL,
      content_type TEXT NOT NULL DEFAULT '',
      file_bytes BYTEA NOT NULL,
      file_size INT NOT NULL,
      uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      uploaded_by TEXT
    )
    """,
    """
    ALTER TABLE client_insight_documents
      ADD COLUMN IF NOT EXISTS folder_id BIGINT REFERENCES client_insight_folders(id) ON DELETE SET NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_insight_folders_slug_parent
      ON client_insight_folders (client_slug, parent_id NULLS FIRST, name ASC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_insight_documents_slug_period
      ON client_insight_documents (client_slug, period_year DESC, period_month DESC, uploaded_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_insight_documents_slug_folder
      ON client_insight_documents (client_slug, folder_id NULLS FIRST, uploaded_at DESC)
    """,
]


@dataclass(frozen=True)
class InsightFolderRow:
    id: int
    client_slug: str
    parent_id: int | None
    name: str
    created_at: str | None
    created_by: str | None


@dataclass(frozen=True)
class InsightDocumentRow:
    id: int
    client_slug: str
    title: str
    period_year: int
    period_month: int
    original_filename: str
    content_type: str
    file_size: int
    uploaded_at: str | None
    uploaded_by: str | None
    folder_id: int | None = None


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with psycopg.connect(url) as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _folder_from_db(row: tuple[Any, ...]) -> InsightFolderRow:
    created = row[4]
    parent = row[2]
    return InsightFolderRow(
        id=int(row[0]),
        client_slug=str(row[1]),
        parent_id=int(parent) if parent is not None else None,
        name=str(row[3] or ""),
        created_at=created.isoformat() if created else None,
        created_by=str(row[5]).strip() if row[5] else None,
    )


def _row_from_db(row: tuple[Any, ...]) -> InsightDocumentRow:
    uploaded = row[8]
    folder_id = row[10] if len(row) > 10 else None
    return InsightDocumentRow(
        id=int(row[0]),
        client_slug=str(row[1]),
        title=str(row[2] or ""),
        period_year=int(row[3]),
        period_month=int(row[4]),
        original_filename=str(row[5] or ""),
        content_type=str(row[6] or ""),
        file_size=int(row[7] or 0),
        uploaded_at=uploaded.isoformat() if uploaded else None,
        uploaded_by=str(row[9]).strip() if row[9] else None,
        folder_id=int(folder_id) if folder_id is not None else None,
    )


def file_type_label(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return "PDF"
    if ext == ".docx":
        return "Word"
    return ext.lstrip(".").upper() or "Document"


def period_label(year: int, month: int) -> str:
    name = month_name[int(month)] if 1 <= int(month) <= 12 else "Unknown"
    return f"{name} {int(year)}"


def parse_period(value: str) -> tuple[int, int]:
    """Parse YYYY-MM from HTML month input."""
    text = (value or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if not match:
        raise ValueError("Period must be a month like 2026-05.")
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        raise ValueError("Period month must be between 01 and 12.")
    return year, month


def current_period() -> tuple[int, int]:
    now = datetime.now(tz=UTC)
    return now.year, now.month


def validate_folder_name(name: str) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Folder name is required.")
    if len(clean) > MAX_FOLDER_NAME_LEN:
        raise ValueError(f"Folder name must be {MAX_FOLDER_NAME_LEN} characters or fewer.")
    if re.search(r"[/\\]", clean):
        raise ValueError("Folder name cannot contain / or \\.")
    return clean


def _normalize_content_type(filename: str, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype in ALLOWED_CONTENT_TYPES and ctype not in ("application/octet-stream", "application/zip"):
        return ctype
    return EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")


def validate_upload(*, filename: str, content_type: str | None, file_bytes: bytes) -> None:
    name = (filename or "").strip()
    if not name:
        raise ValueError("Choose a document to upload.")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Only .docx and .pdf documents are supported.")
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")
    if len(file_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"File is too large (max {MAX_FILE_BYTES // (1024 * 1024)} MB).")
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype and ctype not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Upload must be a .docx or .pdf document.")


def _resolve_folder_id(
    client_slug: str,
    folder_id: int | None,
    *,
    conn: psycopg.Connection,
) -> int | None:
    if folder_id is None:
        return None
    row = conn.execute(
        """
        SELECT id FROM client_insight_folders
        WHERE client_slug = %s AND id = %s
        """,
        (client_slug, int(folder_id)),
    ).fetchone()
    if not row:
        raise ValueError("Folder not found.")
    return int(row[0])


def list_folders(client_slug: str, *, parent_id: int | None = None) -> list[InsightFolderRow]:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return []
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        if parent_id is None:
            rows = conn.execute(
                """
                SELECT id, client_slug, parent_id, name, created_at, created_by
                FROM client_insight_folders
                WHERE client_slug = %s AND parent_id IS NULL
                ORDER BY name ASC
                """,
                (slug,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, client_slug, parent_id, name, created_at, created_by
                FROM client_insight_folders
                WHERE client_slug = %s AND parent_id = %s
                ORDER BY name ASC
                """,
                (slug, int(parent_id)),
            ).fetchall()
    return [_folder_from_db(row) for row in rows]


def get_folder(client_slug: str, folder_id: int) -> InsightFolderRow | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT id, client_slug, parent_id, name, created_at, created_by
            FROM client_insight_folders
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(folder_id)),
        ).fetchone()
    return _folder_from_db(row) if row else None


def folder_breadcrumb(client_slug: str, folder_id: int | None) -> list[InsightFolderRow]:
    """Return ancestors from root to folder (inclusive)."""
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled() or folder_id is None:
        return []
    ensure_schema()
    trail: list[InsightFolderRow] = []
    current_id: int | None = int(folder_id)
    seen: set[int] = set()
    with psycopg.connect(_get_db_url()) as conn:
        while current_id is not None:
            if current_id in seen:
                break
            seen.add(current_id)
            row = conn.execute(
                """
                SELECT id, client_slug, parent_id, name, created_at, created_by
                FROM client_insight_folders
                WHERE client_slug = %s AND id = %s
                """,
                (slug, current_id),
            ).fetchone()
            if not row:
                break
            folder = _folder_from_db(row)
            trail.append(folder)
            current_id = folder.parent_id
    trail.reverse()
    return trail


def folder_is_empty(client_slug: str, folder_id: int) -> bool:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return True
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        child_folder = conn.execute(
            """
            SELECT 1 FROM client_insight_folders
            WHERE client_slug = %s AND parent_id = %s
            LIMIT 1
            """,
            (slug, int(folder_id)),
        ).fetchone()
        if child_folder:
            return False
        child_doc = conn.execute(
            """
            SELECT 1 FROM client_insight_documents
            WHERE client_slug = %s AND folder_id = %s
            LIMIT 1
            """,
            (slug, int(folder_id)),
        ).fetchone()
        return child_doc is None


def create_folder(
    client_slug: str,
    *,
    name: str,
    parent_id: int | None = None,
    created_by: str | None = None,
) -> InsightFolderRow:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to store folders.")

    clean_name = validate_folder_name(name)
    ensure_schema()
    now = datetime.now(tz=UTC)
    with psycopg.connect(_get_db_url()) as conn:
        if parent_id is not None:
            _resolve_folder_id(slug, int(parent_id), conn=conn)
        row = conn.execute(
            """
            INSERT INTO client_insight_folders (client_slug, parent_id, name, created_at, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, client_slug, parent_id, name, created_at, created_by
            """,
            (
                slug,
                int(parent_id) if parent_id is not None else None,
                clean_name,
                now,
                (created_by or "").strip() or None,
            ),
        ).fetchone()
    if not row:
        raise RuntimeError("Failed to create folder.")
    return _folder_from_db(row)


def delete_folder(client_slug: str, folder_id: int) -> bool:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return False
    if not folder_is_empty(slug, int(folder_id)):
        raise ValueError("Delete files and subfolders before removing this folder.")
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        cur = conn.execute(
            """
            DELETE FROM client_insight_folders
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(folder_id)),
        )
    return bool(cur.rowcount)


def list_documents(
    client_slug: str,
    *,
    folder_id: int | None = None,
) -> list[InsightDocumentRow]:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return []
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        if folder_id is None:
            rows = conn.execute(
                """
                SELECT id, client_slug, title, period_year, period_month, original_filename,
                       content_type, file_size, uploaded_at, uploaded_by, folder_id
                FROM client_insight_documents
                WHERE client_slug = %s AND folder_id IS NULL
                ORDER BY uploaded_at DESC
                """,
                (slug,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, client_slug, title, period_year, period_month, original_filename,
                       content_type, file_size, uploaded_at, uploaded_by, folder_id
                FROM client_insight_documents
                WHERE client_slug = %s AND folder_id = %s
                ORDER BY uploaded_at DESC
                """,
                (slug, int(folder_id)),
            ).fetchall()
    return [_row_from_db(row) for row in rows]


def get_document(client_slug: str, doc_id: int) -> InsightDocumentRow | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT id, client_slug, title, period_year, period_month, original_filename,
                   content_type, file_size, uploaded_at, uploaded_by, folder_id
            FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        ).fetchone()
    return _row_from_db(row) if row else None


def get_document_bytes(client_slug: str, doc_id: int) -> tuple[InsightDocumentRow, bytes] | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT id, client_slug, title, period_year, period_month, original_filename,
                   content_type, file_size, uploaded_at, uploaded_by, folder_id, file_bytes
            FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        ).fetchone()
    if not row:
        return None
    meta = _row_from_db(row[:11])
    file_bytes = row[11]
    if file_bytes is None:
        raise RuntimeError("Document file data is missing. Re-upload this document.")
    return meta, bytes(file_bytes)


def save_document(
    client_slug: str,
    *,
    title: str,
    period_year: int,
    period_month: int,
    original_filename: str,
    content_type: str,
    file_bytes: bytes,
    uploaded_by: str | None = None,
    folder_id: int | None = None,
) -> InsightDocumentRow:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to store insight documents.")

    clean_title = (title or "").strip()
    if not clean_title:
        base = os.path.splitext(original_filename)[0].strip()
        clean_title = base or f"{period_label(period_year, period_month)} Paid Digital Reporting"

    validate_upload(
        filename=original_filename,
        content_type=content_type,
        file_bytes=file_bytes,
    )
    normalized_type = _normalize_content_type(original_filename, content_type)

    ensure_schema()
    now = datetime.now(tz=UTC)
    with psycopg.connect(_get_db_url()) as conn:
        resolved_folder_id = _resolve_folder_id(slug, folder_id, conn=conn) if folder_id else None
        row = conn.execute(
            """
            INSERT INTO client_insight_documents (
              client_slug, title, period_year, period_month, original_filename,
              content_type, file_bytes, file_size, uploaded_at, uploaded_by, folder_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, client_slug, title, period_year, period_month, original_filename,
                      content_type, file_size, uploaded_at, uploaded_by, folder_id
            """,
            (
                slug,
                clean_title,
                int(period_year),
                int(period_month),
                original_filename.strip(),
                normalized_type,
                file_bytes,
                len(file_bytes),
                now,
                (uploaded_by or "").strip() or None,
                resolved_folder_id,
            ),
        ).fetchone()
    if not row:
        raise RuntimeError("Failed to save insight document.")
    return _row_from_db(row)


def delete_document(client_slug: str, doc_id: int) -> bool:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return False
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        cur = conn.execute(
            """
            DELETE FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        )
    return bool(cur.rowcount)
