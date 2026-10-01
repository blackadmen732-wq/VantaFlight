import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Link } from "react-router-dom";
import { api, openTelemetryStream } from "./api";
import {
  ACTIVE_MISSION_STATES,
  type AdapterType,
  type Airspace,
  type CommandResult,
  type RunSummary,
} from "./types";
import { useFlightStream } from "./hooks/useFlightStream";
import AdapterSelector from "./components/AdapterSelector";
import DigitalTwin from "./components/DigitalTwin";
import DiagnosticsPanel from "./components/DiagnosticsPanel";
import EventTimeline from "./components/EventTimeline";
import Metric from "./components/Metric";
import FailsafeBanner from "./components/FailsafeBanner";
import { MissionProgress } from "./components/MissionPanel";
import RunSummaryCard from "./components/RunSummaryCard";
import SimulationLab from "./pages/SimulationLab";
import VisionPage from "./pages/VisionPage";
import PerformancePage from "./pages/PerformancePage";
import ReplayPage from "./pages/ReplayPage";
import TrainingPage from "./pages/TrainingPage";
import HopperSetupPage from "./pages/HopperSetupPage";
import MissionPage from "./pages/MissionPage";
import TwinPage from "./pages/TwinPage";
import ForgePage from "./pages/ForgePage";
import EvolutionPage from "./pages/EvolutionPage";

const AIRBORNE_EPS = 0.15;
/** Show the link age once telemetry is older than this (seconds). */
const LINK_AGE_WARN_S = 1.0;

