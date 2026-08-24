"""Read service for the Bluesky dashboard page.

Reads the ``raw_bluesky`` tables (profile_daily, posts_daily) written by the
bluesky connector sync and shapes them into a ``BlueskyReport`` the renderer
consumes. Resolves the client's BigQuery project from its connector config,
falling back to the client dashboard config — the same routing the LinkedIn
Organic reader uses.

Two things this module has to be careful about, both from the protocol:

*   **``posts_daily`` holds one row per post per snapshot day**, because Bluesky
    reports engagement cumulatively rather than per-day. Every read here first
    reduces to the latest snapshot per post (``QUALIFY ROW_NUMBER() … = 1``);
    summing the raw table would multiply a post's likes by the number of days it
    has been synced.
*   **There are no impressions**, so there is no engagement *rate* to report —
    a rate needs a denominator the network doesn't publish. The page reports
    engagements per post instead, which is the honest version of the same idea.

A missing table means the client simply hasn't synced Bluesky yet — an absent
source, not an error, surfacing as an empty (unconfigured) report.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import bigquery_service

_log = logging.getLogger(__name__)

_DEFAULT_BLUESKY_DATASET = "raw_bluesky"
_PROFILE_TABLE = "profile_daily"
_POSTS_TABLE = "posts_daily"


@dataclass
class BlueskyReport:
    configured: bool = False
    error: str | None = None
    handle: str | None = None
    display_name: str | None = None
    # Latest profile snapshot
    followers: int = 0
    follows: int = 0
    lifetime_posts: int = 0
    follower_gain: int = 0
    prev_follower_gain: int = 0
    # Totals over the reporting window, from each post's latest snapshot
    post_count: int = 0
    total_likes: int = 0
    total_reposts: int = 0
    total_replies: int = 0
    total_quotes: int = 0
    total_engagements: int = 0
    # Same-length window ending the day before the reporting window, so the KPI
    # cards can show period-over-period change. These stay 0 when nothing synced
    # that far back, and the renderer then omits the delta rather than calling a
    # client's first-ever window an infinite improvement.
    prev_post_count: int = 0
    prev_total_likes: int = 0
    prev_total_reposts: int = 0
    prev_total_replies: int = 0
    prev_total_quotes: int = 0
    prev_total_engagements: int = 0
    # Series / tables
    follower_series: list[dict[str, Any]] = field(default_factory=list)
    engagement_series: list[dict[str, Any]] = field(default_factory=list)
    top_posts: list[dict[str, Any]] = field(default_factory=list)
    # Last day the connector wrote a snapshot, for the "as of" line.
    last_synced: str | None = None

    @property
    def avg_engagements(self) -> float:
        return (self.total_engagements / self.post_count) if self.post_count else 0.0

    @property
    def prev_avg_engagements(self) -> float:
        return (self.prev_total_engagements / self.prev_post_count) if self.prev_post_count else 0.0


def _resolve_routing(client_slug: str) -> tuple[str | None, str, str | None]:
    """Return (project_id, bluesky_dataset, credentials_env) for this client."""
    project = None
    dataset = _DEFAULT_BLUESKY_DATASET
    try:
        import connector_config_store
        cfg = connector_config_store.get_config(client_slug, "bluesky")
        if cfg:
            project = cfg.bq_project_id or project
            dataset = cfg.raw_dataset_id or dataset
    except Exception:
        pass
    if not project:
        try:
            import client_dashboard_config
            db_cfg = client_dashboard_config.get_config(client_slug)
            if db_cfg and db_cfg.gcp_project_id:
                project = db_cfg.gcp_project_id
        except Exception:
            pass
    dataset = (dataset or os.getenv("BQ_BLUESKY_DATASET_ID") or _DEFAULT_BLUESKY_DATASET).strip()
    return project, dataset, None


def _is_table_not_found(exc: Exception) -> bool:
    if getattr(exc, "code", None) == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "table" in msg


def _params(**values: Any) -> list[Any]:
    """Scalar query parameters, typed by the Python value."""
    from google.cloud import bigquery

    out = []
    for name, value in values.items():
        kind = "DATE" if isinstance(value, date) else "STRING"
        out.append(bigquery.ScalarQueryParameter(name, kind, value))
    return out


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def fetch_latest_followers(client_slug: str, *, lookback_days: int = 45) -> int | None:
    """The client's most recent Bluesky follower count, or None.

    A one-row read for callers that want the headline number without paying for
    :func:`build_report`'s post scans. Returns None (never raises) when Bluesky
    isn't configured or nothing has synced in the lookback — both of which mean
    "no follower figure for this client", not an error.
    """
    project, dataset, creds = _resolve_routing(client_slug)
    if not project:
        return None
    start = date.today() - timedelta(days=max(1, int(lookback_days)))
    try:
        rows = bigquery_service.run_query(
            f"""
            SELECT followers_count
            FROM `{project}.{dataset}.{_PROFILE_TABLE}`
            WHERE client_key = @ck AND metric_date >= @start AND followers_count > 0
            ORDER BY metric_date DESC
            LIMIT 1
            """,
            project_id=project,
            credentials_env=creds,
            query_parameters=_params(ck=client_slug, start=start),
            max_rows=1,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("bluesky follower total read failed [%s]: %s", client_slug, exc)
        return None
    return _int(rows[0].get("followers_count")) or None if rows else None


def build_report(client_slug: str, *, days: int = 90) -> BlueskyReport:
    """Build the Bluesky report for the last ``days`` days."""
    project, dataset, creds = _resolve_routing(client_slug)
    if not project:
        return BlueskyReport(
            configured=False,
            error="No BigQuery project is configured for this client's Bluesky connector.",
        )

    end = date.today()
    start = end - timedelta(days=days)
    prev_start = start - timedelta(days=days + 1)
    report = BlueskyReport(configured=True)

    def _tbl(name: str) -> str:
        return f"`{project}.{dataset}.{name}`"

    # ── Profile snapshots ────────────────────────────────────────────────────
    # Read back through the comparison window in one query so follower growth
    # can be split into "this window" and "the one before" without a second
    # round trip (BigQuery bills a 10MB minimum per query).
    try:
        profile_rows = bigquery_service.run_query(
            f"""
            SELECT CAST(metric_date AS STRING) AS metric_date, handle, display_name,
                   followers_count, follows_count, posts_count
            FROM {_tbl(_PROFILE_TABLE)}
            WHERE client_key = @ck AND metric_date BETWEEN @prev_start AND @end
            ORDER BY metric_date ASC
            """,
            project_id=project,
            credentials_env=creds,
            query_parameters=_params(ck=client_slug, prev_start=prev_start, end=end),
            max_rows=1000,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("bluesky profile_daily read failed [%s]: %s", client_slug, exc)
        profile_rows = []

    if profile_rows:
        latest = profile_rows[-1]
        report.handle = str(latest.get("handle") or "") or None
        report.display_name = str(latest.get("display_name") or "") or None
        report.followers = _int(latest.get("followers_count"))
        report.follows = _int(latest.get("follows_count"))
        report.lifetime_posts = _int(latest.get("posts_count"))
        report.last_synced = str(latest.get("metric_date") or "") or None

        start_iso = start.isoformat()
        in_window = [r for r in profile_rows if str(r.get("metric_date") or "") >= start_iso]
        before = [r for r in profile_rows if str(r.get("metric_date") or "") < start_iso]
        # Growth needs a baseline. The last snapshot *before* the window is the
        # true starting point; without one (a client synced for the first time
        # inside this window) fall back to the window's own first day, which
        # reports growth since the sync began rather than an invented jump from
        # zero.
        baseline = before[-1] if before else (in_window[0] if in_window else None)
        if baseline and in_window:
            report.follower_gain = report.followers - _int(baseline.get("followers_count"))
        if len(before) >= 2:
            report.prev_follower_gain = (
                _int(before[-1].get("followers_count")) - _int(before[0].get("followers_count"))
            )

        report.follower_series = [
            {
                "metric_date": str(r.get("metric_date") or ""),
                "followers": _int(r.get("followers_count")),
            }
            for r in in_window
        ]

    # ── Post totals over both windows, from each post's latest snapshot ──────
    latest_posts_cte = f"""
        WITH latest AS (
          SELECT post_uri, post_url, post_date, text, is_reply, embed_type,
                 like_count, repost_count, reply_count, quote_count, engagements
          FROM {_tbl(_POSTS_TABLE)}
          WHERE client_key = @ck AND post_date BETWEEN @range_start AND @end
          QUALIFY ROW_NUMBER() OVER (PARTITION BY post_uri ORDER BY metric_date DESC) = 1
        )
    """

    try:
        total_rows = bigquery_service.run_query(
            f"""
            {latest_posts_cte}
            SELECT
              COUNTIF(post_date >= @start) AS post_count,
              SUM(IF(post_date >= @start, like_count, 0))   AS likes,
              SUM(IF(post_date >= @start, repost_count, 0)) AS reposts,
              SUM(IF(post_date >= @start, reply_count, 0))  AS replies,
              SUM(IF(post_date >= @start, quote_count, 0))  AS quotes,
              SUM(IF(post_date >= @start, engagements, 0))  AS engagements,
              COUNTIF(post_date < @start) AS prev_post_count,
              SUM(IF(post_date < @start, like_count, 0))   AS prev_likes,
              SUM(IF(post_date < @start, repost_count, 0)) AS prev_reposts,
              SUM(IF(post_date < @start, reply_count, 0))  AS prev_replies,
              SUM(IF(post_date < @start, quote_count, 0))  AS prev_quotes,
              SUM(IF(post_date < @start, engagements, 0))  AS prev_engagements
            FROM latest
            """,
            project_id=project,
            credentials_env=creds,
            query_parameters=_params(
                ck=client_slug, range_start=prev_start, start=start, end=end
            ),
            max_rows=1,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("bluesky post totals read failed [%s]: %s", client_slug, exc)
        total_rows = []

    if total_rows:
        t = total_rows[0]
        report.post_count = _int(t.get("post_count"))
        report.total_likes = _int(t.get("likes"))
        report.total_reposts = _int(t.get("reposts"))
        report.total_replies = _int(t.get("replies"))
        report.total_quotes = _int(t.get("quotes"))
        report.total_engagements = _int(t.get("engagements"))
        report.prev_post_count = _int(t.get("prev_post_count"))
        report.prev_total_likes = _int(t.get("prev_likes"))
        report.prev_total_reposts = _int(t.get("prev_reposts"))
        report.prev_total_replies = _int(t.get("prev_replies"))
        report.prev_total_quotes = _int(t.get("prev_quotes"))
        report.prev_total_engagements = _int(t.get("prev_engagements"))

    # ── Top posts + engagement by publish day ───────────────────────────────
    try:
        post_rows = bigquery_service.run_query(
            f"""
            {latest_posts_cte}
            SELECT post_uri, post_url, CAST(post_date AS STRING) AS post_date, text,
                   is_reply, embed_type, like_count, repost_count, reply_count,
                   quote_count, engagements
            FROM latest
            WHERE post_date >= @start
            ORDER BY engagements DESC
            LIMIT 200
            """,
            project_id=project,
            credentials_env=creds,
            query_parameters=_params(
                ck=client_slug, range_start=start, start=start, end=end
            ),
            max_rows=200,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("bluesky posts read failed [%s]: %s", client_slug, exc)
        post_rows = []

    by_day: dict[str, dict[str, int]] = {}
    for r in post_rows:
        post = {
            "post_uri": str(r.get("post_uri") or ""),
            "url": str(r.get("post_url") or ""),
            "post_date": str(r.get("post_date") or ""),
            "text": str(r.get("text") or ""),
            "is_reply": bool(r.get("is_reply")),
            "embed_type": str(r.get("embed_type") or ""),
            "likes": _int(r.get("like_count")),
            "reposts": _int(r.get("repost_count")),
            "replies": _int(r.get("reply_count")),
            "quotes": _int(r.get("quote_count")),
            "engagements": _int(r.get("engagements")),
        }
        report.top_posts.append(post)
        day = post["post_date"]
        if not day:
            continue
        bucket = by_day.setdefault(day, {"posts": 0, "engagements": 0})
        bucket["posts"] += 1
        bucket["engagements"] += post["engagements"]

    report.engagement_series = [
        {"metric_date": day, "posts": v["posts"], "engagements": v["engagements"]}
        for day, v in sorted(by_day.items())
    ]
    # The table shows the strongest posts; the chart above it already carries
    # the full window, so 50 rows is plenty to scan.
    report.top_posts = report.top_posts[:50]
    return report
