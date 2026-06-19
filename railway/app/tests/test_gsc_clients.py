from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import gsc_clients  # noqa: E402


class GscClientRoutingTests(unittest.TestCase):
    def test_registry_client_routes_to_its_own_project_and_dataset(self) -> None:
        env = {
            "GSC_CLIENTS": json.dumps(
                {
                    "nixon": {
                        "label": "Nixon Medical",
                        "bq_project_id": "nixon-medical-gcp-project",
                        "bq_dataset_id": "marketing_marts",
                        "credentials_env": "GCP_CREDS_NIXON_BASE64",
                        "site_url": "sc-domain:nixonmedical.com",
                        "native_dataset_id": "searchconsole_nixon",
                    }
                }
            )
        }
        with patch.dict(os.environ, env, clear=True):
            target = gsc_clients.resolve_target("nixon")

        self.assertEqual(target.bq_project_id, "nixon-medical-gcp-project")
        self.assertEqual(target.bq_dataset_id, "marketing_marts")
        self.assertEqual(target.credentials_env, "GCP_CREDS_NIXON_BASE64")
        self.assertEqual(target.site_url, "sc-domain:nixonmedical.com")
        self.assertEqual(target.native_dataset_id, "searchconsole_nixon")
        self.assertFalse(target.is_default_fallback)

    def test_client_not_in_registry_falls_back_to_legacy_penn_defaults(self) -> None:
        env = {
            "GSC_CLIENTS": json.dumps(
                {"nixon": {"bq_project_id": "nixon-proj", "bq_dataset_id": "marketing_marts"}}
            )
        }
        with patch.dict(os.environ, env, clear=True):
            target = gsc_clients.resolve_target("some-other-client")

        self.assertTrue(target.is_default_fallback)
        self.assertEqual(target.bq_project_id, "penn-community-b-1699391543298")
        self.assertEqual(target.bq_dataset_id, "marketing_marts")

    def test_no_registry_at_all_falls_back_to_legacy_penn_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            target = gsc_clients.default_target("penn-bq-test")

        self.assertTrue(target.is_default_fallback)
        self.assertEqual(target.bq_project_id, "penn-community-b-1699391543298")
        self.assertEqual(target.bq_dataset_id, "marketing_marts")
        self.assertEqual(target.credentials_env, "GCP_SERVICE_ACCOUNT_JSON")

    def test_default_target_prefers_penn_credential_when_set(self) -> None:
        env = {"GCP_CREDS_PENN_BASE64": "dummy-value"}
        with patch.dict(os.environ, env, clear=True):
            target = gsc_clients.default_target()

        self.assertEqual(target.credentials_env, "GCP_CREDS_PENN_BASE64")

    def test_credentials_env_override_in_registry_is_respected(self) -> None:
        env = {
            "GSC_CLIENTS": json.dumps(
                {
                    "comport": {
                        "bq_project_id": "comport-proj",
                        "bq_dataset_id": "marketing_marts",
                        "credentials_env": "GCP_CREDS_COMPORT_BASE64",
                    }
                }
            )
        }
        with patch.dict(os.environ, env, clear=True):
            target = gsc_clients.resolve_target("comport")

        self.assertEqual(target.credentials_env, "GCP_CREDS_COMPORT_BASE64")

    def test_registry_missing_project_id_throws_clear_error(self) -> None:
        env = {"GSC_CLIENTS": json.dumps({"nixon": {"bq_dataset_id": "marketing_marts"}})}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GSC_CLIENTS.nixon.bq_project_id is required"):
                gsc_clients.load_client_registry()

    def test_registry_missing_dataset_id_throws_clear_error(self) -> None:
        env = {"GSC_CLIENTS": json.dumps({"nixon": {"bq_project_id": "nixon-proj"}})}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GSC_CLIENTS.nixon.bq_dataset_id is required"):
                gsc_clients.load_client_registry()

    def test_invalid_json_throws_clear_error(self) -> None:
        env = {"GSC_CLIENTS": "not json!!!"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GSC_CLIENTS is not valid JSON"):
                gsc_clients.load_client_registry()

    def test_list_clients_public_excludes_credentials_env_value_is_present_but_no_secrets(self) -> None:
        env = {
            "GSC_CLIENTS": json.dumps(
                {
                    "nixon": {
                        "label": "Nixon Medical",
                        "bq_project_id": "nixon-proj",
                        "bq_dataset_id": "marketing_marts",
                        "credentials_env": "GCP_CREDS_NIXON_BASE64",
                    }
                }
            )
        }
        with patch.dict(os.environ, env, clear=True):
            rows = gsc_clients.list_clients_public()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_slug"], "nixon")
        self.assertEqual(rows[0]["bq_project_id"], "nixon-proj")
        # No actual credential material should ever appear in the public listing.
        for v in rows[0].values():
            self.assertNotIn("BEGIN PRIVATE KEY", str(v))


if __name__ == "__main__":
    unittest.main()
