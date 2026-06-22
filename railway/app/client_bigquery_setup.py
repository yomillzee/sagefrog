"""Provision and validate the BigQuery datasets used by one dashboard client."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


def dataset_ids() -> dict[str, str]:
    return {
        "google": (os.getenv("BQ_GOOGLE_DATASET_ID") or "raw_google_ads").strip(),
        "linkedin": (os.getenv("BQ_LINKEDIN_DATASET_ID") or "raw_linkedin_ads").strip(),
        "meta": (os.getenv("BQ_META_DATASET_ID") or "raw_meta_ads").strip(),
        "mart": (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip(),
    }


def _ensure_writable_dataset(
    client: Any,
    *,
    project_id: str,
    dataset_id: str,
    location: str | None,
) -> str:
    from google.cloud import bigquery

    full_dataset_id = f"{project_id}.{dataset_id}"
    dataset = bigquery.Dataset(full_dataset_id)
    if location:
        dataset.location = location
    client.create_dataset(dataset, exists_ok=True)
    existing = client.get_dataset(full_dataset_id)
    existing_location = str(getattr(existing, "location", "") or "").strip()
    if location and existing_location and existing_location.casefold() != location.casefold():
        raise RuntimeError(
            f"Dataset {full_dataset_id} is in {existing_location}, but the client's GA4 "
            f"dataset is in {location}. BigQuery cannot join across locations."
        )

    check_table_id = f"{full_dataset_id}._sagefrog_permission_check_{uuid4().hex}"
    check_table = bigquery.Table(
        check_table_id,
        schema=[bigquery.SchemaField("ok", "BOOL", mode="NULLABLE")],
    )
    check_table.expires = datetime.now(tz=UTC) + timedelta(minutes=10)
    try:
        client.create_table(check_table)
    finally:
        client.delete_table(check_table_id, not_found_ok=True)
    return full_dataset_id


def ensure_client_bq_resources(
    *,
    client_key: str,
    needs_google: bool,
    needs_linkedin: bool,
    needs_meta: bool,
    needs_mart: bool,
) -> dict[str, Any]:
    """Idempotently provision app-owned datasets and validate client access.

    The Google raw dataset belongs to BigQuery Data Transfer Service.  It is
    deliberately verified elsewhere and is never created here, because an
    empty app-created dataset would falsely imply that the transfer exists.
    """
    import bigquery_service
    import ga4_clients

    key = str(client_key or "").strip().lower()
    if not key:
        raise RuntimeError("Select a GA4 client key before configuring BigQuery.")

    resolved = ga4_clients.resolve_client_config(client_key=key)
    project_id = resolved.bq_project_id
    client = bigquery_service.build_client(
        project_id,
        credentials_info=resolved.credentials,
    )

    # A successful SELECT verifies that this identity can create query jobs.
    list(client.query("SELECT 1 AS ok", job_config=bigquery_service.make_job_config()).result(max_results=1))

    location: str | None = None
    try:
        ga4_dataset = client.get_dataset(f"{project_id}.{resolved.bq_dataset_id}")
        location = str(getattr(ga4_dataset, "location", "") or "").strip() or None
    except Exception as exc:
        raise RuntimeError(
            f"Cannot read GA4 dataset {project_id}.{resolved.bq_dataset_id}: {exc}"
        ) from exc

    ids = dataset_ids()
    requested: list[str] = []
    if needs_linkedin:
        requested.append(ids["linkedin"])
    if needs_meta:
        requested.append(ids["meta"])
    if needs_mart:
        requested.append(ids["mart"])

    ready = [
        _ensure_writable_dataset(
            client,
            project_id=project_id,
            dataset_id=dataset_id,
            location=location,
        )
        for dataset_id in dict.fromkeys(requested)
    ]
    return {
        "ok": True,
        "client_key": key,
        "project_id": project_id,
        "credentials_env": resolved.credentials_env,
        "service_account_email": str(resolved.credentials.get("client_email") or ""),
        "location": location,
        "datasets": ready,
        "native_sources": {
            "google": {
                "required": bool(needs_google),
                "dataset": f"{project_id}.{ids['google']}",
                "managed_by": "bigquery_data_transfer_service",
            },
            "ga4": {
                "required": True,
                "dataset": f"{project_id}.{resolved.bq_dataset_id}",
                "managed_by": "google_analytics_export",
            },
        },
    }


def ensure_client_bigquery(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible alias for callers deployed before the refactor."""
    return ensure_client_bq_resources(**kwargs)
