"""Automatic GSC → BigQuery sync, called from the dashboard refresh pipeline.

Behaviour:
  - Enumerates which dates in the retention window are missing from either GSC
    table (not just max_date — a hole *below* the newest row has to be visible,
    or an interrupted backfill never gets repaired)
  - If nothing is missing: does nothing
  - If a small gap (<= 30 days): syncs synchronously inside the refresh thread
  - If a large gap / empty tables: spawns a background daemon thread so the
    refresh is not blocked; the data will be present on the next refresh

The CLI gsc_backfill.py is now a thin wrapper around sync_range() here.

Required env vars:
    GCP_CREDS_PENN_BASE64   base64 service account JSON (GSC + BQ write)
    GSC_SITE_URL            sc-domain:penncommunitybank.com  or https://...

Optional:
    GSC_BQ_PROJECT_ID       (default: penn-community-b-1699391543298)
    BQ_MART_DATASET_ID      (default: marketing_marts)
                            Note: GSC_BQ_DATASET_ID is intentionally NOT used here — that var
                            is reserved for the native GSC export dataset (searchconsole_penn).
                            Historical API-backfill tables are written to the mart dataset.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, UTC
from typing import Any
from uuid import uuid4

import bigquery_service

log = logging.getLogger(__name__)

_DEFAULT_PROJECT     = "penn-community-b-1699391543298"
_DEFAULT_MART_DATASET = "marketing_marts"
_QUERY_TABLE         = "fact_gsc_query_daily"
_PAGE_TABLE          = "fact_gsc_page_daily"
# Days Search Console confirmed hold no data, so hole detection stops re-asking.
_EMPTY_DAYS_TABLE    = "gsc_empty_days"
_GSC_LAG_DAYS    = 3
_MAX_HISTORY     = 180      # 6 months is sufficient
_ROW_LIMIT       = 25_000   # GSC API page size
_RETRY_SLEEP     = 60
_MAX_RETRIES     = 3        # per request, for quota/transient failures
_MAX_PAGES_PER_DAY = 40     # 40 x 25k rows is far past any real day; loop guard
# A page this small cannot be a server-imposed cap (GSC's is 5,000), so it means
# the day is done and there is no point asking for the next page.
_PAGE_PROBE_MIN  = 1_000
# If more than this many days are missing, run in background to avoid blocking refresh
_BACKGROUND_THRESHOLD = 30

_BQ_SCOPE  = "https://www.googleapis.com/auth/bigquery"
_GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve(target=None, client_slug: str | None = None):
    """Pick a GscClientTarget: explicit target > client_slug lookup > legacy Penn default."""
    import gsc_clients
    if target is not None:
        return target
    return gsc_clients.resolve_target(client_slug) if client_slug else gsc_clients.default_target()


def _project_id(target=None) -> str:
    if target is not None:
        return target.bq_project_id
    return (os.getenv("GSC_BQ_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _dataset_id(target=None) -> str:
    if target is not None:
        return target.bq_dataset_id
    return (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DATASET).strip()


def _site_url() -> str:
    return os.getenv("GSC_SITE_URL", "").strip()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _creds_env(target=None) -> str:
    """Prefer the client's registry credential; fall back to Penn-specific, then global."""
    if target is not None and target.credentials_env:
        return target.credentials_env
    if (os.getenv("GCP_CREDS_PENN_BASE64") or "").strip():
        return "GCP_CREDS_PENN_BASE64"
    return "GCP_SERVICE_ACCOUNT_JSON"


def _gsc_creds(target=None):
    """Service-account credentials, scoped for both GSC reads and BQ writes.

    Used as the fallback read path when no agency GSC OAuth token is
    connected yet (keeps Penn's existing setup working unchanged).
    """
    from google.oauth2 import service_account
    from ga4_credentials import load_service_account_info_from_env
    env = _creds_env(target)
    info = load_service_account_info_from_env(env, require_base64=(env == "GCP_CREDS_PENN_BASE64"))
    return service_account.Credentials.from_service_account_info(
        info, scopes=[_GSC_SCOPE, _BQ_SCOPE]
    )


