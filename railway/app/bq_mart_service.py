"""BigQuery marketing mart reader for the Penn BQ Test dashboard.

Reads from `fact_google_ads_campaign_daily` and `fact_linkedin_ads_campaign_daily`
in the marketing_marts dataset and builds a snapshot dict that render_penn_html()
can consume directly — giving Penn BQ Test the same Overview + Campaign Explorer
template as other dashboards without touching the snapshot/refresh workflow.

Required env vars (falls back to defaults if unset):
  BQ_MART_PROJECT_ID         — GCP project (default: penn-community-b-1699391543298)
  BQ_MART_DATASET_ID         — dataset      (default: marketing_marts)
  BQ_MART_TABLE              — Google table (default: fact_google_ads_campaign_daily)
  BQ_MART_LINKEDIN_TABLE     — LinkedIn table (default: fact_linkedin_ads_campaign_daily)

Auth uses the same GCP_SERVICE_ACCOUNT_JSON / BQ_PROJECT_ID credentials as GA4.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import bigquery_service

_DEFAULT_PROJECT = "penn-community-b-1699391543298"
_DEFAULT_DATASET = "marketing_marts"
_DEFAULT_TABLE = "fact_google_ads_campaign_daily"
_DEFAULT_GOOGLE_AD_GROUP_TABLE = "fact_google_ads_ad_group_daily"
_DEFAULT_GOOGLE_AD_TABLE = "fact_google_ads_ad_daily"
_DEFAULT_LINKEDIN_TABLE = "fact_linkedin_ads_campaign_daily"


def _project_id() -> str:
    return (os.getenv("BQ_MART_PROJECT_ID") or _DEFAULT_PROJECT).strip()


def _mart_table(name: str) -> str:
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    return f"`{_project_id()}.{dataset}.{name}`"


def _full_table() -> str:
    return _mart_table(os.getenv("BQ_MART_TABLE") or _DEFAULT_TABLE)


def _linkedin_full_table() -> str:
    return _mart_table(os.getenv("BQ_MART_LINKEDIN_TABLE") or _DEFAULT_LINKEDIN_TABLE)


def _resolve_project(bq_project_id: str | None) -> str:
    project = (bq_project_id or os.getenv("BQ_MART_PROJECT_ID") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project resolved for this client's marketing mart. "
            "Set gcp_project_id in the client's dashboard settings, or BQ_MART_PROJECT_ID "
            "for a single-tenant deployment. Refusing to silently fall back to another "
            "client's project."
        )
    return project


def _resolve_table(name: str, bq_project_id: str | None, bq_dataset_id: str | None) -> str:
    project = _resolve_project(bq_project_id)
    dataset = (bq_dataset_id or os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    return f"`{project}.{dataset}.{name}`"


def _is_table_not_found(exc: Exception) -> bool:
    """True when a mart query failed because the table simply doesn't exist.

    A missing mart table means the client hasn't set that source up (e.g. a
    client with no Google Ads has no fact_google_ads_* views) — that's an
    absent source, not an error worth alarming on. Real failures (auth,
    permission, SQL) are NOT swallowed by this check.
    """
    if getattr(exc, "code", None) == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "table" in msg


def fetch_campaign_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-campaign per-day rows (Google Ads mart).

    Assumes the mart has columns: date, campaign_id, campaign_name,
    spend, impressions, clicks, conversions.
    """
    table = _resolve_table(os.getenv("BQ_MART_TABLE") or _DEFAULT_TABLE, bq_project_id, bq_dataset_id)
    proj = _resolve_project(bq_project_id)
    ck_filter = f" AND client_key = '{client_key}'" if client_key else ""
    if start and end:
        where_clause = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'{ck_filter}"
    else:
        where_clause = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY){ck_filter}"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      CAST(campaign_id AS STRING) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(conversions AS FLOAT64)) AS conversions
    FROM {table}
    WHERE {where_clause}
    GROUP BY 1, 2
    ORDER BY 1 DESC, 4 DESC
    """
    return bigquery_service.run_query(sql, project_id=proj, credentials_env=credentials_env, max_rows=5000)


def fetch_linkedin_campaign_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-campaign per-day rows (LinkedIn Ads mart)."""
    table = _resolve_table(os.getenv("BQ_MART_LINKEDIN_TABLE") or _DEFAULT_LINKEDIN_TABLE, bq_project_id, bq_dataset_id)
    proj = _resolve_project(bq_project_id)
    ck_filter = f" AND client_key = '{client_key}'" if client_key else ""
    if start and end:
        where_clause = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'{ck_filter}"
    else:
        where_clause = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY){ck_filter}"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      campaign_id,
      MAX(campaign_name) AS campaign_name,
      MAX(CAST(campaign_group_id AS STRING)) AS campaign_group_id,
      MAX(CAST(campaign_group_name AS STRING)) AS campaign_group_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where_clause}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=proj, credentials_env=credentials_env, max_rows=5000)


