"""Per-client dashboard account mapping stored in Postgres (admin-editable)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
import db

import web_users

SCHEMA_SQL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS client_dashboard_config (
      client_slug TEXT PRIMARY KEY,
      label TEXT NOT NULL DEFAULT '',
      google_customer_id TEXT,
      linkedin_account_id TEXT,
      meta_account_id TEXT,
      ga4_client_key TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_by TEXT
    )
    """,
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS theme_json JSONB
    """,
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS monthly_budget_usd NUMERIC(14,2)
    """,
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS features_json JSONB
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gcp_project_id TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS bq_mart_dataset_id TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS dashboard_mode TEXT NOT NULL DEFAULT 'api'
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gsc_site_url TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS semrush_domain TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gtm_account_id TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gtm_container_id TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gsc_branded_roots TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gsc_target_keywords TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gsc_branded_exclude TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS gsc_target_exclude TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS ga4_key_events TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS explorer_filters TEXT
    """,
    """
    ALTER TABLE client_dashboard_config ADD COLUMN IF NOT EXISTS pagespeed_targets JSONB
    """,
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS explorer_budget_tracker BOOLEAN NOT NULL DEFAULT TRUE
    """,
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS overview_pinned_card TEXT
    """,
]


@dataclass(frozen=True)
class ClientConfigRow:
    client_slug: str
    label: str
    google_customer_id: str | None
    linkedin_account_id: str | None
    meta_account_id: str | None
    ga4_client_key: str | None
    monthly_budget_usd: float | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    gcp_project_id: str | None = None
    bq_mart_dataset_id: str | None = None
    dashboard_mode: str = "api"
    gsc_site_url: str | None = None
    semrush_domain: str | None = None
    gtm_account_id: str | None = None
    gtm_container_id: str | None = None
    gsc_branded_roots: str | None = None
    gsc_target_keywords: str | None = None
    ga4_key_events: str | None = None
    explorer_filters: str | None = None
    explorer_budget_tracker: bool = True
    gsc_branded_exclude: str | None = None
    gsc_target_exclude: str | None = None
    overview_pinned_card: str | None = None


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


def get_config(client_slug: str) -> ClientConfigRow | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT client_slug, label, google_customer_id, linkedin_account_id,
                   meta_account_id, ga4_client_key,
                   monthly_budget_usd, updated_at, updated_by,
                   gcp_project_id, bq_mart_dataset_id,
                   dashboard_mode, gsc_site_url, semrush_domain,
                   gtm_account_id, gtm_container_id,
                   gsc_branded_roots, gsc_target_keywords, ga4_key_events,
                   explorer_filters, explorer_budget_tracker,
                   gsc_branded_exclude, gsc_target_exclude,
                   overview_pinned_card
            FROM client_dashboard_config
            WHERE client_slug = %s
            """,
            (slug,),
        ).fetchone()
    if not row:
        return None

    def _s(v: object) -> str | None:
        return str(v).strip() or None if v else None

    budget_raw = row[6]
    updated = row[7]
    return ClientConfigRow(
        client_slug=str(row[0]),
        label=str(row[1] or ""),
        google_customer_id=_s(row[2]),
        linkedin_account_id=_s(row[3]),
        meta_account_id=_s(row[4]),
        ga4_client_key=_s(row[5]),
        monthly_budget_usd=float(budget_raw) if budget_raw is not None else None,
        updated_at=updated.isoformat() if updated else None,
        updated_by=_s(row[8]),
        gcp_project_id=_s(row[9]),
        bq_mart_dataset_id=_s(row[10]),
        dashboard_mode=str(row[11] or "api").strip() or "api",
        gsc_site_url=_s(row[12]),
        semrush_domain=_s(row[13]),
        gtm_account_id=_s(row[14]),
        gtm_container_id=_s(row[15]),
        gsc_branded_roots=_s(row[16]),
        gsc_target_keywords=_s(row[17]),
        ga4_key_events=_s(row[18]),
        explorer_filters=_s(row[19]),
        explorer_budget_tracker=bool(row[20]) if row[20] is not None else True,
        gsc_branded_exclude=_s(row[21]),
        gsc_target_exclude=_s(row[22]),
        overview_pinned_card=_s(row[23]),
    )


