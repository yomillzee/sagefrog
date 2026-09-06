from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import gtm_quota  # noqa: E402
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


def _quota_403(reason="rateLimitExceeded", **kwargs):
    """A 403 shaped like Google's quota rejection.

    GTM reports an exhausted quota as 403 with this body — *not* as 429 — which
    is why the classification is body-based rather than status-based.
    """
    return _FakeResponse(
        403,
        payload={"error": {
            "code": 403,
            "message": "Rate Limit Exceeded",
            "errors": [{"domain": "usageLimits", "reason": reason}],
        }},
        **kwargs,
    )


class _FakeClient:
    """Returns queued responses in order for each .get() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None):
        self.calls += 1
        return self._responses.pop(0)


class _GtmTestCase(unittest.TestCase):
    """Base for GTM tests: clears the caches and the process-wide quota
    governor, whose token bucket and breaker would otherwise leak between
    tests (a persistent-429 test parks every later caller for a minute)."""

    def setUp(self):
        gtm_service._containers_cache.clear()
        gtm_service._containers_locks.clear()
        gtm_service._cache.clear()
        gtm_service._access_tokens.clear()
        gtm_quota.reset()
        # These tests exercise gtm_service's own logic — keep the shared
        # Postgres cache out of it (there is no DB in the test environment, but
        # be explicit rather than relying on that).
        self._nodb = patch.object(gtm_service.db_cache, "_get_db_url", return_value=None)
        self._nodb.start()
        self.addCleanup(self._nodb.stop)


class GtmRateLimitTests(_GtmTestCase):
    """A GTM rate-limit response must retry with backoff and, if it persists,
    surface a friendly message — never httpx's raw 'Client error 429'."""

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
        sleep.assert_called_once()
        # Retry-After is respected as a floor, plus jitter: the quota is
        # project-wide, so workers throttled in the same second must not all
        # retry in the same second and re-trip it together.
        (waited,) = sleep.call_args.args
        self.assertGreaterEqual(waited, 7.0)
        self.assertLess(waited, 9.0)

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


class GtmQuota403Tests(_GtmTestCase):
    """GTM signals an exhausted quota with **403**, not 429 — the same status it
    uses for "no access to this container". Only the body's reason separates
    them, and getting that wrong is what made quota errors look like permission
    errors (and skip retrying entirely)."""

    def test_quota_403_is_treated_as_a_rate_limit(self):
        client = _FakeClient([
            _quota_403(),
            _FakeResponse(200, payload={"account": []}),
        ])
        with patch.object(gtm_service.time, "sleep") as sleep:
            resp = gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.calls, 2)
        sleep.assert_called_once()

    def test_persistent_quota_403_raises_rate_limit_error(self):
        client = _FakeClient([_quota_403() for _ in range(gtm_service._MAX_ATTEMPTS)])
        with patch.object(gtm_service.time, "sleep"):
            with self.assertRaises(gtm_quota.GTMRateLimited) as ctx:
                gtm_service._gtm_get(client, "https://x/accounts", "tok")
        msg = str(ctx.exception)
        self.assertIn("rate-limiting", msg)
        # Must not tell the user to fix their scopes — this is quota.
        self.assertNotIn("scope", msg.lower())

    def test_permission_403_is_not_retried(self):
        """A 403 whose reason isn't a quota reason is a real access problem and
        must still reach the caller untouched, so the wizard can say so."""
        client = _FakeClient([_quota_403(reason="insufficientPermissions")])
        with patch.object(gtm_service.time, "sleep") as sleep:
            resp = gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(client.calls, 1)
        sleep.assert_not_called()

    def test_daily_quota_403_is_treated_as_a_rate_limit(self):
        client = _FakeClient([
            _quota_403(reason="dailyLimitExceeded"),
            _FakeResponse(200, payload={}),
        ])
        with patch.object(gtm_service.time, "sleep"):
            resp = gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(resp.status_code, 200)


