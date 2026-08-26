"""Optional BigQuery mirror for Postgres metrics_daily warehouse rows."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

_log = logging.getLogger(__name__)


_DEFAULT_LINKEDIN_DATASET = "raw_linkedin_ads"
_DEFAULT_LINKEDIN_ORGANIC_DATASET = "raw_linkedin_organic"
_DEFAULT_GOOGLE_DATASET = "raw_google_ads"
_DEFAULT_MICROSOFT_DATASET = "raw_microsoft_ads"
_DEFAULT_TABLE = "metrics_daily"
_DEFAULT_CAMPAIGN_TABLE = "campaign_daily"
_DEFAULT_CAMPAIGNS_TABLE = "campaigns"
_DEFAULT_AD_TABLE = "ad_daily"
_DEFAULT_CREATIVE_METADATA_TABLE = "creative_metadata"
_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_LINKEDIN_CAMPAIGN_FACT_TABLE = "fact_linkedin_ads_campaign_daily"
_DEFAULT_LINKEDIN_CREATIVE_FACT_TABLE = "fact_linkedin_ads_creative_daily"
_DEFAULT_LINKEDIN_DEMOGRAPHICS_TABLE = "demographics"
_DEFAULT_LINKEDIN_DEMOGRAPHICS_FACT_TABLE = "fact_linkedin_ads_demographics"


_route_ctx: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "bigquery_warehouse_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    credentials_env: str | None = None,
    google_dataset_id: str | None = None,
    linkedin_dataset_id: str | None = None,
    linkedin_organic_dataset_id: str | None = None,
    meta_dataset_id: str | None = None,
    microsoft_dataset_id: str | None = None,
    mart_dataset_id: str | None = None,
):
    """Route every warehouse read/write to one client's BigQuery identity."""
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "credentials_env": (credentials_env or "").strip() or None,
        "linkedin_dataset": (linkedin_dataset_id or "").strip() or None,
        "linkedin_organic_dataset": (linkedin_organic_dataset_id or "").strip() or None,
        "google_dataset": (google_dataset_id or "").strip() or None,
        "meta_dataset": (meta_dataset_id or "").strip() or None,
        "microsoft_dataset": (microsoft_dataset_id or "").strip() or None,
        "mart_dataset": (mart_dataset_id or "").strip() or None,
    }
    token = _route_ctx.set(payload if any(payload.values()) else None)
    try:
        yield
    finally:
        _route_ctx.reset(token)


def _route_value(key: str) -> str | None:
    current = _route_ctx.get()
    return current.get(key) if current else None


def _credentials_env() -> str | None:
    return _route_value("credentials_env")


def _client(project_id: str):
    import bigquery_service

    return bigquery_service.build_client(project_id, credentials_env=_credentials_env())


def _mart_dataset_id() -> str:
    return (
        _route_value("mart_dataset")
        or os.getenv("BQ_MART_DATASET_ID")
        or _DEFAULT_MART_DATASET
    ).strip()


def _bigquery():
    from google.cloud import bigquery

    return bigquery


