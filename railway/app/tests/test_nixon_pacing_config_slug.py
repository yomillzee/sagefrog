"""Regression test: Nixon's saved active-day override must reach the pacing read.

The Budget tracking card's day picker POSTs to
/dashboard/nixon-bq-test/pacing-active-days, which saves under the URL slug. The
pacing endpoint the same card calls is /api/clients/nixon/budget/pacing, and it
used to read its config under the API client key ("nixon") — a different row,
with no override on it. So Nixon's active days silently fell back to
auto-detection no matter what an admin picked. Same shape as the monthly-goal
bug (see test_nixon_dashboard_budget).
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.routes import api_routes  # noqa: E402


def _row(budget=None, weekdays=None):
    return types.SimpleNamespace(
        monthly_budget_usd=budget, pacing_active_weekdays=weekdays
    )


class NixonPacingConfigSlugTests(unittest.TestCase):
    def _pacing_call(self, rows: dict[str, object], **kwargs) -> dict:
        """Run _pacing_read with a stubbed spend read, capturing what it computed."""
        captured: dict = {}

        def _fake_compute(*, monthly_budget, daily_spend, today, active_weekdays_override):
            captured["budget"] = monthly_budget
            captured["override"] = active_weekdays_override
            return types.SimpleNamespace(to_dict=dict)

        cdc = importlib.import_module("client_dashboard_config")
        with patch.object(api_routes, "_summary_read", return_value={"daily": []}), \
             patch.object(api_routes.pacing, "compute_pacing", _fake_compute), \
             patch.object(cdc, "get_config", side_effect=lambda s: rows.get(s), create=True):
            api_routes._pacing_read("nixon", **kwargs)
        return captured

    def test_override_saved_under_the_slug_is_used(self) -> None:
        got = self._pacing_call(
            {"nixon": _row(), "nixon-bq-test": _row(weekdays="1,2,3,4,5")},
            config_slug="nixon-bq-test",
        )
        self.assertEqual(got["override"], [1, 2, 3, 4, 5])

    def test_goal_saved_under_the_slug_beats_the_page_hint(self) -> None:
        got = self._pacing_call(
            {"nixon": _row(), "nixon-bq-test": _row(budget=25000.0)},
            budget_hint=99.0,
            config_slug="nixon-bq-test",
        )
        self.assertEqual(got["budget"], 25000.0)

    def test_reset_to_auto_is_not_undone_by_the_other_row(self) -> None:
        # The slug's row exists with no override — that is the "auto" setting,
        # not a gap to fill from the API key's row.
        got = self._pacing_call(
            {"nixon": _row(weekdays="6,7"), "nixon-bq-test": _row(weekdays=None)},
            config_slug="nixon-bq-test",
        )
        self.assertIsNone(got["override"])

    def test_api_key_row_still_answers_when_there_is_no_slug_row(self) -> None:
        got = self._pacing_call(
            {"nixon": _row(budget=8000.0, weekdays="1,2")}, config_slug="nixon-bq-test"
        )
        self.assertEqual(got["override"], [1, 2])
        self.assertEqual(got["budget"], 8000.0)

    def test_ordinary_client_reads_its_single_row(self) -> None:
        got = self._pacing_call({"nixon": _row(budget=500.0, weekdays="3")})
        self.assertEqual(got["override"], [3])
        self.assertEqual(got["budget"], 500.0)

    def test_nixon_route_passes_the_slug(self) -> None:
        # The wiring itself: the bespoke /api/clients/nixon route has to tell
        # _pacing_read where Nixon's settings actually live.
        with patch.object(api_routes.web_auth, "authenticate_dashboard_api_any"), \
             patch.object(api_routes, "_pacing_read", return_value={}) as read:
            api_routes.client_budget_pacing(
                client_key="nixon", request=types.SimpleNamespace(), budget=None
            )
        self.assertEqual(read.call_args.kwargs.get("config_slug"), "nixon-bq-test")


if __name__ == "__main__":
    unittest.main()
