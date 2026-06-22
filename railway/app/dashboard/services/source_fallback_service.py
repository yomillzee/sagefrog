"""Decide when an ad source needs API fallback and merge repaired data."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


_BQ_ERROR_KEYS: dict[str, tuple[str, ...]] = {
    "google": ("bq_mart_google",),
    "linkedin": ("bq_mart_linkedin", "linkedin_bigquery"),
    "meta": ("meta_bigquery",),
}


def source_has_snapshot_data(snapshot: dict[str, Any], source: str) -> bool:
    if (snapshot.get("daily_metrics") or {}).get(source):
        return True
    source_breakdowns = (snapshot.get("breakdowns") or {}).get(source) or {}
    return any(bool(rows) for rows in source_breakdowns.values())


def source_snapshot_is_stale(
    snapshot: dict[str, Any], source: str, *, max_lag_days: int = 2
) -> bool:
    rows = (snapshot.get("daily_metrics") or {}).get(source) or []
    dated_rows = [
        str(row.get("metric_date") or row.get("date") or "")[:10]
        for row in rows
        if row.get("metric_date") or row.get("date")
    ]
    if not dated_rows:
        return False
    try:
        latest = max(date.fromisoformat(value) for value in dated_rows)
        expected_end = date.fromisoformat(
            str((snapshot.get("date_range") or {}).get("end") or "")[:10]
        )
    except (TypeError, ValueError):
        return False
    return latest < expected_end - timedelta(days=max(0, int(max_lag_days)))


def configured_fallback_sources(snapshot: dict[str, Any], cfg: Any) -> set[str]:
    """Return configured ad sources with no usable BigQuery dashboard data."""
    configured = {
        "google": cfg.google_customer_id,
        "linkedin": cfg.linkedin_account_id,
        "meta": cfg.meta_account_id,
    }
    errors = snapshot.get("errors") or {}
    fallback: set[str] = set()
    for source, account_id in configured.items():
        if not account_id:
            continue
        has_bq_error = any(
            key == prefix or key.startswith(f"{prefix}_")
            for key in errors
            for prefix in _BQ_ERROR_KEYS[source]
        )
        if (
            has_bq_error
            or not source_has_snapshot_data(snapshot, source)
            or source_snapshot_is_stale(snapshot, source)
        ):
            fallback.add(source)
    return fallback


def merge_api_fallback_sources(
    snapshot: dict[str, Any],
    api_snapshot: dict[str, Any],
    sources: set[str],
) -> set[str]:
    """Overlay successful API results and demote repaired BQ errors to warnings."""
    repaired: set[str] = set()
    for source in sources:
        if not source_has_snapshot_data(api_snapshot, source):
            continue
        for section in ("daily_metrics", "platform_totals", "breakdowns"):
            value = (api_snapshot.get(section) or {}).get(source)
            if value is not None:
                snapshot.setdefault(section, {})[source] = value
        source_sync = (api_snapshot.get("warehouse_sync") or {}).get(source)
        if source_sync is not None:
            snapshot.setdefault("warehouse_sync", {})[source] = source_sync
        snapshot.setdefault("data_sources", {})[source] = "api_fallback"
        repaired.add(source)

    errors = snapshot.setdefault("errors", {})
    warnings = snapshot.setdefault("warnings", {})
    for source in repaired:
        for key in list(errors):
            if any(
                key == prefix or key.startswith(f"{prefix}_")
                for prefix in _BQ_ERROR_KEYS[source]
            ):
                warnings[key] = errors.pop(key)

    api_errors = api_snapshot.get("errors") or {}
    for source in sources - repaired:
        for key, message in api_errors.items():
            if key.startswith(source):
                errors[key] = message
    return repaired

