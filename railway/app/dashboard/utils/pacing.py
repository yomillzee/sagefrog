"""Weekday-aware, confidence-scaled budget pacing recommendation.

Pure computation (no I/O). Given a monthly budget, a trailing window of daily
paid spend, today's date, and an optional admin override of active weekdays,
this derives how to adjust daily spend to land on the monthly goal.

Why weekday-aware: many clients pause ads on weekends, so the naive calendar-day
projection (``mtd / days_elapsed * days_in_month``) reports them wildly *under*
pace on a Monday (two zero-spend days just elapsed). Here each day of the month
carries the client's own day-of-week spend weight — a weekend-off client gets ~0
weight on Sat/Sun — so "expected spend so far" and the projection follow the
client's real rhythm instead of a flat calendar.

Why confidence-scaled: paid data lags a day and early-month samples are noisy, so
a firm over/under verdict is withheld until enough active days have elapsed. Until
then the status is ``"early"`` and the message stays soft. Everything is anchored
to ``as_of`` (yesterday) so a viewer checking at 8am understands today isn't in yet.

The single entry point is :func:`compute_pacing`; the helpers are broken out so
they can be unit-tested in isolation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from collections.abc import Mapping

from dashboard.utils.dates import mtd_calendar_bounds

# Trailing window (days, ending as_of) used to learn each client's day-of-week
# spend rhythm. Eight weeks is long enough to average out a light week yet short
# enough to follow a client that recently changed its flighting.
PROFILE_WINDOW_DAYS = 56

# A weekday whose average spend is below this fraction of the busiest weekday's
# average is treated as *inactive* (weight 0). This is what makes "weekend-off"
# fall out automatically: Sat/Sun average near $0, so they drop below the cutoff.
INACTIVE_WEEKDAY_FRACTION = 0.05

# ±band around the goal within which projected month-end reads as "on track".
# Matches hq_budget_service._pace_status so the two never disagree.
PACE_TOLERANCE = 0.05

# Confidence gate: a firm over/under verdict needs at least this many elapsed
# active days AND this fraction of the month's spend-weight already elapsed.
# Below either, the verdict is held as "early" (the morning-guard).
MIN_ACTIVE_DAYS_FOR_VERDICT = 3
MIN_ELAPSED_WEIGHT_FRACTION = 0.15

_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class PacingResult:
    """The pacing recommendation, JSON-serialisable via :meth:`to_dict`."""

    as_of: str
    monthly_budget: float
    mtd_spend: float
    expected_to_date: float | None
    pace_ratio: float | None          # mtd / expected; >1 = spending ahead
    projected_month_end: float | None
    remaining_budget: float | None
    suggested_daily: float | None     # $/active-day to land on goal
    active_weekdays: list[int]        # ISO weekdays 1..7 (Mon..Sun)
    active_weekdays_source: str       # "override" | "auto" | "default"
    active_weekdays_label: str        # e.g. "Mon–Fri"
    days_elapsed_active: int
    days_remaining_active: int
    confidence: str                   # "high" | "low"
    status: str                       # over|under|on_track|early|no_budget
    message: str
    projected_daily: list[dict] = field(default_factory=list)  # [{date, value}]

    def to_dict(self) -> dict:
        return asdict(self)


def _money(v: float | None) -> str:
    """Compact whole-dollar formatting for the recommendation sentence."""
    return "$" + f"{round(v or 0):,}"


def weekday_label(active_weekdays: list[int]) -> str:
    """Human label for a set of ISO weekdays: "Mon–Fri", "Mon, Wed, Fri", …."""
    days = sorted(set(active_weekdays))
    if not days:
        return "none"
    if len(days) == 7:
        return "every day"
    # Collapse a single contiguous run into a dashed range.
    if days == list(range(days[0], days[-1] + 1)):
        if len(days) == 1:
            return _WEEKDAY_ABBR[days[0] - 1]
        return f"{_WEEKDAY_ABBR[days[0] - 1]}–{_WEEKDAY_ABBR[days[-1] - 1]}"
    return ", ".join(_WEEKDAY_ABBR[d - 1] for d in days)


def weekday_averages(
    daily_spend: Mapping[str, float],
    *,
    as_of: date,
    window_days: int = PROFILE_WINDOW_DAYS,
) -> list[float]:
    """Average spend per ISO weekday over the trailing window ending ``as_of``.

    Returns 7 values indexed 0=Mon .. 6=Sun. Missing days count as $0 spend so a
    client that genuinely goes dark on a weekday averages down toward zero.
    """
    sums = [0.0] * 7
    counts = [0] * 7
    start = as_of - timedelta(days=window_days - 1)
    d = start
    while d <= as_of:
        idx = d.isoweekday() - 1
        sums[idx] += float(daily_spend.get(d.isoformat(), 0.0) or 0.0)
        counts[idx] += 1
        d += timedelta(days=1)
    return [(sums[i] / counts[i]) if counts[i] else 0.0 for i in range(7)]


def active_weekdays_from_history(averages: list[float]) -> list[int]:
    """ISO weekdays whose average spend clears the inactive cutoff.

    Empty when there's no spend history at all (caller falls back to a default).
    """
    peak = max(averages) if averages else 0.0
    if peak <= 0:
        return []
    cutoff = peak * INACTIVE_WEEKDAY_FRACTION
    return [i + 1 for i, avg in enumerate(averages) if avg >= cutoff]


def _weekday_weights(averages: list[float], active_weekdays: list[int]) -> list[float]:
    """Per-weekday weights (index 0=Mon): the client's average spend on active
    weekdays, 0 on inactive ones. Falls back to a flat profile when history is
    thin, and back-fills an active weekday that has no history (e.g. one an admin
    override just switched on) with the mean of the active weekdays that do."""
    active = set(active_weekdays)
    positives = [averages[d - 1] for d in active if averages[d - 1] > 0]
    weights = [0.0] * 7
    if not positives:
        # No usable history for the active set — weight every active day equally.
        for d in active:
            weights[d - 1] = 1.0
        return weights
    mean_pos = sum(positives) / len(positives)
    for d in active:
        weights[d - 1] = averages[d - 1] if averages[d - 1] > 0 else mean_pos
    return weights


def compute_pacing(
    *,
    monthly_budget: float | None,
    daily_spend: Mapping[str, float],
    today: date | None = None,
    active_weekdays_override: list[int] | None = None,
    default_active_weekdays: tuple[int, ...] = (1, 2, 3, 4, 5),
) -> PacingResult:
    """Derive the budget-pacing recommendation.

    ``daily_spend`` maps ISO date strings (``YYYY-MM-DD``) to total paid spend on
    that day; it should cover at least the trailing :data:`PROFILE_WINDOW_DAYS`
    ending yesterday. ``active_weekdays_override`` (ISO 1..7), when given, pins the
    active-day set instead of auto-detecting it — the hybrid override.
    """
    month_start, month_end, cur_today = mtd_calendar_bounds(today)
    as_of = cur_today - timedelta(days=1)  # paid data lags a day
    budget = float(monthly_budget or 0.0)

    # --- Active weekdays: override > auto-detect > default -------------------
    averages = weekday_averages(daily_spend, as_of=as_of)
    if active_weekdays_override:
        active_weekdays = sorted({d for d in active_weekdays_override if 1 <= d <= 7})
        source = "override"
    else:
        detected = active_weekdays_from_history(averages)
        if detected:
            active_weekdays = detected
            source = "auto"
        else:
            active_weekdays = list(default_active_weekdays)
            source = "default"

    weights = _weekday_weights(averages, active_weekdays)

    # --- Weight the month by day-of-week ------------------------------------
    total_weight = 0.0
    elapsed_weight = 0.0
    mtd_spend = 0.0
    days_elapsed_active = 0
    days_remaining_active = 0
    day_weight_pairs: list[tuple[date, float]] = []
    d = month_start
    while d <= month_end:
        w = weights[d.isoweekday() - 1]
        total_weight += w
        day_weight_pairs.append((d, w))
        if d <= as_of:
            elapsed_weight += w
            mtd_spend += float(daily_spend.get(d.isoformat(), 0.0) or 0.0)
            if w > 0:
                days_elapsed_active += 1
        else:
            if w > 0:
                days_remaining_active += 1
        d += timedelta(days=1)

    remaining_weight = max(0.0, total_weight - elapsed_weight)
    mtd_spend = round(mtd_spend, 2)

    expected_to_date: float | None = None
    pace_ratio: float | None = None
    projected: float | None = None
    if budget > 0 and total_weight > 0 and elapsed_weight > 0:
        expected_to_date = round(budget * elapsed_weight / total_weight, 2)
        projected = round(mtd_spend * total_weight / elapsed_weight, 2)
        if expected_to_date > 0:
            pace_ratio = round(mtd_spend / expected_to_date, 4)

    remaining_budget = round(budget - mtd_spend, 2) if budget > 0 else None

    # Suggested $/active-day: spread the *remaining* budget across the remaining
    # weight, expressed in "typical active day" units so weekends carry $0.
    suggested_daily: float | None = None
    if budget > 0 and remaining_weight > 0 and remaining_budget is not None:
        active_avg_weight = sum(weights[dd - 1] for dd in active_weekdays) / len(active_weekdays)
        if active_avg_weight > 0:
            remaining_active_equiv = remaining_weight / active_avg_weight
            if remaining_active_equiv > 0:
                suggested_daily = round(max(0.0, remaining_budget) / remaining_active_equiv, 2)

    # --- Confidence + status (the morning-guard) ----------------------------
    elapsed_fraction = (elapsed_weight / total_weight) if total_weight > 0 else 0.0
    confident = (
        days_elapsed_active >= MIN_ACTIVE_DAYS_FOR_VERDICT
        and elapsed_fraction >= MIN_ELAPSED_WEIGHT_FRACTION
    )
    confidence = "high" if confident else "low"

    if budget <= 0:
        status = "no_budget"
    elif projected is None:
        status = "early"
    elif not confident:
        status = "early"
    elif projected > budget * (1 + PACE_TOLERANCE):
        status = "over"
    elif projected < budget * (1 - PACE_TOLERANCE):
        status = "under"
    else:
        status = "on_track"

    # --- Projected cumulative series (weekday-shaped) for the chart line -----
    projected_daily: list[dict] = []
    if projected is not None:
        run = 0.0
        for day, w in day_weight_pairs:
            run += (projected * w / total_weight) if total_weight > 0 else 0.0
            projected_daily.append({"date": day.isoformat(), "value": round(run, 2)})

    message = _build_message(
        status=status,
        budget=budget,
        projected=projected,
        suggested_daily=suggested_daily,
        active_weekdays=active_weekdays,
        as_of=as_of,
    )

    return PacingResult(
        as_of=as_of.isoformat(),
        monthly_budget=round(budget, 2),
        mtd_spend=mtd_spend,
        expected_to_date=expected_to_date,
        pace_ratio=pace_ratio,
        projected_month_end=projected,
        remaining_budget=remaining_budget,
        suggested_daily=suggested_daily,
        active_weekdays=active_weekdays,
        active_weekdays_source=source,
        active_weekdays_label=weekday_label(active_weekdays),
        days_elapsed_active=days_elapsed_active,
        days_remaining_active=days_remaining_active,
        confidence=confidence,
        status=status,
        message=message,
        projected_daily=projected_daily,
    )


def _build_message(
    *,
    status: str,
    budget: float,
    projected: float | None,
    suggested_daily: float | None,
    active_weekdays: list[int],
    as_of: date,
) -> str:
    """One human sentence sized to the confidence in the reading."""
    as_of_txt = as_of.strftime("%b %-d")
    days_txt = weekday_label(active_weekdays)
    daily_txt = _money(suggested_daily) + "/day" if suggested_daily is not None else "your current daily budget"

    if status == "no_budget":
        return "Set a monthly goal to see a suggested daily budget."
    if status == "early":
        if suggested_daily is not None:
            return (
                f"Still early this month — pacing settles after a few active days. "
                f"So far, about {daily_txt} on active days ({days_txt}) tracks to goal "
                f"(as of {as_of_txt})."
            )
        return f"Still early this month — pacing settles after a few active days (as of {as_of_txt})."

    if status == "over":
        over_by = _money((projected or 0) - budget)
        return (
            f"Ahead of pace — projected {over_by} over goal. Lower to about {daily_txt} "
            f"on active days ({days_txt}) to land on {_money(budget)} (as of {as_of_txt})."
        )
    if status == "under":
        under_by = _money(budget - (projected or 0))
        return (
            f"Behind pace — projected {under_by} under goal. Raise to about {daily_txt} "
            f"on active days ({days_txt}) to land on {_money(budget)} (as of {as_of_txt})."
        )
    # on_track
    return (
        f"On track — keep about {daily_txt} on active days ({days_txt}) to land on "
        f"{_money(budget)} (as of {as_of_txt})."
    )
