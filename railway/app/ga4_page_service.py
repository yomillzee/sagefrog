"""GA4 page path / title metrics from BigQuery events export."""

from __future__ import annotations

from datetime import date
from typing import Any

from bigquery_service import env_summary, run_query
from dates_util import resolve_date_range
from ga4_attribution_service import KEY_EVENT_NAMES
from ga4_clients import Ga4ClientTarget, resolve_target
from penn_business_lines import classify_business_line, client_filter_profile


def _load_custom_business_line_rules(client_slug: str) -> list[tuple[str, str, tuple[str, ...]]] | None:
    slug = (client_slug or "").strip().lower()
    if slug != "penn":
        return None
    try:
        import business_line_rules as bl_rules

        return bl_rules.rules_as_tuples(slug)
    except Exception:
        return None


def classify_page_segment(
    *,
    page_path: str,
    page_title: str,
    campaign_name: str = "",
    client_slug: str,
) -> dict[str, Any]:
    """Keyword-based segment tag for a page (business line or Nixon region)."""
    texts = tuple(
        t.strip()
        for t in (campaign_name, page_path, page_title)
        if (t or "").strip()
    )
    primary = texts[0] if texts else (page_path or page_title or "—")
    extras = texts[1:]
    profile = client_filter_profile(client_slug)
    custom_rules = _load_custom_business_line_rules(client_slug)

    if profile == "nixon":
        from dashboard_regions import classify_regions

        region_ids, label = classify_regions(primary, extra_names=extras)
        primary_id = region_ids[0] if region_ids else "other"
        return {
            "business_line": primary_id,
            "business_line_label": label,
            "region_ids": region_ids,
        }

    bid, label = classify_business_line(
        primary,
        extra_names=extras,
        custom_rules=custom_rules,
    )
    return {
        "business_line": bid,
        "business_line_label": label,
        "region_ids": [bid],
    }


def _merge_page_metrics(existing: dict[str, Any], row: dict[str, Any]) -> None:
    existing["page_views"] += int(row.get("page_views") or 0)
    existing["sessions"] += int(row.get("sessions") or 0)
    existing["users"] += int(row.get("users") or 0)
    existing["engaged_sessions"] += int(row.get("engaged_sessions") or 0)
    existing["key_events"] += int(row.get("key_events") or 0)
    sessions = int(existing["sessions"])
    engaged = int(existing["engaged_sessions"])
    existing["engagement_rate"] = round(engaged / sessions, 4) if sessions else 0.0


def aggregate_landing_rows_by_platform(
    rows: list[dict[str, Any]],
    *,
    client_slug: str,
    limit: int = 500,
) -> dict[str, list[dict[str, Any]]]:
    per_platform: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {
        "google": {},
        "linkedin": {},
        "meta": {},
    }
    for row in rows:
        platform = str(row.get("platform") or "").strip().lower()
        if platform not in per_platform:
            continue
        path = str(row.get("page_path") or "").strip()
        if not path:
            continue
        title = str(row.get("page_title") or "(not set)").strip() or "(not set)"
        campaign_name = str(row.get("campaign_name") or "").strip()
        segment = classify_page_segment(
            page_path=path,
            page_title=title,
            campaign_name=campaign_name,
            client_slug=client_slug,
        )
        seg_id = str(segment["business_line"])
        bucket_key = (seg_id, path, title)
        buckets = per_platform[platform]
        sessions = int(row.get("sessions") or 0)
        engaged = int(row.get("engaged_sessions") or 0)
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "page_path": path,
                "page_title": title,
                "page_views": int(row.get("page_views") or 0),
                "sessions": sessions,
                "users": int(row.get("users") or 0),
                "engaged_sessions": engaged,
                "engagement_rate": round(engaged / sessions, 4) if sessions else 0.0,
                "key_events": int(row.get("key_events") or 0),
                "business_line": seg_id,
                "business_line_label": segment["business_line_label"],
                "region_ids": list(segment.get("region_ids") or [seg_id]),
            }
        else:
            _merge_page_metrics(buckets[bucket_key], row)

    per_limit = max(1, min(int(limit), 2000))
    out: dict[str, list[dict[str, Any]]] = {"google": [], "linkedin": [], "meta": []}
    for platform, buckets in per_platform.items():
        sorted_rows = sorted(
            buckets.values(),
            key=lambda item: (item.get("sessions") or 0, item.get("page_views") or 0),
            reverse=True,
        )
        out[platform] = sorted_rows[:per_limit]
    return out


def annotate_site_pages_with_segment(
    pages: list[dict[str, Any]],
    *,
    client_slug: str,
) -> list[dict[str, Any]]:
    profile = client_filter_profile(client_slug)
    if not profile:
        return pages
    annotated: list[dict[str, Any]] = []
    for page in pages:
        item = dict(page)
        segment = classify_page_segment(
            page_path=str(item.get("page_path") or ""),
            page_title=str(item.get("page_title") or ""),
            client_slug=client_slug,
        )
        item.update(segment)
        annotated.append(item)
    return annotated


