import type {
  AdapterType,
  Capabilities,
  CommandResult,
  Diagnostics,
  DiscoveredDrone,
  HealthResponse,
  RunSummary,
  TwinState,
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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
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
