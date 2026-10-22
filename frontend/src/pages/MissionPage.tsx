import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  ACTIVE_MISSION_STATES,
  type Airspace,
  type CommandResult,
  type GoalMission,
  type GoalMissionType,
  type MissionPlan,
} from "../types";
import { useFlightStream } from "../hooks/useFlightStream";
import AirspacePanel from "../components/AirspacePanel";
import DigitalTwin from "../components/DigitalTwin";
import EventTimeline from "../components/EventTimeline";
import FailsafeBanner from "../components/FailsafeBanner";
import MissionPanel from "../components/MissionPanel";

/**
 * Mission planner: waypoint missions and patterns, route optimisation around
 * no-fly zones, QGroundControl import/export, and the typed mission registry
 * (race, search & rescue, delivery, emergency response).
 */
export default function MissionPage() {
  const stream = useFlightStream();
  const { telemetry, twin, mission } = stream;
  const [airspace, setAirspace] = useState<Airspace | null>(null);
  const [draftPlan, setDraftPlan] = useState<MissionPlan | null>(null);

  const refreshAirspace = useCallback(() => {
    api.airspace.get().then(setAirspace).catch(() => {});
  }, []);
  useEffect(refreshAirspace, [refreshAirspace]);

  const { pushEvent } = stream;
  const reportResult = useCallback(
    (res: CommandResult) => {
      if (res.accepted) return; // accepted actions arrive as events on the stream
      pushEvent({
        timestamp: Date.now() / 1000,
        event_type: "rejected",
        message: `${res.command} rejected: ${res.message}`,
      });
    },
    [pushEvent],
  );

  const missionActive = mission !== null && ACTIVE_MISSION_STATES.includes(mission.state);

  return (
    <div className="page-content mission-page">
      <div className="page-header">
        <h2>Mission Planner</h2>
        <span className={`status-pill ${telemetry.connected ? "ok" : "off"}`}>
          {telemetry.connected ? `${telemetry.armed ? "ARMED" : "DISARMED"} · ${telemetry.flight_mode}` : "NO AIRCRAFT"}
        </span>
      </div>
      <p className="hint">
        Plans are checked against the geofence, no-fly zones and battery before launch. Connect and arm
        on the Control page; a mission takes off by itself.
      </p>

      <FailsafeBanner event={stream.failsafe} onDismiss={stream.dismissFailsafe} />

      <div className="mission-layout">
        <div className="mission-col">
          <MissionPanel
            connected={telemetry.connected}
            armed={telemetry.armed}
            status={mission}
            onResult={reportResult}
            onPlanChange={setDraftPlan}
            airspace={airspace}
            onAirspaceChanged={refreshAirspace}
          />
          <AirspacePanel airspace={airspace} onChanged={refreshAirspace} />
        </div>
        <div className="mission-col">
          <DigitalTwin
            twin={twin}
            plan={missionActive && mission ? mission.plan : draftPlan}
            activeWaypoint={missionActive && mission ? mission.current_index : null}
            zones={airspace?.zones ?? []}
          />
          <EventTimeline events={stream.events} title="Mission Events" />
        </div>
      </div>

      <MissionRegistryPanel />
    </div>
  );
}

const MISSION_TYPES: Array<{ type: GoalMissionType; label: string; hint: string }> = [
  { type: "RACE", label: "Race", hint: "Fly the gates of a course as fast as possible." },
  { type: "SEARCH_RESCUE", label: "Search & Rescue", hint: "Lawnmower sweep of a search area." },
  { type: "PACKAGE_DELIVERY", label: "Delivery", hint: "Fly to a drop point and back." },
  { type: "EMERGENCY_RESPONSE", label: "Emergency", hint: "Reach a target point fast, then hold." },
];

/** The V0.9 typed mission registry, persisted in SQLite across restarts. */
export function MissionRegistryPanel() {
  const [missions, setMissions] = useState<GoalMission[]>([]);
  const [type, setType] = useState<GoalMissionType>("SEARCH_RESCUE");
  const [description, setDescription] = useState("");
  const [area, setArea] = useState({ x0: -20, y0: -20, x1: 20, y1: 20, z: 10 });
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.missions.list().then(setMissions).catch((e) => setError(String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  const create = async () => {
    setError(null);
    const target = [area.x1, area.y1, area.z];
    try {
      await api.missions.create({
        mission_type: type,
        description: description || MISSION_TYPES.find((m) => m.type === type)?.label || type,
        search_area: type === "SEARCH_RESCUE" ? [[area.x0, area.y0, area.z], [area.x1, area.y1, area.z]] : [],
        delivery_target: type === "PACKAGE_DELIVERY" || type === "EMERGENCY_RESPONSE" ? target : null,
        waypoints: type === "RACE" ? [{ x: area.x0, y: area.y0, z: area.z }, { x: area.x1, y: area.y1, z: area.z }] : [],
      });
      setDescription("");
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  const num = (key: keyof typeof area) => (
    <input
      type="number"
      aria-label={key}
      value={area[key]}
      onChange={(e) => setArea({ ...area, [key]: Number(e.target.value) })}
    />
  );

  return (
    <section className="sim-section mission-registry">
      <h3>Mission Library</h3>
      <p className="hint">
        Typed missions for the autonomy stack. They are saved locally and survive restarts; a mission that was
        running when the app closed is marked aborted.
      </p>
      <div className="chip-row" role="radiogroup" aria-label="mission type">
        {MISSION_TYPES.map((m) => (
          <button
            key={m.type}
            role="radio"
            aria-checked={type === m.type}
            className={`chip ${type === m.type ? "active" : ""}`}
            title={m.hint}
            onClick={() => setType(m.type)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="registry-form">
        <label>
          Name
          <input value={description} placeholder="Describe the mission" onChange={(e) => setDescription(e.target.value)} />
        </label>
        {type === "SEARCH_RESCUE" || type === "RACE" ? (
          <>
            <label>From x {num("x0")}</label>
            <label>y {num("y0")}</label>
            <label>To x {num("x1")}</label>
            <label>y {num("y1")}</label>
          </>
        ) : (
          <>
            <label>Target x {num("x1")}</label>
            <label>y {num("y1")}</label>
          </>
        )}
        <label>Alt {num("z")}</label>
        <button className="btn primary" onClick={create}>CREATE</button>
      </div>
      {error && <p className="error-msg">{error}</p>}
      {missions.length === 0 ? (
        <p className="empty">No saved missions yet.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Type</th>
              <th>Name</th>
              <th>Status</th>
              <th>Phase</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {missions.map((m) => (
              <tr key={m.mission_id}>
                <td>{m.mission_id}</td>
                <td>{m.mission_type.replace(/_/g, " ")}</td>
                <td>{m.goal.description}</td>
                <td>
                  <span className={`status-pill ${statusClass(m.status)}`}>{m.status}</span>
                </td>
                <td>{m.phase}</td>
                <td className="row-actions">
                  {m.status === "PENDING" && (
                    <button className="btn small" onClick={() => act(() => api.missions.start(m.mission_id))}>START</button>
                  )}
                  {(m.status === "PENDING" || m.status === "ACTIVE") && (
                    <button className="btn small danger" onClick={() => act(() => api.missions.abort(m.mission_id))}>ABORT</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function statusClass(status: GoalMission["status"]): string {
  if (status === "ACTIVE") return "ok";
  if (status === "COMPLETE") return "done";
  if (status === "PENDING") return "pending";
  return "off";
}
