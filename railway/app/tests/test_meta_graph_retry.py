from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import httpx  # noqa: E402

import meta_service  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, *, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeClient:
    """Context-manager httpx.Client stand-in. Each .get() pops the next queued
    item; a queued Exception is raised (simulating a transport error), a
    _FakeResponse is returned. Records the (url, params) of every call so tests
    can assert on the page limit that was actually sent."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None):
        self.calls += 1
        self.requests.append((url, dict(params or {})))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_REDUCE_DATA_BODY = {
    "error": {
        "code": 1,
        "message": "Please reduce the amount of data you're asking for, then retry your request",
    }
}


_ENV = meta_service.MetaEnv(
    app_id="app",
    app_secret="secret",
    access_token="tok",
    business_id="biz",
    api_version="v21.0",
)


def _run(outcomes):
    client = _FakeClient(outcomes)
    with patch.object(meta_service.httpx, "Client", return_value=client), \
            patch.object(meta_service.time, "sleep") as sleep:
        result = meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
    return result, client, sleep


class MetaGraphRetryTests(unittest.TestCase):
    """A connection reset mid-response (the meta_creative_fetch failure) is an
    httpx.TransportError and must be retried with backoff, not surfaced as a
    hard sync error on the first blip."""

    def test_connection_reset_is_retried_then_succeeds(self):
        reset = httpx.ReadError("[Errno 104] Connection reset by peer")
        result, client, sleep = _run([
            reset,
            _FakeResponse(200, payload={"data": [{"id": "1"}]}),
        ])
        self.assertEqual(result, {"data": [{"id": "1"}]})
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_persistent_transport_error_raises_after_max_attempts(self):
        reset = httpx.ConnectError("[Errno 104] Connection reset by peer")
        client = _FakeClient([reset] * meta_service._MAX_ATTEMPTS)
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
        msg = str(ctx.exception)
        self.assertIn("transport error", msg)
        self.assertIn("Connection reset by peer", msg)
        self.assertEqual(client.calls, meta_service._MAX_ATTEMPTS)

    def test_transient_status_is_retried(self):
        result, client, sleep = _run([
            _FakeResponse(503, text="unavailable"),
            _FakeResponse(429, text="slow down"),
            _FakeResponse(200, payload={"data": []}),
        ])
        self.assertEqual(result, {"data": []})
        self.assertEqual(client.calls, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_honours_retry_after_header(self):
        _result, _client, sleep = _run([
            _FakeResponse(429, headers={"Retry-After": "9"}),
            _FakeResponse(200, payload={}),
        ])
        sleep.assert_called_once_with(9.0)

    def test_non_transient_status_fails_fast(self):
        client = _FakeClient([
            _FakeResponse(400, text="", payload={"error": {"message": "ads_read required"}}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as ctx:
                meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(client.calls, 1)
        sleep.assert_not_called()


class MetaReduceDataTests(unittest.TestCase):
    """Meta's "Please reduce the amount of data you're asking for" 500 is a
    request-size problem, not a transient blip. Retrying the same oversized page
    just fails again, so _graph_get shrinks the page `limit` and retries."""

    def test_reduce_data_shrinks_page_and_retries(self):
        client = _FakeClient([
            _FakeResponse(500, payload=_REDUCE_DATA_BODY),
            _FakeResponse(200, payload={"data": [{"id": "1"}]}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            result = meta_service._graph_get(
                "/act_1/ads", access_token="tok", params={"limit": 50}, env=_ENV
            )
        self.assertEqual(result, {"data": [{"id": "1"}]})
        self.assertEqual(client.calls, 2)
        # first page requested at 50, retried at 25 (halved)
        self.assertEqual(client.requests[0][1]["limit"], 50)
        self.assertEqual(client.requests[1][1]["limit"], 25)

    def test_reduce_data_shrinks_limit_baked_into_cursor_url(self):
        # Later pages carry the limit in the pagination cursor URL, not params.
        cursor = "https://graph.facebook.com/v21.0/act_1/ads?limit=50&after=CURSOR"
        client = _FakeClient([
            _FakeResponse(500, payload=_REDUCE_DATA_BODY),
            _FakeResponse(200, payload={"data": []}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            result = meta_service._graph_get(cursor, access_token="tok", env=_ENV)
        self.assertEqual(result, {"data": []})
        retry_url, retry_params = client.requests[1]
        # the reduced limit moves into params and is stripped from the URL so
        # httpx can't send two conflicting `limit` values; the cursor is kept.
        self.assertEqual(retry_params["limit"], 25)
        self.assertNotIn("limit=", retry_url)
        self.assertIn("after=CURSOR", retry_url)

    def test_persistent_reduce_data_shrinks_each_attempt_then_raises(self):
        client = _FakeClient(
            [_FakeResponse(500, payload=_REDUCE_DATA_BODY)] * meta_service._MAX_ATTEMPTS
        )
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                meta_service._graph_get(
                    "/act_1/ads", access_token="tok", params={"limit": 50}, env=_ENV
                )
        self.assertIn("500", str(ctx.exception))
        self.assertEqual(client.calls, meta_service._MAX_ATTEMPTS)
        # every retry halved the page: 50 -> 25 -> 12 -> 6
        limits = [req[1].get("limit") for req in client.requests]
        self.assertEqual(limits, [50, 25, 12, 6])


if __name__ == "__main__":
    unittest.main()
