"""Resolve GA4 BigQuery targets across multiple GCP projects."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from ga4_credentials import GLOBAL_GCP_CREDENTIALS_ENV, load_service_account_info_from_env


@dataclass(frozen=True)
class Ga4ClientTarget:
    """BigQuery location for one GA4 property."""

    account_id: str
    bq_project_id: str
    bq_dataset_id: str
    client_key: str | None = None
    label: str | None = None
    credentials_env: str | None = None


@dataclass(frozen=True)
class ResolvedGa4Client:
    """Selected GA4 target plus decoded server-side credentials."""

    label: str | None
    bq_project_id: str
    bq_dataset_id: str
    credentials: dict[str, Any] = field(repr=False)
    client_key: str | None = None
    account_id: str | None = None
    credentials_env: str | None = field(default=None, repr=False)


def _strip_env(val: str | None) -> str:
    if not val:
        return ""
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1].strip()
    return v


def _account_id_from_dataset(dataset: str) -> str:
    ds = dataset.strip()
    if ds.startswith("analytics_"):
        return ds.replace("analytics_", "", 1)
    return ds


def _load_from_env() -> dict[str, Ga4ClientTarget]:
    """Load GA4 client registry from the GA4_CLIENTS env var only."""
    raw = _strip_env(os.getenv("GA4_CLIENTS"))
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GA4_CLIENTS is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("GA4_CLIENTS must be a JSON object keyed by client slug.")

    out: dict[str, Ga4ClientTarget] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        slug = str(key).strip().lower()
        project = _strip_env(str(entry.get("bq_project_id") or entry.get("project") or ""))
        dataset = _strip_env(str(entry.get("bq_dataset_id") or entry.get("dataset") or ""))
        if not project:
            raise RuntimeError(f"GA4_CLIENTS.{slug}.bq_project_id is required.")
        if not dataset:
            raise RuntimeError(f"GA4_CLIENTS.{slug}.bq_dataset_id is required.")
        account_id = _strip_env(str(entry.get("account_id") or "")) or _account_id_from_dataset(dataset)
        out[slug] = Ga4ClientTarget(
            client_key=slug,
            label=_strip_env(str(entry.get("label") or "")) or slug,
            bq_project_id=project,
            bq_dataset_id=dataset,
            account_id=account_id,
            credentials_env=_strip_env(str(entry.get("credentials_env") or "")) or None,
        )
    return out


def load_client_registry() -> dict[str, Ga4ClientTarget]:
    """
    Load GA4 client registry, merging env var and database entries.
    Database entries (set via Admin → Client BQ Registry) take precedence.

    Example GA4_CLIENTS env var:
    {
      "penn": {
        "bq_project_id": "penn-community-b-1699391543298",
        "bq_dataset_id": "analytics_313855909",
        "account_id": "313855909",
        "label": "Penn Community Bank"
      }
    }
    """
    out = _load_from_env()
    try:
        import client_registry_store
        for row in client_registry_store.list_ga4_configs():
            slug = row.client_slug
            out[slug] = Ga4ClientTarget(
                client_key=slug,
                label=row.label or slug,
                bq_project_id=row.bq_project_id,
                bq_dataset_id=row.bq_dataset_id,
                account_id=_account_id_from_dataset(row.bq_dataset_id),
                credentials_env=row.credentials_env or None,
            )
    except Exception:
        pass
    return out


def default_target() -> Ga4ClientTarget:
    project = _strip_env(os.getenv("BQ_PROJECT_ID"))
    dataset = _strip_env(os.getenv("BQ_DATASET_ID"))
    if not project or not dataset:
        raise RuntimeError(
            "Set BQ_PROJECT_ID and BQ_DATASET_ID, or pass client_key / bq_project_id + bq_dataset_id."
        )
    explicit = _strip_env(os.getenv("GA4_PROPERTY_ID"))
    account_id = explicit.replace("properties/", "").split("/")[-1] if explicit else _account_id_from_dataset(dataset)
    return Ga4ClientTarget(
        client_key=None,
        label="default",
        bq_project_id=project,
        bq_dataset_id=dataset,
        account_id=account_id,
    )


def resolve_target(
    *,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    account_id: str | None = None,
) -> Ga4ClientTarget:
    """Pick GA4 BigQuery target: registry slug > explicit ids > Railway default env."""
    registry = load_client_registry()
    key = str(client_key or "").strip().lower()
    if key:
        if key not in registry:
            known = ", ".join(sorted(registry.keys())) or "(none — set GA4_CLIENTS)"
            raise RuntimeError(f"Unknown client_key '{client_key}'. Configured keys: {known}")
        return registry[key]

    project = _strip_env(bq_project_id)
    dataset = _strip_env(bq_dataset_id)
    if project or dataset:
        if not project:
            raise RuntimeError("Missing bq_project_id for GA4 BigQuery target.")
        if not dataset:
            raise RuntimeError("Missing bq_dataset_id for GA4 BigQuery target.")
        acct = _strip_env(account_id) or _account_id_from_dataset(dataset)
        return Ga4ClientTarget(
            client_key=None,
            label=project,
            bq_project_id=project,
            bq_dataset_id=dataset,
            account_id=acct,
        )

    return default_target()


def list_clients_public() -> list[dict[str, Any]]:
    """Safe client list for /ga4/clients (no secrets)."""
    registry = load_client_registry()
    if registry:
        return [
            {
                "client_key": t.client_key,
                "label": t.label,
                "bq_project_id": t.bq_project_id,
                "bq_dataset_id": t.bq_dataset_id,
                "account_id": t.account_id,
            }
            for t in registry.values()
        ]
    try:
        t = default_target()
        return [
            {
                "client_key": "default",
                "label": t.label,
                "bq_project_id": t.bq_project_id,
                "bq_dataset_id": t.bq_dataset_id,
                "account_id": t.account_id,
            }
        ]
    except RuntimeError:
        return []


def resolve_client_config(
    *,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    account_id: str | None = None,
) -> ResolvedGa4Client:
    """Resolve the selected GA4 client and decode its server-side BigQuery credentials."""
    target = resolve_target(
        client_key=client_key,
        bq_project_id=bq_project_id,
        bq_dataset_id=bq_dataset_id,
        account_id=account_id,
    )
    raw_env_var = (target.credentials_env or "").strip()
    env_var = raw_env_var or GLOBAL_GCP_CREDENTIALS_ENV
    credentials = load_service_account_info_from_env(
        env_var,
        # Only client-specific vars are base64 -- the shared global default
        # (GCP_SERVICE_ACCOUNT_JSON) is plain JSON even when a caller resolves
        # it explicitly. See bigquery_service.build_client() for the same fix.
        require_base64=(env_var != GLOBAL_GCP_CREDENTIALS_ENV),
    )
    return ResolvedGa4Client(
        label=target.label,
        bq_project_id=target.bq_project_id,
        bq_dataset_id=target.bq_dataset_id,
        credentials=credentials,
        client_key=target.client_key,
        account_id=target.account_id,
        credentials_env=raw_env_var or None,
    )
