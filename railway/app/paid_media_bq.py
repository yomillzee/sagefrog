"""Paid media daily metrics in BigQuery (account + campaign grains)."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any

from google.cloud import bigquery

from bigquery_service import build_client, env_summary, project_id, run_dml, run_query

logger = logging.getLogger(__name__)

PAID_PLATFORMS = frozenset({"google", "linkedin", "meta"})

_DEFAULT_DATASETS = {
    "google": "warehouse_google_ads",
    "linkedin": "warehouse_linkedin",
    "meta": "warehouse_meta",
}

_ACCOUNT_TABLE = "metrics_account_daily"
_CAMPAIGN_TABLE = "metrics_campaign_daily"

_ACCOUNT_SCHEMA = [
    bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("metric_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("spend", "FLOAT64"),
    bigquery.SchemaField("clicks", "INT64"),
    bigquery.SchemaField("impressions", "INT64"),
    bigquery.SchemaField("conversions", "FLOAT64"),
    bigquery.SchemaField("conversion_value", "FLOAT64"),
    bigquery.SchemaField("synced_at", "TIMESTAMP"),
]

_CAMPAIGN_SCHEMA = _ACCOUNT_SCHEMA + [
    bigquery.SchemaField("campaign_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("campaign_name", "STRING"),
]


def enabled() -> bool:
    summ = env_summary()
    return bool(summ.get("has_bq_project_id") and summ.get("gcp_service_account_json_parse_ok"))


def _normalize_platform(platform: str) -> str:
    key = str(platform or "").strip().lower()
    if key not in PAID_PLATFORMS:
        raise ValueError(f"Unsupported paid media platform: {platform}")
    return key


def dataset_for(platform: str) -> str:
    key = _normalize_platform(platform)
    env_map = {
        "google": "BQ_GOOGLE_ADS_DATASET",
        "linkedin": "BQ_LINKEDIN_DATASET",
        "meta": "BQ_META_DATASET",
    }
    override = (os.getenv(env_map[key]) or "").strip()
    return override or _DEFAULT_DATASETS[key]


def normalize_account_id(platform: str, account_id: str) -> str:
    raw = str(account_id or "").strip()
    if not raw:
        return ""
    key = _normalize_platform(platform)
    if key == "google":
        return raw.replace("-", "")
    if key == "meta":
        return raw.removeprefix("act_")
    return raw.split(":")[-1]


def _table_id(platform: str, table: str) -> str:
    pid = project_id()
    ds = dataset_for(platform)
    return f"{pid}.{ds}.{table}"


def _quoted_table(platform: str, table: str) -> str:
    return f"`{_table_id(platform, table)}`"


def _synced_at_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _coerce_metric_date(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "").strip()[:10]


def normalize_account_rows(
    platform: str,
    account_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    account_id_clean = normalize_account_id(platform, account_id)
    synced_at = _synced_at_now()
    out: list[dict[str, Any]] = []
    for row in rows:
        metric_date = _coerce_metric_date(row.get("metric_date") or row.get("metricDate"))
        if not metric_date:
            continue
        out.append(
            {
                "account_id": account_id_clean,
                "metric_date": metric_date,
                "spend": float(row.get("spend") or 0),
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "conversions": float(row.get("conversions") or 0),
                "conversion_value": float(row.get("conversion_value") or row.get("conversionValue") or 0),
                "synced_at": synced_at,
            }
        )
    return out


def normalize_campaign_rows(
    platform: str,
    account_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    account_id_clean = normalize_account_id(platform, account_id)
    synced_at = _synced_at_now()
    out: list[dict[str, Any]] = []
    for row in rows:
        metric_date = _coerce_metric_date(row.get("metric_date") or row.get("metricDate"))
        campaign_id = str(row.get("campaign_id") or row.get("campaignId") or row.get("id") or "").strip()
        if not metric_date or not campaign_id:
            continue
        out.append(
            {
                "account_id": account_id_clean,
                "metric_date": metric_date,
                "campaign_id": campaign_id,
                "campaign_name": str(row.get("campaign_name") or row.get("campaignName") or row.get("name") or "") or None,
                "spend": float(row.get("spend") or 0),
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "conversions": float(row.get("conversions") or 0),
                "conversion_value": float(row.get("conversion_value") or row.get("conversionValue") or 0),
                "synced_at": synced_at,
            }
        )
    return out


def _dedupe_rows(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if key in merged:
            existing = merged[key]
            existing["spend"] = float(existing.get("spend") or 0) + float(row.get("spend") or 0)
            existing["clicks"] = int(existing.get("clicks") or 0) + int(row.get("clicks") or 0)
            existing["impressions"] = int(existing.get("impressions") or 0) + int(row.get("impressions") or 0)
            existing["conversions"] = float(existing.get("conversions") or 0) + float(row.get("conversions") or 0)
            existing["conversion_value"] = float(existing.get("conversion_value") or 0) + float(
                row.get("conversion_value") or 0
            )
            if row.get("campaign_name"):
                existing["campaign_name"] = row.get("campaign_name")
            existing["synced_at"] = row.get("synced_at") or existing.get("synced_at")
        else:
            merged[key] = dict(row)
    return list(merged.values())


def _merge_upsert(
    platform: str,
    table: str,
    rows: list[dict[str, Any]],
    *,
    merge_keys: tuple[str, ...],
    extra_update_fields: tuple[str, ...] = (),
) -> int:
    if not rows:
        return 0

    client = build_client()
    staging_name = f"_staging_{table}_{uuid.uuid4().hex[:12]}"
    staging_id = f"{project_id()}.{dataset_for(platform)}.{staging_name}"
    schema = _CAMPAIGN_SCHEMA if table == _CAMPAIGN_TABLE else _ACCOUNT_SCHEMA

    load_job = client.load_table_from_json(
        rows,
        staging_id,
        job_config=bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    load_job.result()

    target = _quoted_table(platform, table)
    staging = f"`{staging_id}`"
    update_assignments = [
        "spend = S.spend",
        "clicks = S.clicks",
        "impressions = S.impressions",
        "conversions = S.conversions",
        "conversion_value = S.conversion_value",
        "synced_at = S.synced_at",
    ]
    for field in extra_update_fields:
        update_assignments.append(f"{field} = S.{field}")

    on_clause = " AND ".join(f"T.{key} = S.{key}" for key in merge_keys)
    insert_cols = list(merge_keys) + [
        "spend",
        "clicks",
        "impressions",
        "conversions",
        "conversion_value",
        "synced_at",
    ] + list(extra_update_fields)
    insert_vals = [f"S.{col}" for col in insert_cols]

    sql = f"""
        MERGE {target} AS T
        USING {staging} AS S
        ON {on_clause}
        WHEN MATCHED THEN UPDATE SET
          {", ".join(update_assignments)}
        WHEN NOT MATCHED THEN INSERT ({", ".join(insert_cols)})
        VALUES ({", ".join(insert_vals)})
    """
    try:
        affected = run_dml(sql)
        return max(affected, len(rows))
    finally:
        client.delete_table(staging_id, not_found_ok=True)


def upsert_account_daily(platform: str, rows: list[dict[str, Any]]) -> int:
    deduped = _dedupe_rows(rows, ("account_id", "metric_date"))
    return _merge_upsert(
        platform,
        _ACCOUNT_TABLE,
        deduped,
        merge_keys=("account_id", "metric_date"),
    )


def upsert_campaign_daily(platform: str, rows: list[dict[str, Any]]) -> int:
    deduped = _dedupe_rows(rows, ("account_id", "campaign_id", "metric_date"))
    return _merge_upsert(
        platform,
        _CAMPAIGN_TABLE,
        deduped,
        merge_keys=("account_id", "campaign_id", "metric_date"),
        extra_update_fields=("campaign_name",),
    )


def sync_platform_metrics(
    platform: str,
    account_id: str,
    *,
    account_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    date_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize and MERGE upsert account + campaign daily rows for one platform."""
    if not enabled():
        return {"enabled": False, "account_rows_written": 0, "campaign_rows_written": 0}

    key = _normalize_platform(platform)
    account_id_clean = normalize_account_id(key, account_id)
    logger.info(
        "paid_media_bq sync start platform=%s account_id=%s date_range=%s account_rows=%s campaign_rows=%s",
        key,
        account_id_clean,
        date_range,
        len(account_rows),
        len(campaign_rows),
    )

    account_norm = normalize_account_rows(key, account_id_clean, account_rows)
    campaign_norm = normalize_campaign_rows(key, account_id_clean, campaign_rows)

    try:
        account_written = upsert_account_daily(key, account_norm)
        campaign_written = upsert_campaign_daily(key, campaign_norm)
    except Exception as exc:
        logger.exception(
            "paid_media_bq sync failed platform=%s account_id=%s error=%s",
            key,
            account_id_clean,
            exc,
        )
        raise

    result = {
        "enabled": True,
        "platform": key,
        "account_id": account_id_clean,
        "dataset": dataset_for(key),
        "account_rows_pulled": len(account_rows),
        "campaign_rows_pulled": len(campaign_rows),
        "account_rows_written": account_written,
        "campaign_rows_written": campaign_written,
        "upsert": "merge",
    }
    logger.info("paid_media_bq sync end platform=%s result=%s", key, result)
    return result


