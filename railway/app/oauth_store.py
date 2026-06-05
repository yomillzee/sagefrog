"""Encrypted agency-wide OAuth token storage in Postgres."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

import web_users

PLATFORMS = frozenset({"google_ads", "linkedin", "meta"})

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS oauth_credentials (
      platform TEXT PRIMARY KEY,
      refresh_token_enc TEXT,
      access_token_enc TEXT,
      token_expires_at TIMESTAMPTZ,
      scopes TEXT,
      metadata_json JSONB,
      connected_by TEXT,
      connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


@dataclass(frozen=True)
class OAuthCredentialPublic:
    platform: str
    connected: bool
    source: str
    connected_by: str | None
    connected_at: str | None
    updated_at: str | None
    scopes: str | None
    metadata: dict[str, Any]


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


def _encryption_key() -> bytes:
    secret = (
        (os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY") or "").strip()
        or (os.getenv("AUTH_SESSION_SECRET") or "").strip()
        or (os.getenv("CRON_SECRET") or "").strip()
        or (os.getenv("API_KEY") or "").strip()
    )
    if not secret:
        raise RuntimeError(
            "Set OAUTH_TOKEN_ENCRYPTION_KEY or AUTH_SESSION_SECRET to encrypt OAuth tokens."
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_encryption_key())


def _encrypt(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def get_refresh_token(platform: str) -> str | None:
    row = _get_row(platform)
    if not row:
        return None
    return _decrypt(row.get("refresh_token_enc"))


def get_access_token(platform: str) -> str | None:
    row = _get_row(platform)
    if not row:
        return None
    return _decrypt(row.get("access_token_enc"))


def _get_row(platform: str) -> dict[str, Any] | None:
    slug = _normalize_platform(platform)
    if not slug or not enabled():
        return None
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        row = conn.execute(
            """
            SELECT refresh_token_enc, access_token_enc, token_expires_at, scopes,
                   metadata_json, connected_by, connected_at, updated_at
            FROM oauth_credentials
            WHERE platform = %s
            """,
            (slug,),
        ).fetchone()
    if not row:
        return None
    meta = row[4]
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    elif meta is None:
        meta = {}
    return {
        "refresh_token_enc": row[0],
        "access_token_enc": row[1],
        "token_expires_at": row[2],
        "scopes": row[3],
        "metadata_json": meta if isinstance(meta, dict) else {},
        "connected_by": row[5],
        "connected_at": row[6],
        "updated_at": row[7],
    }


def _normalize_platform(platform: str) -> str:
    slug = (platform or "").strip().lower()
    if slug not in PLATFORMS:
        raise ValueError(f"Unknown OAuth platform '{platform}'.")
    return slug


def save_tokens(
    platform: str,
    *,
    refresh_token: str | None = None,
    access_token: str | None = None,
    token_expires_at: datetime | None = None,
    scopes: str | None = None,
    metadata: dict[str, Any] | None = None,
    connected_by: str | None = None,
) -> None:
    slug = _normalize_platform(platform)
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to store OAuth tokens.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = _get_row(slug) or {}
    refresh_enc = _encrypt(refresh_token) if refresh_token else existing.get("refresh_token_enc")
    access_enc = _encrypt(access_token) if access_token else existing.get("access_token_enc")
    if not refresh_enc and not access_enc:
        raise ValueError("At least one token is required.")
    meta_json = json.dumps(metadata or existing.get("metadata_json") or {})
    with psycopg.connect(_get_db_url()) as conn:
        conn.execute(
            """
            INSERT INTO oauth_credentials (
              platform, refresh_token_enc, access_token_enc, token_expires_at,
              scopes, metadata_json, connected_by, connected_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (platform)
            DO UPDATE SET
              refresh_token_enc = COALESCE(EXCLUDED.refresh_token_enc, oauth_credentials.refresh_token_enc),
              access_token_enc = COALESCE(EXCLUDED.access_token_enc, oauth_credentials.access_token_enc),
              token_expires_at = COALESCE(EXCLUDED.token_expires_at, oauth_credentials.token_expires_at),
              scopes = COALESCE(EXCLUDED.scopes, oauth_credentials.scopes),
              metadata_json = COALESCE(EXCLUDED.metadata_json, oauth_credentials.metadata_json),
              connected_by = COALESCE(EXCLUDED.connected_by, oauth_credentials.connected_by),
              updated_at = EXCLUDED.updated_at
            """,
            (
                slug,
                refresh_enc,
                access_enc,
                token_expires_at,
                (scopes or "").strip() or None,
                meta_json,
                (connected_by or "").strip() or None,
                now,
                now,
            ),
        )


def delete_platform(platform: str) -> bool:
    slug = _normalize_platform(platform)
    if not enabled():
        return False
    ensure_schema()
    with psycopg.connect(_get_db_url()) as conn:
        cur = conn.execute("DELETE FROM oauth_credentials WHERE platform = %s", (slug,))
        return bool(getattr(cur, "rowcount", 0))


def public_status(platform: str, *, env_has_token: bool) -> OAuthCredentialPublic:
    slug = _normalize_platform(platform)
    row = _get_row(slug)
    db_connected = bool(
        row
        and (
            (row.get("refresh_token_enc") and _decrypt(row.get("refresh_token_enc")))
            or (row.get("access_token_enc") and _decrypt(row.get("access_token_enc")))
        )
    )
    if env_has_token and db_connected:
        source = "env+database"
    elif env_has_token:
        source = "env"
    elif db_connected:
        source = "database"
    else:
        source = "none"
    connected = env_has_token or db_connected
    connected_at = row.get("connected_at") if row else None
    updated_at = row.get("updated_at") if row else None
    return OAuthCredentialPublic(
        platform=slug,
        connected=connected,
        source=source,
        connected_by=str(row.get("connected_by")) if row and row.get("connected_by") else None,
        connected_at=connected_at.isoformat() if connected_at else None,
        updated_at=updated_at.isoformat() if updated_at else None,
        scopes=str(row.get("scopes")) if row and row.get("scopes") else None,
        metadata=dict(row.get("metadata_json") or {}) if row else {},
    )
