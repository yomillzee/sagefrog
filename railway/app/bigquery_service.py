from __future__ import annotations

import os
import re
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
    env_var = (credentials_env or "").strip() or GLOBAL_GCP_CREDENTIALS_ENV
    info = credentials_info or load_service_account_info_from_env(
        env_var,
        # Only client-specific vars (e.g. GCP_CREDS_PENN_BASE64) are base64 --
        # the shared GLOBAL_GCP_CREDENTIALS_ENV ("GCP_SERVICE_ACCOUNT_JSON") is
        # plain JSON even when a caller passes it explicitly (e.g. as a
        # resolved GscClientTarget/ResolvedGa4Client fallback). Treating any
        # explicit credentials_env as base64 broke every BQ mart read for
        # clients whose resolved credentials_env is the plain-JSON default,
        # while the write paths (which check the var name, not "was one
        # passed") worked fine -- see gsc_sync_service.py's _creds_env().
        require_base64=(env_var != GLOBAL_GCP_CREDENTIALS_ENV),
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

    expects_base64 = env_var != GLOBAL_GCP_CREDENTIALS_ENV
    lead = raw.lstrip()[:1]
    if expects_base64:
        summary["gcp_service_account_json_hint"] = "base64"
    elif lead == "{":
        summary["gcp_service_account_json_hint"] = "raw_json"
    elif lead == '"':
        summary["gcp_service_account_json_hint"] = "possibly_double_quoted_wrap"
    else:
        summary["gcp_service_account_json_hint"] = "base64_or_other"

    summary["gcp_service_account_json_suspected_truncated"] = len(raw) < 1800

    try:
        load_service_account_info_from_env(env_var, require_base64=expects_base64)
        summary["gcp_service_account_json_parse_ok"] = True
        summary["gcp_service_account_json_parse_error"] = None
    except Exception as exc:
        summary["gcp_service_account_json_parse_ok"] = False
        summary["gcp_service_account_json_parse_error"] = str(exc)[:500]

    return summary


def _env_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# Default cost guard: cap bytes *scanned* per query. `max_rows` only limits rows
# returned, so a full events_* scan still bills fully without this. 100 GiB is
# generous for normal queries but blocks runaway full-table scans. Override with
# BQ_MAX_BYTES_BILLED (set to 0 to disable).
_DEFAULT_MAX_BYTES_BILLED = 100 * 1024**3


def make_job_config(*, dry_run: bool = False) -> bigquery.QueryJobConfig:
    """Shared QueryJobConfig so the cost/time cap is universal across the app.

    Every BigQuery query should go through this (directly or via run_query) so
    that `maximum_bytes_billed` — and an optional wall-clock timeout — always
    apply. Without it, an unbounded scan can bill arbitrarily.
    """
    config = bigquery.QueryJobConfig(dry_run=dry_run)
    max_bytes = _env_int("BQ_MAX_BYTES_BILLED")
    if max_bytes is None:
        max_bytes = _DEFAULT_MAX_BYTES_BILLED
    if max_bytes > 0:
        config.maximum_bytes_billed = max_bytes
    timeout_ms = _env_int("BQ_JOB_TIMEOUT_MS")
    if timeout_ms and timeout_ms > 0 and hasattr(config, "job_timeout_ms"):
        config.job_timeout_ms = timeout_ms
    return config


_SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_LINE_COMMENT = re.compile(r"(--|#)[^\n]*")
_FORBIDDEN_SQL_KEYWORD = re.compile(
    r"(?i)\b(INSERT|UPDATE|DELETE|MERGE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|"
    r"REVOKE|CALL|EXPORT|LOAD|BEGIN|COMMIT|ROLLBACK)\b"
)


def assert_read_only_select(sql: str) -> str:
    """Guard arbitrary SQL submitted over the API: allow only a single read-only
    SELECT/WITH statement. Rejects DML/DDL and multi-statement payloads.

    Returns the original SQL when valid; raises ValueError otherwise.
    """
    raw = (sql or "").strip()
    if not raw:
        raise ValueError("Empty SQL.")
    cleaned = _SQL_LINE_COMMENT.sub(" ", _SQL_BLOCK_COMMENT.sub(" ", raw)).strip()
    # Allow at most one trailing semicolon; reject stacked statements.
    body = cleaned.rstrip(";").strip()
    if ";" in body:
        raise ValueError("Multiple SQL statements are not allowed; submit a single SELECT.")
    head = body.lstrip("(").lstrip()
    if not (head[:6].upper() == "SELECT" or head[:4].upper() == "WITH"):
        raise ValueError("Only read-only SELECT / WITH queries are allowed.")
    if _FORBIDDEN_SQL_KEYWORD.search(body):
        raise ValueError("Query contains a disallowed (write/DDL) statement keyword.")
    return raw


def run_query(
    sql: str,
    *,
    max_rows: int = 1000,
    project_id: str | None = None,
    credentials_env: str | None = None,
    credentials_info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    client = build_client(project_id, credentials_env=credentials_env, credentials_info=credentials_info)
    query_job = client.query(sql, job_config=make_job_config())
    rows = query_job.result(max_results=max_rows)
    return [dict(row.items()) for row in rows]
