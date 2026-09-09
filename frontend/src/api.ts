// Thin client for the local Flight Core. Commands go over REST; telemetry and
// events stream over a WebSocket.

import type { CommandResult, WsFrame } from "./types";

async function post(path: string, body?: unknown): Promise<CommandResult> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return (await res.json()) as CommandResult;
}

export const api = {
  connect: () => post("/api/connect"),
  disconnect: () => post("/api/disconnect"),
  arm: () => post("/api/arm"),
  disarm: () => post("/api/disarm"),
  takeoff: (targetAltitudeM = 5) =>
    post("/api/takeoff", { target_altitude_m: targetAltitudeM }),
  hold: () => post("/api/hold"),
  land: () => post("/api/land"),
};

/**
 * Open a resilient telemetry stream. Reconnects automatically if the socket
 * drops, so a backend restart or transient loss recovers on its own.
 */
export function openTelemetryStream(
  onFrame: (frame: WsFrame) => void,
  onStatus: (online: boolean) => void,
): () => void {
  let socket: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const url = () => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}/ws/telemetry`;
  };

  const connect = () => {
    socket = new WebSocket(url());
    socket.onopen = () => onStatus(true);
    socket.onmessage = (ev) => {
      try {
        onFrame(JSON.parse(ev.data) as WsFrame);
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onclose = () => {
      onStatus(false);
      if (!closed) retry = setTimeout(connect, 1000);
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
