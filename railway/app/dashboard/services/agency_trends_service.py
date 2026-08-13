"""Agency Trends: the HQ client view, reproduced (and extended) on DuckDB.

The HQ page answers "how is each client pacing" by looping over clients and,
for each, reading its BigQuery mart and summing the numbers in Python. This
service reproduces exactly what HQ shows — every client's month-to-date spend
vs budget and its trailing-30d web-traffic sparkline — but hands the combined
data to an in-process DuckDB database and does the rollups in columnar SQL. On
top of that same data it also answers the *agency-wide* questions HQ's
per-client loop cannot cheaply reach ("which clients slowed down this week",
"where is spend concentrated by channel"), because those need every client's
daily series lined up side by side.

Paid-media spend and GA4 sessions live in each client's own BigQuery mart
(``vw_paid_media_daily`` / ``vw_ga4_traffic_acq_daily`` in their
``marketing_marts`` dataset — the very sources HQ and the dashboards read), one
project per client, so there is no single table to scan. ``build_agency_overview``
fetches each client's daily spend and sessions concurrently (sharing HQ's cache
keys where the windows line up), loads the combined set into DuckDB, and computes
both views from one set of reads.

Why DuckDB rather than summing in Python: once every client's daily rows are in
one place, the interesting cuts — MTD spend and sessions per client, last-7 vs
prior-7, channel mix, and the per-source pivots we'll want next — are one SQL
each with FILTER buckets and list/window aggregates, instead of hand-rolled dict
arithmetic. DuckDB runs fully in-process (no server, no extension download), so
it stays deploy-safe.
"""

from __future__ import annotations

import calendar
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

import client_dashboard_config
import db_cache
import marketing_service
from dashboard.services import kpi_registry
from dashboard.utils.dates import mtd_calendar_bounds

LOGGER = logging.getLogger(__name__)

# Trailing comparison window: 7 full days vs the 7 before them. Ends *yesterday*
# because platform syncs lag a day, so "today" is usually partial and would read
# as a false dip (same reasoning the HQ sessions sparkline uses).
_WINDOW_DAYS = 7
# Sessions sparkline window, matched to the HQ page so the trends page reads the
# same trailing traffic momentum (and shares HQ's warm sessions cache).
_SESSIONS_TRAILING_DAYS = 30
_OVERVIEW_CACHE_SOURCE = "agency.overview"
# Connector-health staleness, measured against yesterday (platform data lags ~1
# day, so "current" = data through yesterday). Derived purely from the daily
# rows already in DuckDB — no extra BigQuery probes. A lag of >=2 days is amber,
# >=4 days is red; the full per-source diagnosis lives on the client's settings
# page, which the chip links to.
_HEALTH_LAGGING_DAYS = 2
_HEALTH_STALE_DAYS = 4
# Worst-first rank so the client table can sort problems to the top. Neutral
# states (nothing wrong / nothing configured) share rank 0.
_HEALTH_RANK = {
    "current": 0, "no_data": 0, "not_configured": 0,
    "lagging": 1, "stale": 2, "error": 3,
}
_CACHE_TTL_SECONDS = 900
_CLIENT_TTL_SECONDS = 900
_MAX_WORKERS = 8


def _window_bounds(today: date) -> tuple[date, date, date, date]:
    """(last_lo, last_hi, prior_lo, prior_hi) for the two 7-day windows."""
    last_hi = today - timedelta(days=1)                 # yesterday
    last_lo = last_hi - timedelta(days=_WINDOW_DAYS - 1)
    prior_hi = last_lo - timedelta(days=1)
    prior_lo = prior_hi - timedelta(days=_WINDOW_DAYS - 1)
    return last_lo, last_hi, prior_lo, prior_hi


# Date-range choices for the primary-KPI filter (the only thing the filter
# re-scopes — budget pacing stays month-to-date). Each entry maps to a bounds
# resolver in _kpi_window_bounds; "month" is the default and reproduces the
# original month-to-date KPI column.
KPI_RANGES = ("month", "last_week", "last_30d")
_KPI_RANGE_DAYS = 30  # last_30d window length


