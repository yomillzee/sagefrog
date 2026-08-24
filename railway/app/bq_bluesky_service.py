"""Sync Bluesky organic social metrics -> BigQuery raw tables.

Bluesky has no per-day metrics API. ``getProfile`` returns the follower count
*right now*, and every post carries like/repost/reply/quote counters that are
cumulative to now — the same shape as SEMrush's domain snapshots, and unlike
LinkedIn's daily share statistics. So each sync writes a dated snapshot:

  - ``profile_daily`` — one row per client/handle/day: followers, follows, posts.
  - ``posts_daily``   — one row per client/handle/post/day: that post's counters
    as they stood on ``metric_date``. A post published in March still gets a new
    row every day it stays inside the sync window, so late likes are visible.

Two consequences worth remembering when writing SQL over these tables:

  - ``metric_date`` is the **snapshot** date, not the publish date. Post publish
    time is ``post_date`` / ``created_at``.
  - Summing ``like_count`` across dates double-counts. Take the latest snapshot
    per post (``vw_bluesky_posts_latest``) or diff consecutive snapshots for a
    genuine per-day delta.

All tables live in ``{project}.raw_bluesky``. Every table carries client_key and
handle, and DELETEs are scoped to client_key + handle + metric_date so a
re-run for the same day replaces rather than duplicates.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_BLUESKY_DATASET = "raw_bluesky"

_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_bluesky_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    bluesky_dataset_id: str | None = None,
    credentials_env: str | None = None,
):
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "dataset": (
            (bluesky_dataset_id or "").strip()
            or (os.getenv("BQ_BLUESKY_DATASET_ID") or "").strip()
            or _DEFAULT_BLUESKY_DATASET
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
        "dataset": _DEFAULT_BLUESKY_DATASET,
        "credentials_env": None,
    }


def _project_id() -> str:
    project = (_ctx().get("project") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for Bluesky reads/writes. Call "
            "bq_bluesky_service.route(bq_project_id=...) for this client. Refusing to "
            "silently fall back to another client's project."
        )
    return project


def _dataset_id() -> str:
    return (_ctx().get("dataset") or _DEFAULT_BLUESKY_DATASET).strip()


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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _schema_profile_daily(bq):
    return [
        bq.SchemaField("client_key",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("handle",           "STRING",    mode="REQUIRED"),
        bq.SchemaField("did",              "STRING",    mode="NULLABLE"),
        bq.SchemaField("metric_date",      "DATE",      mode="REQUIRED"),
        bq.SchemaField("display_name",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("followers_count",  "INT64",     mode="NULLABLE"),
        bq.SchemaField("follows_count",    "INT64",     mode="NULLABLE"),
        bq.SchemaField("posts_count",      "INT64",     mode="NULLABLE"),
        # Engagement earned by posts published inside the synced window, as of
        # this snapshot. Saves the dashboard a join for the headline numbers.
        bq.SchemaField("window_posts",     "INT64",     mode="NULLABLE"),
        bq.SchemaField("window_likes",     "INT64",     mode="NULLABLE"),
        bq.SchemaField("window_reposts",   "INT64",     mode="NULLABLE"),
        bq.SchemaField("window_replies",   "INT64",     mode="NULLABLE"),
        bq.SchemaField("window_quotes",    "INT64",     mode="NULLABLE"),
        bq.SchemaField("window_engagements", "INT64",   mode="NULLABLE"),
        bq.SchemaField("error",            "STRING",    mode="NULLABLE"),
        bq.SchemaField("synced_at",        "TIMESTAMP", mode="NULLABLE"),
    ]


def _schema_posts_daily(bq):
    return [
        bq.SchemaField("client_key",   "STRING",    mode="REQUIRED"),
        bq.SchemaField("handle",       "STRING",    mode="REQUIRED"),
        bq.SchemaField("did",          "STRING",    mode="NULLABLE"),
        # Snapshot date — NOT the publish date. See the module docstring.
        bq.SchemaField("metric_date",  "DATE",      mode="REQUIRED"),
        bq.SchemaField("post_uri",     "STRING",    mode="REQUIRED"),
        bq.SchemaField("post_cid",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("post_url",     "STRING",    mode="NULLABLE"),
        bq.SchemaField("post_date",    "DATE",      mode="NULLABLE"),
        bq.SchemaField("created_at",   "TIMESTAMP", mode="NULLABLE"),
        bq.SchemaField("text",         "STRING",    mode="NULLABLE"),
        bq.SchemaField("is_reply",     "BOOL",      mode="NULLABLE"),
        bq.SchemaField("embed_type",   "STRING",    mode="NULLABLE"),
        bq.SchemaField("langs",        "STRING",    mode="NULLABLE"),
        bq.SchemaField("like_count",   "INT64",     mode="NULLABLE"),
        bq.SchemaField("repost_count", "INT64",     mode="NULLABLE"),
        bq.SchemaField("reply_count",  "INT64",     mode="NULLABLE"),
        bq.SchemaField("quote_count",  "INT64",     mode="NULLABLE"),
        bq.SchemaField("engagements",  "INT64",     mode="NULLABLE"),
        bq.SchemaField("synced_at",    "TIMESTAMP", mode="NULLABLE"),
    ]


def ensure_bluesky_tables() -> None:
    bq = _bq()
    client = _client()
    dataset_ref = f"{_project_id()}.{_dataset_id()}"
    client.create_dataset(bq.Dataset(dataset_ref), exists_ok=True, timeout=30)

    for table_name, schema_fn in [
        ("profile_daily", _schema_profile_daily),
        ("posts_daily", _schema_posts_daily),
    ]:
        table = bq.Table(f"{dataset_ref}.{table_name}", schema=schema_fn(bq))
        table.time_partitioning = bq.TimePartitioning(field="metric_date")
        client.create_table(table, exists_ok=True, timeout=30)
    _log.info("Bluesky tables ensured in %s", dataset_ref)


def _mart_dataset_id() -> str:
    return (os.getenv("BQ_MART_DATASET_ID") or "marketing_marts").strip()


def create_bluesky_mart_views() -> dict[str, Any]:
    """Build the mart views the social panel reads.

      - vw_bluesky_profile_latest — most recent profile row per client/handle
      - vw_bluesky_profile_daily  — 365-day follower/engagement time series
      - vw_bluesky_posts_latest   — latest snapshot of each post (never sum
        across snapshots; this is the row to aggregate)
    """
    bq = _bq()
    client = _client()
    project = _project_id()
    raw_dataset = _dataset_id()
    mart_dataset = _mart_dataset_id()
    profile_table = f"`{project}.{raw_dataset}.profile_daily`"
    posts_table = f"`{project}.{raw_dataset}.posts_daily`"
    mart_ref = f"{project}.{mart_dataset}"

    client.create_dataset(bq.Dataset(mart_ref), exists_ok=True, timeout=30)

    views = {
        "vw_bluesky_profile_latest": f"""
            SELECT * EXCEPT(rn) FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY client_key, handle ORDER BY metric_date DESC
              ) AS rn
              FROM {profile_table}
            )
            WHERE rn = 1
        """,
        # Follower growth: the day-over-day delta the KPI card shows, computed
        # here so every caller reports the same number.
        "vw_bluesky_profile_daily": f"""
            SELECT
              client_key, handle, metric_date,
              followers_count, follows_count, posts_count,
              window_posts, window_engagements,
              followers_count - LAG(followers_count) OVER (
                PARTITION BY client_key, handle ORDER BY metric_date
              ) AS followers_gained
            FROM {profile_table}
            WHERE metric_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
        """,
        "vw_bluesky_posts_latest": f"""
            SELECT p.* EXCEPT(rn) FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY client_key, handle, post_uri ORDER BY metric_date DESC
              ) AS rn
              FROM {posts_table}
            ) p
            WHERE p.rn = 1
        """,
    }

    results: dict[str, Any] = {}
    for view_name, select_sql in views.items():
        table_id = f"{mart_ref}.{view_name}"
        sql = f"CREATE OR REPLACE VIEW `{table_id}` AS\n{select_sql}"
        try:
            client.query(sql).result(timeout=60)
            results[view_name] = "created"
            _log.info("Bluesky mart view ensured: %s", table_id)
        except Exception as exc:
            results[view_name] = f"error: {exc!s:.200}"
            _log.warning("Bluesky mart view failed [%s]: %s", view_name, exc)
    return results


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_bluesky_to_bq(
    handle: str,
    *,
    client_key: str,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Snapshot `handle` into BQ for today. Returns row counts and errors."""
    import bluesky_service

    ensure_bluesky_tables()

    now = datetime.now(tz=timezone.utc).isoformat()
    today = date.today().isoformat()

    snapshot = bluesky_service.build_bluesky_snapshot(handle, since=start, until=end)
    if snapshot.get("error"):
        return {
            "total_rows": 0, "profile_rows": 0, "post_rows": 0,
            "errors": {"snapshot": snapshot["error"]},
        }

    errors: dict[str, str] = dict(snapshot.get("errors") or {})
    resolved_handle = snapshot.get("handle") or bluesky_service.normalize_handle(handle)
    did = snapshot.get("did") or None
    profile = snapshot.get("profile") or {}
    posts = snapshot.get("posts") or []

    bq = _bq()
    client = _client()

    post_rows = [
        {
            "client_key": client_key,
            "handle": resolved_handle,
            "did": did,
            "metric_date": today,
            "post_uri": post["uri"],
            "post_cid": post.get("cid") or None,
            "post_url": post.get("url") or None,
            "post_date": post["created_date"].isoformat() if post.get("created_date") else None,
            "created_at": post["created_at"].isoformat() if post.get("created_at") else None,
            "text": post.get("text") or None,
            "is_reply": bool(post.get("is_reply")),
            "embed_type": post.get("embed_type") or None,
            "langs": post.get("langs"),
            "like_count": post.get("like_count") or 0,
            "repost_count": post.get("repost_count") or 0,
            "reply_count": post.get("reply_count") or 0,
            "quote_count": post.get("quote_count") or 0,
            "engagements": post.get("engagements") or 0,
            "synced_at": now,
        }
        for post in posts
        if post.get("uri")
    ]

    profile_row = {
        "client_key": client_key,
        "handle": resolved_handle,
        "did": did,
        "metric_date": today,
        "display_name": profile.get("display_name") or None,
        "followers_count": profile.get("followers_count"),
        "follows_count": profile.get("follows_count"),
        "posts_count": profile.get("posts_count"),
        "window_posts": len(post_rows),
        "window_likes": sum(r["like_count"] for r in post_rows),
        "window_reposts": sum(r["repost_count"] for r in post_rows),
        "window_replies": sum(r["reply_count"] for r in post_rows),
        "window_quotes": sum(r["quote_count"] for r in post_rows),
        "window_engagements": sum(r["engagements"] for r in post_rows),
        "error": "; ".join(f"{k}: {v}" for k, v in errors.items()) or None,
        "synced_at": now,
    }

    def _replace(table_name: str, rows: list[dict[str, Any]], schema_fn) -> None:
        table_id = _table_ref(table_name)
        client.query(
            f"DELETE FROM `{table_id}` "
            f"WHERE client_key = @ck AND handle = @handle AND metric_date = @d",
            job_config=bq.QueryJobConfig(query_parameters=[
                bq.ScalarQueryParameter("ck", "STRING", client_key),
                bq.ScalarQueryParameter("handle", "STRING", resolved_handle),
                bq.ScalarQueryParameter("d", "DATE", today),
            ]),
        ).result(timeout=120)
        if not rows:
            return
        client.load_table_from_json(
            rows, table_id,
            job_config=bq.LoadJobConfig(
                schema=schema_fn(bq),
                write_disposition="WRITE_APPEND",
                # Let new columns land on tables created by an earlier version.
                schema_update_options=[bq.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            ),
        ).result(timeout=180)

    _replace("profile_daily", [profile_row], _schema_profile_daily)
    _replace("posts_daily", post_rows, _schema_posts_daily)

    try:
        mart_result = create_bluesky_mart_views()
        errors.update({k: v for k, v in mart_result.items() if str(v).startswith("error")})
    except Exception as exc:
        errors["mart_views"] = str(exc)[:300]

    _log.info(
        "Bluesky sync complete [%s/%s]: %s posts", client_key, resolved_handle, len(post_rows)
    )
    return {
        "total_rows": len(post_rows) + 1,
        "profile_rows": 1,
        "post_rows": len(post_rows),
        "errors": errors,
    }
