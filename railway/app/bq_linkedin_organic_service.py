"""BigQuery ingestion for LinkedIn *organic* (company-page) metrics.

Sibling to ``bq_linkedin_ads_service``: it orchestrates the organic fetchers in
``linkedin_organic_service`` and mirrors their output into the
``raw_linkedin_organic`` dataset, then (re)builds the organic post mart view.

Like the ads service, this is source-specific and route-scoped: callers wrap
``run_organic_sync`` in :func:`route` so a client's organic writes hit that
client's BigQuery project (organic and ads share the project; only the dataset
differs).
"""

from __future__ import annotations

import contextlib
import logging
from datetime import date
from typing import Any

_log = logging.getLogger(__name__)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    credentials_env: str | None = None,
    organic_dataset_id: str | None = None,
):
    """Scope LinkedIn organic BigQuery writes to a specific project/dataset."""
    import bigquery_warehouse

    with bigquery_warehouse.route(
        bq_project_id=(bq_project_id or "").strip() or None,
        credentials_env=(credentials_env or "").strip() or None,
        linkedin_organic_dataset_id=(organic_dataset_id or "").strip() or None,
    ):
        yield


def run_organic_sync(
    *,
    org_id: str,
    start: date,
    end: date,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Fetch + mirror posts, followers, and page stats for one organization.

    Each metric family is isolated in its own try/except so a missing scope or a
    single failing endpoint (e.g. page statistics) degrades gracefully instead of
    failing the whole run. Returns a per-family result dict plus ``rows_upserted``.

    ``access_token`` must be threaded through for connector-onboarded clients:
    their LinkedIn OAuth token is client-scoped (oauth_store), so without it the
    linkedin_organic_service calls fall back to the tokenless global env.
    """
    import bigquery_warehouse
    import linkedin_organic_service as organic

    org_id_clean = str(org_id).strip().split(":")[-1]
    if not org_id_clean:
        return {"rows_upserted": 0, "error": "missing_org_id"}

    rows_upserted = 0
    result: dict[str, Any] = {"org_id": org_id_clean}

    # Posts + per-post engagement
    try:
        posts = organic.fetch_posts_with_stats(
            org_id_clean, start=start, end=end, access_token=access_token
        )
        mirror = bigquery_warehouse.mirror_linkedin_post_stats(org_id_clean, posts)
        rows_upserted += int(mirror.get("rows_upserted") or 0)
        result["posts"] = {"fetched": len(posts), **mirror}
    except Exception as exc:
        _log.warning("organic posts sync failed [%s]: %s", org_id_clean, exc)
        result["posts"] = {"error": str(exc)[:300]}

    # Followers
    try:
        followers = organic.fetch_follower_daily(
            org_id_clean, start=start, end=end, access_token=access_token
        )
        mirror = bigquery_warehouse.mirror_linkedin_follower_daily(org_id_clean, followers)
        rows_upserted += int(mirror.get("rows_upserted") or 0)
        result["followers"] = {"fetched": len(followers), **mirror}
    except Exception as exc:
        _log.warning("organic followers sync failed [%s]: %s", org_id_clean, exc)
        result["followers"] = {"error": str(exc)[:300]}

    # Page / visitor analytics
    try:
        pages = organic.fetch_page_daily(
            org_id_clean, start=start, end=end, access_token=access_token
        )
        mirror = bigquery_warehouse.mirror_linkedin_page_daily(org_id_clean, pages)
        rows_upserted += int(mirror.get("rows_upserted") or 0)
        result["pages"] = {"fetched": len(pages), **mirror}
    except Exception as exc:
        _log.warning("organic page sync failed [%s]: %s", org_id_clean, exc)
        result["pages"] = {"error": str(exc)[:300]}

    # Rebuild the post mart view (idempotent; safe even if some families failed).
    try:
        result["mart_view"] = bigquery_warehouse.create_linkedin_organic_post_mart_view()
    except Exception as exc:
        _log.warning("organic mart view build failed [%s]: %s", org_id_clean, exc)
        result["mart_view"] = {"error": str(exc)[:300]}

    result["rows_upserted"] = rows_upserted
    return result
