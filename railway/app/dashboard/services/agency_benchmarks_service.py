"""Agency benchmarks: "what does a typical client in this industry see?"

Every other agency-wide view answers *per client* questions ("is Nixon pacing
its budget"). This one answers the question a client asks on a QBR call — "is a
2.1% search CTR good for a company like us?" — by bucketing accounts with the
industry tags from ``client_industries`` and reporting the distribution of each
metric inside the bucket. An account can carry several tags (a clinical-workflow
SaaS vendor is both health and software); it then appears in each of those
buckets, and once in the agency-wide row.

Three deliberate choices, because a benchmark that lies is worse than no
benchmark:

* **Median is the headline, not the mean.** With ten clients per bucket, one
  enterprise spender drags a mean far off what a typical account experiences.
  The mean ships alongside it (and min/max) so a skewed bucket is visible.
* **Ratios are averaged per client, not pooled.** CTR is each client's own
  clicks/impressions, then the median of those — so the benchmark describes a
  *client*, not the agency's blended book, which the biggest account would
  otherwise dominate.
* **A client with no denominator is excluded, not counted as zero.** No paid
  impressions in the window means "no CTR", not "0% CTR". Every cell therefore
  carries its own ``n`` — sample sizes differ metric by metric, and a bucket of
  two is labelled thin rather than quietly presented as a benchmark.

Cost: effectively zero extra BigQuery. The per-client paid-media and GA4 reads
go through ``agency_trends_service.cached_client_summary`` /
``cached_client_sessions`` over ``overview_fetch_bounds`` — the exact windows
and cache keys the Health page already warms — so a benchmark refresh normally
reads Postgres cache only. The one genuinely new read is the LinkedIn follower
total (one row per client), cached here on the same TTL.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any

import client_dashboard_config
import client_industries
import dashboard_registry
import db_cache
from dashboard.services import agency_trends_service
from dashboard.utils.dates import mtd_calendar_bounds

LOGGER = logging.getLogger(__name__)

_CACHE_SOURCE = "agency.benchmarks"
_CACHE_TTL_SECONDS = 900
_CLIENT_TTL_SECONDS = 900
_MAX_WORKERS = 8
# Buckets below this many contributing clients are flagged "thin" in the payload
# so the page can render them as directional rather than as a benchmark.
THIN_SAMPLE_MAX = 3

# Aggregation windows. Both are slices of the window
# agency_trends_service.overview_fetch_bounds already fetches — adding a longer
# one (90d) would mean a wider, uncached BigQuery read for every client, so it
# is deliberately not offered here.
WINDOWS = ("month", "last_30d")
_TRAILING_DAYS = 30


def window_bounds(today: date, window: str) -> tuple[date, date, str]:
    """(start, end, label) for a benchmark window.

    ``last_30d`` ends yesterday to match the sessions fetch window (platform
    syncs lag a day); ``month`` is month-to-date through today.
    """
    if window == "last_30d":
        end = today - timedelta(days=1)
        return end - timedelta(days=_TRAILING_DAYS - 1), end, "last 30 days"
    return today.replace(day=1), today, "month to date"


# ── Metric registry ──────────────────────────────────────────────────────────
# One entry per benchmarkable number. ``compute`` takes a client's raw totals
# and returns the client's own value (or None to exclude it from that metric's
# sample). ``direction`` tells the page which end of the range is good; it is
# "none" for size metrics, where bigger is neither better nor worse — it just
# describes the account.


def _ratio(numerator: float, denominator: float, *, scale: float = 1.0) -> float | None:
    if not denominator:
        return None
    return (numerator / denominator) * scale


def _ctr(t: dict[str, float]) -> float | None:
    return _ratio(t["clicks"], t["impressions"], scale=100.0)


def _cpc(t: dict[str, float]) -> float | None:
    return _ratio(t["spend"], t["clicks"])


def _cpa(t: dict[str, float]) -> float | None:
    return _ratio(t["spend"], t["conversions"])


def _cvr(t: dict[str, float]) -> float | None:
    return _ratio(t["conversions"], t["clicks"], scale=100.0)


def _positive(key: str):
    """Metric reader for a raw total: the value itself, or None when it is zero.

    Zero spend/sessions in the window means the client had nothing running (or
    nothing synced), which is an absent observation — averaging it in would pull
    every benchmark toward zero.
    """

    def _read(t: dict[str, float]) -> float | None:
        value = t.get(key) or 0.0
        return float(value) if value > 0 else None

    return _read


METRICS: tuple[dict[str, Any], ...] = (
    {
        "key": "sessions",
        "label": "Website sessions",
        "format": "number",
        "direction": "up",
        "group": "Website",
        "hint": "GA4 sessions in the window — all traffic, not just paid.",
        "compute": _positive("sessions"),
        "platform_scoped": False,
    },
    {
        "key": "ctr",
        "label": "Paid CTR",
        "format": "percent",
        "direction": "up",
        "group": "Paid media",
        "hint": "Clicks ÷ impressions for the selected platform.",
        "compute": _ctr,
        "platform_scoped": True,
    },
    {
        "key": "cpc",
        "label": "Paid CPC",
        "format": "currency2",
        "direction": "down",
        "group": "Paid media",
        "hint": "Spend ÷ clicks.",
        "compute": _cpc,
        "platform_scoped": True,
    },
    {
        "key": "cvr",
        "label": "Paid conversion rate",
        "format": "percent",
        "direction": "up",
        "group": "Paid media",
        "hint": "Conversions ÷ clicks, as counted by each platform.",
        "compute": _cvr,
        "platform_scoped": True,
    },
    {
        "key": "cpa",
        "label": "Paid cost per conversion",
        "format": "currency",
        "direction": "down",
        "group": "Paid media",
        "hint": "Spend ÷ conversions.",
        "compute": _cpa,
        "platform_scoped": True,
    },
    {
        "key": "spend",
        "label": "Paid spend",
        "format": "currency",
        "direction": "none",
        "group": "Paid media",
        "hint": "Media spend in the window — a size marker, not a score.",
        "compute": _positive("spend"),
        "platform_scoped": True,
    },
    {
        "key": "impressions",
        "label": "Paid impressions",
        "format": "number",
        "direction": "none",
        "group": "Paid media",
        "hint": "Impressions served in the window.",
        "compute": _positive("impressions"),
        "platform_scoped": True,
    },
    {
        "key": "linkedin_followers",
        "label": "LinkedIn followers",
        "format": "number",
        "direction": "up",
        "group": "Social",
        "hint": "Latest lifetime company-page follower count (not window-scoped).",
        "compute": _positive("linkedin_followers"),
        "platform_scoped": False,
    },
)

_METRIC_KEYS: tuple[str, ...] = tuple(m["key"] for m in METRICS)


def metric_catalog() -> list[dict[str, Any]]:
    """The metric registry as JSON-safe dicts (drops the ``compute`` callable)."""
    return [{k: v for k, v in m.items() if k != "compute"} for m in METRICS]


# ── Platform filter ──────────────────────────────────────────────────────────
# vw_paid_media_daily labels rows 'paid_google' / 'paid_linkedin' / 'paid_meta' /
# 'paid_microsoft'; the filter keys are the bare channel names.
_PLATFORM_LABELS = {
    "google": "Google Ads",
    "linkedin": "LinkedIn Ads",
    "meta": "Meta Ads",
    "microsoft": "Microsoft Ads",
}


def _platform_key(source_platform: str) -> str:
    s = str(source_platform or "").lower()
    return s[5:] if s.startswith("paid_") else s


def _platform_label(key: str) -> str:
    return _PLATFORM_LABELS.get(key, key.replace("_", " ").title() or "Unknown")


# ── Per-client collection ────────────────────────────────────────────────────


def _empty_totals() -> dict[str, float]:
    return {
        "spend": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "conversions": 0.0,
        "sessions": 0.0,
        "linkedin_followers": 0.0,
    }


def _paid_totals_by_platform(
    summary: dict[str, Any], *, start: date, end: date
) -> dict[str, dict[str, float]]:
    """{platform_key: totals} from a cached ``fetch_summary`` payload.

    Sums the daily rows rather than the payload's own ``summary`` block: the
    cached read spans a wider window than any single benchmark window, so the
    totals have to be re-sliced to the requested dates here.
    """
    out: dict[str, dict[str, float]] = {}
    for row in (summary or {}).get("daily") or []:
        raw_date = str(row.get("date") or "")[:10]
        try:
            day = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= day <= end):
            continue
        key = _platform_key(row.get("source") or "")
        if not key:
            continue
        agg = out.setdefault(key, _empty_totals())
        agg["spend"] += float(row.get("spend") or 0.0)
        agg["impressions"] += float(row.get("impressions") or 0.0)
        agg["clicks"] += float(row.get("clicks") or 0.0)
        agg["conversions"] += float(row.get("conversions") or 0.0)
    return out


def _sessions_total(sessions: dict[str, Any], *, start: date, end: date) -> float:
    total = 0.0
    for row in (sessions or {}).get("daily") or []:
        raw_date = str(row.get("date") or "")[:10]
        try:
            day = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= day <= end:
            total += float(row.get("sessions") or 0.0)
    return total


def _cached_linkedin_followers(slug: str) -> int | None:
    """Latest follower total for one client, cached on the benchmark TTL.

    Cached under a date-only key so the extra read happens at most once a day
    per client — the number moves slowly and is not window-scoped.
    """
    payload = {"day": date.today().isoformat()}
    source = f"{slug}.benchmarks.li_followers"
    hit = db_cache.get_cached(source, payload)
    if hit is not None:
        value = (hit.response_json or {}).get("followers")
        return int(value) if value else None

    import linkedin_organic_report_service

    followers = linkedin_organic_report_service.fetch_latest_followers(slug)
    try:
        db_cache.put_cached(
            source, payload, response_json={"followers": followers},
            row_count=0, ttl_seconds=_CLIENT_TTL_SECONDS,
        )
    except Exception:
        pass
    return followers


def collect_client_metrics(
    *,
    slug: str,
    label: str,
    project_id: str | None,
    dataset_id: str,
    fetch_bounds: tuple[date, date, date, date],
    start: date,
    end: date,
) -> dict[str, Any]:
    """Every raw total one client contributes, keyed by platform.

    Returns ``{"client_slug", "label", "platforms": {key: totals}, "totals": …}``
    where ``totals`` is the all-platforms roll-up plus the non-paid numbers
    (sessions, followers). Read failures degrade to missing numbers for that
    client — one unreachable mart must not empty the whole benchmark.
    """
    spend_start, spend_end, sessions_start, sessions_end = fetch_bounds
    platforms: dict[str, dict[str, float]] = {}
    totals = _empty_totals()

    if project_id:
        try:
            summary = agency_trends_service.cached_client_summary(
                slug=slug, project_id=project_id, dataset_id=dataset_id,
                start=spend_start, end=spend_end,
            )
            platforms = _paid_totals_by_platform(summary, start=start, end=end)
            for agg in platforms.values():
                for key in ("spend", "impressions", "clicks", "conversions"):
                    totals[key] += agg[key]
        except Exception:
            LOGGER.exception("Benchmarks: paid read failed for %s", slug)
        try:
            sessions = agency_trends_service.cached_client_sessions(
                slug=slug, project_id=project_id, dataset_id=dataset_id,
                start=sessions_start, end=sessions_end,
            )
            # The sessions fetch only spans the trailing 30 days; clamp the
            # requested window to it so an early-month MTD slice stays honest.
            totals["sessions"] = _sessions_total(
                sessions, start=max(start, sessions_start), end=min(end, sessions_end)
            )
        except Exception:
            LOGGER.exception("Benchmarks: sessions read failed for %s", slug)

    try:
        followers = _cached_linkedin_followers(slug)
        totals["linkedin_followers"] = float(followers or 0.0)
    except Exception:
        LOGGER.exception("Benchmarks: LinkedIn follower read failed for %s", slug)

    return {
        "client_slug": slug,
        "label": label,
        "platforms": platforms,
        "totals": totals,
    }


# ── Rollup ───────────────────────────────────────────────────────────────────


def _client_values(
    record: dict[str, Any], *, platform: str
) -> dict[str, float | None]:
    """One client's value for every metric, under the selected platform filter.

    Platform-scoped metrics read that platform's totals only (None when the
    client didn't run on it); the rest always read the client-wide totals.
    """
    all_totals = record["totals"]
    if platform == "all":
        paid_totals = all_totals
    else:
        paid_totals = record["platforms"].get(platform)

    values: dict[str, float | None] = {}
    for metric in METRICS:
        source = paid_totals if metric["platform_scoped"] else all_totals
        values[metric["key"]] = metric["compute"](source) if source else None
    return values


def _summarize(values: list[float]) -> dict[str, Any] | None:
    """Distribution stats for one metric inside one bucket, or None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(median(ordered), 4),
        "mean": round(sum(ordered) / len(ordered), 4),
        "min": round(ordered[0], 4),
        "max": round(ordered[-1], 4),
        "thin": len(ordered) < THIN_SAMPLE_MAX,
    }


