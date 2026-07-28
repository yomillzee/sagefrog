"""Synthetic sample data for the built-in demo client.

Every dashboard panel reads through the shared ``_cached_bq_read`` helper in
``dashboard/routes/api_routes.py``. For the demo client that helper calls
``generate(key, payload)`` here instead of querying BigQuery, so the demo
dashboard is fully populated with believable — but entirely fictional —
numbers, with no GCP project, connectors, or live data of any kind.

Design goals
------------
* **Deterministic.** The same panel + date range always yields the same
  numbers, so a demo doesn't jitter between refreshes. Values are derived from
  a stable hash of the inputs (see ``_u``), never ``random`` at call time.
* **Shape-accurate.** Each builder returns the exact dict/list shape the
  corresponding ``marketing_service`` / ``bq_*_service`` fetch returns, so the
  existing frontend renders it unchanged.
* **Future-aligned & safe.** ``generate`` dispatches on the cache-source
  suffix. An unknown key (e.g. a brand-new panel added later) returns an
  empty-but-valid response rather than raising, so the demo never 500s while a
  new sample is being written.

The fictional brand is "Northwind Health" — a placeholder B2B healthcare
company. Nothing here corresponds to a real account.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

# --- Fictional catalog ---------------------------------------------------------

_DOMAIN = "northwindhealth.example"
_SITE_URL = f"https://www.{_DOMAIN}/"

# Paid-media platforms. `source` matches marketing_service's per-source grain;
# `platform` matches the paid_media view's source_platform (paid_* values the
# Overview summary cards filter on).
_PLATFORMS = [
    # source, platform, base daily spend, ctr, conv-rate, cpc
    ("google", "paid_google", 720.0, 0.041, 0.052, 3.10),
    ("meta", "paid_meta", 360.0, 0.019, 0.038, 1.45),
    ("linkedin", "paid_linkedin", 460.0, 0.006, 0.031, 9.20),
    ("microsoft", "paid_microsoft", 190.0, 0.035, 0.047, 2.35),
]

_GOOGLE_CAMPAIGNS = [
    ("2100000001", "NWH | Search | Brand"),
    ("2100000002", "NWH | Search | Occupational Health"),
    ("2100000003", "NWH | Search | Urgent Care"),
    ("2100000004", "NWH | PMax | Employer Services"),
    ("2100000005", "NWH | Display | Remarketing"),
    ("2100000006", "NWH | Search | Telehealth"),
]

_MICROSOFT_CAMPAIGNS = [
    ("3100000001", "NWH | Bing | Brand"),
    ("3100000002", "NWH | Bing | Occupational Health"),
    ("3100000003", "NWH | Bing | Urgent Care"),
]

_META_ADS = [
    ("6100000001", "Employer Benefits — Carousel"),
    ("6100000002", "Telehealth Launch — Video"),
    ("6100000003", "Flu Season — Single Image"),
    ("6100000004", "Occupational Health — Lead Form"),
    ("6100000005", "Urgent Care Near You — Story"),
]

_LINKEDIN_CREATIVES = [
    ("7100000001", "Employer Health Partnerships — Sponsored"),
    ("7100000002", "Whitepaper: Workforce Wellness ROI"),
    ("7100000003", "Webinar: Reducing Absenteeism"),
    ("7100000004", "Case Study: Regional Manufacturer"),
    ("7100000005", "Telehealth for Teams — Video"),
]

_KEYWORDS = [
    ("occupational health services", "EXACT", "Occupational Health", "NWH | Search | Occupational Health"),
    ("urgent care near me", "PHRASE", "Urgent Care", "NWH | Search | Urgent Care"),
    ("employer telehealth", "PHRASE", "Telehealth", "NWH | Search | Telehealth"),
    ("northwind health", "EXACT", "Brand", "NWH | Search | Brand"),
    ("workplace flu shots", "BROAD", "Occupational Health", "NWH | Search | Occupational Health"),
    ("pre employment physical", "PHRASE", "Occupational Health", "NWH | Search | Occupational Health"),
    ("virtual doctor visit", "BROAD", "Telehealth", "NWH | Search | Telehealth"),
    ("walk in clinic", "PHRASE", "Urgent Care", "NWH | Search | Urgent Care"),
]

_PAGES = [
    ("/services/occupational-health", "Occupational Health", "Services"),
    ("/services/urgent-care", "Urgent Care", "Services"),
    ("/services/telehealth", "Telehealth", "Services"),
    ("/employers", "Employer Solutions", "Employers"),
    ("/locations", "Locations", "Locations"),
    ("/about", "About Northwind Health", "Company"),
    ("/contact", "Contact Us", "Contact"),
    ("/resources/workforce-wellness", "Workforce Wellness Guide", "Resources"),
    ("/careers", "Careers", "Company"),
    ("/", "Homepage", "Home"),
]

_CHANNELS = ["Organic Search", "Paid Search", "Direct", "Organic Social", "Referral", "Paid Social", "Email"]
_AI_PLATFORMS = ["ChatGPT", "Perplexity", "Gemini", "Copilot"]
_KEY_EVENTS = ["form_submit", "generate_lead", "phone_call_click", "book_appointment", "form_start"]


# --- Deterministic pseudo-randomness ------------------------------------------

def _u(*parts: Any) -> float:
    """Stable float in [0, 1) derived from a hash of ``parts``.

    Deterministic across processes and time — the demo must not jitter between
    reloads — so this replaces ``random`` for every value here.
    """
    raw = "|".join(str(p) for p in parts)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(0xFFFFFFFFFFFF + 1)


def _jit(seed: Any, lo: float = 0.8, hi: float = 1.2) -> float:
    return lo + (hi - lo) * _u(seed)


def _season(d: date) -> float:
    """Weekday seasonality: quieter weekends, a mild mid-week peak."""
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd >= 5:
        return 0.55
    return 0.9 + 0.06 * (2 - abs(2 - wd))


def _round2(v: float) -> float:
    return round(float(v), 2)


# --- Date handling -------------------------------------------------------------

def _parse_date(val: Any) -> date | None:
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def _dates(payload: dict | None) -> tuple[date, date]:
    """Resolve (start, end) from a cache payload, defaulting to a trailing 30d.

    End defaults to yesterday (the latest 'complete' day), matching how the
    real pipeline lags a day. Capped to 400 days to keep generation light.
    """
    payload = payload or {}
    today = date.today()
    end = _parse_date(payload.get("end")) or (today - timedelta(days=1))
    start = _parse_date(payload.get("start")) or (end - timedelta(days=29))
    if start > end:
        start, end = end, start
    if (end - start).days > 400:
        start = end - timedelta(days=400)
    return start, end


def _each_day(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _date_range(start: date, end: date) -> dict:
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


# --- Paid-media core -----------------------------------------------------------

def _platform_daily(source: str, base_spend: float, ctr: float, conv_rate: float,
                    cpc: float, start: date, end: date) -> list[dict]:
    """One synthetic row per day for a paid platform."""
    rows = []
    for d in _each_day(start, end):
        spend = base_spend * _season(d) * _jit((source, "spend", d))
        eff_cpc = cpc * _jit((source, "cpc", d), 0.85, 1.15)
        clicks = max(1, round(spend / max(eff_cpc, 0.5)))
        impressions = round(clicks / max(ctr, 0.001))
        conversions = round(clicks * conv_rate * _jit((source, "cv", d), 0.7, 1.3), 1)
        conv_value = _round2(conversions * (140.0 + 90.0 * _u(source, "cvv")))
        rows.append({
            "date": d.isoformat(),
            "spend": _round2(spend),
            "impressions": int(impressions),
            "clicks": int(clicks),
            "conversions": float(conversions),
            "conversion_value": conv_value,
        })
    return rows


def _all_platform_daily(start: date, end: date) -> dict[str, list[dict]]:
    return {
        source: _platform_daily(source, base, ctr, conv, cpc, start, end)
        for source, _plat, base, ctr, conv, cpc in _PLATFORMS
    }


def _sum_metrics(rows: list[dict]) -> dict:
    agg = {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "conversion_value": 0.0}
    for r in rows:
        agg["spend"] += r.get("spend", 0) or 0
        agg["impressions"] += r.get("impressions", 0) or 0
        agg["clicks"] += r.get("clicks", 0) or 0
        agg["conversions"] += r.get("conversions", 0) or 0
        agg["conversion_value"] += r.get("conversion_value", 0) or 0
    agg["spend"] = _round2(agg["spend"])
    agg["conversions"] = round(agg["conversions"], 1)
    agg["conversion_value"] = _round2(agg["conversion_value"])
    return agg


# --- Builders: Overview --------------------------------------------------------

def _build_summary(payload: dict) -> dict:
    start, end = _dates(payload)
    per_platform = _all_platform_daily(start, end)
    plat_meta = {p[0]: p[1] for p in _PLATFORMS}

    totals = {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0}
    by_source: dict[str, dict] = {}
    daily: list[dict] = []
    for source, rows in per_platform.items():
        m = _sum_metrics(rows)
        totals["spend"] += m["spend"]
        totals["impressions"] += m["impressions"]
        totals["clicks"] += m["clicks"]
        totals["conversions"] += m["conversions"]
        by_source[plat_meta[source]] = {
            "spend": _round2(m["spend"]),
            "impressions": int(m["impressions"]),
            "clicks": int(m["clicks"]),
            "conversions": round(m["conversions"], 1),
        }
        for r in rows:
            daily.append({
                "date": r["date"],
                "source": plat_meta[source],
                "spend": r["spend"],
                "impressions": r["impressions"],
                "clicks": r["clicks"],
                "conversions": r["conversions"],
                "conversion_value": r["conversion_value"],
            })
    daily.sort(key=lambda r: (r["date"], r["source"]))

    spend = _round2(totals["spend"])
    impressions = int(totals["impressions"])
    clicks = int(totals["clicks"])
    conversions = round(totals["conversions"], 1)
    summary = {
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "cpc": _round2(spend / clicks) if clicks else 0.0,
        "cpa": _round2(spend / conversions) if conversions else 0.0,
        "ctr": _round2(clicks / impressions * 100) if impressions else 0.0,
    }
    return {
        "client_key": "demo",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "summary": summary,
        "by_source": by_source,
        "daily": daily,
    }


def _build_marketing(payload: dict) -> dict:
    start, end = _dates(payload)
    per_platform = _all_platform_daily(start, end)

    by_source = []
    daily_trend = []
    for source, rows in per_platform.items():
        m = _sum_metrics(rows)
        by_source.append({"source": source, **m})
        for r in rows:
            daily_trend.append({"date": r["date"], "source": source, **{
                "spend": r["spend"], "impressions": r["impressions"], "clicks": r["clicks"],
                "conversions": r["conversions"], "conversion_value": r["conversion_value"],
            }})
    by_source.sort(key=lambda r: r["spend"], reverse=True)
    daily_trend.sort(key=lambda r: (r["date"], r["source"]))

    totals = _sum_metrics([r for rows in per_platform.values() for r in rows])
    top = _build_top_campaigns(start, end, payload.get("top_limit", 10))
    return {
        "client": "demo",
        "date_range": _date_range(start, end),
        "summary": totals,
        "by_source": by_source,
        "daily_trend": daily_trend,
        "top_campaigns_by_spend": top,
    }


def _build_top_campaigns(start: date, end: date, limit: int) -> list[dict]:
    days = max(1, (end - start).days + 1)
    catalog = (
        [("google", cid, name) for cid, name in _GOOGLE_CAMPAIGNS]
        + [("microsoft", cid, name) for cid, name in _MICROSOFT_CAMPAIGNS]
        + [("meta", cid, name) for cid, name in _META_ADS]
        + [("linkedin", cid, name) for cid, name in _LINKEDIN_CREATIVES]
    )
    rows = []
    for source, cid, name in catalog:
        base = 40.0 + 260.0 * _u(cid, "spend")
        spend = _round2(base * days * _jit((cid, "campaign")))
        cpc = 1.0 + 9.0 * _u(cid, "cpc")
        clicks = max(1, round(spend / cpc))
        ctr = 0.01 + 0.05 * _u(cid, "ctr")
        impressions = round(clicks / ctr)
        conv = round(clicks * (0.02 + 0.05 * _u(cid, "cv")), 1)
        rows.append({
            "source": source,
            "campaign_id": cid,
            "campaign_name": name,
            "spend": spend,
            "impressions": int(impressions),
            "clicks": int(clicks),
            "conversions": float(conv),
            "conversion_value": _round2(conv * (150 + 120 * _u(cid, "cvv"))),
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    try:
        n = int(limit)
    except Exception:
        n = 10
    return rows[: max(1, n)]


def _build_health(payload: dict) -> dict:
    # mart_health has no date range; anchor freshness to "yesterday".
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=180)
    rows = []
    for source, _plat, base, ctr, conv, cpc in _PLATFORMS:
        daily = _platform_daily(source, base, ctr, conv, cpc, end - timedelta(days=29), end)
        m = _sum_metrics(daily)
        rows.append({
            "source": source,
            "row_count": (end - start).days + 1,
            "earliest_date": start.isoformat(),
            "latest_date": end.isoformat(),
            "spend": m["spend"],
            "impressions": m["impressions"],
            "clicks": m["clicks"],
            "conversions": m["conversions"],
        })
    # GA4 + Search Console freshness rows (no spend — website analytics).
    rows.append({
        "source": "google_analytics", "row_count": (end - start).days + 1,
        "earliest_date": start.isoformat(), "latest_date": end.isoformat(),
        "spend": None, "impressions": None, "clicks": None, "conversions": None,
    })
    rows.append({
        "source": "search_console", "row_count": (end - start).days + 1,
        "earliest_date": start.isoformat(), "latest_date": (end - timedelta(days=2)).isoformat(),
        "spend": None, "impressions": None, "clicks": None, "conversions": None,
    })
    return {"client": "demo", "row_count": len(rows), "rows": rows}


def _build_pacing_source_daily(payload: dict) -> dict:
    """Budget pacing reads /summary's `daily`; nothing extra needed, but the
    pacing endpoint requests summary directly, so this is unused. Kept for
    clarity that pacing is derived, not separately faked."""
    return _build_summary(payload)


# --- Builders: Website analytics (GA4) ----------------------------------------

def _sessions_base(seed: Any) -> int:
    return int(120 + 900 * _u(seed))


def _build_traffic_acquisition(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    by_channel = []
    for ch in _CHANNELS:
        sessions = int(_sessions_base(("ch", ch)) * days / 10)
        engaged = int(sessions * (0.45 + 0.4 * _u("eng", ch)))
        by_channel.append({
            "channel": ch,
            "sessions": sessions,
            "engaged_sessions": engaged,
            "engagement_rate": _round2(engaged / sessions * 100) if sessions else 0.0,
            "key_events": int(sessions * (0.02 + 0.05 * _u("ke", ch))),
        })
    by_channel.sort(key=lambda r: r["sessions"], reverse=True)

    daily = []
    for d in _each_day(start, end):
        sessions = int(sum(_sessions_base(("d", ch, d)) for ch in _CHANNELS) / 8 * _season(d))
        engaged = int(sessions * (0.5 + 0.3 * _u("de", d)))
        daily.append({"date": d.isoformat(), "sessions": sessions, "engaged_sessions": engaged})

    sources = [
        ("google", "organic"), ("google", "cpc"), ("(direct)", "(none)"),
        ("bing", "organic"), ("linkedin.com", "referral"), ("facebook", "paid"),
        ("newsletter", "email"),
    ]
    by_source = []
    for src, med in sources:
        sessions = int(_sessions_base(("src", src, med)) * days / 12)
        engaged = int(sessions * (0.45 + 0.4 * _u("se", src, med)))
        by_source.append({
            "source": src, "medium": med, "sessions": sessions,
            "engaged_sessions": engaged,
            "engagement_rate": _round2(engaged / sessions * 100) if sessions else 0.0,
            "key_events": int(sessions * (0.02 + 0.05 * _u("ske", src, med))),
        })
    by_source.sort(key=lambda r: r["sessions"], reverse=True)
    return {
        "client": "demo", "date_range": _date_range(start, end),
        "by_channel": by_channel, "daily": daily, "by_source": by_source,
    }


def _build_ai_traffic_daily(payload: dict) -> dict:
    start, end = _dates(payload)
    rows = []
    for d in _each_day(start, end):
        for plat in _AI_PLATFORMS:
            sessions = int((3 + 40 * _u("ai", plat)) * _season(d) * _jit(("ai", plat, d)))
            if sessions <= 0:
                continue
            rows.append({"date": d.isoformat(), "ai_platform": plat, "sessions": sessions})
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _page_rows(seed_ns: str, start: date, end: date) -> list[dict]:
    days = max(1, (end - start).days + 1)
    rows = []
    for path, topic, group in _PAGES:
        views = int((80 + 1400 * _u(seed_ns, path)) * days / 10)
        users = int(views * (0.55 + 0.3 * _u(seed_ns, "u", path)))
        sessions = int(views * (0.7 + 0.25 * _u(seed_ns, "s", path)))
        rows.append({
            "page_group": group,
            "page_topic": topic,
            "page_views": views,
            "users": users,
            "sessions": sessions,
            "engagement_seconds": int(sessions * (25 + 90 * _u(seed_ns, "e", path))),
            "key_events": int(sessions * (0.02 + 0.06 * _u(seed_ns, "k", path))),
        })
    rows.sort(key=lambda r: r["page_views"], reverse=True)
    return rows


def _build_pages_top(payload: dict) -> dict:
    start, end = _dates(payload)
    rows = _page_rows("top", start, end)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _build_pages_sources(payload: dict) -> dict:
    start, end = _dates(payload)
    rows = _page_rows("src", start, end)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _build_device_split(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    for device, share in (("desktop", 0.52), ("mobile", 0.41), ("tablet", 0.07)):
        users = int(4200 * share * days / 30 * _jit((device, "d")))
        engaged = int(users * (0.5 + 0.35 * _u("dev", device)))
        rows.append({
            "device": device, "users": users, "engaged_sessions": engaged,
            "key_events": int(users * (0.03 + 0.05 * _u("devk", device))),
        })
    return {"client": "demo", "date_range": _date_range(start, end), "rows": rows}


def _build_landing_pages(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    for path, topic, _group in _PAGES:
        sessions = int((60 + 900 * _u("land", path)) * days / 10)
        users = int(sessions * (0.7 + 0.25 * _u("landu", path)))
        new_users = int(users * (0.5 + 0.35 * _u("landn", path)))
        key_events = int(sessions * (0.02 + 0.06 * _u("landk", path)))
        rows.append({
            "page_path": path,
            "sessions": sessions,
            "users": users,
            "new_users": new_users,
            "key_events": key_events,
            "key_event_rate": _round2(key_events / sessions * 100) if sessions else 0.0,
            "avg_engagement_seconds": _round2(20 + 100 * _u("lande", path)),
        })
    rows.sort(key=lambda r: r["sessions"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _build_user_acquisition(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    by_channel = []
    for ch in _CHANNELS:
        new_users = int((30 + 400 * _u("uac", ch)) * days / 10)
        active = int(new_users * (1.1 + 0.5 * _u("uaa", ch)))
        ke = int(new_users * (0.03 + 0.06 * _u("uak", ch)))
        by_channel.append({
            "channel": ch, "new_users": new_users, "active_users": active,
            "key_events": ke,
            "key_event_rate": _round2(ke / new_users * 100) if new_users else 0.0,
        })
    by_channel.sort(key=lambda r: r["new_users"], reverse=True)

    by_source = []
    for src, med in [("google", "organic"), ("google", "cpc"), ("(direct)", "(none)"),
                    ("bing", "organic"), ("linkedin.com", "referral"), ("newsletter", "email")]:
        new_users = int((20 + 300 * _u("uas", src, med)) * days / 12)
        ke = int(new_users * (0.03 + 0.06 * _u("uask", src, med)))
        by_source.append({
            "source": src, "medium": med, "new_users": new_users, "key_events": ke,
            "key_event_rate": _round2(ke / new_users * 100) if new_users else 0.0,
        })
    by_source.sort(key=lambda r: r["new_users"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end),
            "by_channel": by_channel, "by_source": by_source}


def _build_demographics(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    cities = [
        ("Philadelphia", "Pennsylvania"), ("Pittsburgh", "Pennsylvania"),
        ("Allentown", "Pennsylvania"), ("Cherry Hill", "New Jersey"),
        ("Wilmington", "Delaware"), ("Baltimore", "Maryland"),
        ("Newark", "New Jersey"), ("Reading", "Pennsylvania"),
    ]
    by_city = []
    region_agg: dict[str, dict] = {}
    for city, region in cities:
        users = int((40 + 500 * _u("city", city)) * days / 12)
        sessions = int(users * (1.1 + 0.4 * _u("citys", city)))
        ke = int(users * (0.03 + 0.05 * _u("cityk", city)))
        by_city.append({"city": city, "region": region, "country": "United States",
                        "users": users, "sessions": sessions, "key_events": ke})
        ra = region_agg.setdefault(region, {"region": region, "users": 0, "sessions": 0, "key_events": 0})
        ra["users"] += users
        ra["sessions"] += sessions
        ra["key_events"] += ke
    by_city.sort(key=lambda r: r["users"], reverse=True)
    by_region = sorted(region_agg.values(), key=lambda r: r["users"], reverse=True)

    by_age = []
    for bracket, share in (("18-24", 0.09), ("25-34", 0.24), ("35-44", 0.27),
                          ("45-54", 0.21), ("55-64", 0.13), ("65+", 0.06)):
        users = int(6400 * share * days / 30 * _jit((bracket, "age")))
        by_age.append({"age_bracket": bracket, "users": users,
                      "key_events": int(users * (0.03 + 0.05 * _u("agek", bracket)))})

    by_gender = []
    for gender, share in (("female", 0.54), ("male", 0.44), ("unknown", 0.02)):
        users = int(6400 * share * days / 30 * _jit((gender, "gen")))
        by_gender.append({"gender": gender, "users": users,
                         "key_events": int(users * (0.03 + 0.05 * _u("genk", gender)))})

    return {"client": "demo", "date_range": _date_range(start, end),
            "by_city": by_city, "by_age": by_age, "by_gender": by_gender, "by_region": by_region}


def _build_conversion_events(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    events = ["form_submit", "generate_lead", "phone_call_click", "book_appointment",
              "form_start", "file_download", "newsletter_signup"]
    rows = []
    for ev in events:
        count = int((10 + 220 * _u("conv", ev)) * days / 10)
        users = int(count * (0.6 + 0.3 * _u("convu", ev)))
        rows.append({
            "event_name": ev, "event_count": count, "total_users": users,
            "event_count_per_user": _round2(count / users) if users else 0.0,
        })
    rows.sort(key=lambda r: r["event_count"], reverse=True)
    count_map = {r["event_name"]: r["event_count"] for r in rows}
    funnel = [
        {"step": "Form start", "event": "form_start", "count": count_map.get("form_start", 0)},
        {"step": "Form submit", "event": "form_submit", "count": count_map.get("form_submit", 0)},
        {"step": "Lead", "event": "generate_lead", "count": count_map.get("generate_lead", 0)},
    ]
    return {"client": "demo", "date_range": _date_range(start, end), "rows": rows, "funnel": funnel}


def _build_active_key_events(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = [{"event_name": ev, "key_events": int((5 + 180 * _u("ake", ev)) * days / 10)}
            for ev in _KEY_EVENTS]
    rows.sort(key=lambda r: r["key_events"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end),
            "event_names": [r["event_name"] for r in rows], "events": rows}


def _build_key_events_by_source(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    sources = [("google", "organic"), ("google", "cpc"), ("(direct)", "(none)"),
               ("bing", "organic"), ("linkedin.com", "referral")]
    events = ["form_submit", "generate_lead", "phone_call_click", "page_view", "session_start"]
    ga4_key = {"form_submit", "generate_lead", "phone_call_click"}
    by_source_events = []
    catalog: dict[str, dict] = {}
    for src, med in sources:
        for ev in events:
            count = int((5 + 300 * _u("kes", src, med, ev)) * days / 10)
            ke = int(count * (0.4 + 0.4 * _u("kesk", src, med, ev))) if ev in ga4_key else 0
            by_source_events.append({"source": src, "medium": med, "event_name": ev,
                                    "event_count": count, "key_events": ke})
            c = catalog.setdefault(ev, {"event_name": ev, "event_count": 0, "key_events": 0})
            c["event_count"] += count
            c["key_events"] += ke
    events_out = sorted(catalog.values(), key=lambda r: r["event_count"], reverse=True)
    return {"client": "demo", "by_source_events": by_source_events, "events": events_out}


def _build_page_key_events(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    events = ["form_submit", "generate_lead", "phone_call_click", "page_view"]
    ga4_key = {"form_submit", "generate_lead", "phone_call_click"}
    rows = []
    catalog: dict[str, dict] = {}
    for path, _topic, _group in _PAGES:
        for ev in events:
            count = int((4 + 260 * _u("pke", path, ev)) * days / 10)
            ke = int(count * (0.4 + 0.4 * _u("pkek", path, ev))) if ev in ga4_key else 0
            rows.append({"page_path": path, "event_name": ev, "event_count": count, "key_events": ke})
            c = catalog.setdefault(ev, {"event_name": ev, "event_count": 0, "key_events": 0})
            c["event_count"] += count
            c["key_events"] += ke
    events_out = sorted(catalog.values(), key=lambda r: r["event_count"], reverse=True)
    return {"client": "demo", "rows": rows, "events": events_out}


def _build_landing_page_events(payload: dict) -> dict:
    out = _build_page_key_events(payload)
    start, end = _dates(payload)
    out["date_range"] = _date_range(start, end)
    return out


# --- Builders: Campaign explorers ---------------------------------------------

def _explorer_metrics(seed: Any, days: int, base_spend: float, cpc: float, ctr: float,
                     conv_rate: float) -> dict:
    spend = _round2(base_spend * days * _jit((seed, "es")))
    eff_cpc = cpc * _jit((seed, "ec"), 0.85, 1.15)
    clicks = max(1, round(spend / max(eff_cpc, 0.5)))
    impressions = round(clicks / max(ctr, 0.001))
    conv = round(clicks * conv_rate * _jit((seed, "ev"), 0.7, 1.3), 1)
    return {
        "spend": spend, "impressions": int(impressions), "clicks": int(clicks),
        "conversions": float(conv), "conversion_value": _round2(conv * (150 + 120 * _u(seed, "vv"))),
    }


def _build_google_ads_explorer(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    for cid, cname in _GOOGLE_CAMPAIGNS:
        n_ads = 2 + int(2 * _u(cid, "nads"))
        for i in range(n_ads):
            ad_id = f"{cid}{i:02d}"
            m = _explorer_metrics(ad_id, days, 22.0 + 60 * _u(ad_id, "b"), 2.6, 0.045, 0.05)
            rows.append({
                "source": "google",
                "campaign_id": cid, "campaign_name": cname,
                "ad_group_id": f"{cid}A{i}", "ad_group_name": f"{cname.split('|')[-1].strip()} — Group {i+1}",
                "ad_id": ad_id, "ad_label": f"RSA {i+1}",
                "headline_1": "Northwind Health", "headline_2": "Occupational & Urgent Care",
                "headline_3": "Book Online Today", "description_1": "Trusted employer health services.",
                "description_2": "Same-day appointments available.",
                "headlines": ["Northwind Health", "Occupational & Urgent Care", "Book Online Today"],
                "descriptions": ["Trusted employer health services.", "Same-day appointments available."],
                "image_ad_name": None, "ad_name": f"RSA {i+1}",
                "final_url": f"{_SITE_URL}services", "ad_type": "RESPONSIVE_SEARCH_AD",
                **m,
            })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _build_microsoft_explorer(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    for cid, cname in _MICROSOFT_CAMPAIGNS:
        n_ads = 2 + int(2 * _u(cid, "mads"))
        for i in range(n_ads):
            ad_id = f"{cid}{i:02d}"
            m = _explorer_metrics(ad_id, days, 16.0 + 40 * _u(ad_id, "b"), 2.2, 0.038, 0.045)
            rows.append({
                "campaign_id": cid, "campaign_name": cname,
                "ad_group_id": f"{cid}A{i}", "ad_group_name": f"{cname.split('|')[-1].strip()} — Group {i+1}",
                "ad_id": ad_id, "ad_type": "ResponsiveSearch",
                "ad_title": "Northwind Health — Occupational Care",
                "title_part_1": "Northwind Health", "title_part_2": "Occupational Care",
                "title_part_3": "Book Today",
                "description_1": "Employer health, urgent care & telehealth.",
                "description_2": "Same-day appointments.",
                "headlines": ["Northwind Health", "Occupational Care", "Book Today"],
                "descriptions": ["Employer health, urgent care & telehealth.", "Same-day appointments."],
                "path_1": "services",
                **m,
            })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _build_google_ads_keywords(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    for kw, match, adgroup, campaign in _KEYWORDS:
        m = _explorer_metrics(kw, days, 12.0 + 55 * _u(kw, "b"), 3.0, 0.05, 0.055)
        clicks, impressions, spend = m["clicks"], m["impressions"], m["spend"]
        rows.append({
            "keyword_text": kw, "match_type": match, "ad_group_name": adgroup,
            "campaign_name": campaign,
            "spend": spend, "impressions": impressions, "clicks": clicks,
            "conversions": m["conversions"], "conversion_value": m["conversion_value"],
            "ctr": _round2(clicks / impressions * 100) if impressions else 0.0,
            "avg_cpc": _round2(spend / clicks) if clicks else 0.0,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _build_linkedin_explorer(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    media_cycle = ["SINGLE_IMAGE", "VIDEO", "CAROUSEL", "SINGLE_IMAGE", "DOCUMENT"]
    for idx, (crid, name) in enumerate(_LINKEDIN_CREATIVES):
        m = _explorer_metrics(crid, days, 30.0 + 55 * _u(crid, "b"), 9.0, 0.006, 0.03)
        media = media_cycle[idx % len(media_cycle)]
        rows.append({
            "creative_id": crid, "creative_name": name, "media_type": media,
            "thumbnail_url": None, "image_url": None,
            "video_url": f"https://media.example/{crid}.mp4" if media == "VIDEO" else None,
            "campaign_group_name": "NWH | Employer Solutions",
            **m,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end), "level": "creative",
            "row_count": len(rows), "rows": rows}


def _build_meta_explorer(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    rows = []
    media_cycle = ["IMAGE", "VIDEO", "CAROUSEL", "IMAGE", "VIDEO"]
    for idx, (adid, name) in enumerate(_META_ADS):
        m = _explorer_metrics(adid, days, 24.0 + 45 * _u(adid, "b"), 1.4, 0.018, 0.037)
        rows.append({
            "ad_id": adid, "ad_name": name, "campaign_name": "NWH | Prospecting",
            "media_type": media_cycle[idx % len(media_cycle)],
            "thumbnail_url": None, "image_url": None,
            **m,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return {"client": "demo", "date_range": _date_range(start, end), "row_count": len(rows), "rows": rows}


def _verified_events_catalog() -> list[str]:
    return ["form_submit", "generate_lead", "phone_call_click"]


def _build_meta_verified(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    events = _verified_events_catalog()
    by_ad_id: dict[str, dict] = {}
    by_ad_id_event: dict[str, dict] = {}
    for adid, name in _META_ADS:
        total = int((3 + 40 * _u("mv", adid)) * days / 10)
        by_ad_id[adid] = {"ad_name": name, "verified_conversions": total}
        by_ad_id_event[adid] = {ev: int(total * (0.2 + 0.5 * _u("mve", adid, ev))) for ev in events}
    return {"client": "demo", "date_range": _date_range(start, end),
            "row_count": len(by_ad_id), "by_ad_id": by_ad_id,
            "events": events, "by_ad_id_event": by_ad_id_event}


def _build_google_verified(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    events = _verified_events_catalog()
    by_campaign_id: dict[str, dict] = {}
    by_campaign_id_event: dict[str, dict] = {}
    for cid, name in _GOOGLE_CAMPAIGNS:
        total = int((4 + 55 * _u("gv", cid)) * days / 10)
        by_campaign_id[cid] = {"campaign_name": name, "verified_conversions": total}
        by_campaign_id_event[cid] = {ev: int(total * (0.2 + 0.5 * _u("gve", cid, ev))) for ev in events}
    return {"client": "demo", "date_range": _date_range(start, end),
            "row_count": len(by_campaign_id), "by_campaign_id": by_campaign_id,
            "events": events, "by_campaign_id_event": by_campaign_id_event}


def _build_linkedin_verified(payload: dict) -> dict:
    start, end = _dates(payload)
    days = max(1, (end - start).days + 1)
    events = _verified_events_catalog()
    groups = ["NWH | Employer Solutions", "NWH | Thought Leadership"]
    by_group: dict[str, int] = {}
    by_group_event: dict[str, dict] = {}
    for g in groups:
        total = int((3 + 30 * _u("lv", g)) * days / 10)
        by_group[g] = total
        by_group_event[g] = {ev: int(total * (0.2 + 0.5 * _u("lve", g, ev))) for ev in events}
    return {"client": "demo", "date_range": _date_range(start, end),
            "events": events, "by_group_name": by_group, "by_group_name_event": by_group_event}


# --- Builders: Search Console / SEMrush / PageSpeed ---------------------------

def _build_gsc_summary(payload: dict) -> dict:
    start, end = _dates(payload)
    daily = []
    for d in _each_day(start, end):
        impressions = int((3500 + 4500 * _u("gscd", d)) * _season(d))
        ctr = 0.03 + 0.04 * _u("gscctr", d)
        clicks = int(impressions * ctr)
        daily.append({
            "date": d.isoformat(), "clicks": clicks, "impressions": impressions,
            "ctr": _round2(ctr * 100), "avg_position": _round2(6 + 8 * _u("gscpos", d)),
        })
    tot_clicks = sum(r["clicks"] for r in daily)
    tot_impr = sum(r["impressions"] for r in daily)
    avg_pos = _round2(sum(r["avg_position"] for r in daily) / len(daily)) if daily else 0.0
    prior_clicks = int(tot_clicks * (0.85 + 0.2 * _u("gscprior")))
    prior_impr = int(tot_impr * (0.85 + 0.2 * _u("gscpriori")))
    kpis = {
        "clicks": tot_clicks, "impressions": tot_impr,
        "ctr": _round2(tot_clicks / tot_impr * 100) if tot_impr else 0.0,
        "avg_position": avg_pos,
        "prior_clicks": prior_clicks, "prior_impressions": prior_impr,
        "prior_ctr": _round2(prior_clicks / prior_impr * 100) if prior_impr else 0.0,
        "prior_avg_position": _round2(avg_pos * (1.0 + 0.1 * _u("gscpp"))),
    }
    queries = [
        "northwind health", "occupational health near me", "urgent care philadelphia",
        "employer telehealth services", "pre employment physical", "workplace flu clinic",
        "walk in clinic", "virtual doctor visit", "workforce wellness program", "same day physical",
    ]
    top_queries = []
    for q in queries:
        impressions = int(400 + 6000 * _u("gscq", q))
        ctr = 0.02 + 0.06 * _u("gscqc", q)
        clicks = int(impressions * ctr)
        pos = _round2(1 + 20 * _u("gscqp", q))
        top_queries.append({"query": q, "clicks": clicks, "impressions": impressions,
                           "ctr": _round2(ctr * 100), "avg_position": pos,
                           "prior_avg_position": _round2(pos * (1 + 0.12 * _u("gscqpp", q)))})
    top_queries.sort(key=lambda r: r["clicks"], reverse=True)
    top_pages = []
    for path, _topic, _group in _PAGES:
        impressions = int(300 + 5000 * _u("gscpg", path))
        ctr = 0.02 + 0.06 * _u("gscpgc", path)
        clicks = int(impressions * ctr)
        top_pages.append({"page": f"{_SITE_URL.rstrip('/')}{path}", "clicks": clicks,
                         "impressions": impressions, "ctr": _round2(ctr * 100),
                         "avg_position": _round2(1 + 18 * _u("gscpgp", path))})
    top_pages.sort(key=lambda r: r["clicks"], reverse=True)
    return {"kpis": kpis, "daily": daily, "top_queries": top_queries, "top_pages": top_pages}


def _build_gsc_keyword_matches(payload: dict) -> list[dict]:
    """Rows shape (a LIST) for branded/target keyword matching."""
    start, end = _dates(payload)
    terms = payload.get("terms") or []
    base_terms = [t for t in terms] if isinstance(terms, list) else []
    queries = base_terms or ["northwind health", "northwind urgent care", "northwind telehealth"]
    rows = []
    for q in queries:
        impressions = int(200 + 4000 * _u("kmq", q))
        ctr = 0.03 + 0.06 * _u("kmqc", q)
        clicks = int(impressions * ctr)
        rows.append({"query": q, "clicks": clicks, "impressions": impressions,
                    "ctr": _round2(ctr * 100), "position": _round2(1 + 6 * _u("kmqp", q))})
    rows.sort(key=lambda r: r["clicks"], reverse=True)
    return rows


def _build_gsc_keyword_weekly(payload: dict) -> list[dict]:
    """Weekly position trend (a LIST), ~13 weeks."""
    start, end = _dates(payload)
    out = []
    week_start = end - timedelta(days=7 * 13)
    for w in range(13):
        wk = week_start + timedelta(days=7 * w)
        out.append({"week": wk.isoformat(), "position": _round2(2 + 4 * _u("wk", w)),
                   "clicks": int(80 + 400 * _u("wkc", w)),
                   "impressions": int(2000 + 8000 * _u("wki", w))})
    return out


def _build_semrush_summary(payload: dict) -> dict:
    organic_keywords = 1240 + int(600 * _u("sem_ok"))
    organic_traffic = 8600 + int(5000 * _u("sem_ot"))
    authority = 38 + int(20 * _u("sem_as"))
    total_backlinks = 42000 + int(30000 * _u("sem_bl"))
    referring_domains = 780 + int(400 * _u("sem_rd"))
    keywords = []
    kw_terms = [
        "occupational health", "urgent care near me", "telehealth for employers",
        "pre employment physical", "workplace wellness", "walk in clinic",
        "employee flu shots", "virtual urgent care", "drug screening services",
        "dot physical exam",
    ]
    for kw in kw_terms:
        pos = 1 + int(40 * _u("semk", kw))
        keywords.append({
            "keyword": kw, "position": pos,
            "search_volume": int(200 + 8000 * _u("semv", kw)),
            "cpc": _round2(1 + 12 * _u("semc", kw)),
            "url": f"{_SITE_URL.rstrip('/')}/services",
            "traffic_pct": _round2(_u("semt", kw) * 20),
            "ai_overview": 1 if _u("semai", kw) > 0.6 else 0,
            "ai_overview_cited": 1 if _u("semaic", kw) > 0.8 else 0,
        })
    keywords.sort(key=lambda r: r["traffic_pct"], reverse=True)
    buckets = [("1–3", 1, 3), ("4–10", 4, 10), ("11–20", 11, 20), ("21–50", 21, 50), ("51+", 51, 9999)]
    dist = []
    for label, lo, hi in buckets:
        dist.append({"bucket": label, "count": sum(1 for k in keywords if lo <= k["position"] <= hi)})
    # ~12-week weekly series
    series = []
    end = date.today() - timedelta(days=1)
    for w in range(12):
        d = end - timedelta(days=7 * (11 - w))
        series.append({
            "date": d.isoformat(),
            "organic_traffic": int(organic_traffic * (0.85 + 0.3 * _u("semst", w))),
            "organic_keywords": int(organic_keywords * (0.9 + 0.2 * _u("semsk", w))),
            "authority_score": authority,
            "total_backlinks": int(total_backlinks * (0.95 + 0.1 * _u("semsb", w))),
            "referring_domains": int(referring_domains * (0.95 + 0.1 * _u("semsr", w))),
            "ai_overview_keywords": int(30 + 60 * _u("semsa", w)),
            "ai_overview_cited": int(5 + 25 * _u("semsc", w)),
        })
    return {
        "domain": _DOMAIN, "database": "us",
        "overview": {
            "domain": _DOMAIN, "database": "us",
            "semrush_rank": 240000 + int(80000 * _u("sem_rank")),
            "organic_keywords": organic_keywords, "organic_traffic": organic_traffic,
            "organic_cost": _round2(organic_traffic * 1.8), "paid_keywords": 60 + int(80 * _u("sem_pk")),
            "paid_traffic": 400 + int(600 * _u("sem_pt")), "authority_score": authority, "error": None,
        },
        "keywords": keywords,
        "backlinks": {
            "domain": _DOMAIN, "authority_score": authority, "total_backlinks": total_backlinks,
            "referring_domains": referring_domains, "referring_ips": int(referring_domains * 0.9),
            "dofollow": int(total_backlinks * 0.72), "nofollow": int(total_backlinks * 0.28), "error": None,
        },
        "ai_overview": {"keywords_with_aio": 42 + int(40 * _u("sem_aio")),
                       "keywords_cited": 8 + int(20 * _u("sem_aioc"))},
        "series": series, "position_distribution": dist,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }


def _build_pagespeed_summary(payload: dict, strategy: str) -> dict:
    end = date.today() - timedelta(days=1)
    is_mobile = strategy == "mobile"

    def _scores(seed: Any) -> dict:
        perf = (58 if is_mobile else 82) + int(15 * _u(seed, "perf"))
        return {
            "performance": min(99, perf),
            "accessibility": 90 + int(9 * _u(seed, "a11y")),
            "best_practices": 88 + int(11 * _u(seed, "bp")),
            "seo": 92 + int(8 * _u(seed, "seo")),
            "lcp_ms": _round2((3200 if is_mobile else 1900) + 800 * _u(seed, "lcp")),
            "cls": _round2(0.02 + 0.08 * _u(seed, "cls")),
            "tbt_ms": _round2((260 if is_mobile else 90) + 120 * _u(seed, "tbt")),
            "fcp_ms": _round2((2200 if is_mobile else 1200) + 500 * _u(seed, "fcp")),
            "speed_index_ms": _round2((4200 if is_mobile else 2400) + 900 * _u(seed, "si")),
            "tti_ms": _round2((5200 if is_mobile else 2800) + 1000 * _u(seed, "tti")),
        }

    history = []
    for i in range(30):
        d = end - timedelta(days=29 - i)
        s = _scores((strategy, d))
        history.append({"metric_date": d.isoformat(), **s})

    latest = _scores((strategy, end))
    out = {
        "client_key": "demo", "url": _SITE_URL, "strategy": strategy,
        "metric_date": end.isoformat(),
        "crux_lcp_ms": int(latest["lcp_ms"] * 1.05),
        "crux_cls": _round2(latest["cls"] * 1.1),
        "crux_inp_ms": int((200 if is_mobile else 120) + 80 * _u("inp", strategy)),
        "error": None,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "history": history,
    }
    out.update(latest)
    return out


# --- Dispatch ------------------------------------------------------------------

# Exact-match cache-source suffixes → builder. The suffix is the cache source
# with the client slug prefix removed (e.g. "demo.pages.top" → "pages.top").
_BUILDERS = {
    "summary": _build_summary,
    "marketing": _build_marketing,
    "health": _build_health,
    "marketing.health": _build_health,
    "pages.traffic_acquisition": _build_traffic_acquisition,
    "ai_traffic.daily": _build_ai_traffic_daily,
    "pages.top": _build_pages_top,
    "pages.sources": _build_pages_sources,
    "pages.device_split": _build_device_split,
    "pages.landing": _build_landing_pages,
    "pages.landing_events": _build_landing_page_events,
    "pages.top_key_events": _build_page_key_events,
    "pages.traffic_key_events": _build_key_events_by_source,
    "analytics.user_acq_key_events": _build_key_events_by_source,
    "analytics.conversions": _build_conversion_events,
    "analytics.user_acquisition": _build_user_acquisition,
    "analytics.demographics": _build_demographics,
    "ga4.active_key_events": _build_active_key_events,
    "explorer.google_ads": _build_google_ads_explorer,
    "explorer.microsoft_ads": _build_microsoft_explorer,
    "explorer.google_ads_keywords": _build_google_ads_keywords,
    "explorer.linkedin": _build_linkedin_explorer,
    "explorer.meta": _build_meta_explorer,
    "explorer.meta_verified": _build_meta_verified,
    "explorer.google_verified": _build_google_verified,
    "explorer.linkedin_verified": _build_linkedin_verified,
    "gsc.summary": _build_gsc_summary,
    "gsc.keyword_matches": _build_gsc_keyword_matches,
    "gsc.keyword_weekly_trend": _build_gsc_keyword_weekly,
    "semrush.summary": _build_semrush_summary,
}

# Suffixes that return a LIST rather than a dict (so the empty fallback matches).
_LIST_KEYS = {"gsc.keyword_matches", "gsc.keyword_weekly_trend"}


def generate(key: str, payload: dict | None = None) -> Any:
    """Return synthetic data for a demo cache-source suffix.

    ``key`` is the cache source with the client slug removed (e.g. ``summary``,
    ``pages.top``, ``pagespeed.summary.desktop``). Unknown keys return an
    empty-but-valid response (``{}`` or ``[]``) so a newly added panel degrades
    gracefully instead of erroring while its sample is written.
    """
    key = (key or "").strip()
    payload = payload or {}

    # PageSpeed carries the strategy as a trailing segment: pagespeed.summary.<strat>
    if key.startswith("pagespeed.summary"):
        strat = key.rsplit(".", 1)[-1] if key.count(".") >= 2 else "desktop"
        if strat not in ("desktop", "mobile"):
            strat = "desktop"
        return _build_pagespeed_summary(payload, strat)

    builder = _BUILDERS.get(key)
    if builder is not None:
        return builder(payload)

    # Unknown key: safe, valid-shaped default.
    return [] if key in _LIST_KEYS else {}
