"""Encrypted agency-wide OAuth token storage in Postgres."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
import db

import web_users
from security import is_production

_log = logging.getLogger(__name__)

PLATFORMS = frozenset({"google_ads", "linkedin", "linkedin_organic", "meta", "gsc", "google_analytics", "google_tag_manager", "hubspot", "harvest", "microsoft_ads"})

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
    # Migrate platform-only PK to (client_slug, platform) composite PK so tokens
    # can be scoped to individual clients. Existing rows get client_slug = '' which
    # means "global / agency-wide" and remains the fallback for callers that don't
    # pass a client_slug.
    """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'oauth_credentials' AND column_name = 'client_slug'
      ) THEN
        ALTER TABLE oauth_credentials ADD COLUMN client_slug TEXT NOT NULL DEFAULT '';
        ALTER TABLE oauth_credentials DROP CONSTRAINT oauth_credentials_pkey;
        ALTER TABLE oauth_credentials ADD PRIMARY KEY (client_slug, platform);
      END IF;
    END $$
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
    # True when a token row exists but its ciphertext can't be decrypted with
    # any configured key (encryption key changed). Distinct from "not connected".
    decrypt_error: bool = False


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def ensure_schema() -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
    return True


# Sentinel returned by _decrypt_ex when ciphertext is present but no configured
# key can decrypt it (as opposed to None, which means "no ciphertext stored").
# This is the difference between "never connected" and "connected, but the
# OAUTH_TOKEN_ENCRYPTION_KEY changed" — the failure mode that silently breaks
# every OAuth connector at once (a service-account connector like GSC is
# unaffected, which is the tell). Surfacing it lets callers show an accurate,
# actionable message instead of a misleading "reconnect" prompt.
_UNDECRYPTABLE = object()


