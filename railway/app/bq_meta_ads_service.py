"""Sync Meta Ads API â†’ BigQuery and build dashboard snapshot from mart views."""

from __future__ import annotations

import contextlib
import contextvars
from datetime import date
from typing import Any

import bigquery_service
import bigquery_warehouse
import meta_service


_DEFAULT_MART_DATASET = "marketing_marts"
_DEFAULT_CAMPAIGN_FACT = "fact_meta_ads_campaign_daily"
_DEFAULT_ADSET_FACT = "fact_meta_ads_adset_daily"
_DEFAULT_AD_FACT = "fact_meta_ads_ad_daily"


# Per-client routing override. Default None â†’ Penn env fallback, so Penn /
# penn-bq-test (which never set a route) are unchanged. Unlike LinkedIn, Meta's
# read/write paths use ThreadPoolExecutor, so each pool.submit must run inside a
# copied context (copy_context().run) for the contextvar to reach the worker â€”
# bare submit() does NOT inherit contextvars (the GSC bug).
_route_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "bq_meta_route", default=None
)


@contextlib.contextmanager
def route(
    *,
    bq_project_id: str | None = None,
    credentials_env: str | None = None,
    meta_dataset_id: str | None = None,
):
    """Scope Meta BigQuery reads/writes to a specific project/credentials/dataset."""
    import os
    payload = {
        "project": (bq_project_id or "").strip() or None,
        "credentials_env": (credentials_env or "").strip() or None,
    }
    token = _route_ctx.set(payload if (payload["project"] or payload["credentials_env"]) else None)
    resolved_dataset = (
        (meta_dataset_id or "").strip()
        or (os.getenv("BQ_META_DATASET_ID") or "").strip()
        or "raw_meta_ads"
    )
    try:
        import bigquery_warehouse

        with bigquery_warehouse.route(
            bq_project_id=payload["project"],
            credentials_env=payload["credentials_env"],
            meta_dataset_id=resolved_dataset,
        ):
            yield
    finally:
        _route_ctx.reset(token)


def _routed_credentials_env() -> str | None:
    r = _route_ctx.get()
    return r.get("credentials_env") if r else None


def _project_id() -> str:
    import os
    r = _route_ctx.get()
    if r and r.get("project"):
        return r["project"]
    project = (os.getenv("BQ_META_PROJECT_ID") or "").strip()
    if not project:
        raise RuntimeError(
            "No BigQuery project routed for Meta reads. Call bq_meta_ads_service.route("
            "bq_project_id=...) for this client, or set BQ_META_PROJECT_ID for a single-tenant "
            "deployment. Refusing to silently fall back to another client's project."
        )
    return project


def _mart_table(name: str) -> str:
    import os
    dataset = (os.getenv("BQ_MART_DATASET_ID") or _DEFAULT_MART_DATASET).strip()
    return f"`{_project_id()}.{dataset}.{name}`"


def _safe_ratio(numerator: float, denominator: float, multiplier: float = 1.0) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * multiplier, 4)


