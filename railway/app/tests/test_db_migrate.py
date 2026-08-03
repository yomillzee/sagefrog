"""Behavioral tests for the central migration runner (db_migrate).

Uses an in-memory fake connection that simulates just enough of the
schema_migrations ledger to prove the runner's contract on both a fresh and an
already-initialized database, without a real Postgres.
"""

import contextlib

import db
import db_migrate
import web_users  # noqa: F401  (import registers web_users:0001_baseline)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Records DDL + advisory locks; simulates the schema_migrations ledger."""

    def __init__(self, state, executed, locks):
        self.state, self.executed, self.locks = state, executed, locks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        if s.startswith("SELECT pg_advisory_xact_lock"):
            self.locks.append(params[0])
            return _Result([])
        if s.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
            return _Result([])
        if s.startswith("SELECT id FROM schema_migrations"):
            return _Result([(i,) for i in sorted(self.state["ledger"])])
        if s.startswith("INSERT INTO schema_migrations"):
            self.state["ledger"].add(params[0])
            return _Result([])
        # Anything else is real migration DDL.
        self.executed.append(s)
        return _Result([])


def _install_fake_db(monkeypatch, state, executed, locks):
    @contextlib.contextmanager
    def fake_connection():
        yield _FakeConn(state, executed, locks)

    monkeypatch.setattr(db, "connection", fake_connection)
    monkeypatch.setattr(db, "get_db_url", lambda: "postgres://fake/db")


def test_fresh_database_applies_and_records():
    import pytest

    mp = pytest.MonkeyPatch()
    try:
        state, executed, locks = {"ledger": set()}, [], []
        _install_fake_db(mp, state, executed, locks)

        assert db_migrate.run_migrations() is True
        # Shared advisory lock taken exactly once.
        assert locks == [db_migrate.SCHEMA_ADVISORY_LOCK_KEY]
        # Baseline DDL ran (all statements) and was recorded.
        assert len(executed) == len(web_users.SCHEMA_SQL_STATEMENTS)
        assert "web_users:0001_baseline" in state["ledger"]
    finally:
        mp.undo()


def test_already_initialized_database_is_non_destructive_noop():
    import pytest

    mp = pytest.MonkeyPatch()
    try:
        # Ledger already has the baseline (an existing, migrated DB).
        state = {"ledger": {"web_users:0001_baseline"}}
        executed, locks = [], []
        _install_fake_db(mp, state, executed, locks)

        assert db_migrate.run_migrations() is True
        assert locks == [db_migrate.SCHEMA_ADVISORY_LOCK_KEY]  # still serializes
        assert executed == []                                  # but runs NO DDL
        assert state["ledger"] == {"web_users:0001_baseline"}  # unchanged
    finally:
        mp.undo()


def test_running_twice_applies_baseline_once():
    import pytest

    mp = pytest.MonkeyPatch()
    try:
        state, executed, locks = {"ledger": set()}, [], []
        _install_fake_db(mp, state, executed, locks)

        db_migrate.run_migrations()
        first = list(executed)
        db_migrate.run_migrations()  # baseline now recorded -> skipped

        assert executed == first
        assert len(first) == len(web_users.SCHEMA_SQL_STATEMENTS)
    finally:
        mp.undo()


def test_no_database_url_is_a_safe_noop():
    import pytest

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(db, "get_db_url", lambda: None)
        assert db_migrate.run_migrations() is False
    finally:
        mp.undo()


def test_web_users_shares_the_migration_advisory_lock_key():
    # The legacy per-call safeguard and the runner must serialize on one lock.
    assert web_users._SCHEMA_ADVISORY_LOCK_KEY == db_migrate.SCHEMA_ADVISORY_LOCK_KEY


def test_register_is_idempotent_but_rejects_conflicts():
    import pytest

    before = len(db_migrate.registered())
    # Re-registering the identical baseline is a no-op.
    db_migrate.register(
        [db_migrate.Migration("web_users:0001_baseline", tuple(web_users.SCHEMA_SQL_STATEMENTS))]
    )
    assert len(db_migrate.registered()) == before
    # A different body under the same id is a programming error.
    with pytest.raises(ValueError):
        db_migrate.register([db_migrate.Migration("web_users:0001_baseline", ("SELECT 1",))])