def fetch_google_ad_group_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-ad-group per-day rows from the Google Ads ad group mart."""
    table = _resolve_table(_DEFAULT_GOOGLE_AD_GROUP_TABLE, bq_project_id, bq_dataset_id)
    ck_filter = f" AND client_key = '{client_key}'" if client_key else ""
    if start and end:
        where_clause = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'{ck_filter}"
    else:
        where_clause = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY){ck_filter}"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      ad_group_id,
      MAX(ad_group_name) AS ad_group_name,
      MAX(campaign_id) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where_clause}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_resolve_project(bq_project_id), credentials_env=credentials_env, max_rows=10000)


def fetch_google_ad_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
) -> list[dict[str, Any]]:
    """Return per-ad per-day rows from the Google Ads ad mart."""
    table = _resolve_table(_DEFAULT_GOOGLE_AD_TABLE, bq_project_id, bq_dataset_id)
    ck_filter = f" AND client_key = '{client_key}'" if client_key else ""
    if start and end:
        where_clause = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'{ck_filter}"
    else:
        where_clause = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY){ck_filter}"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      ad_id,
      MAX(ad_name) AS ad_name,
      MAX(ad_group_id) AS ad_group_id,
      MAX(ad_group_name) AS ad_group_name,
      MAX(campaign_id) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where_clause}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_resolve_project(bq_project_id), credentials_env=credentials_env, max_rows=20000)


def _aggregate_campaign_rows(rows: list[dict[str, Any]]) -> tuple[
    dict[str, dict[str, Any]],  # by_date
    dict[str, dict[str, Any]],  # by_campaign
]:
    by_date: dict[str, dict[str, Any]] = {}
    by_campaign: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = str(row.get("metric_date") or "")[:10]
        spend = float(row.get("spend") or 0)
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        conversions = float(row.get("conversions") or 0)
        conversion_value = float(row.get("conversion_value") or 0)
        cid = str(row.get("campaign_id") or "")
        cname = str(row.get("campaign_name") or cid or "—")

        if d:
            day = by_date.setdefault(d, {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0})
            day["spend"] += spend
            day["impressions"] += impressions
            day["clicks"] += clicks
            day["conversions"] += conversions

        if cid:
            camp = by_campaign.setdefault(cid, {
                "campaign_id": cid,
                "campaign_name": cname,
                "spend": 0.0,
                "impressions": 0,
                "clicks": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            })
            camp["spend"] += spend
            camp["impressions"] += impressions
            camp["clicks"] += clicks
            camp["conversions"] += conversions
            camp["conversion_value"] += conversion_value
            if cname and cname != cid and not camp.get("campaign_name"):
                camp["campaign_name"] = cname

    return by_date, by_campaign


def _daily_rows(by_date: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "metric_date": d,
            "spend": v["spend"],
            "impressions": v["impressions"],
            "clicks": v["clicks"],
            "conversions": v["conversions"],
            "conversion_value": 0.0,
        }
        for d, v in sorted(by_date.items())
    ]


def _campaign_breakdowns(by_campaign: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "id": c["campaign_id"],
                "name": c["campaign_name"],
                "entity_level": "campaign",
                "spend": c["spend"],
                "clicks": c["clicks"],
                "impressions": c["impressions"],
                "conversions": c["conversions"],
                "parent_id": "",
                "parent_name": "",
            }
            for c in by_campaign.values()
        ],
        key=lambda r: r["spend"],
        reverse=True,
    )


def _google_ad_group_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ag: dict[str, dict[str, Any]] = {}
    for row in rows:
        agid = str(row.get("ad_group_id") or "").strip()
        if not agid:
            continue
        if agid not in by_ag:
            by_ag[agid] = {
                "id": agid,
                "name": str(row.get("ad_group_name") or agid),
                "entity_level": "ad_group",
                "parent_id": str(row.get("campaign_id") or "").strip(),
                "parent_name": str(row.get("campaign_name") or "").strip(),
                "campaign_id": str(row.get("campaign_id") or "").strip(),
                "campaign_name": str(row.get("campaign_name") or "").strip(),
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        ag = by_ag[agid]
        ag["spend"] += float(row.get("spend") or 0)
        ag["clicks"] += int(row.get("clicks") or 0)
        ag["impressions"] += int(row.get("impressions") or 0)
        ag["conversions"] += float(row.get("conversions") or 0)
        ag["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_ag.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for ag in out:
        ag["ctr"] = _safe_ratio(float(ag["clicks"]), float(ag["impressions"]))
        ag["cpc"] = _safe_ratio(float(ag["spend"]), float(ag["clicks"]))
        ag["cpm"] = _safe_ratio(float(ag["spend"]), float(ag["impressions"]), 1000)
        ag["cost_per_conversion"] = _safe_ratio(float(ag["spend"]), float(ag["conversions"]))
    return out


def _google_ad_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ad: dict[str, dict[str, Any]] = {}
    for row in rows:
        adid = str(row.get("ad_id") or "").strip()
        if not adid:
            continue
        if adid not in by_ad:
            by_ad[adid] = {
                "id": adid,
                "name": str(row.get("ad_name") or f"Ad {adid}"),
                "entity_level": "ad",
                "parent_id": str(row.get("ad_group_id") or "").strip(),
                "parent_name": str(row.get("ad_group_name") or "").strip(),
                "ad_group_id": str(row.get("ad_group_id") or "").strip(),
                "ad_group_name": str(row.get("ad_group_name") or "").strip(),
                "campaign_id": str(row.get("campaign_id") or "").strip(),
                "campaign_name": str(row.get("campaign_name") or "").strip(),
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        ad = by_ad[adid]
        ad["spend"] += float(row.get("spend") or 0)
        ad["clicks"] += int(row.get("clicks") or 0)
        ad["impressions"] += int(row.get("impressions") or 0)
        ad["conversions"] += float(row.get("conversions") or 0)
        ad["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_ad.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for ad in out:
        ad["ctr"] = _safe_ratio(float(ad["clicks"]), float(ad["impressions"]))
        ad["cpc"] = _safe_ratio(float(ad["spend"]), float(ad["clicks"]))
        ad["cpm"] = _safe_ratio(float(ad["spend"]), float(ad["impressions"]), 1000)
        ad["cost_per_conversion"] = _safe_ratio(float(ad["spend"]), float(ad["conversions"]))
    return out


def _platform_totals(by_date: dict, by_campaign: dict) -> dict[str, Any]:
    return {
        "spend": sum(v["spend"] for v in by_date.values()),
        "clicks": sum(v["clicks"] for v in by_date.values()),
        "impressions": sum(v["impressions"] for v in by_date.values()),
        "conversions": sum(v["conversions"] for v in by_date.values()),
        "campaign_count": len(by_campaign),
    }


def google_daily_series(
    *,
    start: date,
    end: date,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    """Per-day Google paid series + totals from the campaign mart.

    Aggregates ``fact_google_ads_campaign_daily`` by date across all campaigns —
    the canonical campaign-level numbers. This deliberately excludes the
    account-level non-campaign spend (adjustments, null-campaign rows) that the
    Google Ads API account total carries, which is why the API daily can read
    high (e.g. $8,238) vs the mart's correct $6,475.

    Returns ``{"daily": [...], "totals": {...}}``; an empty dict when the mart has
    no rows for the range (so callers can fall back to the API daily).
    """
    rows = fetch_campaign_daily(
        start=start,
        end=end,
        bq_project_id=bq_project_id,
        bq_dataset_id=bq_dataset_id,
        credentials_env=credentials_env,
    )
    by_date, by_campaign = _aggregate_campaign_rows(rows)
    if not by_date:
        return {}
    return {"daily": _daily_rows(by_date), "totals": _platform_totals(by_date, by_campaign)}


def _safe_ratio(num: float, den: float, mult: float = 1.0) -> float:
    return (num / den * mult) if den else 0.0


def _clean_id(val: Any) -> str:
    s = str(val or "").strip()
    return "" if s.lower() in ("none", "null", "0", "") else s


def _build_linkedin_mart_breakdowns(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build campaign_group (tier 1) and campaign (tier 2) rows from mart daily rows.

    Gracefully stops at campaign level when campaign_group_id is absent — never
    duplicates parent totals into child rows.
    """
    by_campaign: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = _clean_id(row.get("campaign_id"))
        if not cid:
            continue
        gid = _clean_id(row.get("campaign_group_id"))
        gname = str(row.get("campaign_group_name") or "").strip()
        if gname.lower() in ("none", "null"):
            gname = ""
        cname = str(row.get("campaign_name") or cid)

        if cid not in by_campaign:
            by_campaign[cid] = {
                "id": cid,
                "name": cname,
                "entity_level": "campaign",
                "campaign_id": cid,
                "campaign_name": cname,
                "parent_id": gid,
                "parent_name": gname,
                "campaign_group_id": gid,
                "campaign_group_name": gname,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        item = by_campaign[cid]
        # Backfill group info on the first row that carries it
        if gid and not item["campaign_group_id"]:
            item["campaign_group_id"] = gid
            item["campaign_group_name"] = gname
            item["parent_id"] = gid
            item["parent_name"] = gname
        item["spend"] += float(row.get("spend") or 0)
        item["clicks"] += int(row.get("clicks") or 0)
        item["impressions"] += int(row.get("impressions") or 0)
        item["conversions"] += float(row.get("conversions") or 0)
        item["conversion_value"] += float(row.get("conversion_value") or 0)

    campaign_list = sorted(
        by_campaign.values(), key=lambda r: float(r.get("spend") or 0), reverse=True
    )
    for item in campaign_list:
        item["ctr"] = _safe_ratio(float(item["clicks"]), float(item["impressions"]))
        item["cpc"] = _safe_ratio(float(item["spend"]), float(item["clicks"]))
        item["cpm"] = _safe_ratio(float(item["spend"]), float(item["impressions"]), 1000)
        item["cost_per_conversion"] = _safe_ratio(
            float(item["spend"]), float(item["conversions"])
        )

    # Aggregate campaign_group rows bottom-up from campaign data
    by_group: dict[str, dict[str, Any]] = {}
    for camp in campaign_list:
        gid = str(camp.get("campaign_group_id") or "").strip()
        if not gid:
            continue
        if gid not in by_group:
            by_group[gid] = {
                "id": gid,
                "name": str(camp.get("campaign_group_name") or f"Group {gid}"),
                "entity_level": "campaign_group",
                "campaign_group_id": gid,
                "campaign_group_name": str(camp.get("campaign_group_name") or f"Group {gid}"),
                "parent_id": "",
                "parent_name": "",
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            }
        g = by_group[gid]
        g["spend"] += camp["spend"]
        g["clicks"] += camp["clicks"]
        g["impressions"] += camp["impressions"]
        g["conversions"] += camp["conversions"]
        g["conversion_value"] += camp.get("conversion_value", 0.0)

    group_list = sorted(
        by_group.values(), key=lambda r: float(r.get("spend") or 0), reverse=True
    )
    for g in group_list:
        g["ctr"] = _safe_ratio(float(g["clicks"]), float(g["impressions"]))
        g["cpc"] = _safe_ratio(float(g["spend"]), float(g["clicks"]))
        g["cpm"] = _safe_ratio(float(g["spend"]), float(g["impressions"]), 1000)
        g["cost_per_conversion"] = _safe_ratio(float(g["spend"]), float(g["conversions"]))

    return {
        "campaign_group": group_list,
        "campaign": campaign_list,
        "creative": [],  # no creative-level BQ table yet; drilldown stops here
    }


def fetch_marketing_daily_totals(
    *,
    start: date,
    end: date,
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
) -> dict[str, Any]:
    """Unified paid media totals from fact_marketing_daily (all platforms)."""
    table = _resolve_table("fact_marketing_daily", bq_project_id, bq_dataset_id)
    ck_filter = f" AND client_key = '{client_key}'" if client_key else ""
    sql = f"""
    SELECT
      SUM(CAST(spend AS FLOAT64)) AS spend,
      SUM(CAST(impressions AS INT64)) AS impressions,
      SUM(CAST(clicks AS INT64)) AS clicks,
      SUM(CAST(conversions AS FLOAT64)) AS conversions
    FROM {table}
    WHERE date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'{ck_filter}
    """
    rows = bigquery_service.run_query(
        sql,
        project_id=_resolve_project(bq_project_id),
        credentials_env=credentials_env,
        max_rows=1,
    )
    if rows and rows[0]:
        r = rows[0]
        return {
            "spend": float(r.get("spend") or 0),
            "impressions": int(r.get("impressions") or 0),
            "clicks": int(r.get("clicks") or 0),
            "conversions": float(r.get("conversions") or 0),
        }
    return {}


def build_snapshot(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
    preset: str = "LAST_30_DAYS",
    client_key: str | None = None,
    bq_project_id: str | None = None,
    bq_dataset_id: str | None = None,
    credentials_env: str | None = None,
    include_google: bool = True,
    include_linkedin: bool = True,
) -> dict[str, Any]:
    """Query Google and LinkedIn marts and return a snapshot dict compatible with render_penn_html().

    include_google / include_linkedin let callers skip a platform a client
    doesn't have (e.g. a client with no Google Ads). Skipped platforms simply
    come back empty — no query, no warning. A queried mart whose table doesn't
    exist yet (source set up but not synced) is also treated as empty rather
    than surfaced as an error; only real failures (auth, permission, SQL) are.
    """
    from concurrent.futures import ThreadPoolExecutor
    errors: dict[str, str] = {}

    google_rows: list[dict[str, Any]] = []
    google_ad_group_rows: list[dict[str, Any]] = []
    google_ad_rows: list[dict[str, Any]] = []
    linkedin_rows: list[dict[str, Any]] = []

    _bq_kwargs = dict(client_key=client_key, bq_project_id=bq_project_id, bq_dataset_id=bq_dataset_id, credentials_env=credentials_env)

    def _collect(fut, error_key: str) -> list[dict[str, Any]]:
        """Resolve a mart future; missing table → empty, real error → recorded."""
        try:
            return fut.result()
        except Exception as exc:
            if not _is_table_not_found(exc):
                errors[error_key] = str(exc)[:600]
            return []

    with ThreadPoolExecutor(max_workers=4) as _pool:
        _gf = _gag = _gad = _lf = None
        if include_google:
            _gf = _pool.submit(fetch_campaign_daily, days=days, start=start, end=end, **_bq_kwargs)
            _gag = _pool.submit(fetch_google_ad_group_daily, days=days, start=start, end=end, **_bq_kwargs)
            _gad = _pool.submit(fetch_google_ad_daily, days=days, start=start, end=end, **_bq_kwargs)
        if include_linkedin:
            _lf = _pool.submit(fetch_linkedin_campaign_daily, days=days, start=start, end=end, **_bq_kwargs)

        if _gf is not None:
            google_rows = _collect(_gf, "bq_mart_google")
        if _gag is not None:
            google_ad_group_rows = _collect(_gag, "bq_mart_google_ad_group")
        if _gad is not None:
            google_ad_rows = _collect(_gad, "bq_mart_google_ad")
        if _lf is not None:
            linkedin_rows = _collect(_lf, "bq_mart_linkedin")

    by_date_g, by_campaign_g = _aggregate_campaign_rows(google_rows)
    totals_g = _platform_totals(by_date_g, by_campaign_g)

    # LinkedIn: build grouped breakdown (campaign_group → campaign) from mart rows.
    # Daily chart data still aggregated the same way; breakdown uses the richer builder.
    by_date_li, _ = _aggregate_campaign_rows(linkedin_rows)
    li_breakdowns = _build_linkedin_mart_breakdowns(linkedin_rows)

    li_campaigns = li_breakdowns["campaign"]
    totals_li = {
        "spend": sum(float(c.get("spend") or 0) for c in li_campaigns),
        "clicks": sum(int(c.get("clicks") or 0) for c in li_campaigns),
        "impressions": sum(int(c.get("impressions") or 0) for c in li_campaigns),
        "conversions": sum(float(c.get("conversions") or 0) for c in li_campaigns),
        "campaign_count": len(li_campaigns),
    }

    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=days - 1)

    try:
        agg = fetch_marketing_daily_totals(
            start=start,
            end=end,
            client_key=client_key,
            bq_project_id=bq_project_id,
            bq_dataset_id=bq_dataset_id,
            credentials_env=credentials_env,
        )
        if not agg:
            raise ValueError("empty")
    except Exception as exc:
        if not _is_table_not_found(exc):
            errors["fact_marketing_daily"] = str(exc)[:300]
        agg = {
            "spend": totals_g["spend"] + totals_li["spend"],
            "clicks": totals_g["clicks"] + totals_li["clicks"],
            "impressions": totals_g["impressions"] + totals_li["impressions"],
            "conversions": totals_g["conversions"] + totals_li["conversions"],
        }

    return {
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "preset": preset,
        },
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
        "accounts": {
            "google": _resolve_project(bq_project_id),
            "linkedin": None,
            "meta": None,
        },
        "daily_metrics": {
            "google": _daily_rows(by_date_g),
            "linkedin": _daily_rows(by_date_li),
        },
        "platform_totals": {
            "google": totals_g,
            "linkedin": totals_li,
        },
        "breakdowns": {
            "google": {
                "campaign": _campaign_breakdowns(by_campaign_g),
                "ad_group": _google_ad_group_breakdowns(google_ad_group_rows),
                "ad": _google_ad_breakdowns(google_ad_rows),
            },
            "linkedin": li_breakdowns,
        },
        "aggregated_paid_media": agg,
        "business_line_campaigns": [],
        "warehouse_sync": {},
        "ga4_attribution": None,
        "ga4_pages": None,
        "errors": errors,
        "refresh_mode": "bq_mart",
    }


def env_summary() -> dict[str, Any]:
    """Return BQ mart config for display in dashboard settings."""
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_DATASET).strip()
    return {
        "project_id": _project_id(),
        "dataset_id": dataset,
        "google_table": (os.getenv("BQ_MART_TABLE") or _DEFAULT_TABLE).strip(),
        "linkedin_table": (os.getenv("BQ_MART_LINKEDIN_TABLE") or _DEFAULT_LINKEDIN_TABLE).strip(),
    }
