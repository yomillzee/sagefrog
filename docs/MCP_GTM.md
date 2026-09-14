# Connecting Claude to Google Tag Manager

The portal exposes a **Model Context Protocol (MCP)** server at `POST /mcp`, so
Claude — the desktop app, claude.ai, or Claude Code — can read any client's live
Tag Manager container directly. It answers questions like "is the Meta pixel
still live on Nixon's site", "which clients are still running Universal
Analytics tags", "what fires on the contact form" without anyone logging into
GTM.

## Why it lives inside the app

The GTM API allows **25 requests per 100 seconds for the whole Google Cloud
project** — shared by every client, every code path and every Railway worker.
A standalone MCP server talking to `tagmanager.googleapis.com` would be a
second, uncoordinated consumer of that budget: it would trip the breaker for the
portal that clients are actually looking at.

Running inside the app, every tool call goes through `gtm_service`, which means
it shares the in-process cache, the `api_cache` rows, the single-flight locks
and the `gtm_quota` governor with the connector pages. It also means no second
OAuth app and no re-consent — the per-client refresh tokens are already in
`oauth_store`.

## Turning it on

The endpoint is **off until `MCP_API_KEY` is set**, and returns 503 until then.
Set it in the Railway service variables:

```
MCP_API_KEY=<a long random string>
```

It is deliberately **not** `API_KEY`. This key gets pasted into a Claude
connector's configuration and lives on whatever machine that Claude runs on;
chaining it to the platform API key would mean rotating one to revoke the other.
Rotate it by changing the variable — connectors using the old value start
getting 401s immediately.

## Adding the connector

In Claude (desktop or claude.ai) → **Settings → Connectors → Add custom
connector**, give it the URL:

```
https://<portal-domain>/mcp?key=<MCP_API_KEY>
```

Claude's connector setup takes a URL and little else — there is no field for a
static header — so the key rides the query string, the same way the dashboard's
own share links authenticate. `Authorization: Bearer <key>` and
`X-API-Key: <key>` both work too, and are preferred wherever the client can send
a header (Claude Code's `.mcp.json`, curl, anything scripted): a secret in a
header does not end up in a proxy log.

Treat the URL as the credential. Anyone holding it can read every connected
client's tag configuration.

## The tools

| Tool | What it does | Quota cost |
| --- | --- | --- |
| `list_gtm_clients` | Clients with a GTM connector, the container each points at, credential health, last audit time | None — reads Postgres |
| `list_gtm_containers` | Every container a client's connection can see | **High** — one API call per GTM account; cached 15 min |
| `get_gtm_tags` | The live container version: tags with friendly types, paused state, consent settings, GA4 event names, and the triggers that fire them with their conditions | One read, cached 15 min |

Start with `list_gtm_clients` to learn the slugs, then `get_gtm_tags`.
`get_gtm_tags` defaults to the container the client is already configured for,
so the expensive `list_gtm_containers` is only needed to look at a *different*
container.

Nothing here ever passes `force_refresh`. An audit from within the last fifteen
minutes answers every question these tools exist for, and re-reading an
unchanged container spends quota the live portal needs.

### When the quota runs out

`get_gtm_tags` returns `"stale": true` alongside the `fetched_at` timestamp,
carrying the last known audit rather than failing — the same fallback the
connector pages use. The server instructs Claude to say so when it reports such
an answer. If there is no cached audit at all, the tool returns an error
explaining the limit.

## Read-only, and why

Every connector holds the `tagmanager.readonly` scope. Creating, editing or
publishing tags would need `tagmanager.edit.containers` plus a publish scope,
which means **re-consent from every connected client** and a write path that
`gtm_service` does not have. That is a deliberate, separate decision — not an
oversight.

## Protocol notes

Transport is MCP's Streamable HTTP: one endpoint carrying JSON-RPC 2.0, replying
in plain `application/json`. The spec allows this in place of an SSE stream for
servers that never initiate messages to the client, which this one does not —
three tools, no sampling, no notifications. `GET /mcp` therefore answers 405, as
the spec requires of a server offering no stream.

Supported protocol versions: `2025-06-18` (default), `2025-03-26`,
`2024-11-05`. Pre-`2025-06-18` JSON-RPC batches are still answered, for older
clients.

## Checking it works

```bash
curl -s https://<portal-domain>/mcp \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 400
```

A 503 means `MCP_API_KEY` is unset on the server; a 401 means the key does not
match.

Tests: `railway/app/tests/test_mcp_server.py`.