def save_config(
    client_slug: str,
    *,
    label: str,
    google_customer_id: str | None,
    linkedin_account_id: str | None,
    meta_account_id: str | None,
    ga4_client_key: str | None,
    updated_by: str | None = None,
    gcp_project_id: str | None = None,
    bq_mart_dataset_id: str | None = None,
    dashboard_mode: str | None = None,
    gsc_site_url: str | None = None,
    semrush_domain: str | None = None,
    gtm_account_id: str | None = None,
    gtm_container_id: str | None = None,
    gsc_branded_roots: str | None = None,
    gsc_target_keywords: str | None = None,
) -> ClientConfigRow:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")

    def _clean(val: str | None) -> str | None:
        text = (val or "").strip()
        return text or None

    ensure_schema()
    now = datetime.now(tz=UTC)
    # Build optional column updates dynamically (only include when caller provides a value)
    _optional: list[tuple[str, object]] = []
    if gcp_project_id is not None:
        _optional.append(("gcp_project_id", _clean(gcp_project_id)))
    if bq_mart_dataset_id is not None:
        _optional.append(("bq_mart_dataset_id", _clean(bq_mart_dataset_id)))
    if dashboard_mode is not None:
        _optional.append(("dashboard_mode", (_clean(dashboard_mode) or "api")))
    if gsc_site_url is not None:
        _optional.append(("gsc_site_url", _clean(gsc_site_url)))
    if semrush_domain is not None:
        _optional.append(("semrush_domain", _clean(semrush_domain)))
    if gtm_account_id is not None:
        _optional.append(("gtm_account_id", _clean(gtm_account_id)))
    if gtm_container_id is not None:
        _optional.append(("gtm_container_id", _clean(gtm_container_id)))
    if gsc_branded_roots is not None:
        _optional.append(("gsc_branded_roots", _clean(gsc_branded_roots)))
    if gsc_target_keywords is not None:
        _optional.append(("gsc_target_keywords", _clean(gsc_target_keywords)))

    gcp_set_clause = "".join(
        f",\n              {col} = EXCLUDED.{col}" for col, _ in _optional
    )
    extra_cols = "".join(f", {col}" for col, _ in _optional)
    extra_placeholders = "".join(", %s" for _ in _optional)
    extra_vals: list = [val for _, val in _optional]

    with db.connection() as conn:
        conn.execute(
            f"""
            INSERT INTO client_dashboard_config (
              client_slug, label, google_customer_id, linkedin_account_id,
              meta_account_id, ga4_client_key, updated_at, updated_by{extra_cols}
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s{extra_placeholders})
            ON CONFLICT (client_slug)
            DO UPDATE SET
              label = EXCLUDED.label,
              google_customer_id = EXCLUDED.google_customer_id,
              linkedin_account_id = EXCLUDED.linkedin_account_id,
              meta_account_id = EXCLUDED.meta_account_id,
              ga4_client_key = EXCLUDED.ga4_client_key,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by{gcp_set_clause}
            """,
            (
                slug,
                (label or "").strip() or slug,
                _clean(google_customer_id),
                _clean(linkedin_account_id),
                _clean(meta_account_id),
                _clean(ga4_client_key),
                now,
                (updated_by or "").strip() or None,
                *extra_vals,
            ),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def update_gsc_keywords(
    client_slug: str,
    *,
    branded_roots: str | None,
    target_keywords: str | None,
    branded_exclude: str | None = None,
    target_exclude: str | None = None,
    updated_by: str | None = None,
) -> None:
    """Set the Search Console branded/target keyword filters for a client,
    touching only those columns (label/accounts are left untouched).

    Each group has include roots (branded_roots / target_keywords) and optional
    exclude roots (branded_exclude / target_exclude): a query counts toward a
    group when it contains any include root AND none of the exclude roots -- so
    a client can, e.g., include "benjamin" as branded but exclude "dr"."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save keyword config.")

    def _clean(val: str | None) -> str | None:
        return (val or "").strip() or None

    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, updated_at, updated_by,
              gsc_branded_roots, gsc_target_keywords,
              gsc_branded_exclude, gsc_target_exclude
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              gsc_branded_roots = EXCLUDED.gsc_branded_roots,
              gsc_target_keywords = EXCLUDED.gsc_target_keywords,
              gsc_branded_exclude = EXCLUDED.gsc_branded_exclude,
              gsc_target_exclude = EXCLUDED.gsc_target_exclude,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, slug, now, _clean(updated_by),
             _clean(branded_roots), _clean(target_keywords),
             _clean(branded_exclude), _clean(target_exclude)),
        )


def update_ga4_key_events(
    client_slug: str,
    *,
    event_names: str | None,
    updated_by: str | None = None,
) -> None:
    """Set the client's selected GA4 key events (newline-separated event names).
    Empty = fall back to GA4's own key-event designation. Touches only that
    column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save key-event config.")

    def _clean(val: str | None) -> str | None:
        return (val or "").strip() or None

    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, updated_at, updated_by, ga4_key_events
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              ga4_key_events = EXCLUDED.ga4_key_events,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, slug, now, _clean(updated_by), _clean(event_names)),
        )


