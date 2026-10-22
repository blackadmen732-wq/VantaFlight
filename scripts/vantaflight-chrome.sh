#!/usr/bin/env bash
# Start VantaFlight as an offline Chrome app (the recommended way on a Chromebook).
#
# One process, no Node at runtime: the Python backend serves the built UI and
# the API on http://127.0.0.1:8000. Open that in Chrome and choose
# "Install VantaFlight" (⋮ menu) once; after that it has its own window and
# icon. Nothing here needs the internet.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
DIST="$ROOT/frontend/dist"
PY="$BACKEND/.venv/bin/python"
PORT="${VANTAFLIGHT_PORT:-8000}"
URL="http://127.0.0.1:$PORT"

if [[ ! -x "$PY" ]]; then
  echo "VantaFlight is not installed yet. Run: $ROOT/scripts/install_chromebook_linux.sh" >&2
  exit 1
fi
if [[ ! -f "$DIST/index.html" ]]; then
  echo "The UI has not been built. Run: $ROOT/scripts/install_chromebook_linux.sh" >&2
  exit 1
fi

health() {
  "$PY" - "$URL" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + "/api/health", timeout=0.5).read()
PY
}

open_app() {
  if command -v garcon-url-handler >/dev/null 2>&1; then
    garcon-url-handler "$URL" >/dev/null 2>&1 &   # ChromeOS: opens in the real Chrome
  elif command -v google-chrome >/dev/null 2>&1; then
    google-chrome --app="$URL" >/dev/null 2>&1 &
  elif command -v chromium >/dev/null 2>&1; then
    chromium --app="$URL" >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
  else
    echo "Open $URL in Chrome."
  fi
}

if health; then
  echo "VantaFlight is already running at $URL"
  open_app
  exit 0
fi

cd "$BACKEND"
VANTAFLIGHT_FRONTEND_DIST="$DIST" "$PY" -m uvicorn vantaflight.main:app \
  --host 127.0.0.1 --port "$PORT" --log-level warning &
BACK_PID=$!
trap 'kill "$BACK_PID" 2>/dev/null || true' EXIT INT TERM

for _ in {1..120}; do
  health && break
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    echo "The backend stopped while starting; run it by hand to see why:" >&2
    echo "  cd $BACKEND && VANTAFLIGHT_FRONTEND_DIST=$DIST $PY -m uvicorn vantaflight.main:app --port $PORT" >&2
    exit 1
  fi
  sleep 0.25
done

echo "VantaFlight is running at $URL (close this window or press Ctrl+C to stop it)."
open_app
wait "$BACK_PID"
