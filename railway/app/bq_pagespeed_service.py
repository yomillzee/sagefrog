"""Sync Google PageSpeed Insights scores -> BigQuery raw tables.

PSI has no historical API — each audit is a point-in-time measurement. We call
pagespeed_service.fetch_scores() once per sync and write the result as a dated
row (metric_date = today), building the dated time series in BQ (weekly cadence;
see PageSpeedConnector.min_sync_interval_days) that powers the score-over-time
trend on the Site Performance tab.

All data lives in {project}.raw_pagespeed.scores_daily. Every row includes
client_key + url + strategy for multi-tenant idempotency. DELETE is scoped to
client_key + url + strategy + metric_date so re-running a sync for the same day
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

_DEFAULT_PAGESPEED_DATASET = "raw_pagespeed"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_pagespeed_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    pagespeed_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (pagespeed_dataset_id or "").strip()
            or (os.getenv("BQ_PAGESPEED_DATASET_ID") or "").strip()
            or _DEFAULT_PAGESPEED_DATASET
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
        "dataset": _DEFAULT_PAGESPEED_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    project = (_ctx().get("project") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for PageSpeed reads/writes. Call "
            "bq_pagespeed_service.route(bq_project_id=...) for this client. Refusing "
            "to silently fall back to another client's project."
        )
    return project


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_PAGESPEED_DATASET).strip()


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


def _schema_scores_daily(bq):
    return [
        bq.SchemaField("client_key",     "STRING",  mode="REQUIRED"),
        bq.SchemaField("url",            "STRING",  mode="REQUIRED"),
        bq.SchemaField("strategy",       "STRING",  mode="REQUIRED"),
        bq.SchemaField("metric_date",    "DATE",    mode="REQUIRED"),
        # Lighthouse category scores, 0–100.
        bq.SchemaField("performance",    "INT64",   mode="NULLABLE"),
        bq.SchemaField("accessibility",  "INT64",   mode="NULLABLE"),
        bq.SchemaField("best_practices", "INT64",   mode="NULLABLE"),
        bq.SchemaField("seo",            "INT64",   mode="NULLABLE"),
        # Lab Core Web Vitals.
        bq.SchemaField("lcp_ms",         "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("cls",            "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("tbt_ms",         "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("fcp_ms",         "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("speed_index_ms", "FLOAT64", mode="NULLABLE"),
        bq.SchemaField("tti_ms",         "FLOAT64", mode="NULLABLE"),
        # Field data (CrUX), null when Google lacks real-user data.
        bq.SchemaField("crux_lcp_ms",    "INT64",   mode="NULLABLE"),
        bq.SchemaField("crux_cls",       "INT64",   mode="NULLABLE"),
        bq.SchemaField("crux_inp_ms",    "INT64",   mode="NULLABLE"),
        bq.SchemaField("error",          "STRING",  mode="NULLABLE"),
        bq.SchemaField("synced_at",      "TIMESTAMP", mode="NULLABLE"),
    ]


def ensure_pagespeed_tables() -> None:
    bq = _bq()
    client = _client()
    dataset_ref = f"{_project_id()}.{_dataset_id()}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    table = bq.Table(f"{dataset_ref}.scores_daily", schema=_schema_scores_daily(bq))
    table.time_partitioning = bq.TimePartitioning(field="metric_date")
    client.create_table(table, exists_ok=True, timeout=30)
    _log.info("PageSpeed tables ensured in %s", dataset_ref)


def _mart_dataset_id() -> str:
    return (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()


def create_pagespeed_mart_views() -> dict[str, Any]:
    """Build the mart view the Site Performance tab reads for its latest scorecard."""
    bq = _bq()
    client = _client()
    project = _project_id()
    scores_table = f"`{project}.{_dataset_id()}.scores_daily`"
    mart_ref = f"{project}.{_mart_dataset_id()}"
    client.create_dataset(bq.Dataset(mart_ref), exists_ok=True, timeout=30)

    view_sql = f"""
        SELECT * EXCEPT(rn) FROM (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY client_key, url, strategy ORDER BY metric_date DESC
          ) AS rn
          FROM {scores_table}
        )
        WHERE rn = 1
    """
    results: dict[str, Any] = {}
    view_id = f"{mart_ref}.vw_pagespeed_latest"
    try:
        client.query(f"CREATE OR REPLACE VIEW `{view_id}` AS\n{view_sql}").result(timeout=60)
        results["vw_pagespeed_latest"] = "created"
        _log.info("PageSpeed mart view ensured: %s", view_id)
    except Exception as exc:
        results["vw_pagespeed_latest"] = f"error: {exc!s:.200}"
        _log.warning("PageSpeed mart view failed: %s", exc)
    return results


def sync_pagespeed_to_bq(
    url: str,
    *,
    client_key: str,
    strategy: str = "desktop",
) -> dict[str, Any]:
    """Run one PSI audit for `url` and write the scored row to BQ. Returns row count."""
    import pagespeed_service

    ensure_pagespeed_tables()

    now = datetime.now(tz=timezone.utc).isoformat()
    today = date.today().isoformat()

    snapshot = pagespeed_service.build_pagespeed_snapshot(url, strategy)
    target = snapshot.get("url") or url
    strat = snapshot.get("strategy") or strategy
    if snapshot.get("error"):
        return {"total_rows": 0, "errors": {"snapshot": snapshot["error"]}}

    def _i(v: Any) -> int | None:
        return int(v) if v is not None else None

    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    row = {
        "client_key": client_key,
        "url": target,
        "strategy": strat,
        "metric_date": today,
        "performance": snapshot.get("performance"),
        "accessibility": snapshot.get("accessibility"),
        "best_practices": snapshot.get("best_practices"),
        "seo": snapshot.get("seo"),
        "lcp_ms": _f(snapshot.get("lcp_ms")),
        "cls": _f(snapshot.get("cls")),
        "tbt_ms": _f(snapshot.get("tbt_ms")),
        "fcp_ms": _f(snapshot.get("fcp_ms")),
        "speed_index_ms": _f(snapshot.get("speed_index_ms")),
        "tti_ms": _f(snapshot.get("tti_ms")),
        "crux_lcp_ms": _i(snapshot.get("crux_lcp_ms")),
        "crux_cls": _i(snapshot.get("crux_cls")),
        "crux_inp_ms": _i(snapshot.get("crux_inp_ms")),
        "error": None,
        "synced_at": now,
    }

    bq = _bq()
    client = _client()
    table_id = _table_ref("scores_daily")
    client.query(
        f"DELETE FROM `{table_id}` "
        f"WHERE client_key = @ck AND url = @url AND strategy = @strat AND metric_date = @d",
        job_config=bq.QueryJobConfig(query_parameters=[
            bq.ScalarQueryParameter("ck", "STRING", client_key),
            bq.ScalarQueryParameter("url", "STRING", target),
            bq.ScalarQueryParameter("strat", "STRING", strat),
            bq.ScalarQueryParameter("d", "DATE", today),
        ]),
    ).result(timeout=120)
    client.load_table_from_json(
        [row], table_id,
        job_config=bq.LoadJobConfig(schema=_schema_scores_daily(bq), write_disposition="WRITE_APPEND"),
    ).result(timeout=180)

    errors: dict[str, str] = {}
    try:
        mart_result = create_pagespeed_mart_views()
        errors = {k: v for k, v in mart_result.items() if str(v).startswith("error")}
    except Exception as exc:
        errors["mart_views"] = str(exc)[:300]

    _log.info("PageSpeed sync complete [%s/%s/%s]", client_key, target, strat)
    return {"total_rows": 1, "errors": errors}


def fetch_latest_snapshot(
    *,
    client_key: str,
    project: str | None = None,
    mart_dataset: str | None = None,
    strategy: str = "desktop",
) -> dict[str, Any]:
    """Read the latest synced PSI row back out of BQ for the Site Performance tab.

    Returns {} when there's no data yet. Shape matches pagespeed_service.fetch_scores
    plus a `history` list (metric_date + the four scores, oldest→newest) for the trend.
    """
    import bigquery_service

    proj = (project or "").strip()
    if not proj:
        raise RuntimeError(
            "fetch_latest_snapshot requires an explicit project — refusing to silently "
            "fall back to another client's project."
        )
    mart_ds = (mart_dataset or "").strip() or _mart_dataset_id()
    raw_ds = _DEFAULT_PAGESPEED_DATASET
    client = bigquery_service.build_client(proj)
    bq = _bq()

    latest_view = f"`{proj}.{mart_ds}.vw_pagespeed_latest`"
    params = [
        bq.ScalarQueryParameter("client_key", "STRING", client_key),
        bq.ScalarQueryParameter("strat", "STRING", strategy),
    ]
    rows = list(client.query(
        f"SELECT * FROM {latest_view} WHERE client_key = @client_key AND strategy = @strat LIMIT 1",
        job_config=bq.QueryJobConfig(query_parameters=params),
    ).result(timeout=30))
    if not rows:
        return {}
    latest = dict(rows[0].items())

    hist_rows = list(client.query(
        f"SELECT metric_date, performance, accessibility, best_practices, seo, "
        f"lcp_ms, cls, tbt_ms, fcp_ms, speed_index_ms, tti_ms "
        f"FROM `{proj}.{raw_ds}.scores_daily` "
        f"WHERE client_key = @client_key AND strategy = @strat "
        f"ORDER BY metric_date ASC LIMIT 365",
        job_config=bq.QueryJobConfig(query_parameters=params),
    ).result(timeout=30))
    # Scores drive the "Scores over time" trend; the lab metrics feed the
    # Core Web Vitals sparklines. Both are per-date series oldest→newest.
    history = [
        {
            "metric_date": r["metric_date"].isoformat() if r["metric_date"] else None,
            "performance": r["performance"],
            "accessibility": r["accessibility"],
            "best_practices": r["best_practices"],
            "seo": r["seo"],
            "lcp_ms": r["lcp_ms"],
            "cls": r["cls"],
            "tbt_ms": r["tbt_ms"],
            "fcp_ms": r["fcp_ms"],
            "speed_index_ms": r["speed_index_ms"],
            "tti_ms": r["tti_ms"],
        }
        for r in hist_rows
    ]

    out = dict(latest)
    synced = latest.get("synced_at")
    out["fetched_at"] = synced.isoformat() if hasattr(synced, "isoformat") else synced
    md = latest.get("metric_date")
    out["metric_date"] = md.isoformat() if hasattr(md, "isoformat") else md
    out["history"] = history
    return out
