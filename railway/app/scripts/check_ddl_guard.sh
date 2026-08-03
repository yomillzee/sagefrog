#!/usr/bin/env bash
# CI guard for the schema-migration rollout (docs/schema-migrations.md).
#
# Modules that have completed Phase 2 must not run schema DDL from read/write
# functions again — that was the #297 deadlock root cause. This fails CI if a
# bare, indented `ensure_schema()` call reappears in a completed module.
#
# It is intentionally scoped to completed modules: the other owners still call
# ensure_schema() per the not-yet-done rollout, and flagging them would be noise.
# Add a module to PHASE2_DONE when its Phase 2 PR merges.
set -euo pipefail

cd "$(dirname "$0")/.."   # railway/app

# Modules whose per-call ensure_schema() safeguards have been removed (Phase 2).
PHASE2_DONE=(web_users.py)

fail=0
for f in "${PHASE2_DONE[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "guard: expected file not found: $f" >&2
    fail=1
    continue
  fi
  # A bare, indented ensure_schema() statement — not the def, not a comment.
  hits=$(grep -nE "^[[:space:]]+ensure_schema\(\)[[:space:]]*$" "$f" || true)
  if [[ -n "$hits" ]]; then
    echo "ERROR: $f runs schema DDL on a read/write path (Phase 2 regression):" >&2
    echo "$hits" >&2
    fail=1
  fi
done

if [[ "$fail" -eq 0 ]]; then
  echo "schema-migration guard: OK (${PHASE2_DONE[*]})"
fi
exit "$fail"
