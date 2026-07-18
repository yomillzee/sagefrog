"""Post-sync cache warming for BigQuery dashboard card reads.

When a client's BigQuery sync finishes it calls ``db_cache.invalidate_prefix``
to drop that client's cached card reads immediately (new data just landed in
BQ). Without warming, the *first* dashboard viewer after every sync then pays a
cold BigQuery query for each card, one per client-side fetch — that cold load is
the multi-second "cards are slow" experience users actually notice.

This module recomputes the core paid-media cards right after the sync, so the
first load is served warm from ``api_cache`` instead.

Fail-safe by construction:

* It warms by calling the **same** read helpers the endpoints use
  (``api_routes._summary_read`` / ``_health_read`` / ``_marketing_read``), so a
  warmed entry is always keyed identically to what the endpoint later reads —
  the two paths cannot drift apart.
* Every step is wrapped; any error is swallowed and logged. The worst case is
  simply "not warmed" (i.e. today's behavior). Warming can never change what a
  card returns, and never fails the sync it runs after.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

LOGGER = logging.getLogger(__name__)


def _default_window() -> tuple[date, date]:
    """Last-30-days window — matches the endpoints' default (``_resolve_marketing_dates``).

    The default dashboard load requests no explicit dates, so this is the window
    whose cache key the first viewer hits.
    """
    end = date.today()
    return end - timedelta(days=29), end


def warm_client_cache(client_slug: str) -> dict[str, Any]:
    """Recompute the core card caches for ``client_slug``. Best-effort.

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

    start, end = _default_window()

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

    tasks: list[tuple[str, Any]] = [
        ("summary", lambda: _api._summary_read(
            slug, start, end, project_id=project_id, dataset_id=dataset_id,
        )),
        ("health", lambda: _api._health_read(
            slug, 100, project_id=project_id, dataset_id=dataset_id,
        )),
    ]
    # The top-campaigns card is only wired up as a read endpoint for Nixon today,
    # so only Nixon has a cache entry worth warming for it.
    if slug == "nixon":
        tasks.append(("marketing", lambda: _api._marketing_read(slug, start, end)))

    for name, fn in tasks:
        try:
            fn()
            report["warmed"].append(name)
        except Exception as exc:
            report["errors"][name] = str(exc)[:200]
            LOGGER.warning("cache warm failed [%s/%s]", slug, name, exc_info=True)

    LOGGER.info("cache warm complete [%s]: %s", slug, report)
    return report