class GtmQuotaGovernorTests(_GtmTestCase):
    """GTM allows 0.25 requests/second for the whole *project*, so the limiter
    has to be global — not per client, per token, or per fan-out."""

    def test_requests_are_metered_by_the_governor(self):
        client = _FakeClient([_FakeResponse(200, payload={}) for _ in range(3)])
        with patch.object(gtm_quota, "acquire") as acquire:
            for _ in range(3):
                gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(acquire.call_count, 3)

    def test_bucket_paces_calls_once_the_burst_is_spent(self):
        """Past the burst allowance, callers wait ~1/QPS between requests."""
        with patch.dict(os.environ, {"GTM_QUOTA_BURST": "2", "GTM_QUOTA_QPS": "0.25"}):
            gtm_quota.reset()
            with patch.object(gtm_quota.time, "sleep") as sleep:
                gtm_quota.acquire()   # burst token 1 — free
                gtm_quota.acquire()   # burst token 2 — free
                self.assertEqual(sleep.call_count, 0)
                # The bucket is empty; the next caller has to wait out a refill.
                # Patched sleep doesn't advance time, so acquire() would spin —
                # let it give up instead and assert on the wait it wanted.
                with patch.object(gtm_quota, "max_wait_seconds", return_value=0.0):
                    with self.assertRaises(gtm_quota.GTMRateLimited) as ctx:
                        gtm_quota.acquire()
        # 1 / 0.25 QPS = one slot every 4 seconds.
        self.assertAlmostEqual(ctx.exception.retry_after, 4.0, delta=0.5)

    def test_breaker_parks_every_caller_after_a_rejection(self):
        """Once Google has actually refused us, continuing to spend quota only
        lengthens the outage — so *all* callers stop, not just the one that
        got the 403."""
        gtm_quota.note_rate_limited(30)
        self.assertGreater(gtm_quota.cooldown_remaining(), 0)

        client = _FakeClient([_FakeResponse(200, payload={})])
        with self.assertRaises(gtm_quota.GTMRateLimited):
            gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(client.calls, 0, "breaker must short-circuit before the HTTP call")

    def test_success_lifts_the_breaker(self):
        gtm_quota.note_rate_limited(30)
        gtm_quota.note_success()
        self.assertEqual(gtm_quota.cooldown_remaining(), 0.0)

        # Separate concern: the bucket also ramps back up rather than restoring
        # its burst (see test_recovery_ramps_instead_of_bursting). Give it a
        # fresh window so this test measures the breaker and nothing else.
        gtm_quota.reset()
        client = _FakeClient([_FakeResponse(200, payload={})])
        resp = gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertEqual(resp.status_code, 200)

    def test_recovery_ramps_instead_of_bursting(self):
        """After a rejection the bucket restarts empty, so the first requests
        back trickle in at the sustained rate. Handing back a full 20-request
        burst the moment the cooldown lifts would re-trip the limit on a quota
        that has only just recovered."""
        gtm_quota.note_rate_limited(30)
        gtm_quota.note_success()  # cooldown elapsed
        with patch.object(gtm_quota, "max_wait_seconds", return_value=0.0):
            with self.assertRaises(gtm_quota.GTMRateLimited) as ctx:
                gtm_quota.acquire()
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_exhausted_retries_trip_the_breaker(self):
        client = _FakeClient([_quota_403() for _ in range(gtm_service._MAX_ATTEMPTS)])
        with patch.object(gtm_service.time, "sleep"):
            with self.assertRaises(gtm_quota.GTMRateLimited):
                gtm_service._gtm_get(client, "https://x/accounts", "tok")
        self.assertGreater(
            gtm_quota.cooldown_remaining(), 0,
            "a rate limit that survives retries must park later callers",
        )


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


class GtmContainerCacheTests(_GtmTestCase):
    """list_containers fans out one request per account — the burst that trips
    GTM's quota. The wizard re-fetches on every render, so the result is cached
    briefly per token to keep a single session from re-tripping the limit."""

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

        # The exhausted retries tripped the shared breaker and drained the
        # bucket; stand in for both recovering so this test measures the cache.
        gtm_quota.reset()

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


