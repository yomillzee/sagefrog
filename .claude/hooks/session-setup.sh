#!/usr/bin/env bash
# SessionStart setup for Claude Code web sessions.
#
# The dashboard app (railway/app) imports psycopg / bcrypt / itsdangerous /
# python-dotenv at module load. Web-session containers start without them, so
# `import`-ing any renderer or running the test suite fails — which otherwise
# pushes Claude into stubbing the whole import chain by hand every session.
# Installing the deps once here means renderers can be imported and rendered
# for real (see CLAUDE.md), with no stubs.
#
# Idempotent and best-effort: if the deps are already present it exits fast, and
# a failed install never blocks the session (|| true).

set -u

deps_present() {
  python - <<'PY'
import importlib.util, sys
mods = ["psycopg", "bcrypt", "itsdangerous", "dotenv", "fastapi", "pydantic"]
sys.exit(0 if all(importlib.util.find_spec(m) for m in mods) else 1)
PY
}

if deps_present; then
  exit 0
fi

# Prefer the full requirements (mirrors CI); fall back to the minimal set that
# is enough to import and render the dashboard if the full install hits a
# system-package conflict (e.g. Debian-managed `packaging`).
pip install -q -r railway/app/requirements.txt >/dev/null 2>&1 \
  || pip install -q \
       psycopg-binary==3.2.9 psycopg==3.2.9 psycopg-pool \
       bcrypt itsdangerous python-dotenv fastapi pydantic httpx \
       >/dev/null 2>&1 \
  || true

exit 0
