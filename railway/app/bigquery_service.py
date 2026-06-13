from __future__ import annotations

import os
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

from ga4_credentials import (
    GLOBAL_GCP_CREDENTIALS_ENV,
    load_legacy_service_account_info,
    load_service_account_info_from_env,
    normalize_gcp_sa_raw,
)


def _load_service_account_info() -> dict[str, Any]:
    return load_legacy_service_account_info()


def build_client(
    project_id: str | None = None,
    *,
    credentials_env: str | None = None,
    credentials_info: dict[str, Any] | None = None,
) -> bigquery.Client:
    """BigQuery client. Uses project_id arg, else BQ_PROJECT_ID (billing / job default project)."""
    pid = (project_id or os.getenv("BQ_PROJECT_ID") or "").strip()
    if not pid:
        raise RuntimeError("Missing BigQuery project id (BQ_PROJECT_ID or request override).")
    info = credentials_info or load_service_account_info_from_env(
        (credentials_env or "").strip() or GLOBAL_GCP_CREDENTIALS_ENV,
        require_base64=bool((credentials_env or "").strip()),
    )
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=pid, credentials=creds)


def env_summary(credentials_env: str | None = None) -> dict[str, Any]:
    """
    Summarize GA4/BigQuery env vars. Includes safe credential diagnostics
    (length and parse result only — never echoes the secret).
    """
    env_var = (credentials_env or "").strip() or GLOBAL_GCP_CREDENTIALS_ENV
    raw = normalize_gcp_sa_raw(os.getenv(env_var) or "")
    has_sa = bool(raw)

    summary: dict[str, Any] = {
        "credentials_env": env_var,
        "has_gcp_service_account_json": has_sa,
        "has_bq_project_id": bool((os.getenv("BQ_PROJECT_ID") or "").strip()),
        "has_bq_dataset_id": bool((os.getenv("BQ_DATASET_ID") or "").strip()),
        "bq_project_id": (os.getenv("BQ_PROJECT_ID") or "").strip() or None,
        "bq_dataset_id": (os.getenv("BQ_DATASET_ID") or "").strip() or None,
        "gcp_service_account_json_char_count": len(raw),
        "gcp_service_account_json_hint": "empty",
        "gcp_service_account_json_suspected_truncated": False,
        "gcp_service_account_json_parse_ok": False,
        "gcp_service_account_json_parse_error": None,
    }

    if not raw:
        summary["gcp_service_account_json_parse_error"] = f"{env_var} is unset or only whitespace."
        return summary

    lead = raw.lstrip()[:1]
    if credentials_env:
        summary["gcp_service_account_json_hint"] = "base64"
    elif lead == "{":
        summary["gcp_service_account_json_hint"] = "raw_json"
    elif lead == '"':
        summary["gcp_service_account_json_hint"] = "possibly_double_quoted_wrap"
    else:
        summary["gcp_service_account_json_hint"] = "base64_or_other"

    summary["gcp_service_account_json_suspected_truncated"] = len(raw) < 1800

    try:
        load_service_account_info_from_env(env_var, require_base64=bool(credentials_env))
        summary["gcp_service_account_json_parse_ok"] = True
        summary["gcp_service_account_json_parse_error"] = None
    except Exception as exc:
        summary["gcp_service_account_json_parse_ok"] = False
        summary["gcp_service_account_json_parse_error"] = str(exc)[:500]

    return summary


def run_query(
    sql: str,
    *,
    max_rows: int = 1000,
    project_id: str | None = None,
    credentials_env: str | None = None,
    credentials_info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    client = build_client(project_id, credentials_env=credentials_env, credentials_info=credentials_info)
    query_job = client.query(sql)
    rows = query_job.result(max_results=max_rows)
    return [dict(row.items()) for row in rows]
