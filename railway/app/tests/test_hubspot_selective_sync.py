"""Tests for the HubSpot connector's selective sync.

An admin can pick which HubSpot objects a client pulls (contacts, deals,
marketing emails) so a client who only reports on email performance doesn't pay
BigQuery storage for a full contacts backfill. These cover the option parsing,
the connector honouring the selection, and the sidebar tabs following it.
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hubspot_sync_service as sync  # noqa: E402


class NormalizeObjectsTests(unittest.TestCase):
    def test_unset_means_everything(self):
        # Connectors configured before this option existed must keep syncing
        # all three objects.
        for raw in (None, "", {}, [], "not json"):
            self.assertEqual(
                sync.normalize_objects(raw),
                {"contacts": True, "deals": True, "emails": True},
                msg=f"raw={raw!r}",
            )

    def test_mapping_selection(self):
        self.assertEqual(
            sync.normalize_objects({"contacts": False, "deals": False, "emails": True}),
            {"contacts": False, "deals": False, "emails": True},
        )

    def test_partial_mapping_treats_missing_as_off(self):
        self.assertEqual(
            sync.normalize_objects({"emails": True}),
            {"contacts": False, "deals": False, "emails": True},
        )

    def test_list_selection(self):
        self.assertEqual(
            sync.normalize_objects(["emails", "deals"]),
            {"contacts": False, "deals": True, "emails": True},
        )

    def test_unknown_keys_ignored(self):
        self.assertEqual(
            sync.normalize_objects(["companies", "emails"]),
            {"contacts": False, "deals": False, "emails": True},
        )


class ParseSyncOptionsTests(unittest.TestCase):
    def test_defaults_for_missing_options(self):
        opts = sync.parse_sync_options(None)
        self.assertEqual(opts["lifecycle_stage"], "marketingqualifiedlead")
        self.assertEqual(opts["lookback_days"], 90)
        self.assertEqual(opts["sync_objects"],
                         {"contacts": True, "deals": True, "emails": True})

    def test_reads_stored_json(self):
        raw = json.dumps({
            "lifecycle_stage": "lead",
            "lookback_days": 30,
            "sync_objects": {"contacts": True, "deals": False, "emails": False},
        })
        opts = sync.parse_sync_options(raw)
        self.assertEqual(opts["lifecycle_stage"], "lead")
        self.assertEqual(opts["lookback_days"], 30)
        self.assertEqual(opts["sync_objects"],
                         {"contacts": True, "deals": False, "emails": False})

    def test_legacy_options_without_objects_sync_everything(self):
        opts = sync.parse_sync_options(json.dumps(
            {"lifecycle_stage": "customer", "lookback_days": 180}))
        self.assertEqual(opts["lifecycle_stage"], "customer")
        self.assertEqual(opts["lookback_days"], 180)
        self.assertEqual(opts["sync_objects"],
                         {"contacts": True, "deals": True, "emails": True})

    def test_bad_values_fall_back(self):
        opts = sync.parse_sync_options(json.dumps(
            {"lifecycle_stage": "bogus", "lookback_days": "abc"}))
        self.assertEqual(opts["lifecycle_stage"], "marketingqualifiedlead")
        self.assertEqual(opts["lookback_days"], 90)

    def test_lookback_is_clamped(self):
        # 0/None fall back to the default (same as the save route); a negative
        # or oversized window is clamped into range.
        self.assertEqual(sync.parse_sync_options({"lookback_days": 0})["lookback_days"], 90)
        self.assertEqual(sync.parse_sync_options({"lookback_days": -5})["lookback_days"], 1)
        self.assertEqual(sync.parse_sync_options({"lookback_days": 99999})["lookback_days"], 3650)

    def test_object_options_cover_every_key(self):
        self.assertEqual(
            [o["value"] for o in sync.object_options()],
            ["contacts", "deals", "emails"],
        )
        for opt in sync.object_options():
            self.assertTrue(opt["label"])
            self.assertTrue(opt["help"])


class _Cfg:
    """Minimal stand-in for connector_config_store.ConnectorConfig."""

    def __init__(self, sync_options=None, connector_type="hubspot"):
        self.connector_type = connector_type
        self.sync_options = sync_options
        self.bq_project_id = "proj"
        self.mart_dataset_id = "marketing_marts"


class RunSyncSelectionTests(unittest.TestCase):
    """The connector must only call the pipelines the client selected."""

    def setUp(self):
        import connectors.hubspot as hs_connector
        self.connector = hs_connector.HubSpotConnector()
        self.called: list[str] = []

        # Stub the three sync entry points + the config/oauth lookups so the
        # test exercises the gating alone — no BigQuery, no HubSpot API.
        self._orig = {}
        for name in ("connector_config_store", "hubspot_sync_service", "oauth_store"):
            self._orig[name] = sys.modules.get(name)

        fake_sync = types.ModuleType("hubspot_sync_service")
        fake_sync.parse_sync_options = sync.parse_sync_options

        def _record(kind):
            def _fn(**kwargs):
                self.called.append(kind)
                return {"status": "ok", "rows_synced": 5}
            return _fn

        fake_sync.sync_hubspot_contacts = _record("contacts")
        fake_sync.sync_hubspot_deals = _record("deals")
        fake_sync.sync_hubspot_emails = _record("emails")
        sys.modules["hubspot_sync_service"] = fake_sync

        fake_store = types.ModuleType("connector_config_store")
        fake_store.get_config = lambda slug, ctype: self.cfg
        sys.modules["connector_config_store"] = fake_store

        fake_oauth = types.ModuleType("oauth_store")
        fake_oauth.get_refresh_token = lambda platform, client_slug=None: None
        sys.modules["oauth_store"] = fake_oauth

        self.cfg = _Cfg()

    def tearDown(self):
        for name, mod in self._orig.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def test_no_options_syncs_everything(self):
        result = self.connector.run_sync(client_slug="acme")
        self.assertEqual(sorted(self.called), ["contacts", "deals", "emails"])
        self.assertEqual(result.rows_loaded, 15)
        self.assertIsNone(result.error)

    def test_emails_only(self):
        self.cfg = _Cfg(json.dumps({"sync_objects": {
            "contacts": False, "deals": False, "emails": True}}))
        result = self.connector.run_sync(client_slug="acme")
        self.assertEqual(self.called, ["emails"])
        self.assertEqual(result.rows_loaded, 5)
        self.assertIsNone(result.error)

    def test_contacts_only(self):
        self.cfg = _Cfg(json.dumps({"sync_objects": {
            "contacts": True, "deals": False, "emails": False}}))
        self.connector.run_sync(client_slug="acme")
        self.assertEqual(self.called, ["contacts"])

    def test_contacts_and_emails(self):
        self.cfg = _Cfg(json.dumps({"sync_objects": {
            "contacts": True, "deals": False, "emails": True}}))
        self.connector.run_sync(client_slug="acme")
        self.assertEqual(self.called, ["contacts", "emails"])

    def test_empty_selection_is_an_error_not_a_silent_no_op(self):
        self.cfg = _Cfg(json.dumps({"sync_objects": {
            "contacts": False, "deals": False, "emails": False}}))
        result = self.connector.run_sync(client_slug="acme")
        self.assertEqual(self.called, [])
        self.assertEqual(result.rows_loaded, 0)
        self.assertIn("No HubSpot data is selected", result.error)


class SidebarGateTests(unittest.TestCase):
    """Lead Tracking / Email Performance follow the object selection."""

    def _objects(self, raw):
        from dashboard.renderers import base_layout
        return base_layout._hubspot_sync_objects([_Cfg(raw)])

    def test_unset_shows_everything(self):
        self.assertEqual(self._objects(None),
                         {"contacts": True, "deals": True, "emails": True})

    def test_emails_only_selection(self):
        raw = json.dumps({"sync_objects": {
            "contacts": False, "deals": False, "emails": True}})
        self.assertEqual(self._objects(raw),
                         {"contacts": False, "deals": False, "emails": True})

    def test_no_hubspot_config_falls_open(self):
        from dashboard.renderers import base_layout
        self.assertEqual(
            base_layout._hubspot_sync_objects([_Cfg(None, connector_type="gsc")]),
            {"contacts": True, "deals": True, "emails": True},
        )


if __name__ == "__main__":
    unittest.main()
