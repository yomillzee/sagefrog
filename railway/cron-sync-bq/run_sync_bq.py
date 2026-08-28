"""One-shot cron worker: POST /internal/sync-bq/{CLIENT_SLUG} on the sagefrog API.

Generic BigQuery-mode client refresh. Mirrors cron-sync-penn, but targets the
generic /internal/sync-bq/{slug} endpoint so one codebase serves any client.

Hands-off mode: leave CLIENT_SLUG UNSET and this hits /internal/sync-bq-all
instead, which re-derives the full client list from connector_configs on
every run — connecting a source via the Connectors wizard is then the only
setup step; no per-client Railway cron provisioning needed. Set CLIENT_SLUG
only if you want a dedicated service scoped to one specific client.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def _strip_env(val: str | None) -> str:
    if not val:
        return ""
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1].strip()
    return v


def main() -> int:
    secret = _strip_env(os.getenv("CRON_SECRET"))
    if not secret:
        print("CRON_SECRET is not set on the cron service.", file=sys.stderr)
        return 1

    slug = _strip_env(os.getenv("CLIENT_SLUG"))
    base = _strip_env(os.getenv("SYNC_BASE_URL")) or "https://sagefrog-production.up.railway.app"
    date_range = _strip_env(os.getenv("SYNC_DATE_RANGE")) or "LAST_30_DAYS"

    # CRON_JOB selects which job this worker runs. Unset (or "sync-bq") keeps the
    # original BigQuery refresh behavior; "consent-scan-due" targets the Consent &
    # Tracking Health scheduled-scan endpoint, and "web-mentions" the Google Alerts
    # RSS ingest. Additional Railway cron services (same root dir, own schedule)
    # drive those jobs without a second codebase.
    job = (_strip_env(os.getenv("CRON_JOB")) or "sync-bq").lower()
    if job in ("consent", "consent-scan-due"):
        url = f"{base.rstrip('/')}/internal/consent/scan-due"
        slug = ""  # the consent endpoint is always hands-off (all due clients)
    elif job in ("web-mentions", "web-mentions-ingest"):
        url = f"{base.rstrip('/')}/internal/web-mentions/ingest-due"
        slug = ""  # polls every client that has an active Google Alerts feed
    elif slug:
        url = f"{base.rstrip('/')}/internal/sync-bq/{slug}?date_range={date_range}"
    else:
        url = f"{base.rstrip('/')}/internal/sync-bq-all?date_range={date_range}"

    print(f"POST {url}", flush=True)
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"X-Cron-Secret": secret, "Accept": "application/json"},
    )
    started = time.monotonic()
    # sync-bq-all queues the real work as a FastAPI background task and
    # returns immediately (see /internal/sync-bq-all) — the actual per-client
    # sync can run well past a few minutes once there's more than a handful
    # of clients, long enough to hit Railway's edge proxy timeout on a
    # synchronous response. The single-client path still runs synchronously
    # (that's existing, unbroken behavior), so it keeps the longer timeout.
    timeout = 900 if slug else 60
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed = time.monotonic() - started
            print(f"HTTP {resp.status} in {elapsed:.1f}s", flush=True)
            print(body)
            if resp.status >= 400:
                return 1
            try:
                data = json.loads(body)
                if slug:
                    run = data.get("refresh_run") or {}
                    print(
                        f"refresh_run: status={run.get('status')} date_range={run.get('date_range')}",
                        flush=True,
                    )
                elif job in ("web-mentions", "web-mentions-ingest"):
                    print(
                        f"{data.get('status')}: {data.get('clients')} client(s) with active "
                        "alerts — new mentions land on each client's Web Mentions page, "
                        "not here.",
                        flush=True,
                    )
                else:
                    print(
                        f"queued {data.get('clients_queued')} client(s): {data.get('slugs')} "
                        "— results land in each client's Connectors page, not here.",
                        flush=True,
                    )
            except json.JSONDecodeError:
                pass
            return 0
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - started
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code} failed after {elapsed:.1f}s", file=sys.stderr)
        print(err_body or str(exc), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
