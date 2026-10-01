import { useCallback, useEffect, useRef, useState } from "react";
import { api, openTelemetryStream } from "../api";
import {
  DISCONNECTED,
  type AutonomyState,
  type FlightEvent,
  type MissionStatus,
  type Telemetry,
  type TwinState,
  type TwinWorldSnapshot,
} from "../types";

export interface FlightStream {
  telemetry: Telemetry;
  twin: TwinState | null;
  snapshot: TwinWorldSnapshot | null;
  autonomy: AutonomyState | null;
  mission: MissionStatus | null;
  events: FlightEvent[];
  failsafe: FlightEvent | null;
  streamOnline: boolean;
  pushEvent: (ev: FlightEvent) => void;
  setMission: (m: MissionStatus | null) => void;
  clearTwin: () => void;
  dismissFailsafe: () => void;
}

const MAX_EVENTS = 60;

/**
 * One telemetry WebSocket per page, decoded into the state every flight page
 * needs. Only frames a page uses are kept; the rest (vision, scene, race...)
 * are ignored here rather than mistaken for events.
 */
export function useFlightStream(): FlightStream {
  const [telemetry, setTelemetry] = useState<Telemetry>(DISCONNECTED);
  const [twin, setTwin] = useState<TwinState | null>(null);
  const [snapshot, setSnapshot] = useState<TwinWorldSnapshot | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyState | null>(null);
  const [mission, setMission] = useState<MissionStatus | null>(null);
  const [events, setEvents] = useState<FlightEvent[]>([]);
  const [failsafe, setFailsafe] = useState<FlightEvent | null>(null);
  const [streamOnline, setStreamOnline] = useState(false);
  const seen = useRef(new Set<string>());

  const pushEvent = useCallback((ev: FlightEvent) => {
    const key = `${ev.timestamp}-${ev.event_type}-${ev.message}`;
    if (seen.current.has(key)) return;
    seen.current.add(key);
    setEvents((prev) => [ev, ...prev].slice(0, MAX_EVENTS));
  }, []);

  useEffect(() => {
    return openTelemetryStream((frame) => {
      switch (frame.type) {
        case "telemetry":
          setTelemetry(frame.data);
          if (!frame.data.connected) setTwin(null);
          break;
        case "twin":
          setTwin(frame.data);
          break;
        case "twin_snapshot":
          setSnapshot(frame.data);
          break;
        case "autonomy_state":
          setAutonomy(frame.data);
          break;
        case "mission":
          setMission(frame.data);
          break;
        case "event":
          pushEvent(frame.data);
          if (frame.data.event_type === "failsafe") setFailsafe(frame.data);
          break;
        default:
          break;
      }
    }, setStreamOnline);
  }, [pushEvent]);

  // Pick up a mission already in progress (e.g. after a page reload).
  useEffect(() => {
    api.mission.status().then(setMission).catch(() => {});
  }, []);

  return {
    telemetry,
    twin,
    snapshot,
    autonomy,
    mission,
    events,
    failsafe,
    streamOnline,
    pushEvent,
    setMission,
    clearTwin: useCallback(() => setTwin(null), []),
    dismissFailsafe: useCallback(() => setFailsafe(null), []),
  };
}
