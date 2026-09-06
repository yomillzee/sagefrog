"""GSC sync must repair holes, paginate fully, and clean up after a kill.

A redeploy mid-sync hard-kills the worker. sync_range walks newest -> oldest, so
the old MAX(date) gap detection saw the newest day it managed to write and
declared the client up to date, leaving every older day missing forever (38 days
for one client, 25 for another). These tests pin the three failure modes.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import gsc_sync_service as g  # noqa: E402


class _Target:
    bq_project_id = "proj"
    bq_dataset_id = "raw_gsc"
    credentials_env = "GCP_SERVICE_ACCOUNT_JSON"
    site_url = "sc-domain:example.com"
    label = "Example"
    native_dataset_id = None
    is_default_fallback = False


# ---------------------------------------------------------------------------
# 1. Hole detection
# ---------------------------------------------------------------------------

class MissingDateEnumerationTests(unittest.TestCase):
    def _missing(self, present_query, present_page, empty=None, which="both"):
        empty = empty or {}

        def fake_present(client, table_id, start, end):
            got = present_query if "query" in table_id else present_page
            return {d for d in got if start <= d <= end}

        def fake_empty(client, target, dimension, start, end):
            return {d for d in empty.get(dimension, set()) if start <= d <= end}

        with patch.object(g, "_dates_present", fake_present), \
             patch.object(g, "_empty_days", fake_empty):
            return g.get_missing_dates(client=object(), target=_Target(), which=which)

    def test_a_hole_below_the_newest_row_is_found(self):
        # The exact shape that went unnoticed: newest days written, a gap
        # underneath, and MAX(date) reporting "up to date".
        newest = date.today() - timedelta(days=g._GSC_LAG_DAYS)
        head = {newest - timedelta(days=i) for i in range(5)}
        tail = {newest - timedelta(days=i) for i in range(40, 60)}
        missing = self._missing(head | tail, head | tail)

        hole_day = newest - timedelta(days=20)
        self.assertIn(hole_day, missing["query"])
        self.assertIn(hole_day, missing["page"])
        # Days already written are not re-fetched.
        self.assertNotIn(newest, missing["query"])
        for d in tail:
            self.assertNotIn(d, missing["query"])

    def test_newest_first_so_recent_days_land_first(self):
        missing = self._missing(set(), set())
        self.assertEqual(missing["query"], sorted(missing["query"], reverse=True))

    def test_window_stops_at_the_lag_cutoff_and_retention_limit(self):
        missing = self._missing(set(), set())
        self.assertEqual(missing["query"][0], date.today() - timedelta(days=g._GSC_LAG_DAYS))
        self.assertEqual(missing["query"][-1], date.today() - timedelta(days=g._MAX_HISTORY))

    def test_a_day_google_says_is_empty_is_not_chased_forever(self):
        # A zero-impression day never lands a row, so without the marker it
        # would read as "missing" on every run and burn quota permanently.
        quiet = date.today() - timedelta(days=30)
        missing = self._missing(set(), set(), empty={"query": {quiet}})
        self.assertNotIn(quiet, missing["query"])
        self.assertIn(quiet, missing["page"])  # only the marked dimension is skipped

    def test_which_filter_skips_the_other_dimension(self):
        only_q = self._missing(set(), set(), which="queries")
        self.assertTrue(only_q["query"])
        self.assertEqual(only_q["page"], [])

    def test_a_complete_window_reports_nothing_missing(self):
        newest = date.today() - timedelta(days=g._GSC_LAG_DAYS)
        oldest = date.today() - timedelta(days=g._MAX_HISTORY)
        every = {oldest + timedelta(days=i) for i in range((newest - oldest).days + 1)}
        missing = self._missing(every, every)
        self.assertEqual(missing["query"], [])
        self.assertEqual(missing["page"], [])


# ---------------------------------------------------------------------------
# 2. Pagination
# ---------------------------------------------------------------------------

class PaginationTests(unittest.TestCase):
    # Search Console caps a page at 5,000 rows however many you ask for.
    SERVER_PAGE = 5000

    def _fetch(self, total_rows: int):
        calls: list[int] = []

        def fake_post(token, site_url, body):
            start = body["startRow"]
            calls.append(start)
            n = min(self.SERVER_PAGE, max(0, total_rows - start))
            return {"rows": [
                {"keys": ["2026-08-15", "q%d" % (start + i)], "clicks": 1,
                 "impressions": 2, "position": 3.0}
                for i in range(n)
            ]}

        creds = types.SimpleNamespace(valid=True, token="t")
        with patch.object(g, "_gsc_post", fake_post):
            rows = g._fetch_day(creds, "sc-domain:x.com", date(2026, 8, 15), "query")
        return rows, calls

    def test_a_capped_page_is_not_mistaken_for_the_last_page(self):
        rows, calls = self._fetch(12_500)
        self.assertEqual(len(rows), 12_500)
        self.assertEqual(calls, [0, 5000, 10000, 12500])

    def test_exactly_one_server_page_still_terminates(self):
        rows, calls = self._fetch(self.SERVER_PAGE)
        self.assertEqual(len(rows), self.SERVER_PAGE)
        self.assertEqual(calls, [0, 5000])  # the second call comes back empty

    def test_a_short_page_ends_the_day_in_one_call(self):
        rows, calls = self._fetch(120)
        self.assertEqual(len(rows), 120)
        self.assertEqual(calls, [0])

    def test_no_rows_at_all_is_an_empty_day(self):
        rows, calls = self._fetch(0)
        self.assertEqual(rows, [])
        self.assertEqual(calls, [0])

    def test_a_server_that_never_empties_is_capped_not_infinite(self):
        # Full pages forever (a server ignoring startRow would do this) must hit
        # the page guard rather than spin. Pages below _PAGE_PROBE_MIN already
        # terminate on their own, so the guard needs full-looking pages to matter.
        creds = types.SimpleNamespace(valid=True, token="t")
        page = {"rows": [{"keys": ["2026-08-15", "q"], "clicks": 0,
                          "impressions": 0, "position": 1.0}] * g._PAGE_PROBE_MIN}
        with patch.object(g, "_gsc_post", lambda *a, **k: page):
            rows = g._fetch_day(creds, "sc-domain:x.com", date(2026, 8, 15), "query")
        self.assertEqual(len(rows), g._PAGE_PROBE_MIN * g._MAX_PAGES_PER_DAY)

    def test_a_day_just_under_the_probe_floor_costs_one_call(self):
        rows, calls = self._fetch(g._PAGE_PROBE_MIN - 1)
        self.assertEqual(len(rows), g._PAGE_PROBE_MIN - 1)
        self.assertEqual(calls, [0])


# ---------------------------------------------------------------------------
# 3. Retryable 403s
# ---------------------------------------------------------------------------

class RetryableErrorTests(unittest.TestCase):
    def test_quota_403_is_retryable_but_permission_403_is_not(self):
        quota = g.GscApiError(403, "Quota exceeded for quota metric Queries", "s")
        rate = g.GscApiError(403, "rateLimitExceeded: too many requests", "s")
        denied = g.GscApiError(403, "User does not have sufficient permission for site", "s")
        self.assertTrue(g._is_retryable(quota))
        self.assertTrue(g._is_retryable(rate))
        self.assertFalse(g._is_retryable(denied))

    def test_429_and_5xx_stay_retryable(self):
        for code in (429, 500, 503):
            self.assertTrue(g._is_retryable(g.GscApiError(code, "", "s")))

    def test_404_is_not_retryable(self):
        self.assertFalse(g._is_retryable(g.GscApiError(404, "not found", "s")))

    def test_a_transient_failure_retries_then_succeeds(self):
        seq = [g.GscApiError(403, "rateLimitExceeded", "s"), {"rows": []}]

        def fake_post(*a, **k):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        creds = types.SimpleNamespace(valid=True, token="t")
        with patch.object(g, "_gsc_post", fake_post), \
             patch.object(g.time, "sleep", lambda s: None):
            rows = g._fetch_day(creds, "sc-domain:x.com", date(2026, 8, 15), "query")
        self.assertEqual(rows, [])
        self.assertEqual(seq, [])

    def test_retries_are_bounded(self):
        creds = types.SimpleNamespace(valid=True, token="t")
        calls = {"n": 0}

        def always_rate_limited(*a, **k):
            calls["n"] += 1
            raise g.GscApiError(403, "rateLimitExceeded", "s")

        with patch.object(g, "_gsc_post", always_rate_limited), \
             patch.object(g.time, "sleep", lambda s: None):
            with self.assertRaises(g.GscApiError):
                g._fetch_day(creds, "sc-domain:x.com", date(2026, 8, 15), "query")
        self.assertEqual(calls["n"], g._MAX_RETRIES + 1)

    def test_a_permission_403_fails_the_day_immediately(self):
        creds = types.SimpleNamespace(valid=True, token="t")
        calls = {"n": 0}

        def denied(*a, **k):
            calls["n"] += 1
            raise g.GscApiError(403, "User does not have sufficient permission", "s")

        with patch.object(g, "_gsc_post", denied), \
             patch.object(g.time, "sleep", lambda s: None):
            with self.assertRaises(g.GscApiError):
                g._fetch_day(creds, "sc-domain:x.com", date(2026, 8, 15), "query")
        self.assertEqual(calls["n"], 1)  # no pointless waiting on a hard denial


# ---------------------------------------------------------------------------
# 4. Staging cleanup after a killed sync
# ---------------------------------------------------------------------------

class StagingReapTests(unittest.TestCase):
    def _client(self, tables):
        deleted: list[str] = []
        client = types.SimpleNamespace(
            list_tables=lambda ds: tables,
            delete_table=lambda tid, not_found_ok=False: deleted.append(tid),
        )
        return client, deleted

    def test_an_old_orphan_is_deleted_and_real_tables_are_not(self):
        old = datetime.now(UTC) - timedelta(days=40)
        tables = [
            types.SimpleNamespace(table_id="fact_gsc_query_daily", modified=old),
            types.SimpleNamespace(table_id="fact_gsc_query_daily_stg_abc", modified=old),
        ]
        client, deleted = self._client(tables)
        self.assertEqual(g._reap_stale_staging(client, "proj", "raw_gsc"), 1)
        self.assertEqual(deleted, ["proj.raw_gsc.fact_gsc_query_daily_stg_abc"])

    def test_a_fresh_staging_table_is_left_for_the_worker_using_it(self):
        fresh = datetime.now(UTC) - timedelta(minutes=2)
        tables = [types.SimpleNamespace(table_id="fact_gsc_page_daily_stg_xyz", modified=fresh)]
        client, deleted = self._client(tables)
        self.assertEqual(g._reap_stale_staging(client, "proj", "raw_gsc"), 0)
        self.assertEqual(deleted, [])

    def test_a_listing_failure_never_breaks_the_sync(self):
        def boom(ds):
            raise RuntimeError("no permission to list")
        client = types.SimpleNamespace(list_tables=boom, delete_table=lambda *a, **k: None)
        self.assertEqual(g._reap_stale_staging(client, "proj", "raw_gsc"), 0)


# ---------------------------------------------------------------------------
# 5. sync_range only touches the days it was handed
# ---------------------------------------------------------------------------

class SyncRangeDateListTests(unittest.TestCase):
    def _run(self, dates, fetched_rows=None, fail_on=None):
        fetched: list[tuple] = []
        recorded: list[dict] = []

        def fake_fetch(creds, site_url, day, dimension):
            fetched.append((day, dimension))
            if fail_on and day in fail_on:
                raise g.GscApiError(403, "denied", site_url)
            return list(fetched_rows or [])

        with patch.object(g, "_gsc_read_creds", lambda t=None: object()),              patch.object(g, "_bq_client", lambda t=None: object()),              patch.object(g, "_ensure_tables", lambda c, t=None: ("q.tbl", "p.tbl")),              patch.object(g, "_fetch_day", fake_fetch),              patch.object(g, "_upsert", lambda *a, **k: len(a[2])),              patch.object(g, "_record_empty_days",
                          lambda c, t, rows: recorded.extend(rows)):
            result = g.sync_range(
                min(dates), max(dates), site_url="sc-domain:x.com",
                target=_Target(), dates=dates,
            )
        return result, fetched, recorded

    def test_only_the_listed_days_are_fetched(self):
        holes = [date(2026, 7, 10), date(2026, 7, 12)]
        result, fetched, _ = self._run(holes)
        days = {d for d, _ in fetched}
        self.assertEqual(days, set(holes))
        self.assertNotIn(date(2026, 7, 11), days)   # the span between them is intact
        self.assertEqual(result["days_synced"], 2)

    def test_days_are_processed_newest_first(self):
        holes = [date(2026, 7, 1), date(2026, 7, 20), date(2026, 7, 10)]
        _, fetched, _ = self._run(holes)
        seen = [d for d, dim in fetched if dim == "query"]
        self.assertEqual(seen, sorted(holes, reverse=True))

    def test_an_empty_day_is_marked_so_it_is_not_chased_again(self):
        _, _, recorded = self._run([date(2026, 7, 10)], fetched_rows=[])
        self.assertEqual(
            sorted(r["dimension"] for r in recorded), ["page", "query"]
        )
        self.assertTrue(all(r["date"] == "2026-07-10" for r in recorded))

    def test_a_day_with_rows_is_not_marked_empty(self):
        row = {"date": "2026-07-10", "query": "x", "organic_clicks": 1,
               "organic_impressions": 1, "organic_sum_position": 0.0,
               "is_anonymized_query": False}
        result, _, recorded = self._run([date(2026, 7, 10)], fetched_rows=[row])
        self.assertEqual(recorded, [])
        self.assertEqual(result["query_rows"], 1)

    def test_a_failed_day_is_not_marked_empty_so_it_retries(self):
        day = date(2026, 7, 10)
        result, _, recorded = self._run([day], fail_on={day})
        self.assertEqual(recorded, [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_days"], 1)

    def test_an_empty_date_list_is_a_no_op_not_a_full_span_sync(self):
        with patch.object(g, "_gsc_read_creds", lambda t=None: object()),              patch.object(g, "_bq_client", lambda t=None: object()),              patch.object(g, "_ensure_tables", lambda c, t=None: ("q", "p")),              patch.object(g, "_fetch_day", lambda *a: self.fail("should not fetch")):
            result = g.sync_range(
                date(2026, 7, 1), date(2026, 7, 30), site_url="sc-domain:x.com",
                target=_Target(), dates=[],
            )
        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(result["days_synced"], 0)

    def test_no_date_list_still_syncs_the_whole_span(self):
        # The CLI's force-this-range behaviour must be unchanged.
        _, fetched, _ = self._run_span(date(2026, 7, 1), date(2026, 7, 3))
        self.assertEqual({d for d, _ in fetched},
                         {date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)})

    def _run_span(self, start, end):
        fetched: list[tuple] = []
        with patch.object(g, "_gsc_read_creds", lambda t=None: object()),              patch.object(g, "_bq_client", lambda t=None: object()),              patch.object(g, "_ensure_tables", lambda c, t=None: ("q", "p")),              patch.object(g, "_fetch_day",
                          lambda c, s, day, dim: fetched.append((day, dim)) or []),              patch.object(g, "_upsert", lambda *a, **k: 0),              patch.object(g, "_record_empty_days", lambda *a: None):
            result = g.sync_range(start, end, site_url="sc-domain:x.com", target=_Target())
        return result, fetched, None


# ---------------------------------------------------------------------------
# 6. The refresh entry point acts on holes, not just the head
# ---------------------------------------------------------------------------

class SyncForRefreshTests(unittest.TestCase):
    def _refresh(self, missing, **kw):
        passed = {}

        def fake_sync_range(start, end, **kwargs):
            passed["start"], passed["end"] = start, end
            passed["dates"] = kwargs.get("dates")
            return {"ok": True, "query_rows": 1, "page_rows": 1, "days_synced": 1}

        with patch.object(g, "_bq_client", lambda t=None: object()),              patch.object(g, "_ensure_tables", lambda c, t=None: ("q", "p")),              patch.object(g, "get_missing_dates", lambda client=None, target=None: missing),              patch.object(g, "sync_range", fake_sync_range):
            result = g.sync_for_refresh(
                site_url="sc-domain:x.com", target=_Target(), **kw
            )
        return result, passed

    def test_a_mid_history_hole_is_synced_even_though_the_head_is_current(self):
        # Precisely the state a killed backfill leaves behind.
        hole = [date(2026, 7, 20), date(2026, 7, 19)]
        result, passed = self._refresh({"query": hole, "page": hole})
        self.assertNotEqual(result.get("status"), "up_to_date")
        self.assertEqual(sorted(passed["dates"]), sorted(hole))

    def test_dimensions_are_unioned_so_neither_is_left_behind(self):
        result, passed = self._refresh({
            "query": [date(2026, 7, 20)],
            "page": [date(2026, 7, 21)],
        })
        self.assertEqual(sorted(passed["dates"]), [date(2026, 7, 20), date(2026, 7, 21)])
        self.assertEqual(passed["start"], date(2026, 7, 20))
        self.assertEqual(passed["end"], date(2026, 7, 21))

    def test_nothing_missing_is_still_a_fast_no_op(self):
        result, passed = self._refresh({"query": [], "page": []})
        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(passed, {})

    def test_a_large_gap_runs_inline_when_the_caller_can_wait(self):
        many = [date(2026, 3, 1) + timedelta(days=i) for i in range(90)]
        result, passed = self._refresh(
            {"query": many, "page": []}, wait_for_backfill=True
        )
        self.assertEqual(result.get("status"), "synced")
        self.assertEqual(len(passed["dates"]), 90)


# ---------------------------------------------------------------------------
# 7. The BigQuery-facing bodies actually run
# ---------------------------------------------------------------------------

class _FakeRow:
    def __init__(self, mapping):
        self._m = mapping

    def items(self):
        return self._m.items()


class _FakeJob:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def result(self, **kw):
        return list(self._rows)


class _FakeBqClient:
    """Just enough BigQuery to run the real query/load/merge paths."""

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.queries: list[str] = []
        self.loaded: list[tuple] = []
        self.created: list[str] = []
        self.deleted: list[str] = []

    def query(self, sql, job_config=None):
        self.queries.append(sql)
        if sql.strip().upper().startswith("MERGE"):
            return _FakeJob()
        return _FakeJob(self._rows)

    def load_table_from_json(self, payload, table_id, job_config=None):
        self.loaded.append((table_id, list(payload)))
        return _FakeJob()

    def create_table(self, table, exists_ok=False):
        self.created.append(getattr(table, "table_id", str(table)))
        return table

    def delete_table(self, table_id, not_found_ok=False):
        self.deleted.append(table_id)


class BigQueryPathTests(unittest.TestCase):
    """These bodies were shipped with an undefined module constant because every
    test patched them out. Exercise them for real against a fake client."""

    WINDOW = (date(2026, 7, 1), date(2026, 7, 31))

    def test_dates_present_parses_what_bigquery_returns(self):
        client = _FakeBqClient([
            _FakeRow({"date": date(2026, 7, 4)}),
            _FakeRow({"date": "2026-07-05"}),   # string dates parse too
            _FakeRow({"date": None}),           # and nulls are skipped
        ])
        got = g._dates_present(client, "proj.raw_gsc.fact_gsc_query_daily", *self.WINDOW)
        self.assertEqual(got, {date(2026, 7, 4), date(2026, 7, 5)})
        self.assertIn("fact_gsc_query_daily", client.queries[0])
        self.assertIn("2026-07-01", client.queries[0])

    def test_dates_present_treats_a_missing_table_as_nothing_present(self):
        class Boom(_FakeBqClient):
            def query(self, sql, job_config=None):
                raise RuntimeError("404 table not found")
        self.assertEqual(g._dates_present(Boom(), "p.d.t", *self.WINDOW), set())

    def test_empty_days_reads_the_marker_table(self):
        client = _FakeBqClient([_FakeRow({"date": date(2026, 7, 9)})])
        got = g._empty_days(client, _Target(), "query", *self.WINDOW)
        self.assertEqual(got, {date(2026, 7, 9)})
        sql = client.queries[0]
        self.assertIn(g._EMPTY_DAYS_TABLE, sql)
        self.assertIn("dimension = 'query'", sql)

    def test_record_empty_days_creates_the_table_and_merges(self):
        client = _FakeBqClient()
        g._record_empty_days(client, _Target(), [
            {"date": "2026-07-09", "dimension": "query"},
        ])
        self.assertTrue(client.created)
        self.assertTrue(any(q.strip().upper().startswith("MERGE") for q in client.queries))
        # The stamp column is checked_at here, not synced_at.
        _tid, payload = client.loaded[0]
        self.assertIn("checked_at", payload[0])
        self.assertNotIn("synced_at", payload[0])
        self.assertTrue(client.deleted)  # staging table cleaned up

    def test_record_empty_days_never_raises_into_the_sync(self):
        class Boom(_FakeBqClient):
            def create_table(self, table, exists_ok=False):
                raise RuntimeError("no permission")
        g._record_empty_days(Boom(), _Target(), [{"date": "2026-07-09", "dimension": "query"}])

    def test_record_empty_days_with_nothing_to_record_is_a_no_op(self):
        client = _FakeBqClient()
        g._record_empty_days(client, _Target(), [])
        self.assertEqual(client.queries, [])
        self.assertEqual(client.created, [])

    def test_get_missing_dates_end_to_end_against_a_fake_client(self):
        """No patching of the helpers -- the whole path runs."""
        newest = date.today() - timedelta(days=g._GSC_LAG_DAYS)
        client = _FakeBqClient([_FakeRow({"date": newest})])
        missing = g.get_missing_dates(client=client, target=_Target())
        # The one day the fake reports present is not chased; the rest are.
        self.assertNotIn(newest, missing["query"])
        self.assertIn(newest - timedelta(days=1), missing["query"])
        self.assertTrue(client.queries)


if __name__ == "__main__":
    unittest.main()
