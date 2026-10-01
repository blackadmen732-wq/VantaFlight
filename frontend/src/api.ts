import type {
  AdapterType,
  Airspace,
  AutonomyState,
  CameraProfile,
  CampaignAnalysis,
  Capabilities,
  CommandResult,
  CourseDetail,
  CourseGenerationRequest,
  Diagnostics,
  DiscoveredDrone,
  FailsafeStatus,
  FaultKind,
  FaultProfileInfo,
  FinishAction,
  FusedTargetEstimate,
  HardwareInfo,
  HealthResponse,
  MissionPlan,
  MissionStatus,
  PatternKind,
  PerformanceState,
  PlanReport,
  QgcPlan,
  RaceStateFrame,
  RoutePlan,
  RunMetric,
  RunSummary,
  RuntimeState,
  SceneState,
  SimulationState,
  TrainingCampaign,
  TrainingRunResult,
  TrainingSummary,
  TwinState,
  VisionStatus,
  Waypoint,
  WsFrame,
  ZoneSpec,
} from "./types";

function describeError(data: unknown, status: number): string {
  const body = data as { detail?: unknown; message?: unknown } | null;
  const detail = body?.detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? "invalid"}`,
      )
      .join("; ");
  }
  if (typeof detail === "string") return detail;
  if (typeof body?.message === "string") return body.message;
  return `request failed (${status})`;
}

async function parseError(res: Response): Promise<Error> {
  try {
    return new Error(describeError(await res.json(), res.status));
  } catch {
    return new Error(`request failed (${res.status})`);
  }
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.ok === false) throw await parseError(res);
  return (await res.json()) as T;
}

/**
 * POST a command. Never throws: a network failure or a rejected request comes
 * back as a not-accepted result, so the UI always has a message to show.
 */
async function post(path: string, body?: unknown): Promise<CommandResult> {
  const command = path.replace(/^\/api\//, "");
  try {
    return await postJson<CommandResult>(path, body);
  } catch (err) {
    return {
      command,
      accepted: false,
      message: err instanceof Error ? err.message : "flight core unreachable",
      timestamp: Date.now() / 1000,
    };
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw await parseError(res);
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
  returnHome: () => post("/api/return-home"),

  mission: {
    status: () => get<MissionStatus>("/api/mission"),
    validate: (plan: MissionPlan) => postJson<PlanReport>("/api/mission/validate", plan),
    start: (plan: MissionPlan) => post("/api/mission/start", plan),
    pause: () => post("/api/mission/pause"),
    resume: () => post("/api/mission/resume"),
    abort: () => post("/api/mission/abort"),
    pattern: (kind: PatternKind, params: Record<string, number> = {}) =>
      postJson<{ ok: boolean; error: string | null; plan: MissionPlan | null }>(
        "/api/mission/pattern",
        { kind, params },
      ),
  },

  airspace: {
    get: () => get<Airspace>("/api/airspace"),
    set: (zones: ZoneSpec[], marginM?: number) =>
      postJson<{ ok: boolean; error: string | null; airspace: Airspace }>("/api/airspace", {
        zones,
        margin_m: marginM,
      }),
  },

  route: {
    optimize: (req: {
      stops: Waypoint[];
      optimize_order?: boolean;
      finish?: FinishAction;
      speed_m_s?: number;
      name?: string;
    }) =>
      postJson<{ ok: boolean; error: string | null; route: RoutePlan | null }>(
        "/api/route/optimize",
        req,
      ),
    exportPlan: (plan: MissionPlan, includeAirspace = true) =>
      postJson<QgcPlan>("/api/mission/export", { plan, include_airspace: includeAirspace }),
    importPlan: (doc: QgcPlan) =>
      postJson<{
        ok: boolean;
        error: string | null;
        plan?: MissionPlan;
        zones?: ZoneSpec[];
        warnings?: string[];
      }>("/api/mission/import", doc),
  },

  failsafe: () => get<FailsafeStatus>("/api/failsafe"),
  injectFault: (kind: FaultKind, value: number) => post("/api/sim/fault", { kind, value }),

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
    postJson<TrainingRunResult | { status: string; campaign_id: string }>(
      `/api/training/campaigns/${encodeURIComponent(id)}/run-next`,
      {},
    ),
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
