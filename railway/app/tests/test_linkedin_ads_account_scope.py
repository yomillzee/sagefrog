"""LinkedIn Ads mart reads are scoped to one ad account.

Routing already pins each read to one client's BigQuery project, but a client can
own more than one LinkedIn ad account inside that project — and the dashboard
presents the numbers as one account's. Three of the four mart builders used to
lose that scope: ``campaign_daily_sql`` built the filter into a local and then
never interpolated it, and ``account_summary_sql`` / ``daily_metrics_sql`` took
``account_id`` and ignored it outright, so their totals summed every account in
the mart. A dropped filter is invisible in a single-account project, which is why
it survived — hence these tests assert the clause reaches the emitted SQL rather
than trusting that it was computed.

The id compared is the bare numeric account id: ``build_snapshot`` strips any
``urn:li:sponsoredAccount:`` prefix, and both the raw mirror and the mart store
it bare.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bq_linkedin_ads_service as svc  # noqa: E402

_ACCOUNT = "508590994"
_START = date(2026, 5, 1)
_END = date(2026, 8, 22)

# The builders that read the campaign mart and must scope on `account_id`.
_MART_BUILDERS = (
    ("account_summary_sql", svc.account_summary_sql),
    ("daily_metrics_sql", svc.daily_metrics_sql),
    ("campaign_daily_sql", svc.campaign_daily_sql),
)


class AccountScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        # _mart_table()/_table() resolve a project or raise; pin one for the test
        # and restore whatever was there so no other test inherits it.
        self._prev = os.environ.get("PENN_BQ_LINKEDIN_PROJECT_ID")
        os.environ["PENN_BQ_LINKEDIN_PROJECT_ID"] = "test-project"
        self.addCleanup(self._restore_project)

    def _restore_project(self) -> None:
        if self._prev is None:
            os.environ.pop("PENN_BQ_LINKEDIN_PROJECT_ID", None)
        else:
            os.environ["PENN_BQ_LINKEDIN_PROJECT_ID"] = self._prev

    def test_every_mart_builder_scopes_to_the_account(self) -> None:
        expected = f"AND CAST(account_id AS STRING) = '{_ACCOUNT}'"
        for name, fn in _MART_BUILDERS:
            with self.subTest(builder=name):
                sql = fn(start=_START, end=_END, account_id=_ACCOUNT)
                self.assertIn(
                    expected,
                    sql,
                    f"{name} does not scope its read to the account -- its totals "
                    f"blend every account in the mart",
                )

    def test_creative_builder_scopes_on_its_own_column(self) -> None:
        """The creative fact table names the column source_account_id."""
        sql = svc._creative_daily_sql(start=_START, end=_END, account_id=_ACCOUNT)
        self.assertIn(f"AND CAST(source_account_id AS STRING) = '{_ACCOUNT}'", sql)

    def test_no_account_reads_the_whole_mart(self) -> None:
        """A client with no configured account keeps the unscoped behaviour."""
        for name, fn in _MART_BUILDERS:
            for empty in (None, ""):
                with self.subTest(builder=name, account_id=empty):
                    sql = fn(start=_START, end=_END, account_id=empty)
                    self.assertNotIn("AND CAST(account_id AS STRING)", sql)

    def test_quote_in_account_id_is_escaped(self) -> None:
        """The escape must actually escape.

        Three sibling call sites were written ``.replace("'", "\\'")``, where
        ``"\\'"`` is just an apostrophe in Python -- they replaced a quote with a
        quote. Assert the backslash reaches the SQL.
        """
        sql = svc.campaign_daily_sql(start=_START, end=_END, account_id="1' OR 1=1--")
        self.assertIn(r"'1\' OR 1=1--'", sql)

    def test_scope_helper_is_shared(self) -> None:
        """All four builders route through one helper, so they cannot drift apart.

        The original bug was one builder diverging from its siblings; a single
        source for the clause is what keeps that from recurring.
        """
        self.assertEqual(svc._account_scope(None), "")
        self.assertEqual(
            svc._account_scope(_ACCOUNT),
            f"AND CAST(account_id AS STRING) = '{_ACCOUNT}'",
        )
        self.assertEqual(
            svc._account_scope(_ACCOUNT, column="source_account_id"),
            f"AND CAST(source_account_id AS STRING) = '{_ACCOUNT}'",
        )


if __name__ == "__main__":
    unittest.main()
