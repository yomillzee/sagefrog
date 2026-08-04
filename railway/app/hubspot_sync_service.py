"""HubSpot CRM → BigQuery sync service.

Two pipelines, both direct API → mart (no raw layer, no HubSpot native BQ
transfer — we only pull the properties the dashboard actually uses):

  * Contacts: POST /crm/v3/objects/contacts/search — contacts that reached a
    lifecycle stage within the window, upserted into fact_hubspot_contacts.
  * Deals:    POST /crm/v3/objects/deals/search — deals created OR closed within
    the window, upserted into fact_hubspot_deals.

Both MERGE over a disposable staging table so re-runs are idempotent.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from google.cloud import bigquery

import bigquery_service

LOGGER = logging.getLogger(__name__)

_CONTACTS_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/contacts/search"
_DEALS_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/deals/search"
# Marketing email statistics — metadata + post-send performance in one response.
# Requires the `content` OAuth scope (Marketing Hub tier gated), so a client on a
# lower tier gets a 403 here and the email sync is skipped, not failed.
_EMAILS_STATS_URL = "https://api.hubapi.com/marketing/v3/emails/statistics/list"
# statistics/list requires a time span, and HubSpot binds these two params as
# epoch MILLISECONDS (ISO strings raise "Unable to parse value for query
# parameter: startTimestamp"). We pass a very wide window so the per-email
# counters returned approximate lifetime performance, matching the app's
# "Performance" tab.
_EMAIL_STATS_LOOKBACK_DAYS = 3650  # ~10 years
_PAGE_SIZE = 200
_RATE_DELAY = 0.22   # ~4.5 req/s, well under HubSpot's 5/s search limit

_HS_PROPS = [
    "createdate",
    "lastmodifieddate",
    "email",
    "firstname",
    "lastname",
    "company",
    "jobtitle",
    "lifecyclestage",
    "hs_lead_status",
    "hubspot_owner_id",
    "hs_analytics_source",
    "hs_analytics_source_data_1",
    "hs_analytics_source_data_2",
    "hs_latest_source",
    "hs_latest_source_data_1",
    "hs_latest_source_data_2",
    "first_conversion_event_name",
    "first_conversion_date",
    "recent_conversion_event_name",
    "recent_conversion_date",
    "num_associated_deals",
    "utm_source",
    "utm_medium",
    "utm_campaign",
]

# Deal properties. hs_analytics_source on a deal = original source of the
# earliest-activity contact for that deal, so revenue attributes to a channel
# without a manual contact join. Pipeline/dealstage come back as opaque IDs —
# use the is_closed / is_closed_won booleans for reliable won/revenue logic.
_DEAL_PROPS = [
    "dealname",
    "createdate",
    "closedate",
    "hs_lastmodifieddate",
    "pipeline",
    "dealstage",
    "dealtype",
    "hs_is_closed",
    "hs_is_closed_won",
    "amount",
    "amount_in_home_currency",
    "deal_currency_code",
    "hs_forecast_amount",
    "hs_deal_stage_probability",
    "hs_analytics_source",
    "hs_analytics_source_data_1",
    "hs_analytics_source_data_2",
    "hs_analytics_latest_source",
    "hubspot_owner_id",
    "days_to_close",
    "num_associated_contacts",
]

# Lifecycle funnel: stage key → (label, "date entered stage" property). Filtering
# by the date property captures contacts who REACHED the stage at any point — even
# if they've since progressed — i.e. "is or has been an MQL".
#
# NOTE: use HubSpot's current `hs_v2_date_entered_<stage>` properties. The legacy
# `hs_lifecyclestage_<stage>_date` names were deprecated and now raise a 400
# "Invalid property name" from the CRM search API.
_LIFECYCLE_FUNNEL = [
    ("subscriber",             "Subscriber",                       "hs_v2_date_entered_subscriber"),
    ("lead",                   "Lead",                             "hs_v2_date_entered_lead"),
    ("marketingqualifiedlead", "Marketing Qualified Lead (MQL)",   "hs_v2_date_entered_marketingqualifiedlead"),
    ("salesqualifiedlead",     "Sales Qualified Lead (SQL)",       "hs_v2_date_entered_salesqualifiedlead"),
    ("opportunity",            "Opportunity",                      "hs_v2_date_entered_opportunity"),
    ("customer",               "Customer",                         "hs_v2_date_entered_customer"),
]
_STAGE_DATE_PROP = {k: prop for k, _label, prop in _LIFECYCLE_FUNNEL}
_STAGE_LABELS    = {k: label for k, label, _prop in _LIFECYCLE_FUNNEL}
_DEFAULT_STAGE   = "marketingqualifiedlead"
_DEFAULT_LOOKBACK_DAYS = 90


def lifecycle_options() -> list[dict[str, str]]:
    """Funnel stages for the connector's lifecycle-stage dropdown."""
    return [{"value": k, "label": label} for k, label, _prop in _LIFECYCLE_FUNNEL]


