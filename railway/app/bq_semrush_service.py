"""Sync SEMrush domain analytics -> BigQuery raw tables.

Unlike the ad-platform connectors, SEMrush has no per-day performance API —
domain_ranks/domain_organic/backlinks_overview are point-in-time snapshots.
We call semrush_service.build_semrush_snapshot() once per sync and write the
result as a dated row (metric_date = today), giving a daily time series in BQ
that the connector's "backfill range" and "daily sync" wizard steps apply to
naturally, even though there's no historical backfill possible on day one.

All tables live in {project}.raw_semrush. Every table includes client_key and
domain for multi-tenant idempotency. DELETE is scoped to
client_key + domain + metric_date so re-running a sync for the same day
doesn't duplicate rows.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_SEMRUSH_DATASET = "raw_semrush"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_semrush_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    semrush_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (semrush_dataset_id or "").strip()
            or (os.getenv("BQ_SEMRUSH_DATASET_ID") or "").strip()
            or _DEFAULT_SEMRUSH_DATASET
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
        "dataset": _DEFAULT_SEMRUSH_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    r = _ctx()
    project = (r.get("project") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for SEMrush reads/writes. Call bq_semrush_service.route("
            "bq_project_id=...) for this client. Refusing to silently fall back to another "
            "client's project."
        )
    return project


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_SEMRUSH_DATASET).strip()


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


def _schema_domain_overview_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",  mode="REQUIRED"),
        bq.SchemaField("domain",           "STRING",  mode="REQUIRED"),
        bq.SchemaField("database",         "STRING",  mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",    mode="REQUIRED"),
        bq.SchemaField("semrush_rank",     "INT64",   mode="NULLABLE"),
        bq.SchemaField("organic_keywords", "INT64",   mode="NULLABLE"),
        bq.SchemaField("organic_traffic",  "INT64",   mode="NULLABLE"),
        bq.SchemaField("organic_cost",     "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("paid_keywords",    "INT64",   mode="NULLABLE"),
        bq.SchemaField("paid_traffic",     "INT64",   mode="NULLABLE"),
        bq.SchemaField("authority_score",  "INT64",   mode="NULLABLE"),
        bq.SchemaField("ai_overview_keywords", "INT64", mode="NULLABLE"),
        bq.SchemaField("ai_overview_cited",     "INT64", mode="NULLABLE"),
        bq.SchemaField("total_backlinks",  "INT64",   mode="NULLABLE"),
        bq.SchemaField("referring_domains","INT64",   mode="NULLABLE"),
        bq.SchemaField("referring_ips",    "INT64",   mode="NULLABLE"),
        bq.SchemaField("dofollow",         "INT64",   mode="NULLABLE"),
        bq.SchemaField("nofollow",         "INT64",   mode="NULLABLE"),
        bq.SchemaField("error",            "STRING",  mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def _schema_keywords_daily(bq):
    return [
        bq.SchemaField("client_key",     "STRING",  mode="REQUIRED"),
        bq.SchemaField("domain",         "STRING",  mode="REQUIRED"),
        bq.SchemaField("database",       "STRING",  mode="NULLABLE"),
        bq.SchemaField("metric_date",    "DATE",    mode="REQUIRED"),
        bq.SchemaField("keyword",        "STRING",  mode="NULLABLE"),
        bq.SchemaField("position",       "INT64",   mode="NULLABLE"),
        bq.SchemaField("search_volume",  "INT64",   mode="NULLABLE"),
        bq.SchemaField("cpc",            "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("url",            "STRING",  mode="NULLABLE"),
        bq.SchemaField("traffic_pct",    "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("ai_overview",       "BOOL", mode="NULLABLE"),
        bq.SchemaField("ai_overview_cited", "BOOL", mode="NULLABLE"),
        bq.SchemaField("synced_at",      "TIMESTAMP", mode="NULLABLE"),
    ]


def ensure_semrush_tables() -> None:
    bq = _bq()
    client = _client()
    project = _project_id()
    dataset = _dataset_id()
    dataset_ref = f"{project}.{dataset}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    for table_name, schema_fn in [
        ("domain_overview_daily", _schema_domain_overview_daily),
        ("keywords_daily", _schema_keywords_daily),
    ]:
        table_id = f"{dataset_ref}.{table_name}"
        schema = schema_fn(bq)
        table = bq.Table(table_id, schema=schema)
        table.time_partitioning = bq.TimePartitioning(field="metric_date")
        client.create_table(table, exists_ok=True, timeout=30)
    _log.info("SEMrush tables ensured in %s", dataset_ref)


def _mart_dataset_id() -> str:
    return (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()


def create_semrush_mart_views() -> dict[str, Any]:
    """Build mart views over raw_semrush that the Search Console panel reads.

    Creates two views in marketing_marts:
      - vw_semrush_overview_latest  — most recent domain_overview_daily row per client/domain
      - vw_semrush_keywords_latest  — keyword rows from that same latest sync date
    """
    bq = _bq()
    client = _client()
    project = _project_id()
    raw_dataset = _dataset_id()
    mart_dataset = _mart_dataset_id()
    overview_table = f"`{project}.{raw_dataset}.domain_overview_daily`"
    keywords_table = f"`{project}.{raw_dataset}.keywords_daily`"
    mart_ref = f"{project}.{mart_dataset}"

    client.create_dataset(bq.Dataset(mart_ref), exists_ok=True, timeout=30)

    views = {
        "vw_semrush_overview_latest": f"""
            SELECT * EXCEPT(rn) FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY client_key, domain ORDER BY metric_date DESC
              ) AS rn
              FROM {overview_table}
            )
            WHERE rn = 1
        """,
        "vw_semrush_keywords_latest": f"""
            SELECT k.*
            FROM {keywords_table} k
            JOIN (
              SELECT client_key, domain, MAX(metric_date) AS latest_date
              FROM {keywords_table}
              GROUP BY client_key, domain
            ) latest
              ON  k.client_key = latest.client_key
              AND k.domain = latest.domain
              AND k.metric_date = latest.latest_date
        """,
    }

    results: dict[str, Any] = {}
    for view_name, select_sql in views.items():
        table_id = f"{mart_ref}.{view_name}"
        sql = f"CREATE OR REPLACE VIEW `{table_id}` AS\n{select_sql}"
        try:
            client.query(sql).result(timeout=60)
            results[view_name] = "created"
            _log.info("SEMrush mart view ensured: %s", table_id)
        except Exception as exc:
            results[view_name] = f"error: {exc!s:.200}"
            _log.warning("SEMrush mart view failed [%s]: %s", view_name, exc)
    return results


def sync_semrush_to_bq(
    domain: str,
    *,
    client_key: str,
    database: str = "us",
    keyword_limit: int = 100,
) -> dict[str, Any]:
    """Fetch a SEMrush snapshot for `domain` and write it to BQ. Returns row counts."""
    import semrush_service

    ensure_semrush_tables()

    now = datetime.now(tz=timezone.utc).isoformat()
    today = date.today().isoformat()
    errors: dict[str, str] = {}

    snapshot = semrush_service.build_semrush_snapshot(domain, database, keyword_limit=keyword_limit)
    if snapshot.get("error"):
        return {"total_rows": 0, "overview_rows": 0, "keyword_rows": 0, "errors": {"snapshot": snapshot["error"]}}

    overview = snapshot.get("overview") or {}
    backlinks = snapshot.get("backlinks") or {}
    keywords = snapshot.get("keywords") or []
    ai_overview = snapshot.get("ai_overview") or {}
    snap_errors = snapshot.get("errors") or {}
    if snap_errors:
        errors.update(snap_errors)

    bq = _bq()
    client = _client()

    overview_row = {
        "client_key": client_key,
        "domain": domain,
        "database": database,
        "metric_date": today,
        "semrush_rank": int(overview.get("semrush_rank") or 0) or None,
        "organic_keywords": int(overview.get("organic_keywords") or 0) or None,
        "organic_traffic": int(overview.get("organic_traffic") or 0) or None,
        "organic_cost": float(overview.get("organic_cost") or 0.0),
        "paid_keywords": int(overview.get("paid_keywords") or 0) or None,
        "paid_traffic": int(overview.get("paid_traffic") or 0) or None,
        "authority_score": int(backlinks.get("authority_score") or overview.get("authority_score") or 0) or None,
        "ai_overview_keywords": int(ai_overview.get("keywords_with_aio") or 0) or None,
        "ai_overview_cited": int(ai_overview.get("keywords_cited") or 0) or None,
        "total_backlinks": int(backlinks.get("total_backlinks") or 0) or None,
        "referring_domains": int(backlinks.get("referring_domains") or 0) or None,
        "referring_ips": int(backlinks.get("referring_ips") or 0) or None,
        "dofollow": int(backlinks.get("dofollow") or 0) or None,
        "nofollow": int(backlinks.get("nofollow") or 0) or None,
        "error": "; ".join(f"{k}: {v}" for k, v in snap_errors.items()) or None,
        "synced_at": now,
    }

    table_id = _table_ref("domain_overview_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = '{client_key}' AND domain = '{domain}' AND metric_date = '{today}'"
    ).result(timeout=120)
    client.load_table_from_json(
        [overview_row], table_id,
        job_config=bq.LoadJobConfig(
            schema=_schema_domain_overview_daily(bq),
            write_disposition="WRITE_APPEND",
            # Backfill new columns (e.g. ai_overview_*) onto pre-existing tables.
            schema_update_options=[bq.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        ),
    ).result(timeout=180)

    keyword_rows = [
        {
            "client_key": client_key,
            "domain": domain,
            "database": database,
            "metric_date": today,
            "keyword": kw.get("keyword") or "",
            "position": int(kw.get("position") or 0) or None,
            "search_volume": int(kw.get("search_volume") or 0) or None,
            "cpc": float(kw.get("cpc") or 0.0),
            "url": kw.get("url") or None,
            "traffic_pct": float(kw.get("traffic_pct") or 0.0),
            "ai_overview": bool(kw.get("ai_overview")),
            "ai_overview_cited": bool(kw.get("ai_overview_cited")),
            "synced_at": now,
        }
        for kw in keywords
        if kw.get("keyword")
    ]

    n_keywords = 0
    if keyword_rows:
        kw_table_id = _table_ref("keywords_daily")
        client.query(
            f"DELETE FROM `{kw_table_id}` "
            f"WHERE client_key = '{client_key}' AND domain = '{domain}' AND metric_date = '{today}'"
        ).result(timeout=120)
        client.load_table_from_json(
            keyword_rows, kw_table_id,
            job_config=bq.LoadJobConfig(
                schema=_schema_keywords_daily(bq),
                write_disposition="WRITE_APPEND",
                # Backfill new columns (e.g. ai_overview) onto pre-existing tables.
                schema_update_options=[bq.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            ),
        ).result(timeout=180)
        n_keywords = len(keyword_rows)

    mart_errors: dict[str, str] = {}
    try:
        mart_result = create_semrush_mart_views()
        mart_errors = {k: v for k, v in mart_result.items() if str(v).startswith("error")}
    except Exception as exc:
        mart_errors["mart_views"] = str(exc)[:300]
    errors.update(mart_errors)

    _log.info(
        "SEMrush sync complete [%s/%s]: overview=1 keywords=%d", client_key, domain, n_keywords,
    )
    return {"total_rows": 1 + n_keywords, "overview_rows": 1, "keyword_rows": n_keywords, "errors": errors}


def fetch_latest_snapshot(*, client_key: str, project: str | None = None, mart_dataset: str | None = None) -> dict[str, Any]:
    """Read the latest synced SEMrush snapshot back out of BQ, shaped exactly like
    semrush_service.build_semrush_snapshot() so it drops straight into
    dashboard.renderers.semrush_renderer.semrush_section_html().
    """
    import bigquery_service

    proj = (project or "").strip()
    if not proj:
        raise RuntimeError(
            "fetch_latest_snapshot requires an explicit project — refusing to silently fall "
            "back to another client's project."
        )
    mart_ds = (mart_dataset or "").strip() or _mart_dataset_id()
    client = bigquery_service.build_client(proj)

    overview_view = f"`{proj}.{mart_ds}.vw_semrush_overview_latest`"
    keywords_view = f"`{proj}.{mart_ds}.vw_semrush_keywords_latest`"

    rows = list(client.query(
        f"SELECT * FROM {overview_view} WHERE client_key = @client_key LIMIT 1",
        job_config=_bq().QueryJobConfig(query_parameters=[
            _bq().ScalarQueryParameter("client_key", "STRING", client_key),
        ]),
    ).result(timeout=30))
    if not rows:
        return {}
    ov = dict(rows[0].items())

    kw_rows = list(client.query(
        f"SELECT keyword, position, search_volume, cpc, url, traffic_pct, ai_overview, ai_overview_cited "
        f"FROM {keywords_view} WHERE client_key = @client_key ORDER BY traffic_pct DESC LIMIT 200",
        job_config=_bq().QueryJobConfig(query_parameters=[
            _bq().ScalarQueryParameter("client_key", "STRING", client_key),
        ]),
    ).result(timeout=30))
    keywords = [dict(r.items()) for r in kw_rows]

    from semrush_service import _position_distribution
    position_dist = _position_distribution(keywords)

    return {
        "domain": ov.get("domain") or "",
        "database": ov.get("database") or "us",
        "overview": {
            "domain": ov.get("domain"),
            "database": ov.get("database"),
            "semrush_rank": ov.get("semrush_rank") or 0,
            "organic_keywords": ov.get("organic_keywords") or 0,
            "organic_traffic": ov.get("organic_traffic") or 0,
            "organic_cost": ov.get("organic_cost") or 0.0,
            "paid_keywords": ov.get("paid_keywords") or 0,
            "paid_traffic": ov.get("paid_traffic") or 0,
            "authority_score": ov.get("authority_score") or 0,
            "error": None,
        },
        "keywords": keywords,
        "backlinks": {
            "domain": ov.get("domain"),
            "authority_score": ov.get("authority_score") or 0,
            "total_backlinks": ov.get("total_backlinks") or 0,
            "referring_domains": ov.get("referring_domains") or 0,
            "referring_ips": ov.get("referring_ips") or 0,
            "dofollow": ov.get("dofollow") or 0,
            "nofollow": ov.get("nofollow") or 0,
            "error": None,
        },
        "ai_overview": {
            "keywords_with_aio": ov.get("ai_overview_keywords") or 0,
            "keywords_cited": ov.get("ai_overview_cited") or 0,
        },
        "position_distribution": position_dist,
        "fetched_at": ov.get("synced_at").isoformat() if ov.get("synced_at") else None,
    }
