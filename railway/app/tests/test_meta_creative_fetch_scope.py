from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, UTC
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bq_meta_ads_service  # noqa: E402
import meta_service  # noqa: E402

_ENV = meta_service.MetaEnv(
    app_id="app",
    app_secret="secret",
    access_token="tok",
    business_id="biz",
    api_version="v25.0",
)


def _ad(ad_id: str) -> dict:
    return {
        "id": ad_id,
        "name": f"Ad {ad_id}",
        "campaign": {"id": "c1", "name": "Camp"},
        "adset": {"id": "s1", "name": "Set"},
        "creative": {"id": f"cr-{ad_id}", "image_url": f"img-{ad_id}"},
    }


class TargetedCreativeFetchTests(unittest.TestCase):
    """fetch_ad_creative_metadata_for_ads exists so the sync can refresh a
    handful of ads with ceil(N/25) multi-gets instead of paging every ad the
    account has ever run — the call volume that trips code 17 / subcode
    2446079."""

    def test_batches_into_multi_get_calls(self):
        ad_ids = {f"a{i}" for i in range(60)}
        calls = []

        def fake_graph_get(path, *, access_token, params=None, env=None):
            calls.append((path, params))
            requested = (params or {})["ids"].split(",")
            return {aid: _ad(aid) for aid in requested}

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            rows = meta_service.fetch_ad_creative_metadata_for_ads(
                "act_123", ad_ids, access_token="tok", env=_ENV
            )

        # 60 ids / 25 per batch = 3 multi-gets, no per-account paging at all
        self.assertEqual(len(calls), 3)
        for path, params in calls:
            self.assertEqual(path, "/")
            self.assertLessEqual(
                len(params["ids"].split(",")), meta_service._AD_MEDIA_ID_BATCH
            )
        self.assertEqual(len(rows), 60)
        self.assertEqual({r["ad_id"] for r in rows}, ad_ids)

    def test_rows_match_the_full_account_fetch_shape(self):
        """Both fetches feed the same BigQuery MERGE, so the rows must match."""
        def fake_graph_get(path, *, access_token, params=None, env=None):
            if "ids" in (params or {}):
                return {"a1": _ad("a1")}
            return {"data": [_ad("a1")]}

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            targeted = meta_service.fetch_ad_creative_metadata_for_ads(
                "act_123", ["a1"], access_token="tok", env=_ENV
            )
            full = meta_service.fetch_ad_creative_metadata(
                "act_123", access_token="tok", env=_ENV
            )

        self.assertEqual(targeted, full)
        self.assertEqual(targeted[0]["image_url"], "img-a1")

    def test_batch_failure_falls_back_to_individual_fetches(self):
        def fake_graph_get(path, *, access_token, params=None, env=None):
            if path == "/":  # one inaccessible id poisons the whole multi-get
                raise RuntimeError("Meta Graph API error 400 on /: bad id in batch")
            return _ad(path.lstrip("/"))

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            rows = meta_service.fetch_ad_creative_metadata_for_ads(
                "act_123", ["a1", "a2"], access_token="tok", env=_ENV
            )

        self.assertEqual({r["ad_id"] for r in rows}, {"a1", "a2"})

    def test_missing_id_in_response_is_dropped_not_blanked(self):
        """Meta omits an inaccessible id. Writing a blank row would overwrite a
        good thumbnail with an empty one, so the ad must be left out entirely."""
        def fake_graph_get(path, *, access_token, params=None, env=None):
            return {"a1": _ad("a1")}

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            rows = meta_service.fetch_ad_creative_metadata_for_ads(
                "act_123", ["a1", "a2"], access_token="tok", env=_ENV
            )

        self.assertEqual([r["ad_id"] for r in rows], ["a1"])

    def test_empty_input_makes_no_calls(self):
        with patch.object(meta_service, "_graph_get") as gg:
            rows = meta_service.fetch_ad_creative_metadata_for_ads(
                "act_123", [], access_token="tok", env=_ENV
            )
        self.assertEqual(rows, [])
        gg.assert_not_called()

    def test_missing_permission_returns_empty_without_raising(self):
        """Parity with the full-account fetch: a token without ads_read yields no
        creative rows rather than failing the sync leg."""
        def fake_graph_get(path, *, access_token, params=None, env=None):
            raise RuntimeError("(#200) User has not granted ads_read permission")

        with patch.object(meta_service, "_graph_get", side_effect=fake_graph_get):
            rows = meta_service.fetch_ad_creative_metadata_for_ads(
                "act_123", ["a1"], access_token="tok", env=_ENV
            )
        self.assertEqual(rows, [])


