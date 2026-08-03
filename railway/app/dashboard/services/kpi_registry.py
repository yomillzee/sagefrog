"""Registry of per-client "primary KPI" types for the HQ (Agency Trends) view.

Every client's headline KPI is a *different* metric — one tracks MQLs from
HubSpot, another Google Ads conversions, another Google Ads ROAS — so instead of
a fixed column we key each client's stored spec (``{type, label, goal}``) to a
resolver here. Adding a new KPI is a one-entry change: register its type (label,
format, data source, whether it accumulates over the month) and teach
``_value_for`` how to read it from the inputs the service already gathers.

The resolver is deliberately pure: the service (agency_trends_service) does the
BigQuery reads and hands this module plain aggregates, so KPI math is trivially
unit-testable and never touches I/O.
"""

from __future__ import annotations

from typing import Any

# Channel key as it appears in vw_paid_media_daily.source_platform.
_GOOGLE = "paid_google"

# type id -> descriptor.
#   label   : default column label (a client may override per spec).
#   format  : how the value renders — "number" | "multiplier" | "currency".
#   source  : which input the value comes from — "paid" | "hubspot".
#   paced   : True for metrics that accumulate over the month (conversions,
#             MQLs) — those are graded against how much of the month has
#             elapsed. False for rate metrics (ROAS) graded straight vs goal.
KPI_TYPES: dict[str, dict[str, Any]] = {
    "google_ads_conversions": {
        "label": "Conv.",
        "format": "number",
        "source": "paid",
        "paced": True,
    },
    "google_ads_roas": {
        "label": "Google Ads ROAS",
        "format": "multiplier",
        "source": "paid",
        "paced": False,
    },
    "hubspot_mqls": {
        "label": "MQLs",
        "format": "number",
        "source": "hubspot",
        "paced": True,
    },
}

# Ordered choices for the Settings picker (value, label). "" = no KPI.
KPI_CHOICES: list[tuple[str, str]] = [("", "No KPI")] + [
    (k, v["label"]) for k, v in KPI_TYPES.items()
]


def spec_type(spec: dict[str, Any] | None) -> str | None:
    """The registered type id of a spec, or None if unset/unknown."""
    if not spec:
        return None
    t = str(spec.get("type") or "").strip()
    return t if t in KPI_TYPES else None


def needs_hubspot(spec: dict[str, Any] | None) -> bool:
    """Whether resolving this spec requires the (extra) HubSpot MQL read."""
    t = spec_type(spec)
    return bool(t and KPI_TYPES[t]["source"] == "hubspot")


def _value_for(kpi_type: str, *, paid_mtd: dict[str, dict[str, float]], mql_count: float | None) -> float | None:
    """Compute the raw KPI value from the gathered inputs (None = no data)."""
    if kpi_type == "google_ads_conversions":
        return float(paid_mtd.get(_GOOGLE, {}).get("conversions") or 0.0)
    if kpi_type == "google_ads_roas":
        g = paid_mtd.get(_GOOGLE, {})
        spend = float(g.get("spend") or 0.0)
        conv_value = float(g.get("conversion_value") or 0.0)
        return round(conv_value / spend, 2) if spend > 0 else None
    if kpi_type == "hubspot_mqls":
        return float(mql_count) if mql_count is not None else None
    return None


def _fmt(value: float | None, fmt: str) -> str:
    if value is None:
        return "—"
    if fmt == "multiplier":
        return f"{value:.1f}×"
    if fmt == "currency":
        return f"${value:,.0f}"
    # number: no decimals when whole, else one.
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def _grade(
    *, value: float | None, goal: float | None, paced: bool, pct_month_elapsed: float
) -> tuple[str, float | None]:
    """(status, pct_to_goal). status ∈ good|warn|bad|neutral.

    Paced metrics are judged against the share of the month elapsed (being at
    50% of goal on day 15 of a 30-day month is on pace); rate metrics (ROAS) are
    judged straight against goal.
    """
    if value is None or goal is None or goal <= 0:
        pct = round(100.0 * value / goal, 1) if (value is not None and goal and goal > 0) else None
        return "neutral", pct
    pct = round(100.0 * value / goal, 1)
    if pct >= 100:
        return "good", pct
    if paced:
        expected = max(pct_month_elapsed, 1.0)  # avoid div-by-zero on day 0
        ratio = pct / expected
        status = "good" if ratio >= 0.9 else ("warn" if ratio >= 0.6 else "bad")
    else:
        status = "warn" if pct >= 80 else "bad"
    return status, pct


def resolve_kpi(
    spec: dict[str, Any] | None,
    *,
    paid_mtd: dict[str, dict[str, float]] | None = None,
    mql_count: float | None = None,
    pct_month_elapsed: float = 0.0,
    window_label: str = "month to date",
) -> dict[str, Any] | None:
    """Turn a stored KPI spec + gathered inputs into a render-ready payload.

    Returns None when the spec is unset or names an unknown type. Otherwise a
    dict with: type, label, format, value, goal, pct_to_goal, status, tooltip.
    A ``value`` of None means "no data yet" (renders as an em dash).

    ``pct_month_elapsed`` is the expected share of the goal for the window the
    value covers — for a month-to-date value that is the share of the month
    elapsed, but the Agency Trends filter can scope the value to a complete week
    or the last 30 days and pass that window's expected share instead.
    ``window_label`` names that window for the tooltip.
    """
    kpi_type = spec_type(spec)
    if kpi_type is None:
        return None
    desc = KPI_TYPES[kpi_type]
    paid_mtd = paid_mtd or {}

    value = _value_for(kpi_type, paid_mtd=paid_mtd, mql_count=mql_count)
    goal_raw = (spec or {}).get("goal")
    try:
        goal = float(goal_raw) if goal_raw is not None and goal_raw != "" else None
    except (TypeError, ValueError):
        goal = None
    if goal is not None and goal <= 0:
        goal = None

    label = str((spec or {}).get("label") or "").strip() or desc["label"]
    fmt = desc["format"]
    status, pct_to_goal = _grade(
        value=value, goal=goal, paced=desc["paced"], pct_month_elapsed=pct_month_elapsed
    )

    val_txt = _fmt(value, fmt)
    if goal is not None:
        pct_txt = f" · {round(pct_to_goal)}% of goal" if pct_to_goal is not None else ""
        tooltip = f"{label}: {val_txt} vs {_fmt(goal, fmt)} goal{pct_txt}"
    elif value is None:
        tooltip = f"{label}: no data yet"
    else:
        tooltip = f"{label}: {val_txt} ({window_label})"

    return {
        "type": kpi_type,
        "label": label,
        "format": fmt,
        "value": value,
        "goal": goal,
        "pct_to_goal": pct_to_goal,
        "status": status,
        "tooltip": tooltip,
    }
