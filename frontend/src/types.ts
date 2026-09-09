// Mirror of the backend's normalized telemetry model. Keeping this in one place
// means the UI never assumes anything about which drone it is talking to.

export type FlightMode = "IDLE" | "TAKEOFF" | "HOLD" | "LANDING";

export type ConnectionQuality =
  | "NONE"
  | "POOR"
  | "FAIR"
  | "GOOD"
  | "EXCELLENT";

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

export type WsFrame =
  | { type: "telemetry"; data: Telemetry }
  | { type: "event"; data: FlightEvent };

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
