"""Admin-managed dashboard client registry (Postgres)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
import client_industries
import db

import web_users

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$|^[a-z]$")
_RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "cron",
        "dashboard",
        "health",
        "internal",
        "login",
        "logout",
        "oauth",
        "static",
        "docs",
        "openapi",
    }
)
_PROTECTED_SLUGS: frozenset[str] = frozenset()

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS dashboard_clients (
      client_slug TEXT PRIMARY KEY,
      label TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'admin',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_by TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dashboard_client_suppressions (
      client_slug TEXT PRIMARY KEY,
      deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      deleted_by TEXT
    )
    """,
    # Client logo, stored as a small data: URI (resized client-side).
    """
    ALTER TABLE dashboard_clients ADD COLUMN IF NOT EXISTS logo TEXT
    """,
    # Industry bucket (a client_industries key, or NULL for untagged). Kept as a
    # plain TEXT key rather than an enum/lookup table so the taxonomy can be
    # reworded in code without a migration; unknown keys read as "Unassigned".
    """
    ALTER TABLE dashboard_clients ADD COLUMN IF NOT EXISTS industry TEXT
    """,
]


@dataclass(frozen=True)
class DashboardClientRow:
    client_slug: str
    label: str
    source: str
    created_at: str | None = None
    created_by: str | None = None
    logo: str | None = None
    industry: str | None = None


def _get_db_url() -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    return url or None


def enabled() -> bool:
    return web_users.enabled()


def normalize_slug(raw: str) -> str:
    return (raw or "").strip().lower()


def validate_slug(slug: str) -> str:
    text = normalize_slug(slug)
    if not text:
        raise ValueError("Dashboard slug is required.")
    if text in _RESERVED_SLUGS:
        raise ValueError(f"'{text}' is a reserved slug.")
    if not _SLUG_RE.match(text):
        raise ValueError(
            "Slug must be lowercase letters, numbers, and hyphens (e.g. nixon or acme-corp)."
        )
    return text


def validate_label(label: str) -> str:
    text = (label or "").strip()
    if not text:
        raise ValueError("Dashboard name is required.")
    if len(text) > 120:
        raise ValueError("Dashboard name must be 120 characters or fewer.")
    return text


def ensure_schema(*, seed_defaults: bool = True) -> bool:
    url = _get_db_url()
    if not url:
        return False
    with db.connection() as conn:
        for stmt in SCHEMA_SQL_STATEMENTS:
            conn.execute(stmt)
        if seed_defaults:
            _seed_defaults(conn)
    return True


def suppressed_slugs() -> set[str]:
    if not enabled():
        return set()
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug FROM dashboard_client_suppressions"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _seed_defaults(conn) -> None:
    import client_config

    suppressed = {
        str(row[0])
        for row in conn.execute(
            "SELECT client_slug FROM dashboard_client_suppressions"
        ).fetchall()
    }

    now = datetime.now(tz=UTC)
    seeds: list[tuple[str, str, str]] = []
    for slug, entry in client_config._BUILTIN_CLIENTS.items():
        label = str(entry.get("label") or slug.replace("-", " ").title())
        seeds.append((slug, label, "builtin"))
    for slug, entry in client_config._load_registry_from_env().items():
        label = str(entry.get("label") or slug.replace("-", " ").title())
        seeds.append((slug, label, "env"))

    seen: set[str] = set()
    for slug, label, source in seeds:
        if slug in seen or slug in suppressed:
            continue
        seen.add(slug)
        conn.execute(
            """
            INSERT INTO dashboard_clients (client_slug, label, source, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (client_slug) DO NOTHING
            """,
            (slug, label, source, now),
        )


