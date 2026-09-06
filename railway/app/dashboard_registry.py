"""Admin-managed dashboard client registry (Postgres)."""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import client_industries
import db
import db_migrate

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
    # Industry buckets: a comma-separated list of client_industries keys, or
    # NULL for untagged. Kept as plain TEXT rather than an enum/lookup table so
    # the taxonomy can be reworded in code without a migration; unknown keys read
    # as "Unassigned". Single-key rows written before multi-select shipped are
    # already valid one-element lists, so there is nothing to backfill.
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
    # Every industry the account is tagged with, in taxonomy order (empty when
    # untagged). ``industry`` below is the first of them, kept so callers that
    # only ever wanted "a" bucket keep working.
    industries: tuple[str, ...] = ()

    @property
    def industry(self) -> str | None:
        return self.industries[0] if self.industries else None

    @property
    def industry_label(self) -> str:
        """All of the account's labels as one string, or *Unassigned*."""
        return client_industries.label_list(self.industries)


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


# Every read below opens with ensure_schema(), and the page chrome reads this
# registry several times per request (the client switcher needs labels, logos and
# the suppression list). Re-running the DDL each time is not free: every
# ALTER TABLE takes an ACCESS EXCLUSIVE lock, and with DB_POOL_ENABLED off each
# run also costs a fresh Postgres connection — one dashboard page render was
# spending 5 of these before a single number reached the page. Same disease, and
# the same cure, as web_users #297: a process-level flag (with a lock, since sync
# FastAPI handlers run in a threadpool) so the DDL runs at most once per process,
# plus the shared transaction-scoped advisory lock so this path can never run
# table-locking DDL concurrently with the migration runner. The statements are
# all IF NOT EXISTS and the seeding is ON CONFLICT DO NOTHING, so once per
# process is as correct as once per call.
#
# Seeding is tracked separately because callers ask for it separately: a
# seed_defaults=False caller must not mark the seeding done.
_schema_ready = False
_seed_ready = False
_schema_lock = threading.Lock()
_SCHEMA_ADVISORY_LOCK_KEY = db_migrate.SCHEMA_ADVISORY_LOCK_KEY


def reset_schema_cache() -> None:
    """Forget that the schema/seed ran (for tests, and after a DB switch)."""
    global _schema_ready, _seed_ready
    with _schema_lock:
        _schema_ready = False
        _seed_ready = False


def ensure_schema(*, seed_defaults: bool = True) -> bool:
    global _schema_ready, _seed_ready
    url = _get_db_url()
    if not url:
        return False
    if _schema_ready and (_seed_ready or not seed_defaults):
        return True
    with _schema_lock:
        need_ddl = not _schema_ready
        need_seed = seed_defaults and not _seed_ready
        if not (need_ddl or need_seed):
            return True
        with db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_LOCK_KEY,))
            if need_ddl:
                for stmt in SCHEMA_SQL_STATEMENTS:
                    conn.execute(stmt)
            if need_seed:
                _seed_defaults(conn)
        # Only recorded once the statements actually ran — a failure raises out of
        # here with the flags untouched, so the next caller retries as before.
        if need_ddl:
            _schema_ready = True
        if need_seed:
            _seed_ready = True
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


def list_clients(*, with_logos: bool = True) -> list[DashboardClientRow]:
    """Every registered dashboard client, ordered by label.

    ``with_logos=False`` leaves the ``logo`` column out of the SELECT. Logos are
    admin-uploaded data URIs, so they are by far the largest thing in this table
    — and most callers only want slugs and labels (see ``list_slugs`` and
    ``client_config._labels_for_slugs``). Reading them there meant a page render
    pulled every client's base64 logo over the wire several times to render one
    switcher that needs them exactly once.
    """
    if not enabled():
        return []
    ensure_schema(seed_defaults=True)
    logo_col = "logo" if with_logos else "NULL AS logo"
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT client_slug, label, source, created_at, created_by, {logo_col}, industry
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
                industries=client_industries.normalize_many(
                    row[6] if len(row) > 6 else None
                ),
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


def set_industries(
    client_slug: str, industries: str | Iterable[str | None] | None
) -> bool:
    """Tag (or untag, with None/""/[]) a client with client_industries keys.

    Takes one key, a list of keys (what the admin checkboxes post), or the
    comma-separated storage form. Unrecognized keys are dropped rather than
    raising — the taxonomy lives in code, so a key retired from the list should
    degrade to "Unassigned" instead of wedging the account row.
    """
    if not enabled():
        return False
    slug = normalize_slug(client_slug)
    value = client_industries.serialize(industries)
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        cur = conn.execute(
            "UPDATE dashboard_clients SET industry = %s WHERE client_slug = %s",
            (value, slug),
        )
        return cur.rowcount > 0


def set_industry(client_slug: str, industry: str | None) -> bool:
    """Single-key alias for :func:`set_industries` (one industry replaces all)."""
    return set_industries(client_slug, industry)


def industries_map() -> dict[str, tuple[str, ...]]:
    """{client_slug: (industry_key, …)} for every *tagged* client — one query.

    The Benchmarks rollup joins this onto its per-client metrics by slug, so it
    never needs a per-client registry read. Untagged clients — and clients whose
    only stored keys have since been retired — are simply absent.
    """
    if not enabled():
        return {}
    ensure_schema(seed_defaults=False)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug, industry FROM dashboard_clients WHERE industry IS NOT NULL"
        ).fetchall()
    out: dict[str, tuple[str, ...]] = {}
    for row in rows:
        keys = client_industries.normalize_many(row[1])
        if keys:
            out[str(row[0])] = keys
    return out


def list_slugs() -> list[str]:
    return [row.client_slug for row in list_clients(with_logos=False)]


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
        industries=client_industries.normalize_many(row[5] if len(row) > 5 else None),
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
        # ...and from the account's Sagefrog team, so a slug reused later can't
        # inherit the old account's staffing. The table is created by the startup
        # migration runner (client_team:0001_baseline), so it is always present here.
        conn.execute(
            "DELETE FROM client_team_members WHERE client_slug = %s",
            (slug,),
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
