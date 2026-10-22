"""Feed the Digital Twin 2.0 snapshot from the live flight core.

`TwinSnapshotBuilder` only renders what is published to it. This publisher is
called once per telemetry tick and publishes, in local ENU metres:

* the aircraft (ACTUAL layer) and a short trail of where it has been,
* the active mission route (DESIRED layer) and its current target,
* every no-fly zone (TRUTH regions) and the geofence (envelope),
* the autonomy loop's race progress.

Nothing is published for a value the aircraft cannot measure: with no
position (camera-only Hopper, for instance) the twin shows no aircraft rather
than one parked at the origin.
"""
from __future__ import annotations

import collections
import math
from typing import TYPE_CHECKING, Any

from .primitives import (
    TwinAircraftState,
    TwinEnvelope,
    TwinErrorMetric,
    TwinLayer,
    TwinPath,
    TwinPoint,
    TwinRegion,
    TwinSnapshotBuilder,
)

if TYPE_CHECKING:
    from ..mission import MissionRunner
    from ..models import Telemetry
    from ..routing import Airspace

#: Trail samples kept (at the default 10 Hz stream rate: the last 30 s).
TRAIL_LENGTH = 300
#: Skip trail points closer than this to the previous one (metres).
TRAIL_MIN_STEP_M = 0.1


class LiveTwinPublisher:
    def __init__(self, builder: TwinSnapshotBuilder) -> None:
        self._builder = builder
        self._trail: collections.deque[tuple[float, float, float]] = collections.deque(
            maxlen=TRAIL_LENGTH
        )

    def reset(self) -> None:
        self._trail.clear()
        self._builder.reset()

    def publish(
        self,
        telemetry: "Telemetry",
        mission: "MissionRunner | None" = None,
        airspace: "Airspace | None" = None,
        autonomy: dict[str, Any] | None = None,
    ) -> None:
        b = self._builder
        if telemetry.connected and telemetry.position_available:
            position = (telemetry.x, telemetry.y, telemetry.altitude if telemetry.altitude_available else 0.0)
            heading = math.radians(telemetry.heading)
            speed = telemetry.ground_speed or telemetry.velocity
            b.set_aircraft(
                TwinLayer.ACTUAL,
                TwinAircraftState(
                    position=position,
                    velocity=(speed * math.sin(heading), speed * math.cos(heading), 0.0),
                    yaw_deg=telemetry.heading,
                ),
            )
            if not self._trail or math.dist(self._trail[-1], position) >= TRAIL_MIN_STEP_M:
                self._trail.append(position)
        else:
            b.reset_aircraft()
            if not telemetry.connected:
                self._trail.clear()

        if len(self._trail) >= 2:
            b.add_primitive(TwinPath(points=tuple(self._trail), layer=TwinLayer.ACTUAL, label="trail"))

        plan = getattr(mission, "plan", None) if mission is not None else None
        if plan is not None and getattr(mission.state, "value", "") in ("RUNNING", "PAUSED"):
            route = [(0.0, 0.0, 0.0)] + [(w.x, w.y, w.altitude) for w in plan.waypoints]
            b.add_primitive(TwinPath(points=tuple(route), layer=TwinLayer.DESIRED, label=plan.name))
            index = getattr(mission, "index", 0)
            if 0 <= index < len(plan.waypoints):
                target = plan.waypoints[index]
                b.add_primitive(
                    TwinPoint(target.x, target.y, target.altitude, TwinLayer.DESIRED, label="target", radius=0.6)
                )

        if airspace is not None:
            for zone in airspace.zones:
                b.add_primitive(
                    TwinRegion(
                        vertices=tuple((float(x), float(y), 0.0) for x, y in zone.vertices),
                        layer=TwinLayer.TRUTH,
                        label=zone.name,
                        color="#ff3b5c",
                    )
                )
            fence = airspace.geofence
            b.add_primitive(
                TwinEnvelope(
                    0.0, 0.0, fence.max_altitude_m / 2,
                    fence.radius_m, fence.radius_m, fence.max_altitude_m / 2,
                    layer=TwinLayer.TRUTH, label="geofence", opacity=0.05,
                )
            )

        if telemetry.connected:
            b.add_error(TwinErrorMetric("link_age", telemetry.link_age_s, "s"))

        if autonomy:
            progress = autonomy.get("gate_progression") or {}
            b.set_autonomy(
                str(autonomy.get("state", "IDLE")),
                float(progress.get("race_time_s", 0.0)),
                int(progress.get("gates_passed", 0)),
                int(progress.get("total_gates", 0)),
            )
