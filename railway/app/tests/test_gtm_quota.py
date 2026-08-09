from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import gtm_quota  # noqa: E402


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Stands in for a Postgres connection holding the shared bucket row."""

    def __init__(self, row=None, *, fail=False):
        self.row = row
        self.fail = fail
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("connection reset")
        self.statements.append(" ".join(sql.split()))
        if "SELECT tokens" in sql:
            return _FakeCursor(self.row)
        if "SELECT cooldown_until" in sql:
            return _FakeCursor((self.row[2],) if self.row else None)
        return _FakeCursor(None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _conn_cm(conn):
    class _CM:
        def __enter__(self):
            return conn

        def __exit__(self, *a):
            return False

    return _CM()


class GtmQuotaLocalTests(unittest.TestCase):
    """With no Postgres the governor must still limit — degrading to a
    per-process bucket is wrong by a factor of workers, but failing *open*
    against a 0.25 QPS ceiling would guarantee the 403s this exists to stop."""

    def setUp(self):
        gtm_quota.reset()
        self.addCleanup(gtm_quota.reset)
        self._nodb = patch.object(gtm_quota.db, "get_db_url", return_value=None)
        self._nodb.start()
        self.addCleanup(self._nodb.stop)

    def test_burst_is_capped_then_paced(self):
        with patch.object(gtm_quota, "capacity", return_value=3.0), \
                patch.object(gtm_quota, "refill_per_second", return_value=0.25), \
                patch.object(gtm_quota, "max_wait_seconds", return_value=0.0):
            for _ in range(3):
                gtm_quota.acquire()
            with self.assertRaises(gtm_quota.GTMRateLimited) as ctx:
                gtm_quota.acquire()
        self.assertAlmostEqual(ctx.exception.retry_after, 4.0, delta=0.5)

    def test_waiting_callers_are_admitted_once_tokens_refill(self):
        with patch.object(gtm_quota, "capacity", return_value=1.0), \
                patch.object(gtm_quota, "refill_per_second", return_value=100.0):
            gtm_quota.acquire()
            gtm_quota.acquire()  # refills in ~10ms; must not raise

    def test_cooldown_blocks_without_consuming_a_token(self):
        gtm_quota.note_rate_limited(30)
        self.assertGreater(gtm_quota.cooldown_remaining(), 29)
        with self.assertRaises(gtm_quota.GTMRateLimited) as ctx:
            gtm_quota.acquire()
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_cooldown_floor_is_the_configured_minimum(self):
        """Google's Retry-After can be short; the sliding window still needs to
        drain, so the cooldown never dips below the configured floor."""
        with patch.object(gtm_quota, "cooldown_seconds", return_value=45.0):
            gtm_quota.note_rate_limited(1)
        self.assertGreater(gtm_quota.cooldown_remaining(), 40)


class GtmQuotaSharedTests(unittest.TestCase):
    """The bucket lives in Postgres so every Railway worker draws on one
    project-wide budget — a per-process limiter would let N workers each run at
    the full rate and blow the quota N times over."""

    def setUp(self):
        gtm_quota.reset()
        self.addCleanup(gtm_quota.reset)
        self._db = patch.object(gtm_quota.db, "get_db_url", return_value="postgres://x")
        self._db.start()
        self.addCleanup(self._db.stop)
        self._schema = patch.object(gtm_quota, "ensure_schema", return_value=True)
        self._schema.start()
        self.addCleanup(self._schema.stop)

    def test_token_is_taken_under_row_lock(self):
        now = datetime.now(tz=UTC)
        conn = _FakeConn(row=(5.0, now, None))
        with patch.object(gtm_quota.db, "connection", return_value=_conn_cm(conn)):
            gtm_quota.acquire()
        select = next(s for s in conn.statements if s.startswith("SELECT tokens"))
        self.assertIn("FOR UPDATE", select,
                      "concurrent workers must serialise on the bucket row")
        update = next(s for s in conn.statements if s.startswith("UPDATE"))
        self.assertIn("SET tokens", update)

    def test_empty_shared_bucket_makes_the_caller_wait(self):
        now = datetime.now(tz=UTC)
        conn = _FakeConn(row=(0.0, now, None))
        with patch.object(gtm_quota.db, "connection", return_value=_conn_cm(conn)), \
                patch.object(gtm_quota, "max_wait_seconds", return_value=0.0):
            with self.assertRaises(gtm_quota.GTMRateLimited):
                gtm_quota.acquire()

    def test_cooldown_set_by_another_worker_is_honoured(self):
        now = datetime.now(tz=UTC)
        conn = _FakeConn(row=(20.0, now, now + timedelta(seconds=45)))
        with patch.object(gtm_quota.db, "connection", return_value=_conn_cm(conn)):
            with self.assertRaises(gtm_quota.GTMRateLimited) as ctx:
                gtm_quota.acquire()
        self.assertGreater(ctx.exception.retry_after, 40)

    def test_db_failure_falls_back_to_the_local_bucket(self):
        """A degraded Postgres must not remove the limit — that is exactly when
        a retry storm would otherwise run unmetered."""
        conn = _FakeConn(fail=True)
        with patch.object(gtm_quota.db, "connection", return_value=_conn_cm(conn)), \
                patch.object(gtm_quota, "capacity", return_value=1.0), \
                patch.object(gtm_quota, "refill_per_second", return_value=0.25), \
                patch.object(gtm_quota, "max_wait_seconds", return_value=0.0):
            gtm_quota.acquire()  # served by the in-process bucket
            with self.assertRaises(gtm_quota.GTMRateLimited):
                gtm_quota.acquire()

    def test_cooldown_is_held_locally_when_the_db_write_fails(self):
        conn = _FakeConn(fail=True)
        with patch.object(gtm_quota.db, "connection", return_value=_conn_cm(conn)):
            gtm_quota.note_rate_limited(30)
            # The DB read fails too, so cooldown_remaining falls through to the
            # local copy — the breaker survives a Postgres outage.
            self.assertGreater(gtm_quota.cooldown_remaining(), 25)


if __name__ == "__main__":
    unittest.main()
