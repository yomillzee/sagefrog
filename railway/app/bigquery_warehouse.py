"""Optional BigQuery mirror for Postgres metrics_daily warehouse rows."""

from __future__ import annotations

import contextlib
import contextvars
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_DEFAULT_LINKEDIN_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_LINKEDIN_DATASET = "raw_linkedin_ads"
_DEFAULT_GOOGLE_DATASET = "raw_google_ads"
_DEFAULT_TABLE = "metrics_daily"
_DEFAULT_CAMPAIGN_TABLE = "campaign_daily"
_DEFAULT_CAMPAIGNS_TABLE = "campaigns"
_DEFAULT_AD_TABLE = "ad_daily"
_DEFAULT_CREATIVE_METADATA_TABLE = "creative_metadata"
_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_LINKEDIN_CAMPAIGN_FACT_TABLE = "fact_linkedin_ads_campaign_daily"
_DEFAULT_LINKEDIN_CREATIVE_FACT_TABLE = "fact_linkedin_ads_creative_daily"


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
    meta_dataset_id: str | None = None,
    mart_dataset_id: str | None = None,
):
    """Route every warehouse read/write to one client's BigQuery identity."""
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "credentials_env": (credentials_env or "").strip() or None,
        "linkedin_dataset": (linkedin_dataset_id or "").strip() or None,
        "google_dataset": (google_dataset_id or "").strip() or None,
        "meta_dataset": (meta_dataset_id or "").strip() or None,
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
    if _route_value("project") and source and source.strip().lower() in {"google", "linkedin", "meta"}:
        return True
    return bool(source and source.strip().lower() == "linkedin" and _linkedin_project_id())


def _linkedin_project_id() -> str:
    return (
        _route_value("project")
        or os.getenv("BQ_LINKEDIN_PROJECT_ID")
        or _DEFAULT_LINKEDIN_PROJECT
    ).strip()


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


def rebuild_unified_marketing_mart() -> dict[str, Any]:
    """Build the dashboard's single daily fact from available normalized facts."""
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
    selects: list[str] = []
    included: list[str] = []
    for source, table in candidates.items():
        source_id = f"{project_id}.{mart_dataset}.{table}"
        try:
            client.get_table(source_id)
        except Exception:
            continue
        included.append(source)
        selects.append(
            f"SELECT '{source}' AS source, account_id, date, campaign_id, "
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


_DEFAULT_META_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_META_DATASET = "raw_meta_ads"
_DEFAULT_META_CAMPAIGN_TABLE = "campaign_daily"
_DEFAULT_META_ADSET_TABLE = "adset_daily"
_DEFAULT_META_AD_TABLE = "ad_daily"
_DEFAULT_META_AD_CREATIVE_TABLE = "ad_creative"
_DEFAULT_META_CAMPAIGN_FACT_TABLE = "fact_meta_ads_campaign_daily"
_DEFAULT_META_ADSET_FACT_TABLE = "fact_meta_ads_adset_daily"
_DEFAULT_META_AD_FACT_TABLE = "fact_meta_ads_ad_daily"


def _meta_project_id() -> str:
    return (
        _route_value("project")
        or os.getenv("BQ_META_PROJECT_ID")
        or _DEFAULT_META_PROJECT
    ).strip()


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


def _meta_campaign_daily_schema() -> list[Any]:
    bigquery = _bigquery()
    return [
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
    table = bigquery.Table(table_id, schema=schema)
    field_names = {f.name for f in schema}
    if "metric_date" in field_names:
        table.time_partitioning = bigquery.TimePartitioning(field="metric_date")
    table.clustering_fields = ["account_id"]
    client.create_table(table, exists_ok=True)
    return table_id


def mirror_meta_campaign_daily_batch(account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
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
      campaign_name = S.campaign_name, spend = S.spend, clicks = S.clicks,
      impressions = S.impressions, conversions = S.conversions,
      conversion_value = S.conversion_value, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      account_id, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.account_id, S.campaign_id, S.campaign_name, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_meta_adset_daily_batch(account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
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
      adset_name = S.adset_name, campaign_id = S.campaign_id, campaign_name = S.campaign_name,
      spend = S.spend, clicks = S.clicks, impressions = S.impressions,
      conversions = S.conversions, conversion_value = S.conversion_value, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      account_id, adset_id, adset_name, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.account_id, S.adset_id, S.adset_name, S.campaign_id, S.campaign_name, S.metric_date,
      S.spend, S.clicks, S.impressions, S.conversions, S.conversion_value, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


def mirror_meta_ad_daily_batch(account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
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
      ad_name = S.ad_name, adset_id = S.adset_id, adset_name = S.adset_name,
      campaign_id = S.campaign_id, campaign_name = S.campaign_name,
      spend = S.spend, clicks = S.clicks, impressions = S.impressions,
      conversions = S.conversions, conversion_value = S.conversion_value, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      account_id, ad_id, ad_name, adset_id, adset_name, campaign_id, campaign_name, metric_date,
      spend, clicks, impressions, conversions, conversion_value, synced_at
    ) VALUES (
      S.account_id, S.ad_id, S.ad_name, S.adset_id, S.adset_name, S.campaign_id, S.campaign_name, S.metric_date,
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


def mirror_meta_ad_creative_batch(account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
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
      ad_name = S.ad_name, adset_id = S.adset_id, adset_name = S.adset_name,
      campaign_id = S.campaign_id, campaign_name = S.campaign_name,
      thumbnail_url = S.thumbnail_url, image_url = S.image_url,
      media_type = S.media_type, synced_at = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      account_id, ad_id, ad_name, adset_id, adset_name,
      campaign_id, campaign_name, thumbnail_url, image_url, media_type, synced_at
    ) VALUES (
      S.account_id, S.ad_id, S.ad_name, S.adset_id, S.adset_name,
      S.campaign_id, S.campaign_name, S.thumbnail_url, S.image_url, S.media_type, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return {"enabled": True, "rows_upserted": len(payload), "table": table_id}


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
