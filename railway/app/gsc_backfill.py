"""One-time (rerunnable) GSC → BigQuery historical backfill.

Pulls daily Search Console data via the Searchanalytics API and upserts
it into BigQuery using a staging-table MERGE, so reruns are safe.

Usage
-----
    python gsc_backfill.py [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--dry-run]

Required env vars
-----------------
    GCP_CREDS_PENN_BASE64   base64 service account JSON with Search Console read access.
                            The service account must be added as a user in the GSC property.
    GSC_SITE_URL            The Search Console property identifier.
                            Domain property:  sc-domain:penncommunitybank.com
                            URL-prefix:       https://www.penncommunitybank.com/

Optional env vars
-----------------
    GSC_BQ_PROJECT_ID       GCP project   (default: penn-community-b-1699391543298)
    GSC_BQ_DATASET_ID       BQ dataset    (default: gsc_data)
    GSC_BQ_TABLE            BQ table      (default: url_impressions_daily)

Notes
-----
- GSC retains ~16 months of data; default start is 480 days ago.
- GSC data has a 3-day lag; end_date is capped at today - 3.
- Dimensions: date, query, page, country, device.
- position is stored 1-indexed as returned by the API.
- Anonymized queries (empty string from GSC) are stored as NULL with is_anonymized=TRUE.
- MERGE key: (site_url, date, page, query, country, device).
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
_DEFAULT_DATASET = "gsc_data"
_DEFAULT_TABLE   = "url_impressions_daily"

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
    """Fetch all rows for a single day, paginating with startRow."""
    rows: list[dict[str, Any]] = []
    start_row = 0

    while True:
        token = _token(gsc_creds)
        body = {
            "startDate": day.isoformat(),
            "endDate":   day.isoformat(),
            "dimensions": ["date", "query", "page", "country", "device"],
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
            # keys order matches dimensions: date, query, page, country, device
            raw_query = keys[1] if len(keys) > 1 else ""
            query = raw_query.strip() if raw_query else ""
            rows.append({
                "site_url":     site_url,
                "date":         keys[0] if keys else day.isoformat(),
                "query":        query or None,      # store anonymized as NULL
                "page":         keys[2] if len(keys) > 2 else "",
                "country":      keys[3] if len(keys) > 3 else "",
                "device":       keys[4] if len(keys) > 4 else "",
                "clicks":       int(r.get("clicks")      or 0),
                "impressions":  int(r.get("impressions") or 0),
                "ctr":          float(r.get("ctr")       or 0),
                "position":     float(r.get("position")  or 0),
                "is_anonymized": not bool(query),
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
        bigquery.SchemaField("site_url",      "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("date",          "DATE",      mode="REQUIRED"),
        bigquery.SchemaField("query",         "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("page",          "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("country",       "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("device",        "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("clicks",        "INT64",     mode="REQUIRED"),
        bigquery.SchemaField("impressions",   "INT64",     mode="REQUIRED"),
        bigquery.SchemaField("ctr",           "FLOAT64",   mode="REQUIRED"),
        bigquery.SchemaField("position",      "FLOAT64",   mode="REQUIRED"),
        bigquery.SchemaField("is_anonymized", "BOOL",      mode="REQUIRED"),
        bigquery.SchemaField("synced_at",     "TIMESTAMP", mode="REQUIRED"),
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

    project_id  = _project_id()
    dataset_id  = _dataset_id()
    table_id    = _full_table_id()

    client.create_dataset(
        bigquery.Dataset(f"{project_id}.{dataset_id}"), exists_ok=True
    )
    table = bigquery.Table(table_id, schema=_schema())
    table.time_partitioning = bigquery.TimePartitioning(field="date")
    table.clustering_fields = ["site_url", "country", "device"]
    client.create_table(table, exists_ok=True)
    return table_id


def upsert_day(client, rows: list[dict[str, Any]], dry_run: bool = False) -> int:
    """Upsert one day's rows via staging table MERGE. Returns row count."""
    if not rows:
        return 0

    from google.cloud import bigquery

    schema      = _schema()
    table_id    = _ensure_table(client)
    synced_at   = datetime.now(timezone.utc).isoformat()

    payload = [
        {
            "site_url":     str(r["site_url"]),
            "date":         str(r["date"])[:10],
            "query":        r["query"],          # may be None
            "page":         str(r["page"]),
            "country":      str(r.get("country") or ""),
            "device":       str(r.get("device")  or ""),
            "clicks":       int(r["clicks"]),
            "impressions":  int(r["impressions"]),
            "ctr":          float(r["ctr"]),
            "position":     float(r["position"]),
            "is_anonymized": bool(r["is_anonymized"]),
            "synced_at":    synced_at,
        }
        for r in rows
    ]

    if dry_run:
        return len(payload)

    temp_id    = f"{table_id}_staging_{uuid4().hex}"
    job_config = bigquery.LoadJobConfig(
        schema=schema, write_disposition="WRITE_TRUNCATE"
    )
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()

    # IFNULL guards handle NULL query values in the ON clause
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON  T.site_url              = S.site_url
    AND T.date                  = S.date
    AND T.page                  = S.page
    AND IFNULL(T.query,   '')   = IFNULL(S.query,   '')
    AND IFNULL(T.country, '')   = IFNULL(S.country, '')
    AND IFNULL(T.device,  '')   = IFNULL(S.device,  '')
    WHEN MATCHED THEN UPDATE SET
      clicks        = S.clicks,
      impressions   = S.impressions,
      ctr           = S.ctr,
      position      = S.position,
      is_anonymized = S.is_anonymized,
      synced_at     = S.synced_at
    WHEN NOT MATCHED THEN INSERT (
      site_url, date, query, page, country, device,
      clicks, impressions, ctr, position, is_anonymized, synced_at
    ) VALUES (
      S.site_url, S.date, S.query, S.page, S.country, S.device,
      S.clicks, S.impressions, S.ctr, S.position, S.is_anonymized, S.synced_at
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
        description="Backfill Google Search Console data into BigQuery"
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
    if not args.dry_run:
        table_id = _ensure_table(bq)
        print(f"Table ready: {table_id}\n")

    grand_total = 0
    for i, day in enumerate(_iter_dates(start_date, end_date), 1):
        label = f"[{i:>4}/{total_days}] {day}"
        print(f"{label} ... ", end="", flush=True)
        try:
            rows    = fetch_day(gsc_creds, site_url, day)
            written = upsert_day(bq, rows, dry_run=args.dry_run)
            grand_total += written
            print(f"{len(rows):>6} rows  ({'dry' if args.dry_run else 'upserted'})")
        except Exception as exc:
            print(f"ERROR: {exc}")
            # Log and continue — reruns will fill gaps

    print(f"\nFinished. Total rows {'(dry) ' if args.dry_run else ''}written: {grand_total:,}")


if __name__ == "__main__":
    main()
