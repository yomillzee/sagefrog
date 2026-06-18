"""One-time (rerunnable) GSC → BigQuery historical backfill.

Writes to TWO small tables instead of one giant cross-dimension table:

  fact_gsc_query_daily  — one row per (date, query)  ← ~150K rows for 480 days
  fact_gsc_page_daily   — one row per (date, page)   ← ~70K rows for 480 days

Why two tables?
  Storing (date × query × page) would produce millions of rows for even a
  modest site. The dashboard only needs query performance and page performance
  independently — it never needs to join them at row level.

Usage
-----
    python gsc_backfill.py [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
                           [--table {both,queries,pages}] [--dry-run]

Required env vars
-----------------
    GCP_CREDS_PENN_BASE64   base64 service account JSON
    GSC_SITE_URL            sc-domain:penncommunitybank.com  or  https://www.example.com/

Optional env vars
-----------------
    GSC_BQ_PROJECT_ID       GCP project  (default: penn-community-b-1699391543298)
    GSC_BQ_DATASET_ID       BQ dataset   (default: marketing_marts)

Notes
-----
- GSC retains ~16 months; default start is 480 days ago.
- GSC data has a 3-day lag; end_date is capped at today − 3.
- organic_sum_position stores (position − 1) × impressions for correct
  weighted-avg rollup: SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1
- Reruns are safe: each day uses a MERGE so existing rows are updated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_DATASET = "marketing_marts"
_QUERY_TABLE     = "fact_gsc_query_daily"
_PAGE_TABLE      = "fact_gsc_page_daily"

_GSC_SCOPE    = "https://www.googleapis.com/auth/webmasters.readonly"
_BQ_SCOPE     = "https://www.googleapis.com/auth/bigquery"
_GSC_LAG_DAYS = 3
_MAX_DAYS     = 480
_ROW_LIMIT    = 25_000   # GSC API max per page
_RETRY_SLEEP  = 60


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _load_creds(scopes: list[str]):
    from google.oauth2 import service_account
    from ga4_credentials import load_service_account_info_from_env
    info = load_service_account_info_from_env("GCP_CREDS_PENN_BASE64", require_base64=True)
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def _token(creds) -> str:
    import google.auth.transport.requests
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    return creds.token


# ---------------------------------------------------------------------------
# GSC API
# ---------------------------------------------------------------------------

def _gsc_post(token: str, site_url: str, body: dict[str, Any]) -> dict[str, Any]:
    encoded = urllib.parse.quote(site_url, safe="")
    url = (
        f"https://www.googleapis.com/webmasters/v3/sites/{encoded}"
        "/searchAnalytics/query"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _fetch_dimension(gsc_creds, site_url: str, day: date, dimension: str) -> list[dict[str, Any]]:
    """Fetch all rows for one day and one dimension (query OR page), paginating."""
    rows: list[dict[str, Any]] = []
    start_row = 0
    while True:
        token = _token(gsc_creds)
        body = {
            "startDate":  day.isoformat(),
            "endDate":    day.isoformat(),
            "dimensions": ["date", dimension],
            "rowLimit":   _ROW_LIMIT,
            "startRow":   start_row,
            "dataState":  "final",
        }
        try:
            data = _gsc_post(token, site_url, body)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                print(f"\n    rate-limited — sleeping {_RETRY_SLEEP}s", flush=True)
                time.sleep(_RETRY_SLEEP)
                continue
            raise
        batch = data.get("rows") or []
        if not batch:
            break
        for r in batch:
            keys = r.get("keys") or []
            impressions = int(r.get("impressions") or 0)
            position    = float(r.get("position") or 0)
            if dimension == "query":
                raw_query = keys[1] if len(keys) > 1 else ""
                query = raw_query.strip() if raw_query else ""
                rows.append({
                    "date":                day.isoformat(),
                    "query":               query or None,
                    "organic_clicks":      int(r.get("clicks") or 0),
                    "organic_impressions": impressions,
                    "organic_sum_position": (position - 1.0) * impressions,
                    "is_anonymized_query":  not bool(query),
                })
            else:  # page
                rows.append({
                    "date":                day.isoformat(),
                    "page_url":            keys[1] if len(keys) > 1 else "",
                    "organic_clicks":      int(r.get("clicks") or 0),
                    "organic_impressions": impressions,
                    "organic_sum_position": (position - 1.0) * impressions,
                })
        if len(batch) < _ROW_LIMIT:
            break
        start_row += _ROW_LIMIT
    return rows


# ---------------------------------------------------------------------------
# BigQuery helpers
# ---------------------------------------------------------------------------

def _project_id() -> str:
    return (os.getenv("GSC_BQ_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _dataset_id() -> str:
    return (os.getenv("GSC_BQ_DATASET_ID") or _DEFAULT_DATASET).strip()


def _query_schema():
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


def _page_schema():
    from google.cloud import bigquery as bq
    return [
        bq.SchemaField("date",                 "DATE",      mode="REQUIRED"),
        bq.SchemaField("page_url",             "STRING",    mode="REQUIRED"),
        bq.SchemaField("organic_clicks",       "INT64",     mode="REQUIRED"),
        bq.SchemaField("organic_impressions",  "INT64",     mode="REQUIRED"),
        bq.SchemaField("organic_sum_position", "FLOAT64",   mode="REQUIRED"),
        bq.SchemaField("synced_at",            "TIMESTAMP", mode="REQUIRED"),
    ]


def _bq_client():
    from google.cloud import bigquery as bq
    from google.oauth2 import service_account
    from ga4_credentials import load_service_account_info_from_env
    info = load_service_account_info_from_env("GCP_CREDS_PENN_BASE64", require_base64=True)
    creds = service_account.Credentials.from_service_account_info(info, scopes=[_BQ_SCOPE])
    return bq.Client(project=_project_id(), credentials=creds)


def _ensure_tables(client) -> tuple[str, str]:
    """Create dataset + both tables if they don't exist. Returns (query_id, page_id)."""
    from google.cloud import bigquery as bq

    proj    = _project_id()
    dataset = _dataset_id()
    query_id = f"{proj}.{dataset}.{_QUERY_TABLE}"
    page_id  = f"{proj}.{dataset}.{_PAGE_TABLE}"

    client.create_dataset(bq.Dataset(f"{proj}.{dataset}"), exists_ok=True)

    for table_id, schema in [(query_id, _query_schema()), (page_id, _page_schema())]:
        t = bq.Table(table_id, schema=schema)
        t.time_partitioning = bq.TimePartitioning(
            type_=bq.TimePartitioningType.DAY, field="date"
        )
        t.clustering_fields = ["query" if "query" in table_id else "page_url"]
        client.create_table(t, exists_ok=True)

    return query_id, page_id


