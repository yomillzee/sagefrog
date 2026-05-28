"""Build openapi-chatgpt.json from live /openapi.json (stdlib only)."""

from __future__ import annotations

import copy
import json
import urllib.request
from typing import Any

URL = "https://sagefrog-production.up.railway.app/openapi.json"
OUT = "openapi-chatgpt.json"
_SKIP_PATHS = {"/"}
_AUTH = "ApiKeyAuth"
_PROTECTED = ("/google-ads", "/linkedin", "/ga4", "/warehouse", "/cache")


def _fix_object_schemas(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" not in node:
            node["properties"] = {}
        for value in node.values():
            _fix_object_schemas(value)
    elif isinstance(node, list):
        for item in node:
            _fix_object_schemas(item)


def sanitize(schema: dict) -> dict:
    schema = copy.deepcopy(schema)
    schema["openapi"] = "3.1.0"
    schema["servers"] = [{"url": "https://sagefrog-production.up.railway.app"}]

    paths = schema.get("paths", {})
    for skip in _SKIP_PATHS:
        paths.pop(skip, None)

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        needs_auth = path.startswith(_PROTECTED)
        for method in ("get", "post", "put", "delete", "patch"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            if needs_auth:
                op["security"] = [{_AUTH: []}]
            else:
                op.pop("security", None)

    components = schema.setdefault("components", {})
    if not isinstance(components.get("schemas"), dict):
        components["schemas"] = {}
    components["securitySchemes"] = {
        _AUTH: {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Railway API_KEY value.",
        }
    }

    _fix_object_schemas(schema)
    return schema


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as resp:
        schema = json.load(resp)
    out = sanitize(schema)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {OUT} ({len(json.dumps(out))} bytes)")


if __name__ == "__main__":
    main()
