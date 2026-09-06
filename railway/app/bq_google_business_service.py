"""Sync Google Business Profile metrics + reviews -> BigQuery raw tables.

Two tables under {project}.raw_google_business:

  * ``location_metrics_daily`` — one row per location per day, carrying every
    daily metric Google exposes. Unlike PageSpeed (a point-in-time audit we
    snapshot), Google backfills a real daily history, so a sync re-requests a
    window and replaces it rather than appending one dated row.
  * ``reviews`` — one row per review, replaced wholesale per location, since a
    review's text, rating, and our reply can all change after it is written.

Both are keyed by client_key + location_id for multi-tenant isolation, and both
DELETE their target slice before loading so a re-run is idempotent.

There are deliberately no mart views here. The PageSpeed mart exists because
"latest row per client/url/strategy" needs a window function; these reads are
plain date-range aggregates that BigQuery handles directly, and an extra view
would be one more object to keep in step for no gain.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_DATASET = "raw_google_business"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_google_business_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (dataset_id or "").strip()
            or (os.getenv("BQ_GOOGLE_BUSINESS_DATASET_ID") or "").strip()
            or _DEFAULT_DATASET
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
        "dataset": _DEFAULT_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    project = (_ctx().get("project") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for Google Business reads/writes. Call "
            "bq_google_business_service.route(bq_project_id=...) for this client. "
            "Refusing to silently fall back to another client's project."
        )
    return project


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_DATASET).strip()


def _bq():
    from google.cloud import bigquery
    return bigquery


def _client():
    import bigquery_service
    return bigquery_service.build_client(_project_id(), credentials_env=_ctx().get("credentials_env"))


def _table_ref(table_name: str) -> str:
    return f"{_project_id()}.{_dataset_id()}.{table_name}"


def _schema_metrics_daily(bq):
    """Daily location metrics. Metric columns are generated from the service's
    own list so a metric added there can't silently miss the table."""
    import google_business_service as gbs

    fields = [
        bq.SchemaField("client_key",    "STRING", mode="REQUIRED"),
        bq.SchemaField("location_id",   "STRING", mode="REQUIRED"),
        bq.SchemaField("location_name", "STRING", mode="NULLABLE"),
        bq.SchemaField("metric_date",   "DATE",   mode="REQUIRED"),
    ]
    fields.extend(
        bq.SchemaField(col, "INT64", mode="NULLABLE") for col in gbs.METRIC_COLUMNS
    )
    fields.append(bq.SchemaField("synced_at", "TIMESTAMP", mode="NULLABLE"))
    return fields


def _schema_reviews(bq):
    return [
        bq.SchemaField("client_key",    "STRING", mode="REQUIRED"),
        bq.SchemaField("location_id",   "STRING", mode="REQUIRED"),
        bq.SchemaField("location_name", "STRING", mode="NULLABLE"),
        bq.SchemaField("review_id",     "STRING", mode="REQUIRED"),
        bq.SchemaField("review_date",   "DATE",   mode="NULLABLE"),
        bq.SchemaField("reviewer_name", "STRING", mode="NULLABLE"),
        bq.SchemaField("star_rating",   "INT64",  mode="NULLABLE"),
        bq.SchemaField("comment",       "STRING", mode="NULLABLE"),
        bq.SchemaField("create_time",   "TIMESTAMP", mode="NULLABLE"),
        bq.SchemaField("update_time",   "TIMESTAMP", mode="NULLABLE"),
        bq.SchemaField("reply_comment", "STRING", mode="NULLABLE"),
        bq.SchemaField("reply_time",    "TIMESTAMP", mode="NULLABLE"),
        bq.SchemaField("synced_at",     "TIMESTAMP", mode="NULLABLE"),
    ]


def ensure_tables() -> None:
    bq = _bq()
    client = _client()
    dataset_ref = f"{_project_id()}.{_dataset_id()}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    metrics = bq.Table(f"{dataset_ref}.location_metrics_daily", schema=_schema_metrics_daily(bq))
    metrics.time_partitioning = bq.TimePartitioning(field="metric_date")
    client.create_table(metrics, exists_ok=True, timeout=30)

    reviews = bq.Table(f"{dataset_ref}.reviews", schema=_schema_reviews(bq))
    reviews.time_partitioning = bq.TimePartitioning(field="review_date")
    client.create_table(reviews, exists_ok=True, timeout=30)
    _log.info("Google Business tables ensured in %s", dataset_ref)


