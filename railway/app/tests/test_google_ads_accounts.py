from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Keep this unit test runnable without installing the external Google SDK. The
# discovery logic is tested with a fake client below.
if "google.ads.googleads.client" not in sys.modules:
    google = types.ModuleType("google")
    google.__path__ = []
    ads = types.ModuleType("google.ads")
    ads.__path__ = []
    googleads = types.ModuleType("google.ads.googleads")
    googleads.__path__ = []
    client_module = types.ModuleType("google.ads.googleads.client")
    client_module.GoogleAdsClient = object
    google_auth = types.ModuleType("google.auth")
    google_auth.__path__ = []
    transport = types.ModuleType("google.auth.transport")
    transport.__path__ = []
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = object
    oauth2 = types.ModuleType("google.oauth2")
    oauth2.__path__ = []
    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = object
    protobuf = types.ModuleType("google.protobuf")
    protobuf.__path__ = []
    json_format = types.ModuleType("google.protobuf.json_format")
    json_format.MessageToDict = lambda value, **kwargs: value
    sys.modules.update(
        {
            "google": google,
            "google.ads": ads,
            "google.ads.googleads": googleads,
            "google.ads.googleads.client": client_module,
            "google.auth": google_auth,
            "google.auth.transport": transport,
            "google.auth.transport.requests": requests_module,
            "google.oauth2": oauth2,
            "google.oauth2.credentials": credentials_module,
            "google.protobuf": protobuf,
            "google.protobuf.json_format": json_format,
        }
    )

import google_ads_service  # noqa: E402


class _CustomerService:
    def list_accessible_customers(self):
        return SimpleNamespace(resource_names=["customers/1111111111"])


class _Client:
    def get_service(self, name: str):
        if name == "CustomerService":
            return _CustomerService()
        raise AssertionError(f"Unexpected service: {name}")


class GoogleAdsAccountDiscoveryTests(unittest.TestCase):
    def test_discovers_named_clients_beneath_manager_and_excludes_managers(self) -> None:
        rows = [
            {
                "customer_client": {
                    "client_customer": "customers/1111111111",
                    "id": "1111111111",
                    "descriptive_name": "Agency MCC",
                    "manager": True,
                    "level": "0",
                    "status": "ENABLED",
                }
            },
            {
                "customer_client": {
                    "client_customer": "customers/2222222222",
                    "id": "2222222222",
                    "descriptive_name": "Zebra Client",
                    "manager": False,
                    "level": "1",
                    "status": "ENABLED",
                }
            },
            {
                "customer_client": {
                    "client_customer": "customers/3333333333",
                    "id": "3333333333",
                    "descriptive_name": "Acme Client",
                    "manager": False,
                    "level": "1",
                    "status": "ENABLED",
                }
            },
        ]

        with patch.object(google_ads_service, "search", return_value=rows) as search:
            accounts = google_ads_service.list_accessible_customer_accounts(client=_Client())

        self.assertEqual(
            [account["customer_id"] for account in accounts],
            ["3333333333", "2222222222"],
        )
        self.assertEqual(accounts[0]["descriptive_name"], "Acme Client")
        self.assertIn("FROM customer_client", search.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
