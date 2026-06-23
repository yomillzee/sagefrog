"""Server-side GA4 BigQuery credential parsing helpers."""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

GLOBAL_GCP_CREDENTIALS_ENV = "GCP_SERVICE_ACCOUNT_JSON"


def _get_required_env(key: str) -> str:
    value = (os.getenv(key) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def normalize_gcp_sa_raw(raw: str) -> str:
    """Strip BOM and invisible chars that often sneak in from copy/paste or Railway UI."""
    s = raw.strip().lstrip("\ufeff")
    for ch in ("\u200b", "\u200c", "\u200d", "\u2060"):
        s = s.replace(ch, "")
    return s.strip()


def append_decoded_b64_json_candidates(candidates: list[str], b64_compact: str) -> None:
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


def _decode_required_base64_json(raw: str, env_var: str) -> str:
    compact = "".join(raw.split())
    if not compact:
        raise RuntimeError(f"{env_var} could not be base64-decoded because it is empty.")
    padded = compact + ("=" * ((-len(compact)) % 4))
    try:
        return base64.b64decode(padded, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"{env_var} could not be base64-decoded.") from exc


def _looks_like_service_account_dict(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    return "type" in data and data.get("type") == "service_account" and "private_key" in data


def _parse_service_account_json(candidate: str, *, env_var: str, decoded_from_base64: bool = False) -> dict[str, Any]:
    try:
        data = json.loads(candidate)
        if isinstance(data, str) and data.strip().startswith("{"):
            data = json.loads(data.strip())
    except json.JSONDecodeError as exc:
        prefix = "decoded credential" if decoded_from_base64 else "credential"
        raise RuntimeError(f"{env_var} {prefix} is not valid JSON: {exc}") from exc

    if not _looks_like_service_account_dict(data):
        prefix = "decoded credential" if decoded_from_base64 else "credential"
        raise RuntimeError(
            f"{env_var} {prefix} is not a Google service account key "
            "(expected type=service_account + private_key)."
        )
    return data


def validate_and_encode_service_account(raw_json: str) -> tuple[str, str]:
    """Validate an uploaded service-account JSON; return (base64_value, client_email).

    The base64 is the form client credential env vars (GCP_CREDS_<CLIENT>_BASE64)
    and the global GCP_SERVICE_ACCOUNT_JSON accept. Raises RuntimeError if the
    payload is not a Google service account key. Never logs the key material.
    """
    text = (raw_json or "").strip()
    if not text:
        raise RuntimeError("No JSON provided.")
    info = _parse_service_account_json(text, env_var="uploaded credential")
    client_email = str(info.get("client_email") or "").strip()
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return encoded, client_email


def load_service_account_info_from_env(env_var: str = GLOBAL_GCP_CREDENTIALS_ENV, *, require_base64: bool = False) -> dict[str, Any]:
    """
    Load a Google service account key from an environment variable.

    The legacy global GCP_SERVICE_ACCOUNT_JSON accepts raw JSON, double-encoded JSON,
    or base64 JSON for backward compatibility. Client-specific credential env vars are
    expected to be base64-encoded JSON and never get logged or returned.
    """
    raw = normalize_gcp_sa_raw(os.getenv(env_var) or "")
    if not raw:
        if require_base64:
            raise RuntimeError(
                f"credentials_env is configured as {env_var}, but that environment variable is missing or empty."
            )
        raise RuntimeError(f"Missing required environment variable: {env_var}")

    if require_base64:
        decoded = _decode_required_base64_json(raw, env_var)
        return _parse_service_account_json(decoded, env_var=env_var, decoded_from_base64=True)

    return load_legacy_service_account_info(env_var=env_var, raw=raw)


def load_legacy_service_account_info(
    *, env_var: str = GLOBAL_GCP_CREDENTIALS_ENV, raw: str | None = None
) -> dict[str, Any]:
    """Parse the legacy global GCP service account value from Railway."""
    raw = normalize_gcp_sa_raw(raw if raw is not None else _get_required_env(env_var))

    candidates: list[str] = [raw]

    if raw.startswith('"') and raw.endswith('"'):
        try:
            inner = json.loads(raw)
            if isinstance(inner, str) and inner.strip().startswith("{"):
                candidates.append(inner.strip())
        except json.JSONDecodeError:
            pass

    if not raw.lstrip().startswith("{"):
        b64 = "".join(raw.split())
        append_decoded_b64_json_candidates(candidates, b64)

    last_err: Exception | None = None
    for cand in candidates:
        try:
            return _parse_service_account_json(cand, env_var=env_var)
        except Exception as exc:
            last_err = exc
            continue

    hint = (
        "Paste the downloaded key JSON exactly (starts with { and contains "
        '"type": "service_account"). If Railway mangled quotes, minify to one line '
        "or base64-encode the whole JSON and paste that instead."
    )
    raise RuntimeError(f"{env_var} is not valid JSON ({last_err}). {hint}") from last_err
