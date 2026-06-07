"""Client dashboard registry and config loading."""

from __future__ import annotations

import json
import os
from typing import Any

from penn_config import PennDashboardConfig, load_penn_config

# Built-in clients besides Penn (account IDs optional — set in Settings or DASHBOARD_CLIENTS).
_BUILTIN_CLIENTS: dict[str, dict[str, Any]] = {
    "demo": {
        "label": "Demo Client",
        "google_customer_id": "",
        "linkedin_account_id": "",
        "meta_account_id": "",
        "ga4_client_key": "",
    },
}


def _strip_env(val: str | None) -> str:
    if not val:
        return ""
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1].strip()
    return v


def _load_registry_from_env() -> dict[str, dict[str, Any]]:
    raw = _strip_env(os.getenv("DASHBOARD_CLIENTS"))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DASHBOARD_CLIENTS is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("DASHBOARD_CLIENTS must be a JSON object keyed by client slug.")
    out: dict[str, dict[str, Any]] = {}
    for key, entry in data.items():
        slug = str(key).strip().lower()
        if slug and isinstance(entry, dict):
            out[slug] = entry
    return out


def list_client_slugs() -> list[str]:
    """Known dashboard client slugs (env registry + built-in clients)."""
    registry = _load_registry_from_env()
    slugs = set(registry.keys())
    slugs.update(_BUILTIN_CLIENTS.keys())
    slugs.add("penn")
    return sorted(slugs)


def list_dashboard_clients() -> list[tuple[str, str]]:
    """(slug, label) pairs for admin dashboard links."""
    slugs = list_client_slugs()
    labels = _labels_for_slugs(slugs)
    return [(slug, labels.get(slug) or _default_label(slug)) for slug in slugs]


def _labels_for_slugs(slugs: list[str]) -> dict[str, str]:
    """Batch-load display labels from Postgres when available."""
    try:
        import client_dashboard_config as cdc

        if not cdc.enabled():
            return {}
        rows = cdc.list_config_labels()
        return {slug: label for slug, label in rows.items() if label}
    except Exception:
        return {}


def _get_db_row(slug: str):
    try:
        import client_dashboard_config as cdc

        return cdc.get_config(slug)
    except Exception:
        return None


def _default_label(slug: str) -> str:
    if slug in _BUILTIN_CLIENTS:
        label = _strip_env(str(_BUILTIN_CLIENTS[slug].get("label") or ""))
        if label:
            return label
    registry = _load_registry_from_env()
    if slug in registry:
        label = _strip_env(str(registry[slug].get("label") or ""))
        if label:
            return label
    if slug == "penn":
        return load_penn_config().label
    return slug.replace("-", " ").title()


def client_label(slug: str) -> str:
    slug = slug.strip().lower()
    row = _get_db_row(slug)
    if row and row.label:
        return row.label
    return _default_label(slug)


def load_client_config(client_slug: str) -> PennDashboardConfig:
    """Load merged config for a client slug (env defaults + DB overrides)."""
    slug = (client_slug or "").strip().lower()
    known = list_client_slugs()
    if slug not in known:
        raise ValueError(f"Unknown client '{client_slug}'. Known clients: {', '.join(known)}")

    if slug == "penn":
        return load_penn_config()

    registry = _load_registry_from_env()
    entry: dict[str, Any] = dict(registry.get(slug) or _BUILTIN_CLIENTS.get(slug) or {})
    google = _strip_env(str(entry.get("google_customer_id") or "")).replace("-", "")
    linkedin = _strip_env(str(entry.get("linkedin_account_id") or ""))
    meta = _strip_env(str(entry.get("meta_account_id") or ""))
    ga4_key = _strip_env(str(entry.get("ga4_client_key") or "")) or (slug if slug in _BUILTIN_CLIENTS else "")
    label = _strip_env(str(entry.get("label") or "")) or _default_label(slug)

    row = _get_db_row(slug)
    if row:
        if row.label:
            label = row.label
        if row.google_customer_id:
            google = row.google_customer_id.replace("-", "")
        if row.linkedin_account_id:
            linkedin = row.linkedin_account_id
        if row.meta_account_id:
            meta = row.meta_account_id
        if row.ga4_client_key:
            ga4_key = row.ga4_client_key

    if slug not in _BUILTIN_CLIENTS and not any((google, linkedin, meta, ga4_key)):
        raise RuntimeError(
            f"Client '{slug}' is not configured. Set DASHBOARD_CLIENTS or save account IDs in settings."
        )

    return PennDashboardConfig(
        client_key=slug,
        label=label,
        google_customer_id=google or None,
        linkedin_account_id=linkedin or None,
        meta_account_id=meta or None,
        ga4_client_key=ga4_key or slug,
    )
