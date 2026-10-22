# VantaRace closed loop (simulation)

The **Race** page starts VantaFlight's vision-driven racing loop against a
simulated aircraft — PX4 SITL with Gazebo, or the built-in mock — and shows
every stage of it live.

```
vehicle local state (PX4 EKF, 30 Hz)
  → VantaSight   synthetic forward camera rendered from that pose →
                 colour/shape detection → PnP pose → tracking/fusion
  → VantaState   per-gate tracks: alpha filter, ageing confidence, timeout
  → VantaRace    trajectory through the visible gates, speed envelope
  → VantaExecution  confidence floor → HOLD, ENU → NED, yaw handling
  → FlightController.offboard_setpoint   expiry · speed cap · geofence · no-fly
  → PX4 offboard position + velocity setpoint (20 Hz)
```

`RaceSession` (`backend/vantaflight/autonomy/race_session.py`) owns the loop.
It is ticked at 20 Hz by its own task and holds a single lock, so operator
actions, failsafes and the loop never interleave.

## Running it

1. Start PX4 SITL + Gazebo (see [PX4.md](PX4.md)) and the backend.
2. On **Control**, connect *PX4 SITL* (or the mock).
3. On **Race**, pick a course, perception (*VantaSight vision* or the *course
   map* baseline), a speed limit and a finish action, and press **Start race**.

The session arms and takes off if needed, flies back to the start line if the
aircraft is elsewhere, engages offboard at its current position, turns to face
the first gate, and only then hands control to the loop.

Courses are in local ENU metres from home. In SITL **the gates are virtual**:
they exist in VantaFlight's course frame, VantaSight renders them from PX4's
real pose, and gate passes are checked against PX4's own position estimate.
They are not Gazebo models, and the camera frames are synthetic, not Gazebo
renders. Gates alternate orange / magenta / green so neighbouring gates never
merge into one blob in the image.

## States

| State | Meaning |
|---|---|
| PREPARING | arming, taking off, repositioning to the start line, aligning with gate 1 |
| RUNNING | flying the plan |
| RECOVERING | perception lost or below the confidence floor: holding the latched point |
| HOLDING_STALE | the vehicle state is older than 0.5 s: holding the last known point |
| PAUSED | operator pause: holding, offboard kept alive with hold setpoints |
| FINISHING → COMPLETE | all gates passed; leave offboard and run the finish action |
| ABORTED | operator, pilot override, failsafe, link loss, time limit, offboard lost |
| FAILED | could not start (takeoff, offboard) or the planner failed repeatedly |

Close to a gate the camera can no longer see all of it. Inside a 7 m **commit
zone**, in front of a gate seen in the last 8 s, the loop flies through on its
last estimate (perception status `COMMITTED`) instead of stopping short.

## Safety boundaries

* Simulation only: `SimulationOnlyExecutionGuard` grants an execution permit
  only to simulated adapters on local SITL UDP endpoints (14540–14580) or the
  in-process mock (`sim://`). A physical aircraft is refused at `start`.
* Every setpoint passes `FlightController.offboard_setpoint`: refused if it is
  older than 0.3 s, faster than 8 m/s, outside the geofence or inside a no-fly
  zone, or if the aircraft is not armed and airborne. A geofence/no-fly refusal
  aborts the race.
* The `FailsafeGuardian` keeps running: battery low/critical, geofence and link
  failsafes abort the race first, then command the vehicle (return home, land,
  hold). Any pilot command (hold, land, return home, goto, takeoff, disarm)
  aborts the race and wins.
* If setpoints stop, PX4's own offboard-loss failsafe takes over; the session
  aborts if the vehicle leaves offboard for more than 1 s.

## Rehearsing faults

The Race page (and `POST /api/vantarace/fault`) can inject, during a simulated
race: `vision_blackout`, `low_confidence`, `occlude_gate`, `state_stall`
(freeze the vehicle state the loop sees) and `setpoint_delay` (deliver
setpoints late, which the flight core must refuse).

## Recording and replay

The flight database (schema v5) keeps `autonomy_samples` per flight at 5 Hz —
perception (detections, observations with error vs the course map, targets),
vehicle state and its age, planner state and desired setpoint, execution mode
and setpoint — plus every race event and a final summary. Replay shows them as
`autonomy` frames; `GET /api/vantarace/recording/{flight_id}` returns them.

## Verification

| Scope | How | Level |
|---|---|---|
| Closed loop on the mock (vision, all three courses, every fault case) | `tests/test_race_session.py` (29 tests, fake clock) | INTEGRATION_TESTED |
| Same loop, real-time, through the HTTP API | `race_e2e.py --adapter mock` (28/28 checks) | INTEGRATION_TESTED |
| Same harness against PX4 v1.14.3 + Gazebo | `race_e2e.py` in `.github/workflows/sitl.yml` | **SITL_VERIFIED only once that job passes** |

```bash
python simulation/px4/race_e2e.py --api http://127.0.0.1:8000 --json vantarace-sitl.json
```
