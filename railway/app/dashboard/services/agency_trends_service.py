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

    ``spend_rows``    : (client_slug, source, metric_date, spend).
    ``sessions_rows`` : (client_slug, metric_date, sessions).
    ``clients``       : ordered client rows, each with client_slug, label,
                        monthly_budget, and spend_available / sessions_available
                        flags (whether that client's BigQuery read succeeded).
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
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    tot_budget = 0.0
    tot_spend = 0.0
    tot_projected = 0.0
    n_over = 0
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
        })

        tot_budget += budget
        if spend_available:
            tot_spend += mtd
            if projected is not None:
                tot_projected += projected
            if status == "over":
                n_over += 1

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
            "client_count": len(rows),
        },
        "clients": rows,
    }


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
    payload = {"start": start.isoformat(), "end": end.isoformat()}

    def _fetch() -> dict[str, Any]:
        with marketing_service.route(
            client_key=slug, project_id=project_id, mart_dataset_id=dataset_id
        ):
            return marketing_service.fetch_sessions_daily(start_date=start, end_date=end)

    hit = db_cache.get_cached(f"{slug}.hq.sessions_daily", payload)
    result = hit.response_json if hit is not None else _fetch()
    if hit is None:
        try:
            db_cache.put_cached(
                f"{slug}.hq.sessions_daily", payload, response_json=result,
                row_count=0, ttl_seconds=_CLIENT_TTL_SECONDS,
            )
        except Exception:
            pass

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
) -> list[tuple[str, str, date, float]]:
    """One client's daily paid-media spend by channel, cached like /summary.

    Reuses marketing_service.fetch_summary — the same BigQuery read HQ and the
    dashboards use — and keeps only its per-day/per-source ``daily`` series.
    """
    payload = {"start": start.isoformat(), "end": end.isoformat()}

    def _fetch() -> dict[str, Any]:
        with marketing_service.route(
            client_key=slug, project_id=project_id, mart_dataset_id=dataset_id
        ):
            return marketing_service.fetch_summary(start_date=start, end_date=end)

    hit = db_cache.get_cached(f"{slug}.trends.summary", payload)
    result = hit.response_json if hit is not None else _fetch()
    if hit is None:
        try:
            db_cache.put_cached(
                f"{slug}.trends.summary", payload, response_json=result,
                row_count=0, ttl_seconds=_CLIENT_TTL_SECONDS,
            )
        except Exception:
            pass

    rows: list[tuple[str, str, date, float]] = []
    for r in (result or {}).get("daily") or []:
        raw_date = r.get("date")
        if not raw_date:
            continue
        try:
            d = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append((slug, str(r.get("source") or "unknown"), d, float(r.get("spend") or 0.0)))
    return rows


def _resolve_destination(c: dict[str, Any]) -> tuple[str | None, str]:
    """(project_id, dataset_id) for a client's paid-media mart.

    Mirrors hq_budget_service: Nixon's mart lives in marketing_service module
    defaults rather than on its config row, so resolve that built-in destination
    when the row has no project — otherwise Nixon would score no spend here just
    as it once did on HQ.
    """
    project_id = c.get("gcp_project_id")
    dataset_id = c.get("bq_mart_dataset_id") or "marketing_marts"
    slug = str(c["client_slug"])
    if not project_id and slug in ("nixon", "nixon-bq-test"):
        project_id, dataset_id = marketing_service.default_destination()
    return project_id, dataset_id


def build_agency_overview() -> dict[str, Any]:
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

    Reading spend and sessions once per client (rather than the HQ page and the
    trends page each doing their own reads) is the load-time and BigQuery-usage
    win: the columnar rollups are cheap once the daily rows are in DuckDB.
    """
    _, _, today = mtd_calendar_bounds()
    month_start = today.replace(day=1)
    _, _, prior_lo, _ = _window_bounds(today)
    # Widest window either view needs: MTD spend starts at month_start, the
    # week-over-week prior window starts at prior_lo, whichever is earlier.
    spend_start = min(month_start, prior_lo)
    sessions_end = today - timedelta(days=1)
    sessions_start = sessions_end - timedelta(days=_SESSIONS_TRAILING_DAYS - 1)

    cache_key = {"today": today.isoformat()}
    hit = db_cache.get_cached(_OVERVIEW_CACHE_SOURCE, cache_key)
    if hit is not None:
        return hit.response_json

    clients_cfg = client_dashboard_config.list_budget_overview()

    def _work(
        c: dict[str, Any],
    ) -> tuple[list[tuple[str, str, date, float]], list[tuple[str, date, int]], dict[str, Any]]:
        slug = str(c["client_slug"])
        meta: dict[str, Any] = {
            "client_slug": slug,
            "label": str(c.get("label") or slug),
            "monthly_budget": float(c.get("monthly_budget_usd") or 0.0),
            "spend_available": False,
            "sessions_available": False,
        }
        project_id, dataset_id = _resolve_destination(c)
        if not project_id:
            return [], [], meta

        spend_rows: list[tuple[str, str, date, float]] = []
        sessions_rows: list[tuple[str, date, int]] = []
        try:
            spend_rows = _client_daily_rows(
                slug=slug, project_id=project_id, dataset_id=dataset_id,
                start=spend_start, end=today,
            )
            meta["spend_available"] = True
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

    result = {**budget, "momentum": momentum}
    try:
        db_cache.put_cached(
            _OVERVIEW_CACHE_SOURCE, cache_key, response_json=result,
            row_count=len(result["clients"]), ttl_seconds=_CACHE_TTL_SECONDS,
        )
    except Exception:
        pass
    return result
