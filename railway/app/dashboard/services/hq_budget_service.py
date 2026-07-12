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
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import client_dashboard_config
import db_cache
import marketing_service
from dashboard.utils.dates import mtd_calendar_bounds

LOGGER = logging.getLogger(__name__)

# Match /api/clients/{key}/summary so the HQ read shares its cached result.
_SUMMARY_TTL_SECONDS = 900
# Trailing window for the sessions sparkline — long enough to read momentum,
# short enough to stay a cheap single scan. Ends yesterday (GA4 export lags a
# day, so "today" is usually partial and would show a misleading dip).
_SESSIONS_TRAILING_DAYS = 30


def _hq_max_workers() -> int:
    """Concurrency cap for the per-client HQ reads.

    Each client contributes up to two blocking BigQuery reads (spend + sessions).
    Running clients in a plain loop makes the page latency the *sum* of every
    client's cold-cache reads; fanning them out over a small thread pool makes it
    roughly the slowest single client instead. Capped so a large roster can't open
    an unbounded number of concurrent BigQuery/Postgres connections. Override with
    HQ_MAX_WORKERS if a project needs to tune it.
    """
    raw = (os.getenv("HQ_MAX_WORKERS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            LOGGER.warning("HQ_MAX_WORKERS=%r is not an int; using default", raw)
    return 8


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


def _client_sessions_series(
    *,
    slug: str,
    project_id: str,
    dataset_id: str,
    start: date,
    end: date,
) -> list[int]:
    """Trailing daily GA4 sessions for one client (for the sparkline)."""
    payload = {"start": start.isoformat(), "end": end.isoformat()}

    def _fetch() -> dict[str, Any]:
        with marketing_service.route(
            client_key=slug, project_id=project_id, mart_dataset_id=dataset_id
        ):
            return marketing_service.fetch_sessions_daily(start_date=start, end_date=end)

    result = db_cache_get_or_fetch(f"{slug}.hq.sessions_daily", payload, _fetch)
    daily = (result or {}).get("daily") or []
    return [int(r.get("sessions") or 0) for r in daily]


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


def _build_client_row(
    c: dict[str, Any],
    *,
    month_start: date,
    today: date,
    sessions_start: date,
    sessions_end: date,
    days_elapsed: int,
    days_in_month: int,
    pct_month: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute one client's HQ row plus its contribution to the agency totals.

    Runs on a pool thread. Each per-client read is wrapped so one failing client
    degrades to spend_available/sessions_available False rather than sinking the
    whole page. Returns (row, contrib); contrib carries the *unrounded* values the
    totals accumulate so agency sums match the old sequential arithmetic exactly.
    """
    slug = c["client_slug"]
    budget = float(c.get("monthly_budget_usd") or 0.0)
    project_id = c.get("gcp_project_id")
    dataset_id = c.get("bq_mart_dataset_id") or "marketing_marts"

    spend: float | None = None
    spend_available = False
    sessions_series: list[int] = []
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
        try:
            sessions_series = _client_sessions_series(
                slug=slug, project_id=project_id, dataset_id=dataset_id,
                start=sessions_start, end=sessions_end,
            )
        except Exception:
            LOGGER.exception("HQ budget: sessions failed for %s", slug)
            sessions_series = []

    mtd = spend or 0.0
    pct_budget = round(100 * mtd / budget, 1) if budget > 0 else None
    expected_pace = round(budget * days_elapsed / days_in_month, 2) if budget > 0 else None
    projected = round(mtd / days_elapsed * days_in_month, 2) if days_elapsed > 0 and spend_available else None
    remaining = round(budget - mtd, 2) if budget > 0 else None
    pace_delta = round(projected - budget, 2) if (projected is not None and budget > 0) else None
    status = _pace_status(budget=budget, projected=projected if spend_available else None, pct_month=pct_month)

    row = {
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
        "sessions_series": sessions_series,
        "sessions_total": sum(sessions_series),
        "sessions_available": bool(sessions_series),
    }
    contrib = {
        "budget": budget,
        "spend_available": spend_available,
        "mtd": mtd,
        "projected": projected,
        "over": status == "over",
    }
    return row, contrib


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
    # Sessions sparkline window: trailing N days ending yesterday.
    sessions_end = today - timedelta(days=1)
    sessions_start = sessions_end - timedelta(days=_SESSIONS_TRAILING_DAYS - 1)

    clients = client_dashboard_config.list_budget_overview()

    # Each client is an independent set of cached BigQuery reads, so compute the
    # rows concurrently. Row order is preserved (executor.map yields in input
    # order) and per-client failures stay isolated inside _build_client_row, so
    # the parallel version behaves identically to the old sequential loop — just
    # bounded by the slowest client instead of their sum.
    def _work(c: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return _build_client_row(
            c,
            month_start=month_start,
            today=today,
            sessions_start=sessions_start,
            sessions_end=sessions_end,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            pct_month=pct_month,
        )

    if clients:
        with ThreadPoolExecutor(max_workers=min(len(clients), _hq_max_workers())) as ex:
            results = list(ex.map(_work, clients))
    else:
        results = []

    rows: list[dict[str, Any]] = [row for row, _ in results]
    tot_budget = 0.0
    tot_spend = 0.0
    tot_projected = 0.0
    n_over = 0
    for _, contrib in results:
        tot_budget += contrib["budget"]
        if contrib["spend_available"]:
            tot_spend += contrib["mtd"]
            if contrib["projected"] is not None:
                tot_projected += contrib["projected"]
            if contrib["over"]:
                n_over += 1

    return {
        "month_label": month_start.strftime("%B %Y"),
        "as_of": today.isoformat(),
        "sessions_window": {
            "start": sessions_start.isoformat(),
            "end": sessions_end.isoformat(),
            "days": _SESSIONS_TRAILING_DAYS,
        },
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
