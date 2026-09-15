"""Hopper battery safety manager.

Battery safety overrides mission logic.  Priority order is:
  EMERGENCY > BATTERY_CRITICAL > LINK_LOST > COLLISION_PROTECT
  > MISSION_ABORT > MISSION_OBJECTIVE > SPEED_OPTIMIZE

FTW published hardware thresholds:
- controller flashes red at ~30%
- solid red / "land now" at ~10%

VantaFlight uses the more conservative mission_reserve_pct (default 35%)
so it stops starting new objectives before hardware warnings fire.
"""
from __future__ import annotations

import logging
from typing import Optional

from .config import HopperBatteryConfig
from .models import BatteryState

logger = logging.getLogger(__name__)


class HopperBatteryManager:
    """Tracks battery state and enforces mission-level energy policy."""

    def __init__(self, config: Optional[HopperBatteryConfig] = None) -> None:
        self._config = config or HopperBatteryConfig()
        self._pct: float = 100.0
        self._state = BatteryState.UNKNOWN

    @property
    def percentage(self) -> float:
        return self._pct

    @property
    def state(self) -> BatteryState:
        return self._state

    def update(self, pct: float) -> BatteryState:
        """Ingest a new battery reading and return the new state."""
        self._pct = max(0.0, min(100.0, pct))
        self._state = self._compute_state(self._pct)
        if self._state in (BatteryState.CRITICAL, BatteryState.LANDING):
            logger.warning("Hopper battery critical: %.1f%%", self._pct)
        return self._state

    def may_start_objective(self) -> bool:
        """Return True if there is enough battery to begin a new mission objective."""
        return self._pct >= self._config.mission_reserve_pct

    def should_land_now(self) -> bool:
        """Return True if VantaFlight must command immediate landing."""
        return self._state in (BatteryState.CRITICAL, BatteryState.LANDING)

    def should_return(self) -> bool:
        """Return True if the mission should abort to safe landing zone."""
        return self._state == BatteryState.RETURN_REQUIRED

    def _compute_state(self, pct: float) -> BatteryState:
        if pct <= self._config.critical_pct:
            return BatteryState.CRITICAL
        if pct <= self._config.reserve_pct:
            return BatteryState.RESERVE
        if pct <= self._config.mission_reserve_pct:
            return BatteryState.RETURN_REQUIRED
        return BatteryState.NORMAL
