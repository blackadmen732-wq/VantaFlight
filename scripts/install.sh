#!/usr/bin/env bash
# Idempotent repository bootstrap for VantaFlight.
# Sets up the Python Flight Core virtualenv and the frontend node_modules.
# Safe to run repeatedly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> VantaFlight install (root: ${ROOT})"

# --- System prerequisites --------------------------------------------------
# The base image ships Python 3.12 but not the venv module. Install it once if
# missing (idempotent; no-op when already present).
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "==> Installing python3-venv system package"
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv >/dev/null
fi

# --- Flight Core (Python) --------------------------------------------------
echo "==> Setting up Python Flight Core"
cd "${ROOT}/backend"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
deactivate

# --- Desktop UI (frontend) -------------------------------------------------
echo "==> Setting up frontend"
cd "${ROOT}/frontend"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

echo "==> VantaFlight install complete"