def update_explorer_filters(
    client_slug: str,
    *,
    filters_text: str | None,
    updated_by: str | None = None,
) -> None:
    """Set the client's Campaign Explorer filter chips (one `Label = phrase`
    per line, optionally split into `[Group]` sections). Empty = fall back to
    the renderer's built-in default chips. Touches only that column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save explorer filters.")

    def _clean(val: str | None) -> str | None:
        return (val or "").strip() or None

    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, updated_at, updated_by, explorer_filters
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              explorer_filters = EXCLUDED.explorer_filters,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, slug, now, _clean(updated_by), _clean(filters_text)),
        )


def update_overview_pinned_card(
    client_slug: str,
    *,
    card_key: str | None,
    updated_by: str | None = None,
) -> None:
    """Set which Overview card the admin has pinned to the top (BigQuery-mart
    dashboard). Stores a single stable card key (e.g. ``"website"``); empty/None
    clears the pin so the Overview falls back to its natural order. Touches only
    that column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save the pinned overview card.")

    def _clean(val: str | None) -> str | None:
        return (val or "").strip() or None

    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, updated_at, updated_by, overview_pinned_card
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug) DO UPDATE SET
              overview_pinned_card = EXCLUDED.overview_pinned_card,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, slug, now, _clean(updated_by), _clean(card_key)),
        )


def save_explorer_budget_tracker(
    client_slug: str,
    show: bool,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Toggle whether the budget tracker module shows on the Campaign Explorer."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, explorer_budget_tracker, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              explorer_budget_tracker = EXCLUDED.explorer_budget_tracker,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, bool(show), now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def save_monthly_budget(
    client_slug: str,
    monthly_budget_usd: float | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    if monthly_budget_usd is not None and monthly_budget_usd < 0:
        raise ValueError("Monthly budget must be zero or greater.")

    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, monthly_budget_usd, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              monthly_budget_usd = EXCLUDED.monthly_budget_usd,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (
                slug,
                label,
                monthly_budget_usd,
                now,
                (updated_by or "").strip() or None,
            ),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def list_config_labels() -> dict[str, str]:
    """Return {client_slug: label} for all rows with a non-empty label."""
    if not enabled():
        return {}
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT client_slug, label FROM client_dashboard_config WHERE label <> ''"
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows if row[1]}


