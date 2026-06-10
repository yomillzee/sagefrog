"""Date range and budget pacing calendar helpers."""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

WAREHOUSE_DATE_RANGES = frozenset(
    {
        "LAST_7_DAYS",
        "LAST_30_DAYS",
        "LAST_90_DAYS",
        "LAST_180_DAYS",
        "THIS_MONTH",
        "LAST_MONTH",
    }
)


def parse_monthly_budget_input(raw: str | None) -> float | None:
    text = (raw or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    val = float(text)
    if val < 0:
        raise ValueError("Monthly budget must be zero or greater.")
    return val


def mtd_calendar_bounds(today: date | None = None) -> tuple[date, date, date]:
    """Return (month_start, month_end, today) for budget pacing."""
    current = today or date.today()
    month_start = current.replace(day=1)
    last_day = calendar.monthrange(current.year, current.month)[1]
    month_end = current.replace(day=last_day)
    return month_start, month_end, current


def paid_daily_spend_map(
    daily_metrics: dict[str, Any],
    *,
    month_start: date,
    through: date,
) -> dict[str, float]:
    start_key = month_start.isoformat()
    end_key = through.isoformat()
    out: dict[str, float] = {}
    for platform in ("google", "linkedin", "meta"):
        for row in daily_metrics.get(platform) or []:
            key = str(row.get("metric_date") or "")[:10]
            if not key or key < start_key or key > end_key:
                continue
            out[key] = out.get(key, 0.0) + float(row.get("spend") or 0)
    return out
