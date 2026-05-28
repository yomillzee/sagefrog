"""OpenAPI schema sanitized for ChatGPT Custom Actions."""

from __future__ import annotations

import copy
import os
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

# Paths ChatGPT does not need (root has a free-form object response that fails validation).
_SKIP_PATHS = {"/"}

_GPT_SECURITY_SCHEME = "ApiKeyAuth"


def _fix_object_schemas(node: Any) -> None:
    """ChatGPT requires every object schema to declare `properties`."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" not in node:
            node["properties"] = {}
        for value in node.values():
            _fix_object_schemas(value)
    elif isinstance(node, list):
        for item in node:
            _fix_object_schemas(item)


def build_chatgpt_openapi(app: FastAPI) -> dict:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema = copy.deepcopy(schema)
    schema["openapi"] = "3.1.0"

    base_url = (
        os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or "https://sagefrog-production.up.railway.app"
    )
    schema["servers"] = [{"url": base_url}]

    paths = schema.get("paths", {})
    for skip in _SKIP_PATHS:
        paths.pop(skip, None)

    protected_prefixes = ("/google-ads", "/linkedin", "/ga4", "/warehouse", "/cache")
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        needs_auth = path.startswith(protected_prefixes)
        for method in ("get", "post", "put", "delete", "patch"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            if needs_auth:
                op["security"] = [{_GPT_SECURITY_SCHEME: []}]
            else:
                op.pop("security", None)

    components = schema.setdefault("components", {})
    if not isinstance(components.get("schemas"), dict):
        components["schemas"] = {}
    components["securitySchemes"] = {
        _GPT_SECURITY_SCHEME: {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Railway API_KEY value.",
        }
    }

    _fix_object_schemas(schema)
    return schema