def _gsc_read_creds(target=None):
    """Credentials used to call the Search Console API.

    Prefers the agency-wide GSC OAuth login (Settings -> Admin -> Connect
    Google Search Console) since that one login already has access to
    every client's property. Falls back to the service account when GSC
    hasn't been connected via OAuth yet, so nothing breaks mid-rollout.
    """
    try:
        import oauth_store
        refresh_token = oauth_store.get_refresh_token("gsc")
    except Exception:
        refresh_token = None

    if refresh_token:
        import auth as google_auth
        from google.oauth2.credentials import Credentials
        client_id = google_auth._get_env(*google_auth._ENV_ALIASES["client_id"])
        client_secret = google_auth._get_env(*google_auth._ENV_ALIASES["client_secret"])
        if client_id and client_secret:
            return Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=[_GSC_SCOPE],
            )
    return _gsc_creds(target)


def _bq_client(target=None):
    from google.cloud import bigquery as bq
    from google.oauth2 import service_account
    from ga4_credentials import load_service_account_info_from_env
    env = _creds_env(target)
    info = load_service_account_info_from_env(env, require_base64=(env == "GCP_CREDS_PENN_BASE64"))
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=[_BQ_SCOPE]
    )
    return bq.Client(project=_project_id(target), credentials=creds)


def _token(creds) -> str:
    import google.auth.transport.requests
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    return creds.token


# ---------------------------------------------------------------------------
# GSC API
# ---------------------------------------------------------------------------

def list_accessible_properties() -> list[dict[str, str]]:
    """List every GSC property the connected agency OAuth login can see.

    Requires GSC to be connected via Admin → Connect Google Search Console.
    Used to populate the property dropdown on each client's settings page.
    """
    import google.auth.transport.requests
    creds = _gsc_read_creds()
    cred_type = type(creds).__name__

    # Refresh if needed — captures token refresh errors explicitly
    if not creds.valid:
        try:
            creds.refresh(google.auth.transport.requests.Request())
        except Exception as refresh_exc:
            raise RuntimeError(
                f"Token refresh failed (cred_type={cred_type}): {refresh_exc}"
            ) from refresh_exc

    # Surface which scopes the refreshed token actually carries
    token_scopes = getattr(creds, "scopes", None) or getattr(creds, "_scopes", None)

    req = urllib.request.Request(
        "https://www.googleapis.com/webmasters/v3/sites",
        headers={"Authorization": f"Bearer {creds.token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(
            f"webmasters API {exc.code} (cred_type={cred_type}, "
            f"token_scopes={token_scopes}): {body}"
        ) from exc

    out = []
    for entry in data.get("siteEntry") or []:
        url = entry.get("siteUrl") or ""
        if not url:
            continue
        out.append({"site_url": url, "permission_level": entry.get("permissionLevel") or ""})
    return sorted(out, key=lambda e: e["site_url"])


class GscApiError(RuntimeError):
    """A Search Console API failure that carries the response body.

    urllib's HTTPError stringifies to just "HTTP Error 403: Forbidden", which
    is the same text whether the property is misspelled, the login lost access,
    or the scope is wrong -- Google puts the distinguishing reason in the body
    ("User does not have sufficient permission for site '...'"). Keep .code so
    the 429 retry in _fetch_day still works.
    """

    def __init__(self, code: int, body: str, site_url: str) -> None:
        self.code = code
        self.body = body
        super().__init__(f"HTTP {code} for {site_url}: {body}" if body else f"HTTP {code} for {site_url}")


def _gsc_post(token: str, site_url: str, body: dict) -> dict:
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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = str((raw.get("error") or {}).get("message") or "")[:200]
        except Exception:
            detail = ""
        raise GscApiError(exc.code, detail, site_url) from exc


# Google reports Search Console quota exhaustion as 403 with a rate-limit reason
# in the body, not as 429 -- the same trap gtm_service._is_rate_limited exists for
# (see CLAUDE.md). Treating those as fatal turns a transient quota blip into a
# permanently missing day, so they retry like a 429 and only a genuine permission
# 403 gives up.
_RATE_LIMIT_REASONS = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
    "backenderror",
)


def _is_retryable(exc: GscApiError) -> bool:
    if exc.code in (429, 500, 503):
        return True
    if exc.code != 403:
        return False
    blob = (exc.body or "").replace(" ", "").replace("'", "").lower()
    return any(reason in blob for reason in _RATE_LIMIT_REASONS)