class PlanCreativeFetchTests(unittest.TestCase):
    """The creative fetch is the call that exhausts the ad account budget, so it
    must not run on a sync whose ads already have fresh creatives."""

    def _rows(self, *ad_ids: str) -> list[dict]:
        return [{"ad_id": aid} for aid in ad_ids]

    def _coverage(self, ages_hours: dict[str, float | None], *, available: bool = True):
        now = datetime.now(UTC)
        synced_at = {
            aid: (None if age is None else now - timedelta(hours=age))
            for aid, age in ages_hours.items()
        }
        return {
            "available": available,
            "synced_at": {k: v for k, v in synced_at.items() if v is not None},
        }

    def _plan(self, ad_rows, coverage, *, ad_fetch_failed=False):
        with patch.object(
            bq_meta_ads_service.bigquery_warehouse,
            "meta_ad_creative_coverage",
            return_value=coverage,
        ):
            return bq_meta_ads_service._plan_creative_fetch(
                "123", ad_rows, ad_fetch_failed=ad_fetch_failed
            )

    def test_all_fresh_skips_entirely(self):
        plan = self._plan(self._rows("a1", "a2"), self._coverage({"a1": 1, "a2": 2}))
        self.assertEqual(plan.mode, "skip")
        self.assertEqual(plan.ad_ids, ())

    def test_new_ad_triggers_a_targeted_fetch_for_only_that_ad(self):
        plan = self._plan(self._rows("a1", "a2"), self._coverage({"a1": 1}))
        self.assertEqual(plan.mode, "targeted")
        self.assertEqual(plan.ad_ids, ("a2",))

    def test_aged_out_row_is_refreshed(self):
        plan = self._plan(self._rows("a1", "a2"), self._coverage({"a1": 1, "a2": 999}))
        self.assertEqual(plan.mode, "targeted")
        self.assertEqual(plan.ad_ids, ("a2",))

    def test_many_stale_ads_fall_back_to_one_full_fetch(self):
        count = bq_meta_ads_service._CREATIVE_TARGETED_MAX_ADS + 1
        plan = self._plan(self._rows(*[f"a{i}" for i in range(count)]), self._coverage({}))
        self.assertEqual(plan.mode, "full")
        self.assertEqual(plan.ad_ids, ())

    def test_no_delivering_ads_skips(self):
        plan = self._plan([], self._coverage({}))
        self.assertEqual(plan.mode, "skip")

    def test_unreadable_coverage_fails_open_to_full(self):
        """A wrong skip silently strands the explorer without thumbnails, so any
        uncertainty must resolve to fetching."""
        plan = self._plan(self._rows("a1"), {"available": False, "synced_at": {}})
        self.assertEqual(plan.mode, "full")

    def test_failed_ad_insights_falls_open_to_full(self):
        plan = self._plan(
            self._rows("a1"), self._coverage({"a1": 1}), ad_fetch_failed=True
        )
        self.assertEqual(plan.mode, "full")

    def test_naive_timestamp_is_treated_as_utc_not_stale(self):
        coverage = {
            "available": True,
            "synced_at": {"a1": datetime.utcnow() - timedelta(hours=1)},
        }
        plan = self._plan(self._rows("a1"), coverage)
        self.assertEqual(plan.mode, "skip")

    def test_refresh_window_is_tunable(self):
        with patch.dict("os.environ", {"META_CREATIVE_REFRESH_HOURS": "0.5"}):
            plan = self._plan(self._rows("a1"), self._coverage({"a1": 1}))
        self.assertEqual(plan.mode, "targeted")

    def test_garbage_refresh_window_uses_the_default(self):
        with patch.dict("os.environ", {"META_CREATIVE_REFRESH_HOURS": "soon"}):
            self.assertEqual(
                bq_meta_ads_service._creative_refresh_hours(),
                bq_meta_ads_service._CREATIVE_REFRESH_HOURS,
            )


