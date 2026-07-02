"""Single ingestion and transformation workflow for BigQuery dashboards."""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)


def should_run_ingestion(trigger: str) -> bool:
    """Advertising APIs are allowed only for explicit refresh workflows."""
    return trigger in {"cron", "manual_full", "onboarding"}


def ingestion_window(trigger: str, *, today: date | None = None) -> tuple[date, date]:
    """Cron/full refresh reprocess 30 days; onboarding is a separate 180-day run."""
    end = today or date.today()
    days = 180 if str(trigger).strip().lower() == "onboarding" else 30
    return end - timedelta(days=days - 1), end


# Data connectors driven by the daily refresh, in sync order. One per-client
# path for all of them; GTM (tag-container config, not a BQ data source) stays
# out. GSC + HubSpot self-route from their connector config like the rest.
_SYNC_CONNECTORS = ["ga4", "google_ads", "meta_ads", "linkedin_ads", "gsc", "hubspot", "semrush"]


def _trigger_date_range(trigger: str) -> str:
    """Connector date_range preset for a given refresh trigger."""
    return "LAST_180_DAYS" if str(trigger).strip().lower() == "onboarding" else "LAST_30_DAYS"


def _status(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, **details}


def _run_step(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    started = datetime.now(tz=UTC)
    try:
        result = fn() or {}
        result.setdefault("status", "success")
    except Exception as exc:
        LOGGER.exception("bigquery_refresh step failed: %s", name)
        result = _status("failed", error=str(exc)[:600])
    result["started_at"] = started.isoformat()
    result["finished_at"] = datetime.now(tz=UTC).isoformat()
    LOGGER.info("bigquery_refresh step=%s result=%s", name, result)
    return result


def _table_freshness(client: Any, table_id: str, *, date_column: str = "date") -> dict[str, Any]:
    try:
        client.get_table(table_id)
    except Exception as exc:
        return _status("pending_data", table=table_id, error=str(exc)[:300])
    sql = (
        f"SELECT COUNT(*) AS row_count, CAST(MAX({date_column}) AS STRING) AS data_through "
        f"FROM `{table_id}`"
    )
    rows = list(client.query(sql).result(max_results=1))
    row = rows[0] if rows else None
    count = int(getattr(row, "row_count", 0) or 0)
    through = getattr(row, "data_through", None)
    return _status(
        "success" if count else "pending_data",
        table=table_id,
        row_count=count,
        data_through=str(through) if through else None,
    )


def _latest_table_date(tables: list[Any]) -> str | None:
    suffixes: list[str] = []
    for table in tables:
        match = re.search(r"(20\d{6})$", str(getattr(table, "table_id", table)))
        if match:
            suffixes.append(match.group(1))
    if not suffixes:
        return None
    latest = max(suffixes)
    return f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"


def verify_native_sources(
    *,
    client: Any,
    project_id: str,
    ga4_dataset_id: str,
    google_required: bool,
    client_slug: str | None = None,
) -> dict[str, Any]:
    """Verify native exports without creating or backfilling their datasets."""
    import os

    mart_dataset = (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()
    google_fact = f"{project_id}.{mart_dataset}.fact_google_ads_campaign_daily"
    google = (
        _table_freshness(client, google_fact)
        if google_required
        else _status("not_configured", required=False)
    )

    ga4_dataset = f"{project_id}.{ga4_dataset_id}"
    try:
        ga4_tables = list(client.list_tables(ga4_dataset))
        ga4 = _status(
            "success" if ga4_tables else "pending_data",
            dataset=ga4_dataset,
            table_count=len(ga4_tables),
            data_through=_latest_table_date(ga4_tables),
        )
    except Exception as exc:
        ga4 = _status("not_configured", dataset=ga4_dataset, error=str(exc)[:300])

    try:
        import gsc_clients

        gsc_target = gsc_clients.resolve_target(client_slug)
        native_dataset = str(gsc_target.native_dataset_id or "").strip()
        if not native_dataset:
            gsc = _status("not_configured", required=True)
        else:
            dataset_id = f"{gsc_target.bq_project_id}.{native_dataset}"
            tables = list(client.list_tables(dataset_id))
            gsc = _status(
                "success" if tables else "pending_data",
                dataset=dataset_id,
                table_count=len(tables),
                data_through=_latest_table_date(tables),
            )
    except Exception as exc:
        gsc = _status("not_configured", error=str(exc)[:300])
    return {"google": google, "ga4": ga4, "search_console": gsc}


def run_client_bigquery_refresh(
    *, client_slug: str, trigger: str = "manual_full", today: date | None = None
) -> dict[str, Any]:
    """Provision, drive each connector's sync, rebuild the unified mart, report."""
    import bigquery_warehouse
    import client_bigquery_setup
    import client_config
    import client_dashboard_config
    import connector_config_store
    import connectors  # noqa: F401 — importing registers the connector handlers
    import dashboard_snapshots
    from connectors.base import get as get_handler

    cfg = client_config.load_client_config(client_slug)
    client_key = cfg.ga4_client_key or client_slug

    # BQ routing: prefer client_dashboard_config.gcp_project_id (set once via
    # the dashboard-creation flow, or auto-backfilled from the first
    # connector's "Confirm destination" step — see connector_routes.py). This
    # is the only thing a connector-based client needs configured; it does
    # NOT require a GA4/GSC registry entry (ga4_clients / client_registry_store).
    # Fall back to the older GA4-registry resolution only for clients that
    # predate the connector system and never got gcp_project_id set.
    db_cfg = client_dashboard_config.get_config(client_slug)
    bq_project_id = (db_cfg.gcp_project_id if db_cfg else None) or None
    bq_credentials_env: str | None = None
    if not bq_project_id:
        import ga4_clients
        target = ga4_clients.resolve_client_config(client_key=client_key)
        bq_project_id = target.bq_project_id
        bq_credentials_env = target.credentials_env

    date_range = _trigger_date_range(trigger)
    run_started = datetime.now(tz=UTC)
    result: dict[str, Any] = {
        "status": "running",
        "client_key": client_key,
        "trigger": trigger,
        "date_range": date_range,
        "started_at": run_started.isoformat(),
        "sources": {},
        "transformations": {},
    }

    if db_cfg and db_cfg.gcp_project_id:
        provision = _run_step(
            "provision",
            lambda: client_bigquery_setup.ensure_client_datasets(
                project_id=db_cfg.gcp_project_id,
            ),
        )
    else:
        provision = _run_step(
            "provision",
            lambda: client_bigquery_setup.ensure_client_bq_resources(
                client_key=client_key,
                needs_google=bool(cfg.google_customer_id),
                needs_linkedin=bool(cfg.linkedin_account_id),
                needs_meta=bool(cfg.meta_account_id),
                needs_mart=bool(
                    cfg.google_customer_id or cfg.linkedin_account_id or cfg.meta_account_id
                ),
            ),
        )
    result["provision"] = provision
    if provision["status"] == "failed":
        result.update(status="failed", finished_at=datetime.now(tz=UTC).isoformat())
        return result

    # One sync path: drive each connected connector's run_sync (API → BigQuery +
    # its own mart views), recording results to connector_configs so the
    # connector cards reflect cron runs too. This replaces the old hardcoded
    # per-source ingest and brings GA4 into the automated daily refresh. Each
    # connector self-routes to the client's BQ project from its stored config.
    configs = {c.connector_type: c for c in connector_config_store.list_configs(client_slug)}
    for ctype in _SYNC_CONNECTORS:
        conf = configs.get(ctype)
        handler = get_handler(ctype)
        if not handler or not conf or conf.status not in ("connected", "error", "syncing"):
            result["sources"][ctype] = _status("not_configured")
            continue
        # sync_enabled only gates the automated cron trigger — an explicit
        # manual/onboarding refresh always runs regardless of the toggle.
        if trigger == "cron" and not conf.sync_enabled:
            result["sources"][ctype] = _status("sync_disabled")
            continue

        def _sync(ctype: str = ctype, handler: Any = handler, conf: Any = conf) -> dict[str, Any]:
            run_id = connector_config_store.start_sync_run(
                conf.id, run_type="cron", triggered_by="cron"
            )
            connector_config_store.update_sync_timestamps(client_slug, ctype, started=True)
            res = handler.run_sync(client_slug=client_slug, date_range=date_range)
            connector_config_store.finish_sync_run(
                run_id,
                status="completed" if res.ok else "failed",
                rows_loaded=res.rows_loaded,
                error_message=res.error,
            )
            connector_config_store.update_sync_timestamps(
                client_slug, ctype,
                completed=True, success=res.ok, error=res.error,
                range_start=res.range_start, range_end=res.range_end,
                rows_loaded=res.rows_loaded,
            )
            if res.ok:
                # Drop cached API reads for this client immediately rather than
                # waiting out their TTL — new data just landed in BQ.
                try:
                    import db_cache
                    db_cache.invalidate_prefix(f"{client_slug}.")
                    # The Nixon dashboard's read endpoints are cached under a
                    # literal "nixon." prefix regardless of which BQ-routing
                    # client_slug (e.g. "nixon-bq-test" for GSC/SEMrush) a
                    # given connector syncs under.
                    if client_slug.startswith("nixon"):
                        db_cache.invalidate_prefix("nixon.")
                except Exception:
                    LOGGER.warning("cache invalidation failed [%s/%s]", client_slug, ctype, exc_info=True)
            return {
                "status": "success" if res.ok else "failed",
                "rows_loaded": res.rows_loaded,
                "error": res.error,
                "data_through": res.range_end.isoformat() if (res.ok and res.range_end) else None,
            }

        result["sources"][ctype] = _run_step(ctype, _sync)

    # Cross-source transform: rebuild the unified daily fact from whatever
    # per-platform marts the connectors produced.
    ids = client_bigquery_setup.dataset_ids()
    with bigquery_warehouse.route(
        bq_project_id=bq_project_id,
        credentials_env=bq_credentials_env,
        google_dataset_id=ids["google"],
        linkedin_dataset_id=ids["linkedin"],
        meta_dataset_id=ids["meta"],
        mart_dataset_id=ids["mart"],
    ):
        # client_slug (not client_key) here deliberately — client_key above is
        # resolved from cfg.ga4_client_key purely for the legacy GA4-registry
        # fallback and can differ from the connector system's actual tenant
        # id. The unified mart's client_key COLUMN must be the connectors'
        # real identifier (client_slug) so every source is tagged consistently
        # — a source whose per-platform mart lacks its own client_key column
        # (e.g. LinkedIn) falls back to this value as a literal in the view.
        result["transformations"]["unified_mart"] = _run_step(
            "unified_mart",
            lambda: bigquery_warehouse.rebuild_unified_marketing_mart(client_slug),
        )

    if result["transformations"]["unified_mart"].get("status") == "success":
        cleared = dashboard_snapshots.delete_snapshot(client_slug)
        result["cache"] = _status("cleared", previous_snapshot=bool(cleared))
    else:
        result["cache"] = _status("retained", reason="mart_rebuild_not_successful")

    failures = [
        name
        for name, value in result["sources"].items()
        if value.get("status") in {"failed", "partial_failure"}
    ]
    pending = [
        name
        for name, value in result["sources"].items()
        if value.get("status") == "pending_data"
    ]
    result["status"] = (
        "partial_failure" if failures else "pending_data" if pending else "success"
    )
    result["failed_sources"] = failures
    result["pending_sources"] = pending
    result["last_refreshed_at"] = datetime.now(tz=UTC).isoformat()
    result["finished_at"] = result["last_refreshed_at"]
    return result
