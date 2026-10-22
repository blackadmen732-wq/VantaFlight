# VantaContracts and VantaClock

Every message that crosses a subsystem boundary is a **typed, versioned,
framed, timestamped contract**: never a loose dictionary of raw values. This
page sets out the rules, the topics that exist today and how to add one.

Code: `backend/vantaflight/core/clock.py` (time) and
`backend/vantaflight/contracts/` (frames, units, envelope, registry, payloads).
Tests: `backend/tests/test_clock.py`, `backend/tests/test_contracts.py`.

Why this exists. PR #8 found two bugs that loose dictionaries make easy:
VantaExecution emitted ENU coordinates in a structure labelled NED, and a
state "age" was computed by subtracting timestamps that were not on the same
clock. With contracts, both now fail loudly instead of flying wrong.

---

## 1. Time

### Clock domains

A `TimeStamp(value_ns, domain, uncertainty_ns=None, clock_id="")` always
names the clock it came from.

| Domain | What it is | Use it for |
|--------|------------|------------|
| `HOST_MONOTONIC` | The host's monotonic clock | **Canonical.** Scheduling, deadlines, ages, staleness |
| `UTC_WALL` | Wall time (NTP can step it) | Records, file names, correlating machines. Never deadlines |
| `VEHICLE_BOOT` | PX4 boot clock (`timestamp_us`), `clock_id="px4:<address>"` | Converting vehicle samples to host time |
| `CAMERA_DEVICE` | A camera's device clock | Frame capture times |
| `SIMULATION` | Gazebo / simulator time | Sim-only stamps |
| `MEDIA_TIMELINE` | Position in a video file | Capture/Media (PR L) |
| `REMOTE_AGENT` | Another machine's clock | Bridge (PR J) |

Rules:

1. Subtracting or comparing two timestamps from different clocks (domain or
   `clock_id`) raises `MixedClockDomainError`. Convert first.
2. Deadlines, timeouts and ages use `HOST_MONOTONIC`. Nothing schedules from
   `time.time()`.
3. A source that gives no capture time is stamped at receipt and flagged
   `CAPTURE_TIME_IS_RECEIVE_TIME`. Do not invent a capture time.
4. An unknown age is treated as **stale** (`VantaClock.is_stale(None, …)` is
   `True`).

### Mapping a foreign clock onto host time

`ClockSync` fits `t_host ≈ a·t_source + b` online from
`(source time, host receive time)` pairs:

- **Lower envelope.** Transport delay only ever adds, so the fit follows the
  5th percentile of residuals, not the mean.
- **Outliers.** A single sample far from the fit is rejected.
- **Resets.** A jump larger than `jump_threshold_s` (0.25 s) that holds for
  `jump_confirm_samples` (3) consistent samples is a clock reset (e.g. a
  vehicle reboot). The window restarts.
- **Drift.** Drift is estimated only once there are `min_samples_for_drift`
  (8) samples spanning at least `min_span_for_drift_s` (2 s). Before that the
  model is offset-only, with `assumed_drift_ppm` (100) of unmodelled drift
  added to the uncertainty.
- **Uncertainty.** Each converted `TimeStamp` carries
  `uncertainty_ns ≈ 3σ(line + quantile) + envelope bias + unmodelled drift`.
  In the test scenarios this bound covers 100% of true conversion errors.
- **Latency.** One-way timing cannot observe the minimum transport latency,
  so converted capture times run late by that latency unless the source
  supplies `nominal_latency_ns`. `stats()["latency_compensated"]` says
  whether it did.

`GET /api/clock` reports every sync: `source_drift_ppm` (positive means the
source runs fast), `offset_s`, residual p50/p95/p99, jitter band,
`conversion_uncertainty_ms` and `latency_compensated`.

### Tests and determinism

- `VantaClock.fake()` plus `advance(seconds)` gives a deterministic clock. A
  `VantaClock` is also callable as a `() -> seconds` monotonic source for
  older code (`VantaClock.from_seconds(fn)` wraps one).
- `SequentialIds(seed)` gives reproducible UUIDs. The same seed with the same
  inputs produces identical envelopes, byte for byte.

---

## 2. Frames and units

### Frames

