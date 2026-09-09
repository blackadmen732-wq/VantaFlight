export type FlightMode = "IDLE" | "TAKEOFF" | "HOLD" | "LANDING" | "LAND";

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
}

export interface HealthResponse {
  status: string;
  version: string;
  session_state: SessionState;
}

export type WsFrame =
  | { type: "telemetry"; data: Telemetry }
  | { type: "event"; data: FlightEvent }
  | { type: "twin"; data: TwinState };

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
