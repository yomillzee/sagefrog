"""Optional BigQuery mirror for Postgres metrics_daily warehouse rows."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_DEFAULT_LINKEDIN_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_LINKEDIN_DATASET = "linkedin_ads"
_DEFAULT_TABLE = "metrics_daily"
_DEFAULT_CAMPAIGN_TABLE = "campaign_daily"
_DEFAULT_CAMPAIGNS_TABLE = "campaigns"
_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_LINKEDIN_CAMPAIGN_FACT_TABLE = "fact_linkedin_ads_campaign_daily"


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
    return bool(source and source.strip().lower() == "linkedin" and _linkedin_project_id())


def _linkedin_project_id() -> str:
    return (os.getenv("BQ_LINKEDIN_PROJECT_ID") or _DEFAULT_LINKEDIN_PROJECT).strip()


def _dataset_id(source: str) -> str:
    key = source.strip().upper().replace("-", "_")
    return (os.getenv(f"BQ_{key}_DATASET_ID") or os.getenv("BQ_WAREHOUSE_DATASET_ID") or (_DEFAULT_LINKEDIN_DATASET if source == "linkedin" else "")).strip()


def _table_name(source: str) -> str:
    key = source.strip().upper().replace("-", "_")
    return (os.getenv(f"BQ_{key}_TABLE_ID") or os.getenv("BQ_WAREHOUSE_TABLE_ID") or _DEFAULT_TABLE).strip()


def _target(source: str) -> tuple[Any, str]:
    source_key = source.strip().lower()
    project_id = _linkedin_project_id() if source_key == "linkedin" else (os.getenv("BQ_WAREHOUSE_PROJECT_ID") or os.getenv("BQ_PROJECT_ID") or "").strip()
    dataset_id = _dataset_id(source_key)
    table_name = _table_name(source_key)
    if not project_id or not dataset_id or not table_name:
        raise RuntimeError("Missing BigQuery warehouse project/dataset/table configuration.")
    import bigquery_service

    return bigquery_service.build_client(project_id), f"{project_id}.{dataset_id}.{table_name}"


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


def mirror_metrics_daily_batch(source: str, account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows or not enabled(source):
        return {"enabled": False, "rows_upserted": 0, "table": None}

    source_key = source.strip().lower()
    account_id_clean = str(account_id).strip().split(":")[-1]
    client, table_id = _target(source_key)
    ensure_table(source_key)
    synced_at = datetime.now(timezone.utc).isoformat()
    payload = []
    for row in rows:
        metric_date = row.get("metric_date") or row.get("metricDate")
        if not metric_date:
            continue
        payload.append(
            {
                "source": source_key,
                "account_id": account_id_clean,
                "metric_date": str(metric_date)[:10],
                "spend": float(row.get("spend") or 0),
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "conversions": float(row.get("conversions") or 0),
                "conversion_value": float(row.get("conversion_value") or row.get("conversionValue") or 0),
                "synced_at": synced_at,
            }
        )
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
    mart_dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DATASET).strip()
    mart_table = (
        os.getenv("BQ_LINKEDIN_CAMPAIGN_FACT_TABLE")
        or _DEFAULT_LINKEDIN_CAMPAIGN_FACT_TABLE
    ).strip()
    import bigquery_service

    client = bigquery_service.build_client(project_id)
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
    sql = f"""
    CREATE OR REPLACE TABLE `{table_id}` AS
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
      cd.metric_date,
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
    client.query(sql).result()
    return {"enabled": True, "table": table_id}
