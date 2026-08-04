#!/usr/bin/env python3
"""Seed the staging database from production — real data, no secrets, zero BigQuery.

Staging serves **real** client numbers by copying production's already-cached
dashboard reads (and the config that shapes them) straight from prod's Postgres
into staging's. Because it copies rows prod already paid BigQuery for, this makes
**no BigQuery calls at all**. Combined with a long ``DASH_CACHE_TTL_SECONDS`` and
``DASH_DISABLE_LIVE_FETCH=1`` on staging, the seeded cache stays warm and staging
never queries BigQuery.

What it copies vs. skips is a deliberate policy (see COPY_TABLES / SKIP_TABLES):
real marketing data and non-secret config are copied; auth, tokens, audit logs,
and internal notes are **never** copied. Staging gets its own admin via the
``AUTH_BOOTSTRAP_ADMIN_*`` env vars at app boot, not from this copy.

Usage
-----
    # dry run — connects, prints the plan and source row counts, writes nothing
    PROD_DATABASE_URL=... STAGING_DATABASE_URL=... python seed_staging.py

    # actually copy (TRUNCATES the copied tables on staging first)
    PROD_DATABASE_URL=... STAGING_DATABASE_URL=... python seed_staging.py --yes

Run it as a Railway one-shot in the staging environment, or locally with both
URLs exported. Re-run any time to refresh staging with prod's latest numbers.

Safety
------
- Default is a **dry run**; nothing is written without ``--yes``.
- Refuses to run if source and target URLs are identical (can't seed prod from
  prod).
- Only ever writes to the tables in COPY_TABLES, and only after TRUNCATE-ing
  each; it never touches SKIP_TABLES on the target.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

# --- Copy policy --------------------------------------------------------------
# Copied AS-IS: the real dashboard data + the non-secret config that shapes it.
COPY_TABLES: list[str] = [
    # Real dashboard data (the accuracy).
    "api_cache",                    # cached BigQuery dashboard reads — the core
    "dashboard_snapshots",          # full per-client dashboard JSON
    "metrics_daily",                # daily metrics warehouse
    "campaign_daily",               # per-campaign daily metrics
    # Client registry + display config.
    "dashboard_clients",
    "client_dashboard_config",
    "client_groups",
    "dashboard_client_suppressions",
    # Routing / classification config (non-secret).
    "client_business_line_rules",
    "client_ga",
    "client_gsc_config",
    "connector_configs",            # account/routing config; tokens live elsewhere
    # Goals / Harvest config.
    "harvest_client_goals",
    "harvest_client_owners",
    "harvest_project_tags",
    # Migration state, so staging starts prod-shaped (the point of rehearsals).
    "schema_migrations",
]

# NEVER copied: PII, secrets, per-user, audit, operational, internal notes.
# Kept here explicitly so the policy is auditable and a reviewer can see exactly
# what is withheld from staging.
SKIP_TABLES: list[str] = [
    "web_users",                    # emails + password hashes → staging bootstraps its own admin
    "oauth_credentials",            # live connector tokens → staging runs no live connectors
    "app_settings",                 # instance settings/updated_by → staging uses env vars
    "audit_events",
    "login_rate_buckets",
    "connector_sync_runs",
    "cron_locks",
    "admin_dev_notes",
    "client_notepads",
    "client_insight_documents",
    "client_insight_folders",
    "feature_requests",
    "client_hours_share_links",     # share tokens
    "consent_scan_configs",
    "consent_scan_runs",
]


def _source_url() -> str | None:
    return (os.getenv("PROD_DATABASE_URL") or os.getenv("SOURCE_DATABASE_URL") or "").strip() or None


def _target_url() -> str | None:
    return (os.getenv("STAGING_DATABASE_URL") or os.getenv("TARGET_DATABASE_URL") or "").strip() or None


def _describe(url: str) -> str:
    """host/dbname for a connection, for human-readable confirmation (no creds)."""
    try:
        info = psycopg.conninfo.conninfo_to_dict(url)
        return f"{info.get('host', '?')}/{info.get('dbname', '?')}"
    except Exception:
        return "<unparseable url>"


def _table_exists(conn: psycopg.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()[0])


def _count(conn: psycopg.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _copy_table(src: psycopg.Connection, dst: psycopg.Connection, table: str) -> None:
    """Stream every row of `table` from src to dst using server-side COPY (text).

    Text COPY is portable across Postgres versions and streams block-by-block, so
    even a large api_cache/metrics_daily copies without buffering the whole table
    in memory.
    """
    with src.cursor().copy(f'COPY "{table}" TO STDOUT') as reader:
        with dst.cursor().copy(f'COPY "{table}" FROM STDIN') as writer:
            for chunk in reader:
                writer.write(chunk)


def _reset_sequences(dst: psycopg.Connection, table: str) -> None:
    """After a copy with explicit ids, advance any owned sequence past MAX(id).

    COPY inserts the source's id values verbatim, which leaves the target's
    BIGSERIAL sequence behind them; the next app INSERT would then collide. This
    bumps each owned sequence to MAX(col) so future inserts on staging are safe.
    """
    cols = dst.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    ).fetchall()
    for (col,) in cols:
        seq = dst.execute("SELECT pg_get_serial_sequence(%s, %s)", (f"public.{table}", col)).fetchone()[0]
        if seq:
            dst.execute(
                f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{table}"), 1))',
                (seq,),
            )


def _clear(dst: psycopg.Connection, table: str) -> None:
    """Empty the target table, preferring TRUNCATE but falling back to DELETE if a
    foreign-key reference blocks TRUNCATE."""
    try:
        with dst.transaction():
            dst.execute(f'TRUNCATE TABLE "{table}"')
    except psycopg.errors.Error:
        with dst.transaction():
            dst.execute(f'DELETE FROM "{table}"')


def run(*, apply: bool) -> int:
    src_url, dst_url = _source_url(), _target_url()
    if not src_url or not dst_url:
        print(
            "ERROR: set PROD_DATABASE_URL (source) and STAGING_DATABASE_URL (target).",
            file=sys.stderr,
        )
        return 2
    if src_url == dst_url:
        print("ERROR: source and target are identical — refusing to seed prod from prod.", file=sys.stderr)
        return 2

    print(f"source (prod)   : {_describe(src_url)}")
    print(f"target (staging): {_describe(dst_url)}")
    print(f"mode            : {'APPLY (will TRUNCATE + copy)' if apply else 'DRY RUN (no writes)'}")
    print(f"skipped (never copied): {', '.join(SKIP_TABLES)}")
    print("-" * 72)

    copied = skipped = 0
    with psycopg.connect(src_url, autocommit=True) as src, psycopg.connect(dst_url, autocommit=True) as dst:
        for table in COPY_TABLES:
            if not _table_exists(src, table):
                print(f"  skip  {table:<32} (not present in source)")
                skipped += 1
                continue
            if not _table_exists(dst, table):
                print(f"  skip  {table:<32} (not present in target — boot the staging app once first)")
                skipped += 1
                continue

            src_rows = _count(src, table)
            if not apply:
                print(f"  plan  {table:<32} would copy {src_rows} rows")
                continue

            _clear(dst, table)
            with dst.transaction():
                _copy_table(src, dst, table)
                _reset_sequences(dst, table)
            print(f"  copy  {table:<32} {src_rows} rows -> {_count(dst, table)} rows")
            copied += 1

    print("-" * 72)
    if apply:
        print(f"done: copied {copied} tables, skipped {skipped}.")
    else:
        print("dry run complete — re-run with --yes to apply.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually copy (TRUNCATES the copied tables on staging). Without it, dry run only.",
    )
    args = parser.parse_args()
    return run(apply=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
