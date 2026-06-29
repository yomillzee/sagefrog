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
    """Provision, ingest, transform, and report each source independently."""
    import bigquery_service
    import bigquery_warehouse
    import bq_linkedin_ads_service
    import bq_meta_ads_service
    import client_bigquery_setup
    import client_config
    import dashboard_snapshots
    import ga4_clients
    import linkedin_service

    cfg = client_config.load_client_config(client_slug)
    client_key = cfg.ga4_client_key or client_slug
    target = ga4_clients.resolve_client_config(client_key=client_key)
    start, end = ingestion_window(trigger, today=today)
    run_started = datetime.now(tz=UTC)
    result: dict[str, Any] = {
        "status": "running",
        "client_key": client_key,
        "trigger": trigger,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "started_at": run_started.isoformat(),
        "sources": {},
        "transformations": {},
    }

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

    client = bigquery_service.build_client(
        target.bq_project_id, credentials_info=target.credentials
    )
    result["sources"].update(
        verify_native_sources(
            client=client,
            project_id=target.bq_project_id,
            ga4_dataset_id=target.bq_dataset_id,
            google_required=bool(cfg.google_customer_id),
            client_slug=client_slug,
        )
    )

    ids = client_bigquery_setup.dataset_ids()
    with bigquery_warehouse.route(
        bq_project_id=target.bq_project_id,
        credentials_env=target.credentials_env,
        google_dataset_id=ids["google"],
        linkedin_dataset_id=ids["linkedin"],
        meta_dataset_id=ids["meta"],
        mart_dataset_id=ids["mart"],
    ):
        if cfg.linkedin_account_id:
            def ingest_linkedin() -> dict[str, Any]:
                with bq_linkedin_ads_service.route(
                    bq_project_id=target.bq_project_id,
                    credentials_env=target.credentials_env,
                ):
                    rows = linkedin_service.fetch_campaign_daily_metrics(
                        cfg.linkedin_account_id, start=start, end=end
                    )
                    raw = bigquery_warehouse.mirror_campaign_daily_batch(
                        "linkedin", cfg.linkedin_account_id, rows
                    )
                    transformed = bq_linkedin_ads_service.sync_campaign_metadata_and_rebuild_mart(
                        account_id=cfg.linkedin_account_id, start=start, end=end
                    )
                return {
                    "status": "success",
                    "rows_fetched": len(rows),
                    "rows_merged": raw.get("rows_upserted", 0),
                    "transform": transformed,
                    "data_through": end.isoformat() if rows else None,
                }

            result["sources"]["linkedin"] = _run_step("linkedin", ingest_linkedin)
        else:
            result["sources"]["linkedin"] = _status("not_configured")

        if cfg.meta_account_id:
            def ingest_meta() -> dict[str, Any]:
                with bq_meta_ads_service.route(
                    bq_project_id=target.bq_project_id,
                    credentials_env=target.credentials_env,
                ):
                    synced = bq_meta_ads_service.sync_meta_to_bq(
                        cfg.meta_account_id, start=start, end=end
                    )
                return {
                    "status": "partial_failure" if synced.get("errors") else "success",
                    **synced,
                    "data_through": end.isoformat()
                    if int(synced.get("campaign_rows") or 0) > 0
                    else None,
                }

            result["sources"]["meta"] = _run_step(
                "meta", ingest_meta
            )
        else:
            result["sources"]["meta"] = _status("not_configured")

        if cfg.google_customer_id:
            def ingest_google() -> dict[str, Any]:
                import bq_google_ads_service
                import oauth_store as _oauth

                refresh = _oauth.get_refresh_token("google_ads", client_slug=client_slug)
                if not refresh:
                    raise RuntimeError("No Google Ads refresh token for this client.")
                with bq_google_ads_service.route(
                    bq_project_id=target.bq_project_id,
                    credentials_env=target.credentials_env,
                ):
                    synced = bq_google_ads_service.sync_google_ads_to_bq(
                        cfg.google_customer_id,
                        start=start.isoformat(),
                        end=end.isoformat(),
                        refresh_token=refresh,
                        client_key=client_key,
                    )
                return {
                    "status": "partial_failure" if synced.get("errors") else "success",
                    "campaign_rows": synced.get("campaign_rows", 0),
                    "data_through": end.isoformat() if synced.get("campaign_rows", 0) > 0 else None,
                }

            result["sources"]["google"] = _run_step("google", ingest_google)
        else:
            result["sources"]["google"] = _status("not_configured")

        result["transformations"]["google_fact"] = _run_step(
            "google_fact", bigquery_warehouse.create_google_campaign_mart_view
        ) if cfg.google_customer_id else _status("not_configured")
        result["transformations"]["unified_mart"] = _run_step(
            "unified_mart", lambda: bigquery_warehouse.rebuild_unified_marketing_mart(client_key)
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
