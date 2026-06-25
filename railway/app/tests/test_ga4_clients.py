from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ga4_clients  # noqa: E402


def _credential(project_id: str) -> dict[str, str]:
    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "test-key-id",
        "private_key": "TEST_PRIVATE_KEY",
        "client_email": f"svc@{project_id}.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _b64_json(data: dict[str, str]) -> str:
    return base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")


class Ga4ClientCredentialResolutionTests(unittest.TestCase):
    def test_client_credentials_env_uses_client_specific_env_var(self) -> None:
        client_credential = _credential("client-project")
        global_credential = _credential("global-project")
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_project_id": "penn-community-b-1699391543298",
                        "bq_dataset_id": "analytics_313855909",
                        "credentials_env": "GCP_CREDS_PENN_BASE64",
                    }
                }
            ),
            "GCP_CREDS_PENN_BASE64": _b64_json(client_credential),
            "GCP_SERVICE_ACCOUNT_JSON": json.dumps(global_credential),
        }
        with patch.dict(os.environ, env, clear=True):
            resolved = ga4_clients.resolve_client_config(client_key="penn")

        self.assertEqual(resolved.label, "Penn Community Bank")
        self.assertEqual(resolved.bq_project_id, "penn-community-b-1699391543298")
        self.assertEqual(resolved.bq_dataset_id, "analytics_313855909")
        self.assertEqual(resolved.credentials["project_id"], "client-project")
        self.assertEqual(resolved.credentials_env, "GCP_CREDS_PENN_BASE64")

    def test_client_without_credentials_env_falls_back_to_global_credential(self) -> None:
        global_credential = _credential("global-project")
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "nixon": {
                        "label": "Nixon Medical",
                        "bq_project_id": "nixon-medical",
                        "bq_dataset_id": "analytics_test",
                    }
                }
            ),
            "GCP_SERVICE_ACCOUNT_JSON": json.dumps(global_credential),
        }
        with patch.dict(os.environ, env, clear=True):
            resolved = ga4_clients.resolve_client_config(client_key="nixon")

        self.assertEqual(resolved.credentials["project_id"], "global-project")
        self.assertIsNone(resolved.credentials_env)

    def test_missing_client_specific_env_var_throws_clear_error(self) -> None:
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_project_id": "penn-community-b-1699391543298",
                        "bq_dataset_id": "analytics_313855909",
                        "credentials_env": "GCP_CREDS_PENN_BASE64",
                    }
                }
            )
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GCP_CREDS_PENN_BASE64.*missing or empty"):
                ga4_clients.resolve_client_config(client_key="penn")

    def test_invalid_base64_throws_clear_error(self) -> None:
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_project_id": "penn-community-b-1699391543298",
                        "bq_dataset_id": "analytics_313855909",
                        "credentials_env": "GCP_CREDS_PENN_BASE64",
                    }
                }
            ),
            "GCP_CREDS_PENN_BASE64": "not-base64!!!",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GCP_CREDS_PENN_BASE64 could not be base64-decoded"):
                ga4_clients.resolve_client_config(client_key="penn")

    def test_invalid_client_key_throws_clear_error(self) -> None:
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_project_id": "penn-community-b-1699391543298",
                        "bq_dataset_id": "analytics_313855909",
                    }
                }
            ),
            "GCP_SERVICE_ACCOUNT_JSON": json.dumps(_credential("global-project")),
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Unknown client_key 'missing'"):
                ga4_clients.resolve_client_config(client_key="missing")

    def test_invalid_json_in_decoded_credential_throws_clear_error(self) -> None:
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_project_id": "penn-community-b-1699391543298",
                        "bq_dataset_id": "analytics_313855909",
                        "credentials_env": "GCP_CREDS_PENN_BASE64",
                    }
                }
            ),
            "GCP_CREDS_PENN_BASE64": base64.b64encode(b"not json").decode("ascii"),
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "decoded credential is not valid JSON"):
                ga4_clients.resolve_client_config(client_key="penn")

    def test_missing_registry_project_id_throws_clear_error(self) -> None:
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_dataset_id": "analytics_313855909",
                    }
                }
            ),
            "GCP_SERVICE_ACCOUNT_JSON": json.dumps(_credential("global-project")),
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GA4_CLIENTS.penn.bq_project_id is required"):
                ga4_clients.resolve_client_config(client_key="penn")

    def test_missing_registry_dataset_id_throws_clear_error(self) -> None:
        env = {
            "GA4_CLIENTS": json.dumps(
                {
                    "penn": {
                        "label": "Penn Community Bank",
                        "bq_project_id": "penn-community-b-1699391543298",
                    }
                }
            ),
            "GCP_SERVICE_ACCOUNT_JSON": json.dumps(_credential("global-project")),
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GA4_CLIENTS.penn.bq_dataset_id is required"):
                ga4_clients.resolve_client_config(client_key="penn")


if __name__ == "__main__":
    unittest.main()
