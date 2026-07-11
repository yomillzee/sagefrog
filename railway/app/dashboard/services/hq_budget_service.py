"""HQ budget pacing: every client's month-to-date paid spend vs monthly budget.

Powers the admin "/admin/hq" overview. For each client configured in
client_dashboard_config, computes month-to-date paid spend from its BigQuery
marketing mart and derives simple pacing (percent of budget used, projected
month-end at the current run-rate, over/under). One BigQuery read per client,
cached through db_cache with the same key the per-client /summary endpoint uses
so the two share a warm cache.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date
from typing import Any

import client_dashboard_config
import db_cache
import marketing_service
from dashboard.utils.dates import mtd_calendar_bounds

LOGGER = logging.getLogger(__name__)

# Match /api/clients/{key}/summary so the HQ read shares its cached result.
_SUMMARY_TTL_SECONDS = 900


def _client_mtd_spend(
    *,
    slug: str,
    project_id: str,
    dataset_id: str,
    month_start: date,
    today: date,
) -> float:
    """Month-to-date paid spend for one client, cached like /summary."""
    payload = {"start": month_start.isoformat(), "end": today.isoformat()}

    def _fetch() -> dict[str, Any]:
        with marketing_service.route(
            client_key=slug, project_id=project_id, mart_dataset_id=dataset_id
        ):
            return marketing_service.fetch_summary(start_date=month_start, end_date=today)

    result = db_cache_get_or_fetch(f"{slug}.summary", payload, _fetch)
    summary = (result or {}).get("summary") or {}
    return float(summary.get("spend") or 0.0)


def db_cache_get_or_fetch(source: str, payload: dict, fetch) -> dict:
    """Small shim mirroring api_routes._cached_bq_read (kept here to avoid a
    route <-> service import cycle). Same db_cache keys, same TTL semantics."""
    hit = db_cache.get_cached(source, payload)
    if hit is not None:
        return hit.response_json
    result = fetch()
    try:
        db_cache.put_cached(
            source, payload, response_json=result, row_count=0,
            ttl_seconds=_SUMMARY_TTL_SECONDS,
        )
    except Exception:
        pass
    return result


def _pace_status(*, budget: float, projected: float | None, pct_month: float) -> str:
    """Coarse label the UI colors: over / under / on_track / no_budget."""
    if budget <= 0:
        return "no_budget"
    if projected is None:
        return "on_track"
    # >5% either side of the goal at projected month-end reads as off-pace.
    if projected > budget * 1.05:
        return "over"
    if projected < budget * 0.95:
        return "under"
    return "on_track"


def build_hq_budget_overview() -> dict[str, Any]:
    """Compute the full HQ budget table plus agency-wide totals.

    Never raises for a single bad client — a client whose mart read fails (or
    that has no BigQuery project configured yet) comes back with spend_available
    False so the UI can show it as pending rather than dropping it.
    """
    month_start, month_end, today = mtd_calendar_bounds()
    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
    days_elapsed = today.day
    days_remaining = max(0, days_in_month - days_elapsed)
    pct_month = round(100 * days_elapsed / days_in_month, 1) if days_in_month else 0.0

    clients = client_dashboard_config.list_budget_overview()

    rows: list[dict[str, Any]] = []
    tot_budget = 0.0
    tot_spend = 0.0
    tot_projected = 0.0
    n_over = 0
    for c in clients:
        slug = c["client_slug"]
        budget = float(c.get("monthly_budget_usd") or 0.0)
        project_id = c.get("gcp_project_id")
        dataset_id = c.get("bq_mart_dataset_id") or "marketing_marts"

        spend: float | None = None
        spend_available = False
        if project_id:
            try:
                spend = _client_mtd_spend(
                    slug=slug, project_id=project_id, dataset_id=dataset_id,
                    month_start=month_start, today=today,
                )
                spend_available = True
            except Exception:
                LOGGER.exception("HQ budget: MTD spend failed for %s", slug)
                spend = None
                spend_available = False

        mtd = spend or 0.0
        pct_budget = round(100 * mtd / budget, 1) if budget > 0 else None
        expected_pace = round(budget * days_elapsed / days_in_month, 2) if budget > 0 else None
        projected = round(mtd / days_elapsed * days_in_month, 2) if days_elapsed > 0 and spend_available else None
        remaining = round(budget - mtd, 2) if budget > 0 else None
        pace_delta = round(projected - budget, 2) if (projected is not None and budget > 0) else None
        status = _pace_status(budget=budget, projected=projected if spend_available else None, pct_month=pct_month)

        if spend_available:
            tot_spend += mtd
            if projected is not None:
                tot_projected += projected
            if status == "over":
                n_over += 1
        tot_budget += budget

        rows.append({
            "client_slug": slug,
            "label": c["label"],
            "monthly_budget": round(budget, 2) if budget else 0.0,
            "has_budget": budget > 0,
            "mtd_spend": round(mtd, 2),
            "spend_available": spend_available,
            "pct_budget": pct_budget,
            "expected_pace": expected_pace,
            "projected_month_end": projected,
            "remaining_budget": remaining,
            "pace_delta": pace_delta,
            "status": status,
        })

    return {
        "month_label": month_start.strftime("%B %Y"),
        "as_of": today.isoformat(),
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "pct_month_elapsed": pct_month,
        "totals": {
            "monthly_budget": round(tot_budget, 2),
            "mtd_spend": round(tot_spend, 2),
            "projected_month_end": round(tot_projected, 2),
            "clients_over_pace": n_over,
            "client_count": len(rows),
        },
        "clients": rows,
    }
