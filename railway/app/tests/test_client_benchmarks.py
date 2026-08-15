"""One client's view of the agency benchmark.

``client_benchmarks`` is a pure walk over the payload ``build_agency_benchmarks``
already cached, so these drive it with a hand-built payload and never touch
BigQuery or Postgres. The behaviours worth locking down are the ones a benchmark
gets wrong quietly: counting the client in its own peer group, and presenting a
bucket of one as though it were a distribution.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Same BigQuery stub as test_agency_benchmarks_service: the import tree reaches
# marketing_service, which imports the SDK at module load. Nothing here queries.
if "google.cloud.bigquery" not in sys.modules:
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")
    oauth2_mod = types.ModuleType("google.oauth2")
    service_account_mod = types.ModuleType("google.oauth2.service_account")

    class _FakeScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name, self.type_, self.value = name, type_, value

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

from dashboard.services import agency_benchmarks_service as svc


def _client(slug, *, industries=(), ctr=None, cpa=None, spend=None):
    return {
        "client_slug": slug,
        "label": slug.title(),
        "industries": list(industries),
        "industry_label": ", ".join(industries),
        "metrics": {"ctr": ctr, "cpa": cpa, "spend": spend,
                    "impressions": None, "cpc": None, "cvr": None,
                    "sessions": None, "linkedin_followers": None},
    }


def _payload(clients, industry_buckets):
    """A stand-in for build_agency_benchmarks' cached result."""
    return {
        "clients": clients,
        "industries": [
            {"key": key, "label": label, "client_count": len(slugs),
             "clients": list(slugs), "metrics": {}}
            for key, label, slugs in industry_buckets
        ],
        "agency": {
            "key": "__all__", "label": "All clients",
            "client_count": len(clients),
            "clients": [c["client_slug"] for c in clients], "metrics": {},
        },
        "window": {"key": "last_30d", "label": "last 30 days"},
        "platform": {"key": "all", "label": "All paid media"},
        "generated_at": "2026-08-15T00:00:00Z",
    }


class ClientBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self._real = svc.build_agency_benchmarks

    def tearDown(self):
        svc.build_agency_benchmarks = self._real

    def _install(self, payload):
        svc.build_agency_benchmarks = lambda **kwargs: payload

    def test_peer_group_excludes_the_client_being_measured(self):
        # Subject has a wild CTR. If it were counted in its own peer group the
        # median would be dragged toward it; excluded, peers median a clean 2.0.
        self._install(_payload(
            [
                _client("subject", industries=("health",), ctr=99.0),
                _client("peer-a", industries=("health",), ctr=1.0),
                _client("peer-b", industries=("health",), ctr=2.0),
                _client("peer-c", industries=("health",), ctr=3.0),
            ],
            [("health", "Health care", ["subject", "peer-a", "peer-b", "peer-c"])],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        ctr = got["metrics"]["ctr"]
        self.assertEqual(ctr["value"], 99.0)
        self.assertEqual(ctr["peer"]["median"], 2.0)
        self.assertEqual(ctr["peer"]["n"], 3)
        self.assertEqual(ctr["scope"], "industry")
        self.assertEqual(ctr["peer_label"], "Health care")

    def test_falls_back_to_the_agency_book_when_the_bucket_has_no_peers(self):
        # Only tagged client in its industry — the industry row would be a
        # sample of zero once the subject is removed, so the agency row stands in
        # and says so.
        self._install(_payload(
            [
                _client("subject", industries=("health",), ctr=2.5),
                _client("other-a", industries=("software",), ctr=1.0),
                _client("other-b", industries=("software",), ctr=3.0),
            ],
            [
                ("health", "Health care", ["subject"]),
                ("software", "Software", ["other-a", "other-b"]),
            ],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        ctr = got["metrics"]["ctr"]
        self.assertEqual(ctr["scope"], "agency")
        self.assertEqual(ctr["peer_label"], "all clients")
        self.assertEqual(ctr["peer"]["n"], 2)

    def test_thin_buckets_are_flagged_rather_than_hidden(self):
        self._install(_payload(
            [
                _client("subject", industries=("health",), ctr=2.0),
                _client("peer-a", industries=("health",), ctr=1.0),
            ],
            [("health", "Health care", ["subject", "peer-a"])],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        self.assertTrue(got["metrics"]["ctr"]["peer"]["thin"])
        self.assertEqual(got["thin_sample_max"], svc.THIN_SAMPLE_MAX)

    def test_metrics_with_no_peer_observations_are_omitted(self):
        # Nobody has a CPA, so there is no CPA row at all rather than a zero one.
        self._install(_payload(
            [
                _client("subject", industries=("health",), ctr=2.0),
                _client("peer-a", industries=("health",), ctr=1.0),
                _client("peer-b", industries=("health",), ctr=3.0),
            ],
            [("health", "Health care", ["subject", "peer-a", "peer-b"])],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        self.assertIn("ctr", got["metrics"])
        self.assertNotIn("cpa", got["metrics"])

    def test_only_card_metrics_are_returned(self):
        self._install(_payload(
            [
                _client("subject", industries=("health",), ctr=2.0),
                _client("peer-a", industries=("health",), ctr=1.0),
                _client("peer-b", industries=("health",), ctr=3.0),
            ],
            [("health", "Health care", ["subject", "peer-a", "peer-b"])],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        self.assertTrue(set(got["metrics"]).issubset(set(svc.CARD_METRIC_KEYS)))
        # Sessions is benchmarked on the agency page but has no summary card.
        self.assertNotIn("sessions", got["metrics"])

    def test_an_untagged_client_still_gets_the_agency_comparison(self):
        self._install(_payload(
            [
                _client("subject", industries=(), ctr=2.0),
                _client("peer-a", industries=("health",), ctr=1.0),
                _client("peer-b", industries=("health",), ctr=3.0),
            ],
            [("health", "Health care", ["peer-a", "peer-b"])],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        self.assertTrue(got["available"])
        self.assertEqual(got["reason"], "untagged")
        self.assertEqual(got["metrics"]["ctr"]["scope"], "agency")

    def test_a_client_missing_from_the_benchmark_set_reports_unavailable(self):
        self._install(_payload(
            [_client("peer-a", industries=("health",), ctr=1.0)],
            [("health", "Health care", ["peer-a"])],
        ))
        got = svc.client_benchmarks(client_slug="nobody")
        self.assertFalse(got["available"])
        self.assertEqual(got["reason"], "unknown")
        self.assertEqual(got["metrics"], {})

    def test_multi_tagged_client_uses_its_first_industry(self):
        self._install(_payload(
            [
                _client("subject", industries=("health", "software"), ctr=2.0),
                _client("h-a", industries=("health",), ctr=1.0),
                _client("h-b", industries=("health",), ctr=1.5),
                _client("s-a", industries=("software",), ctr=8.0),
                _client("s-b", industries=("software",), ctr=9.0),
            ],
            [
                ("health", "Health care", ["subject", "h-a", "h-b"]),
                ("software", "Software", ["subject", "s-a", "s-b"]),
            ],
        ))
        got = svc.client_benchmarks(client_slug="subject")
        self.assertEqual(got["metrics"]["ctr"]["peer_label"], "Health care")
        self.assertEqual(got["metrics"]["ctr"]["peer"]["median"], 1.25)


class PeerValuesTests(unittest.TestCase):
    def test_excluded_slug_is_dropped_and_nulls_are_not_counted_as_zero(self):
        payload = _payload(
            [
                _client("me", ctr=5.0),
                _client("a", ctr=1.0),
                _client("b", ctr=None),
            ],
            [],
        )
        got = svc._peer_values(payload, metric="ctr", slugs=["me", "a", "b"], exclude="me")
        self.assertEqual(got, [1.0])


if __name__ == "__main__":
    unittest.main()
