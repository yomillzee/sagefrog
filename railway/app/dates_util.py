"""Shared calendar presets for API date ranges."""

from __future__ import annotations

from datetime import date, timedelta


def resolve_date_range(preset: str) -> tuple[date, date, str]:
    today = date.today()
    key = str(preset or "LAST_30_DAYS").strip().upper().replace("-", "_")

    if key == "LAST_7_DAYS":
        return today - timedelta(days=6), today, key
    if key == "LAST_30_DAYS":
        return today - timedelta(days=29), today, key
    if key == "LAST_90_DAYS":
        return today - timedelta(days=89), today, key
    if key == "LAST_180_DAYS":
        return today - timedelta(days=179), today, key
    if key == "THIS_MONTH":
        return today.replace(day=1), today, key
    if key == "LAST_MONTH":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end, key

    return today - timedelta(days=29), today, "LAST_30_DAYS"