class GtmFanoutPacingTests(_GtmTestCase):
    """The per-account fan-out is the burst that trips GTM's quota. Pacing it
    with a local sleep was never enough — the 0.25 QPS ceiling is project-wide,
    so the fan-out shares it with every other client and worker. Every request
    now goes through the governor, which is the only thing that sees them all."""

    def test_every_fanout_request_takes_a_quota_token(self):
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
                patch.object(gtm_quota, "acquire") as acquire:
            gtm_service.list_containers("refresh-tok")
        # One accounts call + one containers call per account.
        self.assertEqual(client.calls, 4)
        self.assertEqual(acquire.call_count, 4)

    def test_rate_limited_fanout_falls_back_to_the_last_listing(self):
        """A wizard that already listed containers must not break when the
        refresh is throttled — the previous listing still lets the user pick."""
        ok = _FakeClient(_accounts_fanout_responses())
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(ok)):
            first = gtm_service.list_containers("refresh-tok")

        throttled = _FakeClient([_quota_403() for _ in range(gtm_service._MAX_ATTEMPTS)])
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service.httpx, "Client", return_value=_cm(throttled)), \
                patch.object(gtm_service.time, "sleep"):
            second = gtm_service.list_containers("refresh-tok", force_refresh=True)
        self.assertEqual(second, first)


class GtmStaleServeTests(_GtmTestCase):
    """The tag audit page must survive a rate limit. Erroring the page sends the
    user straight back to the refresh button, which spends more of the quota
    that was already exhausted."""

    _RAW = {"containerVersionId": "7", "tag": [{"name": "GA4", "type": "gaawe"}], "trigger": []}

    def _fetch_once(self):
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version", return_value=self._RAW):
            return gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")

    def test_serves_last_audit_when_rate_limited(self):
        fresh = self._fetch_once()
        self.assertEqual(fresh["tag_count"], 1)
        self.assertNotIn("stale", fresh)

        limited = gtm_quota.GTMRateLimited("quota", retry_after=30)
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version", side_effect=limited):
            served = gtm_service.get_live_tags(
                "acme", "1", "2", "refresh-tok", force_refresh=True
            )
        self.assertEqual(served["tag_count"], 1)
        self.assertTrue(served["stale"], "a stale answer must say so")
        self.assertEqual(served["fetched_at"], fresh["fetched_at"])

    def test_rate_limit_propagates_when_nothing_is_cached(self):
        """With no prior audit there is nothing honest to show — the caller has
        to see the rate limit so it can return 429 rather than an empty page."""
        limited = gtm_quota.GTMRateLimited("quota", retry_after=30)
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version", side_effect=limited):
            with self.assertRaises(gtm_quota.GTMRateLimited):
                gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")

    def test_expired_cache_is_still_usable_as_a_stale_fallback(self):
        self._fetch_once()
        # Age the entry past the fresh TTL but within the stale window.
        fetched_at, payload = gtm_service._cache[("acme", "2")]
        gtm_service._cache[("acme", "2")] = (
            fetched_at - gtm_service._CACHE_TTL - timedelta(minutes=1), payload,
        )
        limited = gtm_quota.GTMRateLimited("quota", retry_after=30)
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version", side_effect=limited):
            served = gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")
        self.assertTrue(served["stale"])
        self.assertEqual(served["tag_count"], 1)

    def test_stale_fallback_expires(self):
        """Past _STALE_MAX_AGE a tag audit is too old to pass off as current."""
        self._fetch_once()
        fetched_at, payload = gtm_service._cache[("acme", "2")]
        gtm_service._cache[("acme", "2")] = (
            fetched_at - gtm_service._STALE_MAX_AGE - timedelta(minutes=1), payload,
        )
        limited = gtm_quota.GTMRateLimited("quota", retry_after=30)
        with patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version", side_effect=limited):
            with self.assertRaises(gtm_quota.GTMRateLimited):
                gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")


class _FakeHit:
    """Stands in for db_cache.CacheHit."""

    def __init__(self, response_json, *, age_seconds=0.0, is_stale=False):
        self.response_json = response_json
        self.age_seconds = age_seconds
        self.is_stale = is_stale
        self.row_count = 0
        self.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        self.expires_at = self.created_at


