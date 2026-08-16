"""Ranked, explained observations about a client's paid media — the "so what".

Every other service here answers *what* a number is. This one answers whether it
is worth mentioning, and why it moved. It takes the aggregates the dashboard
already loads and emits a short ranked list of findings, each one a sentence a
person could say on a call:

    Paid conversions up 38% — Google Ads drove 61% of the gain.
    CPA is 34% over its $120 target.
    Meta Ads last synced 4 days ago — this window is incomplete.

Deliberately **rules-based, and deliberately pure**. Rules-based because the
facts have to be right before anything can phrase them nicely: this module
produces the finding structs, and anything that later wants to write prose (a
model, a digest email) consumes those structs rather than the raw warehouse, so
it cannot invent a number that was never computed. Pure because the caller does
the I/O and hands over plain dicts — the same split ``kpi_registry`` uses — which
makes every judgement below unit-testable without a warehouse.

Three judgements are what stop this from being noise:

* **A percentage needs a base.** Conversions going 1 → 4 is a 300% rise and means
  nothing. Every movement must clear an absolute floor (:data:`NOISE_FLOORS`) as
  well as a percentage one, or it is not reported at all.
* **Stale data outranks everything.** If a platform stopped syncing mid-window,
  every other finding is suspect — so freshness findings are scored above
  movements rather than listed after them.
* **A driver is only named when one exists.** "Google Ads drove 61% of the gain"
  is worth saying; "Google Ads drove 34% of it" across three platforms is not, so
  attribution is only attached when one source dominates
  (:data:`DRIVER_MIN_SHARE`).
"""

from __future__ import annotations

from datetime import date
from typing import Any

# ── Metric vocabulary ────────────────────────────────────────────────────────
# Mirrors the paid summary cards. ``direction`` says which way is good ("band"
# for spend, where both over and under are misses); ``weight`` is how much a
# move in that metric matters when findings compete for the top of the list —
# a swing in conversions is the story, the same swing in impressions rarely is.
METRICS: dict[str, dict[str, Any]] = {
    "conversions": {"label": "Conversions", "format": "number", "direction": "up", "weight": 1.0},
    "cpa": {"label": "CPA", "format": "currency", "direction": "down", "weight": 0.9},
    "clicks": {"label": "Clicks", "format": "number", "direction": "up", "weight": 0.6},
    "ctr": {"label": "CTR", "format": "percent", "direction": "up", "weight": 0.55},
    "cpc": {"label": "CPC", "format": "currency2", "direction": "down", "weight": 0.5},
    "spend": {"label": "Spend", "format": "currency", "direction": "band", "weight": 0.45},
    "impressions": {"label": "Impressions", "format": "number", "direction": "up", "weight": 0.3},
}

# Below these, a metric is too small for a percentage to mean anything. Checked
# against the larger of the two periods, so a metric that collapsed from a real
# base still reports.
NOISE_FLOORS: dict[str, float] = {
    "conversions": 5.0,
    "clicks": 100.0,
    "impressions": 2000.0,
    "spend": 250.0,
    "cpa": 0.0,      # rate metrics are gated by their denominator instead
    "cpc": 0.0,
    "ctr": 0.0,
}

# Rate metrics only mean something with enough underlying volume behind them.
RATE_DENOMINATORS: dict[str, tuple[str, float]] = {
    "ctr": ("impressions", 2000.0),
    "cpc": ("clicks", 100.0),
    "cpa": ("conversions", 5.0),
}

# A move smaller than this is period-to-period noise, not news.
MIN_MOVE_PCT = 10.0

# A source must account for *more* than this share of a change before it is named
# as the driver — i.e. strictly more than everything else put together. The
# comparison below is exclusive on purpose: at exactly 0.5 two platforms moved
# the account equally, and picking whichever one ``max()`` happened to return
# would be an arbitrary claim dressed up as an explanation.
DRIVER_MIN_SHARE = 0.5