class SyncCreativeWiringTests(unittest.TestCase):
    """The plan has to actually reach the fetch: assert sync_meta_to_bq spends
    (or does not spend) Meta calls to match it."""

    def _run_sync(self, plan):
        from datetime import date as _date

        warehouse = bq_meta_ads_service.bigquery_warehouse
        ad_fetch = type("AdFetch", (), {"rows": [{"ad_id": "a1"}], "action_rows": []})()
        mirror = {"enabled": True, "rows_upserted": 0}

        with patch.object(bq_meta_ads_service, "_plan_creative_fetch", return_value=plan), \
             patch.object(warehouse, "ensure_meta_tables"), \
             patch.object(warehouse, "mirror_meta_campaign_daily_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_adset_daily_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_ad_daily_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_ad_creative_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_ad_conversion_action_batch", return_value=mirror), \
             patch.object(warehouse, "create_meta_mart_views", return_value={"views": []}), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_result_resolver", return_value=None), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_campaign_daily_metrics", return_value=[]), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_adset_daily_metrics", return_value=[]), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_ad_daily_metrics_with_actions", return_value=ad_fetch), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_ad_creative_metadata", return_value=[]) as full, \
             patch.object(bq_meta_ads_service.meta_service, "fetch_ad_creative_metadata_for_ads", return_value=[]) as targeted:
            result = bq_meta_ads_service.sync_meta_to_bq(
                "act_123", start=_date(2026, 8, 1), end=_date(2026, 8, 26),
                access_token="tok",
            )
        return result, full, targeted

    def test_skip_spends_no_creative_calls(self):
        plan = bq_meta_ads_service._CreativePlan("skip", (), "fresh")
        result, full, targeted = self._run_sync(plan)
        full.assert_not_called()
        targeted.assert_not_called()
        self.assertEqual(result["creative_fetch"], "skip")
        self.assertNotIn("meta_creative_fetch", result["errors"])

    def test_targeted_passes_only_the_planned_ads(self):
        plan = bq_meta_ads_service._CreativePlan("targeted", ("a2", "a3"), "2 need creatives")
        result, full, targeted = self._run_sync(plan)
        full.assert_not_called()
        targeted.assert_called_once()
        self.assertEqual(targeted.call_args.args[1], ("a2", "a3"))
        self.assertEqual(result["creative_fetch"], "targeted")

    def test_full_uses_the_paged_account_fetch(self):
        plan = bq_meta_ads_service._CreativePlan("full", (), "coverage unreadable")
        result, full, targeted = self._run_sync(plan)
        targeted.assert_not_called()
        full.assert_called_once()
        self.assertEqual(result["creative_fetch"], "full")

    def test_rate_limited_creative_fetch_still_records_the_other_rows(self):
        """The regression that started this: a creative fetch failing on the ad
        account call limit must stay one leg's error, not the whole sync."""
        plan = bq_meta_ads_service._CreativePlan("full", (), "coverage unreadable")
        from datetime import date as _date

        warehouse = bq_meta_ads_service.bigquery_warehouse
        ad_fetch = type("AdFetch", (), {"rows": [{"ad_id": "a1"}], "action_rows": []})()
        mirror = {"enabled": True, "rows_upserted": 7}
        boom = RuntimeError(
            "Meta Graph API error 400 on /act_123/ads: "
            "{'error': {'code': 17, 'error_subcode': 2446079}}"
        )

        with patch.object(bq_meta_ads_service, "_plan_creative_fetch", return_value=plan), \
             patch.object(warehouse, "ensure_meta_tables"), \
             patch.object(warehouse, "mirror_meta_campaign_daily_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_adset_daily_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_ad_daily_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_ad_creative_batch", return_value=mirror), \
             patch.object(warehouse, "mirror_meta_ad_conversion_action_batch", return_value=mirror), \
             patch.object(warehouse, "create_meta_mart_views", return_value={"views": []}), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_result_resolver", return_value=None), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_campaign_daily_metrics", return_value=[]), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_adset_daily_metrics", return_value=[]), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_ad_daily_metrics_with_actions", return_value=ad_fetch), \
             patch.object(bq_meta_ads_service.meta_service, "fetch_ad_creative_metadata", side_effect=boom):
            result = bq_meta_ads_service.sync_meta_to_bq(
                "act_123", start=_date(2026, 8, 1), end=_date(2026, 8, 26),
                access_token="tok",
            )

        self.assertIn("2446079", result["errors"]["meta_creative_fetch"])
        self.assertEqual(result["ad_rows"], 7)
        self.assertEqual(result["campaign_rows"], 7)


if __name__ == "__main__":
    unittest.main()
