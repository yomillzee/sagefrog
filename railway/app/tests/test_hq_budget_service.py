from __future__ import annotations

import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# hq_budget_service transitively imports google.cloud.bigquery (via
# marketing_service -> bigquery_service). The BigQuery client is never invoked
# in these tests — every per-client read is patched — so a stub is enough to let
# the import tree load without the heavy SDK. This mirrors test_marketing_service
# exactly: whichever test module loads first installs the shared stub, and the
# guard below lets the other reuse it. Keep the two blocks identical so neither
# shadows the other with a thinner fake.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _FakeQueryJobConfig:
        def __init__(self, dry_run=False):
            self.dry_run = dry_run
            self.query_parameters = []

    class _FakeCredentials:
        @classmethod
        def from_service_account_info(cls, *args, **kwargs):
            return cls()

    bigquery_mod.ScalarQueryParameter = _FakeScalarQueryParameter
    bigquery_mod.QueryJobConfig = _FakeQueryJobConfig
    service_account_mod.Credentials = _FakeCredentials
    cloud_mod.bigquery = bigquery_mod
    google_mod.cloud = cloud_mod
    google_mod.oauth2 = oauth2_mod
    oauth2_mod.service_account = service_account_mod
    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.bigquery"] = bigquery_mod
    sys.modules["google.oauth2"] = oauth2_mod
    sys.modules["google.oauth2.service_account"] = service_account_mod

from dashboard.services import hq_budget_service as hq


def _clients(n: int) -> list[dict]:
    return [
        {
            "client_slug": f"c{i}",
            "label": f"Client {i}",
            "monthly_budget_usd": 1000.0,
            "gcp_project_id": f"proj-{i}",
            "bq_mart_dataset_id": "marketing_marts",
        }
        for i in range(n)
    ]


