"""HubSpot CRM → BigQuery sync service.

Calls POST /crm/v3/objects/contacts/search to pull MQL/SQL contacts
and upserts them into marketing_marts.fact_hubspot_contacts via a MERGE
over a disposable staging table.  No HubSpot native BQ transfer — we
only pull the properties the dashboard actually uses.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from google.cloud import bigquery

import bigquery_service

LOGGER = logging.getLogger(__name__)

_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/contacts/search"
_PAGE_SIZE = 200
_RATE_DELAY = 0.22   # ~4.5 req/s, well under HubSpot's 5/s search limit

_HS_PROPS = [
    "createdate",
    "lastmodifieddate",
    "email",
    "lifecyclestage",
    "hs_lead_status",
    "hs_analytics_source",
    "hs_latest_source",
    "hs_latest_source_data_1",
    "hs_latest_source_data_2",
    "utm_source",
    "utm_medium",
    "utm_campaign",
]

_MQL_STAGES = ["marketingqualifiedlead", "salesqualifiedlead"]

_SCHEMA = [
    bigquery.SchemaField("contact_id",              "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("createdate",              "TIMESTAMP"),
    bigquery.SchemaField("lastmodifieddate",        "TIMESTAMP"),
    bigquery.SchemaField("email",                   "STRING"),
    bigquery.SchemaField("lifecyclestage",          "STRING"),
    bigquery.SchemaField("hs_lead_status",          "STRING"),
    bigquery.SchemaField("hs_analytics_source",     "STRING"),
    bigquery.SchemaField("hs_latest_source",        "STRING"),
    bigquery.SchemaField("hs_latest_source_data_1", "STRING"),
    bigquery.SchemaField("hs_latest_source_data_2", "STRING"),
    bigquery.SchemaField("utm_source",              "STRING"),
    bigquery.SchemaField("utm_medium",              "STRING"),
    bigquery.SchemaField("utm_campaign",            "STRING"),
    bigquery.SchemaField("synced_at",               "TIMESTAMP"),
]

_TARGET_TABLE  = "fact_hubspot_contacts"
_MQL_VIEW      = "fact_hubspot_mql_daily"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _token() -> str:
    t = (os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()
    if not t:
        raise RuntimeError("HUBSPOT_ACCESS_TOKEN is not set")
    return t


def _search_page(
    *,
    token: str,
    after: str | None,
    lookback_ms: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "filterGroups": [{
            "filters": [
                {
                    "propertyName": "lifecyclestage",
                    "operator": "IN",
                    "values": _MQL_STAGES,
                },
                {
                    "propertyName": "lastmodifieddate",
                    "operator": "GTE",
                    "value": str(lookback_ms),
                },
            ]
        }],
        "properties": _HS_PROPS,
        "limit": _PAGE_SIZE,
    }
    if after:
        body["after"] = after

    req = urllib.request.Request(
        _SEARCH_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_row(result: dict[str, Any], synced_at: str) -> dict[str, Any]:
    p = result.get("properties") or {}
    return {
        "contact_id":              str(result["id"]),
        "createdate":              p.get("createdate") or None,
        "lastmodifieddate":        p.get("lastmodifieddate") or None,
        "email":                   p.get("email") or None,
        "lifecyclestage":          p.get("lifecyclestage") or None,
        "hs_lead_status":          p.get("hs_lead_status") or None,
        "hs_analytics_source":     p.get("hs_analytics_source") or None,
        "hs_latest_source":        p.get("hs_latest_source") or None,
        "hs_latest_source_data_1": p.get("hs_latest_source_data_1") or None,
        "hs_latest_source_data_2": p.get("hs_latest_source_data_2") or None,
        "utm_source":              p.get("utm_source") or None,
        "utm_medium":              p.get("utm_medium") or None,
        "utm_campaign":            p.get("utm_campaign") or None,
        "synced_at":               synced_at,
    }


def _ensure_table(client: bigquery.Client, project: str, dataset: str) -> None:
    ref = f"{project}.{dataset}.{_TARGET_TABLE}"
    tbl = bigquery.Table(ref, schema=_SCHEMA)
    tbl.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="lastmodifieddate",
    )
    tbl.clustering_fields = ["lifecyclestage", "contact_id"]
    client.create_table(tbl, exists_ok=True)
    LOGGER.debug("Ensured table %s", ref)


def _upsert(
    client: bigquery.Client,
    project: str,
    dataset: str,
    rows: list[dict[str, Any]],
) -> None:
    stg_id = f"{project}.{dataset}._hs_contacts_stg_{uuid4().hex[:12]}"
    stg_tbl = bigquery.Table(stg_id, schema=_SCHEMA)
    try:
        job = client.load_table_from_json(
            rows,
            stg_id,
            job_config=bigquery.LoadJobConfig(
                schema=_SCHEMA,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            ),
        )
        job.result()
        LOGGER.info("Staged %d rows → %s", len(rows), stg_id)

        target  = f"`{project}.{dataset}.{_TARGET_TABLE}`"
        staging = f"`{stg_id}`"
        upd_cols = [f.name for f in _SCHEMA if f.name != "contact_id"]
        set_clause    = ", ".join(f"{c} = S.{c}" for c in upd_cols)
        insert_cols   = ", ".join(f.name for f in _SCHEMA)
        insert_vals   = ", ".join(f"S.{f.name}" for f in _SCHEMA)

        merge_sql = f"""
