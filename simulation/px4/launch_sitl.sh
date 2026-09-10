#!/usr/bin/env bash
# Launch PX4 SITL for VantaFlight development.
# Requires PX4-Autopilot to be cloned and built separately.
#
# Usage:
#   ./launch_sitl.sh                   # default Gazebo Classic
#   ./launch_sitl.sh jmavsim           # jMAVSim (lighter weight)
#
# VantaFlight connects to the SITL instance at udpin://0.0.0.0:14540 by default.
# Override with VANTAFLIGHT_PX4_SITL_URL environment variable.

set -euo pipefail

PX4_DIR="${PX4_AUTOPILOT_DIR:-$HOME/PX4-Autopilot}"
SIMULATOR="${1:-gazebo-classic}"

if [ ! -d "$PX4_DIR" ]; then
  echo "ERROR: PX4-Autopilot not found at $PX4_DIR"
  echo "Clone it:  git clone https://github.com/PX4/PX4-Autopilot.git --recursive"
  echo "Or set PX4_AUTOPILOT_DIR to your existing clone."
  exit 1
fi

echo "Starting PX4 SITL with $SIMULATOR..."
echo "VantaFlight will connect at: ${VANTAFLIGHT_PX4_SITL_URL:-udpin://0.0.0.0:14540}"
echo ""

cd "$PX4_DIR"
make px4_sitl "$SIMULATOR"
