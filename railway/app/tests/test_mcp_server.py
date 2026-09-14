from __future__ import annotations

import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import connector_config_store  # noqa: E402
import dashboard_registry  # noqa: E402
import gtm_service  # noqa: E402
import mcp_server  # noqa: E402
import oauth_store  # noqa: E402
from gtm_quota import GTMRateLimited  # noqa: E402

KEY = "test-mcp-key"


def _client() -> TestClient:
    app = FastAPI()
    mcp_server.register_mcp_routes(app)
    return TestClient(app)


def _cfg(**over):
    base = {
        "status": "connected",
        "source_account_id": "123:456",
        "source_account_name": "Nixon (GTM-ABC123)",
        "last_success_at": datetime(2026, 9, 12, 9, 12, tzinfo=UTC),
        "last_error_message": None,
    }
    base.update(over)
    return types.SimpleNamespace(**base)


_LIVE_TAGS = {
    "fetched_at": "2026-09-13T08:00:00+00:00",
    "container_version": "42",
    "tag_count": 3,
    "rows": [
        {"tag_name": "GA4 Config", "friendly_type": "GA4 Configuration", "paused": False},
        {"tag_name": "Meta Pixel", "friendly_type": "Meta Pixel", "paused": False},
        {"tag_name": "Old UA", "friendly_type": "Universal Analytics", "paused": True},
    ],
}


class McpAuthTests(unittest.TestCase):
    """The endpoint is off until MCP_API_KEY is set, and never answers without it."""

    def test_unconfigured_key_disables_the_server(self):
        with patch.dict("os.environ", {}, clear=False), \
                patch.object(mcp_server, "configured_mcp_key", return_value=None):
            resp = _client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(resp.status_code, 503)

    def test_missing_credential_is_rejected(self):
        with patch.dict("os.environ", {"MCP_API_KEY": KEY}):
            resp = _client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(resp.status_code, 401)

    def test_wrong_credential_is_rejected(self):
        with patch.dict("os.environ", {"MCP_API_KEY": KEY}):
            resp = _client().post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": "Bearer nope"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_bearer_header_is_accepted(self):
        with patch.dict("os.environ", {"MCP_API_KEY": KEY}):
            resp = _client().post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": f"Bearer {KEY}"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"], {})

    def test_query_param_key_is_accepted(self):
        """Claude's custom-connector setup takes a URL and no static header,
        so the key has to be able to ride the query string."""
        with patch.dict("os.environ", {"MCP_API_KEY": KEY}):
            resp = _client().post(
                f"/mcp?key={KEY}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
            )
        self.assertEqual(resp.status_code, 200)


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict("os.environ", {"MCP_API_KEY": KEY})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.client = _client()
        self.auth = {"Authorization": f"Bearer {KEY}"}

    def _rpc(self, method, params=None, req_id=1):
        body = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            body["params"] = params
        return self.client.post("/mcp", json=body, headers=self.auth)

    def test_initialize_echoes_a_supported_protocol_version(self):
        resp = self._rpc("initialize", {"protocolVersion": "2025-03-26"})
        result = resp.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], mcp_server.SERVER_NAME)
        self.assertIn("tools", result["capabilities"])

    def test_initialize_falls_back_for_an_unknown_version(self):
        result = self._rpc("initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
        self.assertEqual(result["protocolVersion"], mcp_server._DEFAULT_PROTOCOL)

    def test_tools_list_advertises_the_read_only_surface(self):
        tools = self._rpc("tools/list").json()["result"]["tools"]
        self.assertEqual(
            sorted(t["name"] for t in tools),
            ["get_gtm_tags", "list_gtm_clients", "list_gtm_containers"],
        )
        for tool in tools:
            self.assertIn("inputSchema", tool)
            self.assertTrue(tool["description"])

    def test_notification_gets_no_body(self):
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.content, b"")

    def test_unknown_method_is_a_jsonrpc_error(self):
        error = self._rpc("tools/nope").json()["error"]
        self.assertEqual(error["code"], mcp_server._METHOD_NOT_FOUND)

    def test_malformed_json_is_a_parse_error(self):
        resp = self.client.post("/mcp", content=b"{not json", headers=self.auth)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], mcp_server._PARSE_ERROR)

    def test_get_reports_no_sse_stream(self):
        self.assertEqual(self.client.get("/mcp", headers=self.auth).status_code, 405)


class McpToolTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict("os.environ", {"MCP_API_KEY": KEY})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.client = _client()
        self.auth = {"Authorization": f"Bearer {KEY}"}

    def _call(self, name, arguments=None):
        resp = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["result"]

    def _payload(self, result):
        import json
        return json.loads(result["content"][0]["text"])

    def test_list_clients_reads_the_portal_not_gtm(self):
        with patch.object(connector_config_store, "client_slugs_with_configs",
                          return_value={"nixon"}), \
                patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(dashboard_registry, "get_client",
                             return_value=types.SimpleNamespace(label="Nixon Medical")), \
                patch.object(oauth_store, "token_health", return_value="ok"), \
                patch.object(gtm_service, "list_containers") as listc, \
                patch.object(gtm_service, "get_live_tags") as live:
            payload = self._payload(self._call("list_gtm_clients"))

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["clients"][0]["client"], "nixon")
        self.assertEqual(payload["clients"][0]["container"], "123:456")
        self.assertEqual(payload["clients"][0]["label"], "Nixon Medical")
        # Listing clients must cost no GTM quota at all.
        listc.assert_not_called()
        live.assert_not_called()

    def test_get_tags_defaults_to_the_configured_container_and_never_forces(self):
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags", return_value=_LIVE_TAGS) as live:
            payload = self._payload(self._call("get_gtm_tags", {"client": "nixon"}))

        live.assert_called_once()
        self.assertEqual(live.call_args.args[1:3], ("123", "456"))
        # force_refresh spends scarce project-wide quota; a read-only audit tool
        # has no business doing it.
        self.assertNotIn("force_refresh", live.call_args.kwargs)
        self.assertEqual(payload["container"], "123:456")
        self.assertEqual(payload["container_version"], "42")
        self.assertEqual(payload["summary"]["tag_count"], 3)
        self.assertEqual(payload["summary"]["paused_count"], 1)
        self.assertEqual(payload["summary"]["tags_by_type"]["Universal Analytics"], 1)
        self.assertEqual(len(payload["tags"]), 3)
        self.assertFalse(payload["stale"])

    def test_include_tags_false_returns_only_the_counts(self):
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags", return_value=_LIVE_TAGS):
            payload = self._payload(
                self._call("get_gtm_tags", {"client": "nixon", "include_tags": False})
            )
        self.assertNotIn("tags", payload)
        self.assertEqual(payload["summary"]["tag_count"], 3)

    def test_explicit_container_overrides_the_configured_one(self):
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags", return_value=_LIVE_TAGS) as live:
            self._call("get_gtm_tags", {"client": "nixon", "container": "999:888"})
        self.assertEqual(live.call_args.args[1:3], ("999", "888"))

    def test_stale_cache_is_surfaced_not_hidden(self):
        stale = dict(_LIVE_TAGS, stale=True)
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags", return_value=stale):
            payload = self._payload(self._call("get_gtm_tags", {"client": "nixon"}))
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["fetched_at"], "2026-09-13T08:00:00+00:00")

    def test_rate_limit_is_an_explained_tool_error_not_a_crash(self):
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags",
                             side_effect=GTMRateLimited("quota exhausted")):
            result = self._call("get_gtm_tags", {"client": "nixon"})
        self.assertTrue(result["isError"])
        self.assertIn("25 requests per 100 seconds", result["content"][0]["text"])

    def test_unknown_client_never_reaches_gtm(self):
        with patch.object(connector_config_store, "get_config", return_value=None), \
                patch.object(gtm_service, "get_live_tags") as live:
            result = self._call("get_gtm_tags", {"client": "nobody"})
        self.assertTrue(result["isError"])
        self.assertIn("no Google Tag Manager connector", result["content"][0]["text"])
        live.assert_not_called()

    def test_unconfigured_container_is_reported_clearly(self):
        with patch.object(connector_config_store, "get_config",
                          return_value=_cfg(source_account_id=None)), \
                patch.object(gtm_service, "get_live_tags") as live:
            result = self._call("get_gtm_tags", {"client": "nixon"})
        self.assertTrue(result["isError"])
        self.assertIn("accountId:containerId", result["content"][0]["text"])
        live.assert_not_called()

    def test_missing_token_asks_for_a_reconnect(self):
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value=None), \
                patch.object(oauth_store, "token_error", side_effect=lambda *a, **k: k["missing"]), \
                patch.object(gtm_service, "get_live_tags") as live:
            result = self._call("get_gtm_tags", {"client": "nixon"})
        self.assertTrue(result["isError"])
        self.assertIn("reconnecting", result["content"][0]["text"])
        live.assert_not_called()

    def test_list_containers_passes_the_clients_token(self):
        with patch.object(connector_config_store, "get_config", return_value=_cfg()), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "list_containers",
                             return_value=[{"id": "123:456", "name": "Nixon"}]) as listc:
            payload = self._payload(self._call("list_gtm_containers", {"client": "nixon"}))
        listc.assert_called_once_with("rt")
        self.assertEqual(payload["count"], 1)

    def test_unknown_tool_is_a_tool_error(self):
        result = self._call("publish_gtm_container", {"client": "nixon"})
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