# How far from the peer median counts as "notably" ahead or behind.
BENCHMARK_MIN_GAP = 0.15

# A source lagging the window's end by more than this many days is called out.
# Platforms normally report a day behind, so the floor is 2.
STALE_LAG_DAYS = 2

# Base scores per finding kind. Freshness sits above movements on purpose: a
# stale source changes how every other finding should be read.
KIND_BASE_SCORE: dict[str, float] = {
    "data_quality": 120.0,
    "goal": 70.0,
    "movement": 60.0,
    "benchmark": 40.0,
}

_PLATFORM_LABELS = {
    "paid_google": "Google Ads",
    "paid_linkedin": "LinkedIn Ads",
    "paid_meta": "Meta Ads",
    "paid_microsoft": "Microsoft Ads",
    "google": "Google Ads",
    "linkedin": "LinkedIn Ads",
    "meta": "Meta Ads",
    "microsoft": "Microsoft Ads",
}

_SOURCE_LABELS = {
    "google": "Google Ads",
    "linkedin": "LinkedIn Ads",
    "meta": "Meta Ads",
    "microsoft": "Microsoft Ads",
    "google_analytics": "GA4",
    "gsc": "Search Console",
}


# ── Formatting ───────────────────────────────────────────────────────────────


def fmt(value: float | None, fmt_key: str) -> str:
    """Render a metric value the way its dashboard card does."""
    if value is None:
        return "—"
    value = float(value)
    if fmt_key == "currency":
        return f"${value:,.0f}"
    if fmt_key == "currency2":
        return f"${value:,.2f}"
    if fmt_key == "percent":
        return f"{value:.2f}%"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def platform_label(key: str) -> str:
    k = str(key or "").lower()
    return _PLATFORM_LABELS.get(k, k.replace("paid_", "").replace("_", " ").title() or "Unknown")


def source_label(key: str) -> str:
    k = str(key or "").lower()
    return _SOURCE_LABELS.get(k, k.replace("_", " ").title() or "Unknown")


# ── Derivation helpers ───────────────────────────────────────────────────────


def _rates(totals: dict[str, Any]) -> dict[str, float]:
    """Totals plus the three derived rates, all as plain floats.

    ``by_source`` rows carry only the four raw counters, so rates are derived
    here rather than trusted from the payload — that way a per-source view and
    the whole-account view compute CPA the same way.
    """
    spend = float(totals.get("spend") or 0.0)
    impressions = float(totals.get("impressions") or 0.0)
    clicks = float(totals.get("clicks") or 0.0)
    conversions = float(totals.get("conversions") or 0.0)
    return {
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "ctr": (clicks / impressions * 100.0) if impressions else 0.0,
        "cpc": (spend / clicks) if clicks else 0.0,
        "cpa": (spend / conversions) if conversions else 0.0,
    }


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return (current - previous) / abs(previous) * 100.0


def _is_good(metric: str, pct: float) -> str:
    """Whether a move is good, bad, or neither, by the metric's own meaning."""
    direction = METRICS[metric]["direction"]
    if direction == "band":
        return "neutral"
    rising = pct > 0
    if direction == "up":
        return "good" if rising else "bad"
    return "bad" if rising else "good"


def _passes_noise_floor(metric: str, current: dict[str, float], previous: dict[str, float]) -> bool:
    """True when the metric is big enough for a percentage to be meaningful."""
    if metric in RATE_DENOMINATORS:
        key, floor = RATE_DENOMINATORS[metric]
        return max(current.get(key, 0.0), previous.get(key, 0.0)) >= floor
    floor = NOISE_FLOORS.get(metric, 0.0)
    return max(current.get(metric, 0.0), previous.get(metric, 0.0)) >= floor