def _row_to_read_dict(platform: str, row: dict[str, Any], *, include_campaign: bool = False) -> dict[str, Any]:
    metric_date = row.get("metric_date")
    if hasattr(metric_date, "isoformat"):
        metric_date = metric_date.isoformat()
    else:
        metric_date = str(metric_date or "")[:10]
    synced_at = row.get("synced_at")
    if hasattr(synced_at, "isoformat"):
        synced_at = synced_at.isoformat()
    else:
        synced_at = str(synced_at or "")

    out: dict[str, Any] = {
        "source": platform,
        "account_id": str(row.get("account_id") or ""),
        "metric_date": metric_date,
        "spend": float(row.get("spend") or 0),
        "clicks": int(row.get("clicks") or 0),
        "impressions": int(row.get("impressions") or 0),
        "conversions": float(row.get("conversions") or 0),
        "conversion_value": float(row.get("conversion_value") or 0),
        "synced_at": synced_at,
    }
    if include_campaign:
        out["campaign_id"] = str(row.get("campaign_id") or "")
        out["campaign_name"] = row.get("campaign_name") or ""
    return out


def query_account_daily(
    platform: str,
    account_id: str,
    *,
    start: date,
    end: date,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    if not enabled():
        return []
    key = _normalize_platform(platform)
    account_id_clean = normalize_account_id(key, account_id)
    table = _quoted_table(key, _ACCOUNT_TABLE)
    lim = min(max(1, int(limit)), 20000)
    sql = f"""
        SELECT account_id, metric_date, spend, clicks, impressions,
               conversions, conversion_value, synced_at
        FROM {table}
        WHERE account_id = '{account_id_clean}'
          AND metric_date >= DATE '{start.isoformat()}'
          AND metric_date <= DATE '{end.isoformat()}'
        ORDER BY metric_date ASC
        LIMIT {lim}
    """
    rows = run_query(sql, max_rows=lim)
    return [_row_to_read_dict(key, row) for row in rows]


def query_campaign_daily(
    platform: str,
    account_id: str,
    *,
    start: date,
    end: date,
    campaign_id: str | None = None,
    limit: int = 20000,
) -> list[dict[str, Any]]:
    if not enabled():
        return []
    key = _normalize_platform(platform)
    account_id_clean = normalize_account_id(key, account_id)
    table = _quoted_table(key, _CAMPAIGN_TABLE)
    lim = min(max(1, int(limit)), 50000)
    campaign_filter = ""
    if campaign_id and str(campaign_id).strip():
        cid = str(campaign_id).strip()
        campaign_filter = f" AND campaign_id = '{cid}'"
    sql = f"""
        SELECT account_id, campaign_id, campaign_name, metric_date,
               spend, clicks, impressions, conversions, conversion_value, synced_at
        FROM {table}
        WHERE account_id = '{account_id_clean}'
          AND metric_date >= DATE '{start.isoformat()}'
          AND metric_date <= DATE '{end.isoformat()}'
          {campaign_filter}
        ORDER BY metric_date ASC, campaign_id
        LIMIT {lim}
    """
    rows = run_query(sql, max_rows=lim)
    return [_row_to_read_dict(key, row, include_campaign=True) for row in rows]


def query_summary_totals(
    platform: str,
    account_id: str,
    *,
    start: date,
    end: date,
    include_daily: bool = True,
    include_campaigns: bool = False,
) -> dict[str, Any]:
    daily_rows = query_account_daily(platform, account_id, start=start, end=end) if include_daily else []
    totals = {
        "spend": sum(float(r.get("spend") or 0) for r in daily_rows),
        "clicks": sum(int(r.get("clicks") or 0) for r in daily_rows),
        "impressions": sum(int(r.get("impressions") or 0) for r in daily_rows),
        "conversions": sum(float(r.get("conversions") or 0) for r in daily_rows),
        "conversion_value": sum(float(r.get("conversion_value") or 0) for r in daily_rows),
    }
    out: dict[str, Any] = {
        "platform": _normalize_platform(platform),
        "account_id": normalize_account_id(platform, account_id),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "totals": totals,
        "daily_rows": daily_rows,
    }
    if include_campaigns:
        out["campaign_rows"] = query_campaign_daily(platform, account_id, start=start, end=end)
    return out


def env_summary_paid() -> dict[str, Any]:
    base = env_summary()
    return {
        **base,
        "paid_media_bq_enabled": enabled(),
        "bq_google_ads_dataset": dataset_for("google") if base.get("has_bq_project_id") else None,
        "bq_linkedin_dataset": dataset_for("linkedin") if base.get("has_bq_project_id") else None,
        "bq_meta_dataset": dataset_for("meta") if base.get("has_bq_project_id") else None,
    }
