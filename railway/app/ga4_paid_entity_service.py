"""Read GA4 paid-entity rows (raw_ga4.ga4_paid_entity_daily) for id-based
campaign / ad set / ad conversion matching.

This is the GA4 Data API path that replaces the native event-export dependency:
the pipeline (ga4_reporting_service.fetch_paid_entity_daily) lands campaign /
ad set / ad ids via utm_id / utm_term / utm_content, and this service shapes
them into the same per-platform by_campaign / by_entity structure that
ga4_attribution_service produces from the event export — so build_ga4_campaign_index
and build_ga4_level_index consume either source unchanged.

The utm_term -> ad set / utm_content -> ad interpretation lives here (not in the
raw table) because it is a tagging convention that can differ per client.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from bigquery_service import run_query
from dates_util import resolve_date_range
from ga4_attribution_service import normalize_campaign_name
from ga4_clients import Ga4ClientTarget, resolve_target

_DEFAULT_RAW_DATASET = "raw_ga4"
_TABLE = "ga4_paid_entity_daily"


def _classify_platform(source_platform: str, source: str, medium: str) -> str | None:
    """Map a GA4 paid row to google / meta / linkedin, source_platform first."""
    sp = (source_platform or "").lower()
    src = (source or "").lower()
    med = (medium or "").lower()
    # Tag-driven signal (utm_source_platform) is the most reliable.
    if "meta" in sp or "facebook" in sp or "instagram" in sp:
        return "meta"
    if "google ads" in sp or sp == "google":
        return "google"
    if "linkedin" in sp:
        return "linkedin"
    # Fall back to source / medium heuristics.
    if src in ("facebook", "fb", "instagram", "ig", "meta", "msg", "an") \
            or "facebook" in src or "instagram" in src:
        return "meta"
    if "linkedin" in src:
        return "linkedin"
    if src == "google" and med in ("cpc", "ppc", "paid"):
        return "google"
    return None


def shape_paid_entity_rows(raw_rows: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Aggregate raw paid-entity rows into {platform: {by_campaign, by_entity}}.

    by_entity is keyed on (campaign_id, adset_id, ad_id); by_campaign on the
    campaign id (falling back to normalized name). Both sum across the source /
    medium / source_platform splits the raw grain carries.
    """
    acc: dict[str, dict[str, dict[Any, dict[str, Any]]]] = {}
    for r in raw_rows:
        platform = _classify_platform(
            str(r.get("source_platform") or ""),
            str(r.get("session_source") or ""),
            str(r.get("session_medium") or ""),
        )
        if not platform:
            continue
        cid = str(r.get("campaign_id") or "").strip()
        cname = str(r.get("campaign_name") or "").strip()
        if cname == "(not set)":
            cname = ""
        adset_id = str(r.get("manual_term") or "").strip()
        ad_id = str(r.get("manual_ad_content") or "").strip()
        sessions = int(r.get("sessions") or 0)
        engaged = int(r.get("engaged_sessions") or 0)
        key_events = int(r.get("key_events") or 0)
        page_views = int(r.get("screen_page_views") or r.get("page_views") or 0)

        buckets = acc.setdefault(platform, {"entity": {}, "campaign": {}})

        ek = (cid, adset_id, ad_id)
        ent = buckets["entity"].setdefault(ek, {
            "campaign_id": cid, "campaign_name": cname,
            "adset_id": adset_id, "ad_id": ad_id,
            "sessions": 0, "engaged_sessions": 0, "key_events": 0, "page_views": 0,
        })
        ent["sessions"] += sessions
        ent["engaged_sessions"] += engaged
        ent["key_events"] += key_events
        ent["page_views"] += page_views
        if cname and not ent["campaign_name"]:
            ent["campaign_name"] = cname

        ck = cid or normalize_campaign_name(cname)
        camp = buckets["campaign"].setdefault(ck, {
            "campaign_id": cid, "campaign_name": cname,
            "sessions": 0, "engaged_sessions": 0, "key_events": 0, "page_views": 0,
        })
        camp["sessions"] += sessions
        camp["engaged_sessions"] += engaged
        camp["key_events"] += key_events
        camp["page_views"] += page_views
        if cname and not camp["campaign_name"]:
            camp["campaign_name"] = cname

    return {
        platform: {
            "by_entity": list(b["entity"].values()),
            "by_campaign": list(b["campaign"].values()),
        }
        for platform, b in acc.items()
    }


def fetch_paid_entity_report(
    *,
    client_key: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: date | None = None,
    end: date | None = None,
    target: Ga4ClientTarget | None = None,
    raw_dataset: str = _DEFAULT_RAW_DATASET,
) -> dict[str, Any]:
    """Read raw_ga4.ga4_paid_entity_daily for a client and shape per platform.

    Returns {"platforms": {...}, "date_range": {...}, "row_count": N}. Empty
    platforms (e.g. table not synced yet) let callers fall back to the
    event-export attribution path.
    """
    target = target or resolve_target(client_key=client_key)
    if start and end:
        s, e = start, end
    else:
        s, e, _preset = resolve_date_range(date_range)

    table = f"`{target.bq_project_id}.{raw_dataset}.{_TABLE}`"
    where = [f"date BETWEEN DATE '{s.isoformat()}' AND DATE '{e.isoformat()}'"]
    if target.client_key:
        where.append(f"client_key = '{target.client_key}'")
    elif target.account_id:
        where.append(f"property_id = '{target.account_id}'")
    sql = f"""
    SELECT
      source_platform, session_source, session_medium,
      campaign_id, campaign_name, manual_term, manual_ad_content,
      SUM(sessions) AS sessions,
      SUM(engaged_sessions) AS engaged_sessions,
      SUM(key_events) AS key_events,
      SUM(screen_page_views) AS screen_page_views
    FROM {table}
    WHERE {" AND ".join(where)}
    GROUP BY 1, 2, 3, 4, 5, 6, 7
    """
    rows = run_query(
        sql,
        project_id=target.bq_project_id,
        credentials_env=target.credentials_env,
        max_rows=50000,
    )
    return {
        "platforms": shape_paid_entity_rows(rows),
        "date_range": {"start": s.isoformat(), "end": e.isoformat()},
        "row_count": len(rows),
    }