class HqBudgetOverviewTests(unittest.TestCase):
    def test_rows_preserve_client_order_under_parallelism(self):
        # Later clients return faster than earlier ones, so an order-losing
        # implementation (e.g. as_completed) would visibly reshuffle the rows.
        def slow_spend(*, slug, project_id, dataset_id, month_start, today):
            idx = int(slug[1:])
            time.sleep(0.02 * (5 - idx))  # c0 slowest, c4 fastest
            return 100.0

        with patch.object(hq.client_dashboard_config, "list_budget_overview", return_value=_clients(5)), \
             patch.object(hq, "_client_mtd_spend", side_effect=slow_spend), \
             patch.object(hq, "_client_sessions_series", return_value=[1, 2, 3]):
            result = hq.build_hq_budget_overview()

        self.assertEqual([r["client_slug"] for r in result["clients"]], ["c0", "c1", "c2", "c3", "c4"])

    def test_totals_match_sequential_arithmetic(self):
        with patch.object(hq.client_dashboard_config, "list_budget_overview", return_value=_clients(3)), \
             patch.object(hq, "_client_mtd_spend", return_value=250.0), \
             patch.object(hq, "_client_sessions_series", return_value=[10, 20]):
            result = hq.build_hq_budget_overview()

        totals = result["totals"]
        self.assertEqual(totals["client_count"], 3)
        self.assertAlmostEqual(totals["monthly_budget"], 3000.0)
        self.assertAlmostEqual(totals["mtd_spend"], 750.0)  # 3 x 250
        # Every client has spend, so each contributes to the projected total.
        self.assertEqual(sum(r["sessions_total"] for r in result["clients"]), 90)

    def test_one_failing_client_does_not_sink_the_page(self):
        def spend(*, slug, project_id, dataset_id, month_start, today):
            if slug == "c1":
                raise RuntimeError("BigQuery boom")
            return 100.0

        with patch.object(hq.client_dashboard_config, "list_budget_overview", return_value=_clients(3)), \
             patch.object(hq, "_client_mtd_spend", side_effect=spend), \
             patch.object(hq, "_client_sessions_series", return_value=[]):
            result = hq.build_hq_budget_overview()

        by_slug = {r["client_slug"]: r for r in result["clients"]}
        self.assertEqual(len(by_slug), 3)
        self.assertTrue(by_slug["c0"]["spend_available"])
        self.assertFalse(by_slug["c1"]["spend_available"])  # degraded, not dropped
        self.assertEqual(by_slug["c1"]["mtd_spend"], 0.0)
        # Failed client is excluded from spend totals but still counted in budget.
        self.assertAlmostEqual(result["totals"]["mtd_spend"], 200.0)
        self.assertAlmostEqual(result["totals"]["monthly_budget"], 3000.0)

    def test_reads_actually_run_concurrently(self):
        # With a plain loop, N clients each sleeping T would take N*T. The pool
        # should collapse that toward ~T. Assert we're well under the sequential
        # sum to prove the fan-out is real.
        n = 6
        per_read = 0.05
        seen_threads: set[int] = set()
        lock = threading.Lock()

        def spend(*, slug, project_id, dataset_id, month_start, today):
            with lock:
                seen_threads.add(threading.get_ident())
            time.sleep(per_read)
            return 100.0

        with patch.object(hq.client_dashboard_config, "list_budget_overview", return_value=_clients(n)), \
             patch.object(hq, "_client_mtd_spend", side_effect=spend), \
             patch.object(hq, "_client_sessions_series", return_value=[]):
            start = time.monotonic()
            hq.build_hq_budget_overview()
            elapsed = time.monotonic() - start

        self.assertGreater(len(seen_threads), 1)  # ran on multiple worker threads
        self.assertLess(elapsed, n * per_read)    # faster than the sequential sum

    def test_nixon_budget_priced_despite_missing_project_on_config_row(self):
        # nixon-bq-test carries a budget but no gcp_project_id on its config row
        # (Nixon's mart lives in marketing_service defaults). Without the
        # code-level fallback it would skip the spend read and show 0% of budget;
        # with it, HQ resolves the built-in destination and prices spend.
        nixon = {
            "client_slug": "nixon-bq-test",
            "label": "Nixon Medical",
            "monthly_budget_usd": 10000.0,
            "gcp_project_id": None,
            "bq_mart_dataset_id": None,
        }
        seen = {}

        def spend(*, slug, project_id, dataset_id, month_start, today):
            seen["project_id"] = project_id
            seen["dataset_id"] = dataset_id
            return 4000.0

        with patch.object(hq.client_dashboard_config, "list_budget_overview", return_value=[nixon]), \
             patch.object(hq, "_client_mtd_spend", side_effect=spend), \
             patch.object(hq, "_client_sessions_series", return_value=[]):
            result = hq.build_hq_budget_overview()

        row = result["clients"][0]
        self.assertTrue(row["spend_available"])
        self.assertEqual(row["mtd_spend"], 4000.0)
        self.assertEqual(row["pct_budget"], 40.0)  # 4000 / 10000
        # Resolved to Nixon's built-in mart destination, not skipped.
        self.assertEqual(
            (seen["project_id"], seen["dataset_id"]),
            hq.marketing_service.default_destination(),
        )

    def test_non_nixon_without_project_is_still_skipped(self):
        # The fallback is Nixon-only: a generic client with no project must not
        # silently borrow Nixon's mart.
        c = {
            "client_slug": "someclient",
            "label": "Some Client",
            "monthly_budget_usd": 5000.0,
            "gcp_project_id": None,
            "bq_mart_dataset_id": None,
        }
        with patch.object(hq.client_dashboard_config, "list_budget_overview", return_value=[c]), \
             patch.object(hq, "_client_mtd_spend", side_effect=AssertionError("should not read spend")), \
             patch.object(hq, "_client_sessions_series", return_value=[]):
            result = hq.build_hq_budget_overview()

        row = result["clients"][0]
        self.assertFalse(row["spend_available"])
        self.assertEqual(row["pct_budget"], 0.0)  # budget set, spend unknown -> 0%

    def test_max_workers_env_override(self):
        with patch.dict("os.environ", {"HQ_MAX_WORKERS": "3"}):
            self.assertEqual(hq._hq_max_workers(), 3)
        with patch.dict("os.environ", {"HQ_MAX_WORKERS": "not-an-int"}):
            self.assertEqual(hq._hq_max_workers(), 8)


if __name__ == "__main__":
    unittest.main()
