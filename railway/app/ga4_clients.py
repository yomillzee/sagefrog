"""Resolve GA4 BigQuery targets across multiple GCP projects."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ga4ClientTarget:
    """BigQuery location for one GA4 property."""

    account_id: str
    bq_project_id: str
    bq_dataset_id: str
    client_key: str | None = None
    label: str | None = None


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


def load_client_registry() -> dict[str, Ga4ClientTarget]:
    """
    Optional Railway env GA4_CLIENTS — JSON object keyed by client slug.

    Example:
    {
      "penn": {
        "bq_project_id": "penn-community-b-1699391543298",
        "bq_dataset_id": "analytics_313855909",
        "account_id": "313855909",
        "label": "Penn Community Bank"
      },
      "sagefrog": {
        "bq_project_id": "sagefrog",
        "bq_dataset_id": "analytics_123456789"
      }
    }
    """
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
        if not project or not dataset:
            continue
        account_id = _strip_env(str(entry.get("account_id") or "")) or _account_id_from_dataset(dataset)
        out[slug] = Ga4ClientTarget(
            client_key=slug,
            label=_strip_env(str(entry.get("label") or "")) or slug,
            bq_project_id=project,
            bq_dataset_id=dataset,
            account_id=account_id,
        )
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
    if project and dataset:
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
