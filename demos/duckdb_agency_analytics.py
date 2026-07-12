"""DuckDB agency-analytics demo — cross-client rollups in one query.

Run:  python demos/duckdb_agency_analytics.py   (pip install duckdb)

WHY THIS IS INTERESTING
-----------------------
Today the HQ page answers "how is each client pacing" by looping over clients and
doing per-client BigQuery reads. It CANNOT cheaply answer agency-wide questions
like "rank every client by week-over-week spend change, biggest drops first"
without even more per-client queries.

DuckDB is an in-process analytical database (think "SQLite for analytics"). It's
a Python library — no server. It reads your existing Postgres `metrics_daily`
directly (via its `postgres` extension) and runs one columnar query with window
functions over ALL clients at once. This script uses synthetic in-memory data
shaped exactly like `metrics_daily` so you can see the pattern and the output
without a database.

In production the only change is the data source: instead of the two synthetic
`CREATE TABLE` blocks below, you'd do:

    duckdb.sql("INSTALL postgres; LOAD postgres;")
    duckdb.sql("ATTACH '<DATABASE_URL>' AS pg (TYPE postgres, READ_ONLY);")
    # then query pg.metrics_daily directly — no copy, no ETL.
"""

from __future__ import annotations

import datetime as dt

import duckdb

con = duckdb.connect()  # in-process, in-memory

# --- Synthetic stand-ins for two tables you already have ---------------------
# 1) metrics_daily: exact shape of railway/app warehouse.py (keyed by account).
# 2) account_client_map: the account_id -> client mapping that today lives in
#    your connector config. The real value of DuckDB here is doing this join
#    across ALL clients in one scan.

con.execute("""
    CREATE TABLE metrics_daily (
        source        TEXT,
        account_id    TEXT,
        metric_date   DATE,
        spend         DOUBLE,
        clicks        BIGINT,
        impressions   BIGINT,
        conversions   BIGINT
    );
    CREATE TABLE account_client_map (
        account_id  TEXT,
        client_slug TEXT,
        label       TEXT
    );
""")

con.execute("""
    INSERT INTO account_client_map VALUES
        ('acc-penn-g',  'penn',   'Penn Community Bank'),
        ('acc-penn-li', 'penn',   'Penn Community Bank'),
        ('acc-acme-g',  'acme',   'Acme Manufacturing'),
        ('acc-acme-m',  'acme',   'Acme Manufacturing'),
        ('acc-globex',  'globex', 'Globex Health'),
        ('acc-initech', 'initech','Initech Legal');
""")

# Generate 14 days of daily rows per account. "prior week" spends are set higher
# than "last week" for some clients so the WoW query has real winners & losers.
today = dt.date(2026, 7, 12)
# account_id: (source, prior-week daily spend, last-week daily spend)
plan = {
    "acc-penn-g":   ("google",   900,  1150),   # Penn trending UP
    "acc-penn-li":  ("linkedin", 300,   360),
    "acc-acme-g":   ("google",  1200,   700),   # Acme trending DOWN hard
    "acc-acme-m":   ("meta",     500,   380),
    "acc-globex":   ("google",   600,   610),   # ~flat
    "acc-initech":  ("google",   450,   250),   # Initech trending DOWN
}
rows = []
for account_id, (source, prior, last) in plan.items():
    for d in range(14):
        day = today - dt.timedelta(days=13 - d)
        daily = last if d >= 7 else prior          # days 7..13 = "last 7 days"
        rows.append((source, account_id, day, float(daily),
                     daily // 3, daily * 40, max(1, daily // 200)))
con.executemany(
    "INSERT INTO metrics_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows
)

# --- THE PAYOFF: one query, every client, week-over-week ----------------------
# FILTER (WHERE ...) buckets each client's spend into last-7 vs prior-7 in a
# single scan; the join folds account-level rows up to the client. This is the
# query you cannot do cheaply today without N more per-client reads.
print("\n=== Agency spend: week-over-week, biggest drops first ===\n")
wow = con.sql("""
    SELECT
        m.label AS client,
        ROUND(SUM(md.spend) FILTER (
            WHERE md.metric_date > current_date - INTERVAL 7 DAY), 0) AS last_7,
        ROUND(SUM(md.spend) FILTER (
            WHERE md.metric_date <= current_date - INTERVAL 7 DAY
              AND md.metric_date >  current_date - INTERVAL 14 DAY), 0) AS prior_7,
        ROUND(100.0 * (
            SUM(md.spend) FILTER (WHERE md.metric_date > current_date - INTERVAL 7 DAY)
          - SUM(md.spend) FILTER (WHERE md.metric_date <= current_date - INTERVAL 7 DAY
                                    AND md.metric_date >  current_date - INTERVAL 14 DAY)
        ) / NULLIF(SUM(md.spend) FILTER (
            WHERE md.metric_date <= current_date - INTERVAL 7 DAY
              AND md.metric_date >  current_date - INTERVAL 14 DAY), 0), 1) AS pct_change
    FROM metrics_daily md
    JOIN account_client_map m USING (account_id)
    GROUP BY m.label
    ORDER BY pct_change ASC          -- steepest decline at the top
""")
wow.show()

# --- Bonus: agency-wide channel mix, also one scan ---------------------------
print("\n=== Agency-wide spend by channel (last 7 days) ===\n")
con.sql("""
    SELECT
        source AS channel,
        ROUND(SUM(spend), 0) AS spend,
        ROUND(100.0 * SUM(spend) / SUM(SUM(spend)) OVER (), 1) AS pct_of_total
    FROM metrics_daily
    WHERE metric_date > current_date - INTERVAL 7 DAY
    GROUP BY source
    ORDER BY spend DESC
""").show()
