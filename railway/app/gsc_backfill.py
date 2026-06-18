"""One-time (rerunnable) GSC → BigQuery historical backfill.

Pulls daily Search Console data via the Searchanalytics API and upserts
it into BigQuery using a staging-table MERGE, so reruns are safe.

Target table: marketing_marts.fact_gsc_url_daily
This is the same table read by bq_gsc_service.py in the dashboard.

Usage
-----
    python gsc_backfill.py [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--dry-run]

Required env vars
-----------------
    GCP_CREDS_PENN_BASE64   base64 service account JSON with Search Console +
                            BigQuery write access.
    GSC_SITE_URL            The Search Console property identifier.
                            Domain property:  sc-domain:penncommunitybank.com
                            URL-prefix:       https://www.penncommunitybank.com/

Optional env vars
-----------------
    GSC_BQ_PROJECT_ID       GCP project   (default: penn-community-b-1699391543298)
    GSC_BQ_DATASET_ID       BQ dataset    (default: marketing_marts)
    GSC_BQ_TABLE            BQ table      (default: fact_gsc_url_daily)

Notes
-----
- GSC retains ~16 months of data; default start is 480 days ago.
- GSC data has a 3-day lag; end_date is capped at today - 3.
- Dimensions: date, query, page (country/device excluded — not used by the dashboard).
- organic_sum_position stores (position - 1) * impressions for correct weighted-avg
  rollup: SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1
- Anonymized queries (empty string from GSC) are stored as NULL with is_anonymized_query=TRUE.
- MERGE key: (date, page_url, IFNULL(query, '')).
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
_DEFAULT_TABLE   = "fact_gsc_url_daily"

_GSC_SCOPE      = "https://www.googleapis.com/auth/webmasters.readonly"
_BQ_SCOPE       = "https://www.googleapis.com/auth/bigquery"
_GSC_LAG_DAYS   = 3
_MAX_DAYS       = 480       # ~16 months
_ROW_LIMIT      = 25_000    # GSC API max per page
_RETRY_SLEEP    = 60        # seconds to wait after a 429


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
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def fetch_day(gsc_creds, site_url: str, day: date) -> list[dict[str, Any]]:
    """Fetch all rows for a single day, paginating with startRow.

    Dimensions: date, query, page  (no country/device — dashboard doesn't use them).
    Returns rows ready for upsert_day().
    """
    rows: list[dict[str, Any]] = []
    start_row = 0

    while True:
        token = _token(gsc_creds)
        body = {
            "startDate":  day.isoformat(),
            "endDate":    day.isoformat(),
            "dimensions": ["date", "query", "page"],
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
            # keys order matches dimensions: date, query, page
            raw_query   = keys[1] if len(keys) > 1 else ""
            query       = raw_query.strip() if raw_query else ""
            impressions = int(r.get("impressions") or 0)
            position    = float(r.get("position") or 0)
            rows.append({
                "date":               keys[0] if keys else day.isoformat(),
                "page_url":           keys[2] if len(keys) > 2 else "",
                "query":              query or None,          # anonymized → NULL
                "organic_clicks":     int(r.get("clicks") or 0),
                "organic_impressions": impressions,
                # (position - 1) * impressions → correct weighted avg via
                # SAFE_DIVIDE(SUM(organic_sum_position), SUM(organic_impressions)) + 1
                "organic_sum_position": (position - 1.0) * impressions,
                "is_anonymized_query":  not bool(query),
            })

        if len(batch) < _ROW_LIMIT:
            break
        start_row += _ROW_LIMIT

    return rows


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

def _project_id() -> str:
    return (os.getenv("GSC_BQ_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _dataset_id() -> str:
    return (os.getenv("GSC_BQ_DATASET_ID") or _DEFAULT_DATASET).strip()


def _full_table_id() -> str:
    table = (os.getenv("GSC_BQ_TABLE") or _DEFAULT_TABLE).strip()
    return f"{_project_id()}.{_dataset_id()}.{table}"


def _schema():
    from google.cloud import bigquery
    return [
        bigquery.SchemaField("date",                "DATE",      mode="REQUIRED"),
        bigquery.SchemaField("page_url",            "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("query",               "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("organic_clicks",      "INT64",     mode="REQUIRED"),
        bigquery.SchemaField("organic_impressions", "INT64",     mode="REQUIRED"),
        bigquery.SchemaField("organic_sum_position","FLOAT64",   mode="REQUIRED"),
        bigquery.SchemaField("is_anonymized_query", "BOOL",      mode="REQUIRED"),
        bigquery.SchemaField("synced_at",           "TIMESTAMP", mode="REQUIRED"),
    ]


def _bq_client():
    from google.cloud import bigquery
    from google.oauth2 import service_account
    from ga4_credentials import load_service_account_info_from_env

    info = load_service_account_info_from_env("GCP_CREDS_PENN_BASE64", require_base64=True)
    creds = service_account.Credentials.from_service_account_info(info, scopes=[_BQ_SCOPE])
    return bigquery.Client(project=_project_id(), credentials=creds)


def _ensure_table(client) -> str:
    from google.cloud import bigquery

    project_id = _project_id()
    dataset_id = _dataset_id()
    table_id   = _full_table_id()

    client.create_dataset(
        bigquery.Dataset(f"{project_id}.{dataset_id}"), exists_ok=True
    )
    table = bigquery.Table(table_id, schema=_schema())
    table.time_partitioning = bigquery.TimePartitioning(field="date")
    table.clustering_fields = ["page_url"]
    client.create_table(table, exists_ok=True)
    return table_id


def upsert_day(client, table_id: str, rows: list[dict[str, Any]], dry_run: bool = False) -> int:
    """Upsert one day's rows via staging table MERGE. Returns row count."""
    if not rows:
        return 0

    from google.cloud import bigquery

    synced_at = datetime.now(timezone.utc).isoformat()

    payload = [
        {
            "date":                str(r["date"])[:10],
            "page_url":            str(r["page_url"]),
            "query":               r["query"],              # may be None
            "organic_clicks":      int(r["organic_clicks"]),
            "organic_impressions": int(r["organic_impressions"]),
            "organic_sum_position": float(r["organic_sum_position"]),
            "is_anonymized_query": bool(r["is_anonymized_query"]),
            "synced_at":           synced_at,
        }
        for r in rows
    ]

    if dry_run:
        return len(payload)

    temp_id    = f"{table_id}_stg_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(
        schema=_schema(), write_disposition="WRITE_TRUNCATE"
    )
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()

    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON  T.date                     = S.date
    AND T.page_url                 = S.page_url
    AND IFNULL(T.query, '')        = IFNULL(S.query, '')
    WHEN MATCHED THEN UPDATE SET
      organic_clicks       = S.organic_clicks,
      organic_impressions  = S.organic_impressions,
      organic_sum_position = S.organic_sum_position,
      is_anonymized_query  = S.is_anonymized_query,
      synced_at            = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      date, page_url, query,
      organic_clicks, organic_impressions, organic_sum_position,
      is_anonymized_query, synced_at
    ) VALUES (
      S.date, S.page_url, S.query,
      S.organic_clicks, S.organic_impressions, S.organic_sum_position,
      S.is_anonymized_query, S.synced_at
    )
    """
    try:
        client.query(merge_sql).result()
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
        description="Backfill Google Search Console data into BigQuery (marketing_marts.fact_gsc_url_daily)"
    )
    parser.add_argument("--start-date", metavar="YYYY-MM-DD",
                        help=f"First date to fetch (default: {_MAX_DAYS} days ago)")
    parser.add_argument("--end-date",   metavar="YYYY-MM-DD",
                        help=f"Last date to fetch (default: today - {_GSC_LAG_DAYS} days)")
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

    total_days = (end_date - start_date).days + 1
    print(
        f"GSC backfill: {start_date} → {end_date} ({total_days} days)\n"
        f"  site     : {site_url}\n"
        f"  target   : {_full_table_id()}\n"
        f"  dry_run  : {args.dry_run}\n"
    )

    print("Loading credentials...")
    gsc_creds = _load_creds([_GSC_SCOPE])

    print("Connecting to BigQuery...")
    bq = _bq_client()
    table_id = _ensure_table(bq)
    if not args.dry_run:
        print(f"Table ready: {table_id}\n")

    grand_total = 0
    for i, day in enumerate(_iter_dates(start_date, end_date), 1):
        label = f"[{i:>4}/{total_days}] {day}"
        print(f"{label} ... ", end="", flush=True)
        try:
            rows    = fetch_day(gsc_creds, site_url, day)
            written = upsert_day(bq, table_id, rows, dry_run=args.dry_run)
            grand_total += written
            print(f"{len(rows):>6} rows  ({'dry' if args.dry_run else 'upserted'})")
        except Exception as exc:
            print(f"ERROR: {exc}")
            # log and continue — reruns will fill gaps

    print(f"\nFinished. Total rows {'(dry) ' if args.dry_run else ''}written: {grand_total:,}")


if __name__ == "__main__":
    main()