| `FrameId` | Axes |
|-----------|------|
| `WORLD_ENU` | VantaFlight world: metres from home, x east, y north, z up |
| `LOCAL_NED` | PX4 local: x north, y east, z down (same origin as WORLD_ENU in SITL; PR D makes the transform explicit) |
| `BODY_FLU` / `BODY_FRD` | Aircraft body: forward-left-up / forward-right-down |
| `CAMERA_FRONT_OPTICAL` / `CAMERA_DOWN_OPTICAL` | OpenCV optical: +x right, +y down, +z forward |
| `GEODETIC`, `IMU`, `PAYLOAD_HOOK`, `TARGET` | Reserved for PR D |

`Vec3(x, y, z, frame, quantity)` carries its frame and quantity. Adding a NED
velocity to an ENU position raises `FrameMismatchError`. Contract fields say
which frame they accept (`PositionENU`, `VelocityNED`, …); passing the wrong
one is a pydantic `ValidationError`. `enu_to_ned` / `ned_to_enu` are the only
conversions that need no transform tree. `yaw_enu_rad_to_ned` /
`yaw_ned_rad_to_enu` convert headings (counter-clockwise from east ↔
clockwise from north).

### Units

Every float field in a contract does one of three things:

- ends in its unit: `_m`, `_m_s`, `_m_s2`, `_rad`, `_rad_s`, `_deg`,
  `_deg_s`, `_s`, `_ms`, `_us`, `_ns`, `_pct`, `_hz`, `_ppm`, `_v`, `_a`,
  `_j`, `_k`, `_pa`, `_w` or `_px`;
- is a frame-tagged `Vec3`, whose unit follows from its quantity (m, m/s,
  m/s², rad/s, unitless);
- or is a genuinely unitless ratio listed in `UNITLESS_FLOATS`
  (`confidence`, `planner_confidence`, `current_confidence`, `score`,
  `ratio`, `aggression`).

`unit_problems()` checks every registered payload recursively in the test
suite, so a bare `speed: float` cannot ship.

---

## 3. The envelope

```text
EventEnvelope[Payload]
  schema_version   payload version as written
  event_id         UUID (deterministic with SequentialIds)
  run_id           one per run (EnvelopeStamper.new_run())
  sequence         per topic, per run, from 0
  source           component id, e.g. "autonomy.race_session"
  topic            e.g. "race.sample"
  captured_at      when the data was true (source clock allowed)
  received_at      when the host got it      ┐ same clock,
  published_at     when it was put on the bus ┘ published ≥ received
  frame_id         defaults to the topic's frame
  calibration_id   camera/IMU calibration in force, if any
  model_versions   e.g. {"detector": "hsv-1"}
  quality          Quality (below)
  payload          the typed, frozen payload
```

The envelope validates causality: `received_at` and `published_at` must share
a clock, and nothing can be published before it was received. When
`captured_at` is on the same clock, it cannot be later than `received_at`.

`EnvelopeStamper(source, clock)` is how components produce envelopes. It
looks up the registered version, refuses a payload class that is not the one
registered for its topic, numbers sequences per topic and stamps times from
the component's `VantaClock`.