def normalize_stage(stage: str | None) -> str:
    s = (stage or "").strip()
    return s if s in _STAGE_DATE_PROP else _DEFAULT_STAGE


_CONTACT_SCHEMA = [
    bigquery.SchemaField("contact_id",              "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("createdate",              "TIMESTAMP"),
    bigquery.SchemaField("lastmodifieddate",        "TIMESTAMP"),
    bigquery.SchemaField("email",                   "STRING"),
    bigquery.SchemaField("lifecyclestage",          "STRING"),
    bigquery.SchemaField("stage_filter",            "STRING"),   # the funnel stage this row was pulled for
    bigquery.SchemaField("became_stage_date",       "TIMESTAMP"),# when the contact entered that stage
    bigquery.SchemaField("hs_lead_status",          "STRING"),
    bigquery.SchemaField("hs_analytics_source",     "STRING"),
    bigquery.SchemaField("hs_latest_source",        "STRING"),
    bigquery.SchemaField("hs_latest_source_data_1", "STRING"),
    bigquery.SchemaField("hs_latest_source_data_2", "STRING"),
    bigquery.SchemaField("utm_source",              "STRING"),
    bigquery.SchemaField("utm_medium",              "STRING"),
    bigquery.SchemaField("utm_campaign",            "STRING"),
    bigquery.SchemaField("synced_at",               "TIMESTAMP"),
    # --- appended post-launch (kept after synced_at so BQ schema updates on the
    # existing table stay additive / prefix-preserving) ---
    bigquery.SchemaField("firstname",                    "STRING"),
    bigquery.SchemaField("lastname",                     "STRING"),
    bigquery.SchemaField("company",                      "STRING"),
    bigquery.SchemaField("jobtitle",                     "STRING"),
    bigquery.SchemaField("hubspot_owner_id",             "STRING"),
    bigquery.SchemaField("hs_analytics_source_data_1",   "STRING"),
    bigquery.SchemaField("hs_analytics_source_data_2",   "STRING"),
    bigquery.SchemaField("first_conversion_event_name",  "STRING"),
    bigquery.SchemaField("first_conversion_date",        "TIMESTAMP"),
    bigquery.SchemaField("recent_conversion_event_name", "STRING"),
    bigquery.SchemaField("recent_conversion_date",       "TIMESTAMP"),
    bigquery.SchemaField("num_associated_deals",         "INTEGER"),
]

_DEAL_SCHEMA = [
    bigquery.SchemaField("deal_id",                  "STRING", mode="REQUIRED"),
    bigquery.SchemaField("dealname",                 "STRING"),
    bigquery.SchemaField("createdate",               "TIMESTAMP"),
    bigquery.SchemaField("closedate",                "TIMESTAMP"),
    bigquery.SchemaField("lastmodifieddate",         "TIMESTAMP"),
    bigquery.SchemaField("pipeline",                 "STRING"),   # opaque pipeline id
    bigquery.SchemaField("dealstage",                "STRING"),   # opaque stage id
    bigquery.SchemaField("dealtype",                 "STRING"),
    bigquery.SchemaField("is_closed",                "BOOLEAN"),
    bigquery.SchemaField("is_closed_won",            "BOOLEAN"),
    bigquery.SchemaField("amount",                   "FLOAT"),
    bigquery.SchemaField("amount_in_home_currency",  "FLOAT"),
    bigquery.SchemaField("deal_currency_code",       "STRING"),
    bigquery.SchemaField("forecast_amount",          "FLOAT"),
    bigquery.SchemaField("deal_stage_probability",   "FLOAT"),
    bigquery.SchemaField("hs_analytics_source",      "STRING"),
    bigquery.SchemaField("hs_analytics_source_data_1", "STRING"),
    bigquery.SchemaField("hs_analytics_source_data_2", "STRING"),
    bigquery.SchemaField("hs_analytics_latest_source", "STRING"),
    bigquery.SchemaField("hubspot_owner_id",         "STRING"),
    bigquery.SchemaField("days_to_close",            "INTEGER"),
    bigquery.SchemaField("num_associated_contacts",  "INTEGER"),
    bigquery.SchemaField("synced_at",                "TIMESTAMP"),
]

_EMAIL_SCHEMA = [
    bigquery.SchemaField("email_id",     "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name",         "STRING"),   # internal email name
    bigquery.SchemaField("subject",      "STRING"),
    bigquery.SchemaField("email_type",   "STRING"),   # BATCH_EMAIL, AB_EMAIL, AUTOMATED_EMAIL, ...
    bigquery.SchemaField("state",        "STRING"),   # PUBLISHED, DRAFT, ...
    bigquery.SchemaField("publish_date", "TIMESTAMP"),
    # Raw counters — rates are derived at read time (open/delivered etc.) to match
    # how contacts/deals store counts, not pre-computed percentages.
    bigquery.SchemaField("sent",         "INTEGER"),
    bigquery.SchemaField("delivered",    "INTEGER"),
    bigquery.SchemaField("opens",        "INTEGER"),
    bigquery.SchemaField("clicks",       "INTEGER"),
    bigquery.SchemaField("unsubscribed", "INTEGER"),
    bigquery.SchemaField("bounces",      "INTEGER"),
    bigquery.SchemaField("synced_at",    "TIMESTAMP"),
]

_CONTACT_TABLE = "fact_hubspot_contacts"
_DEAL_TABLE    = "fact_hubspot_deals"
_EMAIL_TABLE   = "fact_hubspot_emails"
_MQL_VIEW      = "fact_hubspot_mql_daily"
_DEALS_VIEW    = "fact_hubspot_deals_daily"


# ---------------------------------------------------------------------------
# Value coercion — HubSpot returns every property value as a string.
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> bool | None:
    if v in (None, ""):
        return None
    return str(v).strip().lower() == "true"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _token() -> str:
    t = (os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()
    if not t:
        raise RuntimeError("HUBSPOT_ACCESS_TOKEN is not set")
    return t


def _search(url: str, *, token: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
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


def _get_json(url: str, *, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET a HubSpot v3 endpoint (marketing email stats are read via GET)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_email_row(result: dict[str, Any], synced_at: str) -> dict[str, Any]:
    """Flatten one /marketing/v3/emails/statistics/list object.

    Counters live under stats.counters; we keep the raw counts the report needs
    and let the read side derive open/click/unsub rates over `delivered`.
    """
    counters = ((result.get("stats") or {}).get("counters")) or {}
    # publishDate is the send/publish timestamp; fall back to updated/created so a
    # row is never dropped for a missing field. HubSpot returns these as epoch ms
    # or ISO strings depending on age — BigQuery's TIMESTAMP load accepts both.
    published = (result.get("publishDate") or result.get("publishedAt")
                 or result.get("updated") or result.get("created") or None)
    return {
        "email_id":     str(result.get("id")),
        "name":         result.get("name") or None,
        "subject":      result.get("subject") or None,
        "email_type":   result.get("type") or None,
        "state":        result.get("state") or None,
        "publish_date": _epoch_ms_to_iso(published),
        "sent":         _to_int(counters.get("sent")),
        "delivered":    _to_int(counters.get("delivered")),
        "opens":        _to_int(counters.get("open")),
        "clicks":       _to_int(counters.get("click")),
        "unsubscribed": _to_int(counters.get("unsubscribed")),
        "bounces":      _to_int(counters.get("bounce")),
        "synced_at":    synced_at,
    }


def _epoch_ms_to_iso(v: Any) -> str | None:
    """Normalise a publish timestamp to an ISO string BigQuery can load.

    HubSpot returns epoch-millisecond integers (or numeric strings) on the stats
    endpoint; pass ISO strings through untouched."""
    if v in (None, ""):
        return None
    try:
        ms = int(v)
    except (TypeError, ValueError):
        return str(v)  # already an ISO-8601 string
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _parse_contact_row(result: dict[str, Any], synced_at: str, *, stage: str, date_property: str) -> dict[str, Any]:
    p = result.get("properties") or {}
    return {
        "contact_id":                  str(result["id"]),
        "createdate":                  p.get("createdate") or None,
        "lastmodifieddate":            p.get("lastmodifieddate") or None,
        "email":                       p.get("email") or None,
        "lifecyclestage":              p.get("lifecyclestage") or None,
        "stage_filter":                stage,
        "became_stage_date":           p.get(date_property) or None,
        "hs_lead_status":              p.get("hs_lead_status") or None,
        "hs_analytics_source":         p.get("hs_analytics_source") or None,
        "hs_latest_source":            p.get("hs_latest_source") or None,
        "hs_latest_source_data_1":     p.get("hs_latest_source_data_1") or None,
        "hs_latest_source_data_2":     p.get("hs_latest_source_data_2") or None,
        "utm_source":                  p.get("utm_source") or None,
        "utm_medium":                  p.get("utm_medium") or None,
        "utm_campaign":                p.get("utm_campaign") or None,
        "synced_at":                   synced_at,
        "firstname":                   p.get("firstname") or None,
        "lastname":                    p.get("lastname") or None,
        "company":                     p.get("company") or None,
        "jobtitle":                    p.get("jobtitle") or None,
        "hubspot_owner_id":            p.get("hubspot_owner_id") or None,
        "hs_analytics_source_data_1":  p.get("hs_analytics_source_data_1") or None,
        "hs_analytics_source_data_2":  p.get("hs_analytics_source_data_2") or None,
        "first_conversion_event_name": p.get("first_conversion_event_name") or None,
        "first_conversion_date":       p.get("first_conversion_date") or None,
        "recent_conversion_event_name": p.get("recent_conversion_event_name") or None,
        "recent_conversion_date":      p.get("recent_conversion_date") or None,
        "num_associated_deals":        _to_int(p.get("num_associated_deals")),
    }


def _parse_deal_row(result: dict[str, Any], synced_at: str) -> dict[str, Any]:
    p = result.get("properties") or {}
    return {
        "deal_id":                   str(result["id"]),
        "dealname":                  p.get("dealname") or None,
        "createdate":                p.get("createdate") or None,
        "closedate":                 p.get("closedate") or None,
        "lastmodifieddate":          p.get("hs_lastmodifieddate") or None,
        "pipeline":                  p.get("pipeline") or None,
        "dealstage":                 p.get("dealstage") or None,
        "dealtype":                  p.get("dealtype") or None,
        "is_closed":                 _to_bool(p.get("hs_is_closed")),
        "is_closed_won":             _to_bool(p.get("hs_is_closed_won")),
        "amount":                    _to_float(p.get("amount")),
        "amount_in_home_currency":   _to_float(p.get("amount_in_home_currency")),
        "deal_currency_code":        p.get("deal_currency_code") or None,
        "forecast_amount":           _to_float(p.get("hs_forecast_amount")),
        "deal_stage_probability":    _to_float(p.get("hs_deal_stage_probability")),
        "hs_analytics_source":       p.get("hs_analytics_source") or None,
        "hs_analytics_source_data_1": p.get("hs_analytics_source_data_1") or None,
        "hs_analytics_source_data_2": p.get("hs_analytics_source_data_2") or None,
        "hs_analytics_latest_source": p.get("hs_analytics_latest_source") or None,
        "hubspot_owner_id":          p.get("hubspot_owner_id") or None,
        "days_to_close":             _to_int(p.get("days_to_close")),
        "num_associated_contacts":   _to_int(p.get("num_associated_contacts")),
        "synced_at":                 synced_at,
    }


def _ensure_table(
    client: bigquery.Client,
    project: str,
    dataset: str,
    *,
    table_name: str,
    schema: list[bigquery.SchemaField],
    partition_field: str,
    clustering_fields: list[str],
) -> None:
    ref = f"{project}.{dataset}.{table_name}"
    # Add any columns the live table lacks without dropping data — create_table
    # (exists_ok) never alters an existing schema, so we update_table to append.
    try:
        existing = client.get_table(ref)
        existing_cols = {f.name for f in existing.schema}
        if not {f.name for f in schema}.issubset(existing_cols):
            obj = bigquery.Table(ref, schema=schema)
            client.update_table(obj, ["schema"])
            LOGGER.info("Updated %s schema with new columns", ref)
        return
    except Exception:
        pass  # table doesn't exist yet — create below
    tbl = bigquery.Table(ref, schema=schema)
    tbl.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field=partition_field,
    )
    tbl.clustering_fields = clustering_fields
    client.create_table(tbl, exists_ok=True)
    LOGGER.debug("Ensured table %s", ref)


def _upsert(
    client: bigquery.Client,
    project: str,
    dataset: str,
    *,
    table_name: str,
    schema: list[bigquery.SchemaField],
    id_col: str,
    rows: list[dict[str, Any]],
) -> None:
    stg_id = f"{project}.{dataset}._hs_stg_{uuid4().hex[:12]}"
    try:
        job = client.load_table_from_json(
            rows,
            stg_id,
            job_config=bigquery.LoadJobConfig(
                schema=schema,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            ),
        )
        job.result()
        LOGGER.info("Staged %d rows → %s", len(rows), stg_id)

        target  = f"`{project}.{dataset}.{table_name}`"
        staging = f"`{stg_id}`"
        upd_cols = [f.name for f in schema if f.name != id_col]
        set_clause  = ", ".join(f"{c} = S.{c}" for c in upd_cols)
        insert_cols = ", ".join(f.name for f in schema)
        insert_vals = ", ".join(f"S.{f.name}" for f in schema)

        merge_sql = f"""
MERGE {target} T
USING {staging} S ON T.{id_col} = S.{id_col}
WHEN MATCHED THEN
  UPDATE SET {set_clause}
WHEN NOT MATCHED THEN
  INSERT ({insert_cols}) VALUES ({insert_vals})
"""
        client.query(merge_sql).result()
        LOGGER.info("MERGE complete: %d rows upserted into %s", len(rows), table_name)
    finally:
        client.delete_table(stg_id, not_found_ok=True)


def _rebuild_contact_view(client: bigquery.Client, project: str, dataset: str) -> None:
    # Daily counts keyed on the date the contact ENTERED the filtered stage
    # (became_stage_date), falling back to last-modified for rows synced before
    # that column existed. Grouped by stage_filter so any funnel stage works.
    sql = f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.{_MQL_VIEW}` AS
SELECT
  DATE(COALESCE(became_stage_date, lastmodifieddate, createdate)) AS date,
  stage_filter,
  lifecyclestage,
  hs_lead_status,
  hs_analytics_source,
  hs_latest_source,
  utm_source,
  utm_medium,
  utm_campaign,
  COUNT(*)         AS contacts,
  MAX(synced_at)   AS latest_synced_at
FROM `{project}.{dataset}.{_CONTACT_TABLE}`
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
"""
    client.query(sql).result()
    LOGGER.info("Rebuilt view %s.%s.%s", project, dataset, _MQL_VIEW)


def _rebuild_deal_view(client: bigquery.Client, project: str, dataset: str) -> None:
    # Deals keyed on create date + original source, with won revenue broken out
    # via the reliable is_closed_won boolean (dealstage is an opaque id).
    sql = f"""
CREATE OR REPLACE VIEW `{project}.{dataset}.{_DEALS_VIEW}` AS
SELECT
  DATE(createdate)             AS created_date,
  DATE(closedate)              AS close_date,
  pipeline,
  dealtype,
  hs_analytics_source          AS original_source,
  is_closed,
  is_closed_won,
  COUNT(*)                     AS deals,
  SUM(amount)                  AS amount,
  SUM(IF(is_closed_won, amount, 0)) AS won_amount,
  MAX(synced_at)               AS latest_synced_at
FROM `{project}.{dataset}.{_DEAL_TABLE}`
GROUP BY 1, 2, 3, 4, 5, 6, 7
"""
    client.query(sql).result()
    LOGGER.info("Rebuilt view %s.%s.%s", project, dataset, _DEALS_VIEW)


def _resolve_target(project_id: str | None, dataset_id: str | None) -> tuple[str, str]:
    project = (project_id or os.getenv("HUBSPOT_SYNC_PROJECT_ID") or "").strip()
    dataset = (dataset_id or os.getenv("HUBSPOT_SYNC_DATASET") or "marketing_marts").strip()
    if not project:
        raise RuntimeError("BQ project not set — provide project_id or set HUBSPOT_SYNC_PROJECT_ID")
    return project, dataset


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def sync_hubspot_contacts(
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    lifecycle_stage: str | None = None,
    access_token: str | None = None,
    bq_client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Backfill contacts that reached a lifecycle stage into BigQuery.

    Pulls every contact whose "date entered <stage>" falls within the lookback
    window — so MQL means "is or has been an MQL", even if they've since
    progressed — and upserts them into fact_hubspot_contacts.
    """
    project, dataset = _resolve_target(project_id, dataset_id)
    stage = normalize_stage(lifecycle_stage)
    date_property = _STAGE_DATE_PROP[stage]
    props = _HS_PROPS + [date_property]

    token  = (access_token or "").strip() or _token()
    client = bq_client or bigquery_service.build_client(project_id=project)
    _ensure_table(
        client, project, dataset,
        table_name=_CONTACT_TABLE, schema=_CONTACT_SCHEMA,
        partition_field="lastmodifieddate", clustering_fields=["lifecyclestage", "contact_id"],
    )

    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp() * 1000)
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    LOGGER.info("HubSpot contacts sync — stage=%s, lookback=%dd, project=%s, dataset=%s",
                stage, lookback_days, project, dataset)
    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []
    after: str | None = None
    pages = 0

    while True:
        body: dict[str, Any] = {
            "filterGroups": [{"filters": [
                {"propertyName": date_property, "operator": "GTE", "value": str(cutoff_ms)},
            ]}],
            "properties": props,
            "limit": _PAGE_SIZE,
        }
        if after:
            body["after"] = after
        try:
            resp = _search(_CONTACTS_SEARCH_URL, token=token, body=body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HubSpot search API {exc.code}: {detail[:400]}") from exc

        pages += 1
        batch = resp.get("results") or []
        rows += [_parse_contact_row(r, synced_at, stage=stage, date_property=date_property) for r in batch]
        LOGGER.info("Contacts page %d: %d results (%d total)", pages, len(batch), len(rows))

        next_after = ((resp.get("paging") or {}).get("next") or {}).get("after")
        if not next_after or not batch:
            break
        after = next_after
        time.sleep(_RATE_DELAY)

    if rows:
        _upsert(client, project, dataset, table_name=_CONTACT_TABLE,
                schema=_CONTACT_SCHEMA, id_col="contact_id", rows=rows)
    else:
        LOGGER.info("No contacts reached %s in the last %dd — nothing to upsert", stage, lookback_days)

    _rebuild_contact_view(client, project, dataset)

    summary = {
        "status": "ok",
        "rows_synced": len(rows),
        "pages": pages,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "synced_at": synced_at,
    }
    LOGGER.info("HubSpot contacts sync done: %s", summary)
    return summary


def sync_hubspot_deals(
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    access_token: str | None = None,
    bq_client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Backfill deals created OR closed within the lookback window.

    Two OR'd filter groups: deals created in the window (new pipeline) plus deals
    that actually closed in the window (won or lost) regardless of when they were
    created — so recent revenue on older deals is captured. Upserts into
    fact_hubspot_deals.
    """
    project, dataset = _resolve_target(project_id, dataset_id)
    token  = (access_token or "").strip() or _token()
    client = bq_client or bigquery_service.build_client(project_id=project)
    _ensure_table(
        client, project, dataset,
        table_name=_DEAL_TABLE, schema=_DEAL_SCHEMA,
        partition_field="createdate", clustering_fields=["pipeline", "dealstage"],
    )

    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp() * 1000)
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    LOGGER.info("HubSpot deals sync — lookback=%dd, project=%s, dataset=%s",
                lookback_days, project, dataset)
    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []
    after: str | None = None
    pages = 0

    while True:
        body: dict[str, Any] = {
            "filterGroups": [
                {"filters": [
                    {"propertyName": "createdate", "operator": "GTE", "value": str(cutoff_ms)},
                ]},
                {"filters": [
                    {"propertyName": "closedate", "operator": "GTE", "value": str(cutoff_ms)},
                    {"propertyName": "hs_is_closed", "operator": "EQ", "value": "true"},
                ]},
            ],
            "properties": _DEAL_PROPS,
            "limit": _PAGE_SIZE,
        }
        if after:
            body["after"] = after
        try:
            resp = _search(_DEALS_SEARCH_URL, token=token, body=body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HubSpot deals search API {exc.code}: {detail[:400]}") from exc

        pages += 1
        batch = resp.get("results") or []
        rows += [_parse_deal_row(r, synced_at) for r in batch]
        LOGGER.info("Deals page %d: %d results (%d total)", pages, len(batch), len(rows))

        next_after = ((resp.get("paging") or {}).get("next") or {}).get("after")
        if not next_after or not batch:
            break
        after = next_after
        time.sleep(_RATE_DELAY)

    if rows:
        _upsert(client, project, dataset, table_name=_DEAL_TABLE,
                schema=_DEAL_SCHEMA, id_col="deal_id", rows=rows)
    else:
        LOGGER.info("No deals created/closed in the last %dd — nothing to upsert", lookback_days)

    _rebuild_deal_view(client, project, dataset)

    summary = {
        "status": "ok",
        "rows_synced": len(rows),
        "pages": pages,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "synced_at": synced_at,
    }
    LOGGER.info("HubSpot deals sync done: %s", summary)
    return summary


def sync_hubspot_emails(
    *,
    project_id: str | None = None,
    dataset_id: str | None = None,
    access_token: str | None = None,
    bq_client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Backfill marketing-email performance into fact_hubspot_emails.

    Pulls every marketing email plus its post-send counters (sent, delivered,
    opens, clicks, unsubscribes, bounces) from /marketing/v3/emails/statistics/list
    and upserts one row per email. The full list is refreshed each run so stats on
    older sends keep climbing as engagement trickles in.

    Requires the `content` OAuth scope, which is only granted on the matching
    Marketing Hub tier. A portal without it returns 403 — that's expected for
    non-Pro clients, so we return status="skipped" instead of raising, letting the
    contacts/deals sync succeed on its own.
    """
    project, dataset = _resolve_target(project_id, dataset_id)
    token  = (access_token or "").strip() or _token()
    client = bq_client or bigquery_service.build_client(project_id=project)

    now = datetime.now(timezone.utc)
    synced_at = now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    # Epoch-millisecond window (see _EMAIL_STATS_LOOKBACK_DAYS). end is nudged a
    # day into the future so a portal-vs-UTC clock skew never clips the newest send.
    start_ms = int((now - timedelta(days=_EMAIL_STATS_LOOKBACK_DAYS)).timestamp() * 1000)
    end_ms   = int((now + timedelta(days=1)).timestamp() * 1000)

    LOGGER.info("HubSpot emails sync — project=%s, dataset=%s", project, dataset)
    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    pages = 0

    while True:
        params: dict[str, Any] = {
            "startTimestamp": start_ms,
            "endTimestamp": end_ms,
            "limit": limit,
            "offset": offset,
        }
        try:
            resp = _get_json(_EMAILS_STATS_URL, token=token, params=params)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # Missing `content` scope / not on a Marketing tier that exposes
                # email stats. Expected for non-Pro clients — skip, don't fail.
                LOGGER.info("HubSpot email stats unavailable (HTTP %s) — skipping "
                            "email sync for this portal", exc.code)
                return {"status": "skipped", "reason": f"http_{exc.code}",
                        "rows_synced": 0, "elapsed_s": round(time.monotonic() - t0, 1)}
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HubSpot email stats API {exc.code}: {detail[:400]}") from exc

        pages += 1
        # Results have appeared under "results" and, on older variants, "objects" —
        # accept either so a shape change doesn't silently blank the report.
        batch = resp.get("results") or resp.get("objects") or []
        rows += [_parse_email_row(r, synced_at) for r in batch]
        LOGGER.info("Emails page %d (offset %d): %d results (%d total)",
                    pages, offset, len(batch), len(rows))

        # statistics/list is offset-paginated. Stop on a short/empty page; also
        # honour a cursor if one is ever present, and hard-cap pages so a quirky
        # response can never loop forever.
        next_after = ((resp.get("paging") or {}).get("next") or {}).get("after")
        if len(batch) < limit and not next_after:
            break
        if pages >= 100:
            LOGGER.warning("HubSpot emails sync hit page cap (%d) — stopping", pages)
            break
        offset += limit
        time.sleep(_RATE_DELAY)

    _ensure_table(
        client, project, dataset,
        table_name=_EMAIL_TABLE, schema=_EMAIL_SCHEMA,
        partition_field="publish_date", clustering_fields=["email_type", "email_id"],
    )
    if rows:
        _upsert(client, project, dataset, table_name=_EMAIL_TABLE,
                schema=_EMAIL_SCHEMA, id_col="email_id", rows=rows)
    else:
        LOGGER.info("No marketing emails returned — nothing to upsert")

    summary = {
        "status": "ok",
        "rows_synced": len(rows),
        "pages": pages,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "synced_at": synced_at,
    }
    LOGGER.info("HubSpot emails sync done: %s", summary)
    return summary