def list_clients() -> list[DashboardClientRow]:
    if not enabled():
        return []
    ensure_schema(seed_defaults=True)
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT client_slug, label, source, created_at, created_by, logo, industry
            FROM dashboard_clients
            ORDER BY label ASC, client_slug ASC
            """
        ).fetchall()
    out: list[DashboardClientRow] = []
    for row in rows:
        created = row[3]
        out.append(
            DashboardClientRow(
                client_slug=str(row[0]),
                label=str(row[1] or ""),
                source=str(row[2] or "admin"),
                created_at=created.isoformat() if created else None,
                created_by=str(row[4]).strip() if row[4] else None,
                logo=str(row[5]) if len(row) > 5 and row[5] is not None else None,
                industry=client_industries.normalize(row[6] if len(row) > 6 else None),
            )
        )
    return out


def set_logo(client_slug: str, logo: str | None) -> bool:
    """Set (or clear, with None) a dashboard's logo data URI."""
    if not enabled():
        return False
    slug = normalize_slug(client_slug)
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE dashboard_clients SET logo = %s WHERE client_slug = %s",
            (logo, slug),
        )
        return cur.rowcount > 0


def set_industry(client_slug: str, industry: str | None) -> bool:
    """Tag (or untag, with None/"") a client with a client_industries key.

    An unrecognized key clears the tag rather than raising — the taxonomy lives
    in code, so a key retired from the list should degrade to "Unassigned"
    instead of wedging the account row.
    """
    if not enabled():
        return False
    slug = normalize_slug(client_slug)
    key = client_industries.normalize(industry)
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE dashboard_clients SET industry = %s WHERE client_slug = %s",
            (key, slug),
        )
        return cur.rowcount > 0


def industry_map() -> dict[str, str]:
    """{client_slug: industry_key} for every *tagged* client — one query.

    The Benchmarks rollup joins this onto its per-client metrics by slug, so it
    never needs a per-client registry read. Untagged clients are simply absent.
    """
    if not enabled():
        return {}
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug, industry FROM dashboard_clients WHERE industry IS NOT NULL"
        ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        key = client_industries.normalize(row[1])
        if key:
            out[str(row[0])] = key
    return out


def list_slugs() -> list[str]:
    return [row.client_slug for row in list_clients()]


