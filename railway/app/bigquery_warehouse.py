"""Optional BigQuery mirror for Postgres metrics_daily warehouse rows."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_DEFAULT_LINKEDIN_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_LINKEDIN_DATASET = "linkedin_ads"
_DEFAULT_TABLE = "metrics_daily"


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
        bigquery.SchemaField("reach", "INT64"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
    ]


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
        reach = row.get("reach")
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
                "reach": int(reach) if reach is not None else None,
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
      reach = S.reach,
      synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      source, account_id, metric_date, spend, clicks, impressions, conversions, conversion_value, reach, synced_at
    ) VALUES (
      S.source, S.account_id, S.metric_date, S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.reach, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}
