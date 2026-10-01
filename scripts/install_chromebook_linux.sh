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
need node
need npm

# The frontend toolchain (Vite 8) needs Node 20.19+ or 22.12+. Debian's
# packaged nodejs is often older; see the README for installing Node 22.
if ! node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)'; then
  echo "Node $(node --version) is too old: VantaFlight needs Node 20.19+ or 22.12+." >&2
  echo "Install Node 22 (e.g. https://github.com/nvm-sh/nvm, then: nvm install 22) and re-run." >&2
  exit 1
fi

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
