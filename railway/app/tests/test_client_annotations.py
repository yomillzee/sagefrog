"""Timeline annotations: input cleaning, JSON shape, and the visibility gate.

DB-backed reads and writes need Postgres, so these cover the logic that decides
what gets stored and — more importantly — who is allowed to see it. The
visibility rule is the one that matters: an internal annotation reaching a
client's dashboard is a real disclosure, so the SQL that filters it and the route
that picks the audience are both pinned down here.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _ensure_stub(name: str, build) -> None:
    try:
        __import__(name)
    except Exception:
        sys.modules[name] = build()


def _web_users_stub() -> types.ModuleType:
    m = types.ModuleType("web_users")

    class WebUser:
        def __init__(self, *, email="u@x.com", role="admin", client_slug=None):
            self.email = email
            self.role = role
            self.client_slug = client_slug

        def can_access_client(self, slug: str) -> bool:
            return self.role in ("admin", "standard") or self.client_slug == slug

    m.WebUser = WebUser
    m.enabled = lambda: True
    return m


def _db_stub() -> types.ModuleType:
    m = types.ModuleType("db")
    m.connection = None  # unused: these tests never hit the DB path
    return m


def _stub_bigquery_sdk() -> None:
    """Let ``dashboard.routes`` import without the BigQuery SDK installed.

    The routes package's ``__init__`` pulls in api_routes → bigquery_service →
    google.cloud.bigquery. RouteGateTests only exercises two pure auth helpers,
    so a stub keeps the test standalone (same approach as the benchmark tests).
    """
    if "google.cloud.bigquery" in sys.modules:
        return
    try:
        __import__("google.cloud.bigquery")
        return
    except Exception:
        pass
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name, self.type_, self.value = name, type_, value

    class _FakeQueryJobConfig:
        def __init__(self, dry_run=False):
            self.dry_run = dry_run
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig
    service_account_mod.Credentials = _FakeCredentials
    cloud_mod.bigquery = bigquery_mod
    google_mod.cloud = cloud_mod
    google_mod.oauth2 = oauth2_mod
    oauth2_mod.service_account = service_account_mod
    sys.modules.setdefault("google", google_mod)
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules.setdefault("google.oauth2", oauth2_mod)
    sys.modules["google.oauth2.service_account"] = service_account_mod


_ensure_stub("db", _db_stub)
_ensure_stub("web_users", _web_users_stub)
_stub_bigquery_sdk()

import client_annotations  # noqa: E402


class CleanHelperTests(unittest.TestCase):
    def test_title_is_required_trimmed_and_bounded(self):
        self.assertEqual(client_annotations._clean_title("  Site migration "), "Site migration")
        with self.assertRaises(ValueError):
            client_annotations._clean_title("   ")
        long = "x" * (client_annotations.MAX_TITLE_LEN + 40)
        self.assertEqual(
            len(client_annotations._clean_title(long)), client_annotations.MAX_TITLE_LEN
        )

    def test_unknown_category_falls_back_to_other_rather_than_raising(self):
        self.assertEqual(client_annotations.clean_category("website"), "website")
        self.assertEqual(client_annotations.clean_category("  BUDGET "), "budget")
        self.assertEqual(client_annotations.clean_category("nonsense"), "other")
        self.assertEqual(client_annotations.clean_category(None), "other")

    def test_visibility_fails_closed_to_internal(self):
        # Anything unrecognised must not accidentally become "shared".
        self.assertEqual(client_annotations.clean_visibility("shared"), "shared")
        self.assertEqual(client_annotations.clean_visibility("internal"), "internal")
        self.assertEqual(client_annotations.clean_visibility(""), "internal")
        self.assertEqual(client_annotations.clean_visibility("public"), "internal")
        self.assertEqual(client_annotations.clean_visibility(None), "internal")
        self.assertEqual(client_annotations.DEFAULT_VISIBILITY, "internal")

    def test_slug_is_lowercased_and_required(self):
        self.assertEqual(client_annotations._clean_slug("  Penn "), "penn")
        with self.assertRaises(ValueError):
            client_annotations._clean_slug("  ")

    def test_parse_date_accepts_iso_and_rejects_junk(self):
        self.assertEqual(client_annotations.parse_date("2026-03-12"), date(2026, 3, 12))
        self.assertEqual(client_annotations.parse_date(date(2026, 3, 12)), date(2026, 3, 12))
        with self.assertRaises(ValueError):
            client_annotations.parse_date("12/03/2026")
        with self.assertRaises(ValueError):
            client_annotations.parse_date("")


class DateRangeTests(unittest.TestCase):
    def test_blank_end_date_makes_a_point_event(self):
        start, end = client_annotations._clean_range("2026-03-12", "")
        self.assertEqual(start, date(2026, 3, 12))
        self.assertIsNone(end)

    def test_end_equal_to_start_collapses_to_a_point_event(self):
        _, end = client_annotations._clean_range("2026-03-12", "2026-03-12")
        self.assertIsNone(end)

    def test_a_real_range_is_kept(self):
        start, end = client_annotations._clean_range("2026-03-01", "2026-03-31")
        self.assertEqual((start, end), (date(2026, 3, 1), date(2026, 3, 31)))

    def test_a_backwards_range_is_rejected_not_silently_swapped(self):
        with self.assertRaises(ValueError):
            client_annotations._clean_range("2026-03-31", "2026-03-01")


class AnnotationShapeTests(unittest.TestCase):
    def _anno(self, **over):
        base = dict(
            id=3, client_slug="penn", event_date="2026-03-12", end_date=None,
            title="Site migration", body="New CMS went live.", category="website",
            visibility="internal", created_at="2026-03-12T00:00:00+00:00",
            updated_at="2026-03-12T00:00:00+00:00",
            created_by="a@x.com", updated_by="a@x.com",
        )
        base.update(over)
        return client_annotations.Annotation(**base)

    def test_to_dict_carries_the_label_and_colour_the_chart_draws_with(self):
        d = self._anno().to_dict()
        self.assertEqual(d["category_label"], "Website")
        self.assertEqual(d["color"], client_annotations.CATEGORIES["website"][1])
        self.assertEqual(d["visibility"], "internal")

    def test_to_dict_does_not_leak_the_client_slug_into_the_payload(self):
        # The API is already scoped by slug in the URL; echoing it back is noise
        # in a payload the browser renders.
        self.assertNotIn("client_slug", self._anno().to_dict())

    def test_an_unknown_stored_category_renders_as_other(self):
        d = self._anno(category="obsolete").to_dict()
        self.assertEqual(d["category_label"], "Other")

    def test_category_choices_covers_every_category(self):
        keys = [c["key"] for c in client_annotations.category_choices()]
        self.assertEqual(set(keys), set(client_annotations.CATEGORIES))
        for choice in client_annotations.category_choices():
            self.assertTrue(choice["label"])
            self.assertTrue(choice["color"].startswith("#"))


class VisibilityFilterTests(unittest.TestCase):
    """The SQL a read builds — the guarantee is that a client audience can never
    produce a query that returns internal rows."""

    def setUp(self):
        self.captured = []
        import db

        outer = self

        class _FakeCursor:
            def fetchall(self):
                return []

        class _FakeConn:
            def execute(self, sql, params=None):
                outer.captured.append((sql, params))
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        self._real_connection = getattr(db, "connection", None)
        db.connection = lambda: _FakeConn()
        self._real_ensure = client_annotations.ensure_schema
        client_annotations.ensure_schema = lambda: True
        self._real_enabled = client_annotations.enabled
        client_annotations.enabled = lambda: True

    def tearDown(self):
        import db

        db.connection = self._real_connection
        client_annotations.ensure_schema = self._real_ensure
        client_annotations.enabled = self._real_enabled

    def _sql(self):
        return self.captured[-1][0]

    def test_client_audience_filters_to_shared_in_sql(self):
        client_annotations.list_annotations("penn", audience="client")
        self.assertIn("visibility = 'shared'", self._sql())

    def test_an_unknown_audience_fails_closed_to_shared_only(self):
        client_annotations.list_annotations("penn", audience="")
        self.assertIn("visibility = 'shared'", self._sql())
        client_annotations.list_annotations("penn", audience="administrator")
        self.assertIn("visibility = 'shared'", self._sql())

    def test_agency_audience_sees_everything(self):
        client_annotations.list_annotations("penn", audience="agency")
        # "visibility" still appears in the SELECT list; what must be absent is
        # any filter on it.
        self.assertNotIn("visibility = ", self._sql())

    def test_window_filter_matches_overlapping_ranges_not_just_start_dates(self):
        # A campaign that began before the window still marks the chart it runs
        # across, so the bound is on the *end* of the annotation.
        client_annotations.list_annotations(
            "penn", audience="agency", start="2026-03-01", end="2026-03-31"
        )
        sql = self._sql()
        self.assertIn("COALESCE(end_date, event_date) >= %s", sql)
        self.assertIn("event_date <= %s", sql)

    def test_limit_is_clamped(self):
        client_annotations.list_annotations("penn", audience="agency", limit=99999)
        self.assertEqual(self.captured[-1][1][-1], 1000)
        client_annotations.list_annotations("penn", audience="agency", limit=0)
        self.assertEqual(self.captured[-1][1][-1], 1)


class RouteGateTests(unittest.TestCase):
    """Reads pick an audience; writes require an agency user outright."""

    def setUp(self):
        try:
            from dashboard.routes import annotations_routes
        except Exception as exc:  # pragma: no cover - import-shape guard
            self.skipTest(f"annotations_routes unavailable: {exc}")
        self.mod = annotations_routes

    def _auth(self, role):
        import web_auth

        user = None
        if role is not None:
            user = types.SimpleNamespace(email="u@x.com", role=role)
        return web_auth.DashboardAuth(access_key=None, use_session=True, user=user)

    def test_audience_is_agency_for_admin_and_standard(self):
        self.assertEqual(self.mod._audience(self._auth("admin")), "agency")
        self.assertEqual(self.mod._audience(self._auth("standard")), "agency")

    def test_audience_is_client_for_a_client_role_or_no_user(self):
        self.assertEqual(self.mod._audience(self._auth("client")), "client")
        self.assertEqual(self.mod._audience(self._auth(None)), "client")

    def test_writes_reject_a_client_role(self):
        from fastapi import HTTPException

        for role in ("admin", "standard"):
            self.assertIsNotNone(self.mod._require_agency(self._auth(role)))
        for role in ("client", None):
            with self.assertRaises(HTTPException) as ctx:
                self.mod._require_agency(self._auth(role))
            self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
