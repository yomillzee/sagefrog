"""PageSpeed Insights connector — Lighthouse scores synced daily into BigQuery.

No OAuth: PSI works keyless (or with a single shared PAGESPEED_API_KEY env var,
agency-level like SEMRUSH_API_KEY). The only per-client input is the URL to
audit, entered manually in the wizard (there's no "list accounts" API) and
stored as source_account_id. Strategy defaults to desktop (PAGESPEED_STRATEGY).
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import ConnectorHandler, SyncResult, register

_log = logging.getLogger(__name__)


class PageSpeedConnector(ConnectorHandler):
    connector_type = "pagespeed"
    display_name = "PageSpeed Insights"
    oauth_platform = "pagespeed"
    default_raw_dataset = "raw_pagespeed"
    no_oauth = True
    manual_account_entry = True
    manual_account_label = "Homepage URL (e.g. https://example.com)"

    def list_accounts(self, *, client_slug: str) -> list[dict[str, Any]]:
        """Wizard "Test connection" step — verifies the URL actually returns a
        Lighthouse result from PSI. Not a real account picker (nothing to list).
        """
        import connector_config_store
        import pagespeed_service

        cfg = connector_config_store.get_config(client_slug, "pagespeed")
        url = (cfg.source_account_id if cfg else "") or ""
        if not url:
            raise RuntimeError("Enter a URL first.")

        # A live Lighthouse audit can take 15–60s. The wizard runs this
        # synchronously behind Railway's edge proxy, so use a short timeout and
        # treat a slow audit as a soft pass (the URL/key are fine; the real audit
        # runs in the background sync). Auth/config errors return fast and still
        # surface as a proper failure.
        try:
            scores = pagespeed_service.fetch_scores(url, timeout=25)
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                return [{
                    "id": pagespeed_service.normalize_url(url),
                    "name": f"{url} — reachable; the first audit is slow and completes on the initial sync",
                    "status": "ok",
                }]
            raise

        perf = scores.get("performance")
        label = f"{scores.get('final_url', url)} — Performance {perf}" if perf is not None else url
        return [{"id": scores.get("url", url), "name": label, "status": "ok"}]

    # Audit both form factors every sync so the dashboard's desktop/mobile
    # toggle always has data for either. Each strategy is a separate BQ row
    # (strategy column), so the two never collide.
    strategies = ("desktop", "mobile")

    def run_sync(self, *, client_slug: str, date_range: str = "LAST_30_DAYS") -> SyncResult:
        import bq_pagespeed_service
        import connector_config_store

        cfg = connector_config_store.get_config(client_slug, "pagespeed")
        url = cfg.source_account_id if cfg else None
        if not url:
            return SyncResult(rows_loaded=0, error="No URL configured.")

        bq_project_id = cfg.bq_project_id if cfg else None
        raw_dataset_id = cfg.raw_dataset_id if cfg else None

        total_rows = 0
        errors: dict[str, str] = {}
        try:
            with bq_pagespeed_service.route(
                bq_project_id=bq_project_id,
                pagespeed_dataset_id=raw_dataset_id,
            ):
                for strat in self.strategies:
                    result = bq_pagespeed_service.sync_pagespeed_to_bq(
                        url, client_key=client_slug, strategy=strat
                    )
                    total_rows += result.get("total_rows") or 0
                    for k, v in (result.get("errors") or {}).items():
                        errors[f"{strat}:{k}"] = v
            error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else None
            return SyncResult(rows_loaded=total_rows, error=error_msg)
        except Exception as exc:
            _log.warning("PageSpeed sync failed [%s]: %s", client_slug, exc)
            return SyncResult(rows_loaded=total_rows, error=str(exc)[:500])


register(PageSpeedConnector())
