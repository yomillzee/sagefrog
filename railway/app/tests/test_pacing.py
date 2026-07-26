from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# pacing only depends on dashboard.utils.dates (stdlib only), so no BigQuery stub
# is needed here — unlike the mart-backed service tests.
from dashboard.utils import pacing  # noqa: E402


def _month_weekdays(today: date) -> tuple[int, int, int]:
    """(#weekdays in month, #weekdays month_start..yesterday, #weekdays remaining)."""
    month_start = today.replace(day=1)
    import calendar

    last = calendar.monthrange(today.year, today.month)[1]
    month_end = today.replace(day=last)
    as_of = today - timedelta(days=1)
    total = elapsed = remaining = 0
    d = month_start
    while d <= month_end:
        if d.isoweekday() <= 5:
            total += 1
            if d <= as_of:
                elapsed += 1
            else:
                remaining += 1
        d += timedelta(days=1)
    return total, elapsed, remaining


def _build_spend(today: date, rate) -> dict[str, float]:
    """Daily spend map over the trailing ~80 days ending yesterday (data lags a
    day). ``rate(d)`` returns the spend booked on date ``d``."""
    as_of = today - timedelta(days=1)
    out: dict[str, float] = {}
    d = as_of - timedelta(days=80)
    while d <= as_of:
        out[d.isoformat()] = float(rate(d))
        d += timedelta(days=1)
    return out


class WeekdayLabelTests(unittest.TestCase):
    def test_contiguous_range(self):
        self.assertEqual(pacing.weekday_label([1, 2, 3, 4, 5]), "Mon–Fri")

    def test_gapped_set(self):
        self.assertEqual(pacing.weekday_label([1, 3, 5]), "Mon, Wed, Fri")

    def test_all_seven(self):
        self.assertEqual(pacing.weekday_label([1, 2, 3, 4, 5, 6, 7]), "every day")

    def test_single(self):
        self.assertEqual(pacing.weekday_label([3]), "Wed")


class WeekendOffOnPaceTests(unittest.TestCase):
    """A weekday-only client spending exactly on pace is not flagged 'under'."""

    def setUp(self):
        self.today = date(2026, 7, 20)  # mid-month Monday
        self.budget = 22000.0
        self.total_wd, self.elapsed_wd, self.remaining_wd = _month_weekdays(self.today)
        self.rate = self.budget / self.total_wd  # per-weekday, lands exactly on goal
        spend = _build_spend(
            self.today, lambda d: self.rate if d.isoweekday() <= 5 else 0.0
        )
        self.res = pacing.compute_pacing(
            monthly_budget=self.budget, daily_spend=spend, today=self.today
        )

    def test_detects_weekdays_only(self):
        self.assertEqual(self.res.active_weekdays, [1, 2, 3, 4, 5])
        self.assertEqual(self.res.active_weekdays_source, "auto")

    def test_not_flagged_under_from_weekends(self):
        self.assertEqual(self.res.status, "on_track")

    def test_projection_hits_goal(self):
        self.assertAlmostEqual(self.res.projected_month_end, self.budget, delta=2.0)

    def test_suggested_daily_matches_rate(self):
        self.assertAlmostEqual(self.res.suggested_daily, self.rate, delta=1.0)

    def test_remaining_active_days_are_weekdays(self):
        self.assertEqual(self.res.days_remaining_active, self.remaining_wd)


class AheadOfPaceTests(unittest.TestCase):
    """Overspending on weekdays projects over goal and suggests a lower daily."""

    def setUp(self):
        self.today = date(2026, 7, 20)
        self.budget = 22000.0
        total_wd, _, _ = _month_weekdays(self.today)
        self.base_rate = self.budget / total_wd

        def rate(d):
            if d.isoweekday() > 5:
                return 0.0
            # 20% hot in the current month, on-plan before it.
            return self.base_rate * (1.2 if d >= self.today.replace(day=1) else 1.0)

        spend = _build_spend(self.today, rate)
        self.res = pacing.compute_pacing(
            monthly_budget=self.budget, daily_spend=spend, today=self.today
        )

    def test_status_over(self):
        self.assertEqual(self.res.status, "over")

    def test_suggests_lower_than_base(self):
        self.assertIsNotNone(self.res.suggested_daily)
        self.assertLess(self.res.suggested_daily, self.base_rate)