def _kpi_window_bounds(
    today: date, kpi_range: str, *, include_today: bool
) -> tuple[date, date, int, str]:
    """(start, end, window_days, label) for the primary-KPI aggregation window.

    * ``month``     — month_start..today (month-to-date), the default. window_days
                      is the number of days elapsed so pacing matches the budget
                      view's share-of-month.
    * ``last_week`` — the most recent *complete* Monday–Sunday week (ends last
                      Sunday). Always 7 whole days, so ``include_today`` is moot.
    * ``last_30d``  — the trailing 30 days; ends today when ``include_today`` is
                      set, otherwise yesterday (platform data lags a day).

    ``window_days`` feeds the paced-KPI grader: it is the share of a monthly goal
    the window represents (a complete week is ~7/30 of the goal), so a client on
    monthly pace reads as on-pace for the shorter window too.
    """
    if kpi_range == "last_week":
        # weekday(): Mon=0 … Sun=6. The most recent finished Sunday is
        # today minus (weekday + 1) days; the week's Monday is six days before it.
        last_sunday = today - timedelta(days=today.weekday() + 1)
        start = last_sunday - timedelta(days=6)
        return start, last_sunday, _WINDOW_DAYS, "last week"
    if kpi_range == "last_30d":
        end = today if include_today else today - timedelta(days=1)
        start = end - timedelta(days=_KPI_RANGE_DAYS - 1)
        return start, end, _KPI_RANGE_DAYS, "last 30 days"
    # Default: month-to-date.
    month_start = today.replace(day=1)
    return month_start, today, today.day, "month to date"


def _channel_label(source_platform: str) -> str:
    """vw_paid_media_daily uses 'paid_google' / 'paid_meta' / 'paid_linkedin';
    show the bare channel."""
    s = str(source_platform or "").lower()
    return s[5:] if s.startswith("paid_") else s


def compute_agency_trends(
    daily_rows: list[tuple[str, str, date, float]],
    label_map: dict[str, str],
    *,
    today: date,
) -> dict[str, Any]:
    """Pure DuckDB computation — no I/O, so it is easy to test.

    ``daily_rows`` : (client_slug, source_platform, metric_date, spend), already
                     scoped to the trailing window.
    ``label_map``  : client_slug -> display label.
    """
    last_lo, last_hi, prior_lo, prior_hi = _window_bounds(today)

    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE d (client_slug TEXT, source TEXT, metric_date DATE, spend DOUBLE)"
        )
        if daily_rows:
            con.executemany("INSERT INTO d VALUES (?, ?, ?, ?)", daily_rows)

        client_rows = con.execute(
            """
            SELECT client_slug,
                   SUM(spend) FILTER (WHERE metric_date BETWEEN ? AND ?) AS last_7,
                   SUM(spend) FILTER (WHERE metric_date BETWEEN ? AND ?) AS prior_7
            FROM d
            GROUP BY client_slug
            """,
            [last_lo, last_hi, prior_lo, prior_hi],
        ).fetchall()

        channel_rows = con.execute(
            """
            SELECT source, SUM(spend) AS spend
            FROM d
            WHERE metric_date BETWEEN ? AND ?
            GROUP BY source
            HAVING SUM(spend) > 0
            ORDER BY spend DESC
            """,
            [last_lo, last_hi],
        ).fetchall()
    finally:
        con.close()

    clients: list[dict[str, Any]] = []
    tot_last = 0.0
    tot_prior = 0.0
    n_declining = 0
    for slug, last_7, prior_7 in client_rows:
        last_v = float(last_7 or 0.0)
        prior_v = float(prior_7 or 0.0)
        if last_v <= 0 and prior_v <= 0:
            continue  # no spend in either window — nothing to show
        pct = round(100.0 * (last_v - prior_v) / prior_v, 1) if prior_v > 0 else None
        clients.append({
            "client_slug": slug,
            "label": label_map.get(slug, slug),
            "last_7": round(last_v, 2),
            "prior_7": round(prior_v, 2),
            "delta": round(last_v - prior_v, 2),
            "pct_change": pct,
        })
        tot_last += last_v
        tot_prior += prior_v
        if pct is not None and pct < 0:
            n_declining += 1

    # Steepest decline first (Nones — no prior baseline — sort last).
    clients.sort(key=lambda r: (r["pct_change"] is None, r["pct_change"] if r["pct_change"] is not None else 0))

    channel_total = sum(float(s or 0.0) for _, s in channel_rows)
    channels = [
        {
            "source": _channel_label(src),
            "spend": round(float(spend or 0.0), 2),
            "pct_of_total": round(100.0 * float(spend or 0.0) / channel_total, 1) if channel_total else 0.0,
        }
        for src, spend in channel_rows
    ]

    agency_pct = round(100.0 * (tot_last - tot_prior) / tot_prior, 1) if tot_prior > 0 else None
    return {
        "as_of": last_hi.isoformat(),
        "window": {
            "days": _WINDOW_DAYS,
            "last": {"start": last_lo.isoformat(), "end": last_hi.isoformat()},
            "prior": {"start": prior_lo.isoformat(), "end": prior_hi.isoformat()},
        },
        "totals": {
            "last_7": round(tot_last, 2),
            "prior_7": round(tot_prior, 2),
            "delta": round(tot_last - tot_prior, 2),
            "pct_change": agency_pct,
            "clients_declining": n_declining,
            "client_count": len(clients),
        },
        "clients": clients,
        "channels": channels,
    }


