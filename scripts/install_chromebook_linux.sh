#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
DESKTOP_DIR="$HOME/.local/share/applications"
ENTRY="$DESKTOP_DIR/vantaflight.desktop"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need python3
need npm

PY_VER="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
echo "Installing VantaFlight for ChromeOS Linux (Python $PY_VER)..."

if [[ ! -d "$BACKEND/.venv" ]]; then
  python3 -m venv "$BACKEND/.venv"
fi
"$BACKEND/.venv/bin/python" -m pip install --upgrade pip
"$BACKEND/.venv/bin/python" -m pip install -r "$BACKEND/requirements.txt"
if [[ -f "$BACKEND/requirements-dev.txt" ]]; then
  "$BACKEND/.venv/bin/python" -m pip install -r "$BACKEND/requirements-dev.txt"
fi

cd "$FRONTEND"
npm ci

chmod +x "$ROOT/scripts/vantaflight-desktop.sh"
mkdir -p "$DESKTOP_DIR"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=VantaFlight
Comment=Local autonomous drone mission system
Exec=$ROOT/scripts/vantaflight-desktop.sh
Terminal=false
Categories=Development;Science;Education;
StartupNotify=true
EOF
chmod +x "$ENTRY"

cat <<EOF

VantaFlight is installed.

Start it from the Linux app launcher as "VantaFlight", or run:
  $ROOT/scripts/vantaflight-desktop.sh

For Hopper camera observation, first join the Hopper Wi-Fi network.
Live Hopper flight commands stay disabled until an official supported FTW control transport is configured and verified.
EOF
