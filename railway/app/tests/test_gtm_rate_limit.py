from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import gtm_service  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, *, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Returns queued responses in order for each .get() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None):
        self.calls += 1
        return self._responses.pop(0)


class GtmRateLimitTests(unittest.TestCase):
    """A GTM 429 (low per-minute quota) must retry with backoff and, if it
    persists, surface a friendly message — never httpx's raw 'Client error 429'."""

    def test_retries_transient_then_succeeds(self):
        client = _FakeClient([
            _FakeResponse(429),
            _FakeResponse(503),
            _FakeResponse(200, payload={"account": []}),
        ])
        with patch.object(gtm_service.time, "sleep") as sleep:
            resp = gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.calls, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_persistent_429_raises_friendly_error(self):
        client = _FakeClient([_FakeResponse(429) for _ in range(gtm_service._MAX_ATTEMPTS)])
        with patch.object(gtm_service.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                gtm_service._gtm_get(client, "https://x/accounts", "tok")
        msg = str(ctx.exception)
        self.assertIn("rate-limiting", msg)
        self.assertIn("429", msg)
        self.assertNotIn("Client error", msg)

    def test_honours_retry_after_header(self):
        client = _FakeClient([
            _FakeResponse(429, headers={"Retry-After": "7"}),
            _FakeResponse(200, payload={}),
        ])
        with patch.object(gtm_service.time, "sleep") as sleep:
            gtm_service._gtm_get(client, "https://x/accounts", "tok")
        sleep.assert_called_once_with(7.0)

    def test_non_retryable_status_returned_immediately(self):
        client = _FakeClient([_FakeResponse(403)])
        with patch.object(gtm_service.time, "sleep") as sleep:
            resp = gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(client.calls, 1)
        sleep.assert_not_called()

    def test_list_containers_surfaces_friendly_429(self):
        client = _FakeClient([_FakeResponse(429) for _ in range(gtm_service._MAX_ATTEMPTS)])

        class _CM:
            def __enter__(self_inner):
                return client

            def __exit__(self_inner, *a):
                return False

        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_CM()), \
                patch.object(gtm_service.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                gtm_service.list_containers("refresh-tok")
        self.assertIn("rate-limiting", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
