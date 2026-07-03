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
    """Legacy Penn-only fallback -- unchanged behaviour for Penn itself.

    Only ever returned for Penn (client_slug in (None, "", "penn")). Any other
    slug with no GSC connector and no registry entry means GSC genuinely isn't
    configured for that client yet -- returning Penn's real project/dataset
    here would silently show Penn's Search Console data on someone else's
    dashboard, so that case raises instead.
    """
    slug = (client_slug or "").strip().lower()
    if slug not in ("", "penn"):
        raise RuntimeError(
            f"GSC is not configured for client '{slug}' (no connector, no registry entry). "
            "Refusing to fall back to Penn's BigQuery project."
        )
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


def _ga4_bq(slug: str) -> tuple[str | None, str | None]:
    """The client's BigQuery project + credentials env (where its GA4/ads data lives)."""
    try:
        import client_config
        import ga4_clients
        ck = client_config.load_client_config(slug).ga4_client_key or slug
        t = ga4_clients.resolve_client_config(client_key=ck)
        return (t.bq_project_id or "").strip() or None, (t.credentials_env or "").strip() or None
    except Exception:
        return None, None


def _client_creds_env(slug: str) -> str | None:
    return _ga4_bq(slug)[1]


def _connector_target(slug: str, base: GscClientTarget) -> GscClientTarget | None:
    """Build a target from the per-client GSC connector config, if one exists.

    A GSC connector means this client manages Search Console through the wizard,
    so route to the client's OWN BigQuery project — never the Penn default. The
    project comes from the connector config (cfg.bq_project_id) and, when that
    wasn't filled in, falls back to the client's GA4 BQ project via ga4_clients
    (the same project its other connector data lands in). Dataset defaults to
    raw_gsc. Returns None only when the client has no GSC connector at all, so
    Penn/registry clients are completely unaffected.
    """
    try:
        import connector_config_store
        cfg = connector_config_store.get_config(slug, "gsc")
    except Exception:
        cfg = None
    if not cfg:
        return None  # no GSC connector → leave registry/Penn behaviour untouched

    ga4_project, ga4_creds = _ga4_bq(slug)
    project = (cfg.bq_project_id or "").strip() or ga4_project
    if not project:
        # A GSC connector exists but we can't resolve the client's project — refuse
        # to silently fall back to Penn; let the caller surface "not configured".
        return None
    dataset = (cfg.raw_dataset_id or "").strip() or "raw_gsc"
    return GscClientTarget(
        client_slug=slug,
        bq_project_id=project,
        bq_dataset_id=dataset,  # e.g. "raw_gsc"
        credentials_env=ga4_creds or base.credentials_env,
        site_url=(cfg.source_account_id or "").strip() or base.site_url,
        label=base.label or slug,
        native_dataset_id=base.native_dataset_id,
        is_default_fallback=False,
    )


def _neutral_base(slug: str) -> GscClientTarget:
    """Placeholder target with no real project -- used only to supply optional
    defaults (credentials_env, label, ...) while building a connector-derived
    target for a slug that isn't Penn and isn't in the registry. Deliberately
    NOT default_target(slug): that raises for non-Penn slugs, and calling it
    here (before we've even checked for a connector config) would break the
    connector > registry > Penn-default precedence by raising too early.
    """
    return GscClientTarget(
        client_slug=slug,
        bq_project_id="",
        bq_dataset_id="",
        credentials_env="GCP_SERVICE_ACCOUNT_JSON",
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
    base = registry.get(slug) or _neutral_base(slug)
    conn = _connector_target(slug, base)
    if conn is not None:
        return conn
    if slug in registry:
        return base
    return default_target(slug)


def target_from_config(client_slug: str, cfg: Any) -> GscClientTarget:
    """Build a GSC target directly from a connector config object.

    The connector already holds its config, so it can route explicitly instead of
    relying on resolve_target re-fetching it. Project = cfg.bq_project_id, else the
    client's GA4 BQ project (ga4_clients), else the registry base — but NEVER the
    Penn default for a real client. Dataset defaults to raw_gsc.
    """
    slug = (client_slug or "").strip().lower()
    base = load_client_registry().get(slug) or _neutral_base(slug)
    ga4_project, ga4_creds = _ga4_bq(slug)
    project = (
        (getattr(cfg, "bq_project_id", "") or "").strip()
        or ga4_project
        or (base.bq_project_id if not base.is_default_fallback else "")
        or ga4_project
        or base.bq_project_id
    )
    dataset = (getattr(cfg, "raw_dataset_id", "") or "").strip() or "raw_gsc"
    return GscClientTarget(
        client_slug=slug,
        bq_project_id=project,
        bq_dataset_id=dataset,
        credentials_env=ga4_creds or base.credentials_env,
        site_url=(getattr(cfg, "source_account_id", "") or "").strip() or base.site_url,
        label=base.label or slug,
        native_dataset_id=base.native_dataset_id,
        is_default_fallback=False,
    )


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