def _upsert(client, table_id: str, rows: list[dict[str, Any]],
            schema, merge_key_sql: str, dry_run: bool = False) -> int:
    """Load rows into a staging table then MERGE into target. Returns row count."""
    if not rows:
        return 0
    from google.cloud import bigquery as bq

    synced_at = datetime.now(timezone.utc).isoformat()
    payload = [{**r, "synced_at": synced_at} for r in rows]

    if dry_run:
        return len(payload)

    temp_id    = f"{table_id}_stg_{uuid4().hex}"
    job_config = bq.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()

    # Build SET clause from all non-key columns
    key_cols = {c.strip() for c in merge_key_sql.replace("T.", "").replace("S.", "").split("AND")}
    set_cols  = [f.name for f in schema if f.name not in {"synced_at"} and f.name not in key_cols]
    set_clause = ",\n      ".join(f"{c} = S.{c}" for c in set_cols) + ",\n      synced_at = S.synced_at"
    ins_cols   = ", ".join(f.name for f in schema)
    ins_vals   = ", ".join(f"S.{f.name}" for f in schema)

    sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON {merge_key_sql}
    WHEN MATCHED THEN UPDATE SET
      {set_clause}
    WHEN NOT MATCHED THEN INSERT ({ins_cols})
    VALUES ({ins_vals})
    """
    try:
        client.query(sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)

    return len(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _iter_dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill GSC data into BigQuery (two lean tables)"
    )
    parser.add_argument("--start-date", metavar="YYYY-MM-DD")
    parser.add_argument("--end-date",   metavar="YYYY-MM-DD")
    parser.add_argument(
        "--table", choices=["both", "queries", "pages"], default="both",
        help="Which table(s) to populate (default: both)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch from GSC but skip BigQuery writes")
    args = parser.parse_args()

    today      = date.today()
    lag_cutoff = today - timedelta(days=_GSC_LAG_DAYS)

    end_date = date.fromisoformat(args.end_date) if args.end_date else lag_cutoff
    if end_date > lag_cutoff:
        print(f"WARNING: capping end_date to {lag_cutoff} (GSC {_GSC_LAG_DAYS}-day lag)")
        end_date = lag_cutoff

    start_date = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else today - timedelta(days=_MAX_DAYS)
    )

    site_url = os.getenv("GSC_SITE_URL", "").strip()
    if not site_url:
        sys.exit(
            "ERROR: GSC_SITE_URL is not set.\n"
            "  Domain property : GSC_SITE_URL=sc-domain:penncommunitybank.com\n"
            "  URL-prefix      : GSC_SITE_URL=https://www.penncommunitybank.com/"
        )

    do_queries = args.table in ("both", "queries")
    do_pages   = args.table in ("both", "pages")
    total_days = (end_date - start_date).days + 1

    print(
        f"GSC backfill: {start_date} → {end_date} ({total_days} days)\n"
        f"  site    : {site_url}\n"
        f"  tables  : {'queries + pages' if args.table == 'both' else args.table}\n"
        f"  project : {_project_id()}.{_dataset_id()}\n"
        f"  dry_run : {args.dry_run}\n"
    )

    print("Loading credentials...")
    gsc_creds = _load_creds([_GSC_SCOPE])

    print("Connecting to BigQuery...")
    bq_client = _bq_client()
    query_table_id, page_table_id = _ensure_tables(bq_client)
    print(f"  query table : {query_table_id}")
    print(f"  page  table : {page_table_id}\n")

    q_schema = _query_schema()
    p_schema = _page_schema()
    q_merge  = "T.date = S.date AND IFNULL(T.query, '') = IFNULL(S.query, '')"
    p_merge  = "T.date = S.date AND T.page_url = S.page_url"

    grand_q = grand_p = 0

    for i, day in enumerate(_iter_dates(start_date, end_date), 1):
        label = f"[{i:>4}/{total_days}] {day}"
        print(f"{label}", end="", flush=True)

        q_count = p_count = 0
        err_q = err_p = ""

        if do_queries:
            try:
                q_rows  = _fetch_dimension(gsc_creds, site_url, day, "query")
                q_count = _upsert(bq_client, query_table_id, q_rows, q_schema, q_merge, args.dry_run)
                grand_q += q_count
            except Exception as exc:
                err_q = f"  QUERY ERR: {exc}"

        if do_pages:
            try:
                p_rows  = _fetch_dimension(gsc_creds, site_url, day, "page")
                p_count = _upsert(bq_client, page_table_id, p_rows, p_schema, p_merge, args.dry_run)
                grand_p += p_count
            except Exception as exc:
                err_p = f"  PAGE ERR: {exc}"

        suffix = f"  q:{q_count:>5}  p:{p_count:>4}{'  DRY' if args.dry_run else ''}{err_q}{err_p}"
        print(suffix)

    print(
        f"\nFinished.\n"
        f"  Query rows {'(dry) ' if args.dry_run else ''}written : {grand_q:,}\n"
        f"  Page  rows {'(dry) ' if args.dry_run else ''}written : {grand_p:,}"
    )


if __name__ == "__main__":
    main()
