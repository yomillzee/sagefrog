"""Read service for the LinkedIn Organic dashboard page.

Reads the ``raw_linkedin_organic`` tables (post_stats, follower_daily,
page_daily) written by the linkedin_organic connector sync and shapes them into
a ``LinkedInOrganicReport`` the renderer consumes. Resolves the client's BigQuery
project from its connector config (falling back to the client dashboard config),
mirroring the routing used by the paid LinkedIn reader.

A missing table means the client simply hasn't synced organic yet — that's an
absent source, not an error, and surfaces as an empty (unconfigured) report.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import bigquery_service

_log = logging.getLogger(__name__)

_DEFAULT_ORGANIC_DATASET = "raw_linkedin_organic"
_POST_TABLE = "post_stats"
_FOLLOWER_TABLE = "follower_daily"
_PAGE_TABLE = "page_daily"


@dataclass
class LinkedInOrganicReport:
    configured: bool = False
    error: str | None = None
    org_id: str | None = None
    # Totals over the window
    post_count: int = 0
    total_impressions: int = 0
    total_likes: int = 0
    total_comments: int = 0
    total_shares: int = 0
    total_followers: int = 0
    follower_gain: int = 0
    total_page_views: int = 0
    total_unique_visitors: int = 0
    # Series / tables
    top_posts: list[dict[str, Any]] = field(default_factory=list)
    follower_series: list[dict[str, Any]] = field(default_factory=list)
    page_series: list[dict[str, Any]] = field(default_factory=list)


def _resolve_routing(client_slug: str) -> tuple[str | None, str, str | None]:
    """Return (project_id, organic_dataset, credentials_env) for this client."""
    project = None
    dataset = _DEFAULT_ORGANIC_DATASET
    try:
        import connector_config_store
        cfg = connector_config_store.get_config(client_slug, "linkedin_organic")
        if cfg:
            project = cfg.bq_project_id or project
            dataset = cfg.raw_dataset_id or dataset
    except Exception:
        cfg = None
    if not project:
        try:
            import client_dashboard_config
            db_cfg = client_dashboard_config.get_config(client_slug)
            if db_cfg and db_cfg.gcp_project_id:
                project = db_cfg.gcp_project_id
        except Exception:
            pass
    dataset = (dataset or os.getenv("BQ_LINKEDIN_ORGANIC_DATASET_ID") or _DEFAULT_ORGANIC_DATASET).strip()
    return project, dataset, None


def _is_table_not_found(exc: Exception) -> bool:
    if getattr(exc, "code", None) == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "table" in msg


def build_report(client_slug: str, *, days: int = 90) -> LinkedInOrganicReport:
    """Build the organic report for the last ``days`` days."""
    project, dataset, creds = _resolve_routing(client_slug)
    if not project:
        return LinkedInOrganicReport(
            configured=False,
            error="No BigQuery project is configured for this client's LinkedIn Organic connector.",
        )

    end = date.today()
    start = end - timedelta(days=days)

    def _tbl(name: str) -> str:
        return f"`{project}.{dataset}.{name}`"

    org_id: str | None = None
    report = LinkedInOrganicReport(configured=True, org_id=None)

    # ── Posts (top by impressions within window) ──────────────────────────────
    try:
        post_rows = bigquery_service.run_query(
            f"""
            SELECT org_id, post_id, title, post_type,
                   CAST(published_at AS STRING) AS published_at,
                   impressions, clicks, likes, comments, shares, engagement_rate
            FROM {_tbl(_POST_TABLE)}
            WHERE published_at IS NULL
               OR published_at BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
            ORDER BY impressions DESC
            LIMIT 50
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=50,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("organic post_stats read failed [%s]: %s", client_slug, exc)
        post_rows = []

    for r in post_rows:
        org_id = org_id or (str(r.get("org_id")) if r.get("org_id") else None)
        report.top_posts.append({
            "post_id": str(r.get("post_id") or ""),
            "title": str(r.get("title") or "") or f"Post {r.get('post_id')}",
            "post_type": str(r.get("post_type") or ""),
            "published_at": str(r.get("published_at") or ""),
            "impressions": int(r.get("impressions") or 0),
            "clicks": int(r.get("clicks") or 0),
            "likes": int(r.get("likes") or 0),
            "comments": int(r.get("comments") or 0),
            "shares": int(r.get("shares") or 0),
            "engagement_rate": float(r.get("engagement_rate") or 0.0),
        })
    report.post_count = len(report.top_posts)
    report.total_impressions = sum(p["impressions"] for p in report.top_posts)
    report.total_likes = sum(p["likes"] for p in report.top_posts)
    report.total_comments = sum(p["comments"] for p in report.top_posts)
    report.total_shares = sum(p["shares"] for p in report.top_posts)

    # ── Followers (daily series + latest lifetime total) ──────────────────────
    try:
        foll_rows = bigquery_service.run_query(
            f"""
            SELECT CAST(metric_date AS STRING) AS metric_date,
                   organic_follower_gain, paid_follower_gain,
                   total_follower_gain, total_followers
            FROM {_tbl(_FOLLOWER_TABLE)}
            WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
            ORDER BY metric_date ASC
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=1000,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("organic follower_daily read failed [%s]: %s", client_slug, exc)
        foll_rows = []

    for r in foll_rows:
        report.follower_series.append({
            "metric_date": str(r.get("metric_date") or ""),
            "organic_follower_gain": int(r.get("organic_follower_gain") or 0),
            "paid_follower_gain": int(r.get("paid_follower_gain") or 0),
            "total_follower_gain": int(r.get("total_follower_gain") or 0),
            "total_followers": int(r.get("total_followers") or 0),
        })
    report.follower_gain = sum(r["total_follower_gain"] for r in report.follower_series)
    # Lifetime total is attached to the most recent day carrying a non-zero value.
    for r in reversed(report.follower_series):
        if r["total_followers"]:
            report.total_followers = r["total_followers"]
            break

    # ── Page / visitor analytics (daily series) ───────────────────────────────
    try:
        page_rows = bigquery_service.run_query(
            f"""
            SELECT CAST(metric_date AS STRING) AS metric_date, page_views, unique_visitors
            FROM {_tbl(_PAGE_TABLE)}
            WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
            ORDER BY metric_date ASC
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=1000,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("organic page_daily read failed [%s]: %s", client_slug, exc)
        page_rows = []

    for r in page_rows:
        report.page_series.append({
            "metric_date": str(r.get("metric_date") or ""),
            "page_views": int(r.get("page_views") or 0),
            "unique_visitors": int(r.get("unique_visitors") or 0),
        })
    report.total_page_views = sum(r["page_views"] for r in report.page_series)
    report.total_unique_visitors = sum(r["unique_visitors"] for r in report.page_series)

    report.org_id = org_id
    return report
