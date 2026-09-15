"""Preflight checker — validates system readiness before flight operations."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class CheckSeverity(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    severity: CheckSeverity
    message: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity.value,
            "message": self.message,
        }


@dataclass
class PreflightReport:
    timestamp: float
    checks: list[PreflightCheck]
    all_passed: bool
    critical_failures: int
    warnings: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "checks": [c.to_dict() for c in self.checks],
            "all_passed": self.all_passed,
            "critical_failures": self.critical_failures,
            "warnings": self.warnings,
        }


class PreflightChecker:
    """Runs a suite of preflight checks before flight operations.

    Checks: connection, battery, adapter, safety limits, camera, simulation.
    """

    def check(
        self,
        *,
        connected: bool = False,
        armed: bool = False,
        battery_pct: float = 0.0,
        adapter_name: str | None = None,
        is_simulated: bool = True,
        camera_available: bool = False,
        gps_fix: bool = False,
    ) -> PreflightReport:
        checks: list[PreflightCheck] = []

        checks.append(PreflightCheck(
            name="connection",
            passed=connected,
            severity=CheckSeverity.CRITICAL if not connected else CheckSeverity.OK,
            message="connected" if connected else "no drone connection",
        ))

        checks.append(PreflightCheck(
            name="battery",
            passed=battery_pct >= 20.0,
            severity=(
                CheckSeverity.CRITICAL if battery_pct < 10.0
                else CheckSeverity.WARNING if battery_pct < 20.0
                else CheckSeverity.OK
            ),
            message=f"battery at {battery_pct:.1f}%",
        ))

        checks.append(PreflightCheck(
            name="adapter",
            passed=adapter_name is not None,
            severity=CheckSeverity.CRITICAL if adapter_name is None else CheckSeverity.OK,
            message=f"adapter: {adapter_name}" if adapter_name else "no adapter connected",
        ))

        if not is_simulated:
            checks.append(PreflightCheck(
                name="gps",
                passed=gps_fix,
                severity=CheckSeverity.WARNING if not gps_fix else CheckSeverity.OK,
                message="GPS fix acquired" if gps_fix else "no GPS fix",
            ))

        checks.append(PreflightCheck(
            name="camera",
            passed=camera_available,
            severity=CheckSeverity.WARNING if not camera_available else CheckSeverity.OK,
            message="camera available" if camera_available else "no camera source",
        ))

        checks.append(PreflightCheck(
            name="disarmed",
            passed=not armed,
            severity=CheckSeverity.WARNING if armed else CheckSeverity.OK,
            message="disarmed" if not armed else "already armed",
        ))

        critical = sum(1 for c in checks if c.severity == CheckSeverity.CRITICAL and not c.passed)
        warnings = sum(1 for c in checks if c.severity == CheckSeverity.WARNING and not c.passed)
        all_passed = critical == 0

        return PreflightReport(
            timestamp=time.time(),
            checks=checks,
            all_passed=all_passed,
            critical_failures=critical,
            warnings=warnings,
        )
