#!/usr/bin/env bash
# Everything CI runs, on your own machine. Use it when GitHub Actions can't run.
#   scripts/check.sh            backend tests + frontend typecheck/tests/build
#   scripts/check.sh backend    only the backend
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
FAILED=()

step() {
  local name="$1"; shift
  echo; echo "== $name"
  if "$@"; then echo "-- ok"; else echo "-- FAILED"; FAILED+=("$name"); fi
}

step "backend: pytest" bash -c "cd '$ROOT/backend' && '$PY' -m pytest -q"
if [[ "${1:-all}" != "backend" ]]; then
  if [[ -d "$ROOT/frontend/node_modules" ]]; then
    step "frontend: typecheck" bash -c "cd '$ROOT/frontend' && npm run typecheck"
    step "frontend: tests" bash -c "cd '$ROOT/frontend' && npm test"
    step "frontend: build" bash -c "cd '$ROOT/frontend' && npm run build"
  else
    echo; echo "== frontend skipped: run 'npm ci' in frontend/ first"
    FAILED+=("frontend not checked")
  fi
fi
for f in scripts/*.sh; do bash -n "$ROOT/$f" || FAILED+=("$f syntax"); done

echo
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "ALL CHECKS PASSED  ($(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null))"
else
  echo "FAILED: ${FAILED[*]}"; exit 1
fi
