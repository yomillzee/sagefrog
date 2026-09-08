"""The cadence shown on the Connectors page must match what the cron does."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import connectors  # noqa: F401 — registers the handlers
from connectors.base import CONNECTOR_ORDER, all_handlers


class SyncCadenceTests(unittest.TestCase):
    def test_cron_synced_matches_the_orchestrator(self) -> None:
        # cron_synced is what the Connectors page labels each card with, so it
        # has to be the same set the daily refresh actually loops over.
        import dashboard.services.bigquery_refresh_orchestrator as orch

        flagged = {c for c, h in all_handlers().items() if h.cron_synced}
        self.assertEqual(flagged, set(orch._SYNC_CONNECTORS))

    def test_every_connector_has_a_cadence(self) -> None:
        for ctype in CONNECTOR_ORDER:
            handler = all_handlers()[ctype]
            label, tip = handler.sync_cadence()
            with self.subTest(ctype):
                self.assertTrue(label.strip())
                self.assertTrue(len(tip) > 20)

    def test_known_cadences(self) -> None:
        handlers = all_handlers()
        self.assertEqual(handlers["ga4"].sync_cadence()[0], "Daily")
        self.assertEqual(handlers["pagespeed"].sync_cadence()[0], "Weekly")
        # SEMrush's interval is env-tunable; default is monthly.
        self.assertEqual(handlers["semrush"].sync_cadence()[0], "Monthly")
        self.assertEqual(handlers["linkedin_organic"].sync_cadence()[0], "Manual only")
        self.assertEqual(handlers["gtm"].sync_cadence()[0], "Manual only")


if __name__ == "__main__":
    unittest.main()
