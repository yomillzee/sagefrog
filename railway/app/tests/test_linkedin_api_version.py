"""The Linkedin-Version header is a dated value, and nothing else watches it.

LinkedIn publishes a Marketing API version every month and supports each for a
year. The app's default sat at ``202509`` — which LinkedIn sunset on 2026-09-15
— in six separate places, while the README and ``LinkedInEnvSummary`` both
advertised ``202604``. Nothing alerts on a failed connector sync, so the first
sign that every LinkedIn client had stopped receiving data would have been one
of them asking why their numbers were flat.

Two jobs here.

*A clock.* :func:`linkedin_auth.version_sunset_date` turns a ``YYYYMM`` version
into the date it stops working, and the tests fail while there is still time to move — in CI, weeks
before a client sees anything. This is a test that goes red on a date nobody
touched, which is exactly what happened to the LinkedIn Organic fixtures; the
difference is that this one is the point rather than the accident, and the
failure message says what to change.

*One source of truth.* Every place that defaults the header now reads
``linkedin_auth.DEFAULT_LINKEDIN_VERSION``, and the docs quote it. The scan
below fails if a seventh copy of the literal appears.

Also pinned: a sunset version answers *every* call with 426, not the 404 the
fallback ladder was written for, so the ladder has to step down on 426 or a
sunset takes the sync down with it.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import date
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import linkedin_auth  # noqa: E402
import linkedin_service  # noqa: E402
import models  # noqa: E402
from linkedin_auth import DEFAULT_LINKEDIN_VERSION, LinkedInEnv  # noqa: E402

# How much warning we want. A version's sunset is the day syncs start failing;
# failing the build this far ahead leaves room to test a bump rather than ship
# one under time pressure the morning it breaks.
_WARN_DAYS = 45

def _sunset_for(version: str) -> date:
    """Production's own sunset maths, so there is one implementation of it.

    ``SunsetConfigIsRefused.test_sunset_is_a_year_on_and_rounded_early`` pins it
    against literal dates, which is where an error in it would show up.
    """
    sunset = linkedin_auth.version_sunset_date(version)
    if sunset is None:
        raise AssertionError(f"{version!r} is not a LinkedIn YYYYMM version")
    return sunset


def _days_left(version: str) -> int:
    return (_sunset_for(version) - date.today()).days


class SunsetClock(unittest.TestCase):
    def test_default_version_is_not_near_its_sunset(self) -> None:
        left = _days_left(DEFAULT_LINKEDIN_VERSION)
        self.assertGreater(
            left,
            _WARN_DAYS,
            f"LinkedIn version {DEFAULT_LINKEDIN_VERSION} is ~{left} days from sunset "
            f"(~{_sunset_for(DEFAULT_LINKEDIN_VERSION)}). Every LinkedIn sync — Ads and "
            "Organic — starts failing that day, and nothing in this app will tell anyone. "
            "Move linkedin_auth.DEFAULT_LINKEDIN_VERSION to a current version, check the "
            "request shapes still hold (see the adCampaigns note in "
            "linkedin_service.account_performance), and refresh "
            "linkedin_service._LINKEDIN_VERSION_FALLBACKS.",
        )

    def test_every_fallback_rung_is_still_supported(self) -> None:
        for version in linkedin_service._LINKEDIN_VERSION_FALLBACKS:
            with self.subTest(version=version):
                left = _days_left(version)
                self.assertGreater(
                    left,
                    _WARN_DAYS,
                    f"Fallback rung {version} is ~{left} days from sunset "
                    f"(~{_sunset_for(version)}). A sunset rung is not a fallback, it is a "
                    "second failure — drop it from "
                    "linkedin_service._LINKEDIN_VERSION_FALLBACKS and add a live one.",
                )

    def test_fallbacks_are_ordered_oldest_first(self) -> None:
        rungs = list(linkedin_service._LINKEDIN_VERSION_FALLBACKS)
        self.assertEqual(
            rungs,
            sorted(rungs),
            "Rungs are tried in order, so they run oldest first: an endpoint a newer "
            "version dropped is still reached before one only newer versions have.",
        )


class OneSourceOfTruth(unittest.TestCase):
    def test_env_summary_model_default_matches(self) -> None:
        self.assertEqual(
            models.LinkedInEnvSummary.model_fields["linkedin_version"].default,
            DEFAULT_LINKEDIN_VERSION,
            "The Settings page would report a version the app does not send.",
        )

    def test_readme_and_env_example_quote_the_real_default(self) -> None:
        readme = (APP_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            f"default `{DEFAULT_LINKEDIN_VERSION}`",
            readme,
            "README documents a LINKEDIN_VERSION default the code does not use — the "
            "drift that hid the 202509 sunset.",
        )
        env_example = (APP_DIR / ".env.example").read_text(encoding="utf-8")
        self.assertIn(
            f"LINKEDIN_VERSION={DEFAULT_LINKEDIN_VERSION}",
            env_example,
            ".env.example pins a different version, so a fresh deploy starts out drifted.",
        )

    def test_no_module_defaults_a_version_literal(self) -> None:
        """Every read of the version env var goes through resolve_version.

        There were six copies of ``or "202509"``, and they drifted apart from
        each other and from the docs. Anything that defaults the header itself
        also skips the sunset check, which is the part that matters.
        """
        pattern = re.compile(r"\[\"version\"\]\)\s*or\s*([^,\n]+)")
        offenders: list[str] = []
        for path in APP_DIR.rglob("*.py"):
            if "tests" in path.parts or ".venv" in path.parts:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(APP_DIR)}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "This defaults the LinkedIn version inline, so it neither shares the "
            "constant nor gets the sunset check. Use "
            "linkedin_auth.resolve_version(...):\n" + "\n".join(offenders),
        )


class SunsetConfigIsRefused(unittest.TestCase):
    """A stale LINKEDIN_VERSION in Railway must not take LinkedIn down.

    The ladder in linkedin_service only protects calls that go through it, and
    plenty go straight to ``_linkedin_get`` — so a sunset version in the
    environment is not a survivable state. resolve_version prefers the app's
    default over a version LinkedIn has already retired.
    """

    def test_a_sunset_version_falls_back_to_the_default(self) -> None:
        with self.assertLogs("linkedin_auth", level="WARNING") as logs:
            self.assertEqual(linkedin_auth.resolve_version("202509"), DEFAULT_LINKEDIN_VERSION)
        self.assertIn("202509", "".join(logs.output))

    def test_an_unset_version_takes_the_default(self) -> None:
        self.assertEqual(linkedin_auth.resolve_version(None), DEFAULT_LINKEDIN_VERSION)
        self.assertEqual(linkedin_auth.resolve_version("  "), DEFAULT_LINKEDIN_VERSION)

    def test_a_live_version_is_left_alone(self) -> None:
        """Including one ahead of the default — that is a deliberate override."""
        ahead = f"{date.today().year}{date.today().month:02d}"
        self.assertEqual(linkedin_auth.resolve_version(ahead), ahead)

    def test_a_non_version_string_is_not_second_guessed(self) -> None:
        self.assertEqual(linkedin_auth.resolve_version("beta-1"), "beta-1")
        self.assertIsNone(linkedin_auth.version_sunset_date("beta-1"))
        self.assertIsNone(linkedin_auth.version_sunset_date("202613"))

    def test_sunset_is_a_year_on_and_rounded_early(self) -> None:
        self.assertEqual(linkedin_auth.version_sunset_date("202509"), date(2026, 9, 1))
        self.assertEqual(linkedin_auth.version_sunset_date("202604"), date(2027, 4, 1))


class SunsetVersionStepsDown(unittest.TestCase):
    """A sunset version rejects every call with 426, not 404."""

    def test_426_counts_as_a_version_rejection(self) -> None:
        exc = RuntimeError(
            "LinkedIn API error 426 on /adAnalytics (Linkedin-Version=202509): "
            "{'message': 'Upgrade Required'}"
        )
        self.assertTrue(linkedin_service._is_version_rejected(exc))

    def test_404_resource_not_found_still_counts(self) -> None:
        exc = RuntimeError(
            "LinkedIn API error 404 on /adCampaignGroups: RESOURCE_NOT_FOUND"
        )
        self.assertTrue(linkedin_service._is_version_rejected(exc))

    def test_a_real_failure_is_not_mistaken_for_one(self) -> None:
        """A 403 must propagate — stepping down would report it as missing data."""
        exc = RuntimeError(
            "LinkedIn API error 403 on /adAnalytics: not enough permissions"
        )
        self.assertFalse(linkedin_service._is_version_rejected(exc))

    def test_an_id_containing_404_is_not_a_version_rejection(self) -> None:
        exc = RuntimeError(
            "LinkedIn API error 400 on /adAccounts/404404: RESOURCE_NOT_FOUND field"
        )
        self.assertFalse(linkedin_service._is_version_rejected(exc))

    def test_ladder_retries_the_next_rung_after_a_sunset_426(self) -> None:
        env = LinkedInEnv(
            client_id="cid", client_secret="sec", refresh_token="rt", version="202509"
        )
        tried: list[str] = []
        original = linkedin_service._linkedin_get

        def fake_get(path: str, **kwargs: Any) -> dict[str, Any]:
            version = kwargs.get("api_version")
            tried.append(version)
            if version == "202509":
                raise RuntimeError(
                    f"LinkedIn API error 426 on {path} (Linkedin-Version={version}): "
                    "{'message': 'Upgrade Required'}"
                )
            return {"elements": [{"ok": True}]}

        linkedin_service._linkedin_get = fake_get
        try:
            out = linkedin_service._linkedin_get_with_versions(
                "/adAnalytics", access_token="tok", env=env
            )
        finally:
            linkedin_service._linkedin_get = original

        self.assertEqual(out, {"elements": [{"ok": True}]})
        self.assertEqual(tried[0], "202509")
        self.assertEqual(tried[1], DEFAULT_LINKEDIN_VERSION)


if __name__ == "__main__":
    unittest.main()
