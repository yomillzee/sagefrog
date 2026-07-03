from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import cron_locks  # noqa: E402


class _FakeCursor:
    def __init__(self, fetchone_result=None):
        self._fetchone_result = fetchone_result

    def fetchone(self):
        return self._fetchone_result


class _FakeConn:
    """Records every executed statement; lets each test script canned results."""

    def __init__(self, results: list):
        self.calls: list[tuple[str, tuple]] = []
        self._results = list(results)

    def execute(self, sql, params=()):
        self.calls.append((sql.strip(), params))
        result = self._results.pop(0) if self._results else None
        return _FakeCursor(result)


class CronLocksTests(unittest.TestCase):
    def test_disabled_without_database_url_always_acquires(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(cron_locks.enabled())
            self.assertTrue(cron_locks.try_acquire("some-job"))
            # No-op release must not raise either.
            cron_locks.release("some-job")

    def test_acquire_succeeds_when_lock_is_free(self) -> None:
        # First call: DELETE (no fetchone use), second: INSERT ... RETURNING name (row found).
        fake_conn = _FakeConn(results=[None, ("some-job",)])

        @contextmanager
        def fake_connection():
            yield fake_conn

        with patch.dict("os.environ", {"DATABASE_URL": "postgres://fake"}, clear=True), \
             patch("cron_locks.db.connection", fake_connection), \
             patch("cron_locks.ensure_schema", return_value=True):
            acquired = cron_locks.try_acquire("some-job", locked_by="test")

        self.assertTrue(acquired)
        self.assertTrue(any("INSERT INTO cron_locks" in sql for sql, _ in fake_conn.calls))

    def test_acquire_fails_when_lock_is_already_held(self) -> None:
        # INSERT ... ON CONFLICT DO NOTHING returns no row when the lock is taken.
        fake_conn = _FakeConn(results=[None, None])

        @contextmanager
        def fake_connection():
            yield fake_conn

        with patch.dict("os.environ", {"DATABASE_URL": "postgres://fake"}, clear=True), \
             patch("cron_locks.db.connection", fake_connection), \
             patch("cron_locks.ensure_schema", return_value=True):
            acquired = cron_locks.try_acquire("some-job")

        self.assertFalse(acquired)

    def test_release_issues_a_delete(self) -> None:
        fake_conn = _FakeConn(results=[None])

        @contextmanager
        def fake_connection():
            yield fake_conn

        with patch.dict("os.environ", {"DATABASE_URL": "postgres://fake"}, clear=True), \
             patch("cron_locks.db.connection", fake_connection):
            cron_locks.release("some-job")

        self.assertEqual(len(fake_conn.calls), 1)
        sql, params = fake_conn.calls[0]
        self.assertIn("DELETE FROM cron_locks", sql)
        self.assertEqual(params, ("some-job",))


if __name__ == "__main__":
    unittest.main()
