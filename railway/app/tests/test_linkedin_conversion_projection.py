"""LinkedIn conversion metrics — asking adAnalytics for fields it actually has.

The Campaign Explorer's `Conv.` column was empty for every LinkedIn client, and
it was not an API limitation: adAnalytics has no metric named ``conversions``,
so every projection that asked for one was rejected with a 400 naming the field,
the fallback dropped conversions from the request entirely, and every row was
written with 0. The real names — ``externalWebsiteConversions``,
``oneClickLeads``, ``leadGenerationMailContactInfoShares`` — were already sitting
in ``_CONVERSION_FIELDS``, used to read responses but never to build requests.

What is pinned here:

  * the projection asks for the real metric names, and never a bare
    ``conversions``, at every pivot the explorer depends on;
  * a rejected projection degrades one rung at a time instead of dropping
    straight to no conversions at all;
  * a rejection naming a field other than the one we hardcoded still degrades,
    rather than taking the whole sync down;
  * a real failure (403 from a missing scope) still propagates — the ladder must
    not swallow an access problem and report zero conversions instead;
  * the conversion total does not double count LinkedIn's own components, and
    does not count form or message opens as conversions.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import linkedin_service  # noqa: E402
from linkedin_auth import LinkedInEnv  # noqa: E402

_ENV = LinkedInEnv(
    client_id="cid", client_secret="sec", refresh_token="rt", version="202509"
)

START = date(2026, 8, 1)
END = date(2026, 8, 2)


def _row(campaign_id: str, day: int, **metrics: Any) -> dict[str, Any]:
    return {
        "pivotValues": [f"urn:li:sponsoredCampaign:{campaign_id}"],
        "dateRange": {"start": {"year": 2026, "month": 8, "day": day}},
        "impressions": 100,
        "clicks": 5,
        "costInUsd": "40",
        **metrics,
    }


class FakeApi:
    """Stand-in for _linkedin_get. ``reject`` names fields whose presence in the
    projection makes the call fail the way LinkedIn does — a 400 naming the
    projected field. ``fail_with`` raises something else entirely."""

    def __init__(
        self,
        elements: list[dict[str, Any]] | None = None,
        reject: tuple[str, ...] = (),
        fail_with: Exception | None = None,
    ):
        self.elements = elements or []
        self.reject = reject
        self.fail_with = fail_with
        self.paths: list[str] = []

    def __call__(self, path: str, *, access_token: str, env=None, **kwargs) -> dict:
        self.paths.append(path)
        if self.fail_with is not None:
            raise self.fail_with
        fields = path.split("fields=", 1)[1] if "fields=" in path else ""
        projected = fields.split(",")
        for field in self.reject:
            if field in projected:
                raise RuntimeError(
                    f'LinkedIn API error 400 on {path}: Projected field "{field}" '
                    "is not valid for this pivot"
                )
        return {"elements": self.elements}

    @property
    def projections(self) -> list[list[str]]:
        return [p.split("fields=", 1)[1].split(",") for p in self.paths]


class ConversionProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = linkedin_service._linkedin_get

    def tearDown(self) -> None:
        linkedin_service._linkedin_get = self._orig

    def _campaign_daily(self, api: FakeApi) -> list[dict[str, Any]]:
        linkedin_service._linkedin_get = api
        return linkedin_service.fetch_campaign_daily_metrics(
            "512345678", start=START, end=END, access_token="tok", env=_ENV
        )

    def test_campaign_daily_asks_for_real_conversion_metrics(self) -> None:
        api = FakeApi([_row("111", 1, externalWebsiteConversions="3", oneClickLeads="2")])
        rows = self._campaign_daily(api)
        first = api.projections[0]
        self.assertIn("externalWebsiteConversions", first)
        self.assertIn("oneClickLeads", first)
        # The field that never existed, and the one that went with it.
        self.assertNotIn("conversions", first)
        self.assertNotIn("conversionValueInUsd", first)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conversions"], 5.0)

    def test_conversion_value_uses_the_currency_field_linkedin_reports(self) -> None:
        api = FakeApi([
            _row("111", 1, externalWebsiteConversions="1", conversionValueInLocalCurrency="250.5"),
        ])
        rows = self._campaign_daily(api)
        self.assertIn("conversionValueInLocalCurrency", api.projections[0])
        self.assertEqual(rows[0]["conversion_value"], 250.5)

    def test_value_rejection_keeps_the_conversion_count(self) -> None:
        # Losing revenue must not cost us the counts as well: the next rung down
        # still carries every conversion metric.
        api = FakeApi(
            [_row("111", 1, externalWebsiteConversions="3", oneClickLeads="1")],
            reject=("conversionValueInLocalCurrency",),
        )
        rows = self._campaign_daily(api)
        self.assertEqual(len(api.paths), 2)  # rejected rung + retry
        self.assertEqual(rows[0]["conversions"], 4.0)
        self.assertEqual(rows[0]["conversion_value"], 0.0)

    def test_partial_support_degrades_to_the_metric_that_is_accepted(self) -> None:
        # An account whose pivot refuses lead-gen metrics should still get its
        # website conversions, not fall all the way back to none.
        api = FakeApi(
            [_row("111", 1, externalWebsiteConversions="6")],
            reject=("oneClickLeads", "leadGenerationMailContactInfoShares"),
        )
        rows = self._campaign_daily(api)
        self.assertIn("externalWebsiteConversions", api.projections[-1])
        self.assertEqual(rows[0]["conversions"], 6.0)

    def test_rejection_of_any_field_degrades_rather_than_failing_the_sync(self) -> None:
        # The old code matched one hardcoded error string, so a 400 naming a
        # different field escaped and took the whole LinkedIn sync with it.
        api = FakeApi(
            [_row("111", 1)],
            reject=(
                "externalWebsiteConversions",
                "oneClickLeads",
                "leadGenerationMailContactInfoShares",
                "conversionValueInLocalCurrency",
            ),
        )
        rows = self._campaign_daily(api)
        self.assertEqual(rows[0]["spend"], 40.0)  # spend survived
        self.assertEqual(rows[0]["conversions"], 0.0)

    def test_access_failure_is_not_reported_as_zero_conversions(self) -> None:
        api = FakeApi(fail_with=RuntimeError("LinkedIn API error 403: ACCESS_DENIED"))
        linkedin_service._linkedin_get = api
        with self.assertRaises(RuntimeError):
            linkedin_service.fetch_campaign_daily_metrics(
                "512345678", start=START, end=END, access_token="tok", env=_ENV
            )
        self.assertEqual(len(api.paths), 1)  # no pointless ladder walk

    def test_creative_and_account_pivots_ask_for_the_same_metrics(self) -> None:
        for fetch in (
            linkedin_service.fetch_creative_daily_metrics,
            linkedin_service.fetch_daily_metrics,
        ):
            api = FakeApi([_row("111", 1, externalWebsiteConversions="1")])
            linkedin_service._linkedin_get = api
            fetch("512345678", start=START, end=END, access_token="tok", env=_ENV)
            with self.subTest(fetch=fetch.__name__):
                self.assertIn("externalWebsiteConversions", api.projections[0])
                self.assertNotIn("conversions", api.projections[0])


class ConversionTotalTests(unittest.TestCase):
    def test_components_of_a_total_are_not_added_to_it(self) -> None:
        # externalWebsiteConversions is already postClick + postView. Adding the
        # components would report 8 conversions where LinkedIn counted 4.
        total = linkedin_service._parse_conversions({
            "externalWebsiteConversions": "4",
            "externalWebsitePostClickConversions": "3",
            "externalWebsitePostViewConversions": "1",
        })
        self.assertEqual(total, 4.0)

    def test_opens_are_not_conversions(self) -> None:
        # A form open and an InMail open are views. Counting them inflated the
        # number for anyone running Message Ads.
        total = linkedin_service._parse_conversions({
            "oneClickLeads": "2",
            "oneClickLeadFormOpens": "50",
            "opens": "900",
        })
        self.assertEqual(total, 2.0)

    def test_lead_and_website_conversions_add_up(self) -> None:
        total = linkedin_service._parse_conversions({
            "externalWebsiteConversions": "3",
            "oneClickLeads": "2",
            "leadGenerationMailContactInfoShares": "1",
        })
        self.assertEqual(total, 6.0)


if __name__ == "__main__":
    unittest.main()
