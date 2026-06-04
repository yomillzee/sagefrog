"""One-shot cron worker: POST /internal/sync-penn on the sagefrog API service."""

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

    base = _strip_env(os.getenv("SYNC_BASE_URL")) or "https://sagefrog-production.up.railway.app"
    date_range = _strip_env(os.getenv("SYNC_DATE_RANGE")) or "LAST_30_DAYS"
    url = f"{base.rstrip('/')}/internal/sync-penn?date_range={date_range}"

    print(f"POST {url}", flush=True)
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"X-Cron-Secret": secret, "Accept": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed = time.monotonic() - started
            print(f"HTTP {resp.status} in {elapsed:.1f}s", flush=True)
            print(body)
            if resp.status >= 400:
                return 1
            try:
                data = json.loads(body)
                meta = data.get("sync_meta") or {}
                print(
                    f"sync_meta: trigger={meta.get('trigger')} completed_at={meta.get('completed_at')}",
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
