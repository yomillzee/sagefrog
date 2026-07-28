"""Tests for hubspot_reports_service stage flexibility.

The Lead Tracking report is not hard-wired to MQLs: it reports on whichever
lifecycle stage the connector was configured to import, and falls back to
whichever stage the data is actually keyed on so a client tracking "leads"
never sees an empty page.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# bigquery_service (imported by the report service) imports google.cloud +
# google.oauth2.service_account; stub the tree exactly like the other tests.
if "google.cloud.bigquery" not in sys.modules:
    g = types.ModuleType("google")
    c = types.ModuleType("google.cloud")
    bq = types.ModuleType("google.cloud.bigquery")
    oauth2 = types.ModuleType("google.oauth2")
    sa = types.ModuleType("google.oauth2.service_account")

    class _FakeQueryJobConfig:
        def __init__(self, *a, **k):
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *a, **k):
            return cls()

    bq.QueryJobConfig = _FakeQueryJobConfig
    bq.ScalarQueryParameter = lambda *a, **k: None
    sa.Credentials = _FakeCredentials
    g.cloud = c
    c.bigquery = bq
    g.oauth2 = oauth2
    oauth2.service_account = sa
    sys.modules.update({
        "google": g, "google.cloud": c, "google.cloud.bigquery": bq,
        "google.oauth2": oauth2, "google.oauth2.service_account": sa,
    })

# connector_config_store imports db -> psycopg, which isn't installed in the
# test image. Stub it; tests set get_config on the imported module directly.
if "connector_config_store" not in sys.modules or not hasattr(
    sys.modules["connector_config_store"], "_hs_test_stub"
):
    _ccs = types.ModuleType("connector_config_store")
    _ccs._hs_test_stub = True
    _ccs.get_config = lambda slug, ctype: None
    sys.modules["connector_config_store"] = _ccs

import hubspot_reports_service as svc  # noqa: E402


class _Cfg:
    def __init__(self, project="proj-x", dataset="marketing_marts", sync_options=None):
        self.bq_project_id = project
        self.mart_dataset_id = dataset
        self.sync_options = sync_options


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _FakeClient:
    """Routes SQL to canned rows by inspecting the query text.

    ``present`` maps a stage_filter -> row count and drives both the
    stage-distribution probe and the per-stage summary counts, so tests can
    describe the warehouse contents in one place.
    """

    def __init__(self, present):
        self.present = present

    def query(self, sql):
        s = " ".join(sql.split())
        # Stage distribution probe.
        if "GROUP BY stage_filter" in s and "COUNT(*) AS n" in s:
            return _FakeResult(
                [{"stage_filter": k, "n": v} for k, v in self.present.items()]
            )
        # contacts_summary: COUNTIF(stage_filter = '<stage>') AS mql_count
        if "AS mql_count" in s:
            stage = _stage_in(s)
            return _FakeResult([
                {"mql_count": self.present.get(stage, 0), "contact_count": 915}
            ])
        if "AS deal_count" in s:
            return _FakeResult([
                {"deal_count": 5, "pipeline_amount": 1000.0, "won_amount": 250.0}
            ])
        # Everything else (by_month, by_source, recent, deals_by_source): empty.
        return _FakeResult([])


def _stage_in(sql: str) -> str:
    for stage in svc._STAGE_NOUNS:
        if f"'{stage}'" in sql:
            return stage
    return svc._MQL_STAGE


class _PatchMixin(unittest.TestCase):
    def setUp(self):
        self._orig_get_config = svc.connector_config_store.get_config
        self._orig_build_client = svc.bigquery_service.build_client

    def tearDown(self):
        svc.connector_config_store.get_config = self._orig_get_config
        svc.bigquery_service.build_client = self._orig_build_client

    def _patch(self, cfg, present=None):
        svc.connector_config_store.get_config = lambda slug, ctype: cfg
        if present is not None:
            svc.bigquery_service.build_client = lambda *a, **k: _FakeClient(present)


class StageNounTests(unittest.TestCase):
    def test_known_stage_nouns(self):
        self.assertEqual(svc._stage_nouns("marketingqualifiedlead"), ("MQL", "MQLs"))
        self.assertEqual(svc._stage_nouns("lead"), ("Lead", "Leads"))
        self.assertEqual(svc._stage_nouns("salesqualifiedlead"), ("SQL", "SQLs"))

    def test_unknown_stage_falls_back_to_titlecase(self):
        self.assertEqual(svc._stage_nouns("power_user"), ("Power User", "Power Users"))
        self.assertEqual(svc._stage_nouns(""), ("Lead", "Leads"))


class ResolveStageTests(_PatchMixin):
    def test_reads_configured_lifecycle_stage(self):
        self._patch(_Cfg(sync_options='{"lifecycle_stage": "lead", "lookback_days": 90}'))
        self.assertEqual(svc._resolve_stage("acme"), "lead")

    def test_defaults_to_mql_without_options(self):
        self._patch(_Cfg(sync_options=None))
        self.assertEqual(svc._resolve_stage("acme"), "marketingqualifiedlead")

    def test_rejects_unknown_stage(self):
        self._patch(_Cfg(sync_options='{"lifecycle_stage": "nonsense"}'))
        self.assertEqual(svc._resolve_stage("acme"), "marketingqualifiedlead")


class BuildReportTests(_PatchMixin):
    def test_configured_lead_stage_populates_report(self):
        self._patch(_Cfg(sync_options='{"lifecycle_stage": "lead"}'), {"lead": 915})
        report = svc.build_report("acme")
        self.assertEqual(report.stage, "lead")
        self.assertEqual(report.stage_noun, "Lead")
        self.assertEqual(report.stage_noun_plural, "Leads")
        self.assertEqual(report.mql_count, 915)
        self.assertTrue(report.contacts_available)

    def test_falls_back_to_populated_stage_when_configured_is_empty(self):
        # Connector still says MQL, but every contact was imported as a "lead".
        self._patch(_Cfg(sync_options='{"lifecycle_stage": "marketingqualifiedlead"}'),
                    {"lead": 915})
        report = svc.build_report("acme")
        self.assertEqual(report.stage, "lead")
        self.assertEqual(report.stage_noun_plural, "Leads")
        self.assertEqual(report.mql_count, 915)

    def test_keeps_configured_stage_when_it_has_data(self):
        self._patch(_Cfg(sync_options='{"lifecycle_stage": "marketingqualifiedlead"}'),
                    {"marketingqualifiedlead": 12, "lead": 915})
        report = svc.build_report("acme")
        self.assertEqual(report.stage, "marketingqualifiedlead")
        self.assertEqual(report.stage_noun_plural, "MQLs")
        self.assertEqual(report.mql_count, 12)


if __name__ == "__main__":
    unittest.main()
