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


class MetaErrorMessageTests(unittest.TestCase):
    """A pagination cursor URL carries the access_token and a long `after` blob in
    its query string. The raised error must not leak the token, and must stay
    compact so callers that truncate it to ~400 chars still show Meta's body."""

    def test_error_on_cursor_url_drops_query_and_keeps_meta_body(self):
        secret = "EAAL2HvUw2xcSECRETTOKENvalue123"
        long_after = "QVFI" + "x" * 300  # realistic oversized pagination cursor
        cursor = (
            f"https://graph.facebook.com/v25.0/act_1/ads"
            f"?access_token={secret}&limit=25&after={long_after}"
        )
        # A non-rate-limit 400 (code 100) fails fast in one attempt, so we can
        # assert on the raised message shape directly.
        meta_body = {"error": {"code": 100, "message": "Unsupported get request on this edge"}}
        client = _FakeClient([_FakeResponse(400, payload=meta_body)])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                meta_service._graph_get(cursor, access_token=secret, env=_ENV)
        msg = str(ctx.exception)
        self.assertNotIn(secret, msg)
        self.assertNotIn(long_after, msg)  # cursor blob dropped, not just the token
        self.assertIn("act_1/ads", msg)
        # Meta's own error body survives the caller's 400-char truncation.
        self.assertIn("Unsupported get request on this edge", msg[:400])


class MetaRateLimitTests(unittest.TestCase):
    """Meta returns throttling as HTTP 400 with a rate-limit error code, not a
    429. _graph_get must recognize those and back off/retry rather than failing
    the sync outright on a transient limit."""

    def test_user_request_limit_reached_is_retried(self):
        client = _FakeClient([
            _FakeResponse(400, payload={"error": {"code": 17, "message": "User request limit reached"}}),
            _FakeResponse(200, payload={"data": [{"id": "1"}]}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep") as sleep:
            result = meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
        self.assertEqual(result, {"data": [{"id": "1"}]})
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_business_use_case_rate_limit_code_is_retried(self):
        client = _FakeClient([
            _FakeResponse(400, payload={"error": {"code": 80004, "message": "There have been too many calls"}}),
            _FakeResponse(200, payload={"data": []}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            result = meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
        self.assertEqual(result, {"data": []})
        self.assertEqual(client.calls, 2)

    def test_rate_limit_backoff_is_capped(self):
        # A huge Retry-After must not make the worker sleep for minutes.
        client = _FakeClient([
            _FakeResponse(400, headers={"Retry-After": "600"},
                          payload={"error": {"code": 17, "message": "User request limit reached"}}),
            _FakeResponse(200, payload={}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep") as sleep:
            meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
        sleep.assert_called_once_with(meta_service._RATE_LIMIT_MAX_BACKOFF_SECONDS)

    def test_persistent_rate_limit_eventually_raises(self):
        client = _FakeClient(
            [_FakeResponse(400, payload={"error": {"code": 17, "message": "User request limit reached"}})]
            * meta_service._MAX_ATTEMPTS
        )
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(client.calls, meta_service._MAX_ATTEMPTS)

    def test_non_rate_limit_400_still_fails_fast(self):
        client = _FakeClient([
            _FakeResponse(400, payload={"error": {"code": 100, "message": "Invalid parameter"}}),
        ])
        with patch.object(meta_service.httpx, "Client", return_value=client), \
                patch.object(meta_service.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError):
                meta_service._graph_get("/act_1/ads", access_token="tok", env=_ENV)
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
