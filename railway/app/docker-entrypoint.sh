#!/bin/sh
# Container entrypoint.
#
# On staging we auto-seed sample data so the app is usable end-to-end. This is
# gated on SEED_STAGING, so it is a complete no-op in production (where the var
# is unset). seed_staging.py has its own guards too — it refuses to run without
# SEED_STAGING and aborts on a production RAILWAY_ENVIRONMENT_NAME — so a
# mis-set env cannot seed prod. We also swallow any seed failure here so a
# transient hiccup (e.g. DB not ready yet) never stops the app from starting.
set -e

seed_flag=$(printf '%s' "${SEED_STAGING:-}" | tr '[:upper:]' '[:lower:]')
case "$seed_flag" in
  1 | true | yes | on)
    echo "SEED_STAGING set — running staging sample-data seed..."
    python seed_staging.py || echo "WARNING: seed_staging.py failed; starting app anyway."
    ;;
esac

# Railway injects $PORT at runtime; fall back to 8000 for local runs.
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
