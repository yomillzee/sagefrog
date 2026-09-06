"""Guards on what makes switching to a client fast.

Two costs used to sit between clicking a client in the workspace switcher and
seeing that client's numbers, and both are easy to reintroduce by accident:

1. **Schema DDL on every read.** Each Postgres-backed store opened its reads with
   ``ensure_schema()`` — a run of CREATE TABLE / ALTER TABLE ... IF NOT EXISTS
   statements. They are no-ops after the first run but never free: each takes an
   ACCESS EXCLUSIVE lock, and with ``DB_POOL_ENABLED`` off each run costs its own
   Postgres connection. The client switcher reads three of these stores several
   times per render, so one page render spent 100+ statements over 17
   connections before anything reached the page (the same disease web_users #297
   fixed, in the stores that had not adopted the guard).

2. **A serialized first fetch.** The Overview's data load was chained behind
   ``loadHealth()``, so a whole BigQuery round-trip ran before the first card
   started loading — and on a client nobody had opened since its last sync that
   read is cold, which is precisely the "first switch to a client is slow" case.

These tests are about *shape*, not timing: they count statements and read the
emitted JS, so they fail deterministically rather than flakily.
"""

from __future__ import annotations

import contextlib
import re
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import client_dashboard_config  # noqa: E402
import connector_config_store  # noqa: E402
import dashboard_registry  # noqa: E402
import db  # noqa: E402


class _FakeCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None

    rowcount = 0


class _CountingConn:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def execute(self, sql, params=None):
        self._log.append(" ".join(str(sql).split()))
        return _FakeCursor()

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass


@contextlib.contextmanager
def _counting_db(statements: list[str], connections: list[int]):
    """Patch db.connection so every store's reads land in one counter.

    Every store does ``import db`` then ``db.connection()``, so patching the
    attribute on the shared module object covers all of them at once.
    """
    original = db.connection

    @contextlib.contextmanager
    def fake_connection():
        connections[0] += 1
        yield _CountingConn(statements)

    db.connection = fake_connection
    try:
        yield
    finally:
        db.connection = original


def _reset_schema_guards() -> None:
    dashboard_registry.reset_schema_cache()
    connector_config_store.reset_schema_cache()
    client_dashboard_config.reset_schema_cache()


class SchemaGuardTests(unittest.TestCase):
    """The DDL runs at most once per process, per store."""

    def setUp(self) -> None:
        self._saved_url = db.os.environ.get("DATABASE_URL") if hasattr(db, "os") else None
        import os

        self._os = os
        self._had_url = "DATABASE_URL" in os.environ
        self._old_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        _reset_schema_guards()

    def tearDown(self) -> None:
        if self._had_url:
            self._os.environ["DATABASE_URL"] = self._old_url or ""
        else:
            self._os.environ.pop("DATABASE_URL", None)
        _reset_schema_guards()

    def _ddl_count(self, statements: list[str]) -> int:
        return sum(
            1 for s in statements
            if s.upper().startswith(("CREATE TABLE", "ALTER TABLE", "CREATE INDEX"))
        )

    def test_repeated_ensure_schema_runs_ddl_once(self) -> None:
        for module, kwargs in (
            (dashboard_registry, {"seed_defaults": False}),
            (connector_config_store, {}),
            (client_dashboard_config, {}),
        ):
            with self.subTest(module=module.__name__):
                _reset_schema_guards()
                statements: list[str] = []
                connections = [0]
                with _counting_db(statements, connections):
                    module.ensure_schema(**kwargs)
                    first = self._ddl_count(statements)
                    first_conns = connections[0]
                    for _ in range(9):
                        module.ensure_schema(**kwargs)

                self.assertGreater(first, 0, "no DDL ran at all on the first call")
                self.assertEqual(
                    self._ddl_count(statements), first,
                    "DDL re-ran after the first call — the once-per-process guard is gone",
                )
                self.assertEqual(
                    connections[0], first_conns,
                    "a repeat ensure_schema() still opened a Postgres connection",
                )

    def test_schema_ddl_takes_the_shared_advisory_lock(self) -> None:
        # All schema DDL must serialize on the one lock db_migrate owns, so this
        # path can never run table-locking DDL alongside the migration runner.
        import db_migrate

        for module, kwargs in (
            (dashboard_registry, {"seed_defaults": False}),
            (connector_config_store, {}),
            (client_dashboard_config, {}),
        ):
            with self.subTest(module=module.__name__):
                _reset_schema_guards()
                statements: list[str] = []
                with _counting_db(statements, [0]):
                    module.ensure_schema(**kwargs)
                self.assertIn(
                    "SELECT pg_advisory_xact_lock(%s)", statements,
                    "schema DDL ran without the shared advisory lock",
                )
                self.assertEqual(
                    module._SCHEMA_ADVISORY_LOCK_KEY, db_migrate.SCHEMA_ADVISORY_LOCK_KEY
                )

    def test_seeding_is_tracked_separately_from_the_ddl(self) -> None:
        # A seed_defaults=False caller must not mark the seeding done, or the
        # registry would never seed its built-in clients.
        _reset_schema_guards()
        statements: list[str] = []
        with _counting_db(statements, [0]):
            dashboard_registry.ensure_schema(seed_defaults=False)
            self.assertFalse(
                any(s.startswith("INSERT INTO dashboard_clients") for s in statements)
            )
            dashboard_registry.ensure_schema(seed_defaults=True)
            self.assertTrue(
                any(s.startswith("INSERT INTO dashboard_clients") for s in statements),
                "seeding was skipped because a seed_defaults=False call marked it done",
            )


