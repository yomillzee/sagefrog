from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402

# db.py funnels every read and write through one `connection()` helper so the app
# can pool instead of opening a TCP connection per query. The pooling was written
# behind `DB_POOL_ENABLED` for a careful rollout and sat off for months — which
# meant the pooled branch, the branch now serving production, had never been
# executed by a test.
#
# What matters here is not the pool's own correctness (psycopg_pool's problem) but
# the two promises db.py's docstring makes about it:
#
#   * with the flag on, connections are actually reused rather than reopened
#   * if the pool cannot be built, the helper falls back to a direct connection
#     and logs — it never raises, because a pooling problem must not become an
#     outage
#
# The second is what made enabling it safe, so it is the more important of the two.

HAS_DB = bool((os.getenv("DATABASE_URL") or "").strip())
requires_db = unittest.skipUnless(HAS_DB, "DATABASE_URL is not set")


class PoolFlagTest(unittest.TestCase):
    def test_the_flag_accepts_the_usual_spellings(self):
        for raw in ("1", "true", "TRUE", "yes", " 1 "):
            with self.subTest(value=raw), mock.patch.dict(os.environ, {"DB_POOL_ENABLED": raw}):
                self.assertTrue(db._pool_enabled())

    def test_anything_else_leaves_pooling_off(self):
        for raw in ("", "0", "false", "no", "off", "maybe"):
            with self.subTest(value=raw), mock.patch.dict(os.environ, {"DB_POOL_ENABLED": raw}):
                self.assertFalse(db._pool_enabled())

    def test_tunables_fall_back_to_their_defaults_when_unset_or_junk(self):
        with mock.patch.dict(os.environ, {"DB_POOL_MAX_SIZE": "25"}):
            self.assertEqual(db._env_int("DB_POOL_MAX_SIZE", 10), 25)
        for raw in ("", "   ", "ten", "1.5"):
            with self.subTest(value=raw), mock.patch.dict(os.environ, {"DB_POOL_MAX_SIZE": raw}):
                self.assertEqual(db._env_int("DB_POOL_MAX_SIZE", 10), 10)


class PoolFallbackTest(unittest.TestCase):
    """The reason enabling the flag was safe. Each of these must degrade to a
    direct connection rather than fail the request."""

    def setUp(self):
        self._saved, db._pool = db._pool, None

    def tearDown(self):
        db._pool = self._saved

    def test_no_database_url_means_no_pool_and_no_error(self):
        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "1"}, clear=False), \
             mock.patch.object(db, "get_db_url", return_value=None):
            self.assertIsNone(db._get_pool())

    def test_a_missing_psycopg_pool_falls_back_and_says_so(self):
        real_import = __import__

        def blocked(name, *args, **kwargs):
            if name == "psycopg_pool":
                raise ImportError("simulated: not installed")
            return real_import(name, *args, **kwargs)

        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "1"}), \
             mock.patch.object(db, "get_db_url", return_value="postgres:///unused"), \
             mock.patch("builtins.__import__", side_effect=blocked), \
             self.assertLogs(db.log, level="WARNING") as captured:
            self.assertIsNone(db._get_pool())
        self.assertTrue(
            any("psycopg_pool is not installed" in line for line in captured.output),
            captured.output,
        )

    def test_a_pool_that_cannot_open_falls_back_and_says_so(self):
        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "1"}), \
             mock.patch.object(db, "get_db_url", return_value="postgres:///unused"), \
             mock.patch("psycopg_pool.ConnectionPool", side_effect=RuntimeError("simulated")), \
             self.assertLogs(db.log, level="ERROR") as captured:
            self.assertIsNone(db._get_pool())
        self.assertTrue(
            any("failed to open Postgres connection pool" in line for line in captured.output),
            captured.output,
        )


@requires_db
class PooledConnectionTest(unittest.TestCase):
    """Exercises the branch that now serves production, against a real database."""

    def setUp(self):
        self._saved, db._pool = db._pool, None

    def tearDown(self):
        pool, db._pool = db._pool, self._saved
        if pool is not None and pool is not self._saved:
            try:
                pool.close()
            except Exception:
                # Teardown of a test pool; the assertions have already run.
                pass

    def test_queries_work_through_the_pool(self):
        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "1"}):
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)

    def test_connections_are_reused_rather_than_reopened(self):
        """The whole point: ten sequential queries must not cost ten connections."""
        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "1"}):
            for _ in range(10):
                with db.connection() as conn:
                    conn.execute("SELECT 1")
            stats = db._get_pool().get_stats()
        self.assertGreaterEqual(stats["requests_num"], 10)
        self.assertLessEqual(
            stats["connections_num"],
            4,
            f"10 sequential queries opened {stats['connections_num']} connections; "
            "they should be reused",
        )

    def test_the_pool_respects_its_configured_ceiling(self):
        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "1", "DB_POOL_MAX_SIZE": "3"}):
            with db.connection() as conn:
                conn.execute("SELECT 1")
            self.assertEqual(db._get_pool().get_stats()["pool_max"], 3)

    def test_the_unpooled_path_still_works(self):
        """The flag has to remain a switch, not a one-way door — this is the
        rollback if pooling ever needs turning off in a hurry."""
        with mock.patch.dict(os.environ, {"DB_POOL_ENABLED": "0"}):
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            self.assertIsNone(db._pool)


if __name__ == "__main__":
    unittest.main()
