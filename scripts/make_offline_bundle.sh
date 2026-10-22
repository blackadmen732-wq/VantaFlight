#!/usr/bin/env bash
# Build an offline installer bundle on a computer WITH internet.
#
# Run it on the same CPU type and Python version as the Chromebook that will
# install it (check there with: uname -m; python3 --version). Python wheels
# are per-architecture: an x86_64 bundle will not install on an ARM
# (aarch64) Chromebook.
#
#   scripts/make_offline_bundle.sh            → vantaflight-offline-<arch>-py<ver>.tar.gz
# On the Chromebook:
#   tar xzf vantaflight-offline-*.tar.gz
#   scripts/install_chromebook_linux.sh --offline vantaflight-offline
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(uname -m)"
PYV="$(python3 -c 'import sys;print(f"{sys.version_info[0]}{sys.version_info[1]}")')"
OUT="$ROOT/vantaflight-offline"
rm -rf "$OUT"
mkdir -p "$OUT/wheels"

python3 -m pip download -r "$ROOT/backend/requirements.txt" -d "$OUT/wheels"
python3 -m pip download pip setuptools wheel -d "$OUT/wheels"
(cd "$ROOT/frontend" && npm ci && npm run build)
cp -r "$ROOT/frontend/dist" "$OUT/dist"
cat > "$OUT/README.txt" <<TXT
VantaFlight offline bundle for $ARCH, Python $PYV.
Install: scripts/install_chromebook_linux.sh --offline <this folder>
TXT
(cd "$ROOT" && tar czf "vantaflight-offline-$ARCH-py$PYV.tar.gz" vantaflight-offline)
sha256sum "$ROOT/vantaflight-offline-$ARCH-py$PYV.tar.gz"
echo "Bundle: $ROOT/vantaflight-offline-$ARCH-py$PYV.tar.gz"
