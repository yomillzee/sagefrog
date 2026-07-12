"""Agency Trends: cross-client paid-media momentum, computed with DuckDB.

The HQ page answers "how is each client pacing" by looping over clients and
reading each one's BigQuery paid-media mart. It cannot cheaply answer
*agency-wide* questions — "which clients slowed down this week", "where is spend
concentrated by channel" — because those need every client's daily series lined
up side by side.

This service does exactly that. Paid-media spend lives in each client's own
BigQuery mart (``vw_paid_media_daily`` in their ``marketing_marts`` dataset —
the very source HQ and the dashboards read), one project per client, so there is
no single table to scan. We fetch each client's trailing daily series
concurrently (same cached path HQ uses), then hand the combined set to an
in-process DuckDB database and compute the week-over-week rollup and channel mix
in one columnar query.

Why DuckDB rather than summing in Python: once every client's daily rows are in
one place, the interesting cuts — last-7 vs prior-7 per client, channel mix,
and the per-source pivots we'll want next — are one SQL each with FILTER buckets
and window functions, instead of hand-rolled dict aggregation. DuckDB runs fully
in-process (no server, no extension download), so it stays deploy-safe.
"""

from __future__ import annotations

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
_CACHE_SOURCE = "agency.trends"
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


def build_agency_trends() -> dict[str, Any]:
    """Full agency-trends payload for the admin page. Cached like the HQ read."""
    _, _, today = mtd_calendar_bounds()
    last_lo, last_hi, prior_lo, prior_hi = _window_bounds(today)

    cache_key = {"today": today.isoformat()}
    hit = db_cache.get_cached(_CACHE_SOURCE, cache_key)
    if hit is not None:
        return hit.response_json

    clients_cfg = client_dashboard_config.list_budget_overview()
    label_map = {
        str(c["client_slug"]): str(c.get("label") or c["client_slug"]) for c in clients_cfg
    }

    def _work(c: dict[str, Any]) -> list[tuple[str, str, date, float]]:
        project_id = c.get("gcp_project_id")
        if not project_id:
            return []
        dataset_id = c.get("bq_mart_dataset_id") or "marketing_marts"
        try:
            return _client_daily_rows(
                slug=str(c["client_slug"]), project_id=project_id, dataset_id=dataset_id,
                start=prior_lo, end=last_hi,
            )
        except Exception:
            LOGGER.exception("Agency trends: daily read failed for %s", c.get("client_slug"))
            return []

    daily_rows: list[tuple[str, str, date, float]] = []
    if clients_cfg:
        with ThreadPoolExecutor(max_workers=min(len(clients_cfg), _MAX_WORKERS)) as ex:
            for rows in ex.map(_work, clients_cfg):
                daily_rows.extend(rows)

    result = compute_agency_trends(daily_rows, label_map, today=today)
    try:
        db_cache.put_cached(
            _CACHE_SOURCE, cache_key, response_json=result, row_count=len(result["clients"]),
            ttl_seconds=_CACHE_TTL_SECONDS,
        )
    except Exception:
        pass
    return result
