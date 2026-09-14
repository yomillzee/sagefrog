"""Model Context Protocol server — read-only Google Tag Manager access.

Mounted inside this app rather than shipped as a standalone server, for one
decisive reason: **GTM's quota is project-wide**. The API allows 0.25 requests
per second (25 per 100-second sliding window) across every client, code path and
worker, and a separate process talking to ``tagmanager.googleapis.com`` would be
a second, uncoordinated consumer — one that trips the breaker for the portal
that clients are actually looking at. Living here, every tool call goes through
``gtm_service``, which means it rides the same in-process cache, the same shared
``api_cache`` rows, the same single-flight locks and the same ``gtm_quota``
governor as the connector pages. It also means no second OAuth app and no
re-consent: the per-client refresh tokens are already in ``oauth_store``.

Transport is MCP's Streamable HTTP: one endpoint, ``POST /mcp``, carrying
JSON-RPC 2.0. Replies are plain ``application/json`` — the spec allows this in
place of an SSE stream for servers that never initiate messages to the client,
which is all of them here (three tools, no sampling, no notifications). ``GET``
therefore answers 405, as the spec requires of a server offering no stream.

Scope is ``tagmanager.readonly``, which is what the connectors already hold, so
every tool here reads. Creating or publishing tags would need
``tagmanager.edit.containers``, a publish scope, and re-consent from every
connected client — deliberately out of scope.

Off unless ``MCP_API_KEY`` is set. It is deliberately its own secret rather than
a reuse of ``API_KEY``: the key gets pasted into a Claude connector config, and
a key with that reach should be revocable on its own.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import connector_config_store
import dashboard_registry
import gtm_service
import oauth_store
from gtm_quota import GTMRateLimited

_log = logging.getLogger(__name__)

router = APIRouter()

SERVER_NAME = "sagefrog-gtm"
SERVER_VERSION = "1.0.0"

# Newest first. An `initialize` asking for one of these is answered in kind;
# anything else is answered with our default, which the spec says the client
# must then either accept or disconnect over.
_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_PROTOCOL = _SUPPORTED_PROTOCOLS[0]

# JSON-RPC 2.0 reserved codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ── Auth ─────────────────────────────────────────────────────────────────────

def configured_mcp_key() -> str | None:
    """The MCP endpoint's own bearer secret, or None when the server is off.

    No fallback to ``API_KEY``. This key is handed to a third party (the Claude
    client that connects) and lives in that client's config; chaining it to the
    platform API key would mean rotating one to revoke the other.
    """
    return (os.getenv("MCP_API_KEY") or "").strip() or None


def _presented_key(request: Request) -> str:
    """Pull the caller's key from a header, or the query string.

    ``?key=`` is here because Claude's custom-connector setup takes a URL and
    little else — there is no field for a static header — and it matches how the
    dashboard's own share links already authenticate. Header first, so a caller
    that can send one never puts the secret somewhere it will be logged.
    """
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header_key = (request.headers.get("x-api-key") or "").strip()
    if header_key:
        return header_key
    return (request.query_params.get("key") or "").strip()


def _authorized(request: Request) -> bool:
    expected = configured_mcp_key()
    if not expected:
        return False
    return hmac.compare_digest(_presented_key(request), expected)


# ── Tool definitions ─────────────────────────────────────────────────────────

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_gtm_clients",
        "description": (
            "List the portal clients that have a Google Tag Manager connector, "
            "with the container each one is pointed at and when it was last "
            "audited. Start here: the client slugs it returns are what the other "
            "tools take. Reads the portal's own database — costs no GTM quota."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_gtm_containers",
        "description": (
            "List every GTM container a client's connection can see, as "
            "'accountId:containerId' plus a display name. Use this only to find a "
            "container other than the one the client is configured for — it fans "
            "out one API call per GTM account, making it the most quota-expensive "
            "call available against a project-wide limit of 25 requests per 100 "
            "seconds. Results are cached for 15 minutes and a rate-limited call "
            "returns the last known list rather than failing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "client": {
                    "type": "string",
                    "description": "Client slug, as returned by list_gtm_clients.",
                },
            },
            "required": ["client"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_gtm_tags",
        "description": (
            "Audit a client's live GTM container version: every tag with its "
            "friendly type, paused state, consent setting, GA4 event name, and "
            "the triggers that fire it (with their conditions). Answers 'what is "
            "firing on this site', 'is the Meta pixel still live', 'which tags "
            "are paused', 'what fires on the contact form'. Defaults to the "
            "container the client is configured for. Cached for 15 minutes; if "
            "GTM's quota blocks a fresh read the last known audit is returned "
            "with stale=true and the time it was fetched, rather than an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "client": {
                    "type": "string",
                    "description": "Client slug, as returned by list_gtm_clients.",
                },
                "container": {
                    "type": "string",
                    "description": (
                        "Optional 'accountId:containerId' from list_gtm_containers. "
                        "Omit to use the container this client is configured for."
                    ),
                },
                "include_tags": {
                    "type": "boolean",
                    "description": (
                        "Include the per-tag detail (default true). Set false for "
                        "just the counts by tag type, when the full list would be "
                        "more than the question needs."
                    ),
                },
            },
            "required": ["client"],
            "additionalProperties": False,
        },
    },
]


# ── Tool implementations (blocking; run in a threadpool) ─────────────────────

class _ToolError(Exception):
    """A failure to report to the model as a tool result, not a JSON-RPC error."""


def _resolve_client(client: str) -> tuple[str, connector_config_store.ConnectorConfig]:
    slug = dashboard_registry.normalize_slug(client or "")
    if not slug:
        raise _ToolError("A client slug is required. Call list_gtm_clients first.")
    cfg = connector_config_store.get_config(slug, "gtm")
    if cfg is None:
        raise _ToolError(
            f"Client {slug!r} has no Google Tag Manager connector. "
            "Call list_gtm_clients to see which clients do."
        )
    return slug, cfg


def _refresh_token(slug: str) -> str:
    token = oauth_store.get_refresh_token("google_tag_manager", client_slug=slug)
    if not token:
        raise _ToolError(oauth_store.token_error(
            "google_tag_manager",
            client_slug=slug,
            missing=(
                f"Client {slug!r} has a GTM connector but no stored credentials — "
                "it needs reconnecting in the portal."
            ),
        ))
    return token


def _split_container(raw: str) -> tuple[str, str]:
    parts = (raw or "").split(":")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        raise _ToolError(
            f"Container {raw!r} is not in 'accountId:containerId' form. "
            "Use a value from list_gtm_containers."
        )
    return parts[0].strip(), parts[1].strip()


def _tool_list_gtm_clients() -> dict[str, Any]:
    clients: list[dict[str, Any]] = []
    for slug in sorted(connector_config_store.client_slugs_with_configs()):
        cfg = connector_config_store.get_config(slug, "gtm")
        if cfg is None:
            continue
        row = dashboard_registry.get_client(slug)
        clients.append({
            "client": slug,
            "label": row.label if row else slug,
            "status": cfg.status,
            "container": cfg.source_account_id or None,
            "container_name": cfg.source_account_name or None,
            "credentials": oauth_store.token_health("google_tag_manager", slug),
            "last_audited_at": (
                cfg.last_success_at.isoformat() if cfg.last_success_at else None
            ),
            "last_error": cfg.last_error_message or None,
        })
    return {"clients": clients, "count": len(clients)}


def _tool_list_gtm_containers(client: str) -> dict[str, Any]:
    slug, _cfg = _resolve_client(client)
    containers = gtm_service.list_containers(_refresh_token(slug))
    return {"client": slug, "containers": containers, "count": len(containers)}


def _tool_get_gtm_tags(
    client: str, container: str | None, include_tags: bool
) -> dict[str, Any]:
    slug, cfg = _resolve_client(client)
    account_id, container_id = _split_container(container or cfg.source_account_id or "")

    # Never force_refresh. A tag audit from within the last 15 minutes answers
    # every question this tool exists for, and spending project-wide quota to
    # re-read an unchanged container is what exhausts it for the live portal.
    payload = gtm_service.get_live_tags(slug, account_id, container_id, _refresh_token(slug))

    rows: list[dict[str, Any]] = payload.get("rows") or []
    by_type: dict[str, int] = {}
    for row in rows:
        key = row.get("friendly_type") or "Unknown"
        by_type[key] = by_type.get(key, 0) + 1

    result: dict[str, Any] = {
        "client": slug,
        "container": f"{account_id}:{container_id}",
        "container_name": cfg.source_account_name or None,
        "container_version": payload.get("container_version") or None,
        "fetched_at": payload.get("fetched_at"),
        # True when GTM's quota blocked a live read and this is the last known
        # audit. Surfaced rather than swallowed: the model should say so.
        "stale": bool(payload.get("stale")),
        "summary": {
            "tag_count": len(rows),
            "paused_count": sum(1 for r in rows if r.get("paused")),
            "tags_by_type": dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
    }
    if include_tags:
        result["tags"] = rows
    return result


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "list_gtm_clients":
        return _tool_list_gtm_clients()
    if name == "list_gtm_containers":
        return _tool_list_gtm_containers(str(arguments.get("client") or ""))
    if name == "get_gtm_tags":
        container = arguments.get("container")
        return _tool_get_gtm_tags(
            str(arguments.get("client") or ""),
            str(container) if container else None,
            bool(arguments.get("include_tags", True)),
        )
    raise _ToolError(f"Unknown tool {name!r}.")


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a tool, turning every expected failure into an `isError` result.

    A tool that cannot answer is not a broken protocol exchange — reporting it
    as a JSON-RPC error would hide the reason from the model, which can often
    act on it ("that client needs reconnecting", "quota is exhausted, here is
    the audit from 09:12").
    """
    try:
        payload = _dispatch_tool(name, arguments)
        text = json.dumps(payload, indent=2, default=str)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except _ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except GTMRateLimited as exc:
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Google Tag Manager's project-wide quota is exhausted and no "
                    f"cached audit is available for this container: {exc}. The "
                    f"limit is 25 requests per 100 seconds shared across every "
                    f"client — wait a minute and try again."
                ),
            }],
            "isError": True,
        }
    except PermissionError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # pragma: no cover - defensive
        _log.exception("MCP tool %s failed", name)
        return {
            "content": [{"type": "text", "text": f"{name} failed: {exc}"}],
            "isError": True,
        }


