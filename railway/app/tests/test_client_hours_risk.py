"""The Client Hours retainer-risk model, exercised end to end.

The scoring lives in JavaScript (it has to react to the scope toggle without
re-hitting Harvest), so these tests pull the renderer's ``_RISK_JS`` block —
the exact source the page ships — into node and call it. No re-implementation
of the formula in Python, so the tested code and the shipped code cannot drift.

The Python side (``dashboard.utils.hours_risk``) owns the working-day calendar
and the thresholds; those are tested directly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from dashboard.renderers import client_hours_renderer  # noqa: E402
from dashboard.utils import hours_risk  # noqa: E402

NODE = shutil.which("node")

# August 2026: 31 days, starts on a Saturday, 21 working days. Chosen because the
# calendar/working-day split is wide here — a naive calendar-day pace and a
# working-day pace disagree noticeably, which is the whole point of the model.
YEAR, MONTH = 2026, 8
WORKING_DAYS = 21


def _meta(days_elapsed: int, **overrides) -> dict:
    """The month meta the page hands the model, as harvest_service builds it."""
    meta = {
        "days_in_month": 31,
        "days_elapsed": days_elapsed,
        "working_days": hours_risk.working_day_flags(YEAR, MONTH),
        "risk": hours_risk.risk_config(),
    }
    meta.update(overrides)
    return meta


def _flat_series(days_elapsed: int, total: float, *, through: int | None = None) -> list[float]:
    """Cumulative hours accruing evenly across the working days up to ``through``
    (default: every elapsed day), then flat. ``through=0`` gives an all-zero
    series — the client that has logged nothing at all."""
    flags = hours_risk.working_day_flags(YEAR, MONTH)
    cutoff = days_elapsed if through is None else through
    active = [d for d in range(1, days_elapsed + 1) if flags[d - 1] and d <= cutoff]
    per = (total / len(active)) if active else 0.0
    out, run = [], 0.0
    for d in range(1, days_elapsed + 1):
        if d in active:
            run += per
        out.append(round(run, 4))
    return out


class WorkingDayCalendarTest(unittest.TestCase):
    """The Mon–Fri mask and the counts the model paces against."""

    def test_flags_mark_weekdays_only(self):
        flags = hours_risk.working_day_flags(YEAR, MONTH)
        self.assertEqual(len(flags), 31)
        self.assertEqual(sum(flags), WORKING_DAYS)
        self.assertEqual(flags[0], 0)   # Sat Aug 1
        self.assertEqual(flags[1], 0)   # Sun Aug 2
        self.assertEqual(flags[2], 1)   # Mon Aug 3

    def test_counts_split_the_month_at_today(self):
        counts = hours_risk.working_day_counts(YEAR, MONTH, 13)
        self.assertEqual(counts["total"], WORKING_DAYS)
        self.assertEqual(counts["elapsed"], 9)
        # Today (Thu Aug 13) is a working day and counts as still workable, so
        # elapsed + remaining deliberately exceeds total by one.
        self.assertEqual(counts["remaining"], 13)

    def test_counts_are_bounded_at_the_month_edges(self):
        start = hours_risk.working_day_counts(YEAR, MONTH, 0)
        self.assertEqual(start["elapsed"], 0)
        self.assertEqual(start["remaining"], WORKING_DAYS)
        # Aug 31 is a Monday — the last day of the month is still workable, so
        # there is one working day of runway left, not zero.
        end = hours_risk.working_day_counts(YEAR, MONTH, 31)
        self.assertEqual(end["elapsed"], WORKING_DAYS)
        self.assertEqual(end["remaining"], 1)
        # A month ending on a weekend really does run out of runway (May 2026
        # ends on a Sunday), which is the case the model has to survive.
        self.assertEqual(hours_risk.working_day_counts(2026, 5, 31)["remaining"], 0)

    def test_config_reads_env_overrides(self):
        from unittest import mock

        with mock.patch.dict("os.environ", {"HOURS_RISK_BURDEN_THRESHOLD": "3.5"}):
            self.assertEqual(hours_risk.risk_config()["burden_threshold"], 3.5)
        # An unparseable override falls back rather than breaking the page.
        with mock.patch.dict("os.environ", {"HOURS_RISK_BURDEN_THRESHOLD": "banana"}):
            self.assertEqual(
                hours_risk.risk_config()["burden_threshold"],
                hours_risk.DEFAULT_BURDEN_THRESHOLD,
            )


@unittest.skipIf(NODE is None, "node is required to exercise the shipped risk JS")
class RiskModelTest(unittest.TestCase):
    """The shipped scoring block, run against the cases that motivated it."""

    def assess(self, *, series, meta, goal_min=None, goal_max=None, goal_hard=False) -> dict:
        """Run the renderer's own _RISK_JS under node and return assessRisk()."""
        args = json.dumps({
            "series": series, "meta": meta,
            "goalMin": goal_min, "goalMax": goal_max, "goalHard": goal_hard,
        })
        script = (
            client_hours_renderer._RISK_JS
            + f"\nconst a = {args};\n"
            + "console.log(JSON.stringify(assessRisk(a)));\n"
        )
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            self.fail(f"risk JS failed to run:\n{proc.stderr}")
        return json.loads(proc.stdout)

    # ── The case that motivated the rewrite ──────────────────────────────────

    def test_small_retainer_with_nothing_logged_is_not_at_risk(self):
        """A 10h client at 0h mid-month needs under an hour a day to recover, so
        it must stay quiet — the old proportional model called this 'behind'."""
        r = self.assess(series=_flat_series(13, 0), meta=_meta(13), goal_min=10)
        self.assertEqual(r["level"], "on")
        self.assertLess(r["burden"], 0.5)

    def test_large_retainer_half_short_is_at_risk(self):
        """A 200h client at 50h has the same proportional shortfall as a 10h
        client at 2.5h, but needs ~3 extra hours every remaining working day."""
        r = self.assess(series=_flat_series(13, 50), meta=_meta(13), goal_min=200)
        self.assertEqual(r["level"], "risk")
        self.assertAlmostEqual(r["required"], 150 / 13, places=2)
        self.assertAlmostEqual(r["plan"], 200 / WORKING_DAYS, places=2)
        self.assertGreater(r["burden"], 2.0)
        self.assertEqual([x["kind"] for x in r["reasons"]][0], "shortfall")

    def test_severity_ranks_the_bigger_mountain_first(self):
        """The triage order the At risk grid sorts on."""
        big = self.assess(series=_flat_series(13, 0), meta=_meta(13), goal_min=200)
        mid = self.assess(series=_flat_series(13, 50), meta=_meta(13), goal_min=200)
        small = self.assess(series=_flat_series(13, 0), meta=_meta(13), goal_min=10)
        self.assertGreater(big["severity"], mid["severity"])
        self.assertGreater(mid["severity"], small["severity"])

    def test_large_retainer_slightly_behind_stays_on_track(self):
        """Burden is a difference, not a ratio, so a big retainer a little behind
        doesn't false-positive the way a percentage gap would."""
        r = self.assess(series=_flat_series(13, 230), meta=_meta(13), goal_min=500)
        self.assertEqual(r["level"], "on")
        self.assertLess(r["burden"], 0)

    # ── Self-immunity to early-month noise ───────────────────────────────────

    def test_start_of_month_with_no_hours_is_not_at_risk(self):
        """On day 1 the remaining runway is the whole month, so required ≈ plan
        and burden ≈ 0 — no arbitrary 'don't judge before day N' gate needed."""
        r = self.assess(series=_flat_series(3, 0), meta=_meta(3), goal_min=200)
        self.assertEqual(r["level"], "on")
        self.assertLess(abs(r["burden"]), 1.0)

    def test_the_same_shortfall_sharpens_as_the_month_runs_out(self):
        early = self.assess(series=_flat_series(6, 0), meta=_meta(6), goal_min=100)
        late = self.assess(series=_flat_series(24, 0), meta=_meta(24), goal_min=100)
        self.assertLess(early["burden"], late["burden"])
        self.assertEqual(early["level"], "on")
        self.assertEqual(late["level"], "risk")

    # ── Guards ───────────────────────────────────────────────────────────────

    def test_trivial_absolute_gaps_never_alarm(self):
        """A 4h retainer on the last day is a rounding error, not a mountain —
        the gap floor keeps it out of the at-risk book however steep the rate."""
        r = self.assess(series=_flat_series(31, 0), meta=_meta(31), goal_min=4)
        self.assertEqual(r["level"], "on")
        self.assertGreater(r["burden"], 2.0)   # steep, but below the gap floor

    def test_no_goal_is_unpaceable_rather_than_on_track(self):
        r = self.assess(series=_flat_series(13, 5), meta=_meta(13))
        self.assertEqual(r["level"], "none")

    def test_goal_already_met_is_not_at_risk(self):
        r = self.assess(series=_flat_series(13, 90), meta=_meta(13), goal_min=80)
        self.assertEqual(r["level"], "on")
        self.assertEqual(r["gap"], 0)

    # ── Gone quiet ───────────────────────────────────────────────────────────

    def test_quiet_streak_counts_working_days_not_calendar_days(self):
        """Weekends neither break nor extend a streak, so a client last touched
        on a Friday isn't credited with two extra quiet days over the weekend."""
        # Hours through Mon Aug 3 only, then nothing; today is Thu Aug 13.
        r = self.assess(series=_flat_series(13, 20, through=3), meta=_meta(13), goal_min=40)
        self.assertEqual(r["quietDays"], 7)   # Aug 4-7, 10-12 — the weekend excluded

    def test_activity_today_clears_the_quiet_streak(self):
        r = self.assess(series=_flat_series(13, 20, through=13), meta=_meta(13), goal_min=40)
        self.assertEqual(r["quietDays"], 0)

    def test_quiet_lowers_the_bar_but_cannot_flag_a_small_retainer(self):
        """Quiet corroborates; it doesn't trigger. The 10h client is untouched all
        month and still isn't at risk, because it has the runway to recover."""
        r = self.assess(series=_flat_series(13, 0), meta=_meta(13), goal_min=10)
        self.assertGreaterEqual(r["quietDays"], 7)
        self.assertTrue(r["quiet"])
        self.assertEqual(r["level"], "on")

    def test_quiet_flags_a_client_that_would_otherwise_sit_just_under(self):
        """A 200h client at 60h carries a burden of ~1.25h/day — under the 2.0
        bar on its own, but over the halved bar once it has gone dark."""
        loud = self.assess(series=_flat_series(13, 60), meta=_meta(13), goal_min=200)
        quiet = self.assess(series=_flat_series(13, 60, through=3), meta=_meta(13), goal_min=200)
        self.assertEqual(loud["level"], "on")
        self.assertEqual(quiet["level"], "risk")
        self.assertIn("quiet", [x["kind"] for x in quiet["reasons"]])

    # ── Hard ceiling: the overrun that costs money ───────────────────────────

    def test_hard_ceiling_already_breached_is_at_risk(self):
        r = self.assess(series=_flat_series(13, 105), meta=_meta(13),
                        goal_min=80, goal_max=100, goal_hard=True)
        self.assertEqual(r["level"], "risk")
        self.assertIn("ceiling", [x["kind"] for x in r["reasons"]])

    def test_hard_ceiling_projected_overrun_is_at_risk(self):
        r = self.assess(series=_flat_series(13, 70), meta=_meta(13),
                        goal_min=80, goal_max=100, goal_hard=True)
        self.assertEqual(r["level"], "risk")
        self.assertIn("ceiling", [x["kind"] for x in r["reasons"]])

    def test_soft_ceiling_overrun_is_not_a_status(self):
        """Dropping "Growth": beating a soft ceiling is good news, not a flag."""
        r = self.assess(series=_flat_series(13, 70), meta=_meta(13),
                        goal_min=80, goal_max=100, goal_hard=False)
        self.assertEqual(r["level"], "on")

    def test_early_month_projection_cannot_call_a_ceiling_overrun(self):
        """One busy day on day 2 projects absurdly high; the confidence gate holds
        the verdict until enough of the month has actually elapsed."""
        r = self.assess(series=_flat_series(2, 30), meta=_meta(2),
                        goal_min=80, goal_max=100, goal_hard=True)
        self.assertNotIn("ceiling", [x["kind"] for x in r["reasons"]])

    # ── Deceleration ─────────────────────────────────────────────────────────

    def test_deceleration_is_context_not_a_trigger(self):
        """A client whose pace collapsed but is still comfortably ahead stays on
        track — deceleration only ever annotates a card that already flagged."""
        r = self.assess(series=_flat_series(24, 95, through=10), meta=_meta(24), goal_min=100)
        self.assertTrue(r["decelerating"])
        self.assertEqual(r["level"], "on")

    def test_deceleration_needs_two_full_windows_of_history(self):
        r = self.assess(series=_flat_series(6, 20, through=3), meta=_meta(6), goal_min=100)
        self.assertFalse(r["decelerating"])


if __name__ == "__main__":
    unittest.main()