function FlightDashboard() {
  const stream = useFlightStream();
  const { telemetry, twin, mission, streamOnline, pushEvent } = stream;
  const [busy, setBusy] = useState(false);
  const [adapter, setAdapter] = useState<AdapterType>("mock");
  const [runSummary, setRunSummary] = useState<RunSummary | null>(null);
  const [showDiag, setShowDiag] = useState(false);
  const [airspace, setAirspace] = useState<Airspace | null>(null);

  useEffect(() => {
    api.airspace.get().then(setAirspace).catch(() => {});
  }, []);

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
      stream.dismissFailsafe();
      stream.setMission(null);
    }
    return result;
  };

  const handleDisconnect = async () => {
    const result = await api.disconnect();
    if (result.accepted) {
      stream.clearTwin();
      try {
        const summary = await api.runSummary();
        if (summary && summary.duration > 0) setRunSummary(summary);
      } catch { /* no summary available */ }
    }
    return result;
  };

  const connected = telemetry.connected;
  const armed = telemetry.armed;
  const altitudeKnown = telemetry.altitude_available !== false;
  const batteryKnown = telemetry.battery_available !== false;
  const velocityKnown = telemetry.velocity_available !== false;
  const positionKnown = telemetry.position_available !== false;
  const airborne = connected && altitudeKnown && telemetry.altitude > AIRBORNE_EPS;
  const linkAge = telemetry.link_age_s ?? 0;
  const missionActive = mission !== null && ACTIVE_MISSION_STATES.includes(mission.state);

  return (
    <>
      <section className="top-bar">
        <AdapterSelector selected={adapter} onSelect={setAdapter} disabled={connected} />
        <button className="diag-toggle" onClick={() => setShowDiag(!showDiag)}>
          {showDiag ? "Hide Diagnostics" : "Diagnostics"}
        </button>
      </section>

      {showDiag && <DiagnosticsPanel wsConnected={streamOnline} />}
      <FailsafeBanner event={stream.failsafe} onDismiss={stream.dismissFailsafe} />
      {runSummary && <RunSummaryCard summary={runSummary} onDismiss={() => setRunSummary(null)} />}

      <section className="status-card">
        <div className="aircraft-line">
          <span className="label">Aircraft</span>
          {connected ? (
            <span className="badge connected">CONNECTED</span>
          ) : (
            <span className="badge disconnected">{streamOnline ? "SEARCHING FOR AIRCRAFT" : "DISCONNECTED"}</span>
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
          <Metric
            label="Battery"
            value={batteryKnown ? `${telemetry.battery_percentage.toFixed(1)}%` : "UNKNOWN"}
            warn={batteryKnown && telemetry.battery_percentage < 20}
          />
          <Metric label="Altitude" value={altitudeKnown ? `${telemetry.altitude.toFixed(2)} m` : "UNKNOWN"} />
          <Metric label="Speed" value={velocityKnown ? `${telemetry.velocity.toFixed(2)} m/s` : "UNKNOWN"} />
          <Metric
            label="Position"
            value={
              positionKnown
                ? `${telemetry.x.toFixed(1)}, ${telemetry.y.toFixed(1)}, ${telemetry.z.toFixed(1)}`
                : "UNKNOWN"
            }
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
        <button className="btn" disabled={busy || !connected || airborne} onClick={() => run(armed ? api.disarm : api.arm)}>
          {armed ? "DISARM" : "ARM"}
        </button>
        <button
          className="btn"
          disabled={busy || !connected || !armed || airborne}
          onClick={() => run(() => api.takeoff(5))}
        >
          TAKEOFF
        </button>
        <button className="btn" disabled={busy || !airborne} onClick={() => run(api.hold)}>HOLD</button>
        <button className="btn" disabled={busy || !airborne} onClick={() => run(api.land)}>LAND</button>
        <button className="btn" disabled={busy || !airborne} onClick={() => run(api.returnHome)}>RETURN HOME</button>
      </section>

      <section className="mission-strip">
        {missionActive && mission ? (
          <>
            <MissionProgress status={mission} />
            <div className="mission-actions">
              {mission.state === "RUNNING" ? (
                <button className="btn" disabled={busy} onClick={() => run(api.mission.pause)}>PAUSE</button>
              ) : (
                <button className="btn" disabled={busy} onClick={() => run(api.mission.resume)}>RESUME</button>
              )}
              <button className="btn danger" disabled={busy} onClick={() => run(api.mission.abort)}>ABORT</button>
            </div>
          </>
        ) : (
          <p className="hint">
            No mission running. Plan routes, no-fly zones and patterns on the <Link to="/mission">Mission</Link> page.
          </p>
        )}
      </section>

      <div className="twin-events-layout">
        <DigitalTwin
          twin={twin}
          plan={missionActive && mission ? mission.plan : null}
          activeWaypoint={missionActive && mission ? mission.current_index : null}
          zones={airspace?.zones ?? []}
        />
        <EventTimeline events={stream.events} />
      </div>
    </>
  );
}

const NAV: Array<{ to: string; label: string }> = [
  { to: "/", label: "Control" },
  { to: "/mission", label: "Mission" },
  { to: "/twin", label: "Twin" },
  { to: "/forge", label: "VantaForge" },
  { to: "/hopper", label: "Hopper" },
  { to: "/vision", label: "Vision" },
  { to: "/sim", label: "Sim Lab" },
  { to: "/training", label: "Training" },
  { to: "/evolution", label: "Evolution" },
  { to: "/performance", label: "Performance" },
  { to: "/replay", label: "Replay" },
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header className="brand">
          <img className="logo-mark" src="/vantaflight-icon.svg" alt="" width={32} height={32} />
          VantaFlight
          <nav className="nav-links">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}>
                {n.label}
              </NavLink>
            ))}
          </nav>
        </header>

        <Routes>
          <Route path="/" element={<FlightDashboard />} />
          <Route path="/mission" element={<MissionPage />} />
          <Route path="/twin" element={<TwinPage />} />
          <Route path="/forge" element={<ForgePage />} />
          <Route path="/hopper" element={<HopperSetupPage />} />
          <Route path="/vision" element={<VisionWrapper />} />
          <Route path="/sim" element={<SimLabWrapper />} />
          <Route path="/training" element={<TrainingPage />} />
          <Route path="/evolution" element={<EvolutionPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/replay" element={<ReplayPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

function useStreamOnline(): boolean {
  const [online, setOnline] = useState(false);
  useEffect(() => openTelemetryStream(() => {}, setOnline), []);
  return online;
}

function VisionWrapper() {
  return <VisionPage wsConnected={useStreamOnline()} />;
}

function SimLabWrapper() {
  return <SimulationLab wsConnected={useStreamOnline()} />;
}
