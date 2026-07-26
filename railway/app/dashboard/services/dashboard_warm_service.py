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
default ``Last 30 days`` preset (see ``applyPreset('last_30')`` in
``bigquery_dashboard_renderer``) — the current period **and** its prior-period
comparison, since the summary / website / AI-traffic cards each fetch both. A
warmed entry keyed to a different window than the endpoint later reads would
silently not help, which is exactly the drift the cache-warm test guards against.

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

import logging
from datetime import date, timedelta
from typing import Any

LOGGER = logging.getLogger(__name__)


def _overview_windows(today: date | None = None) -> tuple[tuple[date, date], tuple[date, date]]:
    """The current + prior-period windows the Overview's ``Last 30 days`` preset uses.

    Mirrors ``lastN(30)`` in the page JS exactly: the current window ends
    *yesterday* (today's data is usually unsynced) and spans 30 days; the
    comparison window is the equal-length period immediately before it. Keeping
    this in lockstep with the frontend is what makes the warmed cache entry land
    on the same key the first viewer looks up.
    """
    today = today or date.today()
    cur_end = today - timedelta(days=1)
    cur_start = today - timedelta(days=30)
    cmp_end = cur_start - timedelta(days=1)
    cmp_start = cmp_end - timedelta(days=29)
    return (cur_start, cur_end), (cmp_start, cmp_end)


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

    (cur_start, cur_end), (cmp_start, cmp_end) = _overview_windows()

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
            slug, cur_start, cur_end, **_route())),
        ("traffic_acquisition_prev", lambda: _api._traffic_acquisition_read(
            slug, cmp_start, cmp_end, **_route())),
        ("ai_traffic", lambda: _api._ai_traffic_daily_read(
            slug, cur_start, cur_end, **_route())),
        ("ai_traffic_prev", lambda: _api._ai_traffic_daily_read(
            slug, cmp_start, cmp_end, **_route())),
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