def sync_meta_to_bq(
    account_id: str,
    *,
    start: date,
    end: date,
    access_token: str | None = None,
    client_key: str = "",
) -> dict[str, Any]:
    """Fetch Meta campaign/adset/ad daily metrics + ad creatives and upsert into BigQuery.

    access_token: explicit token to use. When omitted, falls back to load_meta_env()
    which reads the global OAuth store (no client_slug). Always pass an explicit token
    when syncing a client-scoped connector so the right account is used.
    """
    import logging
    from concurrent.futures import ThreadPoolExecutor

    _log = logging.getLogger(__name__)
    account_id_clean = meta_service._normalize_account_id(account_id)
    bigquery_warehouse.ensure_meta_tables()

    _log.info("sync_meta_to_bq: account=%s start=%s end=%s has_token=%s",
              account_id_clean, start, end, bool(access_token))

    campaign_rows: list[dict[str, Any]] = []
    adset_rows: list[dict[str, Any]] = []
    ad_rows: list[dict[str, Any]] = []
    creative_rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    # Resolve each ad set's optimization_goal FIRST so the daily insight parses
    # count only the ad set's true "Result" event (matching Ads Manager) instead
    # of summing every conversion-like action bucket. If this fails, fall back to
    # de-duplicated counting (resolver=None) rather than failing the whole sync.
    resolver = None
    try:
        resolver = meta_service.fetch_result_resolver(
            account_id_clean, access_token=access_token
        )
    except Exception as exc:
        errors["meta_result_resolver"] = str(exc)[:400]
        _log.warning(
            "Meta result resolver failed [%s]: %s — falling back to de-duplicated "
            "conversion counting", client_key, exc,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        _cf = pool.submit(meta_service.fetch_campaign_daily_metrics, account_id_clean,
                          start=start, end=end, access_token=access_token, resolver=resolver)
        _af = pool.submit(meta_service.fetch_adset_daily_metrics, account_id_clean,
                          start=start, end=end, access_token=access_token, resolver=resolver)
        _adf = pool.submit(meta_service.fetch_ad_daily_metrics, account_id_clean,
                           start=start, end=end, access_token=access_token, resolver=resolver)
        _crf = pool.submit(meta_service.fetch_ad_creative_metadata, account_id_clean,
                           access_token=access_token)
        try:
            campaign_rows = _cf.result()
        except Exception as exc:
            errors["meta_campaign_fetch"] = str(exc)[:400]
        try:
            adset_rows = _af.result()
        except Exception as exc:
            errors["meta_adset_fetch"] = str(exc)[:400]
        try:
            ad_rows = _adf.result()
        except Exception as exc:
            errors["meta_ad_fetch"] = str(exc)[:400]
        try:
            creative_rows = _crf.result()
        except Exception as exc:
            errors["meta_creative_fetch"] = str(exc)[:400]

    _log.info("sync_meta_to_bq fetch: campaign=%d adset=%d ad=%d creative=%d errors=%s",
              len(campaign_rows), len(adset_rows), len(ad_rows), len(creative_rows),
              list(errors.keys()) or "none")

    campaign_mirror = bigquery_warehouse.mirror_meta_campaign_daily_batch(account_id_clean, campaign_rows, client_key=client_key)
    adset_mirror = bigquery_warehouse.mirror_meta_adset_daily_batch(account_id_clean, adset_rows, client_key=client_key)
    ad_mirror = bigquery_warehouse.mirror_meta_ad_daily_batch(account_id_clean, ad_rows, client_key=client_key)
    creative_mirror = bigquery_warehouse.mirror_meta_ad_creative_batch(account_id_clean, creative_rows, client_key=client_key)

    _log.info("sync_meta_to_bq bq: campaign=%d adset=%d ad=%d creative=%d",
              campaign_mirror.get("rows_upserted", 0), adset_mirror.get("rows_upserted", 0),
              ad_mirror.get("rows_upserted", 0), creative_mirror.get("rows_upserted", 0))

    views = bigquery_warehouse.create_meta_mart_views()

    return {
        "account_id": account_id_clean,
        "campaign_rows": campaign_mirror.get("rows_upserted", 0),
        "adset_rows": adset_mirror.get("rows_upserted", 0),
        "ad_rows": ad_mirror.get("rows_upserted", 0),
        "creative_rows": creative_mirror.get("rows_upserted", 0),
        "views": views.get("views", []),
        "errors": errors,
    }


def fetch_meta_campaign_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    table = _mart_table(_DEFAULT_CAMPAIGN_FACT)
    if start and end:
        where = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), credentials_env=_routed_credentials_env(), max_rows=5000)


def fetch_meta_adset_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    table = _mart_table(_DEFAULT_ADSET_FACT)
    if start and end:
        where = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      adset_id,
      MAX(adset_name) AS adset_name,
      MAX(campaign_id) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value
    FROM {table}
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), credentials_env=_routed_credentials_env(), max_rows=10000)


