from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import core_routes  # noqa: E402


class _FakeBackgroundTasks:
    """Records the task instead of running it -- we only assert on the
    immediate HTTP response (started vs skipped), not the background work
    itself, which is exercised by the orchestrator/refresh_service tests."""

    def __init__(self):
        self.tasks: list = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


class SyncBqAllConcurrencyTests(unittest.TestCase):
    def test_skips_new_run_when_lock_already_held(self) -> None:
        fake_connector_store = types.SimpleNamespace(
            client_slugs_with_configs=lambda: {"nixon", "andesa"}
        )
        fake_cron_locks = types.SimpleNamespace(try_acquire=lambda *a, **k: False, release=lambda *a, **k: None)
        bg = _FakeBackgroundTasks()

        with patch.dict(sys.modules, {
            "connector_config_store": fake_connector_store,
            "cron_locks": fake_cron_locks,
        }):
            result = core_routes.internal_sync_bq_all(bg, date_range="LAST_30_DAYS")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(bg.tasks, [])  # No background work queued when locked out.

    def test_queues_background_task_when_lock_acquired(self) -> None:
        fake_connector_store = types.SimpleNamespace(
            client_slugs_with_configs=lambda: {"nixon", "andesa"}
        )
        acquired = {"calls": []}
        fake_cron_locks = types.SimpleNamespace(
            try_acquire=lambda *a, **k: acquired["calls"].append((a, k)) or True,
            release=lambda *a, **k: None,
        )
        bg = _FakeBackgroundTasks()

        with patch.dict(sys.modules, {
            "connector_config_store": fake_connector_store,
            "cron_locks": fake_cron_locks,
        }):
            result = core_routes.internal_sync_bq_all(bg, date_range="LAST_30_DAYS")

        self.assertEqual(result["status"], "started")
        self.assertEqual(result["clients_queued"], 2)
        self.assertEqual(len(bg.tasks), 1)
        self.assertEqual(len(acquired["calls"]), 1)

    def test_per_client_sync_runs_concurrently_not_serially(self) -> None:
        """The background task itself must dispatch clients through a bounded
        thread pool rather than looping and blocking one at a time -- this is
        the actual scalability fix, so assert on concurrency directly rather
        than just that all clients eventually get called."""
        import threading

        fake_connector_store = types.SimpleNamespace(
            client_slugs_with_configs=lambda: {"a", "b", "c", "d"}
        )
        fake_cron_locks = types.SimpleNamespace(try_acquire=lambda *a, **k: True, release=lambda *a, **k: None)

        start_barrier = threading.Barrier(4, timeout=5)
        calls: list[str] = []

        def fake_refresh_bq_client(slug, *, date_range, sync_trigger):
            # If sync were serial, the 2nd call would never reach the barrier
            # concurrently with the others and this would time out.
            start_barrier.wait()
            calls.append(slug)

        bg = _FakeBackgroundTasks()
        with patch.dict(sys.modules, {
            "connector_config_store": fake_connector_store,
            "cron_locks": fake_cron_locks,
        }), patch.object(core_routes.dashboard_service, "refresh_bq_client", fake_refresh_bq_client):
            core_routes.internal_sync_bq_all(bg, date_range="LAST_30_DAYS")
            # Run the queued background task synchronously, in this thread's
            # test, exactly as FastAPI would after sending the response.
            fn, args, kwargs = bg.tasks[0]
            fn(*args, **kwargs)

        self.assertEqual(sorted(calls), ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