class BehindPaceTests(unittest.TestCase):
    """Underspending on weekdays projects under goal and suggests a higher daily."""

    def setUp(self):
        self.today = date(2026, 7, 20)
        self.budget = 22000.0
        total_wd, _, _ = _month_weekdays(self.today)
        self.base_rate = self.budget / total_wd

        def rate(d):
            if d.isoweekday() > 5:
                return 0.0
            return self.base_rate * (0.5 if d >= self.today.replace(day=1) else 1.0)

        spend = _build_spend(self.today, rate)
        self.res = pacing.compute_pacing(
            monthly_budget=self.budget, daily_spend=spend, today=self.today
        )

    def test_status_under(self):
        self.assertEqual(self.res.status, "under")

    def test_suggests_higher_than_base(self):
        self.assertIsNotNone(self.res.suggested_daily)
        self.assertGreater(self.res.suggested_daily, self.base_rate)


class EarlyMonthTests(unittest.TestCase):
    """Only one active day elapsed -> verdict is held as 'early', low confidence."""

    def setUp(self):
        self.today = date(2026, 7, 2)  # as_of = Jul 1 (Wed): one weekday elapsed
        self.budget = 22000.0
        total_wd, _, _ = _month_weekdays(self.today)
        rate = self.budget / total_wd
        spend = _build_spend(
            self.today, lambda d: rate if d.isoweekday() <= 5 else 0.0
        )
        self.res = pacing.compute_pacing(
            monthly_budget=self.budget, daily_spend=spend, today=self.today
        )

    def test_status_early(self):
        self.assertEqual(self.res.status, "early")

    def test_low_confidence(self):
        self.assertEqual(self.res.confidence, "low")

    def test_still_offers_soft_estimate(self):
        self.assertIsNotNone(self.res.suggested_daily)


class FlatSevenDayTests(unittest.TestCase):
    """A client that spends evenly all week matches the old calendar projection."""

    def setUp(self):
        self.today = date(2026, 7, 20)
        self.budget = 30000.0
        self.const = 900.0
        spend = _build_spend(self.today, lambda d: self.const)
        self.res = pacing.compute_pacing(
            monthly_budget=self.budget, daily_spend=spend, today=self.today
        )

    def test_all_days_active(self):
        self.assertEqual(self.res.active_weekdays, [1, 2, 3, 4, 5, 6, 7])

    def test_matches_calendar_projection(self):
        import calendar

        days_in_month = calendar.monthrange(self.today.year, self.today.month)[1]
        as_of = self.today - timedelta(days=1)
        days_elapsed = as_of.day
        calendar_projection = self.const * days_elapsed / days_elapsed * days_in_month
        self.assertAlmostEqual(
            self.res.projected_month_end, calendar_projection, delta=2.0
        )


class OverrideTests(unittest.TestCase):
    """An admin override pins the active set regardless of history."""

    def setUp(self):
        self.today = date(2026, 7, 20)
        self.budget = 22000.0
        # Flat 7-day history would auto-detect all 7 days...
        spend = _build_spend(self.today, lambda d: 800.0)
        self.res = pacing.compute_pacing(
            monthly_budget=self.budget,
            daily_spend=spend,
            today=self.today,
            active_weekdays_override=[1, 2, 3, 4, 5],
        )

    def test_override_wins(self):
        self.assertEqual(self.res.active_weekdays, [1, 2, 3, 4, 5])
        self.assertEqual(self.res.active_weekdays_source, "override")

    def test_weekends_carry_no_remaining_days(self):
        _, _, remaining_wd = _month_weekdays(self.today)
        self.assertEqual(self.res.days_remaining_active, remaining_wd)


class NoBudgetTests(unittest.TestCase):
    def test_no_budget_status(self):
        spend = _build_spend(date(2026, 7, 20), lambda d: 500.0)
        res = pacing.compute_pacing(
            monthly_budget=None, daily_spend=spend, today=date(2026, 7, 20)
        )
        self.assertEqual(res.status, "no_budget")
        self.assertIsNone(res.suggested_daily)


if __name__ == "__main__":
    unittest.main()
