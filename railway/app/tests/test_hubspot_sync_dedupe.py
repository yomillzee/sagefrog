"""Tests for the HubSpot → BigQuery upsert's duplicate handling.

The eos-conference portal's emails sync failed every run with a BigQuery 400,
"UPDATE/MERGE must match at most one source row for each target row": HubSpot's
paged emails list returned two of its sends twice, so the staging table carried
two rows for one merge key. The first sync inserted both copies (BigQuery only
rejects a duplicated source row once it MATCHES a target row), and every run
afterwards hit the error.

These cover both halves of the fix — the batch is unique on the merge key before
it is staged, and duplicate rows already in the target are cleared so the MERGE
re-inserts exactly one row per id.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hubspot_sync_service as sync  # noqa: E402


class DedupeRowsTests(unittest.TestCase):
    def test_collapses_duplicate_merge_keys(self):
        rows = [
            {"email_id": "1", "sent": 10},
            {"email_id": "2", "sent": 20},
            {"email_id": "1", "sent": 11},
        ]
        out = sync._dedupe_rows(rows, "email_id")
        self.assertEqual([r["email_id"] for r in out], ["1", "2"])
        # last occurrence wins, matching upsert semantics
        self.assertEqual(out[0]["sent"], 11)

    def test_drops_keyless_rows(self):
        rows = [
            {"email_id": "1"},
            {"email_id": None},
            {"email_id": "  "},
            {"sent": 3},
        ]
        out = sync._dedupe_rows(rows, "email_id")
        self.assertEqual(out, [{"email_id": "1"}])

    def test_unique_batch_passes_through_unchanged(self):
        rows = [{"deal_id": "a"}, {"deal_id": "b"}, {"deal_id": "c"}]
        self.assertEqual(sync._dedupe_rows(rows, "deal_id"), rows)

    def test_missing_email_id_parses_to_none_not_the_string_none(self):
        row = sync._parse_email_row({"name": "no id"}, "2026-08-09T00:00:00Z")
        self.assertIsNone(row["email_id"])
        # ...and such a row never reaches BigQuery
        self.assertEqual(sync._dedupe_rows([row], "email_id"), [])


class _FakeJob:
    def __init__(self, affected: int | None = None):
        self.num_dml_affected_rows = affected

    def result(self):
        return self


class _FakeClient:
    """Records the statements _upsert issues, in order."""

    def __init__(self, deleted_rows: int | None = 0):
        self.loaded: list[list[dict]] = []
        self.queries: list[str] = []
        self.deleted_tables: list[str] = []
        self._deleted_rows = deleted_rows

    def load_table_from_json(self, rows, table_id, job_config=None):
        self.loaded.append(list(rows))
        return _FakeJob()

    def query(self, sql):
        self.queries.append(sql)
        return _FakeJob(self._deleted_rows if sql.lstrip().startswith("DELETE") else None)

    def delete_table(self, table_id, not_found_ok=False):
        self.deleted_tables.append(table_id)


_SCHEMA = sync._EMAIL_SCHEMA
_ROW = {f.name: None for f in _SCHEMA}


def _email_row(email_id: str, sent: int) -> dict:
    return {**_ROW, "email_id": email_id, "sent": sent}


class UpsertTests(unittest.TestCase):
    def _upsert(self, client, rows):
        sync._upsert(
            client, "proj", "ds",
            table_name=sync._EMAIL_TABLE, schema=_SCHEMA,
            id_col="email_id", rows=rows,
        )

    def test_staging_load_is_unique_on_the_merge_key(self):
        client = _FakeClient()
        self._upsert(client, [_email_row("1", 5), _email_row("1", 6), _email_row("2", 7)])
        staged = client.loaded[0]
        self.assertEqual([r["email_id"] for r in staged], ["1", "2"])

    def test_target_duplicates_are_cleared_before_the_merge(self):
        client = _FakeClient(deleted_rows=2)
        self._upsert(client, [_email_row("1", 5)])
        kinds = [q.lstrip().split()[0] for q in client.queries]
        self.assertEqual(kinds, ["DELETE", "MERGE"])
        delete_sql = client.queries[0]
        # only ids this batch is about to re-insert, and only duplicated ones
        self.assertIn("HAVING COUNT(*) > 1", delete_sql)
        self.assertIn("SELECT email_id FROM `proj.ds._hs_stg", delete_sql)

    def test_staging_table_is_dropped_even_when_the_merge_fails(self):
        class Boom(_FakeClient):
            def query(self, sql):
                self.queries.append(sql)
                if sql.lstrip().startswith("MERGE"):
                    raise RuntimeError("400 UPDATE/MERGE must match at most one source row")
                return _FakeJob(0)

        client = Boom()
        with self.assertRaises(RuntimeError):
            self._upsert(client, [_email_row("1", 5)])
        self.assertEqual(len(client.deleted_tables), 1)

    def test_empty_batch_after_dedupe_touches_nothing(self):
        client = _FakeClient()
        self._upsert(client, [{**_ROW, "email_id": None}])
        self.assertEqual(client.loaded, [])
        self.assertEqual(client.queries, [])


if __name__ == "__main__":
    unittest.main()
