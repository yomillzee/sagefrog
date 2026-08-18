"""Google Search Console connector — service-account, no user OAuth."""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


def _describe_errors(
    errors: Any,
    *,
    failed_days: int | None = None,
    error_count: int | None = None,
    days_synced: int | None = None,
) -> str | None:
    """Turn sync_range()'s per-day failures into one sync-history message.

    sync_range caps the "errors" list it returns, and each failed day
    contributes up to two entries (query + page), so len(errors) is neither the
    number of failures nor the number of days. It reports both totals
    separately; prefer them, and fall back to the list only for older callers.

    Every failing day usually carries the *same* reason, so the reason is
    deduplicated rather than repeated until the 500-char column truncates it --
    "175 of 180 days failed: <one 403>" is the useful shape.
    """
    items = [str(e).strip() for e in (errors or []) if str(e).strip()]
    if not items:
        return None

    # Strip the "YYYY-MM-DD query: " / " page: " prefix so identical reasons collapse.
    reasons: list[str] = []
    for it in items:
        reason = it.split(": ", 1)[1].strip() if ": " in it else it
        if reason and reason not in reasons:
            reasons.append(reason)

    n_days = failed_days if failed_days else None
    if n_days and days_synced:
        scope = f"{n_days} of {days_synced} days failed"
    elif n_days:
        scope = f"{n_days} day(s) failed"
    else:
        scope = f"{error_count or len(items)} fetch(es) failed"

    head = " | ".join(reasons[:2])
    more = f" (+{len(reasons) - 2} other reasons)" if len(reasons) > 2 else ""
    return f"{scope}: {head}{more}"[:500]


class GSCConnector(ConnectorHandler):
    connector_type = "gsc"
    display_name = "Search Console"
    oauth_platform = "gsc"
    default_raw_dataset = "raw_gsc"
    no_oauth = True       # service account works headless as the fallback
    agency_oauth = True   # prefers one shared agency Google OAuth (gsc_read_creds)

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        """Every property the agency GSC login can see.

        Raises rather than returning [] on failure. Swallowing the error made a
        broken Google connection and a login with genuinely no properties render
        the same "No accounts found for this connection." — which is the one
        screen you go to in order to tell those two apart when a client's sync
        is 403ing. The wizard already renders the API's error text, and
        test_connection needs the raise to fail the step.
        """
        import gsc_sync_service
        try:
            props = gsc_sync_service.list_accessible_properties()
        except Exception as exc:
            _log.warning("GSC list_accounts failed [%s]: %s", client_slug, exc)
            raise RuntimeError(
                "Could not list Search Console properties. The agency Google "
                "connection (Admin → Connect Google Search Console) may need to be "
                f"reconnected: {exc}"[:400]
            ) from exc
        # list_accessible_properties returns dicts keyed "site_url" (not "siteUrl");
        # using the wrong key collapsed every property to an empty id → the wizard
        # showed "No accounts found" even when the service account had access.
        return [
            {"id": p.get("site_url", ""), "name": p.get("site_url", "")}
            for p in props
            if p.get("site_url")
        ]

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import client_dashboard_config
        import connector_config_store
        import gsc_clients
        import gsc_sync_service

        cfg = connector_config_store.get_config(client_slug, "gsc")
        # source_account_id stores the site URL chosen in step 2
        site_url = cfg.source_account_id if cfg else None
        if not site_url:
            db_cfg = client_dashboard_config.get_config(client_slug)
            site_url = (db_cfg.gsc_site_url if db_cfg else None) or ""

        # Build the BQ destination explicitly from this connector's config so the
        # sync writes to THIS client's project/raw_gsc — never the Penn default,
        # regardless of how resolve_target's lookups behave.
        target = gsc_clients.target_from_config(client_slug, cfg) if cfg else None

        try:
            result = gsc_sync_service.sync_for_refresh(
                site_url=site_url or None,
                client_slug=client_slug,
                target=target,
                # This call is already off the request/response cycle (invoked
                # via FastAPI BackgroundTasks in connector_routes.py), so block
                # for the full backfill instead of handing a large gap to an
                # untracked daemon thread -- that thread has no run tracking
                # and gets silently killed if the worker recycles before a
                # multi-minute backfill finishes, which left raw_gsc
                # permanently empty for at least one client (0 rows on every
                # "completed" sync, forever, with no error surfaced anywhere).
                wait_for_backfill=True,
            )
            ok = result.get("ok", True)
            # sync_range() (what actually runs here) reports query_rows/page_rows,
            # not rows_synced/rows_written -- those were always 0, masking real
            # sync results even when the sync succeeded.
            rows = (
                result.get("query_rows", 0) + result.get("page_rows", 0)
                or result.get("rows_synced") or result.get("rows_written") or 0
            )
            # sync_range() reports per-day failures in "errors" (a list) and only
            # sync_for_refresh's own setup failures in "error" (a string). Reading
            # just "error" meant a sync where every single day's GSC fetch failed
            # -- wrong property, revoked OAuth, 403 -- was recorded as
            # "completed, 0 rows" with a blank error column, every day, forever.
            error = result.get("error") or _describe_errors(
                result.get("errors"),
                failed_days=result.get("failed_days"),
                error_count=result.get("error_count"),
                days_synced=result.get("days_synced"),
            )
            # Refresh the GSC mart views over raw_gsc so the reporting tab has a
            # clean surface. Idempotent CREATE OR REPLACE; non-fatal.
            try:
                import bq_gsc_service
                bq_gsc_service.create_gsc_mart_views(client_slug=client_slug)
            except Exception as exc:
                _log.warning("GSC mart view provision failed [%s]: %s", client_slug, exc)
            return SyncResult(
                rows_loaded=rows,
                error=error if not ok else None,
            )
        except Exception as exc:
            _log.warning("GSC sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(GSCConnector())
