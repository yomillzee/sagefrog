"""Sync Google Ads API → BigQuery raw tables.

All tables live in {project}.raw_google_ads. Every table includes client_key
and account_id for multi-tenant idempotency. DELETE is scoped to
client_key + account_id + metric_date range so multiple clients can share
a dataset without collisions.

The campaign_daily schema is intentionally compatible with the existing
bigquery_warehouse.create_google_campaign_mart_view() view.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_GOOGLE_DATASET = "raw_google_ads"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_google_ads_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    google_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (google_dataset_id or "").strip()
            or (os.getenv("BQ_GOOGLE_DATASET_ID") or "").strip()
            or _DEFAULT_GOOGLE_DATASET
        ),
        "credentials_env": (credentials_env or "").strip() or None,
    }
    token = _route_ctx.set(payload)
    try:
        yield
    finally:
        _route_ctx.reset(token)


def _ctx() -> dict:
    return _route_ctx.get() or {
        "project": None,
        "dataset": _DEFAULT_GOOGLE_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    r = _ctx()
    return (r.get("project") or "").strip() or _DEFAULT_PROJECT


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_GOOGLE_DATASET).strip()


def _credentials_env() -> str | None:
    return _ctx().get("credentials_env")


def _bq():
    from google.cloud import bigquery
    return bigquery


def _client():
    import bigquery_service
    return bigquery_service.build_client(_project_id(), credentials_env=_credentials_env())


def _table_ref(table_name: str) -> str:
    return f"{_project_id()}.{_dataset_id()}.{table_name}"


def _schema_campaign_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("spend",            "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("impressions",      "INT64",     mode="NULLABLE"),
        bq.SchemaField("clicks",           "INT64",     mode="NULLABLE"),
        bq.SchemaField("conversions",      "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("conversion_value", "FLOAT64",   mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def ensure_google_ads_tables() -> None:
    bq = _bq()
    client = _client()
    project = _project_id()
    dataset = _dataset_id()
    dataset_ref = f"{project}.{dataset}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    table_id = f"{dataset_ref}.campaign_daily"
    schema = _schema_campaign_daily(bq)
    table = bq.Table(table_id, schema=schema)
    table.time_partitioning = bq.TimePartitioning(field="metric_date")
    client.create_table(table, exists_ok=True, timeout=30)
    _log.info("Google Ads tables ensured in %s", dataset_ref)


def _write_campaign_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads campaign_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("campaign_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_campaign_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → campaign_daily [client=%s account=%s]",
        len(rows), client_key, account_id,
    )
    return len(rows)


def sync_google_ads_to_bq(
    customer_id: str,
    *,
    start: str,
    end: str,
    refresh_token: str,
    client_key: str,
) -> dict[str, Any]:
    """Pull Google Ads campaign metrics and write to BQ. Returns row counts."""
    from datetime import date as _date
    from auth import GoogleAdsEnv
    import google_ads_service

    ensure_google_ads_tables()

    dev_token = (
        os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or os.getenv("GOOGLE_DEVELOPER_TOKEN") or ""
    ).strip()
    client_id_val = (
        os.getenv("GOOGLE_ADS_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or ""
    ).strip()
    client_secret_val = (
        os.getenv("GOOGLE_ADS_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or ""
    ).strip()
    login_cid = (
        os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or os.getenv("GOOGLE_LOGIN_CUSTOMER_ID") or ""
    ).strip() or None

    env = GoogleAdsEnv(
        developer_token=dev_token,
        client_id=client_id_val,
        client_secret=client_secret_val,
        refresh_token=refresh_token,
        login_customer_id=login_cid,
    )
    ads_client = google_ads_service.build_client(env)

    customer_id_clean = str(customer_id).replace("-", "").strip()
    start_date = _date.fromisoformat(start)
    end_date = _date.fromisoformat(end)

    _log.info(
        "Google Ads sync: client=%s account=%s start=%s end=%s dataset=%s",
        client_key, customer_id_clean, start, end, _dataset_id(),
    )

    raw_rows = google_ads_service.fetch_campaign_daily_metrics(
        customer_id_clean, start=start_date, end=end_date, client=ads_client
    )

    now = datetime.now(tz=timezone.utc).isoformat()
    rows = [
        {
            "client_key": client_key,
            "account_id": customer_id_clean,
            "campaign_id": str(r.get("campaign_id") or ""),
            "campaign_name": r.get("campaign_name") or None,
            "metric_date": r.get("metric_date") or "",
            "spend": float(r.get("spend") or 0.0),
            "impressions": int(r.get("impressions") or 0),
            "clicks": int(r.get("clicks") or 0),
            "conversions": float(r.get("conversions") or 0.0),
            "conversion_value": float(r.get("conversion_value") or 0.0),
            "synced_at": now,
        }
        for r in raw_rows
        if r.get("campaign_id") and r.get("metric_date")
    ]

    n = _write_campaign_daily(
        rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
    )
    _log.info("Google Ads sync complete [%s]: campaign_daily=%d", client_key, n)
    return {"total_rows": n, "campaign_rows": n, "errors": {}}