def compute_benchmarks(
    records: list[dict[str, Any]],
    industries: dict[str, Any],
    *,
    platform: str = "all",
) -> dict[str, Any]:
    """Group per-client records into industry buckets. Pure — no I/O, so tests
    drive it with hand-built records.

    ``records`` come from :func:`collect_client_metrics`, ``industries`` is
    ``{slug: keys}`` from the registry — one key or several, since an account
    that straddles two markets is tagged with both. A multi-tagged client
    contributes to **every** bucket it carries (that is the point: it really is a
    peer of both books) but only once to the agency-wide row and once to the
    coverage count. Clients without a tag land in the agency-wide row only; that
    gap is reported as ``coverage`` so the page can nudge someone to finish
    tagging rather than silently under-report.
    """
    per_client: list[dict[str, Any]] = []
    buckets: dict[str, list[dict[str, float | None]]] = {}
    bucket_members: dict[str, list[str]] = {}
    everything: list[dict[str, float | None]] = []

    for record in sorted(records, key=lambda r: (r["label"].lower(), r["client_slug"])):
        slug = record["client_slug"]
        values = _client_values(record, platform=platform)
        keys = client_industries.normalize_many(industries.get(slug))
        per_client.append({
            "client_slug": slug,
            "label": record["label"],
            "industries": list(keys),
            "industry_labels": list(client_industries.labels_for(keys)),
            # The first key and the joined labels, for anything that wants one
            # value rather than the set (chips, one-line summaries).
            "industry": keys[0] if keys else None,
            "industry_label": client_industries.label_list(keys),
            "metrics": {k: (round(v, 4) if v is not None else None) for k, v in values.items()},
        })
        everything.append(values)
        for industry in keys:
            buckets.setdefault(industry, []).append(values)
            bucket_members.setdefault(industry, []).append(slug)

    def _rows_for(samples: list[dict[str, float | None]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in _METRIC_KEYS:
            observed = [s[key] for s in samples if s.get(key) is not None]
            out[key] = _summarize([float(v) for v in observed])
        return out

    # Keep the taxonomy's own order so the table reads the same every load, and
    # skip buckets nobody is tagged into rather than rendering empty rows.
    industry_rows = [
        {
            "key": key,
            "label": label,
            "client_count": len(buckets[key]),
            "clients": bucket_members.get(key, []),
            "metrics": _rows_for(buckets[key]),
        }
        for key, label in client_industries.choices()
        if key in buckets
    ]

    tagged = sum(1 for c in per_client if c["industries"])
    return {
        "industries": industry_rows,
        "agency": {
            "key": "__all__",
            "label": "All clients",
            "client_count": len(everything),
            "clients": [c["client_slug"] for c in per_client],
            "metrics": _rows_for(everything),
        },
        "clients": per_client,
        "coverage": {
            "total": len(per_client),
            "tagged": tagged,
            "untagged": len(per_client) - tagged,
        },
    }


def _platform_options(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """"All paid" plus every platform some client actually ran on.

    Built from the data rather than a fixed list so the filter never offers a
    channel with nothing behind it.
    """
    seen: set[str] = set()
    for record in records:
        seen.update(record["platforms"].keys())
    ordered = [k for k in _PLATFORM_LABELS if k in seen] + sorted(seen - set(_PLATFORM_LABELS))
    return [{"key": "all", "label": "All paid media"}] + [
        {"key": k, "label": _platform_label(k)} for k in ordered
    ]


def build_agency_benchmarks(
    *, window: str = "month", platform: str = "all"
) -> dict[str, Any]:
    """Full payload for the admin Benchmarks page.

    One concurrent pass over the clients (reading the Health page's warm caches),
    then a pure rollup by industry. Cached whole under ``agency.benchmarks`` so
    switching metric tabs on the page costs nothing.
    """
    if window not in WINDOWS:
        window = "month"
    platform = (platform or "all").strip().lower() or "all"

    _, _, today = mtd_calendar_bounds()
    start, end, window_label = window_bounds(today, window)
    fetch_bounds = agency_trends_service.overview_fetch_bounds(today)

    cache_key = {"today": today.isoformat(), "window": window, "platform": platform}
    hit = db_cache.get_cached(_CACHE_SOURCE, cache_key)
    if hit is not None:
        return hit.response_json

    clients_cfg = client_dashboard_config.list_budget_overview()

    def _work(cfg: dict[str, Any]) -> dict[str, Any]:
        return collect_client_metrics(
            slug=str(cfg["client_slug"]),
            label=str(cfg.get("label") or cfg["client_slug"]),
            project_id=(cfg.get("gcp_project_id") or None),
            dataset_id=(cfg.get("bq_mart_dataset_id") or "marketing_marts"),
            fetch_bounds=fetch_bounds,
            start=start,
            end=end,
        )

    records: list[dict[str, Any]] = []
    if clients_cfg:
        with ThreadPoolExecutor(max_workers=min(len(clients_cfg), _MAX_WORKERS)) as ex:
            records = list(ex.map(_work, clients_cfg))

    try:
        industries = dashboard_registry.industries_map()
    except Exception:
        LOGGER.exception("Benchmarks: industry map read failed")
        industries = {}

    result = {
        **compute_benchmarks(records, industries, platform=platform),
        "window": {
            "key": window,
            "label": window_label,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "platform": {
            "key": platform,
            "label": "All paid media" if platform == "all" else _platform_label(platform),
            "options": _platform_options(records),
        },
        "metric_catalog": metric_catalog(),
        "thin_sample_max": THIN_SAMPLE_MAX,
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        db_cache.put_cached(
            _CACHE_SOURCE, cache_key, response_json=result,
            row_count=len(result["clients"]), ttl_seconds=_CACHE_TTL_SECONDS,
        )
    except Exception:
        pass
    return result