def list_budget_overview() -> list[dict[str, Any]]:
    """All clients with the fields the HQ budget page needs, ordered by label.

    Just the columns budget pacing cares about (slug, label, budget, and the
    BigQuery mart location) so the HQ view can loop clients without loading a
    full ClientConfigRow each. Rows keep their raw values; the caller decides
    which are spend-computable (those with a gcp_project_id)."""
    if not enabled():
        return []
    ensure_schema()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT client_slug, label, monthly_budget_usd,
                   gcp_project_id, bq_mart_dataset_id, dashboard_mode
            FROM client_dashboard_config
            ORDER BY LOWER(NULLIF(label, '')), client_slug
            """
        ).fetchall()
    return [
        {
            "client_slug": str(r[0]),
            "label": str(r[1] or "").strip() or str(r[0]),
            "monthly_budget_usd": float(r[2]) if r[2] is not None else None,
            "gcp_project_id": (str(r[3]).strip() or None) if r[3] else None,
            "bq_mart_dataset_id": (str(r[4]).strip() or None) if r[4] else None,
            "dashboard_mode": str(r[5] or "api"),
        }
        for r in rows
    ]


def get_features(client_slug: str) -> dict[str, Any] | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT features_json FROM client_dashboard_config WHERE client_slug = %s",
            (slug,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    payload = row[0]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict):
        return payload
    return None


def save_features(
    client_slug: str,
    features: dict[str, Any],
    *,
    updated_by: str | None = None,
) -> dict[str, Any]:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save dashboard features.")

    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, features_json, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              features_json = EXCLUDED.features_json,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (
                slug,
                label,
                json.dumps(features),
                now,
                (updated_by or "").strip() or None,
            ),
        )
    saved = get_features(slug)
    if saved is None:
        raise RuntimeError("Failed to load saved dashboard features.")
    return saved


def get_theme(client_slug: str) -> dict[str, Any] | None:
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT theme_json FROM client_dashboard_config WHERE client_slug = %s",
            (slug,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    payload = row[0]
    if isinstance(payload, str):
        return json.loads(payload)
    if isinstance(payload, dict):
        return payload
    return None


def save_theme(
    client_slug: str,
    theme: dict[str, Any],
    *,
    updated_by: str | None = None,
) -> dict[str, Any]:
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard theme.")

    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, theme_json, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              theme_json = EXCLUDED.theme_json,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (
                slug,
                label,
                json.dumps(theme),
                now,
                (updated_by or "").strip() or None,
            ),
        )
    saved = get_theme(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client theme.")
    return saved


def get_pagespeed_targets(client_slug: str) -> dict[str, Any] | None:
    """Per-KPI PageSpeed target bands ({kpi: {min, max}}), used for the Site
    Performance tab's traffic-light coloring. None = fall back to defaults."""
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return None
    ensure_schema()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT pagespeed_targets FROM client_dashboard_config WHERE client_slug = %s",
            (slug,),
        ).fetchone()
    if not row or row[0] is None:
        return None
    payload = row[0]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def save_pagespeed_targets(
    client_slug: str,
    targets: dict[str, Any],
    *,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Persist per-KPI PageSpeed target bands. Touches only that column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save PageSpeed targets.")

    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, pagespeed_targets, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              pagespeed_targets = EXCLUDED.pagespeed_targets,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, slug, json.dumps(targets), now, (updated_by or "").strip() or None),
        )
    saved = get_pagespeed_targets(slug)
    if saved is None:
        raise RuntimeError("Failed to load saved PageSpeed targets.")
    return saved


def as_dict(row: ClientConfigRow | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "client_slug": row.client_slug,
        "label": row.label,
        "google_customer_id": row.google_customer_id,
        "linkedin_account_id": row.linkedin_account_id,
        "meta_account_id": row.meta_account_id,
        "ga4_client_key": row.ga4_client_key,
        "monthly_budget_usd": row.monthly_budget_usd,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
        "gcp_project_id": row.gcp_project_id,
        "bq_mart_dataset_id": row.bq_mart_dataset_id,
        "dashboard_mode": row.dashboard_mode,
        "gsc_site_url": row.gsc_site_url,
        "semrush_domain": row.semrush_domain,
    }
