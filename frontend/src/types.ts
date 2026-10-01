export type FlightMode =
  | "IDLE"
  | "TAKEOFF"
  | "HOLD"
  | "LANDING"
  | "LAND"
  | "MISSION"
  | "RETURNING";

export type ConnectionQuality =
  | "NONE"
  | "POOR"
  | "FAIR"
  | "GOOD"
  | "EXCELLENT";

export type AdapterType = "mock" | "px4_sitl";

export type SessionState =
  | "NO_SESSION"
  | "CONNECTED"
  | "ACTIVE"
  | "INTERRUPTED"
  | "COMPLETED";

export interface Telemetry {
  timestamp: number;
  connected: boolean;
  armed: boolean;
  flight_mode: FlightMode;
  x: number;
  y: number;
  z: number;
  altitude: number;
  velocity: number;
  heading: number;
  battery_percentage: number;
  connection_quality: ConnectionQuality;
  latitude?: number;
  longitude?: number;
  ground_speed?: number;
  /** Seconds since the last message actually received from the aircraft. */
  link_age_s?: number;
}

export interface FlightEvent {
  timestamp: number;
  event_type: string;
  message: string;
}

export interface CommandResult {
  command: string;
  accepted: boolean;
  message: string;
  timestamp: number;
}

export interface DiscoveredDrone {
  drone_id: string;
  name: string;
  adapter_type: AdapterType;
  transport: string;
  address: string;
  capabilities?: string[];
}

export interface Capabilities {
  name: string;
  adapter_type: string;
  is_simulated: boolean;
  supports_gps: boolean;
  supports_velocity: boolean;
  supports_heading: boolean;
  supports_battery: boolean;
  supports_goto?: boolean;
  supports_return?: boolean;
  supported_capabilities: string[];
}

export interface TwinState {
  connected: boolean;
  armed: boolean;
  altitude: number;
  x: number;
  y: number;
  z: number;
  heading: number;
  velocity: number;
  flight_mode: string;
  battery_percentage: number;
  flight_duration: number;
  trajectory: TrajectoryPoint[];
}

export interface TrajectoryPoint {
  timestamp: number;
  x: number;
  y: number;
  z: number;
  heading: number;
}

export interface RunSummary {
  duration: number;
  max_altitude: number;
  max_speed: number;
  battery_start: number;
  battery_end: number;
  command_count: number;
  connection_interruptions: number;
  final_status: string;
}

export interface DiagnosticsMetrics {
  telemetry_hz: number;
  avg_command_rtt_ms: number;
  avg_db_write_ms: number;
}

export interface Diagnostics {
  version: string;
  session_state: SessionState;
  flight_id: number | null;
  ws_clients: number;
  adapter: string | null;
  metrics: DiagnosticsMetrics;
  twin_active: boolean;
  link_age_s?: number;
  mission_state?: MissionState;
  failsafe?: FailsafeStatus;
}

// ── Missions ─────────────────────────────────────────────────

/** Local coordinates: metres east (x) / north (y) of home, metres up. */
export interface Waypoint {
  x: number;
  y: number;
  altitude: number;
  hold_s?: number;
  speed_m_s?: number | null;
  /** "via" points were added by the route planner to get around a zone. */
  kind?: "stop" | "via";
}

export type FinishAction = "land" | "return_home" | "hold";

export interface MissionPlan {
  name: string;
  waypoints: Waypoint[];
  speed_m_s: number;
  finish: FinishAction;
}

export type MissionState = "IDLE" | "RUNNING" | "PAUSED" | "COMPLETED" | "ABORTED";

export interface MissionStatus {
  state: MissionState;
  phase: "TAKEOFF" | "TRANSIT" | "LOITER" | "DONE";
  name: string | null;
  current_index: number;
  waypoints_reached: number;
  total_waypoints: number;
  progress: number;
  distance_to_target_m: number;
  elapsed_s: number;
  message: string;
  plan: MissionPlan | null;
}

export interface PlanReport {
  valid: boolean;
  errors: string[];
  warnings: string[];
  distance_m: number;
  estimated_duration_s: number;
  estimated_battery_pct: number;
}

export type PatternKind = "square" | "orbit" | "survey";

export interface FailsafeTrigger {
  reason: string;
  action: "hold" | "return" | "land";
  message: string;
}

export interface FailsafeStatus {
  config: {
    battery_low_pct: number;
    battery_critical_pct: number;
    link_stale_s: number;
    geofence: { radius_m: number; max_altitude_m: number };
  };
  fired: string[];
  last_trigger: FailsafeTrigger | null;
}

export type FaultKind = "battery" | "link_stall";

// ── Airspace & routing ───────────────────────────────────────

/** A no-fly zone as sent to the server: a circle or a polygon. */
export type ZoneSpec =
  | { name: string; center: [number, number]; radius: number }
  | { name: string; vertices: [number, number][] };

export interface AirspaceZone {
  id: string;
  name: string;
  vertices: [number, number][];
}

export interface Airspace {
  margin_m: number;
  zones: AirspaceZone[];
}

export interface StopBudget {
  stop: number;
  arrival_battery_pct: number;
  battery_to_get_home_pct: number;
  margin_pct: number;
}

export interface RoutePlan {
  plan: MissionPlan;
  order: number[];
  method: "exact" | "heuristic" | "fixed";
  given_order_m: number;
  distance_m: number;
  saved_m: number;
  saved_pct: number;
  detour_points: number;
  duration_s: number;
  battery_used_pct: number;
  battery_start_pct: number;
  battery_end_pct: number;
  feasible: boolean;
  point_of_no_return: number | null;
  budgets: StopBudget[];
  warnings: string[];
}

/** A QGroundControl .plan document (passed through as-is). */
export type QgcPlan = Record<string, unknown>;

export interface HealthResponse {
  status: string;
  version: string;
  session_state: SessionState;
}

export type WsFrame =
  | { type: "telemetry"; data: Telemetry }
  | { type: "event"; data: FlightEvent }
  | { type: "twin"; data: TwinState }
  | { type: "mission"; data: MissionStatus };

export const ACTIVE_MISSION_STATES: MissionState[] = ["RUNNING", "PAUSED"];

export const DISCONNECTED: Telemetry = {
  timestamp: 0,
  connected: false,
  armed: false,
  flight_mode: "IDLE",
  x: 0,
  y: 0,
  z: 0,
  altitude: 0,
  velocity: 0,
  heading: 0,
  battery_percentage: 0,
  connection_quality: "NONE",
};
