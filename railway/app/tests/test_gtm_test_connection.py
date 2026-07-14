from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import connectors.gtm as gtm_connector  # noqa: E402
import connector_config_store  # noqa: E402
import gtm_service  # noqa: E402
import oauth_store  # noqa: E402


def _cfg(source_account_id, name="Clearlink (GTM-K6GNK7Z)"):
    return types.SimpleNamespace(
        source_account_id=source_account_id, source_account_name=name
    )


class GtmTestConnectionTests(unittest.TestCase):
    """The wizard's Test connection step must not re-run the full
    account/container fan-out (which trips GTM's per-minute quota with a 429).
    With a container selected, it does a single live-version read instead."""

    def setUp(self):
        self.conn = gtm_connector.GTMConnector()

    def test_configured_container_uses_single_live_read(self):
        with patch.object(connector_config_store, "get_config",
                          return_value=_cfg("123:456")), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags",
                             return_value={"tag_count": 9}) as live, \
                patch.object(gtm_service, "list_containers") as listc:
            label = self.conn.test_connection(client_slug="clearlink")

        self.assertEqual(label, "Clearlink (GTM-K6GNK7Z)")
        live.assert_called_once()
        self.assertEqual(live.call_args.args[1:3], ("123", "456"))
        # The expensive fan-out must NOT run.
        listc.assert_not_called()

    def test_propagates_live_read_failure(self):
        with patch.object(connector_config_store, "get_config",
                          return_value=_cfg("123:456")), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags",
                             side_effect=RuntimeError("HTTP 429 rate-limited")):
            with self.assertRaises(RuntimeError):
                self.conn.test_connection(client_slug="clearlink")

    def test_no_container_falls_back_to_listing(self):
        with patch.object(connector_config_store, "get_config",
                          return_value=_cfg("")), \
                patch.object(oauth_store, "get_refresh_token", return_value="rt"), \
                patch.object(gtm_service, "get_live_tags") as live, \
                patch.object(gtm_service, "list_containers",
                             return_value=[{"id": "1:2", "name": "Only container"}]) as listc:
            label = self.conn.test_connection(client_slug="clearlink")

        self.assertEqual(label, "Only container")
        listc.assert_called_once()
        live.assert_not_called()


if __name__ == "__main__":
    unittest.main()
