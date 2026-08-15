"""Per-metric targets for the paid summary cards — "is this number good?".

The summary cards have always shown a value and a vs-previous delta, which says
which way a number moved but never whether it landed where it was supposed to.
This module owns the other half: an admin stores a target per metric on the
client's config, and the dashboard grades the live value against it.

Two things make that honest across an arbitrary date range:

* **Cumulative metrics are stored as monthly totals and prorated to the window.**
  A $50k monthly spend goal over a 10-day slice of a 31-day month is a $16.1k
  target for that slice, so the same stored goal grades a week, a month-to-date,
  or a custom range without the client having to restate it. Rate metrics (CTR,
  CPC, CPA) are not prorated — a rate is a rate whatever the window.
* **Direction decides what "good" means.** More clicks is good, a lower CPA is
  good, and spend is neither — it is graded as a *band*, because a client that
  spends half its budget missed the plan just as surely as one that doubled it.

The pacing math lives here (and is unit-tested) rather than in the dashboard's
JavaScript: :func:`resolve_goals` hands the page an already-prorated ``target``
plus the grading mode, so the browser only divides and picks a colour.

``kpi_registry`` grades the single headline KPI on the agency HQ view against a
month-elapsed share. This is deliberately separate: it grades *many* metrics on
*one* client's dashboard over a user-selected window, and it understands
metrics where lower is better, which that view has no metric for.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

# ── Metric catalogue ─────────────────────────────────────────────────────────
# Keys mirror the paid summary cards in bigquery_dashboard_renderer's
# SUMMARY_CARDS, so a goal lands on the card of the same name and nothing has to
# translate between the two lists.
#
#   direction  : "up" (more is better) | "down" (less is better) | "band"
#                (on-plan is better — over and under are both misses)
#   cumulative : True for totals that accumulate over the month (and are
#                therefore prorated to the selected window); False for rates.
#   format     : how the goal renders next to the value.
GOAL_METRICS: tuple[dict[str, Any], ...] = (
    {
        "key": "spend",
        "label": "Spend",
        "format": "currency",
        "direction": "band",
        "cumulative": True,
        "hint": "Monthly media spend target. Graded as a band — well under plan "
                "is a miss too.",
    },
    {
        "key": "impressions",
        "label": "Impressions",
        "format": "number",
        "direction": "up",
        "cumulative": True,
        "hint": "Monthly impression target.",
    },
    {
        "key": "clicks",
        "label": "Clicks",
        "format": "number",
        "direction": "up",
        "cumulative": True,
        "hint": "Monthly click target.",
    },
    {
        "key": "conversions",
        "label": "Conversions",
        "format": "number",
        "direction": "up",
        "cumulative": True,
        "hint": "Monthly conversion target, as counted by the ad platforms.",
    },
    {
        "key": "ctr",
        "label": "CTR",
        "format": "percent",
        "direction": "up",
        "cumulative": False,
        "hint": "Target click-through rate, in percent (e.g. 3.5).",
    },
    {
        "key": "cpc",
        "label": "CPC",
        "format": "currency2",
        "direction": "down",
        "cumulative": False,
        "hint": "Target cost per click — the ceiling, not a goal to reach.",
    },
    {
        "key": "cpa",
        "label": "CPA",
        "format": "currency",
        "direction": "down",
        "cumulative": False,
        "hint": "Target cost per conversion — the ceiling, not a goal to reach.",
    },
)

_BY_KEY: dict[str, dict[str, Any]] = {m["key"]: m for m in GOAL_METRICS}

# Grading thresholds, as a percentage of the (prorated) target. Held here so the
# dashboard never hard-codes a cutoff and one edit retunes every card.
#
#   up   : pct is % of target reached  — at/near 100 is good.
#   down : pct is % of the ceiling used — at/under 100 is good.
#   band : pct is % of plan            — near 100 either way is good.
THRESHOLDS: dict[str, dict[str, float]] = {
    "up": {"good": 90.0, "warn": 60.0},
    "down": {"good": 100.0, "warn": 125.0},
    "band": {"good_low": 90.0, "good_high": 110.0, "warn_low": 75.0, "warn_high": 125.0},
}

# A goal has to be a positive, finite number to mean anything; 0 or a negative
# target would make every percentage nonsense, so those are dropped on save.
MAX_GOAL = 1e12


def metric_keys() -> tuple[str, ...]:
    """Every metric key a goal may be stored against."""
    return tuple(m["key"] for m in GOAL_METRICS)


def catalog() -> list[dict[str, Any]]:
    """The metric list for the Settings editor and the dashboard payload."""
    return [dict(m) for m in GOAL_METRICS]


def normalize_goals(payload: object) -> dict[str, float]:
    """Coerce a stored/posted goals map into ``{known_key: positive_float}``.

    Unknown keys, blanks, non-numbers and non-positive values are dropped rather
    than raising: a goal is optional decoration on a card, so a single bad entry
    must never block a save or wedge a dashboard render. JSONB comes back as a
    dict from psycopg; a JSON string is tolerated too.
    """
    if payload is None:
        return {}
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if not isinstance(payload, dict):
        return {}

    out: dict[str, float] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key or "").strip().lower()
        if key not in _BY_KEY:
            continue
        if raw_value is None or raw_value == "":
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not (0 < value <= MAX_GOAL):
            continue
        out[key] = value
    # Emit in catalogue order so a stored map reads the same way every time.
    return {k: out[k] for k in metric_keys() if k in out}


def prorate(goal: float, *, window_days: int, days_in_month: int) -> float:
    """A monthly goal scaled to the share of a month the window represents.

    Guards the degenerate inputs rather than dividing by zero: a window of no
    days, or a month of no days, prorates to the full goal, which grades as
    "nowhere near" instead of crashing the card.
    """
    if window_days <= 0 or days_in_month <= 0:
        return float(goal)
    return float(goal) * (float(window_days) / float(days_in_month))


def window_days(start: date, end: date) -> int:
    """Inclusive day count for a dashboard range (always at least 1)."""
    return max(1, (end - start).days + 1)


def days_in_month(day: date) -> int:
    """Calendar length of the month a date falls in."""
    return monthrange(day.year, day.month)[1]


def grade(pct: float | None, direction: str) -> str:
    """``good`` | ``warn`` | ``bad`` | ``neutral`` for a percent-of-target.

    Mirrors the thresholds the dashboard applies client-side; kept here so the
    rule is unit-tested in one place and the two can be checked against each
    other.
    """
    if pct is None:
        return "neutral"
    t = THRESHOLDS.get(direction)
    if not t:
        return "neutral"
    if direction == "up":
        if pct >= t["good"]:
            return "good"
        return "warn" if pct >= t["warn"] else "bad"
    if direction == "down":
        if pct <= t["good"]:
            return "good"
        return "warn" if pct <= t["warn"] else "bad"
    # band
    if t["good_low"] <= pct <= t["good_high"]:
        return "good"
    if t["warn_low"] <= pct <= t["warn_high"]:
        return "warn"
    return "bad"


def resolve_goals(
    goals: dict[str, float] | None,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Render-ready goal payload for one client over one dashboard window.

    Each entry carries the stored monthly ``goal``, the ``target`` that goal
    prorates to for this window (identical to ``goal`` for rate metrics), the
    grading ``direction``, and a plain-language ``note`` the card shows on hover
    so nobody has to reverse-engineer where the number came from.
    """
    cleaned = normalize_goals(goals or {})
    days = window_days(start, end)
    month_days = days_in_month(end)

    resolved: dict[str, Any] = {}
    for key, goal in cleaned.items():
        meta = _BY_KEY[key]
        cumulative = bool(meta["cumulative"])
        target = prorate(goal, window_days=days, days_in_month=month_days) if cumulative else goal
        if cumulative:
            note = (
                f"Monthly target prorated to this range — {days} of "
                f"{month_days} days."
            )
        else:
            note = "Target rate — not prorated; a rate reads the same over any range."
        resolved[key] = {
            "goal": round(float(goal), 4),
            "target": round(float(target), 4),
            "direction": meta["direction"],
            "cumulative": cumulative,
            "label": meta["label"],
            "format": meta["format"],
            "note": note,
        }

    return {
        "goals": resolved,
        "thresholds": THRESHOLDS,
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "days_in_month": month_days,
        },
    }
