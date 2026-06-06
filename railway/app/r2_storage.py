"""Cloudflare R2 object storage for client dashboard documents."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import boto3
from botocore.client import Config

_CLIENT: Any | None = None


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def enabled() -> bool:
    """True when R2 credentials and bucket are configured."""
    if _env("R2_DOCUMENTS_ENABLED").lower() in ("0", "false", "no", "off"):
        return False
    return bool(
        _env("R2_BUCKET_NAME")
        and _env("R2_ACCESS_KEY_ID")
        and _env("R2_SECRET_ACCESS_KEY")
        and (_env("R2_ACCOUNT_ID") or _env("R2_ENDPOINT_URL"))
    )


def endpoint_url() -> str:
    custom = _env("R2_ENDPOINT_URL")
    if custom:
        return custom.rstrip("/")
    account_id = _env("R2_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("R2_ACCOUNT_ID or R2_ENDPOINT_URL is required.")
    return f"https://{account_id}.r2.cloudflarestorage.com"


def bucket_name() -> str:
    name = _env("R2_BUCKET_NAME")
    if not name:
        raise RuntimeError("R2_BUCKET_NAME is required.")
    return name


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = boto3.client(
            "s3",
            endpoint_url=endpoint_url(),
            aws_access_key_id=_env("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _CLIENT


def sanitize_filename(filename: str) -> str:
    base = os.path.basename((filename or "document").strip()) or "document"
    cleaned = re.sub(r"[^\w.\- ]", "_", base).strip().replace(" ", "-")
    return cleaned[:180] or "document"


def document_object_key(*, client_slug: str, doc_id: int, filename: str) -> str:
    slug = (client_slug or "client").strip().lower()
    safe = sanitize_filename(filename)
    prefix = _env("R2_KEY_PREFIX") or "client-documents"
    return f"{prefix.strip('/')}/{slug}/{int(doc_id)}/{safe}"


def put_object(*, key: str, body: bytes, content_type: str) -> None:
    client = _client()
    client.put_object(
        Bucket=bucket_name(),
        Key=key,
        Body=body,
        ContentType=(content_type or "application/octet-stream").split(";", 1)[0].strip(),
    )


def get_object(key: str) -> bytes:
    client = _client()
    response = client.get_object(Bucket=bucket_name(), Key=key)
    body = response["Body"].read()
    if not isinstance(body, bytes):
        return bytes(body)
    return body


def delete_object(key: str) -> None:
    if not (key or "").strip():
        return
    client = _client()
    client.delete_object(Bucket=bucket_name(), Key=key)


def presigned_get_url(
    key: str,
    *,
    filename: str,
    expires_seconds: int | None = None,
) -> str:
    client = _client()
    ttl = expires_seconds
    if ttl is None:
        try:
            ttl = max(60, int(_env("R2_DOWNLOAD_URL_TTL_SECONDS") or "300"))
        except ValueError:
            ttl = 300
    safe_name = sanitize_filename(filename)
    disposition = f'attachment; filename="{safe_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket_name(),
            "Key": key,
            "ResponseContentDisposition": disposition,
        },
        ExpiresIn=ttl,
    )


def status() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "bucket": _env("R2_BUCKET_NAME") or None,
        "endpoint": endpoint_url() if enabled() else None,
        "key_prefix": _env("R2_KEY_PREFIX") or "client-documents",
    }
