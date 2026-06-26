"""One-shot cron worker: POST /internal/sync-hubspot on the sagefrog API.

Mirrors the pattern used by cron-sync-bq.  Set HUBSPOT_SYNC_LOOKBACK_DAYS
to override the default 7-day rolling window (e.g. 180 for initial backfill).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def _env(key: str) -> str:
    v = (os.getenv(key) or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1].strip()
    return v


def main() -> int:
    secret = _env("CRON_SECRET")
    if not secret:
        print("CRON_SECRET is not set on the cron service.", file=sys.stderr)
        return 1

    base         = _env("SYNC_BASE_URL") or "https://sagefrog-production.up.railway.app"
    lookback     = _env("HUBSPOT_SYNC_LOOKBACK_DAYS") or "7"
    url          = f"{base.rstrip('/')}/internal/sync-hubspot?lookback_days={lookback}"

    print(f"POST {url}", flush=True)
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"X-Cron-Secret": secret, "Accept": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            body    = resp.read().decode("utf-8", errors="replace")
            elapsed = time.monotonic() - t0
            print(f"HTTP {resp.status} in {elapsed:.1f}s", flush=True)
            print(body)
            if resp.status >= 400:
                return 1
            try:
                data = json.loads(body)
                sync = data.get("hubspot_sync") or {}
                print(
                    f"rows_synced={sync.get('rows_synced')} "
                    f"pages={sync.get('pages')} "
                    f"elapsed_s={sync.get('elapsed_s')}",
                    flush=True,
                )
            except json.JSONDecodeError:
                pass
            return 0
    except urllib.error.HTTPError as exc:
        elapsed  = time.monotonic() - t0
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code} failed after {elapsed:.1f}s", file=sys.stderr)
        print(err_body or str(exc), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