`Quality` carries `valid`, `confidence` (0–1), `covariance` (square,
symmetric, finite, in the payload's units), `stale_after_ms`, `provenance`
and `flags`:

| Flag | Meaning |
|------|---------|
| `MEASURED` / `ESTIMATED` / `PREDICTED` / `EXTRAPOLATED` / `INTERPOLATED` | How the value was produced |
| `SIMULATED` | From a simulator, not an aircraft |
| `SYNTHETIC_SENSOR` | Rendered or generated sensor data |
| `STALE`, `DEGRADED_SOURCE`, `OUTLIER_REJECTED`, `CLAMPED`, `UNCALIBRATED` | Trust problems |
| `CLOCK_UNSYNCED` | `captured_at` could not be mapped to host time |
| `CAPTURE_TIME_IS_RECEIVE_TIME` | The source gave no capture time |
| `LATENCY_UNCOMPENSATED` | Converted time ignores transport latency |
| `LEGACY_UNVERSIONED` | Migrated from a pre-contract record |

---

## 4. Registry and versioning

`ContractRegistry` holds one current payload model and version per topic.

- **Never edit a released payload in place.** To change one, add a new
  class with `SCHEMA_VERSION = n + 1`, register it for the topic and supply
  an upgrader `v_n → v_n+1`: a pure function from the old payload dict to the
  new one.
- The migration chain must be contiguous. Registration refuses gaps.
- Decoding walks the chain from the stored version to the current one.
- A message **newer** than this build understands raises
  `SchemaVersionError`. It is never guessed at.
- `flight_critical=True` marks topics that closed-loop control or safety
  depends on. These are the topics PR B's bus must never drop silently.

`GET /api/contracts` lists every topic with its version, frame, migrations
and JSON schema.

### Legacy records

PR #8 stored race rows in `autonomy_samples` as bare dicts under the kinds
`race_sample`, `race_event` and `race_summary`. These decode as **schema
version 0** and migrate to v1 like any other old version, flagged
`LEGACY_UNVERSIONED` with provenance `autonomy_samples:v0`. New rows store
`kind = topic` and `data = encoded envelope`.
`GET /api/vantarace/recording/{flight_id}?kind=` accepts either a topic or a
legacy kind, and reports rows it could not decode as `decode_errors` instead
of failing the whole recording. Replay does the same per frame.

---

## 5. Topics today

| Topic | Payload | v | Frame | Flight-critical | Migrates from |
|-------|---------|---|-------|-----------------|---------------|
| `vehicle.state` | `VehicleStateV1` | 1 | WORLD_ENU | yes | |
| `vehicle.telemetry` | `TelemetrySampleV1` | 1 | WORLD_ENU | yes | |
| `planner.desired` | `DesiredStateV1` | 1 | WORLD_ENU | yes | |
| `execution.setpoint` | `OffboardSetpointV1` | 1 | LOCAL_NED | yes | |
| `execution.command` | `CommandRecordV1` | 1 | | yes | |
| `safety.failsafe` | `FailsafeTriggerV1` | 1 | | yes | |
| `vision.gate_observation` | `GateObservationV1` | 1 | WORLD_ENU | yes | |
| `vision.perception` | `PerceptionFrameV1` | 1 | | yes | |
| `flight.event` | `FlightEventV1` | 1 | | yes | |
| `authority.decision` | `AuthorityDecisionV1` | 1 | | yes | |
| `system.health` | `HealthChangeV1` | 1 | | | |
| `race.sample` | `RaceSampleV1` | 1 | | | v0 (PR #8 rows) |
| `race.event` | `RaceEventV1` | 1 | | | v0 |
| `race.summary` | `RaceSummaryV1` | 1 | | | v0 |

Since PR B, `flight.event`, `execution.command`, `safety.failsafe` and `authority.decision` are published on VantaBus reliable channels (see [RUNTIME.md](RUNTIME.md)). What PR A made live: `RaceSession` stamps and records `race.sample`,
`race.event` and `race.summary` envelopes. Each sample nests the vehicle
state, perception, planner and execution payloads above. The telemetry,
command and failsafe contracts are defined, with `from_*` converters from
the existing models, and tested. They are not published anywhere yet, because
there is no bus to publish them on until PR B.

---

## 6. Adding a topic

1. Define a `ContractPayload` subclass in `contracts/payloads.py`, with
   `TOPIC = "area.name"` (lowercase, dotted) and `SCHEMA_VERSION = 1`.
   Payloads are frozen and forbid extra fields.
2. Put a unit on every float, or use a framed `Vec3` type
   (`PositionENU`, `VelocityNED`, …).
3. Add a `from_<existing model>` classmethod if it wraps an existing
   structure, so nothing hand-copies fields.
4. Register it in `register_all()`, with its frame and whether it is
   flight-critical.
5. Produce it through an `EnvelopeStamper` on the component's `VantaClock`,
   never by constructing envelopes by hand in business logic.
6. Tests: a round trip through `registry.encode` / `registry.decode`, a frame
   mismatch rejected, and `unit_problems()` clean. The existing suite runs
   the unit check over every registered topic automatically.

## 7. Contributor contract

- No bare dicts across subsystem boundaries. Use a registered payload in an
  envelope.
- No `time.time()` for deadlines or ages. Use `VantaClock` on
  `HOST_MONOTONIC`.
- No unlabelled vectors. A position or velocity says its frame.
- No unitless floats unless they are a ratio listed in `UNITLESS_FLOATS`.
- No silent schema changes. Bump the version and add an upgrader.
- No invented data. A missing capture time, sensor or calibration is flagged,
  not filled in.
- Tests use `VantaClock.fake()` and `SequentialIds` and must be
  deterministic.

## 8. Verification level

UNIT_TESTED (clock, frames, units, envelope, registry, migrations) and
INTEGRATION_TESTED on the mock (race session records and replays contracts;
legacy rows migrate; API serves decoded recordings). **Not SITL_VERIFIED**:
the PX4 SITL CI job has not run (see [STATUS.md](STATUS.md)).
