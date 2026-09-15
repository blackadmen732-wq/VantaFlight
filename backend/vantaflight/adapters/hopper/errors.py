"""Explicit Hopper error types.

Every error maps to a clear operator-readable message on the frontend;
nothing in VantaFlight catches a bare Exception from this layer.
"""
from __future__ import annotations


class HopperError(RuntimeError):
    """Base for all Hopper adapter errors."""


class HopperNotFound(HopperError):
    """Hopper could not be discovered on any configured transport."""


class HopperControlUnavailable(HopperError):
    """The control link is not established or is not supported in the current mode."""


class HopperCameraUnavailable(HopperError):
    """The camera link is not established or the stream is unreachable."""


class HopperTelemetryUnavailable(HopperError):
    """No telemetry source is available."""


class HopperUnsupportedCapability(HopperError):
    """A command was issued for a capability Hopper does not expose.

    VantaFlight must check capabilities before issuing commands; this error
    is the hard stop when that check is skipped or a race condition occurs.
    """


class HopperCommandExpired(HopperError):
    """A command was not acknowledged before its TTL elapsed."""


class HopperLinkLost(HopperError):
    """An established link dropped unexpectedly."""


class HopperBatteryCritical(HopperError):
    """Battery reached the critical threshold; mission must land immediately."""


class HopperPreflightFailed(HopperError):
    """Preflight checks did not pass; flight must not proceed."""


class HopperProgramValidationFailed(HopperError):
    """A generated mission program failed safety validation before upload."""