class SidebarRenderCostTests(unittest.TestCase):
    """One page render must not re-read the same tables over and over."""

    def setUp(self) -> None:
        import os

        self._had_url = "DATABASE_URL" in os.environ
        self._old_url = os.environ.get("DATABASE_URL")
        self._os = os
        os.environ["DATABASE_URL"] = "postgresql://fake/fake"
        _reset_schema_guards()

    def tearDown(self) -> None:
        if self._had_url:
            self._os.environ["DATABASE_URL"] = self._old_url or ""
        else:
            self._os.environ.pop("DATABASE_URL", None)
        _reset_schema_guards()

    def _render_twice(self) -> tuple[list[str], int]:
        """Render once to warm the per-process guards, then measure a second render.

        The second render is what a navigation actually costs — the first in a
        fresh worker legitimately pays the one-time DDL.
        """
        from dashboard.renderers import base_layout

        def render():
            return base_layout.render_sidebar(
                client_slug="acme", label="Acme", active_nav="overview",
                access_key=None, use_session=True, session_is_admin=True,
                session_email="t@e.com", show_files=True,
                session_can_switch_clients=True, view_nav_html="",
            )

        statements: list[str] = []
        connections = [0]
        with _counting_db(statements, connections):
            render()
            statements.clear()
            connections[0] = 0
            render()
        return statements, connections[0]

    def test_navigation_does_not_rerun_schema_ddl(self) -> None:
        statements, _ = self._render_twice()
        ddl = [s for s in statements if s.upper().startswith(("CREATE TABLE", "ALTER TABLE"))]
        self.assertEqual(
            ddl, [], f"page render re-ran schema DDL: {ddl[:3]}"
        )

    def test_navigation_reads_the_client_registry_at_most_twice(self) -> None:
        # The switcher needs labels (via client_config) and logos. Anything more
        # is the same table read repeatedly for one list of clients.
        statements, _ = self._render_twice()
        reads = [s for s in statements if s.startswith("SELECT client_slug, label, source")]
        self.assertLessEqual(
            len(reads), 2, f"dashboard_clients read {len(reads)}x for one render"
        )

    def test_only_one_registry_read_carries_the_logo_column(self) -> None:
        # Logos are data URIs and by far the largest column in the table; the
        # switcher drawer is the only thing on a render that needs them.
        statements, _ = self._render_twice()
        with_logos = [
            s for s in statements
            if s.startswith("SELECT client_slug, label, source") and ", logo," in s
        ]
        self.assertEqual(
            len(with_logos), 1,
            f"{len(with_logos)} registry reads pulled every client's logo",
        )

    def test_navigation_stays_within_a_small_query_budget(self) -> None:
        # A ceiling, not a target. This render used to cost 113 statements over
        # 17 connections; the point is that a new per-render read is noticed.
        statements, connections = self._render_twice()
        self.assertLessEqual(
            len(statements), 12,
            f"page render now costs {len(statements)} statements: {statements}",
        )
        self.assertLessEqual(
            connections, 12, f"page render now opens {connections} Postgres connections"
        )


class OverviewLoadOrderTests(unittest.TestCase):
    """The Overview's cards must not be chained behind the health fetch."""

    def _page(self) -> str:
        from _dashboard_page import render_bigquery_dashboard_page  # noqa: E402

        return render_bigquery_dashboard_page(
            client_slug="demo", api_client_key="demo", label="Demo",
            use_session=True, session_email="t@e.com", session_is_admin=True,
        )

    def test_cards_do_not_wait_for_the_health_fetch(self) -> None:
        # `loadHealth().then(...)` put a cold BigQuery read in front of every
        # card on the first visit to a client. Health only feeds the
        # comparison-window warnings, so it rides alongside instead. Comments are
        # stripped first: the source explains the old shape by name, and a
        # mention in prose is not the gate coming back.
        code = "\n".join(
            re.sub(r"//.*$", "", line) for line in self._page().splitlines()
        )
        self.assertNotRegex(
            code, r"loadHealth\(\)\s*\.then",
            "the Overview load is gated on loadHealth() again",
        )

    def test_overview_branch_kicks_off_its_loaders_directly(self) -> None:
        html = self._page()
        m = re.search(
            r"if \(currentTab==='overview'\) \{(.*?)\n      \}", html, re.S
        )
        self.assertIsNotNone(m, "could not find the overview branch of loadCurrentTab")
        branch = m.group(1)
        for call in ("loadHealth();", "loadSummary();", "loadOverviewHome();"):
            self.assertIn(call, branch)

    def test_health_repaints_the_warnings_it_feeds(self) -> None:
        # Because the cards no longer wait, whichever of the two lands second has
        # to repaint the other's comparison warning icons.
        html = self._page()
        self.assertIn("function refreshCmpWarns()", html)
        m = re.search(r"syncCompareNotice\(\);\s*(?://[^\n]*\n\s*)*refreshCmpWarns\(\);", html)
        self.assertIsNotNone(
            m, "loadHealth() does not refresh the comparison warnings when it lands"
        )


if __name__ == "__main__":
    unittest.main()
