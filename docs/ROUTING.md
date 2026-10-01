# Routing Engine (V0.4)

**The problem it solves:** you know the places the drone has to visit and the
places it must never fly over. You want the shortest route that visits them
all safely, and you want to know before take-off whether the battery can
actually do it, and from where it could no longer get home.

Give VantaFlight the stops and the no-fly zones and it returns a ready-to-fly
mission that flies the same on the simulator and PX4 and opens in QGroundControl.

## What it does

| Step | How | Quality |
|------|-----|---------|
| Safe distance between every pair of stops | Visibility graph around the zones' convex corners (offset by the safety margin) + A* | True shortest path for polygonal zones at the requested clearance. Measured within 0.15% of the exact geometric optimum |
| Best visiting order | Held-Karp dynamic programming up to 12 stops; above that, nearest-neighbour + seeded restarts polished with 2-opt and Or-opt | ≤12 stops: provably optimal (tested against brute force). 12-stop benchmark: the heuristic matched the optimum on every instance. 30 stops with 5 zones plans in about 0.5 s |
| Detours | The corner points each leg needs are inserted as `via` waypoints | Every leg is re-checked against the zones |
| Safe way home | If the straight line home crosses a zone, the route flies the safe path home and lands there instead of using a straight return-to-launch | Same pathfinder |
| Battery budget | Battery on arrival at each stop, battery needed to get home *from that stop* (along the safe path), and the margin left above the reserve | Flags the **point of no return**: the first stop from which it could not get home with the reserve intact |

Distances are the *safe* flying distances, so the order is optimized for the
route the aircraft will really fly, not straight lines through a stadium.

## Using it

1. **No-fly zones** panel (Control page): add circles (east, north, radius),
   set the safety margin (default 5 m). Polygons can be set through the API or
   imported from a QGroundControl plan.
2. **Mission** panel: enter or load your stops, then press **Optimize route**.
   The summary shows the distance, how much shorter it is than the order you
   entered, the detours, the battery from start to finish, and any
   point-of-no-return warning. Detour points show as `via` rows.
3. **Start mission** as usual.

## Safety integration

- Mission plans are checked leg by leg: a leg that crosses a zone (or its
  margin) is refused, with a hint to use Optimize route.
- The command gate refuses a `goto` whose target is inside a zone or whose
  straight flight would enter one. The gate checks the zone itself, not the
  planning margin, so normal tracking error on a planned route never trips it.
- **Return home** (button or the low-battery / geofence failsafe) flies the
  shortest safe path home when the straight line would cross a zone. With no
  safe path it holds position and logs why.
- A zone may not cover home.

## Working with QGroundControl, PX4 and ArduPilot

**Export .plan** writes a standard QGroundControl plan: takeoff, waypoints
(with hold times), speed changes, return-to-launch or land, the no-fly zones as
exclusion polygons, and the geofence as an inclusion circle. Open it in
QGroundControl to review it or upload it to any PX4 or ArduPilot aircraft.

**Import .plan** reads those items back, plus exclusion polygons and circles,
which are added as no-fly zones. Items VantaFlight can't fly (survey "complex
items", camera triggers, jumps, …) are skipped and listed, never dropped
silently. Coordinates are converted around the plan's home; exports use the
aircraft's own home on PX4, or `VANTAFLIGHT_HOME_LAT/LON/ALT_M` on the
simulator.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/api/airspace` | Current zones and margin |
| POST | `/api/airspace` | `{zones: [{name, center:[x,y], radius} \| {name, vertices:[[x,y],...]}], margin_m}` |
| POST | `/api/route/optimize` | `{stops: [Waypoint], optimize_order, finish, speed_m_s, name}` → route + plan |
| POST | `/api/mission/export` | `{plan, include_airspace}` → QGroundControl plan JSON |
| POST | `/api/mission/import` | QGroundControl plan JSON → `{plan, zones, warnings}` (doesn't change the airspace) |

## Limits

- Zones are 2D (infinite height). Altitude-limited airspace isn't modelled.
- The battery model is a time-based estimate tuned to the simulator. Real
  airframes need calibrating (wind, payload, temperature are not modelled).
- A native return-to-launch started by the aircraft itself (e.g. PX4's own
  failsafes) still flies a straight line. Only VantaFlight-initiated returns
  are zone-aware.
