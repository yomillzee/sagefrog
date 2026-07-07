from __future__ import annotations

import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


class BigQueryPortalTests(unittest.TestCase):
    def test_nixon_bq_test_route_is_registered_before_snapshot_dashboard_route(self) -> None:
        init_source = (APP_DIR / "dashboard" / "routes" / "__init__.py").read_text(encoding="utf-8")
        self.assertLess(
            init_source.index("app.include_router(api_router)"),
            init_source.index("app.include_router(core_router)"),
        )

        api_source = (APP_DIR / "dashboard" / "routes" / "api_routes.py").read_text(encoding="utf-8")
        self.assertIn('"/dashboard/nixon-bq-test"', api_source)
        self.assertNotIn('"/dashboard/nixon"', api_source)
        self.assertIn('"/api/clients/{client_key}/summary"', api_source)
        self.assertIn("render_bigquery_dashboard_page", api_source)
        self.assertNotIn("dashboard_snapshots", api_source)


if __name__ == "__main__":
    unittest.main()
