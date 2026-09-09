#!/usr/bin/env bash
# Check that the development environment has the tools VantaFlight needs.
set -euo pipefail

OK=0
WARN=0
FAIL=0

check() {
  local label="$1" cmd="$2"
  if command -v "$cmd" &>/dev/null; then
    printf "  [OK]   %-20s %s\n" "$label" "$(command -v "$cmd")"
    ((OK++))
  else
    printf "  [MISS] %-20s not found\n" "$label"
    ((FAIL++))
  fi
}

optional() {
  local label="$1" cmd="$2"
  if command -v "$cmd" &>/dev/null; then
    printf "  [OK]   %-20s %s\n" "$label" "$(command -v "$cmd")"
    ((OK++))
  else
    printf "  [WARN] %-20s not found (optional)\n" "$label"
    ((WARN++))
  fi
}

echo "VantaFlight Environment Check"
echo "=============================="
echo ""
echo "Required:"
check "Python 3.10+"   python3
check "pip"            pip3
check "Node.js"        node
check "npm"            npm

echo ""
echo "Optional (PX4 SITL):"
optional "PX4-Autopilot" make
optional "Gazebo"        gazebo

echo ""
echo "Python packages:"
if python3 -c "import fastapi" 2>/dev/null; then
  printf "  [OK]   %-20s installed\n" "FastAPI"
  ((OK++))
else
  printf "  [MISS] %-20s run: pip install -r backend/requirements.txt\n" "FastAPI"
  ((FAIL++))
fi

echo ""
echo "Summary: $OK ok, $WARN warnings, $FAIL missing"
[ "$FAIL" -eq 0 ] && echo "Environment ready." || echo "Install missing dependencies before continuing."
exit "$FAIL"
