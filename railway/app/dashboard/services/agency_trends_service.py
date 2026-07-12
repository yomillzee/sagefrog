"""Agency Trends: cross-client paid-media momentum, computed with DuckDB.

The HQ page answers "how is each client pacing" by looping over clients and
reading each one's BigQuery mart. It cannot cheaply answer *agency-wide*
questions — "which clients slowed down this week", "where is spend concentrated
by channel" — because those need one scan over every client at once.

This service does exactly that. It reads two tables that already live in
Postgres — ``metrics_daily`` (daily paid spend per source/account) and
``connector_configs`` (which account belongs to which client) — and runs a
single columnar query over the joined set in an in-process DuckDB database.

Why DuckDB and not the platform's own psycopg + SQL: for *this* 14-day rollup
plain Postgres could do the job too. DuckDB earns its place as the analytics
surface grows — window functions, per-source pivots, reading Parquet/BigQuery
exports alongside Postgres — and keeps that heavier analytical SQL out of the
request-path Postgres connection. It runs fully in-process (no server, no
extension download), so it stays deployment-safe on Railway: we read the rows
with the app's normal pooled psycopg helper and hand them to DuckDB in memory.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import duckdb

import client_dashboard_config
import db_cache
from dashboard.utils.dates import mtd_calendar_bounds

LOGGER = logging.getLogger(__name__)

# Trailing comparison window: 7 full days vs the 7 before them. Ends *yesterday*
# because platform syncs lag a day, so "today" is usually partial and would read
# as a false dip (same reasoning the HQ sessions sparkline uses).
_WINDOW_DAYS = 7
_CACHE_SOURCE = "agency.trends"
_CACHE_TTL_SECONDS = 900
_UNATTRIBUTED = "__unattributed__"

# Only paid-media sources carry spend; ga4 rows are analytics (spend 0/NULL) and
# would just be noise in a spend view.
_PAID_SOURCES = ("google", "linkedin", "meta")


def _window_bounds(today: date) -> tuple[date, date, date, date]:
    """(last_lo, last_hi, prior_lo, prior_hi) for the two 7-day windows."""
    last_hi = today - timedelta(days=1)                 # yesterday
    last_lo = last_hi - timedelta(days=_WINDOW_DAYS - 1)
    prior_hi = last_lo - timedelta(days=1)
    prior_lo = prior_hi - timedelta(days=_WINDOW_DAYS - 1)
    return last_lo, last_hi, prior_lo, prior_hi


def compute_agency_trends(
    metrics_rows: list[tuple[str, str, date, float]],
    mapping_rows: list[tuple[str, str]],
    label_map: dict[str, str],
    *,
    today: date,
) -> dict[str, Any]:
    """Pure DuckDB computation — no database access, so it is easy to test.

    ``metrics_rows``  : (source, account_id, metric_date, spend)
    ``mapping_rows``  : (account_id, client_slug)
    ``label_map``     : client_slug -> display label
    """
    last_lo, last_hi, prior_lo, prior_hi = _window_bounds(today)

    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE md (source TEXT, account_id TEXT, metric_date DATE, spend DOUBLE)"
        )
        con.execute("CREATE TABLE acct_map (account_id TEXT, client_slug TEXT)")
        if metrics_rows:
            con.executemany("INSERT INTO md VALUES (?, ?, ?, ?)", metrics_rows)
        if mapping_rows:
            con.executemany("INSERT INTO acct_map VALUES (?, ?)", mapping_rows)

        # Per client: last-7 vs prior-7 spend in one scan. LEFT JOIN so spend on
        # an account with no connector match is surfaced as "unattributed"
        # rather than silently dropped.
        params = [last_lo, last_hi, prior_lo, prior_hi]
        client_rows = con.execute(
            """
            SELECT COALESCE(m.client_slug, ?) AS slug,
                   SUM(md.spend) FILTER (WHERE md.metric_date BETWEEN ? AND ?) AS last_7,
                   SUM(md.spend) FILTER (WHERE md.metric_date BETWEEN ? AND ?) AS prior_7
            FROM md
            LEFT JOIN acct_map m USING (account_id)
            GROUP BY 1
            """,
            [_UNATTRIBUTED, last_lo, last_hi, prior_lo, prior_hi],
        ).fetchall()

        channel_rows = con.execute(
            """
            SELECT source, SUM(spend) AS spend
            FROM md
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
        unattributed = slug == _UNATTRIBUTED
        clients.append({
            "client_slug": None if unattributed else slug,
            "label": "Unattributed spend" if unattributed else label_map.get(slug, slug),
            "unattributed": unattributed,
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
            "source": src,
            "spend": round(float(spend or 0.0), 2),
            "pct_of_total": round(100.0 * float(spend or 0.0) / channel_total, 1) if channel_total else 0.0,
        }
        for src, spend in channel_rows
    ]

    agency_pct = round(100.0 * (tot_last - tot_prior) / tot_prior, 1) if tot_prior > 0 else None
    return {
        "as_of": (today - timedelta(days=1)).isoformat(),
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


def _fetch_rows(start: date, end: date):
    """Read the two Postgres tables the computation joins. Returns
    (metrics_rows, mapping_rows). Uses the app's pooled psycopg helper."""
    import db

    with db.connection() as conn:
        metrics = conn.execute(
            """
            SELECT source, account_id, metric_date, COALESCE(spend, 0)
            FROM metrics_daily
            WHERE metric_date BETWEEN %s AND %s
              AND source = ANY(%s)
            """,
            (start, end, list(_PAID_SOURCES)),
        ).fetchall()
        mapping = conn.execute(
            """
            SELECT source_account_id, client_slug
            FROM connector_configs
            WHERE source_account_id IS NOT NULL AND source_account_id <> ''
            """
        ).fetchall()

    metrics_rows = [
        (str(r[0]), str(r[1]), r[2], float(r[3] or 0.0)) for r in metrics
    ]
    mapping_rows = [(str(r[0]), str(r[1])) for r in mapping]
    return metrics_rows, mapping_rows


def _label_map() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for c in client_dashboard_config.list_budget_overview():
            slug = c.get("client_slug")
            if slug:
                out[str(slug)] = str(c.get("label") or slug)
    except Exception:
        LOGGER.exception("Agency trends: could not load client labels")
    return out


def build_agency_trends() -> dict[str, Any]:
    """Full agency-trends payload for the admin page. Cached like the HQ read."""
    _, _, today = mtd_calendar_bounds()
    last_lo, _, prior_lo, _ = _window_bounds(today)

    cache_key = {"today": today.isoformat()}
    hit = db_cache.get_cached(_CACHE_SOURCE, cache_key)
    if hit is not None:
        return hit.response_json

    try:
        metrics_rows, mapping_rows = _fetch_rows(prior_lo, today - timedelta(days=1))
    except Exception:
        LOGGER.exception("Agency trends: metrics/mapping read failed")
        metrics_rows, mapping_rows = [], []

    result = compute_agency_trends(
        metrics_rows, mapping_rows, _label_map(), today=today
    )
    try:
        db_cache.put_cached(
            _CACHE_SOURCE, cache_key, response_json=result, row_count=len(result["clients"]),
            ttl_seconds=_CACHE_TTL_SECONDS,
        )
    except Exception:
        pass
    return result
