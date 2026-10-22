# VantaSystem: bus, runtime, health, authority

Spec PR B. Code: `backend/vantaflight/core/{system,runtime,health,authority}.py` and
`backend/vantaflight/bus/`. Tests: `test_bus.py`, `test_runtime.py`, `test_health.py`,
`test_authority.py`, `test_system.py`.

## VantaSystem: one composition root

`VantaSystem.create()` builds the clock, the contract registry, the bus, the
runtime, health and authority once. `main.create_app` passes them to the
flight controller, race session, recorder and cameras. Tests swap in a fake
clock or sleep here, in one place.

## VantaBus: typed channels, every drop counted

| Mode | Used for | Rule |
|------|----------|------|
| `LATEST` | camera frames, the telemetry snapshot | 1–2 items. A reader gets the newest item; older ones count as stale. Capacity above 2 is refused. |
| `BOUNDED_STREAM` | health changes, diagnostics | Bounded FIFO. A reader that falls behind loses the oldest items, and the loss is counted against that reader. |
| `RELIABLE_EVENT` | flight log, commands, failsafes, authority decisions | Kept until every required party has persisted it (a sink) or acked it. When the backlog is full, `publish` raises `BackpressureError`. A failed sink is retried. |

How channels are typed:

- A contract topic (`safety.failsafe`) only carries `EventEnvelope`s holding its registered payload.
- Any other channel declares the Python type it carries.

Each channel reports these metrics (`GET /api/bus`), which health reads:

- rate f
- delivered age p50/p95/p99
- depth
- drops by kind
- drop fraction
- consumer lag, in items and in seconds
- pending, rejected and sink failures (reliable channels only)

### Channels in the app

| Topic | Mode | Capacity | Producer | Required party | Readers |
|-------|------|----------|----------|----------------|---------|
| `flight.telemetry` | LATEST | 1 | `flight.tick` (10 Hz) | — | `ui.stream` |
| `flight.event` | RELIABLE | 1024 | flight core | `flight_db` sink | `ui.stream` (best effort) |
| `execution.command` | RELIABLE | 1024 | flight core | `flight_db` sink | — |
| `safety.failsafe` | RELIABLE | 256 | flight core | `flight_db` sink (contract record store) | — |
| `authority.decision` | RELIABLE | 1024 | VantaAuthority | `flight_db` sink (contract record store) | — |
| `system.health` | STREAM | 256 | `health.monitor` | — | — |
| `camera.frames.<source>` / `camera.preview.<source>` | LATEST | 2 / 1 | camera capture worker | — | vision / preview |

Failsafe triggers and authority decisions are stored as envelopes in
`autonomy_samples`. They show up in `/api/vantarace/recording` and replay
next to the race records.

## VantaRuntime: no unregistered background tasks

- **Spawning.** Long-running work starts through `spawn` or `spawn_periodic`. Structured fan-out inside a worker goes through `gather_children`.
- **Enforcement.** `test_no_service_launches_an_unregistered_background_task` fails if `create_task`, `ensure_future` or `threading.Thread` appears anywhere except `core/runtime.py`.
- **What a worker declares.** Every worker has a name, owner, criticality (`SAFETY` / `CRITICAL` / `NORMAL` / `BEST_EFFORT`) and restart policy. Restarts are bounded with backoff; there is never an infinite blind restart.
- **Periodic workers.** These also declare a period and a deadline. Deadline misses, skipped periods, step errors, duration and lateness p50/p95/p99, and busy fraction (checked against the `ResourceBudget`) are all measured.
- **Lifecycle.** `STARTING → READY → ACTIVE` (`DEGRADED` while a SAFETY or CRITICAL worker is failing or a SAFETY worker misses deadlines) `→ STOPPING → STOPPED`.
- **Status.** `GET /api/runtime/status` lists every worker plus `unmanaged_tasks`: any live task that nobody registered. The Performance page's services table now shows these real workers.

### Workers in the app

