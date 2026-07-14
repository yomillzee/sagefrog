"""Sync GA4 Reporting API → BigQuery raw tables.

All tables live in {project}.raw_ga4. Schema uses snake_case column names.
Every table includes client_key, property_id, and synced_at for multi-tenant
idempotency. DELETE is scoped to client_key + property_id + date range so
multiple clients can share a dataset without collisions.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

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
    project = (r.get("project") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for GA4 reads/writes. Call bq_ga4_service.route("
            "bq_project_id=...) for this client. Refusing to silently fall back to another "
            "client's project."
        )
    return project


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


# ── Common columns added to every table ──────────────────────────────────────

def _common_fields(bq):
    return [
        bq.SchemaField("client_key",   "STRING",    mode="REQUIRED"),
        bq.SchemaField("property_id",  "STRING",    mode="REQUIRED"),
        bq.SchemaField("date",         "DATE",      mode="REQUIRED"),
        bq.SchemaField("synced_at",    "TIMESTAMP", mode="NULLABLE"),
    ]


# ── Schema helpers ────────────────────────────────────────────────────────────

def _schema_sessions():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("session_default_channel_group", "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_source",                "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_medium",                "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_campaign_name",         "STRING",  mode="NULLABLE"),
        bq.SchemaField("sessions",                      "INT64",   mode="NULLABLE"),
        bq.SchemaField("engaged_sessions",              "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",                    "INT64",   mode="NULLABLE"),
        bq.SchemaField("total_users",                   "INT64",   mode="NULLABLE"),
        bq.SchemaField("new_users",                     "INT64",   mode="NULLABLE"),
        bq.SchemaField("bounce_rate",                   "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("engagement_rate",               "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("screen_page_views",             "INT64",   mode="NULLABLE"),
    ]


def _schema_landing_pages():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("landing_page",             "STRING",  mode="NULLABLE"),
        bq.SchemaField("sessions",                 "INT64",   mode="NULLABLE"),
        bq.SchemaField("active_users",             "INT64",   mode="NULLABLE"),
        bq.SchemaField("new_users",                "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",               "INT64",   mode="NULLABLE"),
        bq.SchemaField("average_session_duration", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_landing_page_events():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("landing_page",             "STRING",  mode="NULLABLE"),
        bq.SchemaField("event_name",               "STRING",  mode="NULLABLE"),
        bq.SchemaField("event_count",              "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",               "INT64",   mode="NULLABLE"),
    ]


def _schema_user_acq_events():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("default_channel_group",    "STRING",  mode="NULLABLE"),
        bq.SchemaField("source",                   "STRING",  mode="NULLABLE"),
        bq.SchemaField("medium",                   "STRING",  mode="NULLABLE"),
        bq.SchemaField("event_name",               "STRING",  mode="NULLABLE"),
        bq.SchemaField("event_count",              "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",               "INT64",   mode="NULLABLE"),
    ]


def _schema_page_events():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("page_path",                "STRING",  mode="NULLABLE"),
        bq.SchemaField("event_name",               "STRING",  mode="NULLABLE"),
        bq.SchemaField("event_count",              "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",               "INT64",   mode="NULLABLE"),
    ]


def _schema_pageviews():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("page_path",                "STRING",  mode="NULLABLE"),
        bq.SchemaField("page_title",               "STRING",  mode="NULLABLE"),
        bq.SchemaField("screen_page_views",        "INT64",   mode="NULLABLE"),
        bq.SchemaField("active_users",             "INT64",   mode="NULLABLE"),
        bq.SchemaField("new_users",                "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",               "INT64",   mode="NULLABLE"),
        bq.SchemaField("average_session_duration", "FLOAT64", mode="NULLABLE"),
    ]


def _schema_page_source():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("page_path",                     "STRING",  mode="NULLABLE"),
        bq.SchemaField("page_title",                    "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_default_channel_group", "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_source",                "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_medium",                "STRING",  mode="NULLABLE"),
        bq.SchemaField("session_campaign_name",         "STRING",  mode="NULLABLE"),
        bq.SchemaField("sessions",                      "INT64",   mode="NULLABLE"),
        bq.SchemaField("active_users",                  "INT64",   mode="NULLABLE"),
        bq.SchemaField("screen_page_views",             "INT64",   mode="NULLABLE"),
        bq.SchemaField("engaged_sessions",              "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",                    "INT64",   mode="NULLABLE"),
        bq.SchemaField("user_engagement_duration",      "FLOAT64", mode="NULLABLE"),
    ]


def _schema_paid_entity():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("source_platform",   "STRING", mode="NULLABLE"),
        bq.SchemaField("session_source",    "STRING", mode="NULLABLE"),
        bq.SchemaField("session_medium",    "STRING", mode="NULLABLE"),
        bq.SchemaField("campaign_id",       "STRING", mode="NULLABLE"),
        bq.SchemaField("campaign_name",     "STRING", mode="NULLABLE"),
        bq.SchemaField("manual_term",       "STRING", mode="NULLABLE"),
        bq.SchemaField("manual_ad_content", "STRING", mode="NULLABLE"),
        bq.SchemaField("sessions",          "INT64",  mode="NULLABLE"),
        bq.SchemaField("engaged_sessions",  "INT64",  mode="NULLABLE"),
        bq.SchemaField("key_events",        "INT64",  mode="NULLABLE"),
        bq.SchemaField("screen_page_views", "INT64",  mode="NULLABLE"),
    ]


def _schema_paid_key_event():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("campaign_id",       "STRING", mode="NULLABLE"),
        bq.SchemaField("campaign_name",     "STRING", mode="NULLABLE"),
        bq.SchemaField("manual_term",       "STRING", mode="NULLABLE"),
        bq.SchemaField("manual_ad_content", "STRING", mode="NULLABLE"),
        bq.SchemaField("event_name",        "STRING", mode="NULLABLE"),
        bq.SchemaField("key_events",        "INT64",  mode="NULLABLE"),
    ]


def _schema_google_entity():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("campaign_id",   "STRING", mode="NULLABLE"),
        bq.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bq.SchemaField("key_events",    "INT64",  mode="NULLABLE"),
        bq.SchemaField("sessions",      "INT64",  mode="NULLABLE"),
    ]


def _schema_google_key_event():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("campaign_id",   "STRING", mode="NULLABLE"),
        bq.SchemaField("campaign_name", "STRING", mode="NULLABLE"),
        bq.SchemaField("event_name",    "STRING", mode="NULLABLE"),
        bq.SchemaField("key_events",    "INT64",  mode="NULLABLE"),
    ]


def _schema_events():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("event_name",                    "STRING", mode="NULLABLE"),
        bq.SchemaField("session_default_channel_group", "STRING", mode="NULLABLE"),
        bq.SchemaField("session_source",                "STRING", mode="NULLABLE"),
        bq.SchemaField("session_medium",                "STRING", mode="NULLABLE"),
        bq.SchemaField("session_campaign_name",         "STRING", mode="NULLABLE"),
        bq.SchemaField("event_count",                   "INT64",  mode="NULLABLE"),
        bq.SchemaField("key_events",                    "INT64",  mode="NULLABLE"),
        bq.SchemaField("total_users",                   "INT64",  mode="NULLABLE"),
    ]


def _schema_tech():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("device_category",  "STRING", mode="NULLABLE"),
        bq.SchemaField("browser",          "STRING", mode="NULLABLE"),
        bq.SchemaField("operating_system", "STRING", mode="NULLABLE"),
        bq.SchemaField("active_users",     "INT64",  mode="NULLABLE"),
        bq.SchemaField("engaged_sessions", "INT64",  mode="NULLABLE"),
        bq.SchemaField("key_events",       "INT64",  mode="NULLABLE"),
    ]


def _schema_user_acq():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("first_user_default_channel_group", "STRING", mode="NULLABLE"),
        bq.SchemaField("first_user_source",                "STRING", mode="NULLABLE"),
        bq.SchemaField("first_user_medium",                "STRING", mode="NULLABLE"),
        bq.SchemaField("new_users",                        "INT64",  mode="NULLABLE"),
        bq.SchemaField("active_users",                     "INT64",  mode="NULLABLE"),
        bq.SchemaField("key_events",                       "INT64",  mode="NULLABLE"),
        bq.SchemaField("total_users",                      "INT64",  mode="NULLABLE"),
    ]


def _schema_geo():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("country",      "STRING", mode="NULLABLE"),
        bq.SchemaField("region",       "STRING", mode="NULLABLE"),
        bq.SchemaField("city",         "STRING", mode="NULLABLE"),
        bq.SchemaField("active_users", "INT64",  mode="NULLABLE"),
        bq.SchemaField("sessions",     "INT64",  mode="NULLABLE"),
        bq.SchemaField("key_events",   "INT64",  mode="NULLABLE"),
    ]


def _schema_demographics():
    bq = _bq()
    return _common_fields(bq) + [
        bq.SchemaField("user_age_bracket", "STRING",  mode="NULLABLE"),
        bq.SchemaField("user_gender",      "STRING",  mode="NULLABLE"),
        bq.SchemaField("active_users",     "INT64",   mode="NULLABLE"),
        bq.SchemaField("key_events",       "INT64",   mode="NULLABLE"),
        bq.SchemaField("engagement_rate",  "FLOAT64", mode="NULLABLE"),
    ]


_TABLE_SCHEMAS = {
    "ga4_sessions_daily":     _schema_sessions,
    "ga4_landing_pages_daily": _schema_landing_pages,
    "ga4_landing_page_events_daily": _schema_landing_page_events,
    "ga4_user_acq_events_daily": _schema_user_acq_events,
    "ga4_page_events_daily":  _schema_page_events,
    "ga4_pageviews_daily":    _schema_pageviews,
    "ga4_page_source_daily":  _schema_page_source,
    "ga4_paid_entity_daily":  _schema_paid_entity,
    "ga4_paid_key_event_daily": _schema_paid_key_event,
    "ga4_google_entity_daily": _schema_google_entity,
    "ga4_google_key_event_daily": _schema_google_key_event,
    "ga4_events_daily":       _schema_events,
    "ga4_tech_daily":         _schema_tech,
    "ga4_user_acq_daily":     _schema_user_acq,
    "ga4_geo_daily":          _schema_geo,
    "ga4_demographics_daily": _schema_demographics,
}

# Legacy table names that have been renamed — dropped on first ensure_ga4_tables run.
_LEGACY_TABLES = ["ga4_pages_daily"]


def ensure_ga4_tables() -> None:
    bq = _bq()
    client = _client()
    project = _project_id()
    dataset = _dataset_id()
    dataset_ref = f"{project}.{dataset}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    # Drop renamed legacy tables.
    for old_name in _LEGACY_TABLES:
        client.delete_table(f"{dataset_ref}.{old_name}", not_found_ok=True, timeout=30)

    for table_name, schema_fn in _TABLE_SCHEMAS.items():
        table_id = f"{dataset_ref}.{table_name}"
        schema = schema_fn()

        try:
            existing = client.get_table(table_id, timeout=30)
            existing_cols = {f.name for f in existing.schema}
            required_cols = {f.name for f in schema}

            if "client_key" not in existing_cols:
                # Predates the snake_case migration — drop and recreate.
                _log.info(
                    "GA4 %s missing client_key (pre-migration schema) — dropping for recreation",
                    table_name,
                )
                client.delete_table(table_id, timeout=30)
            elif not required_cols.issubset(existing_cols):
                # Schema has new columns the live table lacks — add them non-destructively.
                missing = required_cols - existing_cols
                _log.info("GA4 %s: adding missing columns %s via schema update", table_name, missing)
                table_obj = bq.Table(table_id, schema=schema)
                table_obj.time_partitioning = bq.TimePartitioning(field="date")
                client.update_table(table_obj, ["schema"])
        except Exception:
            pass  # Table doesn't exist yet — create below.

        table = bq.Table(table_id, schema=schema)
        table.time_partitioning = bq.TimePartitioning(field="date")
        client.create_table(table, exists_ok=True, timeout=30)

    _log.info("GA4 tables ensured in %s", dataset_ref)


# ── Write helpers ─────────────────────────────────────────────────────────────

def _write_table(
    table_name: str,
    rows: list[dict[str, Any]],
    schema_fn,
    *,
    client_key: str,
    property_id: str,
    start: str,
    end: str,
) -> int:
    if not rows:
        _log.info("GA4 %s — 0 rows from API, skipping write", table_name)
        return 0
    bq = _bq()
    client = _client()
    table_id = _table_ref(table_name)
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' "
        f"  AND property_id = '{property_id}' "
        f"  AND date BETWEEN '{start}' AND '{end}'"
    ).result(timeout=120)
    job_config = bq.LoadJobConfig(
        schema=schema_fn(),
        write_disposition="WRITE_APPEND",
    )
    client.load_table_from_json(rows, table_id, job_config=job_config).result(timeout=180)
    _log.info("GA4 wrote %d rows → %s [client=%s property=%s]", len(rows), table_name, client_key, property_id)
    return len(rows)


# ── Main sync ─────────────────────────────────────────────────────────────────

def sync_ga4_to_bq(
    property_id: str,
    *,
    start: str,
    end: str,
    refresh_token: str,
    client_key: str,
) -> dict[str, Any]:
    """Pull all GA4 report types and write to BQ. Returns row counts per table."""
    import ga4_reporting_service as ga4

    ensure_ga4_tables()

    access_token = ga4._get_access_token(refresh_token)
    _log.info(
        "GA4 sync: client=%s property=%s start=%s end=%s dataset=%s",
        client_key, property_id, start, end, _dataset_id(),
    )

    fetchers = [
        ("sessions",      ga4.fetch_sessions_daily,      "ga4_sessions_daily",      _schema_sessions),
        ("landing_pages", ga4.fetch_landing_pages_daily,  "ga4_landing_pages_daily", _schema_landing_pages),
        ("landing_page_events", ga4.fetch_landing_page_events_daily, "ga4_landing_page_events_daily", _schema_landing_page_events),
        ("user_acq_events", ga4.fetch_user_acq_events_daily, "ga4_user_acq_events_daily", _schema_user_acq_events),
        ("page_events",   ga4.fetch_page_events_daily,    "ga4_page_events_daily",   _schema_page_events),
        ("pageviews",     ga4.fetch_pageviews_daily,      "ga4_pageviews_daily",     _schema_pageviews),
        ("page_source",   ga4.fetch_page_source_daily,    "ga4_page_source_daily",   _schema_page_source),
        ("paid_entity",   ga4.fetch_paid_entity_daily,    "ga4_paid_entity_daily",   _schema_paid_entity),
        ("paid_key_event", ga4.fetch_paid_key_event_daily, "ga4_paid_key_event_daily", _schema_paid_key_event),
        ("paid_google_entity", ga4.fetch_paid_google_entity_daily, "ga4_google_entity_daily", _schema_google_entity),
        ("paid_google_key_event", ga4.fetch_paid_google_key_event_daily, "ga4_google_key_event_daily", _schema_google_key_event),
        ("events",        ga4.fetch_events_daily,         "ga4_events_daily",        _schema_events),
        ("tech",          ga4.fetch_tech_daily,           "ga4_tech_daily",          _schema_tech),
        ("user_acq",      ga4.fetch_user_acq_daily,       "ga4_user_acq_daily",      _schema_user_acq),
        ("geo",           ga4.fetch_geo_daily,            "ga4_geo_daily",           _schema_geo),
        ("demographics",  ga4.fetch_demographics_daily,   "ga4_demographics_daily",  _schema_demographics),
    ]
    # Geo and demographics may return 0 rows (Google Signals not enabled, no
    # audience data) or a 400 from the API — treat as optional so they don't
    # surface as errors in the sync run status.
    _OPTIONAL_KEYS = {"geo", "demographics", "paid_entity", "paid_key_event", "paid_google_entity", "paid_google_key_event"}

    errors: dict[str, str] = {}
    counts: dict[str, int] = {}

    for key, fetch_fn, table_name, schema_fn in fetchers:
        try:
            rows = fetch_fn(property_id, start, end, access_token, client_key=client_key)
            if not rows and key in _OPTIONAL_KEYS:
                _log.info("GA4 %s — 0 rows (optional), skipping", key)
                counts[key] = 0
                continue
            n = _write_table(
                table_name, rows, schema_fn,
                client_key=client_key, property_id=property_id,
                start=start, end=end,
            )
            counts[key] = n
        except Exception as exc:
            if key in _OPTIONAL_KEYS:
                _log.info("GA4 %s — optional fetch skipped: %s", key, exc)
                counts[key] = 0
            else:
                _log.warning("GA4 sync error [%s]: %s", key, exc)
                errors[key] = str(exc)[:300]
                counts[key] = 0

    total = sum(counts.values())
    _log.info(
        "GA4 sync complete [%s]: %s | errors=%s",
        client_key,
        " ".join(f"{k}={v}" for k, v in counts.items()),
        list(errors.keys()) or "none",
    )
    return {"total_rows": total, "counts": counts, "errors": errors}


# ── Health check ──────────────────────────────────────────────────────────────

def check_ga4_health(*, client_key: str, property_id: str) -> dict[str, Any]:
    """Validation query: row counts, date range, and yesterday coverage per table."""
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client = _client()
    dataset_ref = f"{_project_id()}.{_dataset_id()}"
    results = {}
    for table_name in _TABLE_SCHEMAS:
        try:
            sql = f"""
            SELECT
              COUNT(*) AS row_count,
              MIN(date) AS earliest_date,
              MAX(date) AS latest_date,
              COUNTIF(date = '{yesterday}') AS yesterday_rows
            FROM `{dataset_ref}.{table_name}`
            WHERE client_key = '{client_key}' AND property_id = '{property_id}'
            """
            rows = list(client.query(sql).result())
            r = dict(rows[0]) if rows else {}
            results[table_name] = {
                "row_count": r.get("row_count", 0),
                "earliest_date": str(r.get("earliest_date") or ""),
                "latest_date": str(r.get("latest_date") or ""),
                "has_yesterday": int(r.get("yesterday_rows") or 0) > 0,
            }
        except Exception as exc:
            results[table_name] = {"error": str(exc)[:200]}
    return results
