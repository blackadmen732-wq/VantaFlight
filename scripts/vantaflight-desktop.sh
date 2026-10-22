#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PY="$BACKEND/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "VantaFlight backend environment is missing. Run scripts/install_chromebook_linux.sh first." >&2
  exit 1
fi
if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo "VantaFlight frontend dependencies are missing. Run scripts/install_chromebook_linux.sh first." >&2
  exit 1
fi

cleanup() {
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$BACKEND"
"$PY" -m uvicorn vantaflight.main:app --host 127.0.0.1 --port 8000 &
BACK_PID=$!

cd "$FRONTEND"
npm run dev -- --host 127.0.0.1 --port 5173 &
FRONT_PID=$!

URL="http://127.0.0.1:5173"
for _ in {1..80}; do
  if "$PY" - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=0.25).read()
PY
  then
    break
  fi
  sleep 0.25
done

if command -v google-chrome >/dev/null 2>&1; then
  google-chrome --app="$URL" >/dev/null 2>&1 &
elif command -v chromium >/dev/null 2>&1; then
  chromium --app="$URL" >/dev/null 2>&1 &
elif command -v chromium-browser >/dev/null 2>&1; then
  chromium-browser --app="$URL" >/dev/null 2>&1 &
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
else
  echo "Open $URL in Chrome."
fi

wait "$BACK_PID" "$FRONT_PID"