def _review_date(create_time: str | None) -> str | None:
    """Partition key for a review: the date part of its RFC-3339 createTime."""
    if not create_time:
        return None
    try:
        return datetime.fromisoformat(str(create_time).replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return None


def sync_google_business_to_bq(
    *,
    client_key: str,
    refresh_token: str,
    account: str,
    locations: list[dict[str, Any]],
    start: date,
    end: date,
    include_reviews: bool = True,
) -> dict[str, Any]:
    """Pull metrics (and reviews) for every configured location and write to BQ.

    Errors are collected per location rather than raised: one location losing
    its Google permission shouldn't cost a multi-location client the rest of
    their data. The exception is a zero-quota 429 — that is account-wide and
    identical for every location, so it aborts immediately with the setup
    message instead of repeating itself once per location.
    """
    import google_business_service as gbs

    ensure_tables()
    now = datetime.now(tz=timezone.utc).isoformat()
    bq = _bq()
    client = _client()

    metric_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    synced_locations: list[str] = []

    for loc in locations:
        location_id = (loc.get("id") or "").strip()
        location_name = loc.get("name") or location_id
        if not location_id:
            continue
        try:
            daily = gbs.fetch_daily_metrics(refresh_token, location_id, start=start, end=end)
        except gbs.GoogleBusinessAccessNotApproved:
            raise
        except Exception as exc:
            errors[f"metrics:{location_name}"] = str(exc)[:300]
            continue

        synced_locations.append(location_id)
        for row in daily:
            metric_rows.append({
                "client_key": client_key,
                "location_id": location_id,
                "location_name": location_name,
                "metric_date": row["metric_date"],
                **{col: row.get(col) for col in gbs.METRIC_COLUMNS},
                "synced_at": now,
            })

        if not include_reviews:
            continue
        try:
            reviews = gbs.fetch_reviews(refresh_token, account, location_id)
        except gbs.GoogleBusinessAccessNotApproved:
            raise
        except Exception as exc:
            # Reviews live on the legacy v4 API, which is the likeliest half of
            # this connector to break. Losing them must not lose the metrics.
            errors[f"reviews:{location_name}"] = str(exc)[:300]
            continue
        for review in reviews.get("reviews") or []:
            review_rows.append({
                "client_key": client_key,
                "location_id": location_id,
                "location_name": location_name,
                "review_id": review.get("review_id") or "",
                "review_date": _review_date(review.get("create_time")),
                "reviewer_name": review.get("reviewer_name"),
                "star_rating": review.get("star_rating"),
                "comment": review.get("comment"),
                "create_time": review.get("create_time"),
                "update_time": review.get("update_time"),
                "reply_comment": review.get("reply_comment"),
                "reply_time": review.get("reply_time"),
                "synced_at": now,
            })

    total_rows = 0
    if metric_rows:
        table_id = _table_ref("location_metrics_daily")
        client.query(
            f"DELETE FROM `{table_id}` WHERE client_key = @ck "
            f"AND location_id IN UNNEST(@locs) "
            f"AND metric_date BETWEEN @start AND @end",
            job_config=bq.QueryJobConfig(query_parameters=[
                bq.ScalarQueryParameter("ck", "STRING", client_key),
                bq.ArrayQueryParameter("locs", "STRING", synced_locations),
                bq.ScalarQueryParameter("start", "DATE", start.isoformat()),
                bq.ScalarQueryParameter("end", "DATE", end.isoformat()),
            ]),
        ).result(timeout=120)
        client.load_table_from_json(
            metric_rows, table_id,
            job_config=bq.LoadJobConfig(
                schema=_schema_metrics_daily(bq), write_disposition="WRITE_APPEND"
            ),
        ).result(timeout=300)
        total_rows += len(metric_rows)

    if review_rows:
        table_id = _table_ref("reviews")
        # Replace every review for the locations we just read: ratings, text and
        # our replies are all mutable, so an append-only merge would keep stale
        # copies alongside the current ones.
        review_locations = sorted({r["location_id"] for r in review_rows})
        client.query(
            f"DELETE FROM `{table_id}` WHERE client_key = @ck AND location_id IN UNNEST(@locs)",
            job_config=bq.QueryJobConfig(query_parameters=[
                bq.ScalarQueryParameter("ck", "STRING", client_key),
                bq.ArrayQueryParameter("locs", "STRING", review_locations),
            ]),
        ).result(timeout=120)
        client.load_table_from_json(
            review_rows, table_id,
            job_config=bq.LoadJobConfig(
                schema=_schema_reviews(bq), write_disposition="WRITE_APPEND"
            ),
        ).result(timeout=300)
        total_rows += len(review_rows)

    _log.info(
        "Google Business sync complete [%s]: %d metric rows, %d reviews, %d locations",
        client_key, len(metric_rows), len(review_rows), len(synced_locations),
    )
    return {"total_rows": total_rows, "locations": len(synced_locations), "errors": errors}


# ---------------------------------------------------------------------------
# Reads for the dashboard
# ---------------------------------------------------------------------------

def _empty_summary() -> dict[str, Any]:
    import google_business_service as gbs

    return {
        "totals": {col: 0 for col in gbs.METRIC_COLUMNS},
        "daily": [],
        "locations": [],
        "reviews": {"average_rating": None, "total": 0, "unanswered": 0, "recent": []},
    }


def fetch_summary(
    *,
    client_key: str,
    project: str | None = None,
    dataset: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Everything the Google Business tab shows, for one client and date range.

    Returns zeroed totals rather than raising when the tables don't exist yet —
    a client whose first sync hasn't run should see an empty tab, not a 500.
    """
    import bigquery_service
    import google_business_service as gbs

    proj = (project or "").strip()
    if not proj:
        raise RuntimeError(
            "fetch_summary requires an explicit project — refusing to silently "
            "fall back to another client's project."
        )
    ds = (dataset or "").strip() or _DEFAULT_DATASET
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=29))

    client = bigquery_service.build_client(proj)
    bq = _bq()
    params = [
        bq.ScalarQueryParameter("ck", "STRING", client_key),
        bq.ScalarQueryParameter("start", "DATE", start.isoformat()),
        bq.ScalarQueryParameter("end", "DATE", end.isoformat()),
    ]
    metrics_table = f"`{proj}.{ds}.location_metrics_daily`"
    sums = ", ".join(f"SUM({col}) AS {col}" for col in gbs.METRIC_COLUMNS)
    out = _empty_summary()

    try:
        rows = list(client.query(
            f"SELECT {sums} FROM {metrics_table} "
            f"WHERE client_key = @ck AND metric_date BETWEEN @start AND @end",
            job_config=bq.QueryJobConfig(query_parameters=params),
        ).result(timeout=30))
        if rows:
            out["totals"] = {col: int(rows[0][col] or 0) for col in gbs.METRIC_COLUMNS}

        daily_rows = list(client.query(
            f"SELECT metric_date, {sums} FROM {metrics_table} "
            f"WHERE client_key = @ck AND metric_date BETWEEN @start AND @end "
            f"GROUP BY metric_date ORDER BY metric_date ASC",
            job_config=bq.QueryJobConfig(query_parameters=params),
        ).result(timeout=30))
        out["daily"] = [
            {
                "metric_date": r["metric_date"].isoformat() if r["metric_date"] else None,
                **{col: int(r[col] or 0) for col in gbs.METRIC_COLUMNS},
            }
            for r in daily_rows
        ]

        # Busiest location first, by total views across all four impression
        # surfaces — ordering by a single one would rank a Maps-heavy location
        # above a Search-heavy one with more views overall.
        total_views = " + ".join(f"SUM({col})" for col in gbs.IMPRESSION_COLUMNS)
        loc_rows = list(client.query(
            f"SELECT location_id, ANY_VALUE(location_name) AS location_name, {sums} "
            f"FROM {metrics_table} "
            f"WHERE client_key = @ck AND metric_date BETWEEN @start AND @end "
            f"GROUP BY location_id ORDER BY ({total_views}) DESC LIMIT 100",
            job_config=bq.QueryJobConfig(query_parameters=params),
        ).result(timeout=30))
        out["locations"] = [
            {
                "location_id": r["location_id"],
                "location_name": r["location_name"],
                **{col: int(r[col] or 0) for col in gbs.METRIC_COLUMNS},
            }
            for r in loc_rows
        ]
    except Exception as exc:
        _log.info("Google Business metrics unavailable for %s: %s", client_key, exc)

    # Reviews are deliberately NOT filtered to the date range: "what is our
    # rating and what is waiting for a reply" is a current-state question, and
    # scoping it to the selected window would hide an angry review from March
    # whenever someone looks at April.
    reviews_table = f"`{proj}.{ds}.reviews`"
    try:
        agg = list(client.query(
            f"SELECT AVG(star_rating) AS avg_rating, COUNT(*) AS total, "
            f"COUNTIF(reply_comment IS NULL OR reply_comment = '') AS unanswered "
            f"FROM {reviews_table} WHERE client_key = @ck",
            job_config=bq.QueryJobConfig(query_parameters=[params[0]]),
        ).result(timeout=30))
        if agg and agg[0]["total"]:
            out["reviews"]["average_rating"] = (
                round(float(agg[0]["avg_rating"]), 2) if agg[0]["avg_rating"] is not None else None
            )
            out["reviews"]["total"] = int(agg[0]["total"] or 0)
            out["reviews"]["unanswered"] = int(agg[0]["unanswered"] or 0)

        recent = list(client.query(
            f"SELECT location_name, reviewer_name, star_rating, comment, create_time, "
            f"reply_comment FROM {reviews_table} WHERE client_key = @ck "
            f"ORDER BY create_time DESC LIMIT 20",
            job_config=bq.QueryJobConfig(query_parameters=[params[0]]),
        ).result(timeout=30))
        out["reviews"]["recent"] = [
            {
                "location_name": r["location_name"],
                "reviewer_name": r["reviewer_name"],
                "star_rating": r["star_rating"],
                "comment": r["comment"],
                "create_time": (
                    r["create_time"].isoformat() if hasattr(r["create_time"], "isoformat")
                    else r["create_time"]
                ),
                "answered": bool(r["reply_comment"]),
            }
            for r in recent
        ]
    except Exception as exc:
        _log.info("Google Business reviews unavailable for %s: %s", client_key, exc)

    out["start_date"] = start.isoformat()
    out["end_date"] = end.isoformat()
    return out
