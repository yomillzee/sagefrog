"""Automatic GSC → BigQuery sync, called from the dashboard refresh pipeline.

Behaviour:
  - Checks max_date in both GSC tables
  - If tables are up to date (max_date >= today − 3): does nothing
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
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

_DEFAULT_PROJECT     = "penn-community-b-1699391543298"
_DEFAULT_MART_DATASET = "marketing_marts"
_QUERY_TABLE         = "fact_gsc_query_daily"
_PAGE_TABLE          = "fact_gsc_page_daily"
_GSC_LAG_DAYS    = 3
_MAX_HISTORY     = 180      # 6 months is sufficient
_ROW_LIMIT       = 25_000   # GSC API page size
_RETRY_SLEEP     = 60
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
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _fetch_day(creds, site_url: str, day: date, dimension: str) -> list[dict]:
    rows: list[dict] = []
    start_row = 0
    while True:
        try:
            data = _gsc_post(_token(creds), site_url, {
                "startDate": day.isoformat(), "endDate": day.isoformat(),
                "dimensions": ["date", dimension],
                "rowLimit": _ROW_LIMIT, "startRow": start_row, "dataState": "final",
            })
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
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
        if len(batch) < _ROW_LIMIT:
            break
        start_row += _ROW_LIMIT
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
    return query_id, page_id


def _upsert(client, table_id: str, rows: list[dict], schema, merge_key_sql: str) -> int:
    if not rows:
        return 0
    from google.cloud import bigquery as bq
    synced_at = datetime.now(timezone.utc).isoformat()
    payload   = [{**r, "synced_at": synced_at} for r in rows]
    temp_id   = f"{table_id}_stg_{uuid4().hex}"
    job_config = bq.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_json(payload, temp_id, job_config=job_config).result()
    set_cols = [f.name for f in schema if f.name not in {"synced_at"}]
    set_clause = ",\n      ".join(f"{c} = S.{c}" for c in set_cols) + ",\n      synced_at = S.synced_at"
    ins_cols = ", ".join(f.name for f in schema)
    ins_vals = ", ".join(f"S.{f.name}" for f in schema)
    sql = f"""
    MERGE `{table_id}` T USING `{temp_id}` S ON {merge_key_sql}
    WHEN MATCHED THEN UPDATE SET {set_clause}
    WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})
    """
    try:
        client.query(sql).result()
    finally:
        client.delete_table(temp_id, not_found_ok=True)
    return len(payload)


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def _table_max_date(client, table_id: str) -> date | None:
    """Return the most recent date in the table, or None if empty / missing."""
    try:
        rows = list(client.query(
            f"SELECT MAX(date) AS max_date FROM `{table_id}` LIMIT 1"
        ).result(max_results=1))
        if rows:
            val = dict(rows[0].items()).get("max_date")
            if val:
                return val if isinstance(val, date) else date.fromisoformat(str(val)[:10])
    except Exception:
        pass
    return None


def get_missing_range(client=None, target=None) -> tuple[date | None, date | None]:
    """Return (start, end) for dates that need syncing, or (None, None) if current."""
    lag_cutoff = date.today() - timedelta(days=_GSC_LAG_DAYS)
    max_history = date.today() - timedelta(days=_MAX_HISTORY)

    try:
        c = client or _bq_client(target)
        proj, ds = _project_id(target), _dataset_id(target)
        q_max = _table_max_date(c, f"{proj}.{ds}.{_QUERY_TABLE}")
        p_max = _table_max_date(c, f"{proj}.{ds}.{_PAGE_TABLE}")
        # Sync from the earlier of the two max dates (keep tables in step)
        earliest_max = min(d for d in [q_max, p_max] if d) if (q_max or p_max) else None
    except Exception:
        earliest_max = None

    if earliest_max is None:
        # Tables empty — full historical backfill
        return max_history, lag_cutoff

    next_day = earliest_max + timedelta(days=1)
    if next_day > lag_cutoff:
        return None, None  # already up to date

    return next_day, lag_cutoff


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
) -> dict[str, Any]:
    """Fetch and write GSC data for start..end. Returns summary dict."""
    target = _resolve(client_slug=client_slug)
    site_url = (site_url or "").strip() or (target.site_url or "") or _site_url()
    if not site_url:
        return {"ok": False, "error": "GSC_SITE_URL env var not set"}

    do_q = which in ("both", "queries")
    do_p = which in ("both", "pages")
    total_days = (end - start).days + 1

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
    day_num = 0

    # Process newest → oldest so the dashboard's "Last 30 days" view fills in first.
    dates = [end - timedelta(days=i) for i in range(total_days)]

    for d in dates:
        day_num += 1
        if progress_cb:
            progress_cb(d, total_days)
        if do_q:
            try:
                rows   = _fetch_day(creds, site_url, d, "query")
                grand_q += _upsert(client, query_id, rows, q_schema, q_merge)
            except Exception as exc:
                errors.append(f"{d} query: {exc}")
        if do_p:
            try:
                rows   = _fetch_day(creds, site_url, d, "page")
                grand_p += _upsert(client, page_id, rows, p_schema, p_merge)
            except Exception as exc:
                errors.append(f"{d} page: {exc}")
        if day_num % 30 == 0 or day_num == total_days:
            log.info(
                "GSC backfill progress: %d/%d days (latest=%s), "
                "query_rows=%d, page_rows=%d, errors=%d",
                day_num, total_days, dates[0], grand_q, grand_p, len(errors),
            )

    return {
        "ok":            len(errors) == 0,
        "start":         start.isoformat(),
        "end":           end.isoformat(),
        "days_synced":   total_days,
        "query_rows":    grand_q,
        "page_rows":     grand_p,
        "errors":        errors[:10],
    }


# ---------------------------------------------------------------------------
# Refresh-pipeline entry point
# ---------------------------------------------------------------------------

def sync_for_refresh(site_url: str | None = None, client_slug: str | None = None) -> dict[str, Any]:
    """Called automatically from the dashboard refresh pipeline.

    site_url: override the GSC_SITE_URL env var (for per-client config).
    client_slug: which client's BigQuery destination (project/dataset/creds)
        to route into, via gsc_clients.resolve_target(). Omit to keep the
        legacy Penn-only behaviour.
    - Tables up to date → no-op (fast)
    - Small gap (≤ 30 days) → sync synchronously; fresh data in this snapshot
    - Large gap / empty tables → spawn background thread; data available next refresh
    """
    target = _resolve(client_slug=client_slug)
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
        start, end = get_missing_range(client=client, target=target)
    except Exception as exc:
        return {"ok": False, "error": f"get_missing_range failed: {exc}"}

    if start is None:
        return {"ok": True, "status": "up_to_date"}

    days_missing = (end - start).days + 1

    if days_missing > _BACKGROUND_THRESHOLD:
        # Full backfill — don't block the refresh
        _url = site_url  # capture for closure
        _slug = client_slug

        def _bg():
            try:
                result = sync_range(start, end, site_url=_url, client_slug=_slug)
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
        result = sync_range(start, end, site_url=site_url, client_slug=client_slug)
        result["status"] = "synced"
        return result
