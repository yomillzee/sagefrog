"""Per-client dashboard account mapping stored in Postgres (admin-editable)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS consent_sidebar_enabled BOOLEAN NOT NULL DEFAULT FALSE
    """,
    # Each client's headline KPI for the HQ view, stored as a small JSON spec
    # ({"type","label","goal"}). Client KPIs differ widely (MQLs, Google Ads
    # conversions, ROAS, …), so the type maps to a resolver rather than a fixed
    # column — see dashboard.services.kpi_registry.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS primary_kpi JSONB
    """,
    # Optional admin override of a client's active ad days for budget pacing,
    # stored as an ISO-weekday CSV (Mon=1..Sun=7, e.g. "1,2,3,4,5" for weekdays
    # only). NULL means auto-detect the rhythm from spend history — see
    # dashboard.utils.pacing.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS pacing_active_weekdays TEXT
    """,
    # How the campaign/segment filters classify this client's data:
    #   'business_lines' — keyword-based business-line rules (see penn_business_lines)
    #   'regions'        — geographic region rules (see dashboard_regions)
    #   NULL             — no segment filters
    # Drives the filter UI and data grouping with no client-name branching in code.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS segment_filter_profile TEXT
    """,
    # Which client-facing sidebar section tabs an admin has hidden for this
    # client, stored as a JSON array of tab keys (e.g. ["ai_traffic"]). Empty /
    # NULL means every core tab shows. This is server-side and per client, so a
    # tab an admin hides stays hidden for every user of that client's portal in
    # every browser — unlike the old per-browser localStorage toggle.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS sidebar_hidden_tabs JSONB
    """,
    # Admin-authored per-tab card layout, stored as
    #   {tab_key: {"order": [card_key, ...], "hidden": [card_key, ...]}}.
    # An admin enters a tab's "edit mode" to hide a card, show one back, or drag
    # to reorder; the choice is saved here and applied server-side on render, so
    # (like sidebar_hidden_tabs) it holds for every user of that client's portal
    # in every browser. Cards a client never sees are simply not emitted for
    # them — they can't tell anything was hidden. Empty/NULL = every card shows
    # in its natural order. Card keys are validated against the tab's known
    # cards by the caller, so a stale/unknown key here is ignored on render.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS card_layouts JSONB
    """,
    # The date-range preset the dashboard lands on for this client, chosen by an
    # admin via the Range picker's "Make default" control (e.g. 'last_month').
    # NULL means fall back to the renderer's built-in default ('last_30'). Only
    # one of the known DATE_RANGE_PRESETS is ever stored.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS default_date_preset TEXT
    """,
    # Campaign allowlist for the Campaign Explorer, stored as a JSON array of
    # campaign names. When set, the Explorer table (and its summary cards) only
    # shows these campaigns — used when the account pulls more campaigns than the
    # portal's client should see. Empty/NULL = show every campaign. Set by an
    # admin from the Explorer's "Campaigns" picker.
    """
    ALTER TABLE client_dashboard_config
      ADD COLUMN IF NOT EXISTS explorer_campaign_allowlist JSONB
    """,
]

# The date-range presets the dashboard's Range picker offers; a stored
# default_date_preset must be one of these (mirrors the <option> values in
# bigquery_dashboard_renderer's #datePresets select).
DATE_RANGE_PRESETS: tuple[str, ...] = (
    "last_7",
    "last_30",
    "last_90",
    "last_365",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
)

