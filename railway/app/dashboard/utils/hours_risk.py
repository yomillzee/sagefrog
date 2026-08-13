"""Working-day calendar and tunables for the Client Hours retainer-risk model.

The Client Hours page answers one question: *which clients are at risk of not
fulfilling their monthly retainer hours?* The naive answer — project the
calendar-day run rate and compare it to the goal — gets two things badly wrong,
and this module supplies what the page needs to fix both.

**Calendar days aren't working days.** Hours are logged Mon–Fri, so a projection
divided by ``days_elapsed / days_in_month`` reports every client as collapsing
after a long weekend. :func:`working_day_flags` hands the page a per-day mask of
the month so it can pace against the days work actually happens on. (Same
motivation as :mod:`dashboard.utils.pacing`, which does this for ad spend.)

**Shortfall is absolute, not proportional.** A 10h/month client that has logged
nothing by mid-month needs ~0.8h/day to recover — a single meeting. A 200h client
sitting at 50h needs ~12.5h/day, which is most of two people. Both are "50% off
pace" to a ratio, but only one is a problem. So the page scores clients on
**burden** — the *extra hours per remaining working day* a client now needs
beyond its planned daily rate:

    gap            = goal_min − hours logged
    required_daily = gap / remaining working days
    plan_daily     = goal_min / total working days
    burden         = required_daily − plan_daily

Being a difference rather than a ratio, burden stays near zero for a large
retainer that is slightly behind, and it self-immunises against early-month
noise: on day 1 the remaining working days are the whole month, so
``required_daily ≈ plan_daily`` and burden ≈ 0. No "don't judge before day N"
gate is needed — the reading sharpens on its own as the runway shrinks.

The scoring itself lives in the page's ``_RISK_JS`` block (it has to react to the
scope toggle without re-hitting Harvest); this module owns the calendar and the
thresholds, which ship to the browser in the overview payload's ``meta.risk``.
"""

from __future__ import annotations

import calendar
import os

# ── Tunables (all env-overridable; shipped to the page in ``meta.risk``) ──────

# Burden, in extra hours per remaining working day, at which a client is called
# at risk. 2.0 is roughly a quarter of an FTE of unplanned catch-up sustained
# every remaining day — enough of a mountain to warrant a call, while a client
# needing an extra 20 minutes a day stays quiet.
DEFAULT_BURDEN_THRESHOLD = 2.0

# Shortfalls below this many hours never alarm, whatever the burden says. Keeps
# a 4h/month retainer from screaming on the last day of the month over what is,
# in absolute terms, a rounding error to the agency.
DEFAULT_GAP_FLOOR = 5.0

# Consecutive *working* days with no logged hours before a client counts as
# having gone quiet. Gone-quiet is the leading indicator (client not approving,
# AM not proactive) and shows up before the pace math moves.
DEFAULT_QUIET_WORKING_DAYS = 7

# Quiet clients are held to a lower bar: a client nobody has touched in over a
# week won't close its gap by accident, so it flags at half the usual burden.
# Quiet corroborates rather than triggers — on its own it can't flag the small
# retainer that has plenty of runway left.
DEFAULT_QUIET_MULTIPLIER = 0.5

# Trailing working-day window for the deceleration read, and the fraction of the
# earlier run rate below which the trailing window counts as decelerating. This
# is context on the card, never a trigger.
DEFAULT_DECEL_WINDOW = 5
DEFAULT_DECEL_RATIO = 0.5

# Confidence gate on the run-rate *projection* (used only for the hard-ceiling
# overrun call). Early-month projections swing wildly, so no overrun verdict is
# issued until this many working days and this fraction of the month's working
# days have elapsed. Mirrors the morning-guard in dashboard.utils.pacing.
DEFAULT_MIN_ELAPSED_WORKING_DAYS = 3
DEFAULT_MIN_ELAPSED_FRACTION = 0.15

# Slack on the hard ceiling before a projected overrun is called, so a client
# landing a hair over its cap isn't flagged on projection noise alone.
DEFAULT_CEILING_TOLERANCE = 1.03


def _env_float(name: str, default: float) -> float:
    """Env override for a tunable, falling back on anything unparseable."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


def working_day_flags(year: int, month: int) -> list[int]:
    """Per-day Mon–Fri mask for the month: ``1`` on a working day, ``0`` else.

    Index 0 is the 1st of the month, so the page can line the mask up directly
    with the 1-based day index its cumulative hour series is keyed on.

    Weekends are the only non-working days here. Public holidays are deliberately
    not modelled: agency staff log against retainers through most of them, and a
    wrong holiday calendar would bias every client's burden in the same direction.
    """
    days = calendar.monthrange(year, month)[1]
    return [1 if calendar.weekday(year, month, d) < 5 else 0 for d in range(1, days + 1)]


def working_day_counts(year: int, month: int, days_elapsed: int) -> dict[str, int]:
    """Working days in the month, through today, and still to come.

    ``remaining`` *includes today* — hours can still be logged against it — so
    ``elapsed + remaining`` exceeds ``total`` by one on a working day. That's
    intentional: the two counts feed different denominators (elapsed drives the
    run-rate projection, remaining drives the catch-up rate), and neither wants
    today omitted.
    """
    flags = working_day_flags(year, month)
    today = max(0, min(len(flags), int(days_elapsed or 0)))
    return {
        "total": sum(flags),
        "elapsed": sum(flags[: max(0, today)]),
        "remaining": sum(flags[max(0, today - 1) :]) if today else sum(flags),
    }


def risk_config() -> dict[str, float]:
    """The thresholds the page scores against, resolved from the environment."""
    return {
        "burden_threshold": _env_float("HOURS_RISK_BURDEN_THRESHOLD", DEFAULT_BURDEN_THRESHOLD),
        "gap_floor": _env_float("HOURS_RISK_GAP_FLOOR", DEFAULT_GAP_FLOOR),
        "quiet_working_days": _env_int("HOURS_RISK_QUIET_DAYS", DEFAULT_QUIET_WORKING_DAYS),
        "quiet_multiplier": _env_float("HOURS_RISK_QUIET_MULTIPLIER", DEFAULT_QUIET_MULTIPLIER),
        "decel_window": _env_int("HOURS_RISK_DECEL_WINDOW", DEFAULT_DECEL_WINDOW),
        "decel_ratio": _env_float("HOURS_RISK_DECEL_RATIO", DEFAULT_DECEL_RATIO),
        "min_elapsed_working_days": _env_int(
            "HOURS_RISK_MIN_ELAPSED_DAYS", DEFAULT_MIN_ELAPSED_WORKING_DAYS
        ),
        "min_elapsed_fraction": _env_float(
            "HOURS_RISK_MIN_ELAPSED_FRACTION", DEFAULT_MIN_ELAPSED_FRACTION
        ),
        "ceiling_tolerance": _env_float("HOURS_RISK_CEILING_TOLERANCE", DEFAULT_CEILING_TOLERANCE),
    }