def _pace_status(*, budget: float, projected: float | None) -> str:
    """Coarse pacing label the UI colors: over / under / on_track / no_budget.

    Kept byte-for-byte in step with hq_budget_service._pace_status so the DuckDB
    reproduction lands on the same status a client shows on the HQ page.
    """
    if budget <= 0:
        return "no_budget"
    if projected is None:
        return "on_track"
    if projected > budget * 1.05:
        return "over"
    if projected < budget * 0.95:
        return "under"
    return "on_track"


def _health_rank(status: str) -> int:
    return _HEALTH_RANK.get(status, 0)


def _worse(a: str, b: str) -> str:
    """The more alarming of two health statuses."""
    return a if _health_rank(a) >= _health_rank(b) else b


def _client_health(
    *,
    configured: bool,
    spend_available: bool,
    sessions_available: bool,
    fresh_through: date | None,
    sessions_through: date | None,
    channels: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """A lightweight connector-health verdict from data already in DuckDB.

    We can't afford the per-source BigQuery probes the settings page runs for
    every client at once, so we infer health from how fresh each client's feed
    is: a healthy connector's data reaches ~yesterday, a stalled one stops
    advancing. ``fresh_through`` is the newest paid-media date we hold for the
    client (max over every channel), so a client is only flagged when *no*
    channel is current — an intentionally-paused single channel (which simply
    has an older through-date) never trips a false alarm on its own. A read that
    outright failed is a hard error. The ``channels`` list carries each active
    channel's through-date for the tooltip so the culprit is visible.
    """
    yesterday = today - timedelta(days=1)
    reasons: list[str] = []

    if not configured:
        return {
            "status": "not_configured", "lag_days": None,
            "fresh_through": None, "sessions_through": None,
            "channels": channels, "reasons": ["no BigQuery project configured"],
        }

    lag = (yesterday - fresh_through).days if fresh_through else None
    sess_lag = (yesterday - sessions_through).days if sessions_through else None

    if not spend_available:
        status = "error"
        reasons.append("paid-media read failed")
    elif fresh_through is None:
        status = "no_data"
    elif lag >= _HEALTH_STALE_DAYS:
        status = "stale"
        reasons.append(f"paid data {lag} days behind")
    elif lag >= _HEALTH_LAGGING_DAYS:
        status = "lagging"
        reasons.append(f"paid data {lag} days behind")
    else:
        status = "current"

    # Traffic is a second feed (GA4 export). A failed read, or a sessions series
    # that has fallen well behind, is at least amber — but never downgrades a
    # harder paid-media verdict.
    if not sessions_available:
        reasons.append("traffic read failed")
        status = _worse(status, "lagging")
    elif sess_lag is not None and sess_lag >= _HEALTH_STALE_DAYS:
        reasons.append(f"traffic {sess_lag} days behind")
        status = _worse(status, "lagging")

    return {
        "status": status,
        "lag_days": lag,
        "fresh_through": fresh_through.isoformat() if fresh_through else None,
        "sessions_through": sessions_through.isoformat() if sessions_through else None,
        "channels": channels,
        "reasons": reasons,
    }


def compute_agency_budget(
    spend_rows: list[tuple[str, str, date, float]],
    sessions_rows: list[tuple[str, date, int]],
    clients: list[dict[str, Any]],
    *,
    today: date,
) -> dict[str, Any]:
    """Pure DuckDB reproduction of the HQ budget overview — no I/O, easy to test.

    This is the DuckDB analogue of hq_budget_service.build_hq_budget_overview:
    given every client's daily paid spend and daily GA4 sessions lined up in one
    place, DuckDB rolls up each client's month-to-date spend and trailing-30-day
    sessions in two columnar queries, and we derive the same pacing fields HQ
    shows. The returned payload is key-for-key identical to the HQ feed, so the
    same client list (spend vs budget + sessions sparkline) renders unchanged.

    Each row also carries a lightweight ``health`` verdict (connector freshness
    inferred from the same rows — see _client_health) plus a ``health_rank`` for
    sorting problems to the top.

    ``spend_rows``    : (client_slug, source, metric_date, spend).
    ``sessions_rows`` : (client_slug, metric_date, sessions).
    ``clients``       : ordered client rows, each with client_slug, label,
                        monthly_budget, a ``configured`` flag (whether a
                        BigQuery project is set), and spend_available /
                        sessions_available flags (whether that client's read
                        succeeded).
    """
    month_start = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    days_remaining = max(0, days_in_month - days_elapsed)
    pct_month = round(100 * days_elapsed / days_in_month, 1) if days_in_month else 0.0
    sessions_end = today - timedelta(days=1)
    sessions_start = sessions_end - timedelta(days=_SESSIONS_TRAILING_DAYS - 1)

    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE d (client_slug TEXT, source TEXT, metric_date DATE, spend DOUBLE)"
        )
        con.execute(
            "CREATE TABLE s (client_slug TEXT, metric_date DATE, sessions BIGINT)"
        )
        if spend_rows:
            con.executemany("INSERT INTO d VALUES (?, ?, ?, ?)", spend_rows)
        if sessions_rows:
            con.executemany("INSERT INTO s VALUES (?, ?, ?)", sessions_rows)

        # Month-to-date paid spend per client (inclusive of today's partial day,
        # matching HQ's month_start..today read).
        mtd_map = {
            slug: float(v or 0.0)
            for slug, v in con.execute(
                """
                SELECT client_slug, SUM(spend)
                FROM d
                WHERE metric_date BETWEEN ? AND ?
                GROUP BY client_slug
                """,
                [month_start, today],
            ).fetchall()
        }

        # Trailing-30d sessions per client, kept as an ordered daily list so the
        # sparkline reads chronologically — DuckDB's list() aggregate does the
        # ordering that HQ relied on the SQL ORDER BY to provide.
        sess_map: dict[str, tuple[list[int], int]] = {
            slug: ([int(x or 0) for x in (series or [])], int(total or 0))
            for slug, series, total in con.execute(
                """
                SELECT client_slug,
                       list(sessions ORDER BY metric_date) AS series,
                       SUM(sessions) AS total
                FROM s
                WHERE metric_date BETWEEN ? AND ?
                GROUP BY client_slug
                """,
                [sessions_start, sessions_end],
            ).fetchall()
        }

        # Connector-health freshness: the newest date each feed reaches. The
        # freshest paid date overall (max across channels) grades the client;
        # the per-channel through-dates feed the chip tooltip so the culprit is
        # visible. All from the rows already loaded — no extra reads.
        fresh_map = {
            slug: mx
            for slug, mx in con.execute(
                "SELECT client_slug, MAX(metric_date) FROM d GROUP BY client_slug"
            ).fetchall()
        }
        sess_fresh_map = {
            slug: mx
            for slug, mx in con.execute(
                "SELECT client_slug, MAX(metric_date) FROM s GROUP BY client_slug"
            ).fetchall()
        }
        chan_map: dict[str, list[dict[str, Any]]] = {}
        for slug, src, mx, sp in con.execute(
            """
            SELECT client_slug, source, MAX(metric_date) AS mx, SUM(spend) AS sp
            FROM d
            GROUP BY client_slug, source
            ORDER BY sp DESC
            """
        ).fetchall():
            chan_map.setdefault(slug, []).append({
                "source": _channel_label(src),
                "through": mx.isoformat() if mx else None,
                "spend": round(float(sp or 0.0), 2),
            })
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    tot_budget = 0.0
    tot_spend = 0.0
    tot_projected = 0.0
    n_over = 0
    n_data_issues = 0
    for c in clients:
        slug = str(c["client_slug"])
        budget = float(c.get("monthly_budget") or 0.0)
        spend_available = bool(c.get("spend_available"))
        sessions_available = bool(c.get("sessions_available"))

        mtd = mtd_map.get(slug, 0.0) if spend_available else 0.0
        series, sess_total = sess_map.get(slug, ([], 0))

        pct_budget = round(100 * mtd / budget, 1) if budget > 0 else None
        expected_pace = round(budget * days_elapsed / days_in_month, 2) if budget > 0 else None
        projected = (
            round(mtd / days_elapsed * days_in_month, 2)
            if days_elapsed > 0 and spend_available
            else None
        )
        remaining = round(budget - mtd, 2) if budget > 0 else None
        pace_delta = round(projected - budget, 2) if (projected is not None and budget > 0) else None
        status = _pace_status(budget=budget, projected=projected)

        health = _client_health(
            configured=bool(c.get("configured")),
            spend_available=spend_available,
            sessions_available=sessions_available,
            fresh_through=fresh_map.get(slug),
            sessions_through=sess_fresh_map.get(slug),
            channels=chan_map.get(slug, []),
            today=today,
        )

        rows.append({
            "client_slug": slug,
            "label": str(c.get("label") or slug),
            "monthly_budget": round(budget, 2) if budget else 0.0,
            "has_budget": budget > 0,
            "mtd_spend": round(mtd, 2),
            "spend_available": spend_available,
            "pct_budget": pct_budget,
            "expected_pace": expected_pace,
            "projected_month_end": projected,
            "remaining_budget": remaining,
            "pace_delta": pace_delta,
            "status": status,
            "sessions_series": series,
            "sessions_total": sess_total,
            "sessions_available": sessions_available and bool(series),
            "health": health,
            "health_rank": _health_rank(health["status"]),
        })

        tot_budget += budget
        if spend_available:
            tot_spend += mtd
            if projected is not None:
                tot_projected += projected
            if status == "over":
                n_over += 1
        if health["status"] in ("stale", "error"):
            n_data_issues += 1

    # Attach each client's latest Consent & Tracking Health verdict (one batched
    # query, best-effort — never blocks the HQ view if it fails).
    try:
        import consent_store
        consent_map = consent_store.latest_health_by_slug()
    except Exception:
        LOGGER.warning("HQ trends: consent health lookup failed", exc_info=True)
        consent_map = {}
    for row in rows:
        row["consent"] = consent_map.get(str(row["client_slug"]).strip().lower())

    return {
        "month_label": month_start.strftime("%B %Y"),
        "as_of": today.isoformat(),
        "sessions_window": {
            "start": sessions_start.isoformat(),
            "end": sessions_end.isoformat(),
            "days": _SESSIONS_TRAILING_DAYS,
        },
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "pct_month_elapsed": pct_month,
        "totals": {
            "monthly_budget": round(tot_budget, 2),
            "mtd_spend": round(tot_spend, 2),
            "projected_month_end": round(tot_projected, 2),
            "clients_over_pace": n_over,
            "clients_with_data_issues": n_data_issues,
            "client_count": len(rows),
        },
        "clients": rows,
    }