def _events_table(target: Ga4ClientTarget) -> str:
    return f"`{target.bq_project_id}.{target.bq_dataset_id}.events_*`"


def _key_events_sql_list() -> str:
    return ", ".join(f"'{name}'" for name in KEY_EVENT_NAMES)


def _ensure_bq_ready(credentials_env: str | None = None) -> None:
    summ = env_summary(credentials_env=credentials_env)
    if not summ.get("gcp_service_account_json_parse_ok"):
        raise RuntimeError(
            summ.get("gcp_service_account_json_parse_error")
            or "GCP_SERVICE_ACCOUNT_JSON did not parse."
        )


def fetch_page_metrics(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Aggregate page path/title metrics for the date range (site-wide traffic)."""
    target = target or resolve_target()
    _ensure_bq_ready(credentials_env=target.credentials_env)

    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = _events_table(target)
    key_events = _key_events_sql_list()
    row_limit = max(1, min(int(limit), 2000))

    sql = f"""
    WITH page_events AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        event_name,
        COALESCE(
          NULLIF(TRIM((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_path')), ''),
          REGEXP_EXTRACT(
            (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
            r'https?://[^/]+(/[^?#]*)'
          )
        ) AS page_path,
        COALESCE(
          NULLIF(TRIM((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_title')), ''),
          '(not set)'
        ) AS page_title,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec') AS engagement_time_msec
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{suffix_start}' AND '{suffix_end}'
        AND event_name IN ('page_view', {key_events})
    ),
    normalized AS (
      SELECT
        user_pseudo_id,
        ga_session_id,
        event_name,
        IFNULL(page_path, '') AS page_path,
        page_title,
        engagement_time_msec
      FROM page_events
      WHERE page_path != ''
    )
    SELECT
      page_path,
      page_title,
      COUNTIF(event_name = 'page_view') AS page_views,
      COUNT(DISTINCT IF(
        event_name = 'page_view',
        CONCAT(user_pseudo_id, '-', CAST(ga_session_id AS STRING)),
        NULL
      )) AS sessions,
      COUNT(DISTINCT IF(event_name = 'page_view', user_pseudo_id, NULL)) AS users,
      COUNT(DISTINCT IF(
        event_name = 'page_view' AND IFNULL(engagement_time_msec, 0) > 0,
        CONCAT(user_pseudo_id, '-', CAST(ga_session_id AS STRING)),
        NULL
      )) AS engaged_sessions,
      COUNTIF(event_name IN ({key_events})) AS key_events
    FROM normalized
    GROUP BY page_path, page_title
    ORDER BY page_views DESC
    LIMIT {row_limit}
    """
    rows = run_query(sql, max_rows=row_limit, project_id=target.bq_project_id, credentials_env=target.credentials_env)
    out: list[dict[str, Any]] = []
    for row in rows:
        path = str(row.get("page_path") or "").strip()
        if not path:
            continue
        sessions = int(row.get("sessions") or 0)
        engaged = int(row.get("engaged_sessions") or 0)
        out.append(
            {
                "page_path": path,
                "page_title": str(row.get("page_title") or "(not set)").strip() or "(not set)",
                "page_views": int(row.get("page_views") or 0),
                "sessions": sessions,
                "users": int(row.get("users") or 0),
                "engaged_sessions": engaged,
                "engagement_rate": round(engaged / sessions, 4) if sessions else 0.0,
                "key_events": int(row.get("key_events") or 0),
            }
        )
    return out


def fetch_site_metrics_summary(
    *,
    start: date,
    end: date,
    target: Ga4ClientTarget | None = None,
) -> dict[str, Any]:
    """Site-wide GA4 session metrics for the dashboard summary bar."""
    target = target or resolve_target()
    _ensure_bq_ready(credentials_env=target.credentials_env)

    suffix_start = start.strftime("%Y%m%d")
    suffix_end = end.strftime("%Y%m%d")
    table = _events_table(target)
    key_events = _key_events_sql_list()

    sql = f"""
    WITH raw_events AS (
      SELECT
        user_pseudo_id,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
        event_name,
        event_timestamp,
        (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'engagement_time_msec') AS engagement_time_msec
      FROM {table}
      WHERE _TABLE_SUFFIX BETWEEN '{suffix_start}' AND '{suffix_end}'
        AND (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NOT NULL
    ),
    session_rollups AS (
      SELECT
        user_pseudo_id,
        ga_session_id,
        COUNT(*) AS event_count,
        COUNTIF(event_name = 'page_view') AS page_views,
        SUM(IFNULL(engagement_time_msec, 0)) AS total_engagement_time_msec,
        COALESCE(MAX(engagement_time_msec), 0) AS max_engagement_time_msec,
        COUNTIF(event_name IN ({key_events})) AS key_events,
        COUNTIF(event_name = 'user_engagement') AS user_engagement_events,
        TIMESTAMP_MICROS(MIN(event_timestamp)) AS session_start_ts,
        TIMESTAMP_MICROS(MAX(event_timestamp)) AS session_end_ts
      FROM raw_events
      GROUP BY user_pseudo_id, ga_session_id
    ),
    session_metrics AS (
      SELECT
        *,
        TIMESTAMP_DIFF(session_end_ts, session_start_ts, SECOND) AS session_duration_sec,
        (
          max_engagement_time_msec >= 10000
          OR page_views >= 2
          OR key_events >= 1
          OR user_engagement_events >= 1
        ) AS is_engaged
      FROM session_rollups
    )
    SELECT
      COUNT(*) AS sessions,
      COUNTIF(is_engaged) AS engaged_sessions,
      SAFE_DIVIDE(COUNTIF(is_engaged), COUNT(*)) AS engagement_rate,
      SAFE_DIVIDE(SUM(total_engagement_time_msec) / 1000.0, COUNT(*)) AS avg_engagement_time_sec,
      SAFE_DIVIDE(AVG(session_duration_sec), 1) AS avg_session_duration_sec,
      SAFE_DIVIDE(SUM(event_count), COUNT(*)) AS events_per_session,
      SAFE_DIVIDE(SUM(page_views), COUNT(*)) AS views_per_session
    FROM session_metrics
    """
    rows = run_query(sql, max_rows=1, project_id=target.bq_project_id, credentials_env=target.credentials_env)
    row = rows[0] if rows else {}
    sessions = int(row.get("sessions") or 0)
    return {
        "sessions": sessions,
        "engaged_sessions": int(row.get("engaged_sessions") or 0),
        "engagement_rate": round(float(row.get("engagement_rate") or 0), 4),
        "avg_engagement_time_sec": round(float(row.get("avg_engagement_time_sec") or 0), 1),
        "avg_session_duration_sec": round(float(row.get("avg_session_duration_sec") or 0), 1),
        "events_per_session": round(float(row.get("events_per_session") or 0), 2),
        "views_per_session": round(float(row.get("views_per_session") or 0), 2),
    }


def fetch_pages_for_dashboard(
    *,
    date_range: str = "LAST_30_DAYS",
    client_key: str | None = None,
    client_slug: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    from ga4_attribution_service import (
        LANDING_PAGE_ATTRIBUTION_LABELS,
        fetch_landing_page_rows,
    )
    import ga4_ai_service

    target = resolve_target(client_key=client_key)
    slug = (client_slug or client_key or target.client_key or "").strip().lower()
    start, end, preset = resolve_date_range(date_range)

    # Run all five BQ queries in parallel
    with ThreadPoolExecutor(max_workers=5) as pool:
        _pages_f   = pool.submit(fetch_page_metrics, start=start, end=end, target=target, limit=limit)
        _summary_f = pool.submit(fetch_site_metrics_summary, start=start, end=end, target=target)
        _landing_f = pool.submit(fetch_landing_page_rows, start=start, end=end, target=target, limit=limit)
        _ai_day_f  = pool.submit(ga4_ai_service.fetch_ai_daily, start=start, end=end, target=target)
        _ai_pgs_f  = pool.submit(ga4_ai_service.fetch_ai_pages, start=start, end=end, target=target)

        pages           = _pages_f.result()
        summary         = _summary_f.result()
        raw_landing     = _landing_f.result()
        ai_daily_rows   = _ai_day_f.result() if not _ai_day_f.exception() else []
        ai_page_rows    = _ai_pgs_f.result() if not _ai_pgs_f.exception() else []

    pages = annotate_site_pages_with_segment(pages, client_slug=slug)
    pages_by_platform = aggregate_landing_rows_by_platform(raw_landing, client_slug=slug, limit=limit)
    ai_traffic = ga4_ai_service.build_ai_traffic(ai_daily_rows, ai_page_rows)

    return {
        "client_key": target.client_key,
        "account_id": target.account_id,
        "bq_project_id": target.bq_project_id,
        "bq_dataset_id": target.bq_dataset_id,
        "date_range": {"start": start.isoformat(), "end": end.isoformat(), "preset": preset},
        "row_count": len(pages),
        "summary": summary,
        "pages": pages,
        "pages_by_platform": pages_by_platform,
        "landing_page_labels": LANDING_PAGE_ATTRIBUTION_LABELS,
        "ai_traffic": ai_traffic,
    }


_CACHE_PRESETS = ("LAST_7_DAYS", "LAST_30_DAYS", "LAST_90_DAYS", "THIS_MONTH", "LAST_MONTH")


def fetch_pages_for_all_presets(
    *,
    client_key: str,
    client_slug: str,
) -> dict[str, Any]:
    """Fetch GA4 pages for all view-range presets in parallel for snapshot caching."""
    from concurrent.futures import ThreadPoolExecutor

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(_CACHE_PRESETS)) as pool:
        futures = {
            p: pool.submit(
                fetch_pages_for_dashboard,
                date_range=p,
                client_key=client_key,
                client_slug=client_slug,
            )
            for p in _CACHE_PRESETS
        }
        for p, fut in futures.items():
            try:
                results[p] = fut.result()
            except Exception:
                pass
    return results
