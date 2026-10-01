import { useCallback, useEffect, useRef, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { api, openTelemetryStream } from "./api";
import {
  ACTIVE_MISSION_STATES,
  DISCONNECTED,
  type AdapterType,
  type Airspace,
  type CommandResult,
  type FlightEvent,
  type MissionPlan,
  type MissionStatus,
  type RunSummary,
  type Telemetry,
  type TwinState,
} from "./types";
import AdapterSelector from "./components/AdapterSelector";
import AirspacePanel from "./components/AirspacePanel";
import DigitalTwin from "./components/DigitalTwin";
import DiagnosticsPanel from "./components/DiagnosticsPanel";
import MissionPanel from "./components/MissionPanel";
import RunSummaryCard from "./components/RunSummaryCard";
import SimulationLab from "./pages/SimulationLab";

const AIRBORNE_EPS = 0.15;
/** Show the link age once telemetry is older than this (seconds). */
const LINK_AGE_WARN_S = 1.0;

function FlightDashboard() {
  const [telemetry, setTelemetry] = useState<Telemetry>(DISCONNECTED);
  const [twin, setTwin] = useState<TwinState | null>(null);
  const [events, setEvents] = useState<FlightEvent[]>([]);
  const [streamOnline, setStreamOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [adapter, setAdapter] = useState<AdapterType>("mock");
  const [runSummary, setRunSummary] = useState<RunSummary | null>(null);
  const [showDiag, setShowDiag] = useState(false);
  const [mission, setMission] = useState<MissionStatus | null>(null);
  const [draftPlan, setDraftPlan] = useState<MissionPlan | null>(null);
  const [failsafe, setFailsafe] = useState<FlightEvent | null>(null);
  const [airspace, setAirspace] = useState<Airspace | null>(null);
  const seen = useRef(new Set<string>());

  const pushEvent = useCallback((ev: FlightEvent) => {
    const key = `${ev.timestamp}-${ev.event_type}-${ev.message}`;
    if (seen.current.has(key)) return;
    seen.current.add(key);
    setEvents((prev) => [ev, ...prev].slice(0, 60));
  }, []);

  useEffect(() => {
    return openTelemetryStream((frame) => {
      if (frame.type === "telemetry") {
        setTelemetry(frame.data);
        if (!frame.data.connected) setTwin(null);
      } else if (frame.type === "twin") {
        setTwin(frame.data);
      } else if (frame.type === "mission") {
        setMission(frame.data);
      } else {
        pushEvent(frame.data);
        if (frame.data.event_type === "failsafe") setFailsafe(frame.data);
      }
    }, setStreamOnline);
  }, [pushEvent]);

  // Pick up a mission already in progress (e.g. after a page reload).
  useEffect(() => {
    api.mission.status().then(setMission).catch(() => {});
  }, []);

  const refreshAirspace = useCallback(() => {
    api.airspace.get().then(setAirspace).catch(() => {});
  }, []);
  useEffect(refreshAirspace, [refreshAirspace]);

  const reportResult = useCallback(
    (res: CommandResult) => {
      if (!res.accepted) {
        pushEvent({
          timestamp: Date.now() / 1000,
          event_type: "rejected",
          message: `${res.command} rejected: ${res.message}`,
        });
      }
    },
    [pushEvent],
  );

  const run = async (fn: () => Promise<CommandResult>) => {
    setBusy(true);
    try {
      reportResult(await fn());
    } finally {
      setBusy(false);
    }
  };

  const handleConnect = async () => {
    const result = await api.connect(adapter);
    if (result.accepted) {
      setRunSummary(null);
      setFailsafe(null);
      setMission(null);
    }
    return result;
  };

  const handleDisconnect = async () => {
    const result = await api.disconnect();
    if (result.accepted) {
      setTwin(null);
      try {
        const summary = await api.runSummary();
        if (summary && summary.duration > 0) setRunSummary(summary);
      } catch { /* no summary available */ }
    }
    return result;
  };

  const connected = telemetry.connected;
  const armed = telemetry.armed;
  const airborne = connected && telemetry.altitude > AIRBORNE_EPS;
  const linkAge = telemetry.link_age_s ?? 0;
  const missionActive = mission !== null && ACTIVE_MISSION_STATES.includes(mission.state);
  const twinPlan = missionActive ? mission.plan : draftPlan;

  return (
    <>
      <section className="top-bar">
        <AdapterSelector
          selected={adapter}
          onSelect={setAdapter}
          disabled={connected}
        />
        <button
          className="diag-toggle"
          onClick={() => setShowDiag(!showDiag)}
        >
          {showDiag ? "Hide Diagnostics" : "Diagnostics"}
        </button>
      </section>

      {showDiag && <DiagnosticsPanel wsConnected={streamOnline} />}

      {failsafe && (
        <div className="failsafe-banner" role="alert">
          <strong>FAILSAFE</strong>
          <span>{failsafe.message}</span>
          <button className="icon-btn" aria-label="dismiss failsafe" onClick={() => setFailsafe(null)}>
            &times;
          </button>
        </div>
      )}

      {runSummary && (
        <RunSummaryCard summary={runSummary} onDismiss={() => setRunSummary(null)} />
      )}

      <section className="status-card">
        <div className="aircraft-line">
          <span className="label">Aircraft</span>
          {connected ? (
            <span className="badge connected">CONNECTED</span>
          ) : (
            <span className="badge disconnected">
              {streamOnline ? "SEARCHING FOR AIRCRAFT" : "DISCONNECTED"}
            </span>
          )}
          <span className={`quality-badge quality-${telemetry.connection_quality.toLowerCase()}`}>
            {telemetry.connection_quality}
          </span>
          {connected && linkAge > LINK_AGE_WARN_S && (
            <span className="link-age" title="time since the last message from the aircraft">
              LINK {linkAge.toFixed(1)}s
            </span>
          )}
        </div>

        <div className="metrics">
          <Metric label="Battery" value={`${telemetry.battery_percentage.toFixed(1)}%`} warn={telemetry.battery_percentage < 20} />
          <Metric label="Altitude" value={`${telemetry.altitude.toFixed(2)} m`} />
          <Metric label="Speed" value={`${telemetry.velocity.toFixed(2)} m/s`} />
          <Metric
            label="Position"
            value={`${telemetry.x.toFixed(1)}, ${telemetry.y.toFixed(1)}, ${telemetry.z.toFixed(1)}`}
          />
          <Metric label="Flight Mode" value={telemetry.flight_mode} />
          <Metric label="State" value={armed ? "ARMED" : "DISARMED"} />
        </div>
      </section>

      <section className="controls">
        <button
          className={connected ? "btn danger" : "btn primary"}
          disabled={busy}
          onClick={() => run(connected ? handleDisconnect : handleConnect)}
        >
          {connected ? "DISCONNECT" : "CONNECT"}
        </button>
        <button
          className="btn"
          disabled={busy || !connected || airborne}
          onClick={() => run(armed ? api.disarm : api.arm)}
        >
          {armed ? "DISARM" : "ARM"}
        </button>
        <button
          className="btn"
          disabled={busy || !connected || !armed || airborne}
          onClick={() => run(() => api.takeoff(5))}
        >
          TAKEOFF
        </button>
        <button className="btn" disabled={busy || !airborne} onClick={() => run(api.hold)}>
          HOLD
        </button>
        <button className="btn" disabled={busy || !airborne} onClick={() => run(api.land)}>
          LAND
        </button>
        <button className="btn" disabled={busy || !airborne} onClick={() => run(api.returnHome)}>
          RETURN HOME
        </button>
      </section>

      <MissionPanel
        connected={connected}
        armed={armed}
        status={mission}
        onResult={reportResult}
        onPlanChange={setDraftPlan}
        airspace={airspace}
        onAirspaceChanged={refreshAirspace}
      />

      <AirspacePanel airspace={airspace} onChanged={refreshAirspace} />

      <div className="twin-events-layout">
        <DigitalTwin
          twin={twin}
          plan={twinPlan}
          activeWaypoint={missionActive ? mission.current_index : null}
          zones={airspace?.zones ?? []}
        />

        <section className="timeline">
          <h2>Event Timeline</h2>
          {events.length === 0 ? (
            <p className="empty">No events yet.</p>
          ) : (
            <ul>
              {events.map((ev, i) => (
                <li key={i} className={`ev ${ev.event_type}`}>
                  <span className="ts">{formatTime(ev.timestamp)}</span>
                  <span className="tag" title={ev.event_type}>{ev.event_type.replace(/_/g, " ")}</span>
                  <span className="msg">{ev.message}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header className="brand">
          <span className="logo">&#9650;</span> VantaFlight
          <nav className="nav-links">
            <NavLink to="/" end>Control</NavLink>
            <NavLink to="/sim">Sim Lab</NavLink>
          </nav>
        </header>

        <Routes>
          <Route path="/" element={<FlightDashboard />} />
          <Route path="/sim" element={<SimLabWrapper />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

function SimLabWrapper() {
  const [wsOnline, setWsOnline] = useState(false);
  useEffect(() => {
    const unsub = openTelemetryStream(() => {}, setWsOnline);
    return unsub;
  }, []);
  return <SimulationLab wsConnected={wsOnline} />;
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={`metric ${warn ? "metric-warn" : ""}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}