| Worker | Criticality | Period | Owner |
|--------|-------------|--------|-------|
| `flight.tick` | SAFETY | 100 ms | flight core: telemetry, failsafes, missions |
| `px4.telemetry:<addr>` (+ stream children) | SAFETY | — | PX4 adapter |
| `race.loop` | CRITICAL | 50 ms (own loop) | VantaRace |
| `vision.capture:<source>`, `vision.process` | CRITICAL | — | camera manager |
| `recorder.writer` | NORMAL | — | async recorder |
| `health.monitor` | NORMAL | 500 ms | VantaHealth |
| `bus.maintenance` | NORMAL | 1 s | retries failed sinks |
| `ui.stream` | BEST_EFFORT | 100 ms | WebSocket push |
| `ui.ws_client` | BEST_EFFORT | — | each WebSocket session (tracked) |

The flight tick and the UI stream used to be the same loop. A slow browser
could hold the failsafe check up by seconds. Now they are separate workers,
and `test_a_slow_ui_cannot_delay_the_flight_tick` pins that.

**Shutdown order** (`main.lifespan`):

1. abort the race and stop `race.loop`
2. stop `ui.stream`, then `flight.tick`
3. close the vehicle link (`controller.shutdown`)
4. drain the recorder
5. `system.stop()` stops whatever remains

Execution stops before adapters close, and the runtime ends with zero
registered workers.

## VantaHealth: five dimensions, not `connected: bool`

Each component is judged on LIVENESS, FRESHNESS, CORRECTNESS, RESOURCE and
DEPENDENCY. Each dimension is one of UNKNOWN, STARTING, HEALTHY, DEGRADED,
FAILED, RECOVERING or STOPPED, and the worst dimension wins. Probes:

- every bus channel (freshness against its promised rate, backlog, sink errors)
- every runtime worker (liveness, deadline misses and budget, step errors)
- the runtime (unmanaged tasks)
- `vehicle.link`: LIVENESS = connected, FRESHNESS = telemetry age. These are independent, so a stalled link that is still connected shows as fresh-FAILED but live-HEALTHY.
- `vehicle.battery` and `vehicle.position`: UNKNOWN when the link doesn't report them, never assumed.
- the recorder
- `flight.recording`: refused reliable records

`GET /api/health/components` returns the full picture. `/api/health` is unchanged.

## VantaAuthority: leases on every execution attempt

`FAILSAFE > EMERGENCY_OPERATOR > OPERATOR > APPROVED_MISSION_AUTOMATION > AGENT_PROPOSAL > TRAINING_OBSERVATION`

- **Race lease.** A race holds an `APPROVED_MISSION_AUTOMATION` lease (scope `trajectory`, `offboard`), approved by the operator who started it. The lease lasts 2 s and is renewed every tick.
- **Flight core check.** `offboard_setpoint` and `start_offboard` refuse anything without a live lease ("unauthorized" in the offboard stats).
- **Pilot override.** An operator override preempts the lease immediately, so a setpoint already in flight is refused.
- **Failsafe.** A failsafe preempts the lease and holds a lock-out until the aircraft is down and disarmed. A new race cannot start mid-recovery.
- **Agents and training.** These levels can never hold an execution scope on a physical vehicle (unknown vehicles count as physical). They may propose, observe, or act in simulation.
- **Lease rules.** Automation leases must expire (≤ 30 s). A stale or forged lease (wrong token) is refused, and disconnecting the vehicle revokes its leases.
- **Audit.** Every decision (grant, deny, revoke, release, expire, preempt) is recorded and published as `authority.decision`. `GET /api/authority` shows the live leases.

## Contributor contract for new workers and channels

Document these for any new asynchronous component:

- inputs and outputs
- clock domain
- frame and units
- update rate
- deadline
- backpressure policy
- failure mode
- safety classification
- persistence requirement
- test level

For channels, that means the table above. For workers, the name, criticality,
period/deadline and restart policy in the `spawn` call.

## Verification level

UNIT_TESTED and INTEGRATION_TESTED on the mock: 54 new tests, and the full
backend suite of 778 passes on the local stand-in runner. Not SITL_VERIFIED,
because GitHub Actions is locked by billing.
