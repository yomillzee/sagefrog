"""Regression test: the Nixon *dashboard* must re-render the saved monthly goal.

Nixon is the one client whose dashboard renders with a client_slug
("nixon-bq-test") that differs from its API client key ("nixon"). Every write
keys on the slug — the inline "Save goal" POST goes to
/dashboard/nixon-bq-test/budget, and the settings page and the active-days POST
write there too — but the renderer's config read preferred the API key, and a
"nixon" row exists (it backs the connector configs). So the goal saved fine and
came back blank on the next load, which reads as the budget not saving at all.
The settings page carries the same fix; the dashboard never got it.
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

from dashboard.renderers.bigquery_dashboard_renderer import (  # noqa: E402
    render_bigquery_dashboard_page,
)

# The budget module only renders for a client with a live paid-ads connector.
_PAID_CONNECTOR = [types.SimpleNamespace(connector_type="google_ads", status="connected")]


def _live_module(name: str):
    """The module object the renderer's own ``import`` will resolve to.

    Other tests swap these modules in and out of ``sys.modules``, so binding
    them at import time here would patch an object the renderer never sees.
    """
    return importlib.import_module(name)


def _row(slug: str, budget: float | None):
    """A config row stub with only the attributes the renderer reads."""
    return types.SimpleNamespace(
        client_slug=slug,
        monthly_budget_usd=budget,
        gsc_branded_roots="",
        gsc_target_keywords="",
        ga4_key_events="",
        explorer_filters="",
    )


class NixonDashboardBudgetTests(unittest.TestCase):
    def _render(self, rows: dict[str, object]) -> str:
        with patch.object(
            _live_module("client_dashboard_config"), "get_config",
            side_effect=lambda s: rows.get(s), create=True,
        ), patch.object(
            _live_module("connector_config_store"), "list_configs",
            return_value=_PAID_CONNECTOR, create=True,
        ):
            return render_bigquery_dashboard_page(
                client_slug="nixon-bq-test",
                api_client_key="nixon",
                label="Nixon Medical",
                use_session=True,
                session_email="a@b.com",
                session_is_admin=True,
            )

    def test_goal_saved_under_the_slug_survives_a_reload(self) -> None:
        # The "nixon" row wins the general config read but has no budget; the
        # goal lives in the slug's row, where every save puts it.
        html = self._render(
            {"nixon": _row("nixon", None), "nixon-bq-test": _row("nixon-bq-test", 25000.0)}
        )
        self.assertIn('"monthlyBudget": 25000.0', html)
        # And the save still targets the slug, so the round-trip closes.
        self.assertIn("/dashboard/nixon-bq-test/budget", html)

    def test_api_key_row_still_supplies_the_goal_when_the_slug_row_has_none(self) -> None:
        html = self._render(
            {"nixon": _row("nixon", 8000.0), "nixon-bq-test": _row("nixon-bq-test", None)}
        )
        self.assertIn('"monthlyBudget": 8000.0', html)

    def test_no_goal_anywhere_renders_zero(self) -> None:
        html = self._render({"nixon": _row("nixon", None)})
        self.assertIn('"monthlyBudget": 0', html)

    def test_ordinary_client_reads_its_single_row(self) -> None:
        # Slug == API key for every client onboarded through the generic route,
        # so the extra lookup must not change what they see.
        with patch.object(
            _live_module("client_dashboard_config"), "get_config",
            side_effect=lambda s: _row("acme", 1234.0) if s == "acme" else None,
            create=True,
        ), patch.object(
            _live_module("connector_config_store"), "list_configs",
            return_value=_PAID_CONNECTOR, create=True,
        ):
            html = render_bigquery_dashboard_page(
                client_slug="acme", api_client_key="acme", label="Acme",
                use_session=True, session_email="a@b.com", session_is_admin=True,
            )
        self.assertIn('"monthlyBudget": 1234.0', html)


if __name__ == "__main__":
    unittest.main()
