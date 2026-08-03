"""Centralized, versioned schema migrations.

Phase 0/1 of the plan in ``docs/schema-migrations.md``. This adds the migration
*runner* and a ``schema_migrations`` ledger, and lets modules register their
schema as ordered, forward-only migrations. It runs once at startup and applies
only migrations not yet recorded in the ledger.

Deliberately additive and non-destructive: this does not remove any existing
per-call ``ensure_schema()`` safeguard. Modules keep working exactly as before;
the runner just guarantees the same schema is present, recorded, and applied at
most once ever (not once per process, not once per request).

Safety properties:
  * **One advisory lock for all schema DDL.** ``run_migrations()`` and every
    legacy ``ensure_schema()`` that opts in take the *same* transaction-scoped
    advisory lock (``SCHEMA_ADVISORY_LOCK_KEY``), so during the transition the
    old and new paths can never run table-locking DDL concurrently and
    reintroduce the deadlock #297 fixed.
  * **Run-then-record, never fake-apply.** A migration is recorded only after its
    statements actually run, so a database that missed an earlier change still
    converges. Baselines are idempotent (``IF NOT EXISTS`` / ordered
    ``DROP IF EXISTS`` + ``ADD`` pairs), so re-running them on an
    already-initialized database preserves all data.
  * **Fresh and existing databases.** On a fresh DB the baseline creates
    everything; on an existing DB it is a non-destructive no-op that is then
    recorded.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import psycopg

import db

log = logging.getLogger(__name__)

# Canonical key for the schema-migration advisory lock. Seeded from the value
# web_users has used since #297 so adopting the runner does not change which lock
# that module takes — the runner and web_users.ensure_schema() share one lock.
SCHEMA_ADVISORY_LOCK_KEY = 4_021_769_001

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  id         TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  checksum   TEXT
)
"""


@dataclass(frozen=True)
class Migration:
    """One forward-only schema step.

    ``id`` is globally unique and sorts in apply order — use
    ``"<module>:<NNNN>_<slug>"`` (e.g. ``"web_users:0001_baseline"``).
    ``statements`` is idempotent, non-destructive DDL run in order. ``run`` is an
    optional hook for steps that can't be expressed as plain SQL; it receives the
    open connection and runs inside the same migration transaction.
    """

    id: str
    statements: tuple[str, ...] = ()
    run: Callable[[psycopg.Connection], None] | None = field(default=None, compare=False)


_registry: list[Migration] = []
_registry_lock = threading.Lock()


def register(migrations: Iterable[Migration]) -> None:
    """Register migrations to be applied by ``run_migrations()``.

    Idempotent per id, so re-importing a module (or a test reload) does not
    double-register. Raises if two different migrations claim the same id.
    """
    with _registry_lock:
        by_id = {m.id: m for m in _registry}
        for m in migrations:
            existing = by_id.get(m.id)
            if existing is not None:
                if existing.statements != m.statements:
                    raise ValueError(f"Conflicting migration registered for id {m.id!r}")
                continue
            _registry.append(m)
            by_id[m.id] = m


def registered() -> list[Migration]:
    """A snapshot of registered migrations in apply (sorted-by-id) order."""
    with _registry_lock:
        return sorted(_registry, key=lambda m: m.id)


def _checksum(m: Migration) -> str:
    h = hashlib.sha256()
    for stmt in m.statements:
        h.update(stmt.encode("utf-8"))
        h.update(b"\x00")
    if m.run is not None:
        h.update(getattr(m.run, "__qualname__", "run").encode("utf-8"))
    return h.hexdigest()


def run_migrations() -> bool:
    """Apply all unapplied registered migrations. Returns False if no DB is set.

    Call once at startup. Never call this from a request/read/write path.
    """
    if not db.get_db_url():
        return False
    pending = registered()
    with db.connection() as conn:
        # Serialize every schema-DDL path (this runner + any ensure_schema that
        # shares the key) across processes/replicas. Released on commit/rollback.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_ADVISORY_LOCK_KEY,))
        conn.execute(_LEDGER_DDL)
        applied = {
            row[0] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
        }
        for m in pending:
            if m.id in applied:
                continue
            for stmt in m.statements:
                conn.execute(stmt)
            if m.run is not None:
                m.run(conn)
            conn.execute(
                "INSERT INTO schema_migrations (id, checksum) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (m.id, _checksum(m)),
            )
            log.info("applied schema migration %s", m.id)
    return True