# ── JSON-RPC plumbing ────────────────────────────────────────────────────────

def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = str(params.get("protocolVersion") or "")
    return {
        "protocolVersion": requested if requested in _SUPPORTED_PROTOCOLS else _DEFAULT_PROTOCOL,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "Read-only Google Tag Manager access for Sage Frog portal clients. "
            "Call list_gtm_clients first to learn the client slugs, then "
            "get_gtm_tags for a container audit. GTM's API is limited to 25 "
            "requests per 100 seconds across the whole project, so prefer the "
            "cached answer these tools return and avoid looping over many "
            "clients in one go. Results marked stale=true came from cache "
            "because the quota was exhausted — say so when reporting them."
        ),
    }


async def _handle_message(message: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for a notification."""
    if not isinstance(message, dict):
        return _error(None, _INVALID_REQUEST, "Expected a JSON-RPC object.")

    method = message.get("method")
    req_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    if not isinstance(method, str):
        return None if is_notification else _error(req_id, _INVALID_REQUEST, "Missing method.")

    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _ok(req_id, _initialize(params))

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": _TOOLS})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(req_id, _INVALID_PARAMS, "tools/call requires a tool name.")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        # gtm_service is synchronous and can block for seconds — on an HTTP
        # round-trip, on a single-flight lock, or waiting on the quota governor.
        # Off the event loop it goes, or one tag audit stalls every dashboard
        # request this worker is serving.
        result = await run_in_threadpool(_call_tool, name, arguments)
        return _ok(req_id, result)

    if is_notification:
        return None
    return _error(req_id, _METHOD_NOT_FOUND, f"Unknown method {method!r}.")


@router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    if not configured_mcp_key():
        return JSONResponse(
            {"error": "MCP_API_KEY is not configured on this server."}, status_code=503
        )
    if not _authorized(request):
        return JSONResponse(
            {"error": "Unauthorized."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        body = json.loads(await request.body() or b"")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_error(None, _PARSE_ERROR, "Invalid JSON."), status_code=400)

    # A list is a pre-2025-06-18 batch. Still answered, so an older client keeps
    # working; newer ones never send one.
    messages = body if isinstance(body, list) else [body]
    if not messages:
        return JSONResponse(_error(None, _INVALID_REQUEST, "Empty batch."), status_code=400)

    responses = [r for r in [await _handle_message(m) for m in messages] if r is not None]
    if not responses:
        # Notifications only — nothing to say back.
        return Response(status_code=202)
    payload = responses if isinstance(body, list) else responses[0]
    return JSONResponse(payload)


@router.get("/mcp", include_in_schema=False)
async def mcp_no_stream() -> Response:
    """The spec's required answer when a server offers no server-to-client SSE
    stream. These tools only ever reply to a request."""
    return JSONResponse({"error": "This server does not offer an SSE stream."}, status_code=405)


@router.delete("/mcp", include_in_schema=False)
async def mcp_end_session() -> Response:
    """Session termination. Nothing is kept between requests, so this is a
    formality — but answering it keeps a well-behaved client from reporting an
    error when it disconnects."""
    return Response(status_code=204)


def register_mcp_routes(app) -> None:
    """Attach the MCP endpoint."""
    app.include_router(router)