# The client-facing section tabs an admin can show/hide from the Advanced admin
# tab. Keys mirror the ``data-tab`` attributes the sidebar nav renders; anything
# outside this set is ignored on save so a stray value can't wedge the sidebar.
SIDEBAR_TOGGLEABLE_TABS: tuple[str, ...] = (
    "overview",
    "explorer",
    "analytics",
    "ai_traffic",
    "gsc",
)


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
    # Headline KPI spec for the HQ view: {"type": <registry id>, "label": str,
    # "goal": float|None}. None when the client has no KPI configured.
    primary_kpi: dict[str, Any] | None = None
    # Whether Consent & Tracking Health appears in the client-viewable sidebar.
    # Off by default: most clients don't need it, and it only clutters their nav —
    # admins turn it on per client from Settings.
    consent_sidebar_enabled: bool = False
    # Admin override of the client's active ad days for budget pacing, as an ISO
    # weekday CSV (Mon=1..Sun=7). None = auto-detect from spend history.
    pacing_active_weekdays: str | None = None
    # Segment-filter classification: 'business_lines', 'regions', or None. Drives
    # the campaign/segment filter UI and grouping — see penn_business_lines.
    segment_filter_profile: str | None = None
    # Client-facing sidebar tabs an admin has hidden for this client, as a tuple
    # of tab keys (subset of SIDEBAR_TOGGLEABLE_TABS). Empty = every tab shows.
    sidebar_hidden_tabs: tuple[str, ...] = ()
    # Admin-authored per-tab card layout: {tab_key: {"order": [...], "hidden":
    # [...]}}. Empty dict = every card shows in natural order. See the
    # card_layouts column comment above and get_card_layout / save_card_layout.
    card_layouts: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # Date-range preset the dashboard lands on for this client (one of
    # DATE_RANGE_PRESETS), or None to use the renderer's built-in default.
    default_date_preset: str | None = None
    # Campaign Explorer allowlist: campaign names the portal's client is allowed
    # to see. Empty = show every campaign. See the column comment above.
    explorer_campaign_allowlist: tuple[str, ...] = ()


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
                   overview_pinned_card, consent_sidebar_enabled, primary_kpi,
                   pacing_active_weekdays, segment_filter_profile,
                   sidebar_hidden_tabs, card_layouts,
                   default_date_preset, explorer_campaign_allowlist
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
        consent_sidebar_enabled=bool(row[24]) if row[24] is not None else False,
        primary_kpi=_normalize_kpi_spec(row[25]),
        pacing_active_weekdays=_s(row[26]),
        segment_filter_profile=_s(row[27]),
        sidebar_hidden_tabs=_normalize_hidden_tabs(row[28]),
        card_layouts=_normalize_card_layouts(row[29]),
        default_date_preset=_normalize_date_preset(row[30]),
        explorer_campaign_allowlist=_normalize_campaign_allowlist(row[31]),
    )


def _normalize_date_preset(value: object) -> str | None:
    """Coerce a stored default_date_preset into a known preset id, or None.

    Anything outside DATE_RANGE_PRESETS (stale/garbage) collapses to None so the
    renderer falls back to its built-in default."""
    preset = str(value or "").strip().lower()
    return preset if preset in DATE_RANGE_PRESETS else None


def _normalize_campaign_allowlist(payload: object) -> tuple[str, ...]:
    """Coerce a stored explorer_campaign_allowlist into a clean tuple of names.

    JSONB comes back as a list from psycopg, but tolerate a JSON string too.
    Blanks are dropped and duplicates removed (first occurrence wins); order is
    preserved. Empty result means 'no restriction — show every campaign'."""
    if payload is None:
        return ()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return ()
    if not isinstance(payload, (list, tuple)):
        return ()
    seen: list[str] = []
    for v in payload:
        name = str(v).strip()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def _normalize_kpi_spec(payload: object) -> dict[str, Any] | None:
    """Coerce a stored primary_kpi value into a clean spec dict (or None).

    JSONB comes back as a dict from psycopg, but tolerate a JSON string too.
    A spec is only meaningful with a non-empty ``type``."""
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    kpi_type = str(payload.get("type") or "").strip()
    if not kpi_type:
        return None
    goal_raw = payload.get("goal")
    try:
        goal = float(goal_raw) if goal_raw is not None and goal_raw != "" else None
    except (TypeError, ValueError):
        goal = None
    label = str(payload.get("label") or "").strip() or None
    return {"type": kpi_type, "label": label, "goal": goal}


def _normalize_hidden_tabs(payload: object) -> tuple[str, ...]:
    """Coerce a stored sidebar_hidden_tabs value into a clean tuple of tab keys.

    JSONB comes back as a list from psycopg, but tolerate a JSON string too.
    Filters to SIDEBAR_TOGGLEABLE_TABS (dropping unknown/garbage keys), dedupes
    while preserving the canonical tab order."""
    if payload is None:
        return ()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return ()
    if not isinstance(payload, (list, tuple)):
        return ()
    present = {str(v).strip() for v in payload}
    return tuple(tab for tab in SIDEBAR_TOGGLEABLE_TABS if tab in present)


