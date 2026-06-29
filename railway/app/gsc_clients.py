"""Resolve GSC -> BigQuery destinations per client.

GA4 already solved multi-tenant BQ routing with a GA4_CLIENTS registry
(see ga4_clients.py). GSC never got the same treatment — it was built
Penn-only, with credentials, project, and dataset all hardcoded as global
env vars. This module gives GSC the same per-client routing GA4 already
has, keyed directly by the dashboard's client_slug (no extra indirection
needed, unlike GA4's separate ga4_client_key).

Clients not present in the registry fall back to the legacy Penn-only
global env vars (GCP_CREDS_PENN_BASE64 / GSC_BQ_PROJECT_ID /
BQ_MART_DATASET_ID), so Penn keeps working with zero config changes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_CREDENTIALS_ENV = "GCP_CREDS_PENN_BASE64"


@dataclass(frozen=True)
class GscClientTarget:
    """BigQuery destination (+ optional site_url override) for one client."""

    client_slug: str
    bq_project_id: str
    bq_dataset_id: str
    credentials_env: str
    site_url: str | None = None
    label: str | None = None
    # Dataset for Google's native GSC -> BQ bulk export (set up per-property
    # directly in the Search Console UI, separate from our API backfill).
    # Assumed to live in the same bq_project_id unless this differs.
    native_dataset_id: str | None = None
    is_default_fallback: bool = False


def _strip_env(val: str | None) -> str:
    if not val:
        return ""
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1].strip()
    return v


def _load_from_env() -> dict[str, GscClientTarget]:
    """Load GSC client registry from the GSC_CLIENTS env var only."""
    raw = _strip_env(os.getenv("GSC_CLIENTS"))
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GSC_CLIENTS is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("GSC_CLIENTS must be a JSON object keyed by client slug.")

    out: dict[str, GscClientTarget] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        slug = str(key).strip().lower()
        project = _strip_env(str(entry.get("bq_project_id") or entry.get("project") or ""))
        dataset = _strip_env(str(entry.get("bq_dataset_id") or entry.get("dataset") or ""))
        if not project:
            raise RuntimeError(f"GSC_CLIENTS.{slug}.bq_project_id is required.")
        if not dataset:
            raise RuntimeError(f"GSC_CLIENTS.{slug}.bq_dataset_id is required.")
        out[slug] = GscClientTarget(
            client_slug=slug,
            bq_project_id=project,
            bq_dataset_id=dataset,
            credentials_env=_strip_env(str(entry.get("credentials_env") or "")) or _DEFAULT_CREDENTIALS_ENV,
            site_url=_strip_env(str(entry.get("site_url") or "")) or None,
            label=_strip_env(str(entry.get("label") or "")) or slug,
            native_dataset_id=_strip_env(str(entry.get("native_dataset_id") or "")) or None,
        )
    return out


def load_client_registry() -> dict[str, GscClientTarget]:
    """
    Load GSC client registry, merging env var and database entries.
    Database entries (set via Admin → Client BQ Registry) take precedence.

    Example GSC_CLIENTS env var:
    {
      "penn-bq-test": {
        "bq_project_id": "penn-community-b-1699391543298",
        "bq_dataset_id": "marketing_marts",
        "credentials_env": "GCP_CREDS_PENN_BASE64"
      }
    }

    site_url is optional here -- normally stored per-client in the dashboard
    settings DB (client_dashboard_config.gsc_site_url).
    """
    out = _load_from_env()
    try:
        import client_registry_store
        for row in client_registry_store.list_gsc_configs():
            slug = row.client_slug
            out[slug] = GscClientTarget(
                client_slug=slug,
                bq_project_id=row.bq_project_id,
                bq_dataset_id=row.bq_dataset_id,
                credentials_env=row.credentials_env or _DEFAULT_CREDENTIALS_ENV,
                native_dataset_id=row.native_dataset_id,
                label=row.label or slug,
            )
    except Exception:
        pass
    return out


def default_target(client_slug: str | None = None) -> GscClientTarget:
    """Legacy Penn-only fallback -- unchanged behaviour for clients not in GSC_CLIENTS."""
    return GscClientTarget(
        client_slug=client_slug or "default",
        bq_project_id=_strip_env(os.getenv("GSC_BQ_PROJECT_ID")) or _DEFAULT_PROJECT,
        bq_dataset_id=_strip_env(os.getenv("BQ_MART_DATASET_ID")) or _DEFAULT_MART_DATASET,
        credentials_env=(
            _DEFAULT_CREDENTIALS_ENV
            if _strip_env(os.getenv("GCP_CREDS_PENN_BASE64"))
            else "GCP_SERVICE_ACCOUNT_JSON"
        ),
        is_default_fallback=True,
    )


def _client_creds_env(slug: str) -> str | None:
    """The client's BigQuery credentials env (same one its GA4/ads data uses)."""
    try:
        import client_config
        import ga4_clients
        ck = client_config.load_client_config(slug).ga4_client_key or slug
        return ga4_clients.resolve_client_config(client_key=ck).credentials_env or None
    except Exception:
        return None


def _connector_target(slug: str, base: GscClientTarget) -> GscClientTarget | None:
    """Build a target from the per-client GSC connector config, if one exists.

    The connector wizard is the source of truth for project + raw dataset (so GSC
    lands in `raw_gsc` like every other connector, not the separate GSC registry's
    dataset). Credentials reuse the client's BQ creds (via ga4_clients), falling
    back to the registry/default target's creds. Returns None when the client has
    no GSC connector configured, so Penn/registry clients are unaffected.
    """
    try:
        import connector_config_store
        cfg = connector_config_store.get_config(slug, "gsc")
    except Exception:
        cfg = None
    if not cfg or not (cfg.bq_project_id or "").strip() or not (cfg.raw_dataset_id or "").strip():
        return None
    return GscClientTarget(
        client_slug=slug,
        bq_project_id=cfg.bq_project_id.strip(),
        bq_dataset_id=cfg.raw_dataset_id.strip(),  # e.g. "raw_gsc"
        credentials_env=_client_creds_env(slug) or base.credentials_env,
        site_url=(cfg.source_account_id or "").strip() or base.site_url,
        label=base.label or slug,
        native_dataset_id=base.native_dataset_id,
        is_default_fallback=False,
    )


def resolve_target(client_slug: str) -> GscClientTarget:
    """GSC -> BigQuery destination for this client.

    Precedence: per-client GSC connector config (writes to raw_gsc in the client's
    project, consistent with the other connectors) > GSC registry entry > legacy
    Penn default. Both the sync (write) and bq_gsc_service (read) paths resolve
    through here, so the two always agree.
    """
    slug = (client_slug or "").strip().lower()
    registry = load_client_registry()
    base = registry.get(slug) or default_target(slug)
    conn = _connector_target(slug, base)
    if conn is not None:
        return conn
    return base if slug in registry else default_target(slug)


def list_clients_public() -> list[dict[str, Any]]:
    """Safe client list (no secrets) for admin/debug surfaces."""
    registry = load_client_registry()
    return [
        {
            "client_slug": t.client_slug,
            "label": t.label,
            "bq_project_id": t.bq_project_id,
            "bq_dataset_id": t.bq_dataset_id,
            "credentials_env": t.credentials_env,
        }
        for t in sorted(registry.values(), key=lambda t: (t.label or t.client_slug).lower())
    ]
