"""Write service variables to Railway via its public GraphQL API.

Used by the admin "GCP service account" upload: the app base64-encodes an
uploaded service-account JSON and upserts it as a Railway service variable.
Railway then redeploys the service so the new credential becomes live.

Configure these as Railway variables on this service:
  RAILWAY_API_TOKEN        — account or team token (sent as `Authorization: Bearer`)
  RAILWAY_PROJECT_ID
  RAILWAY_ENVIRONMENT_ID
  RAILWAY_SERVICE_ID
  RAILWAY_API_URL          — optional override (default: public GraphQL endpoint)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_API_URL = "https://backboard.railway.com/graphql/v2"


def _cfg(name: str) -> str:
    return (os.getenv(name) or "").strip()


def config() -> dict[str, str]:
    return {
        "token": _cfg("RAILWAY_API_TOKEN"),
        "project_id": _cfg("RAILWAY_PROJECT_ID"),
        "environment_id": _cfg("RAILWAY_ENVIRONMENT_ID"),
        "service_id": _cfg("RAILWAY_SERVICE_ID"),
    }


def enabled() -> bool:
    return all(config().values())


def env_summary() -> dict[str, Any]:
    """Non-sensitive readiness summary for the admin UI (never returns the token)."""
    c = config()
    return {
        "has_token": bool(c["token"]),
        "has_project_id": bool(c["project_id"]),
        "has_environment_id": bool(c["environment_id"]),
        "has_service_id": bool(c["service_id"]),
        "ready": enabled(),
    }


def _api_url() -> str:
    return _cfg("RAILWAY_API_URL") or _DEFAULT_API_URL


def set_variable(name: str, value: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Upsert a Railway service variable. Triggers a redeploy of the service.

    Raises RuntimeError on misconfiguration or API failure. Never logs `value`.
    """
    c = config()
    missing = [key for key, val in c.items() if not val]
    if missing:
        raise RuntimeError(
            "Railway API is not configured. Missing: " + ", ".join(sorted(missing))
        )

    query = (
        "mutation VariableUpsert($input: VariableUpsertInput!) {\n"
        "  variableUpsert(input: $input)\n"
        "}"
    )
    variables = {
        "input": {
            "projectId": c["project_id"],
            "environmentId": c["environment_id"],
            "serviceId": c["service_id"],
            "name": name,
            "value": value,
        }
    }
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        _api_url(),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {c['token']}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"Railway API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Railway API request failed: {exc.reason}") from exc

    if isinstance(payload, dict) and payload.get("errors"):
        msg = "; ".join(
            str(err.get("message") or err) for err in payload["errors"]
        )[:500]
        raise RuntimeError(f"Railway API error: {msg}")
    return {"ok": True, "name": name}
