#!/usr/bin/env bash
# Install VantaFlight in ChromeOS Linux (Crostini) or any Debian/Ubuntu.
#
#   scripts/install_chromebook_linux.sh                 # online, one time
#   scripts/install_chromebook_linux.sh --offline DIR   # from an offline bundle
#                                                       # (scripts/make_offline_bundle.sh)
#
# Internet is needed once (Python packages + building the UI), unless you
# install from an offline bundle. After that VantaFlight never needs it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
DESKTOP_DIR="$HOME/.local/share/applications"
ENTRY="$DESKTOP_DIR/vantaflight.desktop"
OFFLINE=""
DEV=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline) OFFLINE="$(cd "$2" && pwd)"; shift 2 ;;
    --dev) DEV=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "Missing python3: sudo apt install python3 python3-venv" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "VantaFlight needs Python 3.10 or newer." >&2; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "Missing venv: sudo apt install python3-venv" >&2; exit 1; }

echo "Installing VantaFlight ($(uname -m), Python $(python3 -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")'))..."
[[ -d "$BACKEND/.venv" ]] || python3 -m venv "$BACKEND/.venv"
PIP=("$BACKEND/.venv/bin/python" -m pip)

if [[ -n "$OFFLINE" ]]; then
  [[ -d "$OFFLINE/wheels" && -f "$OFFLINE/dist/index.html" ]] || {
    echo "$OFFLINE is not a VantaFlight offline bundle (needs wheels/ and dist/)." >&2; exit 1; }
  "${PIP[@]}" install --no-index --find-links "$OFFLINE/wheels" -r "$BACKEND/requirements.txt"
  rm -rf "$FRONTEND/dist"
  cp -r "$OFFLINE/dist" "$FRONTEND/dist"
else
  "${PIP[@]}" install --upgrade pip
  "${PIP[@]}" install -r "$BACKEND/requirements.txt"
  if [[ "$DEV" = 1 ]]; then
    "${PIP[@]}" install -r "$BACKEND/requirements-dev.txt"
  fi
  if [[ ! -f "$FRONTEND/dist/index.html" || "$DEV" = 1 ]]; then
    command -v npm >/dev/null 2>&1 || {
      echo "Building the UI needs Node 20.19+ or 22.12+ once (https://github.com/nvm-sh/nvm, then: nvm install 22)." >&2
      echo "Or install from an offline bundle: --offline DIR" >&2; exit 1; }
    (cd "$FRONTEND" && npm ci && npm run build)
  fi
fi

chmod +x "$ROOT/scripts/vantaflight-chrome.sh" "$ROOT/scripts/vantaflight-desktop.sh"
mkdir -p "$DESKTOP_DIR"
cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=VantaFlight
Comment=Local drone ground station (runs offline)
Exec=$ROOT/scripts/vantaflight-chrome.sh
Icon=$FRONTEND/public/vantaflight-icon.png
Terminal=false
Categories=Development;Science;Education;
StartupNotify=true
DESKTOP
chmod +x "$ENTRY"

cat <<DONE

VantaFlight is installed. It no longer needs the internet.

Start it: "VantaFlight" in the Linux apps folder, or
  $ROOT/scripts/vantaflight-chrome.sh
It opens http://127.0.0.1:8000 in Chrome. Use the ⋮ menu → "Install VantaFlight"
once to give it its own window and icon.

Drones:
  Mock     no network needed
  Hopper   join the Hopper_#### Wi-Fi; the camera is at http://192.168.2.1 (flight stays in FTW's apps)
  Tello    join TELLO-XXXXXX; on ChromeOS forward UDP 8890 under Settings → Developers → Linux → Port forwarding
Check your setup any time in the app, or at http://127.0.0.1:8000/api/network/check
DONE