MERGE {target} T
USING {staging} S ON T.contact_id = S.contact_id
WHEN MATCHED THEN
  UPDATE SET {set_clause}
WHEN NOT MATCHED THEN
  INSERT ({insert_cols}) VALUES ({insert_vals})
"""
        client.query(merge_sql).result()
        LOGGER.info("MERGE complete: %d rows upserted", len(rows))
    finally:
        client.delete_table(stg_id, not_found_ok=True)


def _rebuild_view(client: bigquery.Client, project: str, dataset: str) -> None:
    sql = f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.{_MQL_VIEW}` AS
SELECT
  DATE(COALESCE(lastmodifieddate, createdate)) AS date,
  lifecyclestage,
  hs_lead_status,
  hs_latest_source,
  utm_source,
  utm_medium,
  utm_campaign,
  COUNT(*)         AS contacts,
  MAX(synced_at)   AS latest_synced_at
FROM `{project}.{dataset}.{_TARGET_TABLE}`
WHERE lifecyclestage IN ('marketingqualifiedlead', 'salesqualifiedlead')
GROUP BY 1, 2, 3, 4, 5, 6, 7
"""
    client.query(sql).result()
    LOGGER.info("Rebuilt view %s.%s.%s", project, dataset, _MQL_VIEW)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def sync_hubspot_contacts(
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    lookback_days: int = 7,
    bq_client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Fetch recent MQL/SQL contacts from HubSpot and upsert into BigQuery.

    Args:
        project_id:    GCP project (default: HUBSPOT_SYNC_PROJECT_ID env).
        dataset_id:    BQ dataset  (default: HUBSPOT_SYNC_DATASET env, then "marketing_marts").
        lookback_days: How far back to filter by lastmodifieddate (default 7).
        bq_client:     Inject an existing BQ client (testing / reuse).

    Returns:
        Summary dict: status, rows_synced, pages, elapsed_s, synced_at.
    """
    project = (project_id or os.getenv("HUBSPOT_SYNC_PROJECT_ID") or "").strip()
    dataset = (dataset_id or os.getenv("HUBSPOT_SYNC_DATASET") or "marketing_marts").strip()
    if not project:
        raise RuntimeError(
            "BQ project not set — provide project_id or set HUBSPOT_SYNC_PROJECT_ID"
        )

    token  = _token()
    client = bq_client or bigquery_service.build_client(project_id=project)
    _ensure_table(client, project, dataset)

    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp() * 1000
    )
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    LOGGER.info(
        "HubSpot sync start — lookback=%d days, project=%s, dataset=%s",
        lookback_days, project, dataset,
    )
    t0     = time.monotonic()
    rows:  list[dict[str, Any]] = []
    after: str | None = None
    pages = 0

    while True:
        try:
            resp = _search_page(token=token, after=after, lookback_ms=cutoff_ms)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HubSpot search API {exc.code}: {body[:400]}") from exc

        pages  += 1
        batch   = resp.get("results") or []
        rows   += [_parse_row(r, synced_at) for r in batch]
        LOGGER.info("Page %d: %d results (%d total)", pages, len(batch), len(rows))

        next_after = ((resp.get("paging") or {}).get("next") or {}).get("after")
        if not next_after or not batch:
            break

        after = next_after
        time.sleep(_RATE_DELAY)

    if rows:
        _upsert(client, project, dataset, rows)
    else:
        LOGGER.info("No contacts modified in the last %d days — nothing to upsert", lookback_days)

    _rebuild_view(client, project, dataset)

    elapsed = round(time.monotonic() - t0, 1)
    summary = {
        "status":      "ok",
        "rows_synced": len(rows),
        "pages":       pages,
        "elapsed_s":   elapsed,
        "synced_at":   synced_at,
    }
    LOGGER.info("HubSpot sync done: %s", summary)
    return summary
