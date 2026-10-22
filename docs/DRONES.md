# Drones: what connects, what flies, what to buy

VantaFlight never spins motors itself. The drone's own flight controller keeps
it stable; VantaFlight sends high-level commands (take off, hold, land, go
there) through the flight core, which checks every one (geofence, battery,
link freshness) and runs the failsafes. Autonomous racing (VantaRace) is
**simulation-only**: it is refused on any physical aircraft.

## Supported today

| Drone | Link | What VantaFlight does | Limits |
|---|---|---|---|
| **Mock** | none (in-process) | everything: missions, failsafes, VantaRace, training | it's a simulator |
| **PX4 SITL** (Gazebo) | MAVLink/UDP via MAVSDK | missions, failsafes, VantaRace (offboard) | needs an x86 Linux PC for the simulator; VantaRace on PX4 not yet SITL-verified |
| **FTW Hopper** | its Wi-Fi `Hopper_####` | camera observation (`http://192.168.2.1`), vision | **cannot fly it**: FTW's flight link is closed Bluetooth. Fly it with FTWCode; ask FTW (ftw-robotics.ai/contact) for the integrator ICD |
| **DJI/Ryze Tello, Tello EDU** | its Wi-Fi `TELLO-XXXXXX`, SDK text over UDP | arm (ready), takeoff to a height, hold, land, battery/height/speed telemetry, failsafes (low battery → land, stale link → hold) | no position (no GPS): no missions, goto or return-home; no autonomy; discontinued in the US, buy used |

A real PX4/ArduPilot aircraft (MAVLink over Wi-Fi or a telemetry radio) uses
the same PX4 adapter: missions and failsafes, no autonomy.

## What to buy next (if you want to script real flights)

| Option | Price (approx.) | Why |
|---|---|---|
| Used **Tello / Tello EDU** | ~$100 | Works with VantaFlight now, pure Wi-Fi, no USB passthrough. Discontinued: frozen firmware. |
| **Crazyflie 2.1+** starter bundle (with Crazyradio) | ~$270 | Open source, precise indoors, can run PX4. Needs a VantaFlight adapter (not written yet) and USB passthrough of the Crazyradio — test `lsusb` in Crostini inside the return window. |
| **Holybro PX4 dev kit (X500 v2)** | ~$600 | Real PX4 outdoors; VantaFlight's PX4 adapter already speaks to it. Not a first drone. |

## Rules (US) and safety

- Everyone flying for fun takes the free FAA **TRUST** test first.
- Under 250 g and recreational (Hopper, Tello, Crazyflie): no registration, no Remote ID.
- 250 g or more: register ($5, 3 years) and Remote ID.
- Any paid/commercial use: Part 107 certificate, registration regardless of weight.
- Test indoors first: cleared room, prop guards, people behind you, hand ready
  to land. Order of trust: **mock → simulator → real drone, small and slow.**
