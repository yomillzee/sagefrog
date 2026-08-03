"""Regression test: the dedicated nixon-bq-test settings page must re-render the
saved monthly budget.

The budget "Save goal" control POSTs to /dashboard/nixon-bq-test/budget, which
persists under the "nixon-bq-test" slug. The GET settings page for nixon-bq-test
is served by the dedicated ``nixon_bq_settings_page`` route (registered before
the generic one), which historically never loaded that saved value back — so the
goal field always re-rendered blank and the budget looked like it wasn't saving.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import api_routes  # noqa: E402


class _FakeAuth:
    def __init__(self) -> None:
        self.access_key = None
        self.use_session = True
        self.user = types.SimpleNamespace(email="a@b.com", role="admin")


class _FakeCfgRow:
    monthly_budget_usd = 25000.0
    explorer_budget_tracker = True
    explorer_filters = "[Product]\nApparel = apparel"


class NixonSettingsBudgetTests(unittest.TestCase):
    def _render(self, db_cfg) -> str:
        import client_dashboard_config

        with patch.object(api_routes.web_auth, "authenticate_dashboard_any", return_value=_FakeAuth()), \
             patch.object(api_routes, "_nixon_settings_context", return_value=({"marts_dataset": "marketing_marts"}, {})), \
             patch.object(client_dashboard_config, "get_config", return_value=db_cfg) as get_config:
            resp = api_routes.nixon_bq_settings_page(request=types.SimpleNamespace())
        # The saved values are read back under the BigQuery slug, not "nixon".
        get_config.assert_any_call("nixon-bq-test")
        return resp.body.decode()

    def test_saved_budget_rerenders_in_goal_config(self) -> None:
        body = self._render(_FakeCfgRow())
        # The budget module's JS config carries the persisted goal so the field
        # repopulates on reload instead of coming back blank.
        self.assertIn('"monthlyBudget": 25000.0', body)
        # Save target stays the BigQuery slug so the POST round-trips correctly.
        self.assertIn("/dashboard/nixon-bq-test/budget", body)

    def test_no_saved_row_defaults_to_zero(self) -> None:
        body = self._render(None)
        self.assertIn('"monthlyBudget": 0', body)


if __name__ == "__main__":
    unittest.main()