def _driver(
    metric: str,
    by_source_current: dict[str, dict[str, Any]],
    by_source_previous: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """The one platform responsible for most of a metric's change, if there is one.

    Only defined for the additive metrics — "which platform drove the CPA change"
    has no honest answer, because a blended rate can move without any single
    platform's rate moving at all (the mix shifted). Those return None rather
    than a plausible-looking attribution.
    """
    if metric not in ("spend", "impressions", "clicks", "conversions"):
        return None
    deltas: dict[str, float] = {}
    for key in set(by_source_current) | set(by_source_previous):
        cur = float((by_source_current.get(key) or {}).get(metric) or 0.0)
        prev = float((by_source_previous.get(key) or {}).get(metric) or 0.0)
        deltas[key] = cur - prev
    if not deltas:
        return None
    total_abs = sum(abs(v) for v in deltas.values())
    if not total_abs:
        return None
    top_key = max(deltas, key=lambda k: abs(deltas[k]))
    share = abs(deltas[top_key]) / total_abs
    if share <= DRIVER_MIN_SHARE:
        return None
    return {
        "source": top_key,
        "label": platform_label(top_key),
        "delta": round(deltas[top_key], 2),
        "share": round(share, 4),
    }


def _annotations_in(
    annotations: list[dict[str, Any]] | None, start: date, end: date
) -> list[dict[str, Any]]:
    """Timeline events overlapping a window, earliest first."""
    out = []
    for a in annotations or []:
        a_start = str(a.get("event_date") or "")
        a_end = str(a.get("end_date") or a.get("event_date") or "")
        if not a_start:
            continue
        if a_start <= end.isoformat() and a_end >= start.isoformat():
            out.append(a)
    return sorted(out, key=lambda a: str(a.get("event_date") or ""))


# ── Finding builders ─────────────────────────────────────────────────────────


def _finding(
    *, kind: str, key: str, severity: str, headline: str, detail: str,
    score: float, evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{kind}:{key}",
        "kind": kind,
        "key": key,
        "severity": severity,
        "headline": headline,
        "detail": detail,
        "score": round(float(score), 2),
        "evidence": evidence or {},
    }


def _movement_findings(
    *,
    current: dict[str, float],
    previous: dict[str, float],
    by_source_current: dict[str, dict[str, Any]],
    by_source_previous: dict[str, dict[str, Any]],
    compare_label: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for metric, meta in METRICS.items():
        if not _passes_noise_floor(metric, current, previous):
            continue
        pct = _pct_change(current.get(metric, 0.0), previous.get(metric, 0.0))
        if pct is None or abs(pct) < MIN_MOVE_PCT:
            continue

        severity = _is_good(metric, pct)
        word = "up" if pct > 0 else "down"
        headline = (
            f"{meta['label']} {word} {abs(pct):.0f}% "
            f"({fmt(previous.get(metric), meta['format'])} → "
            f"{fmt(current.get(metric), meta['format'])}) vs {compare_label}"
        )

        bits: list[str] = []
        driver = _driver(metric, by_source_current, by_source_previous)
        if driver:
            bits.append(
                f"{driver['label']} accounts for {driver['share'] * 100:.0f}% of the change"
            )
        # A movement that coincides with something the agency recorded is the
        # single most useful thing this list can say, so it always rides along.
        if events:
            titles = ", ".join(str(e.get("title") or "") for e in events[:2])
            bits.append(f"coincides with {titles}")

        # Score: how big the move is, damped, times how much the metric matters.
        score = KIND_BASE_SCORE["movement"] + min(abs(pct), 200.0) * 0.35
        score *= meta["weight"]
        if driver:
            score += 4.0   # an explained movement beats an unexplained one
        if events:
            score += 6.0

        findings.append(_finding(
            kind="movement", key=metric, severity=severity, headline=headline,
            detail=". ".join(b[0].upper() + b[1:] for b in bits) + ("." if bits else ""),
            score=score,
            evidence={
                "metric": metric, "pct_change": round(pct, 2),
                "current": current.get(metric), "previous": previous.get(metric),
                "driver": driver,
                "annotations": [e.get("id") for e in events],
            },
        ))
    return findings


def _goal_findings(
    *, current: dict[str, float], goals: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Where a metric sits against its target, reported when it is not on track.

    A metric comfortably on target is not a finding — it is the absence of one —
    so only ``warn``/``bad`` states and a clear overshoot are emitted. This
    intentionally re-derives status from the resolved payload's own thresholds
    rather than importing metric_goals, keeping the module free of app imports.
    """
    if not goals:
        return []
    resolved = goals.get("goals") or {}
    thresholds = goals.get("thresholds") or {}
    findings: list[dict[str, Any]] = []

    for metric, spec in resolved.items():
        meta = METRICS.get(metric)
        target = float(spec.get("target") or 0.0)
        if not meta or target <= 0:
            continue
        value = current.get(metric)
        if value is None:
            continue
        pct = float(value) / target * 100.0
        direction = str(spec.get("direction") or "up")
        t = thresholds.get(direction) or {}
        if not t:
            continue

        if direction == "up":
            status = "good" if pct >= t.get("good", 90) else (
                "warn" if pct >= t.get("warn", 60) else "bad")
        elif direction == "down":
            status = "good" if pct <= t.get("good", 100) else (
                "warn" if pct <= t.get("warn", 125) else "bad")
        else:
            status = "good" if t.get("good_low", 90) <= pct <= t.get("good_high", 110) else (
                "warn" if t.get("warn_low", 75) <= pct <= t.get("warn_high", 125) else "bad")

        if status == "good":
            continue

        if direction == "down":
            over = pct - 100.0
            headline = (
                f"{meta['label']} is {over:.0f}% over its "
                f"{fmt(target, meta['format'])} target"
            )
        elif direction == "band":
            side = "over" if pct > 100 else "under"
            headline = (
                f"{meta['label']} is {abs(pct - 100):.0f}% {side} plan "
                f"({fmt(value, meta['format'])} vs {fmt(target, meta['format'])})"
            )
        else:
            headline = (
                f"{meta['label']} is at {pct:.0f}% of its "
                f"{fmt(target, meta['format'])} target"
            )

        findings.append(_finding(
            kind="goal", key=metric, severity=status, headline=headline,
            detail=str(spec.get("note") or ""),
            score=KIND_BASE_SCORE["goal"] * meta["weight"] + (14.0 if status == "bad" else 0.0),
            evidence={
                "metric": metric, "value": value, "target": target,
                "pct_of_target": round(pct, 1), "direction": direction,
            },
        ))
    return findings


def _benchmark_findings(benchmarks: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Where the account sits against its peers, when the gap is worth a mention."""
    if not benchmarks or not benchmarks.get("available"):
        return []
    findings: list[dict[str, Any]] = []
    for metric, row in (benchmarks.get("metrics") or {}).items():
        meta = METRICS.get(metric)
        peer = row.get("peer") or {}
        median = float(peer.get("median") or 0.0)
        value = row.get("value")
        if not meta or not median or value is None:
            continue
        gap = (float(value) - median) / median
        if abs(gap) < BENCHMARK_MIN_GAP:
            continue

        direction = meta["direction"]
        if direction == "band":
            severity, word = "neutral", ("above" if gap > 0 else "below")
        elif (direction == "up" and gap > 0) or (direction == "down" and gap < 0):
            severity, word = "good", "better than"
        else:
            severity, word = "bad", "worse than"

        scope = row.get("peer_label") or "peers"
        headline = (
            f"{meta['label']} is {abs(gap) * 100:.0f}% {word} the {scope} median "
            f"({fmt(value, meta['format'])} vs {fmt(median, meta['format'])})"
        )
        detail = f"Across {peer.get('n')} comparable account" + ("" if peer.get("n") == 1 else "s")
        if peer.get("thin"):
            detail += " — a thin sample, so read it as directional"
        findings.append(_finding(
            kind="benchmark", key=metric, severity=severity, headline=headline,
            detail=detail + ".",
            # A thin peer group is worth less attention than a deep one.
            score=(KIND_BASE_SCORE["benchmark"] * meta["weight"]
                   + min(abs(gap) * 100, 100) * 0.2
                   - (12.0 if peer.get("thin") else 0.0)),
            evidence={"metric": metric, "value": value, "median": median,
                      "n": peer.get("n"), "thin": bool(peer.get("thin")),
                      "gap": round(gap, 4)},
        ))
    return findings


def _freshness_findings(
    *, freshness: list[dict[str, Any]] | None, window_end: date
) -> list[dict[str, Any]]:
    """Sources that stopped short of the window, which make everything else suspect."""
    findings: list[dict[str, Any]] = []
    for row in freshness or []:
        latest = str(row.get("latest_date") or "")
        if not latest:
            continue
        try:
            lag = (window_end - date.fromisoformat(latest)).days
        except ValueError:
            continue
        if lag <= STALE_LAG_DAYS:
            continue
        name = source_label(str(row.get("source") or ""))
        findings.append(_finding(
            kind="data_quality", key=str(row.get("source") or ""),
            severity="warn" if lag <= 7 else "bad",
            headline=f"{name} data stops {lag} days before the end of this range",
            detail=(
                f"Last synced day is {latest}. Totals and comparisons that include "
                f"{name} understate this window until it catches up."
            ),
            score=KIND_BASE_SCORE["data_quality"] + min(lag, 30) * 1.5,
            evidence={"source": row.get("source"), "latest_date": latest, "lag_days": lag},
        ))
    return findings


# ── Entry point ──────────────────────────────────────────────────────────────


def build_findings(
    *,
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None = None,
    goals: dict[str, Any] | None = None,
    benchmarks: dict[str, Any] | None = None,
    annotations: list[dict[str, Any]] | None = None,
    freshness: list[dict[str, Any]] | None = None,
    window: tuple[date, date],
    compare_label: str = "the previous period",
    limit: int = 8,
) -> dict[str, Any]:
    """Rank what is worth saying about this window. Pure — no I/O.

    ``current``/``previous`` are ``marketing_service.fetch_summary`` payloads.
    Every other input is optional and simply contributes no findings when absent,
    so a client with no goals, no peers, and no timeline still gets movement
    findings, and one with no comparison window still gets goal and benchmark
    ones.
    """
    start, end = window
    cur_totals = _rates((current or {}).get("summary") or {})
    prev_totals = _rates((previous or {}).get("summary") or {}) if previous else {}
    by_source_current = (current or {}).get("by_source") or {}
    by_source_previous = (previous or {}).get("by_source") or {}
    events = _annotations_in(annotations, start, end)

    findings: list[dict[str, Any]] = []
    findings += _freshness_findings(freshness=freshness, window_end=end)
    if previous:
        findings += _movement_findings(
            current=cur_totals, previous=prev_totals,
            by_source_current=by_source_current, by_source_previous=by_source_previous,
            compare_label=compare_label, events=events,
        )
    findings += _goal_findings(current=cur_totals, goals=goals)
    findings += _benchmark_findings(benchmarks)

    findings.sort(key=lambda f: (-f["score"], f["id"]))
    limited = findings[: max(1, int(limit))]

    return {
        "findings": limited,
        "total_found": len(findings),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "compare_label": compare_label,
        "counts": {
            kind: sum(1 for f in findings if f["kind"] == kind)
            for kind in KIND_BASE_SCORE
        },
        "timeline_events": [
            {"id": e.get("id"), "event_date": e.get("event_date"),
             "title": e.get("title"), "category_label": e.get("category_label"),
             "color": e.get("color")}
            for e in events
        ],
        "inputs": {
            "has_comparison": bool(previous),
            "has_goals": bool((goals or {}).get("goals")),
            "has_benchmarks": bool((benchmarks or {}).get("available")),
            "has_annotations": bool(annotations),
        },
    }