def fetch_meta_ad_daily(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    table = _mart_table(_DEFAULT_AD_FACT)
    if start and end:
        where = f"date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
    else:
        where = f"date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)"
    sql = f"""
    SELECT
      CAST(date AS STRING) AS metric_date,
      ad_id,
      MAX(ad_name) AS ad_name,
      MAX(adset_id) AS adset_id,
      MAX(adset_name) AS adset_name,
      MAX(campaign_id) AS campaign_id,
      MAX(campaign_name) AS campaign_name,
      SUM(spend) AS spend,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      SUM(conversions) AS conversions,
      SUM(conversion_value) AS conversion_value,
      MAX(thumbnail_url) AS thumbnail_url,
      MAX(image_url) AS image_url,
      MAX(media_type) AS media_type
    FROM {table}
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1 DESC, spend DESC
    """
    return bigquery_service.run_query(sql, project_id=_project_id(), credentials_env=_routed_credentials_env(), max_rows=20000)


def _build_campaign_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_c: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("campaign_id") or "").strip()
        if not cid:
            continue
        if cid not in by_c:
            by_c[cid] = {
                "id": cid,
                "name": str(row.get("campaign_name") or cid),
                "entity_level": "campaign",
                "parent_id": None,
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        c = by_c[cid]
        c["spend"] += float(row.get("spend") or 0)
        c["clicks"] += int(row.get("clicks") or 0)
        c["impressions"] += int(row.get("impressions") or 0)
        c["conversions"] += float(row.get("conversions") or 0)
        c["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_c.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for c in out:
        c["ctr"] = _safe_ratio(float(c["clicks"]), float(c["impressions"]))
        c["cpc"] = _safe_ratio(float(c["spend"]), float(c["clicks"]))
        c["cpm"] = _safe_ratio(float(c["spend"]), float(c["impressions"]), 1000)
        c["cost_per_conversion"] = _safe_ratio(float(c["spend"]), float(c["conversions"]))
    return out


def _build_adset_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_as: dict[str, dict[str, Any]] = {}
    for row in rows:
        asid = str(row.get("adset_id") or "").strip()
        if not asid:
            continue
        if asid not in by_as:
            by_as[asid] = {
                "id": asid,
                "name": str(row.get("adset_name") or asid),
                "entity_level": "adset",
                "parent_id": str(row.get("campaign_id") or "").strip(),
                "parent_name": str(row.get("campaign_name") or "").strip(),
                "campaign_id": str(row.get("campaign_id") or "").strip(),
                "campaign_name": str(row.get("campaign_name") or "").strip(),
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        a = by_as[asid]
        a["spend"] += float(row.get("spend") or 0)
        a["clicks"] += int(row.get("clicks") or 0)
        a["impressions"] += int(row.get("impressions") or 0)
        a["conversions"] += float(row.get("conversions") or 0)
        a["conversion_value"] += float(row.get("conversion_value") or 0)
    out = sorted(by_as.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for a in out:
        a["ctr"] = _safe_ratio(float(a["clicks"]), float(a["impressions"]))
        a["cpc"] = _safe_ratio(float(a["spend"]), float(a["clicks"]))
        a["cpm"] = _safe_ratio(float(a["spend"]), float(a["impressions"]), 1000)
        a["cost_per_conversion"] = _safe_ratio(float(a["spend"]), float(a["conversions"]))
    return out


def _build_ad_breakdowns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ad: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = str(row.get("ad_id") or "").strip()
        if not aid:
            continue
        if aid not in by_ad:
            by_ad[aid] = {
                "id": aid,
                "name": str(row.get("ad_name") or f"Ad {aid}"),
                "entity_level": "ad",
                "parent_id": str(row.get("adset_id") or "").strip(),
                "parent_name": str(row.get("adset_name") or "").strip(),
                "adset_id": str(row.get("adset_id") or "").strip(),
                "adset_name": str(row.get("adset_name") or "").strip(),
                "campaign_id": str(row.get("campaign_id") or "").strip(),
                "campaign_name": str(row.get("campaign_name") or "").strip(),
                "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0,
            }
        a = by_ad[aid]
        a["spend"] += float(row.get("spend") or 0)
        a["clicks"] += int(row.get("clicks") or 0)
        a["impressions"] += int(row.get("impressions") or 0)
        a["conversions"] += float(row.get("conversions") or 0)
        a["conversion_value"] += float(row.get("conversion_value") or 0)
        for field in ("thumbnail_url", "image_url", "media_type"):
            val = str(row.get(field) or "").strip()
            if val and not a.get(field):
                a[field] = val
    out = sorted(by_ad.values(), key=lambda r: float(r.get("spend") or 0), reverse=True)
    for a in out:
        a["ctr"] = _safe_ratio(float(a["clicks"]), float(a["impressions"]))
        a["cpc"] = _safe_ratio(float(a["spend"]), float(a["clicks"]))
        a["cpm"] = _safe_ratio(float(a["spend"]), float(a["impressions"]), 1000)
        a["cost_per_conversion"] = _safe_ratio(float(a["spend"]), float(a["conversions"]))
    return out


def _build_daily_metrics(campaign_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-campaign daily rows into account-level daily totals for charts."""
    by_date: dict[str, dict[str, Any]] = {}
    for row in campaign_rows:
        d = str(row.get("metric_date") or "")[:10]
        if not d:
            continue
        if d not in by_date:
            by_date[d] = {"metric_date": d, "spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "conversion_value": 0.0}
        day = by_date[d]
        day["spend"] += float(row.get("spend") or 0)
        day["clicks"] += int(row.get("clicks") or 0)
        day["impressions"] += int(row.get("impressions") or 0)
        day["conversions"] += float(row.get("conversions") or 0)
        day["conversion_value"] += float(row.get("conversion_value") or 0)
    return sorted(by_date.values(), key=lambda r: r["metric_date"])


def build_meta_breakdowns(
    *,
    days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Read Meta mart views and return breakdowns + platform_totals + daily_metrics."""
    from concurrent.futures import ThreadPoolExecutor
    errors: dict[str, str] = {}
    campaign_rows: list[dict[str, Any]] = []
    adset_rows: list[dict[str, Any]] = []
    ad_rows: list[dict[str, Any]] = []

    # These fetch from the Meta mart views in BigQuery, so each task must run in
    # the calling thread's context for the routing contextvar to reach the
    # worker â€” bare pool.submit() does NOT inherit contextvars. copy_context()
    # is evaluated HERE (calling thread) so it snapshots the active route; a
    # fresh copy per task avoids the "already entered" error a shared Context
    # raises across concurrent threads.
    with ThreadPoolExecutor(max_workers=3) as pool:
        _cf = pool.submit(contextvars.copy_context().run,
                          lambda: fetch_meta_campaign_daily(days=days, start=start, end=end))
        _af = pool.submit(contextvars.copy_context().run,
                          lambda: fetch_meta_adset_daily(days=days, start=start, end=end))
        _adf = pool.submit(contextvars.copy_context().run,
                           lambda: fetch_meta_ad_daily(days=days, start=start, end=end))
        try:
            campaign_rows = _cf.result()
        except Exception as exc:
            errors["meta_campaign_read"] = str(exc)[:400]
        try:
            adset_rows = _af.result()
        except Exception as exc:
            errors["meta_adset_read"] = str(exc)[:400]
        try:
            ad_rows = _adf.result()
        except Exception as exc:
            errors["meta_ad_read"] = str(exc)[:400]

    campaigns = _build_campaign_breakdowns(campaign_rows)
    adsets = _build_adset_breakdowns(adset_rows)
    ads = _build_ad_breakdowns(ad_rows)
    daily = _build_daily_metrics(campaign_rows)

    totals = {
        "spend": sum(float(c.get("spend") or 0) for c in campaigns),
        "clicks": sum(int(c.get("clicks") or 0) for c in campaigns),
        "impressions": sum(int(c.get("impressions") or 0) for c in campaigns),
        "conversions": sum(float(c.get("conversions") or 0) for c in campaigns),
        "campaign_count": len(campaigns),
    }

    return {
        "breakdowns": {"campaign": campaigns, "adset": adsets, "ad": ads},
        "platform_totals": totals,
        "daily_metrics": daily,
        "errors": errors,
    }
