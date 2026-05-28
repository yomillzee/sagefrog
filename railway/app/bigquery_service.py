from __future__ import annotations

import base64
import binascii
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


def _normalize_gcp_sa_raw(raw: str) -> str:
    """Strip BOM and invisible chars that often sneak in from copy/paste or Railway UI."""
    s = raw.strip().lstrip("\ufeff")
    for ch in ("\u200b", "\u200c", "\u200d", "\u2060"):
        s = s.replace(ch, "")
    return s.strip()


def _append_decoded_b64_json_candidates(candidates: list[str], b64_compact: str) -> None:
    """Decode base64 (standard + URL-safe) with optional strict validation."""
    if not b64_compact:
        return
    pad = (-len(b64_compact)) % 4
    padded = b64_compact + ("=" * pad)
    seen: set[str] = set()
    for decode_fn in (
        lambda: base64.b64decode(padded, validate=True),
        lambda: base64.b64decode(padded, validate=False),
        lambda: base64.urlsafe_b64decode(padded),
    ):
        try:
            decoded = decode_fn().decode("utf-8").strip()
            if decoded.startswith("{") and decoded not in seen:
                seen.add(decoded)
                candidates.append(decoded)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue


def _looks_like_service_account_dict(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    return "type" in data and data.get("type") == "service_account" and "private_key" in data


def _load_service_account_info() -> dict[str, Any]:
    """
    Parse GCP_SERVICE_ACCOUNT_JSON from Railway.

    Common paste issues handled:
    - UTF-8 BOM at start of string
    - Whole JSON wrapped as a JSON string (double-encoded)
    - Entire key file base64-encoded (single line)
    """
    raw = _normalize_gcp_sa_raw(_get_required_env("GCP_SERVICE_ACCOUNT_JSON"))

    candidates: list[str] = [raw]

    # Double-wrapped: "\"{...}\""
    if raw.startswith('"') and raw.endswith('"'):
        try:
            inner = json.loads(raw)
            if isinstance(inner, str) and inner.strip().startswith("{"):
                candidates.append(inner.strip())
        except json.JSONDecodeError:
            pass

    # Base64 of the JSON file (no leading '{'); allow pasted line breaks in the blob
    if not raw.lstrip().startswith("{"):
        b64 = "".join(raw.split())
        _append_decoded_b64_json_candidates(candidates, b64)

    last_err: Exception | None = None
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, str) and data.strip().startswith("{"):
                data = json.loads(data.strip())
            if not _looks_like_service_account_dict(data):
                raise ValueError("JSON is not a Google service account key (expected type=service_account + private_key).")
            return data
        except Exception as exc:
            last_err = exc
            continue

    hint = (
        "Paste the downloaded key JSON exactly (starts with { and contains "
        "\"type\": \"service_account\"). If Railway mangled quotes, minify to one line "
        "or base64-encode the whole JSON and paste that instead."
    )
    raise RuntimeError(f"GCP_SERVICE_ACCOUNT_JSON is not valid JSON ({last_err}). {hint}") from last_err


def build_client(project_id: str | None = None) -> bigquery.Client:
    """BigQuery client. Uses project_id arg, else BQ_PROJECT_ID (billing / job default project)."""
    pid = (project_id or os.getenv("BQ_PROJECT_ID") or "").strip()
    if not pid:
        raise RuntimeError("Missing BigQuery project id (BQ_PROJECT_ID or request override).")
    info = _load_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=pid, credentials=creds)


def env_summary() -> dict[str, Any]:
    """
    Summarize GA4/BigQuery env vars. Includes safe diagnostics for GCP_SERVICE_ACCOUNT_JSON
    (length and parse result only — never echoes the secret).
    """
    raw = _normalize_gcp_sa_raw(os.getenv("GCP_SERVICE_ACCOUNT_JSON") or "")
    has_sa = bool(raw)

    summary: dict[str, Any] = {
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
        summary["gcp_service_account_json_parse_error"] = (
            "GCP_SERVICE_ACCOUNT_JSON is unset or only whitespace."
        )
        return summary

    lead = raw.lstrip()[:1]
    if lead == "{":
        summary["gcp_service_account_json_hint"] = "raw_json"
    elif lead == '"':
        summary["gcp_service_account_json_hint"] = "possibly_double_quoted_wrap"
    else:
        summary["gcp_service_account_json_hint"] = "base64_or_other"

    # Real SA keys (JSON or base64) are usually ~2k+ chars; much shorter is almost always broken paste
    summary["gcp_service_account_json_suspected_truncated"] = len(raw) < 1800

    try:
        _load_service_account_info()
        summary["gcp_service_account_json_parse_ok"] = True
        summary["gcp_service_account_json_parse_error"] = None
    except Exception as exc:
        summary["gcp_service_account_json_parse_ok"] = False
        summary["gcp_service_account_json_parse_error"] = str(exc)[:500]

    return summary


def run_query(sql: str, *, max_rows: int = 1000, project_id: str | None = None) -> list[dict[str, Any]]:
    client = build_client(project_id)
    query_job = client.query(sql)
    rows = query_job.result(max_results=max_rows)
    return [dict(row.items()) for row in rows]