def get_client(client_slug: str) -> DashboardClientRow | None:
    slug = normalize_slug(client_slug)
    if not slug or not enabled():
        return None
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT client_slug, label, source, created_at, created_by, industry
            FROM dashboard_clients
            WHERE client_slug = %s
            """,
            (slug,),
        ).fetchone()
    if not row:
        return None
    created = row[3]
    return DashboardClientRow(
        client_slug=str(row[0]),
        label=str(row[1] or ""),
        source=str(row[2] or "admin"),
        created_at=created.isoformat() if created else None,
        created_by=str(row[4]).strip() if row[4] else None,
        industry=client_industries.normalize(row[5] if len(row) > 5 else None),
    )


def has_slug(client_slug: str) -> bool:
    return get_client(client_slug) is not None


def create_client(
    *,
    client_slug: str,
    label: str,
    created_by: str | None = None,
) -> DashboardClientRow:
    slug = validate_slug(client_slug)
    name = validate_label(label)
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to manage dashboards.")

    ensure_schema(seed_defaults=True)
    if get_client(slug):
        raise ValueError(f"Dashboard '{slug}' already exists.")

    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            "DELETE FROM dashboard_client_suppressions WHERE client_slug = %s",
            (slug,),
        )
        conn.execute(
            """
            INSERT INTO dashboard_clients (client_slug, label, source, created_at, created_by)
            VALUES (%s, %s, 'admin', %s, %s)
            """,
            (slug, name, now, (created_by or "").strip() or None),
        )

    import client_dashboard_config as cdc

    cdc.save_config(
        slug,
        label=name,
        google_customer_id=None,
        linkedin_account_id=None,
        meta_account_id=None,
        ga4_client_key=slug,
        updated_by=created_by or "admin",
        # New dashboards go straight to the connector-based Nixon-style
        # template (empty/"no data yet" until a connector is connected),
        # not the older Penn-style snapshot dashboard -- previously this was
        # left unset here, defaulting to "api" mode, so every freshly
        # created dashboard showed the old template until a settings save
        # (which itself used to hardcode plain "bigquery" mode) happened.
        dashboard_mode="bigquery_nixon",
    )
    row = get_client(slug)
    if not row:
        raise RuntimeError("Failed to load created dashboard.")
    return row


def rename_client(
    *,
    client_slug: str,
    label: str,
    updated_by: str | None = None,
) -> DashboardClientRow:
    """Change a dashboard's display label, keeping the slug intact.

    The slug is the immutable internal key: routes (``/dashboard/<slug>/…``),
    BigQuery routing, OAuth token storage, and config lookups all key on it, so
    only the human-facing label changes here. Updates the registry row and — when
    one exists — the matching ``client_dashboard_config`` row, so the picker,
    workspace switcher, and dashboard header all read the new name.
    """
    slug = normalize_slug(client_slug)
    name = validate_label(label)
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to manage dashboards.")

    ensure_schema(seed_defaults=False)
    if not get_client(slug):
        raise ValueError(f"Unknown dashboard '{slug}'.")

    with db.connection() as conn:
        conn.execute(
            "UPDATE dashboard_clients SET label = %s WHERE client_slug = %s",
            (name, slug),
        )

    # Keep the client_dashboard_config label (read by client_label() and the
    # dashboard header) in step. No-op when the client has no config row yet.
    try:
        import client_dashboard_config as cdc

        cdc.set_label(slug, name, updated_by=updated_by)
    except Exception:
        pass

    row = get_client(slug)
    if not row:
        raise RuntimeError("Failed to load renamed dashboard.")
    return row


def delete_client(
    *,
    client_slug: str,
    confirm_label: str,
    deleted_by: str | None = None,
) -> dict[str, Any]:
    slug = normalize_slug(client_slug)
    if not slug:
        raise ValueError("Dashboard slug is required.")
    if slug in _PROTECTED_SLUGS:
        raise ValueError(f"The '{slug}' dashboard cannot be deleted.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to manage dashboards.")

    ensure_schema(seed_defaults=False)
    row = get_client(slug)
    if not row:
        raise ValueError(f"Unknown dashboard '{slug}'.")

    expected = row.label.strip()
    typed = (confirm_label or "").strip()
    if typed != expected:
        raise ValueError(f"Confirmation name must exactly match '{expected}'.")

    with db.connection() as conn:
        conn.execute(
            "DELETE FROM client_insight_documents WHERE client_slug = %s",
            (slug,),
        )
        conn.execute(
            "DELETE FROM client_insight_folders WHERE client_slug = %s",
            (slug,),
        )
        conn.execute(
            "DELETE FROM client_business_line_rules WHERE client_slug = %s",
            (slug,),
        )
        conn.execute(
            "DELETE FROM dashboard_snapshots WHERE client_key = %s",
            (slug,),
        )
        conn.execute(
            "DELETE FROM client_dashboard_config WHERE client_slug = %s",
            (slug,),
        )
        conn.execute(
            "UPDATE web_users SET client_slug = NULL, updated_at = NOW() WHERE client_slug = %s",
            (slug,),
        )
        # Also drop the slug from any 'standard' user's per-client access list.
        conn.execute(
            "UPDATE web_users SET allowed_client_slugs = array_remove(allowed_client_slugs, %s), "
            "updated_at = NOW() WHERE %s = ANY(allowed_client_slugs)",
            (slug, slug),
        )
        deleted = conn.execute(
            "DELETE FROM dashboard_clients WHERE client_slug = %s RETURNING client_slug",
            (slug,),
        ).fetchone()
        if deleted:
            conn.execute(
                """
                INSERT INTO dashboard_client_suppressions (client_slug, deleted_at, deleted_by)
                VALUES (%s, NOW(), %s)
                ON CONFLICT (client_slug) DO UPDATE
                  SET deleted_at = EXCLUDED.deleted_at,
                      deleted_by = EXCLUDED.deleted_by
                """,
                (slug, (deleted_by or "").strip() or None),
            )
    if not deleted:
        raise RuntimeError(f"Failed to delete dashboard '{slug}'.")

    return {
        "client_slug": slug,
        "label": expected,
        "deleted_by": (deleted_by or "").strip() or None,
    }