def _normalize_card_layouts(payload: object) -> dict[str, dict[str, list[str]]]:
    """Coerce a stored card_layouts value into a clean {tab: {order, hidden}} map.

    JSONB comes back as a dict from psycopg, but tolerate a JSON string too.
    Every tab entry is reduced to two string lists — ``order`` and ``hidden`` —
    each deduped (first occurrence wins) with blanks dropped. Card keys are NOT
    validated here (this layer doesn't know a tab's card set); the renderer
    ignores any key that isn't a real card, so a stale key is harmless. Tabs
    with no usable order or hidden list are omitted entirely."""
    if payload is None:
        return {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if not isinstance(payload, dict):
        return {}

    def _clean_keys(values: object) -> list[str]:
        if not isinstance(values, (list, tuple)):
            return []
        seen: list[str] = []
        for v in values:
            key = str(v).strip()
            if key and key not in seen:
                seen.append(key)
        return seen

    out: dict[str, dict[str, list[str]]] = {}
    for tab, spec in payload.items():
        tab_key = str(tab).strip()
        if not tab_key or not isinstance(spec, dict):
            continue
        order = _clean_keys(spec.get("order"))
        hidden = _clean_keys(spec.get("hidden"))
        if order or hidden:
            out[tab_key] = {"order": order, "hidden": hidden}
    return out


def get_card_layout(client_slug: str, tab_key: str) -> dict[str, list[str]]:
    """The admin-authored layout for one tab: ``{"order": [...], "hidden": [...]}``.

    Returns empty lists when the tab has no stored layout. Card keys are raw as
    stored — the caller filters them against that tab's real card set."""
    tab = (tab_key or "").strip()
    row = get_config(client_slug)
    layouts = row.card_layouts if row else {}
    spec = layouts.get(tab) if tab else None
    if not spec:
        return {"order": [], "hidden": []}
    return {"order": list(spec.get("order") or []), "hidden": list(spec.get("hidden") or [])}


def save_card_layout(
    client_slug: str,
    tab_key: str,
    *,
    order: list[str] | tuple[str, ...] | None,
    hidden: list[str] | tuple[str, ...] | None,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Persist one tab's card layout (order + hidden set), merging into the
    stored ``card_layouts`` map without disturbing other tabs' entries.

    ``order`` is the full card order for the tab and ``hidden`` the cards an
    admin has hidden; both are normalized (blanks dropped, deduped). Passing two
    empty lists clears the tab's entry so it falls back to the natural order.
    Server-side + per client, so the layout applies to every user of the
    client's portal in every browser. Touches only the card_layouts column."""
    slug = (client_slug or "").strip().lower()
    tab = (tab_key or "").strip()
    if not slug:
        raise ValueError("client_slug is required.")
    if not tab:
        raise ValueError("tab_key is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    layouts = dict(existing.card_layouts) if existing else {}
    normalized_entry = _normalize_card_layouts({tab: {"order": order or [], "hidden": hidden or []}})
    if tab in normalized_entry:
        layouts[tab] = normalized_entry[tab]
    else:
        layouts.pop(tab, None)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, card_layouts, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              card_layouts = EXCLUDED.card_layouts,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, json.dumps(layouts), now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def save_sidebar_hidden_tabs(
    client_slug: str,
    hidden_tabs: list[str] | tuple[str, ...] | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Persist which client-facing sidebar tabs are hidden for this client.

    ``hidden_tabs`` is the set of tab keys to hide (any subset of
    SIDEBAR_TOGGLEABLE_TABS); unknown keys are dropped. Server-side + per client,
    so the choice applies to every user of the client's portal in every browser.
    Touches only that column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    present = {str(v).strip() for v in (hidden_tabs or [])}
    normalized = [tab for tab in SIDEBAR_TOGGLEABLE_TABS if tab in present]
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, sidebar_hidden_tabs, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              sidebar_hidden_tabs = EXCLUDED.sidebar_hidden_tabs,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, json.dumps(normalized), now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def get_sidebar_hidden_tabs(client_slug: str) -> tuple[str, ...]:
    """The client-facing sidebar tabs an admin has hidden (empty if none/unset)."""
    row = get_config(client_slug)
    return row.sidebar_hidden_tabs if row else ()


def set_label(client_slug: str, label: str, *, updated_by: str | None = None) -> bool:
    """Rename a client's display label, touching only the label column.

    Accounts, layouts, budgets and every other setting are left untouched. This
    deliberately does NOT create a row when none exists — ``dashboard_clients`` is
    the source of truth for a client's name, and this only keeps a pre-existing
    config row's label (which ``client_label()`` and the dashboard header read)
    in step. Returns True when an existing row was updated."""
    slug = (client_slug or "").strip().lower()
    name = (label or "").strip()
    if not slug:
        raise ValueError("client_slug is required.")
    if not name:
        raise ValueError("label is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE client_dashboard_config
            SET label = %s, updated_at = %s, updated_by = %s
            WHERE client_slug = %s
            """,
            (name, now, (updated_by or "").strip() or None, slug),
        )
        return cur.rowcount > 0


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


def save_default_date_preset(
    client_slug: str,
    preset: str | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Set (or clear, with None/empty) the date-range preset the dashboard lands
    on for this client. ``preset`` must be one of DATE_RANGE_PRESETS or empty.

    Server-side + per client, so the landing range applies to every user of the
    client's portal in every browser. Touches only that column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    normalized = (preset or "").strip().lower() or None
    if normalized is not None and normalized not in DATE_RANGE_PRESETS:
        raise ValueError(
            f"default_date_preset must be one of {DATE_RANGE_PRESETS} or empty."
        )
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, default_date_preset, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              default_date_preset = EXCLUDED.default_date_preset,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, normalized, now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def save_explorer_campaign_allowlist(
    client_slug: str,
    campaigns: list[str] | tuple[str, ...] | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Set (or clear, with an empty list) the Campaign Explorer allowlist — the
    campaign names the portal's client is allowed to see. Empty = show every
    campaign. Names are normalized (blanks dropped, deduped). Touches only that
    column."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    normalized = list(_normalize_campaign_allowlist(list(campaigns or [])))
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, explorer_campaign_allowlist, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              explorer_campaign_allowlist = EXCLUDED.explorer_campaign_allowlist,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, json.dumps(normalized), now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


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


def save_consent_sidebar_enabled(
    client_slug: str,
    show: bool,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Toggle whether Consent & Tracking Health shows in the client sidebar."""
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
              client_slug, label, consent_sidebar_enabled, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              consent_sidebar_enabled = EXCLUDED.consent_sidebar_enabled,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, bool(show), now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


SEGMENT_FILTER_PROFILES: tuple[str, ...] = ("business_lines", "regions")


def save_segment_filter_profile(
    client_slug: str,
    profile: str | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Set how the campaign/segment filters classify this client's data.

    ``profile`` must be one of SEGMENT_FILTER_PROFILES or None (no segment
    filters). Empty string is treated as None.
    """
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    normalized = (profile or "").strip().lower() or None
    if normalized is not None and normalized not in SEGMENT_FILTER_PROFILES:
        raise ValueError(
            f"segment_filter_profile must be one of {SEGMENT_FILTER_PROFILES} or empty."
        )
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, segment_filter_profile, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              segment_filter_profile = EXCLUDED.segment_filter_profile,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, normalized, now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def backfill_segment_filter_profile(client_slug: str, profile: str) -> bool:
    """One-time seed: set a client's segment_filter_profile only if currently NULL.

    Clients that predate the segment_filter_profile column had their filter type
    inferred from slug/label at runtime. This seeds those known clients so removing
    that inference keeps their filters working; it never overwrites an existing
    value (admin choices win). Returns True if a row was updated.
    """
    slug = (client_slug or "").strip().lower()
    if not slug or not enabled():
        return False
    if profile not in SEGMENT_FILTER_PROFILES:
        raise ValueError(
            f"segment_filter_profile must be one of {SEGMENT_FILTER_PROFILES}."
        )
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE client_dashboard_config
               SET segment_filter_profile = %s, updated_at = %s
             WHERE client_slug = %s AND segment_filter_profile IS NULL
            """,
            (profile, now, slug),
        )
    return bool(getattr(cur, "rowcount", 0))


def backfill_marketing_mart_destination(
    client_slug: str, project_id: str, dataset_id: str
) -> bool:
    """One-time seed: set a client's BigQuery mart project/dataset only where NULL.

    Nixon's mart destination historically lived in marketing_service module
    defaults rather than on its config row, so HQ/agency code carried a
    client-name fallback. Seeding the row here makes the config self-describing so
    that fallback can be removed. Never overwrites existing values. Returns True
    if a row was updated.
    """
    slug = (client_slug or "").strip().lower()
    proj = (project_id or "").strip()
    ds = (dataset_id or "").strip()
    if not slug or not proj or not ds or not enabled():
        return False
    ensure_schema()
    now = datetime.now(tz=UTC)
    with db.connection() as conn:
        cur = conn.execute(
            """
            UPDATE client_dashboard_config
               SET gcp_project_id = COALESCE(gcp_project_id, %s),
                   bq_mart_dataset_id = COALESCE(bq_mart_dataset_id, %s),
                   updated_at = %s
             WHERE client_slug = %s
               AND (gcp_project_id IS NULL OR bq_mart_dataset_id IS NULL)
            """,
            (proj, ds, now, slug),
        )
    return bool(getattr(cur, "rowcount", 0))


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


def normalize_active_weekdays_csv(raw: str | None) -> str | None:
    """Coerce free-form input into a clean ISO-weekday CSV, or None for auto.

    Accepts "1,2,3,4,5" style input; ignores blanks and out-of-range values;
    dedupes and sorts. Returns None when nothing valid is left (auto-detect)."""
    if raw is None:
        return None
    days: set[int] = set()
    for part in str(raw).replace(" ", "").split(","):
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            raise ValueError("Active days must be numbers 1 (Mon) through 7 (Sun).")
        if 1 <= n <= 7:
            days.add(n)
    if not days:
        return None
    return ",".join(str(d) for d in sorted(days))


def parse_active_weekdays(value: str | None) -> list[int]:
    """Parse a stored ISO-weekday CSV into a sorted list (empty for auto)."""
    csv = normalize_active_weekdays_csv(value) if value else None
    return [int(d) for d in csv.split(",")] if csv else []


def save_pacing_active_weekdays(
    client_slug: str,
    active_weekdays_csv: str | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Set (or clear, with None) a client's active-ad-days override for pacing."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")
    normalized = normalize_active_weekdays_csv(active_weekdays_csv)
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, pacing_active_weekdays, updated_at, updated_by
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              pacing_active_weekdays = EXCLUDED.pacing_active_weekdays,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, normalized, now, (updated_by or "").strip() or None),
        )
    saved = get_config(slug)
    if not saved:
        raise RuntimeError("Failed to load saved client config.")
    return saved


def get_primary_kpi(client_slug: str) -> dict[str, Any] | None:
    """The client's headline KPI spec ({type,label,goal}) or None if unset."""
    row = get_config(client_slug)
    return row.primary_kpi if row else None


def save_primary_kpi(
    client_slug: str,
    spec: dict[str, Any] | None,
    *,
    updated_by: str | None = None,
) -> ClientConfigRow:
    """Persist (or clear) the client's headline KPI spec. Touches only that column.

    Passing None — or a spec with an empty ``type`` — clears the KPI. The spec is
    normalized before storage so the HQ view always reads a clean shape."""
    slug = (client_slug or "").strip().lower()
    if not slug:
        raise ValueError("client_slug is required.")
    if not enabled():
        raise RuntimeError("DATABASE_URL is required to save client dashboard config.")

    normalized = _normalize_kpi_spec(spec) if spec else None
    ensure_schema()
    now = datetime.now(tz=UTC)
    existing = get_config(slug)
    label = existing.label if existing else slug
    payload = json.dumps(normalized) if normalized is not None else None
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO client_dashboard_config (
              client_slug, label, primary_kpi, updated_at, updated_by
            )
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (client_slug)
            DO UPDATE SET
              primary_kpi = EXCLUDED.primary_kpi,
              updated_at = EXCLUDED.updated_at,
              updated_by = EXCLUDED.updated_by
            """,
            (slug, label, payload, now, (updated_by or "").strip() or None),
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
                   gcp_project_id, bq_mart_dataset_id, dashboard_mode, primary_kpi
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
            "primary_kpi": _normalize_kpi_spec(r[6]),
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
