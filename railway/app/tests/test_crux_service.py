"""CrUX History parsing — the shapes Google actually returns, not the tidy ones.

Three of these encode real quirks of the API that would silently corrupt the
Site Performance tab if parsing regressed:

  * CLS percentiles arrive as **strings**, not numbers.
  * A period the origin wasn't eligible for arrives as ``null`` (percentiles)
    or the string ``"NaN"`` (histogram densities) — a NaN float reaching
    BigQuery fails the load job, and reaching a chart draws a hole.
  * A site with too little Chrome traffic 404s, which is a fact about the site
    rather than a broken connector, so it must not surface as an error.
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import crux_service  # noqa: E402


def _response(metrics: dict, periods: list[dict]) -> dict:
    return {"record": {"key": {"origin": "https://example.com"}, "metrics": metrics,
                       "collectionPeriods": periods}}


_PERIODS = [
    {"firstDate": {"year": 2026, "month": 1, "day": 1}, "lastDate": {"year": 2026, "month": 1, "day": 28}},
    {"firstDate": {"year": 2026, "month": 1, "day": 8}, "lastDate": {"year": 2026, "month": 2, "day": 4}},
]


class NormalizeOriginTests(unittest.TestCase):
    def test_strips_path_query_and_fragment(self) -> None:
        self.assertEqual(
            crux_service.normalize_origin("https://example.com/pricing?a=1#top"),
            "https://example.com",
        )

    def test_adds_missing_scheme(self) -> None:
        self.assertEqual(crux_service.normalize_origin("example.com"), "https://example.com")

    def test_keeps_port(self) -> None:
        self.assertEqual(crux_service.normalize_origin("https://example.com:8443/x"),
                         "https://example.com:8443")

    def test_blank_stays_blank(self) -> None:
        self.assertEqual(crux_service.normalize_origin("  "), "")


class FetchHistoryTests(unittest.TestCase):
    def test_parses_percentiles_and_densities_per_period(self) -> None:
        payload = _response({
            "largest_contentful_paint": {
                "histogramTimeseries": [
                    {"start": 0, "end": 2500, "densities": [0.90, 0.80]},
                    {"start": 2500, "end": 4000, "densities": [0.07, 0.15]},
                    {"start": 4000, "densities": [0.03, 0.05]},
                ],
                "percentilesTimeseries": {"p75s": [1362, 2600]},
            },
        }, _PERIODS)
        with patch.object(crux_service, "_post", return_value=payload):
            out = crux_service.fetch_history("https://example.com/pricing", "desktop")

        self.assertFalse(out["not_enough_data"])
        self.assertEqual(len(out["periods"]), 2)
        first, second = out["periods"]
        self.assertEqual(first["period_start"], "2026-01-01")
        self.assertEqual(first["period_end"], "2026-01-28")
        self.assertEqual(first["lcp_p75"], 1362.0)
        self.assertEqual(first["lcp_good"], 0.90)
        self.assertEqual(first["lcp_ni"], 0.07)
        self.assertEqual(first["lcp_poor"], 0.03)
        self.assertEqual(second["lcp_p75"], 2600.0)
        self.assertEqual(second["lcp_good"], 0.80)

    def test_cls_percentile_arrives_as_a_string(self) -> None:
        payload = _response({
            "cumulative_layout_shift": {
                "histogramTimeseries": [
                    {"densities": [0.95, 0.94]}, {"densities": [0.03, 0.04]}, {"densities": [0.02, 0.02]},
                ],
                "percentilesTimeseries": {"p75s": ["0.08", "0.09"]},
            },
        }, _PERIODS)
        with patch.object(crux_service, "_post", return_value=payload):
            out = crux_service.fetch_history("https://example.com", "mobile")

        self.assertEqual(out["periods"][0]["cls_p75"], 0.08)
        self.assertIsInstance(out["periods"][0]["cls_p75"], float)

    def test_ineligible_period_becomes_none_not_nan(self) -> None:
        payload = _response({
            "largest_contentful_paint": {
                "histogramTimeseries": [
                    {"densities": [0.90, "NaN"]},
                    {"densities": [0.07, "NaN"]},
                    {"densities": [0.03, "NaN"]},
                ],
                "percentilesTimeseries": {"p75s": [1362, None]},
            },
        }, _PERIODS)
        with patch.object(crux_service, "_post", return_value=payload):
            out = crux_service.fetch_history("https://example.com")

        gap = out["periods"][1]
        self.assertIsNone(gap["lcp_p75"])
        self.assertIsNone(gap["lcp_good"])
        self.assertIsNone(gap["lcp_poor"])
        # Not merely falsy — a NaN float would pass an "is None" check written
        # carelessly and then fail the BigQuery load job.
        for key in ("lcp_p75", "lcp_good", "lcp_ni", "lcp_poor"):
            self.assertNotIsInstance(gap[key], float, key)

    def test_missing_metric_yields_none_columns(self) -> None:
        """Google omits metrics an origin has no data for; every column still exists."""
        payload = _response({"largest_contentful_paint": {
            "histogramTimeseries": [{"densities": [0.9]}, {"densities": [0.07]}, {"densities": [0.03]}],
            "percentilesTimeseries": {"p75s": [1200]},
        }}, _PERIODS[:1])
        with patch.object(crux_service, "_post", return_value=payload):
            out = crux_service.fetch_history("https://example.com")

        row = out["periods"][0]
        for prefix in crux_service.METRIC_PREFIXES:
            for suffix in ("p75", "good", "ni", "poor"):
                self.assertIn(f"{prefix}_{suffix}", row)
        self.assertIsNone(row["inp_p75"])
        self.assertEqual(row["lcp_p75"], 1200.0)

    def test_requests_origin_not_page_url(self) -> None:
        """Origin records aggregate every page, so a low-traffic client is far
        more likely to be eligible than any single URL would be."""
        seen: dict = {}

        def _capture(body, *, timeout):
            seen.update(body)
            return _response({}, [])

        with patch.object(crux_service, "_post", side_effect=_capture):
            crux_service.fetch_history("https://example.com/deep/page?x=1", "mobile")

        self.assertEqual(seen["origin"], "https://example.com")
        self.assertEqual(seen["formFactor"], "PHONE")
        self.assertNotIn("url", seen)
        # collectionPeriodCount is deliberately absent — an unknown field is a
        # hard 400 against this endpoint.
        self.assertNotIn("collectionPeriodCount", seen)

    def test_unknown_strategy_falls_back_to_desktop(self) -> None:
        seen: dict = {}

        def _capture(body, *, timeout):
            seen.update(body)
            return _response({}, [])

        with patch.object(crux_service, "_post", side_effect=_capture):
            out = crux_service.fetch_history("https://example.com", "smartfridge")

        self.assertEqual(seen["formFactor"], "DESKTOP")
        self.assertEqual(out["form_factor"], "desktop")

    def test_blank_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            crux_service.fetch_history("")


class NotEnoughDataTests(unittest.TestCase):
    def test_404_reports_not_enough_data_rather_than_raising(self) -> None:
        with patch.object(crux_service, "_post", side_effect=crux_service.CruxNotFound("no data")):
            out = crux_service.fetch_history("https://tiny-site.example")

        self.assertTrue(out["not_enough_data"])
        self.assertEqual(out["periods"], [])
        self.assertNotIn("error", out)

    def test_empty_collection_periods_also_reads_as_not_enough_data(self) -> None:
        with patch.object(crux_service, "_post", return_value=_response({}, [])):
            out = crux_service.fetch_history("https://example.com")
        self.assertTrue(out["not_enough_data"])

    def test_post_maps_404_to_crux_not_found(self) -> None:
        err = urllib.error.HTTPError(
            "u", 404, "Not Found", {},
            __import__("io").BytesIO(json.dumps({"error": {"message": "not found"}}).encode()),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(crux_service.CruxNotFound):
                crux_service._post({"origin": "https://x.example"}, timeout=5)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_never_raises(self) -> None:
        with patch.object(crux_service, "fetch_history", side_effect=RuntimeError("CrUX 500")):
            snap = crux_service.build_crux_snapshot("https://example.com")

        self.assertEqual(snap["periods"], [])
        self.assertIn("CrUX 500", snap["error"])

    def test_snapshot_without_url_reports_config_error(self) -> None:
        snap = crux_service.build_crux_snapshot("")
        self.assertEqual(snap["error"], "No URL configured")

    def test_not_enough_data_is_not_an_error(self) -> None:
        """The connector card must stay green for a site Google doesn't cover."""
        with patch.object(crux_service, "_post", side_effect=crux_service.CruxNotFound("x")):
            snap = crux_service.build_crux_snapshot("https://tiny.example")

        self.assertNotIn("error", snap)
        self.assertTrue(snap["not_enough_data"])


class ThresholdTests(unittest.TestCase):
    def test_thresholds_cover_every_synced_metric(self) -> None:
        """The renderer draws a goal line per metric from its own copy of these;
        a metric without a threshold would render an unlabelled card."""
        self.assertEqual(set(crux_service.THRESHOLDS), set(crux_service.METRIC_PREFIXES))


if __name__ == "__main__":
    unittest.main()
