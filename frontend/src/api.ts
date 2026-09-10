import type {
  AdapterType,
  AutonomyState,
  CampaignAnalysis,
  Capabilities,
  CameraProfile,
  CommandResult,
  CourseDetail,
  CourseGenerationRequest,
  Diagnostics,
  DiscoveredDrone,
  FaultProfileInfo,
  HardwareInfo,
  HealthResponse,
  FusedTargetEstimate,
  PerformanceState,
  RaceStateFrame,
  RuntimeState,
  RunMetric,
  RunSummary,
  SceneState,
  SimulationState,
  TrainingCampaign,
  TrainingRunResult,
  TrainingSummary,
  TwinState,
  VisionStatus,
  WsFrame,
} from "./types";

async function post(path: string, body?: unknown): Promise<CommandResult> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return (await res.json()) as CommandResult;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`request failed: ${res.status}`);
  return (await res.json()) as T;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`request failed: ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  connect: (adapterType?: AdapterType) =>
    post("/api/connect", adapterType ? { adapter_type: adapterType } : undefined),
  disconnect: () => post("/api/disconnect"),
  arm: () => post("/api/arm"),
  disarm: () => post("/api/disarm"),
  takeoff: (targetAltitudeM = 5) =>
    post("/api/takeoff", { target_altitude_m: targetAltitudeM }),
  hold: () => post("/api/hold"),
  land: () => post("/api/land"),

  health: () => get<HealthResponse>("/api/health"),
  discover: () => get<{ drones: DiscoveredDrone[] }>("/api/discover"),
  capabilities: () =>
    get<{ connected: boolean; capabilities: Capabilities | null }>("/api/capabilities").then(
      (r) => r.capabilities,
    ),
  twin: () => get<TwinState>("/api/twin"),
  runSummary: () =>
    get<{ source: string; summary: RunSummary }>("/api/run-summary").then((r) => r.summary),
  diagnostics: () => get<Diagnostics>("/api/diagnostics"),
  visionStatus: () => get<VisionStatus>("/api/vision/status"),
  cameraProfiles: () => get<CameraProfile[]>("/api/vision/camera-profiles"),
  visionTracks: () => get<FusedTargetEstimate[]>("/api/vision/tracks"),
  scene: () => get<SceneState>("/api/scene"),
  race: () => get<RaceStateFrame>("/api/race"),
  simulationStatus: () => get<SimulationState>("/api/simulation/status"),
  runMetrics: () => get<RunMetric[]>("/api/run-metrics"),
  generateCourse: (request: CourseGenerationRequest) =>
    postJson<CourseDetail>("/api/courses/generate", request),
  course: (courseId: string) =>
    get<CourseDetail>(`/api/courses/${encodeURIComponent(courseId)}`),

  runtimeStatus: () => get<RuntimeState>("/api/runtime/status"),
  performanceStatus: () => get<PerformanceState>("/api/runtime/performance"),
  hardwareInfo: () => get<HardwareInfo>("/api/runtime/hardware"),
  autonomyStatus: () => get<AutonomyState>("/api/autonomy/status"),

  listCampaigns: () => get<TrainingCampaign[]>("/api/training/campaigns"),
  createCampaign: (config: Record<string, unknown>) =>
    postJson<TrainingCampaign>("/api/training/campaigns", config),
  startCampaign: (id: string) => post(`/api/training/campaigns/${encodeURIComponent(id)}/start`),
  pauseCampaign: (id: string) => post(`/api/training/campaigns/${encodeURIComponent(id)}/pause`),
  cancelCampaign: (id: string) => post(`/api/training/campaigns/${encodeURIComponent(id)}/cancel`),
  campaignSummary: (id: string) =>
    get<TrainingSummary>(`/api/training/campaigns/${encodeURIComponent(id)}/summary`),
  campaignAnalysis: (id: string) =>
    get<CampaignAnalysis>(`/api/training/campaigns/${encodeURIComponent(id)}/analysis`),
  runNextTraining: (id: string) =>
    postJson<TrainingRunResult>(`/api/training/campaigns/${encodeURIComponent(id)}/run-next`, {}),
  autoCurriculum: (config: Record<string, unknown>) =>
    postJson<Record<string, unknown>>("/api/training/auto-curriculum", config),
  faultProfiles: () => get<Record<string, FaultProfileInfo>>("/api/training/fault-profiles"),
};

export function openTelemetryStream(
  onFrame: (frame: WsFrame) => void,
  onStatus: (online: boolean) => void,
): () => void {
  let socket: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = 1000;

  const url = () => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}/ws/telemetry`;
  };

  const connect = () => {
    socket = new WebSocket(url());
    socket.onopen = () => {
      onStatus(true);
      backoff = 1000;
    };
    socket.onmessage = (ev) => {
      try {
        onFrame(JSON.parse(ev.data) as WsFrame);
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onclose = () => {
      onStatus(false);
      if (!closed) {
        retry = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 1.5, 8000);
      }
    };
    socket.onerror = () => socket?.close();
  };

  connect();

  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    socket?.close();
  };
}
