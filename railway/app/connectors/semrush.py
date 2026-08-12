"""SEMrush connector — domain analytics synced monthly into BigQuery.

No OAuth: SEMrush is authenticated with a single shared SEMRUSH_API_KEY env
var (agency-level, like the GSC service account). The only per-client input
is the root domain to track, entered manually in the wizard (no "list
accounts" API exists for SEMrush) and stored as source_account_id.

That single key draws on one agency-wide monthly API-unit balance, so every
client's sync competes for the same pool — see DEFAULT_SYNC_INTERVAL_DAYS.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)

# Days between automated (cron) SEMrush syncs. One sync spends units on four
# separate endpoints (domain overview, organic keywords, backlinks, AI
# overview), and organic keywords bills per returned row — so a nightly run
# per client drained the shared agency balance in days. Domain authority,
# backlink counts and keyword rankings move slowly, so a monthly snapshot is
# the right granularity anyway. Manual "Run sync now" and the onboarding
# backfill ignore this and still run on demand.
DEFAULT_SYNC_INTERVAL_DAYS = 30


def sync_interval_days() -> int:
    """SEMRUSH_SYNC_INTERVAL_DAYS override, so the cadence can be retuned
    against the remaining unit balance without a code change."""
    raw = (os.getenv("SEMRUSH_SYNC_INTERVAL_DAYS") or "").strip()
    if not raw:
        return DEFAULT_SYNC_INTERVAL_DAYS
    try:
        return max(0, int(raw))
    except ValueError:
        _log.warning("Invalid SEMRUSH_SYNC_INTERVAL_DAYS=%r; using %s", raw, DEFAULT_SYNC_INTERVAL_DAYS)
        return DEFAULT_SYNC_INTERVAL_DAYS


class SEMrushConnector(ConnectorHandler):
    connector_type = "semrush"
    display_name = "SEMrush"
    oauth_platform = "semrush"
    default_raw_dataset = "raw_semrush"
    no_oauth = True
    manual_account_entry = True
    manual_account_label = "Root domain (no www, no https://)"
    min_sync_interval_days = sync_interval_days()

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        """Used by the wizard's "Test connection" step — verifies the configured
        domain actually returns data from the SEMrush API. Not a real account
        picker (there's nothing to list), so it always returns 0 or 1 entries.
        """
        import connector_config_store
        import semrush_service

        cfg = connector_config_store.get_config(client_slug, "semrush")
        domain = (cfg.source_account_id if cfg else "") or ""
        if not domain:
            raise RuntimeError("Enter a domain first.")

        overview = semrush_service.fetch_domain_overview(domain)
        if overview.get("error"):
            raise RuntimeError(overview["error"])
        return [{"id": domain, "name": domain, "status": "ok"}]

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_semrush_service
        import connector_config_store

        cfg = connector_config_store.get_config(client_slug, "semrush")
        domain = cfg.source_account_id if cfg else None
        if not domain:
            return SyncResult(rows_loaded=0, error="No domain configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = cfg.raw_dataset_id if cfg else None

        try:
            with bq_semrush_service.route(
                bq_project_id=bq_project_id,
                semrush_dataset_id=raw_dataset_id,
            ):
                result = bq_semrush_service.sync_semrush_to_bq(domain, client_key=client_slug)
            errors = result.get("errors") or {}
            error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None
            return SyncResult(rows_loaded=result.get("total_rows") or 0, error=error_msg)
        except Exception as exc:
            _log.warning("SEMrush sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=0, error=str(exc)[:500])


register(SEMrushConnector())
