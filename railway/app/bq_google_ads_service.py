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


def _schema_ad_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("account_id",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("campaign_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_group_id",      "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_group_name",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_id",            "STRING",    mode="REQUIRED"),
        bq.SchemaField("ad_name",          "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_type",          "STRING",    mode="NULLABLE"),
        bq.SchemaField("ad_status",        "STRING",    mode="NULLABLE"),
        bq.SchemaField("final_url",        "STRING",    mode="NULLABLE"),
        bq.SchemaField("headline_1",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("headline_2",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("headline_3",       "STRING",    mode="NULLABLE"),
        bq.SchemaField("description_1",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("description_2",    "STRING",    mode="NULLABLE"),
        bq.SchemaField("image_ad_name",    "STRING",    mode="NULLABLE"),
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

    for table_name, schema_fn in [
        ("campaign_daily", _schema_campaign_daily),
        ("ad_daily", _schema_ad_daily),
    ]:
        table_id = f"{dataset_ref}.{table_name}"
        table = bq.Table(table_id, schema=schema_fn(bq))
        table.time_partitioning = bq.TimePartitioning(field="metric_date")
        client.create_table(table, exists_ok=True, timeout=30)
    _log.info("Google Ads tables ensured in %s", dataset_ref)


def _mart_dataset_id() -> str:
    return (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()


def create_google_ads_mart_views() -> dict[str, Any]:
    """Build mart views over raw_google_ads.ad_daily that the explorer and breakdowns read.

    Creates three views in marketing_marts:
      - fact_google_ads_ad_daily       — ad-level metrics with creative fields
      - fact_google_ads_ad_group_daily — ad-group aggregation (derived from ad_daily)
      - explorer_google_ads_daily      — denormalized explorer table the UI queries
    """
    bq = _bq()
    client = _client()
    project = _project_id()
    raw_dataset = _dataset_id()
    mart_dataset = _mart_dataset_id()
    raw_ad_table = f"`{project}.{raw_dataset}.ad_daily`"
    mart_ref = f"{project}.{mart_dataset}"

    client.create_dataset(bq.Dataset(mart_ref), exists_ok=True, timeout=30)

    views = {
        "fact_google_ads_ad_daily": f"""
            SELECT
              metric_date AS date,
              account_id,
              campaign_id,
              campaign_name,
              ad_group_id,
              ad_group_name,
              ad_id,
              ad_name,
              ad_type,
              ad_status,
              final_url,
              headline_1,
              headline_2,
              headline_3,
              description_1,
              description_2,
              image_ad_name,
              spend,
              impressions,
              clicks,
              conversions,
              conversion_value,
              synced_at
            FROM {raw_ad_table}
        """,
        "fact_google_ads_ad_group_daily": f"""
            SELECT
              metric_date AS date,
              account_id,
              campaign_id,
              ANY_VALUE(campaign_name) AS campaign_name,
              ad_group_id,
              ANY_VALUE(ad_group_name) AS ad_group_name,
              SUM(spend) AS spend,
              SUM(impressions) AS impressions,
              SUM(clicks) AS clicks,
              SUM(conversions) AS conversions,
              SUM(conversion_value) AS conversion_value
            FROM {raw_ad_table}
            GROUP BY 1, 2, 3, 5
        """,
        "explorer_google_ads_daily": f"""
            SELECT
              'google_ads' AS source,
              account_id,
              campaign_id,
              campaign_name,
              ad_group_id,
              ad_group_name,
              ad_id,
              COALESCE(NULLIF(ad_name, ''), ad_id) AS ad_label,
              NULLIF(headline_1, '') AS headline_1,
              NULLIF(headline_2, '') AS headline_2,
              NULLIF(headline_3, '') AS headline_3,
              NULLIF(description_1, '') AS description_1,
              NULLIF(description_2, '') AS description_2,
              NULLIF(image_ad_name, '') AS image_ad_name,
              NULLIF(ad_name, '') AS ad_name,
              NULLIF(final_url, '') AS final_url,
              NULLIF(ad_type, '') AS ad_type,
              metric_date AS date,
              spend,
              impressions,
              clicks,
              conversions,
              conversion_value
            FROM {raw_ad_table}
        """,
    }

    results: dict[str, Any] = {}
    for view_name, select_sql in views.items():
        table_id = f"{mart_ref}.{view_name}"
        sql = f"CREATE OR REPLACE VIEW `{table_id}` AS\n{select_sql}"
        try:
            client.query(sql).result(timeout=60)
            results[view_name] = "created"
            _log.info("Google Ads mart view ensured: %s", table_id)
        except Exception as exc:
            results[view_name] = f"error: {exc!s:.200}"
            _log.warning("Google Ads mart view failed [%s]: %s", view_name, exc)
    return results


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


def _write_ad_daily(
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    account_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("Google Ads ad_daily — 0 rows from API, skipping write")
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref("ad_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND account_id = '{account_id}' "
        f"  AND metric_date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=_schema_ad_daily(bq),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info(
        "Google Ads wrote %d rows → ad_daily [client=%s account=%s]",
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
    """Pull Google Ads campaign + ad-level metrics and write to BQ. Returns row counts."""
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

    now = datetime.now(tz=timezone.utc).isoformat()
    errors: dict[str, str] = {}

    # Campaign-level daily metrics
    campaign_raw = google_ads_service.fetch_campaign_daily_metrics(
        customer_id_clean, start=start_date, end=end_date, client=ads_client
    )
    campaign_rows = [
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
        for r in campaign_raw
        if r.get("campaign_id") and r.get("metric_date")
    ]
    n_campaign = _write_campaign_daily(
        campaign_rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
    )

    # Ad-level daily metrics with creative fields (headlines, descriptions, ad type, URL)
    n_ad = 0
    try:
        ad_raw = google_ads_service.fetch_ad_daily_metrics(
            customer_id_clean, start=start_date, end=end_date, client=ads_client
        )
        ad_rows = [
            {
                "client_key": client_key,
                "account_id": customer_id_clean,
                "campaign_id": str(r.get("campaign_id") or ""),
                "campaign_name": r.get("campaign_name"),
                "ad_group_id": str(r.get("ad_group_id") or ""),
                "ad_group_name": r.get("ad_group_name"),
                "ad_id": str(r.get("ad_id") or ""),
                "ad_name": r.get("ad_name"),
                "ad_type": r.get("ad_type"),
                "ad_status": r.get("ad_status"),
                "final_url": r.get("final_url"),
                "headline_1": r.get("headline_1"),
                "headline_2": r.get("headline_2"),
                "headline_3": r.get("headline_3"),
                "description_1": r.get("description_1"),
                "description_2": r.get("description_2"),
                "image_ad_name": r.get("image_ad_name"),
                "metric_date": r.get("metric_date") or "",
                "spend": float(r.get("spend") or 0.0),
                "impressions": int(r.get("impressions") or 0),
                "clicks": int(r.get("clicks") or 0),
                "conversions": float(r.get("conversions") or 0.0),
                "conversion_value": float(r.get("conversion_value") or 0.0),
                "synced_at": now,
            }
            for r in ad_raw
            if r.get("ad_id") and r.get("metric_date")
        ]
        n_ad = _write_ad_daily(
            ad_rows, client_key=client_key, account_id=customer_id_clean, start=start, end=end
        )
    except Exception as exc:
        _log.warning("Google Ads ad_daily sync failed [%s]: %s", client_key, exc)
        errors["ad_daily"] = str(exc)[:300]

    # Rebuild mart views (explorer_google_ads_daily + fact_ views)
    mart_errors: dict[str, str] = {}
    try:
        mart_result = create_google_ads_mart_views()
        mart_errors = {k: v for k, v in mart_result.items() if str(v).startswith("error")}
    except Exception as exc:
        _log.warning("Google Ads mart view rebuild failed [%s]: %s", client_key, exc)
        mart_errors["mart_views"] = str(exc)[:300]
    errors.update(mart_errors)

    total = n_campaign + n_ad
    _log.info(
        "Google Ads sync complete [%s]: campaign_daily=%d ad_daily=%d",
        client_key, n_campaign, n_ad,
    )
    return {"total_rows": total, "campaign_rows": n_campaign, "ad_rows": n_ad, "errors": errors}
