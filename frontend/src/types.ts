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

export type VisionLockState =
  | "SEARCHING"
  | "CANDIDATE"
  | "DETECTED"
  | "CONFIRMED"
  | "TRACKED"
  | "POSE_LOCKED"
  | "PREDICTIVE_LOCK"
  | "RACE_LOCK"
  | "DEGRADED"
  | "LOST";

export type RaceState =
  | "IDLE"
  | "SEARCH"
  | "ACQUIRE"
  | "LOCK"
  | "ALIGN"
  | "ACCELERATE"
  | "PASS"
  | "NEXT"
  | "RECOVER"
  | "COMPLETE";

export interface Vector3 {
  x: number;
  y: number;
  z: number;
}

export interface CameraProfile {
  camera_id: string;
  width: number;
  height: number;
  fps: number;
  focal_length_x?: number;
  focal_length_y?: number;
  camera_matrix: number[][];
  distortion_coefficients: number[];
  horizontal_fov_deg?: number;
  vertical_fov_deg?: number;
  mount_transform: number[][];
  estimated_capture_latency_s: number;
  calibration_version: string;
}

export interface FrameBufferMetrics {
  frames_captured: number;
  frames_processed: number;
  dropped_frames: number;
  queue_depth: number;
  oldest_frame_age_s: number;
  current_frame_age_s: number;
}

export interface VisionStatus {
  running: boolean;
  source: string | null;
  lock_state: VisionLockState;
  frame_metrics: FrameBufferMetrics;
  pipeline_latency_ms: number;
}

export interface FusedTargetEstimate {
  target_id: string;
  profile_id: string;
  observed_position?: Vector3;
  predicted_position?: Vector3;
  velocity: Vector3;
  confidence: number;
  uncertainty: number;
  measurement_age_s: number;
  track_state: VisionLockState;
  evidence: Record<string, string | number | boolean>;
}

export interface SceneState {
  timestamp: number;
  current?: FusedTargetEstimate;
  next?: FusedTargetEstimate;
  future?: FusedTargetEstimate;
}

export interface DesiredTrajectoryState {
  desired_position: Vector3;
  desired_velocity: Vector3;
  desired_acceleration: Vector3;
  desired_yaw: number;
  timestamp: number;
  trajectory_id: string;
  planner_confidence: number;
}

export interface RaceStateFrame {
  state: RaceState;
  timestamp: number;
  trajectory?: DesiredTrajectoryState;
  aggression_scale: number;
}

export interface SimulationState {
  run_id: string | null;
  course_id: string | null;
  status: "idle" | "running" | "completed" | "failed";
  timestamp: number;
}

export interface RunMetric {
  run_id: string;
  scope: "run" | "gate" | "segment";
  scope_id?: string;
  metric_name: string;
  metric_value: number;
  unit?: string;
  timestamp: number;
}

export type CourseMode =
  | "RANDOM"
  | "SLALOM"
  | "VERTICAL"
  | "TECHNICAL"
  | "SPEED_RUN"
  | "CHALLENGE"
  | "ADVERSARY";

export interface CourseGenerationRequest {
  seed: number;
  mode: CourseMode;
  gate_count: number;
  width: number;
  length: number;
  height: number;
  floor: number;
  ceiling: number;
  boundary_margin: number;
}

export interface CourseDetail {
  id: string;
  seed: number;
  mode: CourseMode;
  safe_volume: Record<string, unknown>;
  path: number[][];
  gates: Array<Record<string, unknown>>;
  difficulty: Record<string, number>;
}

export type WsFrame =
  | { type: "telemetry"; data: Telemetry }
  | { type: "event"; data: FlightEvent }
  | { type: "twin"; data: TwinState }
  | { type: "vision_state"; data: VisionStatus }
  | { type: "scene_state"; data: SceneState }
  | { type: "race_state"; data: RaceStateFrame }
  | { type: "simulation_state"; data: SimulationState }
  | { type: "run_metric"; data: RunMetric };

export interface RuntimeState {
  overall: string;
  deployment_mode: string;
  services: Record<string, { name: string; state: string; uptime_s: number; error: string | null }>;
  node: Record<string, unknown>;
}

export interface PerformanceState {
  level: string;
  frame_age_p95_ms: number;
  pipeline_p95_ms: number;
  cpu_pressure: number;
  memory_pressure: number;
  frame_drops: number;
  queue_depth: number;
  transition_count: number;
}

export interface AutonomyState {
  state: string;
  metrics: {
    loop_iterations: number;
    vision_results: number;
    plans_generated: number;
    setpoints_sent: number;
    gate_passes: number;
    recovery_events: number;
    failures: number;
    avg_loop_ms: number;
    state: string;
  };
  gate_progression: {
    gates_passed: number;
    total_gates: number;
    current_gate_index: number;
    is_complete: boolean;
    race_time_s: number;
    history: Array<{ gate_id: string; gate_order: number; race_time_s: number }>;
  };
  execution: {
    mode: string;
    has_permit: boolean;
    metrics: Record<string, number | string>;
  };
  planner_state: RaceState;
}

export interface HardwareInfo {
  cpu_arch: string;
  cpu_count: number;
  ram_total_mb: number;
  os_name: string;
  python_version: string;
  opencv_version: string;
  numpy_version: string;
  has_shared_memory: boolean;
}

export interface TrainingCampaign {
  campaign_id: string;
  name: string;
  state: string;
  total_runs: number;
  completed_runs: number;
}

export interface TrainingSummary {
  campaign_id: string;
  state: string;
  total_runs: number;
  completed_runs: number;
  successful_runs: number;
  failed_runs: number;
  success_rate: number;
  avg_gate_completion: number;
  avg_race_time_s: number;
  failure_breakdown: Record<string, number>;
  elapsed_s: number;
}

export interface TrainingRunResult {
  run_id: string;
  campaign_id: string;
  gates_passed: number;
  total_gates: number;
  race_time_s: number;
  complete: boolean;
  success: boolean;
  failures: string[];
  failure_categories: string[];
  gate_completion_rate: number;
  duration_s: number;
}

export interface CampaignAnalysis {
  campaign_id: string;
  total_runs: number;
  successes: number;
  failures: number;
  overall_success_rate: number;
  overall_gate_completion: number;
  overall_avg_time_s: number;
  tier_breakdown: Array<{
    tier: string;
    total_runs: number;
    successes: number;
    failures: number;
    success_rate: number;
    avg_gate_completion: number;
  }>;
  mode_breakdown: Array<{
    mode: string;
    total_runs: number;
    successes: number;
    success_rate: number;
    avg_gate_completion: number;
  }>;
  top_failures: Array<{ category: string; count: number }>;
  fault_impact: Record<string, number>;
}

export interface FaultProfileInfo {
  name: string;
  faults: Array<{
    fault_type: string;
    probability: number;
    duration_s: number;
    magnitude: number;
  }>;
}

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
