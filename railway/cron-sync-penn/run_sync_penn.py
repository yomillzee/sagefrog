"""One-shot cron worker: POST /internal/sync-penn on the sagefrog API service."""

from __future__ import annotations

import json
import os
import sys
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

    req = urllib.request.Request(
        url,
        method="POST",
        headers={"X-Cron-Secret": secret, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(body)
            if resp.status >= 400:
                return 1
            return 0
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(err_body or str(exc), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