class GtmSharedCacheTests(_GtmTestCase):
    """Railway runs several workers against one project quota. A cache that
    lives only in process memory means each worker pays the full fan-out — the
    shared Postgres layer is what makes N workers cost one read."""

    _RAW = {"containerVersionId": "7", "tag": [{"name": "GA4", "type": "gaawe"}], "trigger": []}

    def test_live_tags_served_from_another_workers_read(self):
        cached = {"fetched_at": "2026-01-01T00:00:00+00:00", "container_version": "7",
                  "tag_count": 3, "rows": []}
        with patch.object(gtm_service.db_cache, "get_cached",
                          return_value=_FakeHit(cached)) as get_cached, \
                patch.object(gtm_service, "_fetch_live_version") as fetch:
            result = gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")
        self.assertEqual(result["tag_count"], 3)
        fetch.assert_not_called()
        self.assertEqual(get_cached.call_args.args[0], "gtm.live_tags")

    def test_promoted_row_keeps_its_real_age(self):
        """Promoting a shared row into the in-process cache must not restart its
        TTL, or a row could be served for two full windows instead of one."""
        cached = {"fetched_at": "x", "tag_count": 1, "rows": []}
        with patch.object(gtm_service.db_cache, "get_cached",
                          return_value=_FakeHit(cached, age_seconds=600)), \
                patch.object(gtm_service, "_fetch_live_version"):
            gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")
        fetched_at, _ = gtm_service._cache[("acme", "2")]
        age = (datetime.now(UTC) - fetched_at).total_seconds()
        self.assertGreater(age, 590, "promotion must preserve the original fetch time")

    def test_fetch_publishes_to_the_shared_cache(self):
        with patch.object(gtm_service.db_cache, "get_cached", return_value=None), \
                patch.object(gtm_service.db_cache, "put_cached") as put, \
                patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version", return_value=self._RAW):
            gtm_service.get_live_tags("acme", "1", "2", "refresh-tok")
        put.assert_called_once()
        self.assertEqual(put.call_args.args[0], "gtm.live_tags")

    def test_container_cache_key_never_stores_the_refresh_token(self):
        """api_cache keeps request_json in plaintext, so the key is a digest —
        a refresh token is a credential, not cache key material."""
        payload = gtm_service._containers_cache_payload("super-secret-token")
        self.assertNotIn("super-secret-token", str(payload))
        self.assertEqual(
            payload,
            gtm_service._containers_cache_payload("super-secret-token"),
            "the digest must be stable or the cache never hits",
        )

    def test_shared_stale_row_is_marked_stale(self):
        cached = {"fetched_at": "x", "tag_count": 2, "rows": []}
        with patch.object(gtm_service.db_cache, "get_cached",
                          return_value=_FakeHit(cached, age_seconds=3600, is_stale=True)), \
                patch.object(gtm_service, "_get_access_token", return_value="tok"), \
                patch.object(gtm_service, "_fetch_live_version",
                             side_effect=gtm_quota.GTMRateLimited("quota")):
            result = gtm_service.get_live_tags(
                "acme", "1", "2", "refresh-tok", force_refresh=True
            )
        self.assertTrue(result["stale"])
        self.assertEqual(result["tag_count"], 2)


class GtmAccessTokenCacheTests(_GtmTestCase):
    """Every GTM call used to mint a fresh access token, doubling the round
    trips for no benefit — Google's tokens last an hour."""

    def test_token_is_reused_across_calls(self):
        with patch("ga4_reporting_service._get_access_token", return_value="tok") as mint:
            self.assertEqual(gtm_service._get_access_token("refresh-tok"), "tok")
            self.assertEqual(gtm_service._get_access_token("refresh-tok"), "tok")
        mint.assert_called_once()

    def test_rejected_token_is_dropped(self):
        with patch("ga4_reporting_service._get_access_token", side_effect=["a", "b"]) as mint:
            self.assertEqual(gtm_service._get_access_token("refresh-tok"), "a")
            gtm_service._invalidate_access_token("a")
            self.assertEqual(gtm_service._get_access_token("refresh-tok"), "b")
        self.assertEqual(mint.call_count, 2)


class GtmSingleFlightTests(_GtmTestCase):
    """Overlapping wizard loads (rapid step clicks, multiple workers) must not
    each launch the fan-out — that multiplies the very burst GTM rate-limits on.
    Concurrent callers serialise on a per-token lock and the late one serves the
    first caller's cached result rather than re-fanning."""

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