def _fetch_day(creds, site_url: str, day: date, dimension: str) -> list[dict]:
    rows: list[dict] = []
    start_row = 0
    attempts = 0
    pages = 0
    while True:
        try:
            data = _gsc_post(_token(creds), site_url, {
                "startDate": day.isoformat(), "endDate": day.isoformat(),
                "dimensions": ["date", dimension],
                "rowLimit": _ROW_LIMIT, "startRow": start_row, "dataState": "final",
            })
        except GscApiError as exc:
            attempts += 1
            if _is_retryable(exc) and attempts <= _MAX_RETRIES:
                time.sleep(_RETRY_SLEEP)
                continue
            raise
        attempts = 0
        batch = data.get("rows") or []
        if not batch:
            break
        for r in batch:
            keys = r.get("keys") or []
            impressions = int(r.get("impressions") or 0)
            position    = float(r.get("position") or 0)
            if dimension == "query":
                raw = keys[1] if len(keys) > 1 else ""
                query = raw.strip() if raw else ""
                rows.append({
                    "date": day.isoformat(), "query": query or None,
                    "organic_clicks": int(r.get("clicks") or 0),
                    "organic_impressions": impressions,
                    "organic_sum_position": (position - 1.0) * impressions,
                    "is_anonymized_query": not bool(query),
                })
            else:
                rows.append({
                    "date": day.isoformat(),
                    "page_url": keys[1] if len(keys) > 1 else "",
                    "organic_clicks": int(r.get("clicks") or 0),
                    "organic_impressions": impressions,
                    "organic_sum_position": (position - 1.0) * impressions,
                })
        # Advance by what the server actually returned, and keep going until a
        # page comes back empty. Comparing len(batch) against the *requested*
        # _ROW_LIMIT and stopping early silently truncated every high-volume day:
        # GSC caps a page at 5,000 rows regardless of asking for 25,000, so page
        # one always looked like the last one and pagination never advanced --
        # every day of one client's history held exactly 5,000 query rows.
        start_row += len(batch)
        pages += 1
        if len(batch) < _PAGE_PROBE_MIN:
            # Far below any server-side page cap, so the day is exhausted. Without
            # this floor every day would cost one extra empty request just to
            # confirm the end -- 360 wasted calls on a 180-day backfill, against a
            # quota that already reports exhaustion as a 403.
            break
        if pages >= _MAX_PAGES_PER_DAY:
            log.warning(
                "GSC %s %s: stopped at %d pages (%d rows) -- page cap reached",
                dimension, day, pages, len(rows),
            )
            break
    return rows


# ---------------------------------------------------------------------------
# BQ schemas & write
# ---------------------------------------------------------------------------

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


def _ensure_tables(client, target=None) -> tuple[str, str]:
    from google.cloud import bigquery as bq
    proj, ds = _project_id(target), _dataset_id(target)
    query_id = f"{proj}.{ds}.{_QUERY_TABLE}"
    page_id  = f"{proj}.{ds}.{_PAGE_TABLE}"
    client.create_dataset(bq.Dataset(f"{proj}.{ds}"), exists_ok=True)
    for tid, schema, cluster in [
        (query_id, _query_schema(), "query"),
        (page_id,  _page_schema(),  "page_url"),
    ]:
        t = bq.Table(tid, schema=schema)
        t.time_partitioning = bq.TimePartitioning(
            type_=bq.TimePartitioningType.DAY, field="date"
        )
        t.clustering_fields = [cluster]
        client.create_table(t, exists_ok=True)
    _reap_stale_staging(client, proj, ds)
    return query_id, page_id


