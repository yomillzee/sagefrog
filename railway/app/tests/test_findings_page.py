"""The findings surface: admin-only on the Insights page, admin-only at the API.

``test_findings_service`` covers what a finding says; this covers who can see it.
The narrative layer is the one most likely to be wrong while its wording settles,
so both the section and the endpoint behind it stay admin-gated, and that gating
is what these lock down.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class InsightsPageTests(unittest.TestCase):
    def _render(self, **kw):
        from dashboard.renderers.bigquery_settings_renderer import (
            render_bigquery_settings_page,
        )

        return render_bigquery_settings_page(
            use_session=True, session_email="t@e.com", **kw
        )

    def test_an_admin_gets_the_findings_section(self):
        html = self._render(session_is_admin=True)
        self.assertIn('id="sec-findings"', html)
        self.assertIn('id="findingsHost"', html)
        self.assertIn("What&#39;s notable", html)

    def test_a_non_admin_gets_no_findings_section(self):
        html = self._render(session_is_admin=False)
        self.assertNotIn('id="sec-findings"', html)
        self.assertNotIn('id="findingsHost"', html)

    def test_the_section_points_at_the_findings_endpoint(self):
        html = self._render(session_is_admin=True, api_client_key="acme")
        self.assertIn("/api/clients/acme/findings", html)

    def test_it_sits_above_the_settings_that_feed_it(self):
        # Goals and benchmarks change what the findings say, so the list has to
        # be the first thing on the page, not buried under its own inputs.
        html = self._render(session_is_admin=True)
        self.assertLess(html.index('id="sec-findings"'), html.index('id="sec-metric-goals"'))
        self.assertLess(html.index('id="sec-findings"'), html.index('id="sec-benchmarks"'))

    def test_the_range_picker_offers_the_windows_the_engine_supports(self):
        html = self._render(session_is_admin=True)
        self.assertIn('id="findingsRange"', html)
        for days in ("7", "30", "90"):
            self.assertIn(f'value="{days}"', html)


class EndpointGateTests(unittest.TestCase):
    """The findings route must sit behind the same admin gate as goals."""

    def setUp(self):
        try:
            from dashboard.routes import api_routes
        except Exception as exc:  # pragma: no cover - import-shape guard
            self.skipTest(f"api_routes unavailable: {exc}")
        self.mod = api_routes

    def test_the_route_is_registered(self):
        paths = {getattr(r, "path", "") for r in self.mod.router.routes}
        self.assertIn("/api/clients/{client_key}/findings", paths)

    def test_it_uses_the_shared_admin_requirement(self):
        import inspect

        src = inspect.getsource(self.mod.client_findings)
        self.assertIn("_require_admin(request, slug)", src)

    def test_optional_inputs_are_each_wrapped_so_one_failure_is_not_fatal(self):
        # A failed benchmark or timeline read should cost those findings, not the
        # whole response — the endpoint is only useful if it degrades.
        import inspect

        src = inspect.getsource(self.mod.client_findings)
        for probe in ("comparison read failed", "health read failed",
                      "goals read failed", "benchmark read failed",
                      "annotation read failed"):
            self.assertIn(probe, src)

    def test_benchmarks_honour_the_per_client_opt_in(self):
        import inspect

        src = inspect.getsource(self.mod.client_findings)
        self.assertIn("benchmarks_enabled(slug)", src)


if __name__ == "__main__":
    unittest.main()