def _schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("spend", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _campaign_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("spend", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]



def _campaign_metadata_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_status", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_group_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_group_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("last_synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _campaigns_table_id() -> tuple[Any, str]:
    client, base_table = _target("linkedin")
    return client, base_table.rsplit(".", 1)[0] + "." + _DEFAULT_CAMPAIGNS_TABLE


def ensure_linkedin_campaigns_table() -> str:
    bigquery = _bigquery()
    client, table_id = _campaigns_table_id()
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    table = bigquery.Table(table_id, schema=_campaign_metadata_schema())
    table.clustering_fields = ["source", "account_id", "campaign_id"]
    client.create_table(table, exists_ok=True)
    return table_id

def enabled(source: str | None = None) -> bool:
    flag = (os.getenv("BQ_WAREHOUSE_ENABLED") or os.getenv("BIGQUERY_WAREHOUSE_ENABLED") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if _route_value("project") and source and source.strip().lower() in {"google", "linkedin", "meta", "microsoft"}:
        return True
    return bool(source and source.strip().lower() == "linkedin" and _linkedin_project_id())


def _linkedin_project_id() -> str:
    project = (_route_value("project") or os.getenv("BQ_LINKEDIN_PROJECT_ID") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for LinkedIn reads/writes. Call bigquery_warehouse.route("
            "bq_project_id=...) for this client, or set BQ_LINKEDIN_PROJECT_ID for a single-tenant "
            "deployment. Refusing to silently fall back to another client's project."
        )
    return project


def _dataset_id(source: str) -> str:
    routed = _route_value(f"{source.strip().lower()}_dataset")
    if routed:
        return routed
    key = source.strip().upper().replace("-", "_")
    default_dataset = (
        _DEFAULT_LINKEDIN_DATASET
        if source == "linkedin"
        else _DEFAULT_GOOGLE_DATASET
        if source == "google"
        else _DEFAULT_MICROSOFT_DATASET
        if source == "microsoft"
        else ""
    )
    return (
        os.getenv(f"BQ_{key}_DATASET_ID")
        or os.getenv("BQ_WAREHOUSE_DATASET_ID")
        or default_dataset
    ).strip()


def _table_name(source: str) -> str:
    key = source.strip().upper().replace("-", "_")
    return (os.getenv(f"BQ_{key}_TABLE_ID") or os.getenv("BQ_WAREHOUSE_TABLE_ID") or _DEFAULT_TABLE).strip()


def _target(source: str) -> tuple[Any, str]:
    source_key = source.strip().lower()
    project_id = (
        _route_value("project")
        or (
            _linkedin_project_id()
            if source_key == "linkedin"
            else os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or ""
        )
    ).strip()
    dataset_id = _dataset_id(source_key)
    table_name = _table_name(source_key)
    if not project_id or not dataset_id or not table_name:
        raise RuntimeError("Missing BigQuery warehouse project/dataset/table configuration.")
    return _client(project_id), f"{project_id}.{dataset_id}.{table_name}"


def ensure_table(source: str) -> str:
    bigquery = _bigquery()
    client, table_id = _target(source)
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    table = bigquery.Table(table_id, schema=_schema())
    table.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    table.clustering_fields = ["source", "account_id"]
    client.create_table(table, exists_ok=True)
    return table_id


def _aggregate_daily_metrics(
    source_key: str,
    account_id_clean: str,
    rows: list[dict[str, Any]],
    synced_at: str,
) -> list[dict[str, Any]]:
    """Collapse metric rows to one per (source, account_id, metric_date).

    The metrics_daily fact is account+day grain, but callers may hand us finer
    rows -- e.g. LinkedIn's ``fetch_campaign_daily_metrics`` returns one row per
    (campaign, day). Summing them to account/day here keeps the totals correct
    AND guarantees the MERGE staging table has at most one row per target key,
    which BigQuery requires: a MERGE whose source matches >1 row for a target
    row fails with "UPDATE/MERGE must match at most one source row for each
    target row".
    """
    agg: dict[str, dict[str, Any]] = {}
    for row in rows:
        metric_date = row.get("metric_date") or row.get("metricDate")
        if not metric_date:
            continue
        md = str(metric_date)[:10]
        rec = agg.get(md)
        if rec is None:
            rec = {
                "source": source_key,
                "account_id": account_id_clean,
                "metric_date": md,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
                "synced_at": synced_at,
            }
            agg[md] = rec
        rec["spend"] += float(row.get("spend") or 0)
        rec["clicks"] += int(row.get("clicks") or 0)
        rec["impressions"] += int(row.get("impressions") or 0)
        rec["conversions"] += float(row.get("conversions") or 0)
        rec["conversion_value"] += float(row.get("conversion_value") or row.get("conversionValue") or 0)
    return list(agg.values())


def mirror_metrics_daily_batch(source: str, account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows or not enabled(source):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    source_key = source.strip().lower()
    account_id_clean = str(account_id).strip().split(":")[-1]
    client, table_id = _target(source_key)
    ensure_table(source_key)
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = _aggregate_daily_metrics(source_key, account_id_clean, rows, synced_at)
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    bigquery = _bigquery()
    job_config = bigquery.LoadJobConfig(schema=_schema(), write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      spend = S.spend,
      clicks = S.clicks,
      impressions = S.impressions,
      conversions = S.conversions,
      conversion_value = S.conversion_value,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, metric_date, spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.source, S.account_id, S.metric_date, S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_campaign_daily_batch(source: str, account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mirror per-campaign daily rows to BigQuery campaign_daily table."""
    if not rows or not enabled(source):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    source_key = source.strip().lower()
    account_id_clean = str(account_id).strip().split(":")[-1]
    client, base_table = _target(source_key)
    table_id = base_table.rsplit(".", 1)[0] + "." + _DEFAULT_CAMPAIGN_TABLE
    bigquery = _bigquery()
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    table = bigquery.Table(table_id, schema=_campaign_daily_schema())
    table.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    table.clustering_fields = ["source", "account_id", "campaign_id"]
    client.create_table(table, exists_ok=True)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        metric_date = row.get("metric_date")
        campaign_id = str(row.get("campaign_id") or "").strip()
        if not metric_date or not campaign_id:
            continue
        payload.append({
            "source": source_key,
            "account_id": account_id_clean,
            "campaign_id": campaign_id,
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": str(metric_date)[:10],
            "spend": float(row.get("spend") or 0),
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=_campaign_daily_schema(), write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id
      AND T.campaign_id = S.campaign_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      campaign_name = S.campaign_name,
      spend = S.spend, clicks = S.clicks, impressions = S.impressions,
      conversions = S.conversions, conversion_value = S.conversion_value,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.source, S.account_id, S.campaign_id, S.campaign_name, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def create_google_campaign_mart_view() -> dict[str, Any]:
    """Normalize a native Google transfer table into the campaign fact view.

    This function never creates or populates a Google raw table.  A missing
    transfer table is returned as ``pending_data`` so callers can explain the
    required GCP setup instead of silently substituting API snapshot rows.
    """
    project_id = _route_value("project") or (
        os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or ""
    ).strip()
    if not project_id:
        return {"enabled": False, "table": None, "reason": "missing_project"}
    raw_dataset = _dataset_id("google")
    raw_table = (os.getenv("BQ_GOOGLE_CAMPAIGN_RAW_TABLE") or "campaign_daily").strip()
    mart_dataset = _mart_dataset_id()
    fact_table = (os.getenv("BQ_MART_TABLE") or "fact_google_ads_campaign_daily").strip()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    table_id = f"{project_id}.{mart_dataset}.{fact_table}"
    raw_table_id = f"{project_id}.{raw_dataset}.{raw_table}"
    try:
        client.get_table(raw_table_id)
    except Exception as exc:
        return {
            "status": "pending_data",
            "table": table_id,
            "raw_table": raw_table_id,
            "error": str(exc)[:400],
        }
    try:
        existing_fact = client.get_table(table_id)
        if str(getattr(existing_fact, "table_type", "TABLE")).upper() == "TABLE":
            return {
                "status": "success",
                "table": table_id,
                "raw_table": raw_table_id,
                "existing": True,
            }
        create_statement = "CREATE OR REPLACE VIEW"
    except Exception:
        create_statement = "CREATE VIEW"
    sql = f"""
    {create_statement} `{table_id}` AS
    SELECT
      client_key,
      metric_date AS date,
      account_id,
      campaign_id,
      campaign_name,
      spend,
      impressions,
      clicks,
      conversions,
      conversion_value,
      synced_at
    FROM `{raw_table_id}`
    """
    client.query(sql).result()
    return {"status": "success", "table": table_id, "raw_table": raw_table_id}


def _microsoft_ad_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    S = bigquery.SchemaField
    return [
        S("source", "STRING", mode="REQUIRED"),
        S("account_id", "STRING", mode="REQUIRED"),
        S("campaign_id", "STRING", mode="REQUIRED"),
        S("campaign_name", "STRING", mode="NULLABLE"),
        S("ad_group_id", "STRING", mode="REQUIRED"),
        S("ad_group_name", "STRING", mode="NULLABLE"),
        S("ad_id", "STRING", mode="REQUIRED"),
        S("ad_type", "STRING", mode="NULLABLE"),
        S("ad_title", "STRING", mode="NULLABLE"),
        S("title_part_1", "STRING", mode="NULLABLE"),
        S("title_part_2", "STRING", mode="NULLABLE"),
        S("title_part_3", "STRING", mode="NULLABLE"),
        S("description_1", "STRING", mode="NULLABLE"),
        S("description_2", "STRING", mode="NULLABLE"),
        # Full RSA asset lists (JSON string arrays) from Campaign Management.
        S("headlines", "STRING", mode="NULLABLE"),
        S("descriptions", "STRING", mode="NULLABLE"),
        S("path_1", "STRING", mode="NULLABLE"),
        S("path_2", "STRING", mode="NULLABLE"),
        S("display_url", "STRING", mode="NULLABLE"),
        S("final_url", "STRING", mode="NULLABLE"),
        S("metric_date", "DATE", mode="REQUIRED"),
        S("spend", "FLOAT64", mode="NULLABLE"),
        S("impressions", "INT64", mode="NULLABLE"),
        S("clicks", "INT64", mode="NULLABLE"),
        S("conversions", "FLOAT64", mode="NULLABLE"),
        S("conversion_value", "FLOAT64", mode="NULLABLE"),
        S("synced_at", "TIMESTAMP", mode="NULLABLE"),
    ]


def mirror_microsoft_ad_daily_batch(account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert per-ad daily rows (with ad copy) into raw_microsoft_ads.ad_daily."""
    if not rows or not enabled("microsoft"):
        return {"enabled": False, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().split(":")[-1]
    client, base_table = _target("microsoft")
    table_id = base_table.rsplit(".", 1)[0] + ".ad_daily"
    bigquery = _bigquery()
    schema = _microsoft_ad_daily_schema()
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    table.clustering_fields = ["source", "account_id", "campaign_id", "ad_group_id"]
    client.create_table(table, exists_ok=True)
    # Add any columns an already-existing table lacks (e.g. headlines/descriptions
    # added after the table was first created) — create_table(exists_ok) never
    # alters schema, and the MERGE below would fail on the missing columns.
    try:
        existing = client.get_table(table_id)
        existing_cols = {f.name for f in existing.schema}
        missing = [f for f in schema if f.name not in existing_cols]
        if missing:
            existing.schema = list(existing.schema) + missing
            client.update_table(existing, ["schema"])
    except Exception:
        _log.warning("Microsoft ad_daily schema sync skipped for %s", table_id, exc_info=True)

    synced_at = datetime.now(timezone.utc).isoformat()
    cols = [f.name for f in schema]
    payload = []
    for row in rows:
        metric_date = row.get("metric_date")
        ad_id = str(row.get("ad_id") or "").strip()
        campaign_id = str(row.get("campaign_id") or "").strip()
        if not metric_date or not ad_id or not campaign_id:
            continue
        rec = {
            "source": str(row.get("source") or "microsoft"),
            "account_id": account_id_clean,
            "campaign_id": campaign_id,
            "campaign_name": row.get("campaign_name"),
            "ad_group_id": str(row.get("ad_group_id") or "").strip() or "0",
            "ad_group_name": row.get("ad_group_name"),
            "ad_id": ad_id,
            "ad_type": row.get("ad_type"),
            "ad_title": row.get("ad_title"),
            "title_part_1": row.get("title_part_1"),
            "title_part_2": row.get("title_part_2"),
            "title_part_3": row.get("title_part_3"),
            "description_1": row.get("description_1"),
            "description_2": row.get("description_2"),
            "headlines": row.get("headlines"),
            "descriptions": row.get("descriptions"),
            "path_1": row.get("path_1"),
            "path_2": row.get("path_2"),
            "display_url": row.get("display_url"),
            "final_url": row.get("final_url"),
            "metric_date": str(metric_date)[:10],
            "spend": float(row.get("spend") or 0),
            "impressions": int(row.get("impressions") or 0),
            "clicks": int(row.get("clicks") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        }
        payload.append(rec)
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    set_cols = ", ".join(f"{c} = S.{c}" for c in cols if c not in ("source", "account_id", "campaign_id", "ad_group_id", "ad_id", "metric_date"))
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"S.{c}" for c in cols)
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id AND T.campaign_id = S.campaign_id
       AND T.ad_group_id = S.ad_group_id AND T.ad_id = S.ad_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET {set_cols}
    WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def _microsoft_goal_daily_schema() -> list[Any]:
    """Per-conversion-goal daily rows from the Goals and Funnels report.

    Ad-group grain — Microsoft's report has no AdId column — and conversion
    metrics only: a row scoped to one goal has no share of the ad group's cost.
    """
    bigquery = _bigquery()
    S = bigquery.SchemaField
    return [
        S("source", "STRING", mode="REQUIRED"),
        S("account_id", "STRING", mode="REQUIRED"),
        S("campaign_id", "STRING", mode="REQUIRED"),
        S("campaign_name", "STRING", mode="NULLABLE"),
        S("ad_group_id", "STRING", mode="REQUIRED"),
        S("ad_group_name", "STRING", mode="NULLABLE"),
        S("goal_id", "STRING", mode="NULLABLE"),
        S("goal_name", "STRING", mode="REQUIRED"),
        S("metric_date", "DATE", mode="REQUIRED"),
        S("conversions", "FLOAT64", mode="NULLABLE"),
        S("conversion_value", "FLOAT64", mode="NULLABLE"),
        S("synced_at", "TIMESTAMP", mode="NULLABLE"),
    ]


def mirror_microsoft_goal_daily_batch(
    account_id: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Replace the per-goal breakdown for the days this batch covers.

    DELETE-then-INSERT for the same reason the Meta one is: a goal that stops
    converting stops appearing in the report entirely rather than arriving as a
    zero, so a MERGE would leave the last non-zero row standing forever.
    """
    if not rows or not enabled("microsoft"):
        return {"enabled": False, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().split(":")[-1]
    client, base_table = _target("microsoft")
    table_id = base_table.rsplit(".", 1)[0] + ".goal_daily"
    bigquery = _bigquery()
    schema = _microsoft_goal_daily_schema()
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    table.clustering_fields = ["source", "account_id", "campaign_id", "ad_group_id"]
    client.create_table(table, exists_ok=True)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    days: set[str] = set()
    for row in rows:
        metric_date = str(row.get("metric_date") or "")[:10]
        campaign_id = str(row.get("campaign_id") or "").strip()
        goal_name = str(row.get("goal_name") or "").strip()
        if not metric_date or not campaign_id or not goal_name:
            continue
        days.add(metric_date)
        payload.append({
            "source": str(row.get("source") or "microsoft"),
            "account_id": account_id_clean,
            "campaign_id": campaign_id,
            "campaign_name": row.get("campaign_name"),
            "ad_group_id": str(row.get("ad_group_id") or "").strip() or "0",
            "ad_group_name": row.get("ad_group_name"),
            "goal_id": str(row.get("goal_id") or "").strip() or None,
            "goal_name": goal_name,
            "metric_date": metric_date,
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE account_id = '{account_id_clean}' "
        f"  AND metric_date BETWEEN '{min(days)}' AND '{max(days)}'"
    ).result()
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")
    client.load_table_from_json(payload, table_id, job_config=job_config).result()
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def create_microsoft_conversion_action_mart_view() -> dict[str, Any]:
    """Expose raw_microsoft_ads.goal_daily as the explorer's per-goal view.

    Returns pending_data when the raw table doesn't exist yet — a client synced
    before the Goals and Funnels report was added has none until it re-syncs,
    and CREATE VIEW over a missing table would fail the whole rebuild.
    """
    project_id = _route_value("project") or (
        os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or ""
    ).strip()
    if not project_id:
        return {"status": "failed", "error": "missing_project"}
    raw_dataset = _dataset_id("microsoft")
    mart_dataset = _mart_dataset_id()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    raw_table_id = f"{project_id}.{raw_dataset}.goal_daily"
    try:
        client.get_table(raw_table_id)
    except Exception:
        return {"status": "pending_data", "table": None}
    view_id = f"{project_id}.{mart_dataset}.explorer_microsoft_conversion_action_daily"
    select_sql = f"""
    SELECT
      source,
      account_id,
      campaign_id,
      campaign_name,
      ad_group_id,
      ad_group_name,
      goal_id,
      goal_name,
      metric_date AS date,
      conversions,
      conversion_value
    FROM `{raw_table_id}`
    """
    _replace_object_with_view(client, view_id, select_sql)
    return {"status": "success", "table": view_id, "raw_table": raw_table_id}


def create_microsoft_ads_mart_view() -> dict[str, Any]:
    """Build the Microsoft Ads campaign explorer view the dashboard queries.

    Prefers the ad-level table (raw_microsoft_ads.ad_daily) so the Campaign
    Explorer can drill campaign → ad group → ad with ad copy, mirroring
    explorer_google_ads_daily. Falls back to campaign_daily (campaign grain, ad
    fields NULL) when ad_daily hasn't been synced yet. The view always exposes
    the same column set so fetch_microsoft_explorer reads it uniformly. Returns
    pending_data when neither raw table exists.
    """
    project_id = _route_value("project") or (
        os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or ""
    ).strip()
    if not project_id:
        return {"status": "failed", "error": "missing_project"}
    raw_dataset = _dataset_id("microsoft")
    mart_dataset = _mart_dataset_id()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    ad_table_id = f"{project_id}.{raw_dataset}.ad_daily"
    campaign_table_id = f"{project_id}.{raw_dataset}.campaign_daily"
    view_id = f"{project_id}.{mart_dataset}.explorer_microsoft_ads_daily"

    def _table_exists(tid: str) -> bool:
        try:
            client.get_table(tid)
            return True
        except Exception:
            return False

    if _table_exists(ad_table_id):
        select_sql = f"""SELECT
          metric_date                       AS date,
          account_id,
          campaign_id,
          campaign_name,
          ad_group_id,
          ad_group_name,
          ad_id,
          ad_type,
          ad_title,
          title_part_1,
          title_part_2,
          title_part_3,
          description_1,
          description_2,
          headlines,
          descriptions,
          path_1,
          path_2,
          display_url,
          final_url,
          spend, impressions, clicks, conversions, conversion_value
        FROM `{ad_table_id}`"""
        source_table = ad_table_id
    elif _table_exists(campaign_table_id):
        # Campaign-grain fallback: ad-level columns are NULL so the view schema is
        # identical whether or not ad_daily has been synced.
        select_sql = f"""SELECT
          metric_date                       AS date,
          account_id,
          campaign_id,
          campaign_name,
          CAST(NULL AS STRING) AS ad_group_id,
          CAST(NULL AS STRING) AS ad_group_name,
          CAST(NULL AS STRING) AS ad_id,
          CAST(NULL AS STRING) AS ad_type,
          CAST(NULL AS STRING) AS ad_title,
          CAST(NULL AS STRING) AS title_part_1,
          CAST(NULL AS STRING) AS title_part_2,
          CAST(NULL AS STRING) AS title_part_3,
          CAST(NULL AS STRING) AS description_1,
          CAST(NULL AS STRING) AS description_2,
          CAST(NULL AS STRING) AS headlines,
          CAST(NULL AS STRING) AS descriptions,
          CAST(NULL AS STRING) AS path_1,
          CAST(NULL AS STRING) AS path_2,
          CAST(NULL AS STRING) AS display_url,
          CAST(NULL AS STRING) AS final_url,
          spend, impressions, clicks, conversions, conversion_value
        FROM `{campaign_table_id}`"""
        source_table = campaign_table_id
    else:
        return {"status": "pending_data", "table": view_id, "raw_table": campaign_table_id}

    _replace_object_with_view(client, view_id, select_sql)
    return {"status": "success", "table": view_id, "raw_table": source_table}


def rebuild_unified_marketing_mart(client_key: str | None = None) -> dict[str, Any]:
    """Build the dashboard's single daily fact from available normalized facts.

    fact_marketing_daily carries client_key for multi-tenant filtering. Sources
    whose fact view exposes client_key (e.g. Google) are read directly; sources
    that don't yet (Meta/LinkedIn raw tables lack it) fall back to the run's
    client_key literal so the column is always present and filterable.
    """
    project_id = _route_value("project") or (
        os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or ""
    ).strip()
    if not project_id:
        return {"status": "failed", "error": "missing_project"}
    mart_dataset = _mart_dataset_id()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    candidates = {
        "google": os.getenv("BQ_MART_TABLE") or "fact_google_ads_campaign_daily",
        "meta": _DEFAULT_META_CAMPAIGN_FACT_TABLE,
        "linkedin": _DEFAULT_LINKEDIN_CAMPAIGN_FACT_TABLE,
    }
    ck_literal = (client_key or "").strip()
    selects: list[str] = []
    included: list[str] = []
    for source, table in candidates.items():
        source_id = f"{project_id}.{mart_dataset}.{table}"
        try:
            tbl = client.get_table(source_id)
        except Exception:
            continue
        has_ck = any(f.name == "client_key" for f in tbl.schema)
        if has_ck:
            ck_expr = "client_key"
        elif ck_literal:
            ck_expr = f"'{ck_literal}' AS client_key"
        else:
            ck_expr = "CAST(NULL AS STRING) AS client_key"
        included.append(source)
        selects.append(
            f"SELECT {ck_expr}, '{source}' AS source, account_id, date, campaign_id, "
            f"campaign_name, spend, impressions, clicks, conversions, "
            f"conversion_value FROM `{source_id}`"
        )
    if not selects:
        return {"status": "pending_data", "sources": [], "table": None}
    table_id = f"{project_id}.{mart_dataset}.fact_marketing_daily"
    client.query(
        f"CREATE OR REPLACE VIEW `{table_id}` AS\n" + "\nUNION ALL\n".join(selects)
    ).result()
    return {"status": "success", "sources": included, "table": table_id}


def _replace_object_with_view(client: Any, object_id: str, select_sql: str) -> None:
    """CREATE OR REPLACE a view, first dropping any pre-existing TABLE of the
    same name (BigQuery won't let CREATE OR REPLACE VIEW replace a table, and
    both vw_paid_media_daily and mart_health may already exist as tables — from
    a prior Dataform run, where mart_health is a table, or the unused
    client_bq_service.provision_mart_tables schema)."""
    try:
        existing = client.get_table(object_id)
        if str(getattr(existing, "table_type", "TABLE")).upper() == "TABLE":
            client.delete_table(object_id, not_found_ok=True)
    except Exception:
        pass
    client.query(f"CREATE OR REPLACE VIEW `{object_id}` AS\n{select_sql}").result()


def create_paid_media_mart_views(client_key: str | None = None) -> dict[str, Any]:
    """Build vw_paid_media_daily + mart_health from the raw Google/LinkedIn
    campaign_daily tables the connectors already write.

    This replaces the Dataform definitions of the same name so the dashboard
    Overview (summary cards, trend chart, data-health panel) populates from the
    app's own syncs — no separate per-client Dataform workspace required. The
    logic is a faithful port of sagefrog-dataform/definitions/marts/
    vw_paid_media_daily.sqlx + mart_health.sqlx (Google Ads + LinkedIn, source_
    platform paid_google/paid_linkedin).

    Dynamically includes whichever paid sources' raw campaign_daily tables exist,
    so a client with only Google (or only LinkedIn) connected still gets a
    working Overview. Returns pending_data if neither raw table exists yet.
    """
    project_id = _route_value("project") or (
        os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or ""
    ).strip()
    if not project_id:
        return {"status": "failed", "error": "missing_project"}
    mart_dataset = _mart_dataset_id()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)

    raw_table = "campaign_daily"
    # (raw dataset, paid-media source_platform label, mart_health source label).
    # All three write an identically-shaped campaign_daily (metric_date, spend,
    # impressions, clicks, conversions, conversion_value) at campaign-per-day
    # grain, so UNION ALL gives correct cross-platform totals with no double
    # counting. Meta uses _meta_dataset_id() (defaults to raw_meta_ads) — note
    # _dataset_id("meta") has no default and would return "".
    sources = [
        (_dataset_id("google"), "paid_google", "google"),
        (_dataset_id("linkedin"), "paid_linkedin", "linkedin"),
        (_meta_dataset_id(), "paid_meta", "meta"),
        (_dataset_id("microsoft"), "paid_microsoft", "microsoft"),
    ]

    paid_selects: list[str] = []
    health_selects: list[str] = []
    included: list[str] = []
    for raw_dataset, platform_label, health_label in sources:
        raw_id = f"{project_id}.{raw_dataset}.{raw_table}"
        try:
            client.get_table(raw_id)
        except Exception:
            continue
        included.append(health_label)
        paid_selects.append(
            f"""SELECT
              metric_date                          AS date,
              '{platform_label}'                   AS source_platform,
              CAST(spend AS FLOAT64)               AS spend,
              CAST(impressions AS INT64)           AS impressions,
              CAST(clicks AS INT64)                AS clicks,
              CAST(conversions AS FLOAT64)         AS conversions,
              CAST(conversion_value AS FLOAT64)    AS conversion_value
            FROM `{raw_id}`"""
        )
        health_selects.append(
            f"""SELECT
              '{health_label}'                     AS source,
              COUNT(*)                             AS row_count,
              MIN(metric_date)                     AS earliest_date,
              MAX(metric_date)                     AS latest_date,
              ROUND(SUM(spend), 2)                 AS spend,
              SUM(impressions)                     AS impressions,
              SUM(clicks)                          AS clicks,
              SUM(conversions)                     AS conversions
            FROM `{raw_id}`"""
        )

    if not paid_selects:
        return {"status": "pending_data", "sources": [], "views": []}

    paid_view_id = f"{project_id}.{mart_dataset}.vw_paid_media_daily"
    health_view_id = f"{project_id}.{mart_dataset}.mart_health"
    _replace_object_with_view(client, paid_view_id, "\nUNION ALL\n".join(paid_selects))
    _replace_object_with_view(client, health_view_id, "\nUNION ALL\n".join(health_selects))
    return {
        "status": "success",
        "sources": included,
        "views": [paid_view_id, health_view_id],
    }


def mirror_linkedin_campaign_metadata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert LinkedIn campaign metadata rows into linkedin_ads.campaigns."""
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    client, table_id = _campaigns_table_id()
    ensure_linkedin_campaigns_table()
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        account_id = str(row.get("account_id") or "").strip().split(":")[-1]
        campaign_id = str(row.get("campaign_id") or "").strip()
        if not account_id or not campaign_id:
            continue
        payload.append({
            "source": str(row.get("source") or "linkedin"),
            "account_id": account_id,
            "campaign_id": campaign_id,
            "campaign_name": str(row.get("campaign_name") or ""),
            "campaign_status": str(row.get("campaign_status") or ""),
            "campaign_group_id": str(row.get("campaign_group_id") or ""),
            "campaign_group_name": str(row.get("campaign_group_name") or ""),
            "last_synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    bigquery = _bigquery()
    job_config = bigquery.LoadJobConfig(
        schema=_campaign_metadata_schema(),
        write_disposition="WRITE_TRUNCATE",
    )
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id AND T.campaign_id = S.campaign_id
    WHEN MATCHED THEN UPDATE SET
      campaign_name = S.campaign_name,
      campaign_status = S.campaign_status,
      campaign_group_id = S.campaign_group_id,
      campaign_group_name = S.campaign_group_name,
      last_synced_at = S.last_synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, campaign_id, campaign_name, campaign_status,
      campaign_group_id, campaign_group_name, last_synced_at
    ) VALUES (
      S.source, S.account_id, S.campaign_id, S.campaign_name, S.campaign_status,
      S.campaign_group_id, S.campaign_group_name, S.last_synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def _bigquery_table_has_column(
    client: Any,
    *,
    project_id: str,
    dataset_id: str,
    table_name: str,
    column_name: str,
) -> bool:
    sql = f"""
    SELECT 1 AS found
    FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = @table_name
      AND column_name = @column_name
    LIMIT 1
    """
    bigquery = _bigquery()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("table_name", "STRING", table_name),
            bigquery.ScalarQueryParameter("column_name", "STRING", column_name),
        ]
    )
    try:
        return bool(list(client.query(sql, job_config=job_config).result(max_results=1)))
    except Exception:
        return True


def rebuild_linkedin_campaign_daily_mart() -> dict[str, Any]:
    """Rebuild marketing_marts.fact_linkedin_ads_campaign_daily with campaign metadata."""
    if not enabled("linkedin"):
        return {"enabled": False, "table": None}
    project_id = _linkedin_project_id()
    raw_dataset = _dataset_id("linkedin")
    mart_dataset = _mart_dataset_id()
    mart_table = (
        os.getenv("BQ_LINKEDIN_CAMPAIGN_FACT_TABLE")
        or _DEFAULT_LINKEDIN_CAMPAIGN_FACT_TABLE
    ).strip()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    table_id = f"{project_id}.{mart_dataset}.{mart_table}"
    reach_expr = (
        "IFNULL(cd.reach, 0)"
        if _bigquery_table_has_column(
            client,
            project_id=project_id,
            dataset_id=raw_dataset,
            table_name=_DEFAULT_CAMPAIGN_TABLE,
            column_name="reach",
        )
        else "0"
    )
    # Built as a VIEW (always live off the raw mirror) to match the Google fact
    # (create_google_campaign_mart_view) and the unified mart. metric_date is
    # aliased to `date` because every consumer — campaign_daily_sql,
    # bq_mart_service.fetch_linkedin_campaign_daily, and rebuild_unified_marketing_mart
    # — reads `date`. Emitting raw `metric_date` here silently broke those reads.
    sql = f"""
    CREATE OR REPLACE VIEW `{table_id}` AS
    SELECT
      cd.source,
      CAST(cd.account_id AS STRING) AS account_id,
      CAST(cd.campaign_id AS STRING) AS campaign_id,
      COALESCE(
        NULLIF(TRIM(CAST(cd.campaign_name AS STRING)), ''),
        NULLIF(TRIM(CAST(c.campaign_name AS STRING)), ''),
        CAST(cd.campaign_id AS STRING)
      ) AS campaign_name,
      CAST(c.campaign_status AS STRING) AS campaign_status,
      CAST(c.campaign_group_id AS STRING) AS campaign_group_id,
      CAST(c.campaign_group_name AS STRING) AS campaign_group_name,
      cd.metric_date AS date,
      cd.spend,
      cd.clicks,
      cd.impressions,
      cd.conversions,
      cd.conversion_value,
      {reach_expr} AS reach,
      cd.synced_at
    FROM `{project_id}.{raw_dataset}.campaign_daily` cd
    LEFT JOIN `{project_id}.{raw_dataset}.campaigns` c
      ON CAST(cd.account_id AS STRING) = CAST(c.account_id AS STRING)
     AND CAST(cd.campaign_id AS STRING) = CAST(c.campaign_id AS STRING)
    """
    # CREATE OR REPLACE VIEW cannot replace a pre-existing TABLE of the same name;
    # drop a stale materialized fact (from the old table-based rebuild) first.
    try:
        existing = client.get_table(table_id)
        if str(getattr(existing, "table_type", "")).upper() == "TABLE":
            client.delete_table(table_id, not_found_ok=True)
    except Exception:
        pass
    client.query(sql).result()
    return {"enabled": True, "table": table_id, "object_type": "view"}


def _ad_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("creative_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("spend", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _creative_metadata_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("creative_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("creative_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("status", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("media_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("thumbnail_url", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("image_url", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("video_url", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("last_synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _ad_daily_table_id() -> tuple[Any, str]:
    client, base_table = _target("linkedin")
    return client, base_table.rsplit(".", 1)[0] + "." + _DEFAULT_AD_TABLE


def _creative_metadata_table_id() -> tuple[Any, str]:
    client, base_table = _target("linkedin")
    return client, base_table.rsplit(".", 1)[0] + "." + _DEFAULT_CREATIVE_METADATA_TABLE


def mirror_linkedin_ad_daily_batch(
    account_id: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Mirror per-creative daily metric rows to linkedin_ads.ad_daily."""
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    account_id_clean = str(account_id).strip().split(":")[-1]
    client, table_id = _ad_daily_table_id()
    bigquery = _bigquery()
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    schema = _ad_daily_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    t.clustering_fields = ["source", "account_id", "creative_id"]
    client.create_table(t, exists_ok=True)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        crid = str(row.get("creative_id") or "").strip()
        metric_date = str(row.get("metric_date") or "")[:10]
        if not crid or not metric_date:
            continue
        payload.append({
            "source": "linkedin",
            "account_id": account_id_clean,
            "creative_id": crid,
            "metric_date": metric_date,
            "spend": float(row.get("spend") or 0),
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id
      AND T.creative_id = S.creative_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      spend = S.spend, clicks = S.clicks, impressions = S.impressions,
      conversions = S.conversions, conversion_value = S.conversion_value,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, creative_id, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.source, S.account_id, S.creative_id, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_linkedin_creative_metadata(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert LinkedIn creative metadata (thumbnails, media_type, etc.) to linkedin_ads.creative_metadata."""
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    client, table_id = _creative_metadata_table_id()
    bigquery = _bigquery()
    dataset_ref = ".".join(table_id.split(".")[:2])
    client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
    schema = _creative_metadata_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.clustering_fields = ["source", "account_id", "creative_id"]
    client.create_table(t, exists_ok=True)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        account_id = str(row.get("account_id") or "").strip().split(":")[-1]
        crid = str(row.get("creative_id") or row.get("id") or "").strip()
        if not crid:
            continue
        payload.append({
            "source": "linkedin",
            "account_id": account_id,
            "creative_id": crid,
            "campaign_id": str(row.get("campaign_id") or "").strip() or None,
            "creative_name": str(row.get("creative_name") or row.get("name") or "").strip() or None,
            "status": str(row.get("status") or "").strip() or None,
            "media_type": str(row.get("media_type") or "").strip() or None,
            "thumbnail_url": str(row.get("thumbnail_url") or "").strip() or None,
            "image_url": str(row.get("image_url") or "").strip() or None,
            "video_url": str(row.get("video_url") or "").strip() or None,
            "last_synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id AND T.creative_id = S.creative_id
    WHEN MATCHED THEN UPDATE SET
      campaign_id = S.campaign_id,
      creative_name = S.creative_name,
      status = S.status,
      media_type = S.media_type,
      thumbnail_url = S.thumbnail_url,
      image_url = S.image_url,
      video_url = S.video_url,
      last_synced_at = S.last_synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, creative_id, campaign_id, creative_name,
      status, media_type, thumbnail_url, image_url, video_url, last_synced_at
    ) VALUES (
      S.source, S.account_id, S.creative_id, S.campaign_id, S.creative_name,
      S.status, S.media_type, S.thumbnail_url, S.image_url, S.video_url, S.last_synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


_DEFAULT_META_DATASET = "raw_meta_ads"
_DEFAULT_META_CAMPAIGN_TABLE = "campaign_daily"
_DEFAULT_META_ADSET_TABLE = "adset_daily"
_DEFAULT_META_AD_TABLE = "ad_daily"
_DEFAULT_META_AD_CREATIVE_TABLE = "ad_creative"
_DEFAULT_META_AD_CONVERSION_ACTION_TABLE = "ad_conversion_action_daily"
_DEFAULT_META_CAMPAIGN_FACT_TABLE = "fact_meta_ads_campaign_daily"
_DEFAULT_META_ADSET_FACT_TABLE = "fact_meta_ads_adset_daily"
_DEFAULT_META_AD_FACT_TABLE = "fact_meta_ads_ad_daily"


def _meta_project_id() -> str:
    project = (_route_value("project") or os.getenv("BQ_META_PROJECT_ID") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for Meta reads/writes. Call bigquery_warehouse.route("
            "bq_project_id=...) for this client, or set BQ_META_PROJECT_ID for a single-tenant "
            "deployment. Refusing to silently fall back to another client's project."
        )
    return project


def _meta_dataset_id() -> str:
    return (
        _route_value("meta_dataset")
        or os.getenv("BQ_META_DATASET_ID")
        or _DEFAULT_META_DATASET
    ).strip()


def _meta_client():
    return _client(_meta_project_id())


def _meta_adset_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("client_key", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("adset_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("adset_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("spend", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _meta_ad_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("client_key", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ad_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ad_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("adset_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("adset_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("spend", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _meta_ad_conversion_action_schema() -> list[Any]:
    """Per-result-token daily conversions for one Meta ad.

    No spend/clicks/impressions: a row scoped to one action has no share of the
    ad's cost. ``is_result`` marks the tokens that make up the ad's headline
    ``conversions`` (its ad set's optimization goal) — the rest are the other
    events the same insights call reported.
    """
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("client_key", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ad_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("adset_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("action_token", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("action_label", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("is_result", "BOOL", mode="NULLABLE"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _meta_campaign_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("client_key", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("spend", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("conversion_value", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _ensure_meta_table(table_name: str, schema: list[Any]) -> str:
    bigquery = _bigquery()
    client = _meta_client()
    project_id = _meta_project_id()
    dataset_id = _meta_dataset_id()
    table_id = f"{project_id}.{dataset_id}.{table_name}"
    client.create_dataset(bigquery.Dataset(f"{project_id}.{dataset_id}"), exists_ok=True)
    field_names = {f.name for f in schema}

    # Add any columns the live table lacks (e.g. client_key) without dropping
    # data — create_table(exists_ok) alone never alters an existing schema.
    try:
        existing = client.get_table(table_id)
        existing_cols = {f.name for f in existing.schema}
        if not field_names.issubset(existing_cols):
            obj = bigquery.Table(table_id, schema=schema)
            client.update_table(obj, ["schema"])
    except Exception:
        pass  # Table doesn't exist yet — create below.

    table = bigquery.Table(table_id, schema=schema)
    if "metric_date" in field_names:
        table.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    table.clustering_fields = ["account_id"]
    client.create_table(table, exists_ok=True)
    return table_id


def mirror_meta_campaign_daily_batch(account_id: str, rows: list[dict[str, Any]], *, client_key: str = "") -> dict[str, Any]:
    """Upsert per-campaign daily Meta metrics to meta_data.campaign_daily."""
    if not rows:
        return {"enabled": True, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().lstrip("act_").split(":")[-1]
    table_id = _ensure_meta_table(_DEFAULT_META_CAMPAIGN_TABLE, _meta_campaign_daily_schema())
    client = _meta_client()
    bigquery = _bigquery()
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        cid = str(row.get("campaign_id") or "").strip()
        md = str(row.get("metric_date") or "")[:10]
        if not cid or not md:
            continue
        payload.append({
            "client_key": client_key or None,
            "account_id": account_id_clean,
            "campaign_id": cid,
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": md,
            "spend": float(row.get("spend") or 0),
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}
    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=_meta_campaign_daily_schema(), write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.account_id = S.account_id AND T.campaign_id = S.campaign_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      client_key = S.client_key,
      campaign_name = S.campaign_name, spend = S.spend, clicks = S.clicks,
      impressions = S.impressions, conversions = S.conversions,
      conversion_value = S.conversion_value, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      client_key, account_id, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.client_key, S.account_id, S.campaign_id, S.campaign_name, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_meta_adset_daily_batch(account_id: str, rows: list[dict[str, Any]], *, client_key: str = "") -> dict[str, Any]:
    """Upsert per-adset daily Meta metrics to meta_data.adset_daily."""
    if not rows:
        return {"enabled": True, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().lstrip("act_").split(":")[-1]
    table_id = _ensure_meta_table(_DEFAULT_META_ADSET_TABLE, _meta_adset_daily_schema())
    client = _meta_client()
    bigquery = _bigquery()
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        asid = str(row.get("adset_id") or "").strip()
        md = str(row.get("metric_date") or "")[:10]
        if not asid or not md:
            continue
        payload.append({
            "client_key": client_key or None,
            "account_id": account_id_clean,
            "adset_id": asid,
            "adset_name": str(row.get("adset_name") or ""),
            "campaign_id": str(row.get("campaign_id") or "").strip(),
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": md,
            "spend": float(row.get("spend") or 0),
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}
    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=_meta_adset_daily_schema(), write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.account_id = S.account_id AND T.adset_id = S.adset_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      client_key = S.client_key,
      adset_name = S.adset_name, campaign_id = S.campaign_id, campaign_name = S.campaign_name,
      spend = S.spend, clicks = S.clicks, impressions = S.impressions,
      conversions = S.conversions, conversion_value = S.conversion_value, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      client_key, account_id, adset_id, adset_name, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.client_key, S.account_id, S.adset_id, S.adset_name, S.campaign_id, S.campaign_name, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_meta_ad_daily_batch(account_id: str, rows: list[dict[str, Any]], *, client_key: str = "") -> dict[str, Any]:
    """Upsert per-ad daily Meta metrics to meta_data.ad_daily."""
    if not rows:
        return {"enabled": True, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().lstrip("act_").split(":")[-1]
    table_id = _ensure_meta_table(_DEFAULT_META_AD_TABLE, _meta_ad_daily_schema())
    client = _meta_client()
    bigquery = _bigquery()
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        aid = str(row.get("ad_id") or "").strip()
        md = str(row.get("metric_date") or "")[:10]
        if not aid or not md:
            continue
        payload.append({
            "client_key": client_key or None,
            "account_id": account_id_clean,
            "ad_id": aid,
            "ad_name": str(row.get("ad_name") or ""),
            "adset_id": str(row.get("adset_id") or "").strip(),
            "adset_name": str(row.get("adset_name") or ""),
            "campaign_id": str(row.get("campaign_id") or "").strip(),
            "campaign_name": str(row.get("campaign_name") or ""),
            "metric_date": md,
            "spend": float(row.get("spend") or 0),
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}
    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=_meta_ad_daily_schema(), write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.account_id = S.account_id AND T.ad_id = S.ad_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      client_key = S.client_key,
      ad_name = S.ad_name, adset_id = S.adset_id, adset_name = S.adset_name,
      campaign_id = S.campaign_id, campaign_name = S.campaign_name,
      spend = S.spend, clicks = S.clicks, impressions = S.impressions,
      conversions = S.conversions, conversion_value = S.conversion_value, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      client_key, account_id, ad_id, ad_name, adset_id, adset_name, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.client_key, S.account_id, S.ad_id, S.ad_name, S.adset_id, S.adset_name, S.campaign_id, S.campaign_name, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def _meta_ad_creative_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("client_key", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ad_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ad_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("adset_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("adset_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("thumbnail_url", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("image_url", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("media_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def mirror_meta_ad_creative_batch(account_id: str, rows: list[dict[str, Any]], *, client_key: str = "") -> dict[str, Any]:
    """Upsert Meta ad creative metadata (thumbnails) to meta_data.ad_creative."""
    if not rows:
        return {"enabled": True, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().lstrip("act_").split(":")[-1]
    table_id = _ensure_meta_table(_DEFAULT_META_AD_CREATIVE_TABLE, _meta_ad_creative_schema())
    client = _meta_client()
    bigquery = _bigquery()
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        aid = str(row.get("ad_id") or "").strip()
        if not aid:
            continue
        payload.append({
            "client_key": client_key or None,
            "account_id": account_id_clean,
            "ad_id": aid,
            "ad_name": str(row.get("ad_name") or ""),
            "adset_id": str(row.get("adset_id") or "").strip(),
            "adset_name": str(row.get("adset_name") or ""),
            "campaign_id": str(row.get("campaign_id") or "").strip(),
            "campaign_name": str(row.get("campaign_name") or ""),
            "thumbnail_url": str(row.get("thumbnail_url") or ""),
            "image_url": str(row.get("image_url") or ""),
            "media_type": str(row.get("media_type") or ""),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}
    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=_meta_ad_creative_schema(), write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.account_id = S.account_id AND T.ad_id = S.ad_id
    WHEN MATCHED THEN UPDATE SET
      client_key = S.client_key,
      ad_name = S.ad_name, adset_id = S.adset_id, adset_name = S.adset_name,
      campaign_id = S.campaign_id, campaign_name = S.campaign_name,
      thumbnail_url = S.thumbnail_url, image_url = S.image_url,
      media_type = S.media_type, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      client_key, account_id, ad_id, ad_name, adset_id, adset_name,
      campaign_id, campaign_name, thumbnail_url, image_url, media_type, synced_at
    ) VALUES (
      S.client_key, S.account_id, S.ad_id, S.ad_name, S.adset_id, S.adset_name,
      S.campaign_id, S.campaign_name, S.thumbnail_url, S.image_url, S.media_type, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def meta_ad_creative_coverage(account_id: str) -> dict[str, Any]:
    """When each of an ad account's ads last had its creative metadata synced.

    Returns ``{"available": bool, "synced_at": {ad_id: datetime}}``. The creative
    fetch is the most expensive call in the Meta sync -- it is the one that trips
    the ad account's Ads API call limit -- so callers use this to skip it when the
    warehouse already holds fresh creatives for every ad in the window.

    Fails open with ``available=False`` and empty coverage: an unreadable table
    must mean "go fetch", never "skip". Deliberately does not call
    ``_ensure_meta_table`` -- a read path should not create or alter a table.
    """
    empty: dict[str, Any] = {"available": False, "synced_at": {}}
    account_id_clean = str(account_id).strip().lstrip("act_").split(":")[-1]
    if not account_id_clean:
        return empty
    try:
        project_id = _meta_project_id()
        dataset_id = _meta_dataset_id()
        client = _meta_client()
        bigquery = _bigquery()
    except Exception:
        return empty
    sql = f"""
    SELECT ad_id, MAX(synced_at) AS synced_at
    FROM `{project_id}.{dataset_id}.{_DEFAULT_META_AD_CREATIVE_TABLE}`
    WHERE account_id = @account_id
    GROUP BY ad_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("account_id", "STRING", account_id_clean)
        ]
    )
    try:
        rows = list(client.query(sql, job_config=job_config).result())
    except Exception:
        return empty
    synced_at: dict[str, Any] = {}
    for row in rows:
        aid = str(row.get("ad_id") or "").strip() if hasattr(row, "get") else ""
        if aid and row.get("synced_at") is not None:
            synced_at[aid] = row.get("synced_at")
    return {"available": True, "synced_at": synced_at}


def mirror_meta_ad_conversion_action_batch(
    account_id: str, rows: list[dict[str, Any]], *, client_key: str = ""
) -> dict[str, Any]:
    """Replace the per-action breakdown for the days this batch covers.

    DELETE-then-INSERT rather than MERGE, scoped to (account, day): an ad that
    stopped recording an action would keep a stale row forever under a MERGE,
    because the key simply stops arriving instead of arriving as a zero.
    """
    if not rows:
        return {"enabled": True, "rows_upserted": 0, "table": None}
    account_id_clean = str(account_id).strip().lstrip("act_").split(":")[-1]
    table_id = _ensure_meta_table(
        _DEFAULT_META_AD_CONVERSION_ACTION_TABLE, _meta_ad_conversion_action_schema()
    )
    client = _meta_client()
    bigquery = _bigquery()
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    days: set[str] = set()
    for row in rows:
        aid = str(row.get("ad_id") or "").strip()
        token = str(row.get("action_token") or "").strip()
        md = str(row.get("metric_date") or "")[:10]
        if not aid or not token or not md:
            continue
        days.add(md)
        payload.append({
            "client_key": client_key or None,
            "account_id": account_id_clean,
            "ad_id": aid,
            "adset_id": str(row.get("adset_id") or "").strip() or None,
            "campaign_id": str(row.get("campaign_id") or "").strip() or None,
            "action_token": token,
            "action_label": str(row.get("action_label") or token),
            "is_result": bool(row.get("is_result")),
            "metric_date": md,
            "conversions": float(row.get("conversions") or 0),
            "conversion_value": float(row.get("conversion_value") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE account_id = '{account_id_clean}' "
        f"  AND metric_date BETWEEN '{min(days)}' AND '{max(days)}'"
    ).result()
    job_config = bigquery.LoadJobConfig(
        schema=_meta_ad_conversion_action_schema(), write_disposition="WRITE_APPEND"
    )
    client.load_table_from_json(payload, table_id, job_config=job_config).result()
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def ensure_meta_tables() -> None:
    """Create all Meta raw tables if they don't exist. Call before create_meta_mart_views."""
    _ensure_meta_table(_DEFAULT_META_CAMPAIGN_TABLE, _meta_campaign_daily_schema())
    _ensure_meta_table(_DEFAULT_META_ADSET_TABLE, _meta_adset_daily_schema())
    _ensure_meta_table(_DEFAULT_META_AD_TABLE, _meta_ad_daily_schema())
    _ensure_meta_table(_DEFAULT_META_AD_CREATIVE_TABLE, _meta_ad_creative_schema())
    _ensure_meta_table(
        _DEFAULT_META_AD_CONVERSION_ACTION_TABLE, _meta_ad_conversion_action_schema()
    )


def create_meta_mart_views() -> dict[str, Any]:
    """Create or replace Meta mart views in marketing_marts (ad view joins creative metadata)."""
    project_id = _meta_project_id()
    dataset_id = _meta_dataset_id()
    mart_dataset = _mart_dataset_id()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)

    views_created = []

    # Campaign and adset views â€” simple passthroughs
    for fact_table, raw_table, id_col, name_col, parent_cols in [
        (
            _DEFAULT_META_CAMPAIGN_FACT_TABLE,
            _DEFAULT_META_CAMPAIGN_TABLE,
            "campaign_id",
            "campaign_name",
            "",
        ),
        (
            _DEFAULT_META_ADSET_FACT_TABLE,
            _DEFAULT_META_ADSET_TABLE,
            "adset_id",
            "adset_name",
            "campaign_id, campaign_name,",
        ),
    ]:
        table_id = f"{project_id}.{mart_dataset}.{fact_table}"
        sql = f"""
        CREATE OR REPLACE VIEW `{table_id}` AS
        SELECT
          client_key,
          account_id,
          {parent_cols}
          {id_col},
          {name_col},
          metric_date AS date,
          spend,
          impressions,
          clicks,
          conversions,
          conversion_value
        FROM `{project_id}.{dataset_id}.{raw_table}`
        """
        client.query(sql).result()
        views_created.append(table_id)

    # Ad view â€” LEFT JOIN creative metadata for thumbnails
    ad_fact_id = f"{project_id}.{mart_dataset}.{_DEFAULT_META_AD_FACT_TABLE}"
    ad_sql = f"""
    CREATE OR REPLACE VIEW `{ad_fact_id}` AS
    SELECT
      ad.client_key,
      ad.account_id,
      ad.adset_id,
      ad.adset_name,
      ad.campaign_id,
      ad.campaign_name,
      ad.ad_id,
      ad.ad_name,
      ad.metric_date AS date,
      ad.spend,
      ad.impressions,
      ad.clicks,
      ad.conversions,
      ad.conversion_value,
      cr.thumbnail_url,
      cr.image_url,
      cr.media_type
    FROM `{project_id}.{dataset_id}.{_DEFAULT_META_AD_TABLE}` ad
    LEFT JOIN `{project_id}.{dataset_id}.{_DEFAULT_META_AD_CREATIVE_TABLE}` cr
      ON ad.account_id = cr.account_id AND ad.ad_id = cr.ad_id
    """
    client.query(ad_sql).result()
    views_created.append(ad_fact_id)

    # Per-action breakdown the explorer's Conv. selector reads. Same ad grain as
    # the ad view above, so the UI matches it on ad_id and rolls it up the tree.
    conv_view_id = f"{project_id}.{mart_dataset}.explorer_meta_conversion_action_daily"
    conv_sql = f"""
    CREATE OR REPLACE VIEW `{conv_view_id}` AS
    SELECT
      client_key,
      account_id,
      campaign_id,
      adset_id,
      ad_id,
      action_token,
      action_label,
      is_result,
      metric_date AS date,
      conversions,
      conversion_value
    FROM `{project_id}.{dataset_id}.{_DEFAULT_META_AD_CONVERSION_ACTION_TABLE}`
    """
    client.query(conv_sql).result()
    views_created.append(conv_view_id)

    return {"enabled": True, "views": views_created}


def create_linkedin_creative_mart_view() -> dict[str, Any]:
    """Create or replace marketing_marts.fact_linkedin_ads_creative_daily view."""
    if not enabled("linkedin"):
        return {"enabled": False, "table": None}
    project_id = _linkedin_project_id()
    raw_dataset = _dataset_id("linkedin")
    mart_dataset = _mart_dataset_id()
    mart_table = _DEFAULT_LINKEDIN_CREATIVE_FACT_TABLE
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    table_id = f"{project_id}.{mart_dataset}.{mart_table}"
    sql = f"""
    CREATE OR REPLACE VIEW `{table_id}` AS
    SELECT
      'penn' AS client_key,
      ad.metric_date AS date,
      ad.source AS source_platform,
      CAST(ad.account_id AS STRING) AS source_account_id,
      CAST(ad.creative_id AS STRING) AS creative_id,
      COALESCE(
        NULLIF(TRIM(CAST(cm.creative_name AS STRING)), ''),
        CAST(ad.creative_id AS STRING)
      ) AS creative_name,
      CAST(cm.campaign_id AS STRING) AS campaign_id,
      CAST(camp.campaign_name AS STRING) AS campaign_name,
      CAST(camp.campaign_group_id AS STRING) AS campaign_group_id,
      CAST(camp.campaign_group_name AS STRING) AS campaign_group_name,
      CAST(cm.status AS STRING) AS status,
      CAST(cm.media_type AS STRING) AS media_type,
      CAST(cm.thumbnail_url AS STRING) AS thumbnail_url,
      CAST(cm.image_url AS STRING) AS image_url,
      CAST(cm.video_url AS STRING) AS video_url,
      SUM(ad.spend) AS spend,
      SUM(ad.clicks) AS clicks,
      SUM(ad.impressions) AS impressions,
      SUM(ad.conversions) AS conversions,
      SUM(ad.conversion_value) AS conversion_value,
      CURRENT_TIMESTAMP() AS updated_at
    FROM `{project_id}.{raw_dataset}.{_DEFAULT_AD_TABLE}` ad
    LEFT JOIN `{project_id}.{raw_dataset}.{_DEFAULT_CREATIVE_METADATA_TABLE}` cm
      ON CAST(ad.creative_id AS STRING) = CAST(cm.creative_id AS STRING)
    LEFT JOIN `{project_id}.{raw_dataset}.{_DEFAULT_CAMPAIGNS_TABLE}` camp
      ON CAST(cm.campaign_id AS STRING) = CAST(camp.campaign_id AS STRING)
    GROUP BY
      client_key, date, source_platform, source_account_id,
      creative_id, creative_name, campaign_id, campaign_name,
      campaign_group_id, campaign_group_name, status, media_type,
      thumbnail_url, image_url, video_url
    """
    client.query(sql).result()
    return {"enabled": True, "table": table_id}


def _linkedin_demographics_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        # The window this row totals over. Demographics have no date dimension
        # (see linkedin_service.fetch_ads_demographics), so the window replaces
        # the `date` column every other ads table here carries: a row is a total
        # for exactly this window, and windows are never summed together.
        bigquery.SchemaField("window_key", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("window_start", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("window_end", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("dimension", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("category", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("category_urn", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        # NULLABLE on purpose: LinkedIn refuses these projections for some
        # pivots/versions, and NULL renders as "—" where 0 would read as a real
        # measured zero.
        bigquery.SchemaField("spend", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("conversions", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _linkedin_demographics_table_id() -> tuple[Any, str]:
    client, base_table = _target("linkedin")
    return client, base_table.rsplit(".", 1)[0] + "." + _DEFAULT_LINKEDIN_DEMOGRAPHICS_TABLE


def mirror_linkedin_ads_demographics(
    account_id: str, window_key: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Replace one account's member demographics for one window.

    Grain is (source, account_id, window_key, dimension, category_urn). Each sync
    carries a complete snapshot for that window, so categories that dropped out
    of the window are deleted rather than left behind as stale rows — the same
    replace-per-scope shape as mirror_linkedin_follower_demographics.

    The delete is scoped to this account *and* this window: syncing LAST_30_DAYS
    must not touch the LAST_90_DAYS rows sitting in the same table.
    """
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    account_id_clean = str(account_id).strip().split(":")[-1]
    window_clean = str(window_key or "").strip()
    if not window_clean:
        return {"enabled": False, "rows_upserted": 0, "table": None, "reason": "missing_window"}

    client, table_id = _linkedin_demographics_table_id()
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(".".join(table_id.split(".")[:2])), exists_ok=True)
    schema = _linkedin_demographics_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.clustering_fields = ["source", "account_id", "window_key", "dimension"]
    client.create_table(t, exists_ok=True)
    _sync_table_columns(client, table_id, schema)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        category_urn = str(row.get("category_urn") or "").strip()
        dimension = str(row.get("dimension") or "").strip()
        window_start = row.get("window_start")
        window_end = row.get("window_end")
        if not category_urn or not dimension or not window_start or not window_end:
            continue
        spend = row.get("spend")
        conversions = row.get("conversions")
        payload.append({
            "source": "linkedin",
            "account_id": account_id_clean,
            "window_key": window_clean,
            "window_start": str(window_start),
            "window_end": str(window_end),
            "dimension": dimension,
            "category": str(row.get("category") or "").strip() or None,
            "category_urn": category_urn,
            "impressions": int(row.get("impressions") or 0),
            "clicks": int(row.get("clicks") or 0),
            "spend": None if spend is None else float(spend),
            "conversions": None if conversions is None else float(conversions),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.account_id = S.account_id
       AND T.window_key = S.window_key
       AND T.dimension = S.dimension AND T.category_urn = S.category_urn
    WHEN MATCHED THEN UPDATE SET
      window_start = S.window_start, window_end = S.window_end,
      category = S.category, impressions = S.impressions, clicks = S.clicks,
      spend = S.spend, conversions = S.conversions, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, window_key, window_start, window_end,
      dimension, category, category_urn, impressions, clicks,
      spend, conversions, synced_at
    ) VALUES (
      S.source, S.account_id, S.window_key, S.window_start, S.window_end,
      S.dimension, S.category, S.category_urn, S.impressions, S.clicks,
      S.spend, S.conversions, S.synced_at
    )
    WHEN NOT MATCHED BY SOURCE AND T.source = 'linkedin'
      AND T.account_id = '{account_id_clean}' AND T.window_key = '{window_clean}'
      THEN DELETE
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def create_linkedin_demographics_mart_view() -> dict[str, Any]:
    """Create or replace marketing_marts.fact_linkedin_ads_demographics.

    A thin pass-through over the raw table (there is nothing to join — a
    demographic row carries its own label), so the dashboard read path resolves
    the same marts dataset as every other panel instead of reaching into
    raw_linkedin_ads.
    """
    if not enabled("linkedin"):
        return {"enabled": False, "table": None}
    project_id = _linkedin_project_id()
    raw_dataset = _dataset_id("linkedin")
    mart_dataset = _mart_dataset_id()
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    table_id = f"{project_id}.{mart_dataset}.{_DEFAULT_LINKEDIN_DEMOGRAPHICS_FACT_TABLE}"
    sql = f"""
    CREATE OR REPLACE VIEW `{table_id}` AS
    SELECT
      source AS source_platform,
      CAST(account_id AS STRING) AS source_account_id,
      window_key,
      window_start,
      window_end,
      dimension,
      category,
      category_urn,
      impressions,
      clicks,
      spend,
      conversions,
      SAFE_DIVIDE(clicks, impressions) AS ctr,
      synced_at
    FROM `{project_id}.{raw_dataset}.{_DEFAULT_LINKEDIN_DEMOGRAPHICS_TABLE}`
    """
    client.query(sql).result()
    return {"enabled": True, "table": table_id}


# ──────────────────────────────────────────────────────────────────────────────
# LinkedIn *organic* (company-page) mirrors — raw_linkedin_organic dataset
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_ORGANIC_POST_TABLE = "post_stats"
_DEFAULT_ORGANIC_FOLLOWER_TABLE = "follower_daily"
_DEFAULT_ORGANIC_PAGE_TABLE = "page_daily"
_DEFAULT_LINKEDIN_ORGANIC_POST_FACT_TABLE = "fact_linkedin_organic_post_stats"


def _organic_dataset_id() -> str:
    return (
        _route_value("linkedin_organic_dataset")
        or os.getenv("BQ_LINKEDIN_ORGANIC_DATASET_ID")
        or _DEFAULT_LINKEDIN_ORGANIC_DATASET
    ).strip()


def _organic_target(table_name: str) -> tuple[Any, str]:
    """Return (client, fully-qualified table id) in the organic dataset.

    Reuses the routed/env LinkedIn *project* (organic and ads share a project;
    only the dataset differs), so a client's organic writes land in the same
    BigQuery project as its ads data.
    """
    project_id = _linkedin_project_id()
    dataset_id = _organic_dataset_id()
    if not project_id or not dataset_id:
        raise RuntimeError("Missing BigQuery project/dataset for LinkedIn organic.")
    return _client(project_id), f"{project_id}.{dataset_id}.{table_name}"


def _sync_table_columns(client: Any, table_id: str, schema: list[Any]) -> None:
    """Add any NULLABLE columns an already-existing table is missing.

    ``create_table(exists_ok=True)`` never alters an existing table's schema, so a
    table created before a column was added would make the MERGE below fail on the
    missing column. This backfills the schema (nullable additions only, which is
    all BigQuery allows) so new metrics land on long-lived tables.
    """
    try:
        existing = client.get_table(table_id)
        existing_cols = {f.name for f in existing.schema}
        missing = [f for f in schema if f.name not in existing_cols]
        if missing:
            existing.schema = list(existing.schema) + missing
            client.update_table(existing, ["schema"])
    except Exception:
        _log.warning("organic schema sync skipped for %s", table_id, exc_info=True)


def _organic_post_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("org_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("post_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("post_urn", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("title", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("post_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("published_at", "DATE", mode="NULLABLE"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        # Reach (distinct viewers). NULLABLE so it can be added to tables created
        # before this column existed via the schema-sync in the mirror below.
        bigquery.SchemaField("unique_impressions", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("likes", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("comments", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("shares", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("engagement_rate", "FLOAT64", mode="REQUIRED"),
        # Per-reaction-type counts as a JSON object string (e.g.
        # {"LIKE": 30, "PRAISE": 4}); empty/absent when the breakdown is
        # unavailable for the post.
        bigquery.SchemaField("reactions_by_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _organic_follower_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("org_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("organic_follower_gain", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("paid_follower_gain", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("total_follower_gain", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("total_followers", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


_ORGANIC_PAGE_BREAKDOWN_COLS = (
    "desktop_page_views", "mobile_page_views", "overview_page_views",
    "careers_page_views", "jobs_page_views", "life_page_views",
    "products_page_views", "people_page_views",
)


def _organic_page_schema() -> list[Any]:
    bigquery = _bigquery()
    schema = [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("org_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("page_views", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("unique_visitors", "INT64", mode="REQUIRED"),
    ]
    # Device (desktop/mobile) + per-section view splits. NULLABLE so they can be
    # backfilled onto page_daily tables created before these columns existed.
    schema += [
        bigquery.SchemaField(col, "INT64", mode="NULLABLE")
        for col in _ORGANIC_PAGE_BREAKDOWN_COLS
    ]
    schema.append(bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"))
    return schema


def mirror_linkedin_post_stats(org_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert per-post organic stats to raw_linkedin_organic.post_stats.

    Grain is post (org_id, post_id); LinkedIn exposes per-post engagement as
    lifetime totals, so each sync refreshes the current totals.
    """
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    org_id_clean = str(org_id).strip().split(":")[-1]
    client, table_id = _organic_target(_DEFAULT_ORGANIC_POST_TABLE)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(".".join(table_id.split(".")[:2])), exists_ok=True)
    schema = _organic_post_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.clustering_fields = ["source", "org_id", "post_id"]
    client.create_table(t, exists_ok=True)
    _sync_table_columns(client, table_id, schema)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        post_id = str(row.get("post_id") or "").strip()
        if not post_id:
            continue
        reactions = row.get("reactions_by_type") or {}
        payload.append({
            "source": "linkedin",
            "org_id": org_id_clean,
            "post_id": post_id,
            "post_urn": str(row.get("post_urn") or "").strip() or None,
            "title": str(row.get("title") or "").strip() or None,
            "post_type": str(row.get("post_type") or "").strip() or None,
            "published_at": (str(row.get("published_at") or "")[:10] or None),
            "impressions": int(row.get("impressions") or 0),
            "unique_impressions": int(row.get("unique_impressions") or 0),
            "clicks": int(row.get("clicks") or 0),
            "likes": int(row.get("likes") or 0),
            "comments": int(row.get("comments") or 0),
            "shares": int(row.get("shares") or 0),
            "engagement_rate": float(row.get("engagement_rate") or 0.0),
            "reactions_by_type": (json.dumps(reactions, separators=(",", ":")) if reactions else None),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.org_id = S.org_id AND T.post_id = S.post_id
    WHEN MATCHED THEN UPDATE SET
      post_urn = S.post_urn, title = S.title, post_type = S.post_type,
      published_at = S.published_at, impressions = S.impressions,
      unique_impressions = S.unique_impressions, clicks = S.clicks,
      likes = S.likes, comments = S.comments, shares = S.shares,
      engagement_rate = S.engagement_rate, reactions_by_type = S.reactions_by_type,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, org_id, post_id, post_urn, title, post_type, published_at,
      impressions, unique_impressions, clicks, likes, comments, shares,
      engagement_rate, reactions_by_type, synced_at
    ) VALUES (
      S.source, S.org_id, S.post_id, S.post_urn, S.title, S.post_type, S.published_at,
      S.impressions, S.unique_impressions, S.clicks, S.likes, S.comments, S.shares,
      S.engagement_rate, S.reactions_by_type, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_linkedin_follower_daily(org_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert per-day follower gains to raw_linkedin_organic.follower_daily."""
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    org_id_clean = str(org_id).strip().split(":")[-1]
    client, table_id = _organic_target(_DEFAULT_ORGANIC_FOLLOWER_TABLE)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(".".join(table_id.split(".")[:2])), exists_ok=True)
    schema = _organic_follower_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    t.clustering_fields = ["source", "org_id"]
    client.create_table(t, exists_ok=True)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        metric_date = str(row.get("metric_date") or "")[:10]
        if not metric_date:
            continue
        payload.append({
            "source": "linkedin",
            "org_id": org_id_clean,
            "metric_date": metric_date,
            "organic_follower_gain": int(row.get("organic_follower_gain") or 0),
            "paid_follower_gain": int(row.get("paid_follower_gain") or 0),
            "total_follower_gain": int(row.get("total_follower_gain") or 0),
            "total_followers": int(row.get("total_followers") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.org_id = S.org_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      organic_follower_gain = S.organic_follower_gain,
      paid_follower_gain = S.paid_follower_gain,
      total_follower_gain = S.total_follower_gain,
      total_followers = S.total_followers,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, org_id, metric_date, organic_follower_gain, paid_follower_gain,
      total_follower_gain, total_followers, synced_at
    ) VALUES (
      S.source, S.org_id, S.metric_date, S.organic_follower_gain, S.paid_follower_gain,
      S.total_follower_gain, S.total_followers, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_linkedin_page_daily(org_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert per-day page views/visitors to raw_linkedin_organic.page_daily."""
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    org_id_clean = str(org_id).strip().split(":")[-1]
    client, table_id = _organic_target(_DEFAULT_ORGANIC_PAGE_TABLE)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(".".join(table_id.split(".")[:2])), exists_ok=True)
    schema = _organic_page_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    t.clustering_fields = ["source", "org_id"]
    client.create_table(t, exists_ok=True)
    _sync_table_columns(client, table_id, schema)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        metric_date = str(row.get("metric_date") or "")[:10]
        if not metric_date:
            continue
        payload.append({
            "source": "linkedin",
            "org_id": org_id_clean,
            "metric_date": metric_date,
            "page_views": int(row.get("page_views") or 0),
            "unique_visitors": int(row.get("unique_visitors") or 0),
            **{col: int(row.get(col) or 0) for col in _ORGANIC_PAGE_BREAKDOWN_COLS},
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    breakdown_set = ", ".join(f"{c} = S.{c}" for c in _ORGANIC_PAGE_BREAKDOWN_COLS)
    breakdown_cols = ", ".join(_ORGANIC_PAGE_BREAKDOWN_COLS)
    breakdown_vals = ", ".join(f"S.{c}" for c in _ORGANIC_PAGE_BREAKDOWN_COLS)
    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.org_id = S.org_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      page_views = S.page_views, unique_visitors = S.unique_visitors,
      {breakdown_set}, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, org_id, metric_date, page_views, unique_visitors, {breakdown_cols}, synced_at
    ) VALUES (
      S.source, S.org_id, S.metric_date, S.page_views, S.unique_visitors, {breakdown_vals}, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


_DEFAULT_ORGANIC_DEMOGRAPHICS_TABLE = "follower_demographics"
_DEFAULT_ORGANIC_ENGAGEMENT_TABLE = "engagement_daily"


def _organic_demographics_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("org_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("dimension", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("category", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("category_urn", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("organic_followers", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("paid_followers", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("total_followers", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def _organic_engagement_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
        bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("org_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("impressions", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("unique_impressions", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("clicks", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("likes", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("comments", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("shares", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("engagement_rate", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def mirror_linkedin_follower_demographics(org_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace this org's lifetime follower demographics in
    raw_linkedin_organic.follower_demographics.

    Grain is (source, org_id, dimension, category_urn). Since each sync carries a
    complete lifetime snapshot, stale categories for this org are deleted so a
    demographic that drops out doesn't linger.
    """
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    org_id_clean = str(org_id).strip().split(":")[-1]
    client, table_id = _organic_target(_DEFAULT_ORGANIC_DEMOGRAPHICS_TABLE)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(".".join(table_id.split(".")[:2])), exists_ok=True)
    schema = _organic_demographics_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.clustering_fields = ["source", "org_id", "dimension"]
    client.create_table(t, exists_ok=True)
    _sync_table_columns(client, table_id, schema)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        category_urn = str(row.get("category_urn") or "").strip()
        dimension = str(row.get("dimension") or "").strip()
        if not category_urn or not dimension:
            continue
        payload.append({
            "source": "linkedin",
            "org_id": org_id_clean,
            "dimension": dimension,
            "category": str(row.get("category") or "").strip() or None,
            "category_urn": category_urn,
            "organic_followers": int(row.get("organic_followers") or 0),
            "paid_followers": int(row.get("paid_followers") or 0),
            "total_followers": int(row.get("total_followers") or 0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.org_id = S.org_id
       AND T.dimension = S.dimension AND T.category_urn = S.category_urn
    WHEN MATCHED THEN UPDATE SET
      category = S.category, organic_followers = S.organic_followers,
      paid_followers = S.paid_followers, total_followers = S.total_followers,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, org_id, dimension, category, category_urn,
      organic_followers, paid_followers, total_followers, synced_at
    ) VALUES (
      S.source, S.org_id, S.dimension, S.category, S.category_urn,
      S.organic_followers, S.paid_followers, S.total_followers, S.synced_at
    )
    WHEN NOT MATCHED BY SOURCE AND T.source = 'linkedin' AND T.org_id = '{org_id_clean}'
      THEN DELETE
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_linkedin_engagement_daily(org_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert org-level daily engagement to raw_linkedin_organic.engagement_daily."""
    if not rows or not enabled("linkedin"):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    org_id_clean = str(org_id).strip().split(":")[-1]
    client, table_id = _organic_target(_DEFAULT_ORGANIC_ENGAGEMENT_TABLE)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(".".join(table_id.split(".")[:2])), exists_ok=True)
    schema = _organic_engagement_schema()
    t = bigquery.Table(table_id, schema=schema)
    t.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    t.clustering_fields = ["source", "org_id"]
    client.create_table(t, exists_ok=True)
    _sync_table_columns(client, table_id, schema)

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        metric_date = str(row.get("metric_date") or "")[:10]
        if not metric_date:
            continue
        payload.append({
            "source": "linkedin",
            "org_id": org_id_clean,
            "metric_date": metric_date,
            "impressions": int(row.get("impressions") or 0),
            "unique_impressions": int(row.get("unique_impressions") or 0),
            "clicks": int(row.get("clicks") or 0),
            "likes": int(row.get("likes") or 0),
            "comments": int(row.get("comments") or 0),
            "shares": int(row.get("shares") or 0),
            "engagement_rate": float(row.get("engagement_rate") or 0.0),
            "synced_at": synced_at,
        })
    if not payload:
        return {"enabled": True, "rows_upserted": 0, "table": table_id}

    temp_id = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.source = S.source AND T.org_id = S.org_id AND T.metric_date = S.metric_date
    WHEN MATCHED THEN UPDATE SET
      impressions = S.impressions, unique_impressions = S.unique_impressions,
      clicks = S.clicks, likes = S.likes, comments = S.comments, shares = S.shares,
      engagement_rate = S.engagement_rate, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, org_id, metric_date, impressions, unique_impressions, clicks,
      likes, comments, shares, engagement_rate, synced_at
    ) VALUES (
      S.source, S.org_id, S.metric_date, S.impressions, S.unique_impressions, S.clicks,
      S.likes, S.comments, S.shares, S.engagement_rate, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def create_linkedin_organic_post_mart_view() -> dict[str, Any]:
    """Create or replace marketing_marts.fact_linkedin_organic_post_stats view."""
    if not enabled("linkedin"):
        return {"enabled": False, "table": None}
    project_id = _linkedin_project_id()
    raw_dataset = _organic_dataset_id()
    mart_dataset = _mart_dataset_id()
    mart_table = _DEFAULT_LINKEDIN_ORGANIC_POST_FACT_TABLE
    client = _client(project_id)
    bigquery = _bigquery()
    client.create_dataset(bigquery.Dataset(f"{project_id}.{mart_dataset}"), exists_ok=True)
    table_id = f"{project_id}.{mart_dataset}.{mart_table}"
    sql = f"""
    CREATE OR REPLACE VIEW `{table_id}` AS
    SELECT
      p.source AS source_platform,
      CAST(p.org_id AS STRING) AS org_id,
      CAST(p.post_id AS STRING) AS post_id,
      p.post_urn,
      COALESCE(NULLIF(TRIM(p.title), ''), CAST(p.post_id AS STRING)) AS title,
      p.post_type,
      p.published_at AS date,
      p.impressions,
      p.unique_impressions,
      p.clicks,
      p.likes,
      p.comments,
      p.shares,
      p.engagement_rate,
      p.reactions_by_type,
      p.synced_at
    FROM `{project_id}.{raw_dataset}.{_DEFAULT_ORGANIC_POST_TABLE}` p
    """
    try:
        existing = client.get_table(table_id)
        if str(getattr(existing, "table_type", "")).upper() == "TABLE":
            client.delete_table(table_id, not_found_ok=True)
    except Exception:
        pass
    client.query(sql).result()
    return {"enabled": True, "table": table_id, "object_type": "view"}
