"""Post-sync cache warming for the dashboard Overview page.

When a client's BigQuery sync finishes it calls ``db_cache.invalidate_prefix``
to drop that client's cached card reads immediately (new data just landed in
BQ). Without warming, the *first* dashboard viewer after every sync then pays a
cold BigQuery query for each card, one per client-side fetch — that cold load is
the multi-second "cards are slow" experience users actually notice.

This module recomputes the **Overview** page's cards right after the sync — the
page virtually every client and internal review opens first — so that first
load is served warm from ``api_cache`` instead. Deeper tabs (Explorer,
Analytics, GSC, …) are intentionally left cold to keep the daily BigQuery cost
proportional to the one page people actually land on; the read-cache TTL floor
(``app_settings.dashboard_cache_ttl_seconds``) then keeps whatever anyone opens
warm for the rest of the day.

The warmed windows mirror exactly what the Overview page requests on load: the
preset the page lands on — the client's admin-pinned ``default_date_preset``, or
``Last 30 days`` when it has none (see
``applyPreset(DEFAULT_DATE_PRESET || 'last_30')`` in
``bigquery_dashboard_renderer``) — for the current period **and** its
prior-period comparison, since the summary / website / AI-traffic cards each
fetch both. A warmed entry keyed to a different window than the endpoint later
reads would silently not help, which is exactly the drift the cache-warm test
guards against — and is what a client with a pinned default other than Last 30
days used to hit on every single first load, since the warmer assumed that one
window for everybody.

Fail-safe by construction:

* It warms by calling the **same** read helpers the endpoints use
  (``api_routes._summary_read`` / ``_health_read`` / ``_traffic_acquisition_read``
  / ``_ai_traffic_daily_read``), so a warmed entry is always keyed identically to
  what the endpoint later reads — the two paths cannot drift apart.
* Every step is wrapped; any error is swallowed and logged. The worst case is
  simply "not warmed" (i.e. today's behavior). Warming can never change what a
  card returns, and never fails the sync it runs after.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from typing import Any

LOGGER = logging.getLogger(__name__)

# Fallback when a client has no stored default (mirrors the renderer's own
# fallback in ``applyPreset(DEFAULT_DATE_PRESET || 'last_30')``).
DEFAULT_PRESET = "last_30"

_TRAILING_DAYS = {"last_7": 7, "last_30": 30, "last_90": 90, "last_365": 365}


def _monday_of(d: date) -> date:
    """Monday of ``d``'s ISO week — the page's ``mondayOf``."""
    return d - timedelta(days=d.weekday())


def _quarter_start(d: date) -> date:
    """Jan/Apr/Jul/Oct 1 of ``d``'s quarter — the page's ``quarterStart``."""
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def _shift_month(d: date, months: int) -> date:
    """First day of the month ``months`` away from ``d``'s month."""
    total = (d.year * 12 + d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _month_end(d: date) -> date:
    """Last day of ``d``'s month."""
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _overview_windows(
    today: date | None = None, preset: str = DEFAULT_PRESET,
) -> tuple[tuple[date, date], tuple[date, date]]:
    """The current + prior-period windows the Overview lands on for ``preset``.

    Mirrors ``applyPreset`` in the page JS branch for branch. The trailing
    presets end *yesterday* (today's data is usually unsynced) and the ``this_*``
    presets clamp to their period start when yesterday falls before it — both
    exactly as the page does, because a window off by a single day is a different
    cache key and warms nothing.

    An unknown preset falls back to ``last_30``, matching the renderer, which
    validates the stored value against the same list before using it.
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    days = _TRAILING_DAYS.get(preset)
    if days is None and preset not in (
        "this_week", "last_week", "this_month",
        "last_month", "this_quarter", "last_quarter", "this_year",
    ):
        days = _TRAILING_DAYS[DEFAULT_PRESET]

    if days is not None:
        # lastN(n): ends yesterday, spans n days; comparison is the equal-length
        # period immediately before it.
        cur_end = yesterday
        cur_start = today - timedelta(days=days)
        cmp_end = cur_start - timedelta(days=1)
        cmp_start = cmp_end - timedelta(days=days - 1)
        return (cur_start, cur_end), (cmp_start, cmp_end)

    if preset == "this_week":
        cur_start = _monday_of(today)
        cur_end = max(yesterday, cur_start)
        return (
            (cur_start, cur_end),
            (cur_start - timedelta(days=7), cur_end - timedelta(days=7)),
        )

    if preset == "last_week":
        cur_start = _monday_of(today - timedelta(days=7))
        cur_end = cur_start + timedelta(days=6)
        return (
            (cur_start, cur_end),
            (cur_start - timedelta(days=7), cur_end - timedelta(days=7)),
        )

    if preset == "this_month":
        cur_start = today.replace(day=1)
        cur_end = max(yesterday, cur_start)
        cmp_start = _shift_month(cur_start, -1)
        # Same day-of-month a month back, clamped: a 31st has no counterpart in
        # a 30-day month.
        cmp_end = cmp_start.replace(
            day=min(cur_end.day, calendar.monthrange(cmp_start.year, cmp_start.month)[1])
        )
        return (cur_start, cur_end), (cmp_start, cmp_end)

    if preset == "last_month":
        cur_start = _shift_month(today.replace(day=1), -1)
        cur_end = _month_end(cur_start)
        cmp_start = _shift_month(cur_start, -1)
        return (cur_start, cur_end), (cmp_start, _month_end(cmp_start))

    if preset == "this_quarter":
        cur_start = _quarter_start(today)
        cur_end = max(yesterday, cur_start)
        cmp_start = _shift_month(cur_start, -3)
        prev_q_end = cur_start - timedelta(days=1)
        cmp_end = min(cmp_start + (cur_end - cur_start), prev_q_end)
        return (cur_start, cur_end), (cmp_start, cmp_end)

    if preset == "last_quarter":
        qs = _quarter_start(today)
        cur_start = _shift_month(qs, -3)
        cur_end = qs - timedelta(days=1)
        cmp_start = _shift_month(qs, -6)
        return (cur_start, cur_end), (cmp_start, cur_start - timedelta(days=1))

    # this_year
    cur_start = date(today.year, 1, 1)
    cur_end = max(yesterday, cur_start)
    cmp_start = date(today.year - 1, 1, 1)
    prev_y_end = date(today.year - 1, 12, 31)
    cmp_end = min(cmp_start + (cur_end - cur_start), prev_y_end)
    return (cur_start, cur_end), (cmp_start, cmp_end)


def _client_default_preset(client_slug: str) -> str:
    """The preset this client's dashboard lands on, or ``last_30``.

    An admin can pin any preset as a client's default (Range → Make default),
    and the page then opens on *that* window. Warming the ``last_30`` window for
    such a client populates a key nothing ever reads, so the first viewer after
    every sync pays a cold BigQuery query for every card — the exact cost this
    module exists to remove.

    Read against the slug the caches are keyed under, which for Nixon is the
    literal ``"nixon"`` rather than the ``nixon-bq-test`` slug its dashboard is
    served at (see the caller in ``bigquery_refresh_orchestrator``). If no config
    row answers to that slug there is nothing pinned to find and we fall back —
    i.e. the previous behaviour, never something worse.
    """
    try:
        import client_dashboard_config

        stored = getattr(
            client_dashboard_config.get_config(client_slug), "default_date_preset", None
        )
        if stored in client_dashboard_config.DATE_RANGE_PRESETS:
            return stored
    except Exception:
        LOGGER.warning(
            "cache warm: default-preset read failed for %s", client_slug, exc_info=True
        )
    return DEFAULT_PRESET


def warm_client_cache(client_slug: str) -> dict[str, Any]:
    """Recompute the Overview card caches for ``client_slug``. Best-effort.

    Returns a small report ({"warmed": [...], "skipped": [...], "errors": {...}})
    for observability; callers can attach it to the refresh result but should not
    depend on it.
    """
    slug = (client_slug or "").strip().lower()
    report: dict[str, Any] = {"warmed": [], "skipped": [], "errors": {}}
    if not slug:
        return report

    # Local import: this service runs at the tail of a refresh, and api_routes
    # imports services heavily, so importing it here (call time) rather than at
    # module load avoids any import cycle.
    try:
        from dashboard.routes import api_routes as _api
    except Exception:
        LOGGER.warning("cache warm: api_routes import failed for %s", slug, exc_info=True)
        report["errors"]["import"] = "api_routes import failed"
        return report

    preset = _client_default_preset(slug)
    (cur_start, cur_end), (cmp_start, cmp_end) = _overview_windows(preset=preset)
    report["preset"] = preset

    project_id: str | None = None
    dataset_id: str | None = None
    if slug != "nixon":
        # Generic BigQuery clients need their project/dataset resolved to route
        # the mart reads. No project configured → nothing to warm (the endpoints
        # would 503 too); skip quietly.
        try:
            project_id, dataset_id = _api._load_bq_test_config(slug)
        except Exception as exc:
            report["skipped"].append("all")
            report["errors"]["config"] = str(exc)[:200]
            return report

    def _route(**kw: Any) -> dict[str, Any]:
        return dict(project_id=project_id, dataset_id=dataset_id, **kw)

    # The client's Website Analytics page-path scope is part of the cache key for
    # the page-scoped reads, so warm with it — warming unscoped would populate a
    # key the endpoints never read, leaving scoped clients cold.
    page_path_filter = _api._load_page_path_filter(slug)

    def _route_scoped(**kw: Any) -> dict[str, Any]:
        return _route(page_path_filter=page_path_filter, **kw)

    # Overview cards, each warmed for the exact window(s) the page requests. The
    # summary / website / AI-traffic cards render a current-vs-prior comparison,
    # so both windows are warmed; health takes no date range. The GSC keyword and
    # PageSpeed cards are intentionally omitted: PageSpeed is a snapshot read on a
    # 6h TTL (cheap and near-always warm), and GSC keyword-matches depends on each
    # client's configured terms — both are left to the TTL floor rather than
    # adding per-client warm logic and BigQuery cost here.
    tasks: list[tuple[str, Any]] = [
        ("summary", lambda: _api._summary_read(slug, cur_start, cur_end, **_route())),
        ("summary_prev", lambda: _api._summary_read(slug, cmp_start, cmp_end, **_route())),
        ("health", lambda: _api._health_read(slug, 100, **_route())),
        ("traffic_acquisition", lambda: _api._traffic_acquisition_read(
            slug, cur_start, cur_end, **_route_scoped())),
        ("traffic_acquisition_prev", lambda: _api._traffic_acquisition_read(
            slug, cmp_start, cmp_end, **_route_scoped())),
        ("ai_traffic", lambda: _api._ai_traffic_daily_read(
            slug, cur_start, cur_end, **_route_scoped())),
        ("ai_traffic_prev", lambda: _api._ai_traffic_daily_read(
            slug, cmp_start, cmp_end, **_route_scoped())),
    ]

    for name, fn in tasks:
        try:
            fn()
            report["warmed"].append(name)
        except Exception as exc:
            report["errors"][name] = str(exc)[:200]
            LOGGER.warning("cache warm failed [%s/%s]", slug, name, exc_info=True)

    LOGGER.info("cache warm complete [%s]: %s", slug, report)
    return report
