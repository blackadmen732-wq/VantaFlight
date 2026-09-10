#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

echo "=== VantaFlight v0.9.0 — Local Startup ==="

# Environment check
echo "[1/4] Checking environment..."
python3 -c "import sys; assert sys.version_info >= (3, 11), f'Python 3.11+ required, got {sys.version}'"
python3 -c "import numpy, cv2, scipy, fastapi, uvicorn; print('  Dependencies OK')"

# Backend
echo "[2/4] Starting backend on :8000..."
cd "$BACKEND"
PYTHONPATH="$BACKEND" uvicorn vantaflight.main:app \
    --host 0.0.0.0 --port 8000 --reload --log-level info &
BACKEND_PID=$!

# Frontend
echo "[3/4] Starting frontend dev server on :5173..."
cd "$FRONTEND"
npm run dev -- --host 0.0.0.0 --port 5173 &
FRONTEND_PID=$!

cleanup() {
    echo ""
    echo "Shutting down..."
    kill "$FRONTEND_PID" 2>/dev/null || true
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

echo "[4/4] Ready!"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo "  Health:   http://localhost:8000/api/health"
echo ""
echo "Press Ctrl+C to stop."

wait
