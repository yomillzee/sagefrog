"""BigQuery table provisioning and validation for Portal dashboards.

Dream state: when a specialist enters GCP credentials in the settings page,
this service auto-creates all required datasets and tables so the dashboard
works immediately.

Current usage:
- Called during refresh to validate tables before querying
- Called from settings probe to surface setup status
- Called manually for initial setup

Required env vars (Penn defaults):
    GCP_CREDS_PENN_BASE64      base64 service account JSON
    BQ_MART_PROJECT_ID         GCP project (default: penn-community-b-1699391543298)
    BQ_MART_DATASET_ID         dataset    (default: marketing_marts)
    BQ_DATASET_ID              GA4 export dataset (default: analytics_...)
    BQ_PROJECT_ID              GA4 project
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import bigquery_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mart_project_id() -> str:
    return os.getenv("BQ_MART_PROJECT_ID") or "penn-community-b-1699391543298"


def _mart_dataset_id() -> str:
    return os.getenv("BQ_MART_DATASET_ID") or "marketing_marts"


def _ga4_project_id() -> str:
    return os.getenv("BQ_PROJECT_ID") or _mart_project_id()


def _ga4_dataset_id() -> str:
    return os.getenv("BQ_DATASET_ID") or ""


def _credentials_env() -> str:
    return "GCP_CREDS_PENN_BASE64"


def _bq_client(credentials_env: str | None = None, project_id: str | None = None):
    """Build a BigQuery client using bigquery_service.build_client. Raises on bad credentials."""
    return bigquery_service.build_client(
        project_id=project_id or _mart_project_id(),
        credentials_env=credentials_env or _credentials_env(),
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TableStatus:
    table_id: str            # fully qualified: project.dataset.table
    exists: bool
    row_count: int | None    # None = not checked
    min_date: str | None
    max_date: str | None
    created_now: bool        # True if we just created it
    error: str | None

    @property
    def ok(self) -> bool:
        return self.exists and not self.error

    def as_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "exists": self.exists,
            "row_count": self.row_count,
            "min_date": self.min_date,
            "max_date": self.max_date,
            "created_now": self.created_now,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class ProvisionResult:
    tables: list[TableStatus]
    credentials_ok: bool
    error: str | None        # top-level error (e.g. bad credentials)

    @property
    def all_ok(self) -> bool:
        return self.credentials_ok and all(t.ok for t in self.tables) and not self.error

    def as_dict(self) -> dict:
        return {
            "all_ok": self.all_ok,
            "credentials_ok": self.credentials_ok,
            "error": self.error,
            "tables": [t.as_dict() for t in self.tables],
        }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _gsc_query_schema():
    from google.cloud import bigquery as bq
    return [
        bq.SchemaField("date",                 "DATE",      mode="REQUIRED"),
        bq.SchemaField("query",                "STRING",    mode="NULLABLE"),
        bq.SchemaField("organic_clicks",       "INT64",     mode="REQUIRED"),
        bq.SchemaField("organic_impressions",  "INT64",     mode="REQUIRED"),
        bq.SchemaField("organic_sum_position", "FLOAT64",   mode="REQUIRED"),
        bq.SchemaField("is_anonymized_query",  "BOOL",      mode="REQUIRED"),
        bq.SchemaField("synced_at",            "TIMESTAMP", mode="REQUIRED"),
    ]


def _gsc_page_schema():
    from google.cloud import bigquery as bq
    return [
        bq.SchemaField("date",                 "DATE",      mode="REQUIRED"),
        bq.SchemaField("page_url",             "STRING",    mode="REQUIRED"),
        bq.SchemaField("organic_clicks",       "INT64",     mode="REQUIRED"),
        bq.SchemaField("organic_impressions",  "INT64",     mode="REQUIRED"),
        bq.SchemaField("organic_sum_position", "FLOAT64",   mode="REQUIRED"),
        bq.SchemaField("synced_at",            "TIMESTAMP", mode="REQUIRED"),
    ]


def _ensure_one_table(c, full_id: str, schema, cluster_field: str) -> TableStatus:
    """Create a single date-partitioned, clustered table if it doesn't exist."""
    from google.cloud import bigquery as bq
    from google.cloud.exceptions import NotFound

    created_now = False
    try:
        c.get_table(full_id)
    except NotFound:
        t = bq.Table(full_id, schema=schema)
        t.time_partitioning = bq.TimePartitioning(
            type_=bq.TimePartitioningType.DAY, field="date"
        )
        t.clustering_fields = [cluster_field]
        c.create_table(t, exists_ok=True)
        created_now = True

    sql = (
        f"SELECT COUNT(*) AS n, MIN(date) AS min_date, MAX(date) AS max_date "
        f"FROM `{full_id}` LIMIT 1"
    )
    rows = list(c.query(sql).result(max_results=1))
    r = dict(rows[0].items()) if rows else {}
    return TableStatus(
        table_id=full_id,
        exists=True,
        row_count=int(r.get("n") or 0),
        min_date=str(r.get("min_date") or "")[:10] or None,
        max_date=str(r.get("max_date") or "")[:10] or None,
        created_now=created_now,
        error=None,
    )