def overview_fetch_bounds(today: date) -> tuple[date, date, date, date]:
    """(spend_start, spend_end, sessions_start, sessions_end) — the BigQuery
    read windows every agency-wide view shares.

    Exported (rather than inlined in build_agency_overview) so the Benchmarks
    rollup can fetch over *exactly* these windows and therefore hit the very
    same ``{slug}.trends.summary`` / ``{slug}.hq.sessions_daily`` cache entries.
    A one-day drift here would silently double the agency's BigQuery bill, so
    the two pages must agree on the bounds by construction, not by convention.

    The spend window is the widest any view needs: month-to-date, the
    week-over-week prior window, and the KPI filter's 30-day reach, whichever
    starts earliest. Sessions trail 30 days ending *yesterday* (platform syncs
    lag a day, so today would read as a false dip).
    """
    month_start = today.replace(day=1)
    _, _, prior_lo, _ = _window_bounds(today)
    spend_start = min(month_start, prior_lo, today - timedelta(days=_KPI_RANGE_DAYS))
    sessions_end = today - timedelta(days=1)
    sessions_start = sessions_end - timedelta(days=_SESSIONS_TRAILING_DAYS - 1)
    return spend_start, today, sessions_start, sessions_end


def cached_client_summary(
    *,
    slug: str,
    project_id: str,
    dataset_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    """One client's paid-media summary payload (``marketing_service.fetch_summary``),
    read through the shared ``{slug}.trends.summary`` cache.

    The whole response is cached, not just the fields this page happens to use,
    so a second consumer (Benchmarks needs impressions and clicks; the trends
    rollup only needs spend) reads the richer columns for free off a warm entry.
    """
    payload = {"start": start.isoformat(), "end": end.isoformat()}
    hit = db_cache.get_cached(f"{slug}.trends.summary", payload)
    if hit is not None:
        return hit.response_json or {}

    with marketing_service.route(
        client_key=slug, project_id=project_id, mart_dataset_id=dataset_id
    ):
        result = marketing_service.fetch_summary(start_date=start, end_date=end)
    try:
        db_cache.put_cached(
            f"{slug}.trends.summary", payload, response_json=result,
            row_count=0, ttl_seconds=_CLIENT_TTL_SECONDS,
        )
    except Exception:
        pass
    return result or {}


def cached_client_sessions(
    *,
    slug: str,
    project_id: str,
    dataset_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    """One client's daily GA4 sessions payload, read through HQ's
    ``{slug}.hq.sessions_daily`` cache so every agency view shares one read."""
    payload = {"start": start.isoformat(), "end": end.isoformat()}
    hit = db_cache.get_cached(f"{slug}.hq.sessions_daily", payload)
    if hit is not None:
        return hit.response_json or {}

    with marketing_service.route(
        client_key=slug, project_id=project_id, mart_dataset_id=dataset_id
    ):
        result = marketing_service.fetch_sessions_daily(start_date=start, end_date=end)
    try:
        db_cache.put_cached(
            f"{slug}.hq.sessions_daily", payload, response_json=result,
            row_count=0, ttl_seconds=_CLIENT_TTL_SECONDS,
        )
    except Exception:
        pass
    return result or {}


def _client_sessions_rows(
    *,
    slug: str,
    project_id: str,
    dataset_id: str,
    start: date,
    end: date,
) -> list[tuple[str, date, int]]:
    """One client's daily GA4 sessions, cached under HQ's key so the two share
    a warm read. Returns (client_slug, metric_date, sessions) for DuckDB."""
    result = cached_client_sessions(
        slug=slug, project_id=project_id, dataset_id=dataset_id, start=start, end=end
    )

    rows: list[tuple[str, date, int]] = []
    for r in (result or {}).get("daily") or []:
        raw_date = r.get("date")
        if not raw_date:
            continue
        try:
            d = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append((slug, d, int(r.get("sessions") or 0)))
    return rows


def _client_daily_rows(
    *,
    slug: str,
    project_id: str,
    dataset_id: str,
    start: date,
    end: date,
    kpi_start: date,
    kpi_end: date,
) -> tuple[list[tuple[str, str, date, float]], dict[str, dict[str, float]]]:
    """One client's daily paid-media spend by channel, cached like /summary.

    Reuses marketing_service.fetch_summary — the same BigQuery read HQ and the
    dashboards use — and returns two things from that one read:

    * ``rows``     : the per-day/per-source spend series (for the spend rollups),
    * ``paid_kpi`` : per-source totals (spend / conversions / conversion_value)
                     over the primary-KPI window ``kpi_start``..``kpi_end``, which
                     the KPI resolver reads for the paid KPI types. The read spans
                     the wider ``start``..``end`` fetch window so the same cached
                     summary serves every KPI date-range choice; we simply sum the
                     slice the selected range asks for. Computing it here means the
                     Google Ads conversions / ROAS KPIs cost no extra BigQuery
                     reads — they ride the summary we already pull.
    """
    result = cached_client_summary(
        slug=slug, project_id=project_id, dataset_id=dataset_id, start=start, end=end
    )

    rows: list[tuple[str, str, date, float]] = []
    paid_kpi: dict[str, dict[str, float]] = {}
    for r in (result or {}).get("daily") or []:
        raw_date = r.get("date")
        if not raw_date:
            continue
        try:
            d = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        source = str(r.get("source") or "unknown")
        spend = float(r.get("spend") or 0.0)
        rows.append((slug, source, d, spend))
        if kpi_start <= d <= kpi_end:
            agg = paid_kpi.setdefault(source, {"spend": 0.0, "conversions": 0.0, "conversion_value": 0.0})
            agg["spend"] += spend
            agg["conversions"] += float(r.get("conversions") or 0.0)
            agg["conversion_value"] += float(r.get("conversion_value") or 0.0)
    return rows, paid_kpi


def _client_mqls(*, slug: str, start: date, end: date) -> int | None:
    """One client's HubSpot MQL count over ``start``..``end``, cached for the KPI
    column. The window follows the primary-KPI date-range filter (month-to-date
    by default), so the cache key carries the window and distinct ranges never
    collide.

    Routed through hubspot_reports_service (its own connector config picks the
    HubSpot mart project/dataset, which may differ from the paid-media mart).
    Returns None when HubSpot isn't configured or the read fails, so the KPI
    simply reads as "no data" rather than blocking the HQ view.
    """
    payload = {"start": start.isoformat(), "end": end.isoformat()}
    hit = db_cache.get_cached(f"{slug}.hq.mtd_mqls", payload)
    if hit is not None:
        val = hit.response_json.get("mqls") if isinstance(hit.response_json, dict) else None
        return int(val) if val is not None else None

    import hubspot_reports_service
    count = hubspot_reports_service.fetch_mtd_mql_count(slug, start=start, end=end)
    try:
        db_cache.put_cached(
            f"{slug}.hq.mtd_mqls", payload, response_json={"mqls": count},
            row_count=0, ttl_seconds=_CLIENT_TTL_SECONDS,
        )
    except Exception:
        pass
    return count


def _resolve_destination(c: dict[str, Any]) -> tuple[str | None, str]:
    """(project_id, dataset_id) for a client's paid-media mart, from its config.

    A client with no gcp_project_id on its config row is not spend-computable and
    is skipped by the caller — same as any other unconfigured client.
    """
    project_id = c.get("gcp_project_id")
    dataset_id = c.get("bq_mart_dataset_id") or "marketing_marts"
    return project_id, dataset_id


def build_agency_overview(
    *, kpi_range: str = "month", include_today: bool = False
) -> dict[str, Any]:
    """Full DuckDB-powered payload for the Agency Trends page.

    One concurrent pass of per-client BigQuery reads (each client's daily paid
    spend and daily GA4 sessions) feeds a single DuckDB session that produces
    *both* views the page shows:

    * The HQ reproduction — every client's month-to-date spend vs budget plus a
      trailing-30d sessions sparkline — is returned key-for-key as the HQ feed
      (``clients``, ``totals``, ``sessions_window``, …) at the top level, so the
      same client list renders unchanged.
    * ``momentum`` (nested) — week-over-week paid-media change and channel mix,
      which the front-end folds onto each client row by slug.

    ``kpi_range`` / ``include_today`` scope *only* the primary-KPI column (see
    _kpi_window_bounds); spend-vs-budget pacing and the sessions sparkline are
    always month-to-date / trailing-30d respectively. The daily reads span a
    fixed window wide enough for every choice, so switching the KPI range never
    triggers a fresh paid-media read — only the aggregation slice changes.

    Reading spend and sessions once per client (rather than the HQ page and the
    trends page each doing their own reads) is the load-time and BigQuery-usage
    win: the columnar rollups are cheap once the daily rows are in DuckDB.
    """
    if kpi_range not in KPI_RANGES:
        kpi_range = "month"
    _, _, today = mtd_calendar_bounds()
    kpi_start, kpi_end, kpi_window_days, kpi_window_label = _kpi_window_bounds(
        today, kpi_range, include_today=include_today
    )
    # Fixed fetch windows shared with the Benchmarks page (see
    # overview_fetch_bounds) — wide enough for every KPI range, so switching the
    # range never triggers a fresh paid-media read.
    spend_start, _spend_end, sessions_start, sessions_end = overview_fetch_bounds(today)

    cache_key = {
        "today": today.isoformat(),
        "kpi_range": kpi_range,
        "include_today": bool(include_today),
    }
    hit = db_cache.get_cached(_OVERVIEW_CACHE_SOURCE, cache_key)
    if hit is not None:
        return hit.response_json

    clients_cfg = client_dashboard_config.list_budget_overview()

    def _work(
        c: dict[str, Any],
    ) -> tuple[list[tuple[str, str, date, float]], list[tuple[str, date, int]], dict[str, Any]]:
        slug = str(c["client_slug"])
        kpi_spec = c.get("primary_kpi")
        meta: dict[str, Any] = {
            "client_slug": slug,
            "label": str(c.get("label") or slug),
            "monthly_budget": float(c.get("monthly_budget_usd") or 0.0),
            "configured": False,
            "spend_available": False,
            "sessions_available": False,
            "primary_kpi_spec": kpi_spec,
            "kpi_inputs": {"paid_mtd": {}, "mql_count": None},
        }
        # HubSpot MQLs live in a separate mart, reachable even when the client
        # has no paid-media project — so fetch them off the paid-media gate.
        if kpi_registry.needs_hubspot(kpi_spec):
            try:
                meta["kpi_inputs"]["mql_count"] = _client_mqls(
                    slug=slug, start=kpi_start, end=kpi_end,
                )
            except Exception:
                LOGGER.exception("Agency overview: MQL read failed for %s", slug)

        project_id, dataset_id = _resolve_destination(c)
        meta["configured"] = bool(project_id)
        if not project_id:
            return [], [], meta

        spend_rows: list[tuple[str, str, date, float]] = []
        sessions_rows: list[tuple[str, date, int]] = []
        try:
            spend_rows, paid_kpi = _client_daily_rows(
                slug=slug, project_id=project_id, dataset_id=dataset_id,
                start=spend_start, end=today, kpi_start=kpi_start, kpi_end=kpi_end,
            )
            meta["spend_available"] = True
            meta["kpi_inputs"]["paid_mtd"] = paid_kpi
        except Exception:
            LOGGER.exception("Agency overview: spend read failed for %s", slug)
        try:
            sessions_rows = _client_sessions_rows(
                slug=slug, project_id=project_id, dataset_id=dataset_id,
                start=sessions_start, end=sessions_end,
            )
            meta["sessions_available"] = True
        except Exception:
            LOGGER.exception("Agency overview: sessions read failed for %s", slug)
        return spend_rows, sessions_rows, meta

    spend_all: list[tuple[str, str, date, float]] = []
    sessions_all: list[tuple[str, date, int]] = []
    metas: list[dict[str, Any]] = []
    if clients_cfg:
        with ThreadPoolExecutor(max_workers=min(len(clients_cfg), _MAX_WORKERS)) as ex:
            for spend_rows, sessions_rows, meta in ex.map(_work, clients_cfg):
                spend_all.extend(spend_rows)
                sessions_all.extend(sessions_rows)
                metas.append(meta)

    budget = compute_agency_budget(spend_all, sessions_all, metas, today=today)
    label_map = {m["client_slug"]: m["label"] for m in metas}
    momentum = compute_agency_trends(spend_all, label_map, today=today)

    # Fold each client's primary KPI onto its budget row. The resolver is pure —
    # it reads the paid aggregate / MQL count gathered above — and grades progress
    # against the share of a monthly goal the selected window represents (a full
    # month-to-date uses the share of the month elapsed; a complete week is ~7/30
    # of the goal), so a client on monthly pace reads on-pace for shorter windows.
    days_in_month = int(budget.get("days_in_month") or 30) or 30
    expected_pct = round(min(100.0, 100.0 * kpi_window_days / days_in_month), 1)
    kpi_by_slug = {m["client_slug"]: m for m in metas}
    for row in budget["clients"]:
        m = kpi_by_slug.get(str(row["client_slug"]))
        inputs = (m or {}).get("kpi_inputs") or {}
        row["kpi"] = kpi_registry.resolve_kpi(
            (m or {}).get("primary_kpi_spec"),
            paid_mtd=inputs.get("paid_mtd") or {},
            mql_count=inputs.get("mql_count"),
            pct_month_elapsed=expected_pct,
            window_label=kpi_window_label,
        )

    result = {
        **budget,
        "momentum": momentum,
        "kpi_window": {
            "range": kpi_range,
            "label": kpi_window_label,
            "start": kpi_start.isoformat(),
            "end": kpi_end.isoformat(),
            "include_today": bool(include_today),
        },
    }
    try:
        db_cache.put_cached(
            _OVERVIEW_CACHE_SOURCE, cache_key, response_json=result,
            row_count=len(result["clients"]), ttl_seconds=_CACHE_TTL_SECONDS,
        )
    except Exception:
        pass
    return result
