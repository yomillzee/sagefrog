from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import meta_service  # noqa: E402


class MetaInsightsChunkingTests(unittest.TestCase):
    def test_splits_range_into_chunks_and_concatenates(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_insights(path, *, access_token, params, env=None):
            tr = params["time_range"]
            calls.append((tr, params.get("level")))
            # one row per chunk so we can count concatenation
            return [{"chunk": tr}]

        with patch.object(meta_service, "_graph_insights", side_effect=fake_insights):
            rows = meta_service._graph_insights_chunked(
                "/act_1/insights",
                access_token="t",
                params={"level": "ad", "time_increment": 1},
                start=date(2026, 6, 14),
                end=date(2026, 7, 13),
                chunk_days=7,
            )

        # 30-day span in 7-day windows -> 5 chunks (7+7+7+7+2), no time_range leaks in.
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(rows), 5)
        # First chunk covers exactly 7 days starting at start.
        self.assertIn('"since": "2026-06-14"', calls[0][0])
        self.assertIn('"until": "2026-06-20"', calls[0][0])
        # Last chunk is clamped to end (no overrun past 07-13).
        self.assertIn('"until": "2026-07-13"', calls[-1][0])

    def test_single_failing_chunk_is_skipped_not_fatal(self) -> None:
        def fake_insights(path, *, access_token, params, env=None):
            if '"since": "2026-06-14"' in params["time_range"]:
                raise RuntimeError("Meta async insights job timed out")
            return [{"ok": True}]

        with patch.object(meta_service, "_graph_insights", side_effect=fake_insights):
            rows = meta_service._graph_insights_chunked(
                "/act_1/insights",
                access_token="t",
                params={"level": "ad"},
                start=date(2026, 6, 14),
                end=date(2026, 7, 13),
                chunk_days=7,
            )

        # 4 of 5 chunks succeed -> recent days still flow instead of freezing.
        self.assertEqual(len(rows), 4)

    def test_total_wipeout_raises_so_connector_surfaces_it(self) -> None:
        def always_fail(path, *, access_token, params, env=None):
            raise RuntimeError("rate limited")

        with patch.object(meta_service, "_graph_insights", side_effect=always_fail):
            with self.assertRaises(RuntimeError):
                meta_service._graph_insights_chunked(
                    "/act_1/insights",
                    access_token="t",
                    params={"level": "ad"},
                    start=date(2026, 6, 14),
                    end=date(2026, 7, 13),
                    chunk_days=7,
                )


if __name__ == "__main__":
    unittest.main()
