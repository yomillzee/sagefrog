"""Per-client insight documents — metadata in Postgres, bytes in R2 or Postgres."""

from __future__ import annotations

import os
import re
from calendar import month_name
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg

import r2_storage
import web_users

MAX_FILE_BYTES = 25 * 1024 * 1024
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
    CREATE INDEX IF NOT EXISTS idx_client_insight_documents_slug_period
      ON client_insight_documents (client_slug, period_year DESC, period_month DESC, uploaded_at DESC)
    """,
    """
    ALTER TABLE client_insight_documents
      ADD COLUMN IF NOT EXISTS storage_backend TEXT NOT NULL DEFAULT 'postgres'
    """,
    """
    ALTER TABLE client_insight_documents
      ADD COLUMN IF NOT EXISTS object_key TEXT
    """,
    """
    ALTER TABLE client_insight_documents
      ALTER COLUMN file_bytes DROP NOT NULL
    """,
]


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
    storage_backend: str = "postgres"
    object_key: str | None = None


@dataclass(frozen=True)
class DocumentDownload:
    meta: InsightDocumentRow
    kind: Literal["bytes", "redirect"]
    payload: bytes | str


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def uses_object_storage() -> bool:
    return r2_storage.enabled()


def storage_status() -> dict[str, Any]:
    return {
        "database": enabled(),
        "object_storage": r2_storage.status(),
        "preferred_backend": "r2" if uses_object_storage() else "postgres",
    }


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with psycopg.connect(url) as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


def _row_from_db(row: tuple[Any, ...]) -> InsightDocumentRow:
    uploaded = row[8]
    storage_backend = str(row[10]).strip() if len(row) > 10 and row[10] else "postgres"
    object_key = str(row[11]).strip() if len(row) > 11 and row[11] else None
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
        storage_backend=storage_backend or "postgres",
        object_key=object_key or None,
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


def _select_columns(include_storage: bool = True) -> str:
    base = """
            id, client_slug, title, period_year, period_month, original_filename,
            content_type, file_size, uploaded_at, uploaded_by"""
    if include_storage:
        return f"{base}, storage_backend, object_key"
    return base


def list_documents(client_slug: str) -> list[InsightDocumentRow]:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return []
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        rows = conn.execute(
            f"""
            SELECT {_select_columns()}
            FROM client_insight_documents
            WHERE client_slug = %s
            ORDER BY period_year DESC, period_month DESC, uploaded_at DESC
            """,
            (slug,),
        ).fetchall()
    return [_row_from_db(row) for row in rows]


def get_document(client_slug: str, doc_id: int) -> InsightDocumentRow | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            f"""
            SELECT {_select_columns()}
            FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        ).fetchone()
    return _row_from_db(row) if row else None


def resolve_download(client_slug: str, doc_id: int) -> DocumentDownload | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            f"""
            SELECT {_select_columns()}, file_bytes
            FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        ).fetchone()
    if not row:
        return None
    meta = _row_from_db(row[:12])
    file_bytes = row[12]
    if meta.object_key and meta.storage_backend == "r2":
        if not r2_storage.enabled():
            raise RuntimeError("Document is stored in R2 but R2 is not configured.")
        url = r2_storage.presigned_get_url(
            meta.object_key,
            filename=meta.original_filename,
        )
        return DocumentDownload(meta=meta, kind="redirect", payload=url)
    if file_bytes is None:
        raise RuntimeError("Document bytes are missing.")
    return DocumentDownload(meta=meta, kind="bytes", payload=bytes(file_bytes))


def get_document_bytes(client_slug: str, doc_id: int) -> tuple[InsightDocumentRow, bytes] | None:
    """Legacy helper — loads file bytes (fetches from R2 when needed)."""
    resolved = resolve_download(client_slug, doc_id)
    if not resolved:
        return None
    if resolved.kind == "redirect":
        data = r2_storage.get_object(resolved.meta.object_key or "")
        return resolved.meta, data
    return resolved.meta, resolved.payload


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
    use_r2 = r2_storage.enabled()

    ensure_schema()
    now = datetime.now(tz=UTC)
    with psycopg.connect(_get_db_url()) as conn:
        if use_r2:
            row = conn.execute(
                f"""
                INSERT INTO client_insight_documents (
                  client_slug, title, period_year, period_month, original_filename,
                  content_type, file_bytes, file_size, uploaded_at, uploaded_by,
                  storage_backend, object_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, 'r2', NULL)
                RETURNING {_select_columns()}
                """,
                (
                    slug,
                    clean_title,
                    int(period_year),
                    int(period_month),
                    original_filename.strip(),
                    normalized_type,
                    len(file_bytes),
                    now,
                    (uploaded_by or "").strip() or None,
                ),
            ).fetchone()
            if not row:
                raise RuntimeError("Failed to save insight document.")
            meta = _row_from_db(row)
            object_key = r2_storage.document_object_key(
                client_slug=slug,
                doc_id=meta.id,
                filename=original_filename,
            )
            try:
                r2_storage.put_object(
                    key=object_key,
                    body=file_bytes,
                    content_type=normalized_type,
                )
            except Exception:
                conn.execute(
                    "DELETE FROM client_insight_documents WHERE client_slug = %s AND id = %s",
                    (slug, meta.id),
                )
                raise
            updated = conn.execute(
                f"""
                UPDATE client_insight_documents
                SET object_key = %s
                WHERE client_slug = %s AND id = %s
                RETURNING {_select_columns()}
                """,
                (object_key, slug, meta.id),
            ).fetchone()
            if not updated:
                r2_storage.delete_object(object_key)
                raise RuntimeError("Failed to finalize insight document storage.")
            return _row_from_db(updated)

        row = conn.execute(
            f"""
            INSERT INTO client_insight_documents (
              client_slug, title, period_year, period_month, original_filename,
              content_type, file_bytes, file_size, uploaded_at, uploaded_by,
              storage_backend, object_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'postgres', NULL)
            RETURNING {_select_columns()}
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
    object_key: str | None = None
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT object_key, storage_backend
            FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        ).fetchone()
        if row and row[0] and str(row[1] or "") == "r2":
            object_key = str(row[0])
        cur = conn.execute(
            """
            DELETE FROM client_insight_documents
            WHERE client_slug = %s AND id = %s
            """,
            (slug, int(doc_id)),
        )
        deleted = bool(cur.rowcount)
    if deleted and object_key:
        try:
            r2_storage.delete_object(object_key)
        except Exception:
            pass
    return deleted
