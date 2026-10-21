"""Flight core: the controller plus foundation services (clock, ...).

``FlightController`` is imported lazily so that foundation modules such as
``core.clock`` can be imported from anywhere (models, adapters) without
pulling in the whole flight stack and creating import cycles.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .flight_controller import FlightController

__all__ = ["FlightController"]


def __getattr__(name: str):
    if name == "FlightController":
        from .flight_controller import FlightController

        return FlightController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
