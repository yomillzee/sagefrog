from __future__ import annotations

import sys
import threading
import time
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

    def setUp(self):
        gtm_service._containers_cache.clear()

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


def _cm(client):
    class _CM:
        def __enter__(self):
            return client

        def __exit__(self, *a):
            return False

    return _CM()


def _accounts_fanout_responses():
    """One account with one container — the minimal successful fan-out."""
    return [
        _FakeResponse(200, payload={"account": [{"accountId": "1", "name": "Acct"}]}),
        _FakeResponse(200, payload={"container": [
            {"containerId": "2", "name": "Cont", "publicId": "GTM-X"},
        ]}),
    ]


class GtmContainerCacheTests(unittest.TestCase):
    """list_containers fans out one request per account — the burst that trips
    GTM's per-minute quota. The wizard re-fetches on every render, so the result
    is cached briefly per token to keep a single session from re-tripping the 429."""

    def setUp(self):
        gtm_service._containers_cache.clear()

    def test_second_call_served_from_cache_without_api_calls(self):
        client = _FakeClient(_accounts_fanout_responses())
        with patch.object(gtm_service, "_get_access_token", return_value="tok") as tok, \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(client)):
            first = gtm_service.list_containers("refresh-tok")
            second = gtm_service.list_containers("refresh-tok")

        self.assertEqual(first, second)
        self.assertEqual(first, [{"id": "1:2", "name": "Cont (Acct) • GTM-X"}])
        # The fan-out fired exactly twice (accounts + one containers call) — the
        # second list_containers() hit cache and issued no requests or token refresh.
        self.assertEqual(client.calls, 2)
        tok.assert_called_once()

    def test_force_refresh_bypasses_cache(self):
        client = _FakeClient(_accounts_fanout_responses() + _accounts_fanout_responses())
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(client)):
            gtm_service.list_containers("refresh-tok")
            gtm_service.list_containers("refresh-tok", force_refresh=True)
        self.assertEqual(client.calls, 4)

    def test_failed_fanout_is_not_cached(self):
        # A persistent 429 mid-fan-out must not poison the cache with a partial
        # (or empty) list — the next attempt has to retry the real fan-out.
        failing = _FakeClient([_FakeResponse(429) for _ in range(gtm_service._MAX_ATTEMPTS)])
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(failing)), \
                patch.object(gtm_service.time, "sleep"):
            with self.assertRaises(RuntimeError):
                gtm_service.list_containers("refresh-tok")
        self.assertNotIn("refresh-tok", gtm_service._containers_cache)

        ok = _FakeClient(_accounts_fanout_responses())
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(ok)):
            result = gtm_service.list_containers("refresh-tok")
        self.assertEqual(result, [{"id": "1:2", "name": "Cont (Acct) • GTM-X"}])

    def test_expired_cache_refetches(self):
        from datetime import timedelta

        client = _FakeClient(_accounts_fanout_responses() + _accounts_fanout_responses())
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(client)):
            gtm_service.list_containers("refresh-tok")
            # Age the cache entry past its TTL and confirm the fan-out re-runs.
            fetched_at, cached = gtm_service._containers_cache["refresh-tok"]
            gtm_service._containers_cache["refresh-tok"] = (
                fetched_at - gtm_service._CONTAINERS_CACHE_TTL - timedelta(seconds=1),
                cached,
            )
            gtm_service.list_containers("refresh-tok")
        self.assertEqual(client.calls, 4)


class GtmFanoutPacingTests(unittest.TestCase):
    """The per-account fan-out fired back-to-back is the burst that trips GTM's
    per-user quota. The calls must be spaced apart so the *first* (uncached)
    listing stays under the limit instead of 429-ing before it can cache."""

    def setUp(self):
        gtm_service._containers_cache.clear()
        gtm_service._containers_locks.clear()

    def test_per_account_calls_are_paced(self):
        accounts = {"account": [
            {"accountId": "1", "name": "A"},
            {"accountId": "2", "name": "B"},
            {"accountId": "3", "name": "C"},
        ]}
        client = _FakeClient([
            _FakeResponse(200, payload=accounts),
            _FakeResponse(200, payload={"container": []}),
            _FakeResponse(200, payload={"container": []}),
            _FakeResponse(200, payload={"container": []}),
        ])
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(client)), \
                patch.object(gtm_service.time, "sleep") as sleep:
            gtm_service.list_containers("refresh-tok")
        # Two gaps between three accounts — no sleep before the first account.
        self.assertEqual(sleep.call_count, 2)
        for call in sleep.call_args_list:
            self.assertEqual(call.args, (gtm_service._FANOUT_PACE_SECONDS,))

    def test_single_account_is_not_paced(self):
        client = _FakeClient(_accounts_fanout_responses())
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(client)), \
                patch.object(gtm_service.time, "sleep") as sleep:
            gtm_service.list_containers("refresh-tok")
        sleep.assert_not_called()


class GtmSingleFlightTests(unittest.TestCase):
    """Overlapping wizard loads (rapid step clicks, multiple workers) must not
    each launch the fan-out — that multiplies the very burst GTM rate-limits on.
    Concurrent callers serialise on a per-token lock and the late one serves the
    first caller's cached result rather than re-fanning."""

    def setUp(self):
        gtm_service._containers_cache.clear()
        gtm_service._containers_locks.clear()

    def test_concurrent_callers_share_one_fanout(self):
        gate = threading.Event()
        token_calls: list[str] = []

        def blocking_token(refresh_token):
            # Runs inside the single-flight lock; hold it until the second caller
            # has queued so we can prove it doesn't launch its own fan-out.
            token_calls.append(refresh_token)
            gate.wait(2)
            return "tok"

        client = _FakeClient(_accounts_fanout_responses())
        results: dict[str, list] = {}

        def worker(name):
            results[name] = gtm_service.list_containers("refresh-tok")

        with patch.object(gtm_service, "_get_access_token", side_effect=blocking_token), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(client)):
            t1 = threading.Thread(target=worker, args=("a",))
            t1.start()
            # Wait until t1 is inside the fan-out (token requested → holds lock).
            deadline = time.monotonic() + 2
            while not token_calls and time.monotonic() < deadline:
                time.sleep(0.01)
            t2 = threading.Thread(target=worker, args=("b",))
            t2.start()
            time.sleep(0.1)  # let t2 block on the lock
            gate.set()       # release t1
            t1.join(2)
            t2.join(2)

        self.assertEqual(results["a"], results["b"])
        self.assertEqual(results["a"], [{"id": "1:2", "name": "Cont (Acct) • GTM-X"}])
        # The fan-out (and its token refresh) ran exactly once; t2 hit the cache.
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
