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

import json
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
_DEMOGRAPHICS_TABLE = "follower_demographics"
_ENGAGEMENT_TABLE = "engagement_daily"

# Page-section columns -> display label, in the order we surface them.
_PAGE_SECTIONS = (
    ("overview_page_views", "Overview"),
    ("careers_page_views", "Careers"),
    ("jobs_page_views", "Jobs"),
    ("life_page_views", "Life"),
    ("products_page_views", "Products"),
    ("people_page_views", "People"),
)


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
    total_unique_impressions: int = 0
    total_followers: int = 0
    follower_gain: int = 0
    total_page_views: int = 0
    total_unique_visitors: int = 0
    # Page-view splits over the window
    page_desktop_views: int = 0
    page_mobile_views: int = 0
    page_sections: list[dict[str, Any]] = field(default_factory=list)  # [{label, views}]
    # Series / tables
    top_posts: list[dict[str, Any]] = field(default_factory=list)
    follower_series: list[dict[str, Any]] = field(default_factory=list)
    page_series: list[dict[str, Any]] = field(default_factory=list)
    engagement_series: list[dict[str, Any]] = field(default_factory=list)
    # Lifetime follower demographics keyed by dimension
    # (seniority/function/industry/company_size/region) -> ranked category rows.
    follower_demographics: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


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


def _parse_reactions(raw: Any) -> dict[str, int]:
    """Decode the ``reactions_by_type`` JSON string column into {TYPE: count}."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    out: dict[str, int] = {}
    for key, value in (data or {}).items():
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count:
            out[str(key).upper()] = count
    return out


def _is_table_not_found(exc: Exception) -> bool:
    if getattr(exc, "code", None) == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "table" in msg


def fetch_latest_followers(client_slug: str, *, lookback_days: int = 45) -> int | None:
    """The client's most recent lifetime LinkedIn follower count, or None.

    A one-row read of ``follower_daily`` for callers that want the headline
    number without paying for :func:`build_report`'s posts / page / demographics
    scans — the agency Benchmarks rollup does this once per client.

    Returns None (never raises) when organic isn't configured, the table hasn't
    been synced, or no day in the lookback carries a non-zero lifetime total —
    all of which mean "no follower benchmark for this client", not an error. The
    lookback is generous because the lifetime total is only written on days the
    sync ran.
    """
    project, dataset, creds = _resolve_routing(client_slug)
    if not project:
        return None
    start = date.today() - timedelta(days=max(1, int(lookback_days)))
    try:
        rows = bigquery_service.run_query(
            f"""
            SELECT total_followers
            FROM `{project}.{dataset}.{_FOLLOWER_TABLE}`
            WHERE metric_date >= DATE '{start.isoformat()}'
              AND total_followers > 0
            ORDER BY metric_date DESC
            LIMIT 1
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=1,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.warning("organic follower total read failed [%s]: %s", client_slug, exc)
        return None
    if not rows:
        return None
    try:
        total = int(rows[0].get("total_followers") or 0)
    except (TypeError, ValueError):
        return None
    return total or None


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
            "unique_impressions": 0,
            "clicks": int(r.get("clicks") or 0),
            "likes": int(r.get("likes") or 0),
            "comments": int(r.get("comments") or 0),
            "shares": int(r.get("shares") or 0),
            "engagement_rate": float(r.get("engagement_rate") or 0.0),
            "reactions": {},
        })
    report.post_count = len(report.top_posts)
    report.total_impressions = sum(p["impressions"] for p in report.top_posts)
    report.total_likes = sum(p["likes"] for p in report.top_posts)
    report.total_comments = sum(p["comments"] for p in report.top_posts)
    report.total_shares = sum(p["shares"] for p in report.top_posts)

    # Reach (unique_impressions) + per-reaction breakdown live in columns added
    # after the table's first version, so they're read in a separate, tolerant
    # query: a table that predates them simply yields no reach/reactions rather
    # than breaking the whole posts read.
    if report.top_posts:
        by_post = {p["post_id"]: p for p in report.top_posts}
        try:
            extra_rows = bigquery_service.run_query(
                f"""
                SELECT post_id, unique_impressions, reactions_by_type
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
                _log.info("organic post reach/reactions read skipped [%s]: %s", client_slug, exc)
            extra_rows = []
        for r in extra_rows:
            post = by_post.get(str(r.get("post_id") or ""))
            if not post:
                continue
            post["unique_impressions"] = int(r.get("unique_impressions") or 0)
            post["reactions"] = _parse_reactions(r.get("reactions_by_type"))
    report.total_unique_impressions = sum(p["unique_impressions"] for p in report.top_posts)

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

    # ── Page views by device + section (tolerant: columns added after v1) ──────
    _section_cols = ", ".join(f"SUM({c}) AS {c}" for c, _ in _PAGE_SECTIONS)
    try:
        split_rows = bigquery_service.run_query(
            f"""
            SELECT SUM(desktop_page_views) AS desktop, SUM(mobile_page_views) AS mobile,
                   {_section_cols}
            FROM {_tbl(_PAGE_TABLE)}
            WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=1,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.info("organic page split read skipped [%s]: %s", client_slug, exc)
        split_rows = []
    if split_rows:
        row = split_rows[0]
        report.page_desktop_views = int(row.get("desktop") or 0)
        report.page_mobile_views = int(row.get("mobile") or 0)
        report.page_sections = [
            {"label": label, "views": int(row.get(col) or 0)}
            for col, label in _PAGE_SECTIONS
            if int(row.get(col) or 0) > 0
        ]

    # ── Follower demographics (lifetime segments by dimension) ─────────────────
    try:
        demo_rows = bigquery_service.run_query(
            f"""
            SELECT dimension, category, organic_followers, paid_followers, total_followers
            FROM {_tbl(_DEMOGRAPHICS_TABLE)}
            ORDER BY dimension ASC, total_followers DESC
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=500,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.info("organic demographics read skipped [%s]: %s", client_slug, exc)
        demo_rows = []
    for r in demo_rows:
        dim = str(r.get("dimension") or "")
        if not dim:
            continue
        report.follower_demographics.setdefault(dim, []).append({
            "category": str(r.get("category") or "Unknown"),
            "organic_followers": int(r.get("organic_followers") or 0),
            "paid_followers": int(r.get("paid_followers") or 0),
            "total_followers": int(r.get("total_followers") or 0),
        })

    # ── Organization-level engagement over time ────────────────────────────────
    try:
        eng_rows = bigquery_service.run_query(
            f"""
            SELECT CAST(metric_date AS STRING) AS metric_date, impressions,
                   unique_impressions, clicks, likes, comments, shares, engagement_rate
            FROM {_tbl(_ENGAGEMENT_TABLE)}
            WHERE metric_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
            ORDER BY metric_date ASC
            """,
            project_id=project,
            credentials_env=creds,
            max_rows=1000,
        )
    except Exception as exc:
        if not _is_table_not_found(exc):
            _log.info("organic engagement read skipped [%s]: %s", client_slug, exc)
        eng_rows = []
    for r in eng_rows:
        report.engagement_series.append({
            "metric_date": str(r.get("metric_date") or ""),
            "impressions": int(r.get("impressions") or 0),
            "unique_impressions": int(r.get("unique_impressions") or 0),
            "clicks": int(r.get("clicks") or 0),
            "likes": int(r.get("likes") or 0),
            "comments": int(r.get("comments") or 0),
            "shares": int(r.get("shares") or 0),
            "engagement_rate": float(r.get("engagement_rate") or 0.0),
        })

    report.org_id = org_id
    return report
