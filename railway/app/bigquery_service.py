from __future__ import annotations

import json
import os
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account


def _get_required_env(key: str) -> str:
    value = (os.getenv(key) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _load_service_account_info() -> dict[str, Any]:
    raw = _get_required_env("GCP_SERVICE_ACCOUNT_JSON")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GCP_SERVICE_ACCOUNT_JSON is not valid JSON") from exc


def build_client() -> bigquery.Client:
    project_id = _get_required_env("BQ_PROJECT_ID")
    info = _load_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=project_id, credentials=creds)


def env_summary() -> dict[str, Any]:
    return {
        "has_gcp_service_account_json": bool((os.getenv("GCP_SERVICE_ACCOUNT_JSON") or "").strip()),
        "has_bq_project_id": bool((os.getenv("BQ_PROJECT_ID") or "").strip()),
        "has_bq_dataset_id": bool((os.getenv("BQ_DATASET_ID") or "").strip()),
        "bq_project_id": (os.getenv("BQ_PROJECT_ID") or "").strip() or None,
        "bq_dataset_id": (os.getenv("BQ_DATASET_ID") or "").strip() or None,
    }


def run_query(sql: str, *, max_rows: int = 1000) -> list[dict[str, Any]]:
    client = build_client()
    query_job = client.query(sql)
    rows = query_job.result(max_results=max_rows)
    return [dict(row.items()) for row in rows]