def _reap_stale_staging(client, project: str, dataset: str, *, older_than_hours: int = 6) -> int:
    """Delete leftover *_stg_* load tables from killed syncs.

    _upsert removes its staging table in a finally, so a survivor means the
    process was killed between the load and the MERGE -- exactly what a redeploy
    mid-sync does. They are invisible in the dashboard but sit in the dataset
    costing storage, and they make it impossible to read __TABLES__ and tell
    what the sync actually wrote.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    removed = 0
    try:
        for tbl in client.list_tables(f"{project}.{dataset}"):
            name = tbl.table_id
            if "_stg_" not in name:
                continue
            modified = getattr(tbl, "modified", None) or getattr(tbl, "created", None)
            if modified is not None and modified > cutoff:
                continue  # possibly an in-flight sync on another worker
            client.delete_table(f"{project}.{dataset}.{name}", not_found_ok=True)
            removed += 1
    except Exception as exc:
        log.warning("GSC staging reap skipped: %s", exc)
    if removed:
        log.info("GSC reaped %d stale staging table(s) in %s.%s", removed, project, dataset)
    return removed


def _upsert(
    client, table_id: str, rows: list[dict], schema, merge_key_sql: str,
    *, stamp_column: str = "synced_at",
) -> int:
    if not rows:
        return 0
    from google.cloud import bigquery as bq
    stamp     = datetime.now(UTC).isoformat()
    payload   = [{**r, stamp_column: stamp} for r in rows]
    temp_id   = f"{table_id}_stg_{uuid4().hex}"
    job_config = bq.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    set_cols = [f.name for f in schema if f.name != stamp_column]
    set_clause = ",\n      ".join(f"{c} = S.{c}" for c in set_cols) + f",\n      {stamp_column} = S.{stamp_column}"
    ins_cols = ", ".join(f.name for f in schema)
    ins_vals = ", ".join(f"S.{f.name}" for f in schema)
    sql = f"""
    MERGE `{table_id}` T USING `{temp_id}` S ON {merge_key_sql}
    WHEN MATCHED THEN UPDATE SET {set_clause}
    WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})
    """
    try:
        client.query(sql, job_config=bigquery_service.make_job_config()).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return len(payload)


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def _dates_present(client, table_id: str, start: date, end: date) -> set[date]:
    """Dates that already hold at least one row in this table."""
    sql = (
        f"SELECT DISTINCT date FROM `{table_id}` "
        f"WHERE date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    )
    out: set[date] = set()
    try:
        for row in client.query(sql, job_config=bigquery_service.make_job_config()).result():
            val = dict(row.items()).get("date")
            if val:
                out.add(val if isinstance(val, date) else date.fromisoformat(str(val)[:10]))
    except Exception:
        pass  # missing table / permission -> treat as nothing present
    return out


def _empty_days(client, target, dimension: str, start: date, end: date) -> set[date]:
    """Days Search Console has already told us hold no data for this dimension.

    Without this, a day with genuinely zero impressions -- or any day before the
    property existed -- is absent from the fact table forever, so hole detection
    would re-fetch it on every single run and burn quota permanently.
    """
    tid = f"{_project_id(target)}.{_dataset_id(target)}.{_EMPTY_DAYS_TABLE}"
    sql = (
        f"SELECT date FROM `{tid}` WHERE dimension = '{dimension}' "
        f"AND date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    )
    out: set[date] = set()
    try:
        for row in client.query(sql, job_config=bigquery_service.make_job_config()).result():
            val = dict(row.items()).get("date")
            if val:
                out.add(val if isinstance(val, date) else date.fromisoformat(str(val)[:10]))
    except Exception:
        pass
    return out


def _record_empty_days(client, target, rows: list[dict]) -> None:
    """Remember (date, dimension) pairs that Search Console returned nothing for."""
    if not rows:
        return
    from google.cloud import bigquery as bq
    tid = f"{_project_id(target)}.{_dataset_id(target)}.{_EMPTY_DAYS_TABLE}"
    schema = [
        bq.SchemaField("date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("dimension", "STRING",    mode="REQUIRED"),
        bq.SchemaField("checked_at", "TIMESTAMP", mode="REQUIRED"),
    ]
    try:
        t = bq.Table(tid, schema=schema)
        t.time_partitioning = bq.TimePartitioning(type_=bq.TimePartitioningType.DAY, field="date")
        client.create_table(t, exists_ok=True)
        _upsert(
            client, tid, rows, schema,
            "T.date = S.date AND T.dimension = S.dimension",
            stamp_column="checked_at",
        )
    except Exception as exc:
        # Losing these markers only costs a re-fetch next run; never fail a sync.
        log.warning("GSC empty-day markers not recorded: %s", exc)


def get_missing_dates(
    client=None, target=None, *, which: str = "both"
) -> dict[str, list[date]]:
    """Which dates in the retention window each dimension is still missing.

    Replaces MAX(date) gap detection, which could not see a hole *below* the
    newest row. sync_range walks newest -> oldest, so any interrupted backfill
    (a redeploy mid-sync hard-kills the worker) left MAX(date) at the newest day
    it managed to write while every older day stayed missing -- and the next run
    read that as "up to date" and did nothing, forever. One client lost 38 days
    in the middle of its history that way, invisibly.
    """
    lag_cutoff  = date.today() - timedelta(days=_GSC_LAG_DAYS)
    window_start = date.today() - timedelta(days=_MAX_HISTORY)
    c = client or _bq_client(target)
    proj, ds = _project_id(target), _dataset_id(target)
    all_days = {
        window_start + timedelta(days=i)
        for i in range((lag_cutoff - window_start).days + 1)
    }

    out: dict[str, list[date]] = {}
    for dim, table, which_name in (
        ("query", _QUERY_TABLE, "queries"),
        ("page", _PAGE_TABLE, "pages"),
    ):
        if which not in ("both", which_name):
            out[dim] = []
            continue
        have = _dates_present(c, f"{proj}.{ds}.{table}", window_start, lag_cutoff)
        have |= _empty_days(c, target, dim, window_start, lag_cutoff)
        out[dim] = sorted(all_days - have, reverse=True)  # newest first
    return out


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------

def sync_range(
    start: date,
    end: date,
    which: str = "both",
    progress_cb=None,
    *,
    site_url: str | None = None,
    client_slug: str | None = None,
    target=None,
    dates: list[date] | None = None,
) -> dict[str, Any]:
    """Fetch and write GSC data for start..end. Returns summary dict.

    dates: sync exactly these days instead of the whole start..end span. Used by
    sync_for_refresh to fill scattered holes without re-fetching days that are
    already complete. start/end stay in the signature (and in the summary) so the
    CLI's "force this whole range" behaviour is unchanged.
    """
    target = target if target is not None else _resolve(client_slug=client_slug)
    site_url = (site_url or "").strip() or (target.site_url or "") or _site_url()
    if not site_url:
        return {"ok": False, "error": "GSC_SITE_URL env var not set"}

    do_q = which in ("both", "queries")
    do_p = which in ("both", "pages")

    try:
        creds  = _gsc_read_creds(target)
        client = _bq_client(target)
        query_id, page_id = _ensure_tables(client, target)
        q_schema = _query_schema()
        p_schema = _page_schema()
        q_merge  = "T.date = S.date AND IFNULL(T.query, '') = IFNULL(S.query, '')"
        p_merge  = "T.date = S.date AND T.page_url = S.page_url"
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}

    grand_q = grand_p = 0
    errors: list[str] = []
    failed_days: set[date] = set()
    empty_markers: list[dict] = []
    day_num = 0

    # Process newest → oldest so the dashboard's "Last 30 days" view fills in first.
    if dates is None:
        dates = [end - timedelta(days=i) for i in range((end - start).days + 1)]
    else:
        dates = sorted(dates, reverse=True)
    total_days = len(dates)
    if not total_days:
        return {
            "ok": True, "status": "up_to_date", "start": start.isoformat(),
            "end": end.isoformat(), "days_synced": 0, "query_rows": 0,
            "page_rows": 0, "errors": [], "error_count": 0, "failed_days": 0,
        }

    for d in dates:
        day_num += 1
        if progress_cb:
            progress_cb(d, total_days)
        if do_q:
            try:
                rows   = _fetch_day(creds, site_url, d, "query")
                grand_q += _upsert(client, query_id, rows, q_schema, q_merge)
                if not rows:
                    empty_markers.append({"date": d.isoformat(), "dimension": "query"})
            except Exception as exc:
                errors.append(f"{d} query: {exc}")
                failed_days.add(d)
        if do_p:
            try:
                rows   = _fetch_day(creds, site_url, d, "page")
                grand_p += _upsert(client, page_id, rows, p_schema, p_merge)
                if not rows:
                    empty_markers.append({"date": d.isoformat(), "dimension": "page"})
            except Exception as exc:
                errors.append(f"{d} page: {exc}")
                failed_days.add(d)
        # Flush empty-day markers as we go: a redeploy mid-backfill kills the
        # worker outright, and progress that only lands at the end is progress
        # that gets redone.
        if len(empty_markers) >= 25:
            _record_empty_days(client, target, empty_markers)
            empty_markers = []
        if day_num % 30 == 0 or day_num == total_days:
            log.info(
                "GSC backfill progress: %d/%d days (latest=%s), "
                "query_rows=%d, page_rows=%d, errors=%d",
                day_num, total_days, dates[0], grand_q, grand_p, len(errors),
            )

    _record_empty_days(client, target, empty_markers)

    return {
        "ok":            len(errors) == 0,
        "start":         start.isoformat(),
        "end":           end.isoformat(),
        "days_synced":   total_days,
        "query_rows":    grand_q,
        "page_rows":     grand_p,
        # "errors" is capped so a 180-day backfill that fails on every day does
        # not return 360 near-identical strings. The caps make len(errors) a
        # floor, not a count, so report the real totals alongside: a sync stalled
        # for 5 days and one that never worked at all look identical otherwise.
        "errors":        errors[:10],
        "error_count":   len(errors),
        "failed_days":   len(failed_days),
    }


# ---------------------------------------------------------------------------
# Refresh-pipeline entry point
# ---------------------------------------------------------------------------

def sync_for_refresh(
    site_url: str | None = None,
    client_slug: str | None = None,
    target=None,
    *,
    wait_for_backfill: bool = False,
) -> dict[str, Any]:
    """Called automatically from the dashboard refresh pipeline.

    site_url: override the GSC_SITE_URL env var (for per-client config).
    client_slug: which client's BigQuery destination to route into, via
        gsc_clients.resolve_target(). Omit to keep the legacy Penn-only behaviour.
    target: an explicit GscClientTarget (the connector builds one from its config
        so routing never depends on re-resolution). Threaded all the way through
        the background backfill so it can't silently fall back to Penn.
    wait_for_backfill: run a large/full backfill synchronously instead of handing
        it to a daemon thread. Set this from callers that are themselves already
        off the request/response cycle (e.g. the connector's run_sync(), invoked
        via FastAPI BackgroundTasks) -- a raw daemon thread has no run tracking
        and gets silently killed if the worker process recycles before a
        multi-minute backfill finishes, which left raw_gsc permanently empty
        for at least one client despite "completed" sync runs every day.
    - Tables up to date → no-op (fast)
    - Small gap (≤ 30 days) → sync synchronously; fresh data in this snapshot
    - Large gap / empty tables, wait_for_backfill=False → spawn background
      thread; data available next refresh (dashboard cache-miss path only)
    - Large gap / empty tables, wait_for_backfill=True → sync synchronously
    """
    target = target if target is not None else _resolve(client_slug=client_slug)
    site_url = (site_url or "").strip() or (target.site_url or "") or _site_url()
    if not site_url:
        return {"ok": False, "error": "GSC_SITE_URL not configured (set it in Settings → GSC Site URL)"}

    # Test credentials and table creation synchronously so errors surface immediately
    # rather than disappearing into the background thread.
    try:
        client = _bq_client(target)
        _ensure_tables(client, target)
    except Exception as exc:
        return {"ok": False, "error": f"BQ setup failed (check service account permissions): {exc}"}

    try:
        missing = get_missing_dates(client=client, target=target)
    except Exception as exc:
        return {"ok": False, "error": f"get_missing_dates failed: {exc}"}

    # A day missing from either dimension is a day to sync. Fetching both
    # dimensions for it re-fetches at most a handful of already-complete
    # dimension-days (holes almost always line up), and keeps one date list
    # rather than two divergent ones.
    todo = sorted(set(missing.get("query") or []) | set(missing.get("page") or []), reverse=True)
    if not todo:
        return {"ok": True, "status": "up_to_date"}

    start, end = todo[-1], todo[0]
    days_missing = len(todo)

    if days_missing > _BACKGROUND_THRESHOLD and not wait_for_backfill:
        # Full backfill — don't block the refresh
        _url = site_url  # capture for closure
        _slug = client_slug
        _tgt = target

        _dates = todo

        def _bg():
            try:
                result = sync_range(
                    start, end, site_url=_url, client_slug=_slug, target=_tgt, dates=_dates,
                )
                log.info("GSC background backfill complete: %s", result)
            except Exception as exc:
                log.error("GSC background backfill failed: %s", exc)

        t = threading.Thread(target=_bg, name="gsc-backfill", daemon=True)
        t.start()
        return {
            "ok": True,
            "status": "backfill_started",
            "days_missing": days_missing,
            "message": f"Full GSC backfill started in background ({days_missing} days). "
                       "Data will appear on the next refresh.",
        }
    else:
        # Small gap — sync now so this snapshot has fresh data
        result = sync_range(
            start, end, site_url=site_url, client_slug=client_slug, target=target, dates=todo,
        )
        result["status"] = "synced"
        return result
