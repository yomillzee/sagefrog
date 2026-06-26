"""Sync GA4 Reporting API → BigQuery raw tables."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_GA4_DATASET = "raw_ga4"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_ga4_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    ga4_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (ga4_dataset_id or "").strip()
            or (os.getenv("BQ_GA4_DATASET_ID") or "").strip()
            or _DEFAULT_GA4_DATASET
        ),
        "credentials_env": (credentials_env or "").strip() or None,
    }
    token = _route_ctx.set(payload)
    try:
        yield
    finally:
        _route_ctx.reset(token)


def _ctx() -> dict:
    return _route_ctx.get() or {
        "project": None,
        "dataset": _DEFAULT_GA4_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    r = _ctx()
    return (r.get("project") or "").strip() or _DEFAULT_PROJECT


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_GA4_DATASET).strip()


def _credentials_env() -> str | None:
    return _ctx().get("credentials_env")


def _bq():
    from google.cloud import bigquery
    return bigquery


def _client():
    import bigquery_service
    return bigquery_service.build_client(_project_id(), credentials_env=_credentials_env())


def _table_ref(table_name: str) -> str:
    return f"{_project_id()}.{_dataset_id()}.{table_name}"


# ── Schema helpers ────────────────────────────────────────────────────────────

def _schema_sessions():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("sessionDefaultChannelGroup", "STRING", mode="NULLABLE"),
        bq.SchemaField("sessionSource", "STRING", mode="NULLABLE"),
        bq.SchemaField("sessionMedium", "STRING", mode="NULLABLE"),
        bq.SchemaField("sessions", "INT64", mode="NULLABLE"),
        bq.SchemaField("engagedSessions", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("totalUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("newUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("bounceRate", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("engagementRate", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("screenPageViews", "INT64", mode="NULLABLE"),
    ]


def _schema_tech():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("deviceCategory", "STRING", mode="NULLABLE"),
        bq.SchemaField("browser", "STRING", mode="NULLABLE"),
        bq.SchemaField("operatingSystem", "STRING", mode="NULLABLE"),
        bq.SchemaField("activeUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("engagedSessions", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_pages():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("landingPage", "STRING", mode="NULLABLE"),
        bq.SchemaField("sessions", "INT64", mode="NULLABLE"),
        bq.SchemaField("activeUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("newUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("userEngagementDurationPerSession", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_events():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("eventName", "STRING", mode="NULLABLE"),
        bq.SchemaField("eventCount", "INT64", mode="NULLABLE"),
        bq.SchemaField("totalUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("eventCountPerUser", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_user_acq():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("firstUserDefaultChannelGroup", "STRING", mode="NULLABLE"),
        bq.SchemaField("firstUserSource", "STRING", mode="NULLABLE"),
        bq.SchemaField("firstUserMedium", "STRING", mode="NULLABLE"),
        bq.SchemaField("newUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("activeUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("totalUsers", "INT64", mode="NULLABLE"),
    ]


def _schema_demographics():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("userAgeBracket", "STRING", mode="NULLABLE"),
        bq.SchemaField("userGender", "STRING", mode="NULLABLE"),
        bq.SchemaField("activeUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("engagementRate", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_geo():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("country", "STRING", mode="NULLABLE"),
        bq.SchemaField("region", "STRING", mode="NULLABLE"),
        bq.SchemaField("city", "STRING", mode="NULLABLE"),
        bq.SchemaField("activeUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("sessions", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_pageviews():
    bq = _bq()
    return [
        bq.SchemaField("date", "DATE", mode="REQUIRED"),
        bq.SchemaField("pagePath", "STRING", mode="NULLABLE"),
        bq.SchemaField("screenPageViews", "INT64", mode="NULLABLE"),
        bq.SchemaField("activeUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("newUsers", "INT64", mode="NULLABLE"),
        bq.SchemaField("keyEvents", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("averageSessionDuration", "FLOAT64", mode="NULLABLE"),
    ]


_TABLE_SCHEMAS = {
    "ga4_sessions_daily": _schema_sessions,
    "ga4_tech_daily": _schema_tech,
    "ga4_pages_daily": _schema_pages,
    "ga4_events_daily": _schema_events,
    "ga4_user_acq_daily": _schema_user_acq,
    "ga4_demographics_daily": _schema_demographics,
    "ga4_geo_daily": _schema_geo,
    "ga4_pageviews_daily": _schema_pageviews,
}


def ensure_ga4_tables() -> None:
    bq = _bq()
    client = _client()
    project = _project_id()
    dataset = _dataset_id()
    dataset_ref = f"{project}.{dataset}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True)
    for table_name, schema_fn in _TABLE_SCHEMAS.items():
        schema = schema_fn()
        table_id = f"{dataset_ref}.{table_name}"
        table = bq.Table(table_id, schema=schema)
        table.time_partitioning = bq.TimePartitioning(field="date")
        client.create_table(table, exists_ok=True)

    # Add new columns to existing tables (idempotent — IF NOT EXISTS)
    _alter_cols = [
        ("ga4_sessions_daily", "totalUsers",      "INT64"),
        ("ga4_sessions_daily", "newUsers",         "INT64"),
        ("ga4_sessions_daily", "bounceRate",       "FLOAT64"),
        ("ga4_sessions_daily", "engagementRate",   "FLOAT64"),
        ("ga4_sessions_daily", "screenPageViews",  "INT64"),
        ("ga4_tech_daily",     "browser",          "STRING"),
        ("ga4_tech_daily",     "operatingSystem",  "STRING"),
    ]
    for tbl, col, dtype in _alter_cols:
        try:
            client.query(
                f"ALTER TABLE `{dataset_ref}.{tbl}` ADD COLUMN IF NOT EXISTS {col} {dtype}"
            ).result()
        except Exception as exc:
            _log.warning("ALTER TABLE %s.%s skipped: %s", tbl, col, exc)

    _log.info("GA4 tables ensured in %s", dataset_ref)


# ── Write helpers ─────────────────────────────────────────────────────────────

def _write_table(
    table_name: str,
    rows: list[dict[str, Any]],
    schema_fn,
    start: str,
    end: str,
) -> int:
    if not rows:
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref(table_name)
    # Delete existing rows for this date range (idempotent re-runs)
    client.query(
        f"DELETE FROM `{table_id}` WHERE date BETWEEN '{start}' AND '{end}'"
    ).result()
    job_config = bq.LoadJobConfig(
        schema=schema_fn(),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result()
    _log.info("GA4 wrote %d rows → %s", len(rows), table_name)
    return len(rows)


# ── Main sync ─────────────────────────────────────────────────────────────────

def sync_ga4_to_bq(
    property_id: str,
    *,
    start: str,
    end: str,
    refresh_token: str,
) -> dict[str, Any]:
    """
    Pull all 6 GA4 report types and write to BQ.
    Returns row counts per table.
    """
    import ga4_reporting_service as ga4

    ensure_ga4_tables()

    access_token = ga4._get_access_token(refresh_token)
    _log.info(
        "GA4 sync: property=%s start=%s end=%s dataset=%s",
        property_id, start, end, _dataset_id(),
    )

    errors: dict[str, str] = {}
    counts: dict[str, int] = {}

    fetchers = [
        ("sessions",     ga4.fetch_sessions_daily,    "ga4_sessions_daily",    _schema_sessions),
        ("tech",         ga4.fetch_tech_daily,         "ga4_tech_daily",         _schema_tech),
        ("pages",        ga4.fetch_pages_daily,        "ga4_pages_daily",        _schema_pages),
        ("pageviews",    ga4.fetch_pageviews_daily,    "ga4_pageviews_daily",    _schema_pageviews),
        ("events",       ga4.fetch_events_daily,       "ga4_events_daily",       _schema_events),
        ("user_acq",     ga4.fetch_user_acq_daily,     "ga4_user_acq_daily",     _schema_user_acq),
        ("geo",          ga4.fetch_geo_daily,          "ga4_geo_daily",          _schema_geo),
        ("demographics", ga4.fetch_demographics_daily, "ga4_demographics_daily", _schema_demographics),
    ]

    for key, fetch_fn, table_name, schema_fn in fetchers:
        try:
            rows = fetch_fn(property_id, start, end, access_token)
            n = _write_table(table_name, rows, schema_fn, start, end)
            counts[key] = n
        except Exception as exc:
            _log.warning("GA4 sync error [%s]: %s", key, exc)
            errors[key] = str(exc)[:300]
            counts[key] = 0

    _log.info(
        "GA4 sync complete: sessions=%d tech=%d pages=%d pageviews=%d events=%d "
        "user_acq=%d geo=%d demographics=%d errors=%s",
        counts.get("sessions", 0), counts.get("tech", 0), counts.get("pages", 0),
        counts.get("pageviews", 0), counts.get("events", 0), counts.get("user_acq", 0),
        counts.get("geo", 0), counts.get("demographics", 0),
        list(errors.keys()) or "none",
    )

    total = sum(counts.values())
    return {"total_rows": total, "counts": counts, "errors": errors}
