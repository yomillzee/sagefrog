"""CLI wrapper for GSC → BigQuery backfill.

The sync logic lives in gsc_sync_service.py and runs automatically during
each dashboard refresh. Use this script only when you need to force a full
historical backfill outside of the normal refresh cycle, e.g.:

    python gsc_backfill.py                          # full history (480 days)
    python gsc_backfill.py --start-date 2025-01-01  # from a specific date
    python gsc_backfill.py --table pages            # pages only
    python gsc_backfill.py --dry-run                # count rows, no writes

Required env vars:
    GCP_CREDS_PENN_BASE64   base64 service account JSON
    GSC_SITE_URL            sc-domain:penncommunitybank.com
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

_GSC_LAG_DAYS = 3
_MAX_DAYS      = 480


def main() -> None:
    parser = argparse.ArgumentParser(description="Force a GSC → BigQuery backfill")
    parser.add_argument("--start-date", metavar="YYYY-MM-DD")
    parser.add_argument("--end-date",   metavar="YYYY-MM-DD")
    parser.add_argument(
        "--table", choices=["both", "queries", "pages"], default="both",
    )
    parser.add_argument(
        "--client-slug", default=None,
        help="Route into this client's BQ destination (GSC_CLIENTS registry). Omit for legacy Penn behaviour.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch from GSC but skip BigQuery writes")
    args = parser.parse_args()

    if not os.getenv("GSC_SITE_URL"):
        sys.exit(
            "ERROR: GSC_SITE_URL is not set.\n"
            "  Domain property : GSC_SITE_URL=sc-domain:penncommunitybank.com\n"
            "  URL-prefix      : GSC_SITE_URL=https://www.penncommunitybank.com/"
        )

    today      = date.today()
    lag_cutoff = today - timedelta(days=_GSC_LAG_DAYS)

    end_date = date.fromisoformat(args.end_date) if args.end_date else lag_cutoff
    if end_date > lag_cutoff:
        print(f"WARNING: capping end_date to {lag_cutoff}")
        end_date = lag_cutoff

    start_date = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else today - timedelta(days=_MAX_DAYS)
    )

    total_days = (end_date - start_date).days + 1
    print(
        f"GSC backfill: {start_date} → {end_date} ({total_days} days)\n"
        f"  site    : {os.getenv('GSC_SITE_URL')}\n"
        f"  tables  : {args.table}\n"
        f"  dry_run : {args.dry_run}\n"
    )

    if args.dry_run:
        print("DRY RUN — no writes will happen. Counting rows only...")

    import gsc_sync_service

    if args.dry_run:
        # Dry run: just count what would be written without actually writing
        from datetime import timedelta as td
        import gsc_sync_service as svc
        creds = svc._gsc_creds()
        total_q = total_p = 0
        d = start_date
        do_q = args.table in ("both", "queries")
        do_p = args.table in ("both", "pages")
        while d <= end_date:
            i = (d - start_date).days + 1
            print(f"[{i:>4}/{total_days}] {d}", end="", flush=True)
            q_n = p_n = 0
            if do_q:
                rows = svc._fetch_day(creds, os.getenv("GSC_SITE_URL"), d, "query")
                q_n = len(rows)
                total_q += q_n
            if do_p:
                rows = svc._fetch_day(creds, os.getenv("GSC_SITE_URL"), d, "page")
                p_n = len(rows)
                total_p += p_n
            print(f"  q:{q_n:>5}  p:{p_n:>4}  DRY")
            d += td(days=1)
        print(f"\nDry run complete. Would write: {total_q:,} query rows, {total_p:,} page rows")
        return

    i = 0

    def _progress(d: date, total: int) -> None:
        nonlocal i
        i += 1
        print(f"[{i:>4}/{total}] {d}", flush=True)

    result = gsc_sync_service.sync_range(
        start_date, end_date, which=args.table, progress_cb=_progress, client_slug=args.client_slug
    )

    print(
        f"\nFinished.\n"
        f"  Query rows written : {result.get('query_rows', 0):,}\n"
        f"  Page  rows written : {result.get('page_rows', 0):,}"
    )
    if result.get("errors"):
        print(f"  Errors ({len(result['errors'])}):")
        for e in result["errors"]:
            print(f"    {e}")


if __name__ == "__main__":
    main()