# ---------------------------------------------------------------------------
# Table operations
# ---------------------------------------------------------------------------

def ensure_gsc_tables(client=None, client_slug: str | None = None) -> list[TableStatus]:
    """Ensure fact_gsc_query_daily and fact_gsc_page_daily exist.

    client_slug routes into that client's BQ destination via gsc_clients
    (same registry gsc_sync_service uses). Omit for legacy Penn-only behaviour.

    Returns [query_status, page_status].
    """
    from google.cloud import bigquery as bq
    import gsc_clients

    target = gsc_clients.resolve_target(client_slug) if client_slug else gsc_clients.default_target()
    proj    = target.bq_project_id
    dataset = target.bq_dataset_id
    query_id = f"{proj}.{dataset}.fact_gsc_query_daily"
    page_id  = f"{proj}.{dataset}.fact_gsc_page_daily"

    statuses = []
    for full_id, schema, cluster in [
        (query_id, _gsc_query_schema(), "query"),
        (page_id,  _gsc_page_schema(),  "page_url"),
    ]:
        try:
            c = client or _bq_client(credentials_env=target.credentials_env, project_id=proj)
            ds_ref = bq.Dataset(f"{proj}.{dataset}")
            ds_ref.location = "US"
            c.create_dataset(ds_ref, exists_ok=True)
            statuses.append(_ensure_one_table(c, full_id, schema, cluster))
        except Exception as exc:
            statuses.append(TableStatus(
                table_id=full_id, exists=False, row_count=None,
                min_date=None, max_date=None, created_now=False,
                error=str(exc)[:300],
            ))
    return statuses


def validate_ga4_export(client=None) -> TableStatus:
    """Validate that GA4 BQ export tables exist. We can't create them (Google manages them)."""
    project = _ga4_project_id()
    dataset = _ga4_dataset_id()
    table_id = f"{project}.{dataset}.events_*" if dataset else "(ga4 export)"

    if not dataset:
        return TableStatus(
            table_id="(ga4 export)",
            exists=False,
            row_count=None,
            min_date=None,
            max_date=None,
            created_now=False,
            error="BQ_DATASET_ID not set — set in env to validate GA4 export",
        )

    try:
        c = client or _bq_client()
        sql = (
            f"SELECT COUNT(*) AS n FROM `{project}.{dataset}.events_*` "
            f"WHERE _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) "
            f"LIMIT 1"
        )
        rows = list(c.query(sql).result(max_results=1))
        n = 0
        if rows:
            r = dict(rows[0].items())
            n = int(r.get("n") or 0)
        return TableStatus(
            table_id=f"{project}.{dataset}.events_*",
            exists=True,
            row_count=n,
            min_date=None,
            max_date=None,
            created_now=False,
            error=None,
        )
    except Exception as exc:
        return TableStatus(
            table_id=table_id,
            exists=False,
            row_count=None,
            min_date=None,
            max_date=None,
            created_now=False,
            error=str(exc)[:300],
        )


# ---------------------------------------------------------------------------
# Top-level validate / provision
# ---------------------------------------------------------------------------

def validate_all() -> ProvisionResult:
    """Validate all required BQ tables. Creates GSC tables if missing.

    Never raises — all exceptions are captured into the result.
    """
    try:
        c = _bq_client()
        credentials_ok = True
    except Exception as exc:
        return ProvisionResult(
            tables=[],
            credentials_ok=False,
            error=str(exc)[:300],
        )

    gsc_statuses = ensure_gsc_tables(client=c)
    ga4_status   = validate_ga4_export(client=c)

    return ProvisionResult(
        tables=[*gsc_statuses, ga4_status],
        credentials_ok=credentials_ok,
        error=None,
    )


# provision_all is semantically the same as validate_all:
# ensure_gsc_tables already creates tables if missing.
provision_all = validate_all