def _primary_secret() -> str:
    """The secret used to ENCRYPT new tokens (and the first decrypt candidate)."""
    dedicated = (os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY") or "").strip()
    if dedicated:
        return dedicated
    if is_production():
        # Fail closed: in production we will NOT silently derive the token key
        # from AUTH_SESSION_SECRET / CRON_SECRET / API_KEY. That chain meant
        # rotating any of those secrets made every stored OAuth token
        # undecryptable, and tied token security to unrelated secrets. To migrate
        # without re-auth, set OAUTH_TOKEN_ENCRYPTION_KEY to the value previously
        # used (historically AUTH_SESSION_SECRET) — or rely on the legacy-key
        # read fallback below, which decrypts old tokens and re-encrypts them
        # with the current key on first use.
        raise RuntimeError(
            "OAUTH_TOKEN_ENCRYPTION_KEY is not set. Set a dedicated OAuth token "
            "encryption key in the environment (in production this is required)."
        )
    # Local dev convenience only: fall back to an existing secret so OAuth
    # flows work without extra setup.
    secret = (
        (os.getenv("AUTH_SESSION_SECRET") or "").strip()
        or (os.getenv("CRON_SECRET") or "").strip()
        or (os.getenv("API_KEY") or "").strip()
    )
    if not secret:
        raise RuntimeError(
            "Set OAUTH_TOKEN_ENCRYPTION_KEY (or AUTH_SESSION_SECRET in dev) to encrypt OAuth tokens."
        )
    return secret


# Env vars whose values a pre-migration deployment may have derived the OAuth
# token key from. Rationale for each:
#   - APP_ENCRYPTION_KEY: an early dedicated key that predated
#     OAUTH_TOKEN_ENCRYPTION_KEY; renaming it left the old value orphaned.
#   - AUTH_SESSION_SECRET / CRON_SECRET / API_KEY: the documented fallback chain.
#     Tokens connected before AUTH_SESSION_SECRET existed were encrypted under the
#     shared secret (which resolved to CRON_SECRET / API_KEY at the time).
#   - DASHBOARD_SECRET: seeded from the shared CRON_SECRET value during the
#     de-overload migration (it took over the legacy ?key= dashboard link). When
#     CRON_SECRET was later rotated independently, DASHBOARD_SECRET is the var
#     that still carries the ORIGINAL shared value the tokens were encrypted under.
# Trying a key that didn't encrypt a given token is harmless: Fernet is
# authenticated, so a wrong key can't false-decrypt — it fails cleanly and the
# next candidate runs.
_LEGACY_SECRET_ENV_VARS = (
    "APP_ENCRYPTION_KEY",
    "AUTH_SESSION_SECRET",
    "CRON_SECRET",
    "DASHBOARD_SECRET",
    "API_KEY",
)


def _legacy_secrets() -> list[str]:
    """Historical secrets an existing token may have been encrypted under.

    When an operator sets a fresh OAUTH_TOKEN_ENCRYPTION_KEY without carrying over
    the old value, every previously stored token becomes undecryptable with the
    primary key alone. These are tried as additional decrypt candidates so the
    migration self-heals (each token is re-encrypted under the current key on
    first read) instead of forcing every client to reconnect. Only secrets still
    present in the environment appear here, so this grants no new access — it just
    reads tokens this deployment could already have read a moment ago.

    OAUTH_TOKEN_ENCRYPTION_KEY_FALLBACKS (comma-separated raw secrets) lets an
    operator supply prior key values directly, so a future key rotation needs no
    code change — set it to the outgoing key, deploy, then clear it once tokens
    have migrated.
    """
    out: list[str] = []

    def _add(val: str) -> None:
        val = (val or "").strip()
        if val and val not in out:
            out.append(val)

    for raw in (os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY_FALLBACKS") or "").split(","):
        _add(raw)
    for name in _LEGACY_SECRET_ENV_VARS:
        _add(os.getenv(name) or "")
    return out


def _fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _encryption_key() -> bytes:
    """Fernet key used to encrypt new tokens (kept for backward compatibility)."""
    return _fernet_key(_primary_secret())


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_encryption_key())


def _decrypt_fernets() -> list:
    """Ordered decrypt candidates: the primary key first, then legacy keys."""
    from cryptography.fernet import Fernet

    primary = _primary_secret()
    secrets = [primary] + [s for s in _legacy_secrets() if s != primary]
    return [Fernet(_fernet_key(s)) for s in secrets]


def _encrypt(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def _decrypt_ex(value: str | None) -> tuple[Any, bool]:
    """Decrypt, returning ``(result, used_legacy_key)``.

    ``result`` is the plaintext str, ``None`` when there is no ciphertext to
    decrypt, or the ``_UNDECRYPTABLE`` sentinel when ciphertext is present but no
    configured key can decrypt it. ``used_legacy_key`` is True only when a legacy
    key (not the current primary) produced the plaintext, so the caller can
    re-encrypt it under the current key.
    """
    if not value:
        return None, False
    token = value.encode("ascii")
    try:
        fernets = _decrypt_fernets()
    except Exception:
        # Primary key unavailable (e.g. fail-closed in prod). Can't decrypt.
        _log.error("OAuth token decrypt skipped: no usable encryption key configured.")
        return _UNDECRYPTABLE, False
    for idx, fernet in enumerate(fernets):
        try:
            plain = fernet.decrypt(token).decode("utf-8")
        except Exception:
            continue
        return plain, idx > 0
    _log.error(
        "OAuth token present but undecryptable with all configured keys — "
        "OAUTH_TOKEN_ENCRYPTION_KEY likely changed. Restore the previous key or reconnect."
    )
    return _UNDECRYPTABLE, False


def _decrypt(value: str | None) -> str | None:
    """Backward-compatible decrypt: plaintext, or None on absent/undecryptable."""
    plain, _ = _decrypt_ex(value)
    return plain if isinstance(plain, str) else None


def _reencrypt_token(platform: str, client_slug: str, column: str, plaintext: str) -> None:
    """Best-effort: re-store a legacy-key token under the current primary key.

    Called after a successful legacy-key decrypt so the migration completes on
    first read and later reads take the fast path. Never raises — a write-back
    failure must not break the token read that triggered it.
    """
    if column not in ("refresh_token_enc", "access_token_enc"):
        return
    try:
        enc = _encrypt(plaintext)
        if not enc:
            return
        with db.connection() as conn:
            conn.execute(
                f"UPDATE oauth_credentials SET {column} = %s, updated_at = %s "
                "WHERE platform = %s AND client_slug = %s",
                (enc, datetime.now(tz=UTC), platform, (client_slug or "").strip()),
            )
        _log.info(
            "Re-encrypted %s.%s for client '%s' under the current OAuth key.",
            platform, column, client_slug or "global",
        )
    except Exception:
        _log.warning(
            "OAuth token re-encryption failed [%s/%s/%s]",
            platform, client_slug or "global", column, exc_info=True,
        )


def _read_token(platform: str, column: str, client_slug: str) -> str | None:
    """Read+decrypt a token column, checking the client row then the global row.

    Auto-migrates any token decrypted with a legacy key to the current key.
    """
    slugs = [client_slug, ""] if (client_slug or "").strip() else [""]
    for cs in slugs:
        row = _get_row(platform, client_slug=cs)
        if not row:
            continue
        plain, used_legacy = _decrypt_ex(row.get(column))
        if isinstance(plain, str):
            if used_legacy:
                _reencrypt_token(_normalize_platform(platform), cs, column, plain)
            return plain
    return None


def get_refresh_token(platform: str, client_slug: str = "") -> str | None:
    """Return refresh token. Falls back to the global (client_slug='') token if a client-specific one isn't found."""
    return _read_token(platform, "refresh_token_enc", client_slug)


def get_access_token(platform: str, client_slug: str = "") -> str | None:
    return _read_token(platform, "access_token_enc", client_slug)


def token_health(platform: str, client_slug: str = "") -> str:
    """Classify a connector's stored token: 'ok', 'undecryptable', or 'missing'.

    Lets callers distinguish a genuinely-absent token ("reconnect") from one
    that exists but can't be decrypted because the encryption key changed
    ("restore the key or reconnect") — the app-wide failure that otherwise looks
    like every connector spontaneously disconnecting.
    """
    slug = _normalize_platform(platform)
    if not enabled():
        return "missing"
    saw_undecryptable = False
    slugs = [client_slug, ""] if (client_slug or "").strip() else [""]
    for cs in slugs:
        row = _get_row(slug, client_slug=cs)
        if not row:
            continue
        for column in ("refresh_token_enc", "access_token_enc"):
            plain, _ = _decrypt_ex(row.get(column))
            if isinstance(plain, str):
                return "ok"
            if plain is _UNDECRYPTABLE:
                saw_undecryptable = True
    return "undecryptable" if saw_undecryptable else "missing"


def token_error(platform: str, *, client_slug: str, missing: str) -> str:
    """Pick an accurate 'no usable token' message for a connector sync failure.

    Returns the connector's own `missing` copy when the token is simply absent,
    but a key-mismatch-specific message when the token exists yet won't decrypt.
    """
    try:
        health = token_health(platform, client_slug)
    except Exception:
        return missing
    if health == "undecryptable":
        return (
            f"{platform} token is stored but could not be decrypted — the OAuth "
            "encryption key (OAUTH_TOKEN_ENCRYPTION_KEY) changed. Restore the previous "
            "key in the environment, or reconnect via the connector wizard."
        )
    return missing


def _get_row(platform: str, client_slug: str = "") -> dict[str, Any] | None:
    slug = _normalize_platform(platform)
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT refresh_token_enc, access_token_enc, token_expires_at, scopes,
                   metadata_json, connected_by, connected_at, updated_at
            FROM oauth_credentials
            WHERE platform = %s AND client_slug = %s
            """,
            (slug, (client_slug or "").strip()),
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
    client_slug: str = "",
) -> None:
    slug = _normalize_platform(platform)
    cs = (client_slug or "").strip()
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to store OAuth tokens.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = _get_row(slug, client_slug=cs) or {}
    refresh_enc = _encrypt(refresh_token) if refresh_token else existing.get("refresh_token_enc")
    access_enc = _encrypt(access_token) if access_token else existing.get("access_token_enc")
    if not refresh_enc and not access_enc:
        raise ValueError("At least one token is required.")
    meta_json = json.dumps(metadata or existing.get("metadata_json") or {})
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_credentials (
              client_slug, platform, refresh_token_enc, access_token_enc, token_expires_at,
              scopes, metadata_json, connected_by, connected_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (client_slug, platform)
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
                cs,
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


def delete_platform(platform: str, client_slug: str = "") -> bool:
    slug = _normalize_platform(platform)
    cs = (client_slug or "").strip()
    if not enabled():
        return False
    ensure_schema()
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM oauth_credentials WHERE platform = %s AND client_slug = %s",
            (slug, cs),
        )
        return bool(getattr(cur, "rowcount", 0))


def public_status(platform: str, client_slug: str = "") -> OAuthCredentialPublic:
    slug = _normalize_platform(platform)
    row = _get_row(slug, client_slug=client_slug)
    return _public_status_from_row(slug, row)


def _public_status_from_row(slug: str, row: dict[str, Any] | None) -> OAuthCredentialPublic:
    db_connected = False
    decrypt_error = False
    if row:
        for column in ("refresh_token_enc", "access_token_enc"):
            plain, _ = _decrypt_ex(row.get(column))
            if isinstance(plain, str):
                db_connected = True
                break
            if plain is _UNDECRYPTABLE:
                decrypt_error = True
    source = "database" if db_connected else "none"
    connected_at = row.get("connected_at") if row else None
    updated_at = row.get("updated_at") if row else None
    return OAuthCredentialPublic(
        platform=slug,
        connected=db_connected,
        source=source,
        connected_by=str(row.get("connected_by")) if row and row.get("connected_by") else None,
        connected_at=connected_at.isoformat() if connected_at else None,
        updated_at=updated_at.isoformat() if updated_at else None,
        scopes=str(row.get("scopes")) if row and row.get("scopes") else None,
        metadata=dict(row.get("metadata_json") or {}) if row else {},
        decrypt_error=decrypt_error and not db_connected,
    )


def all_public_status() -> dict[str, OAuthCredentialPublic]:
    """Load connected/disconnected state for all platforms in one query."""
    out: dict[str, OAuthCredentialPublic] = {}
    for platform in sorted(PLATFORMS):
        out[platform] = _public_status_from_row(platform, None)
    if not enabled():
        return out
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT platform, refresh_token_enc, access_token_enc, token_expires_at,
                   scopes, metadata_json, connected_by, connected_at, updated_at
            FROM oauth_credentials
            WHERE platform = ANY(%s) AND client_slug = ''
            """,
            (list(PLATFORMS),),
        ).fetchall()
    by_platform: dict[str, dict[str, Any]] = {}
    for row in rows:
        slug = str(row[0])
        meta = row[5]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        elif meta is None:
            meta = {}
        by_platform[slug] = {
            "refresh_token_enc": row[1],
            "access_token_enc": row[2],
            "token_expires_at": row[3],
            "scopes": row[4],
            "metadata_json": meta if isinstance(meta, dict) else {},
            "connected_by": row[6],
            "connected_at": row[7],
            "updated_at": row[8],
        }
    for platform in PLATFORMS:
        out[platform] = _public_status_from_row(platform, by_platform.get(platform))
    return out
