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


def resolve_sync_date_range(
    date_range: str | None = None,
    *,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    default_preset: str = "LAST_7_DAYS",
) -> tuple[date, date, str]:
    """Resolve sync window from explicit dates or a preset (default LAST_7_DAYS)."""
    if start_date is not None and end_date is not None:
        start = date.fromisoformat(str(start_date).strip()[:10]) if not isinstance(start_date, date) else start_date
        end = date.fromisoformat(str(end_date).strip()[:10]) if not isinstance(end_date, date) else end_date
        if end < start:
            raise ValueError("end_date must be on or after start_date")
        return start, end, "CUSTOM"
    preset = str(date_range or default_preset).strip().upper().replace("-", "_")
    return resolve_date_range(preset)
