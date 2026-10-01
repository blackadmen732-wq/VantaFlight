import { useEffect, useState } from "react";
import { api } from "../api";
import {
  ACTIVE_MISSION_STATES,
  type CommandResult,
  type FinishAction,
  type MissionPlan,
  type MissionStatus,
  type PatternKind,
  type PlanReport,
  type Waypoint,
} from "../types";

interface Props {
  connected: boolean;
  armed: boolean;
  status: MissionStatus | null;
  /** Report a command outcome (e.g. to the event timeline). */
  onResult: (result: CommandResult) => void;
  /** Called whenever the draft plan changes, so the twin can preview it. */
  onPlanChange?: (plan: MissionPlan) => void;
}

const PATTERNS: { kind: PatternKind; label: string; params: Record<string, number> }[] = [
  { kind: "square", label: "Square", params: { size_m: 20, altitude_m: 10 } },
  { kind: "orbit", label: "Orbit", params: { radius_m: 15, altitude_m: 10, points: 12 } },
  { kind: "survey", label: "Survey", params: { width_m: 40, height_m: 30, spacing_m: 10, altitude_m: 15 } },
];

const FINISH_LABELS: Record<FinishAction, string> = {
  return_home: "Return home & land",
  land: "Land at last waypoint",
  hold: "Hover at last waypoint",
};

export const DEFAULT_PLAN: MissionPlan = {
  name: "Quick hop",
  waypoints: [
    { x: 10, y: 0, altitude: 8, hold_s: 0 },
    { x: 10, y: 10, altitude: 8, hold_s: 0 },
  ],
  speed_m_s: 5,
  finish: "return_home",
};

export default function MissionPanel({ connected, armed, status, onResult, onPlanChange }: Props) {
  const [plan, setPlan] = useState<MissionPlan>(DEFAULT_PLAN);
  const [report, setReport] = useState<PlanReport | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const state = status?.state ?? "IDLE";
  const active = ACTIVE_MISSION_STATES.includes(state);

  useEffect(() => {
    onPlanChange?.(plan);
    // Validate as the operator edits, but not on every keystroke.
    const id = setTimeout(() => {
      api.mission
        .validate(plan)
        .then((r) => {
          setReport(r);
          setCheckError(null);
        })
        .catch((err: unknown) => {
          setReport(null);
          setCheckError(err instanceof Error ? err.message : "could not check plan");
        });
    }, 300);
    return () => clearTimeout(id);
  }, [plan, onPlanChange]);

  const run = async (fn: () => Promise<CommandResult>) => {
    setBusy(true);
    try {
      onResult(await fn());
    } finally {
      setBusy(false);
    }
  };

  const loadPattern = async (kind: PatternKind, params: Record<string, number>) => {
    try {
      const res = await api.mission.pattern(kind, params);
      if (res.ok && res.plan) setPlan(res.plan);
      else if (res.error) setCheckError(res.error);
    } catch {
      setCheckError("could not load pattern");
    }
  };

  const updateWaypoint = (i: number, patch: Partial<Waypoint>) =>
    setPlan((p) => ({
      ...p,
      waypoints: p.waypoints.map((w, j) => (j === i ? { ...w, ...patch } : w)),
    }));

  const removeWaypoint = (i: number) =>
    setPlan((p) => ({ ...p, waypoints: p.waypoints.filter((_, j) => j !== i) }));

  const addWaypoint = () =>
    setPlan((p) => {
      const last = p.waypoints[p.waypoints.length - 1] ?? { x: 0, y: 0, altitude: 8 };
      return { ...p, waypoints: [...p.waypoints, { x: last.x + 10, y: last.y, altitude: last.altitude, hold_s: 0 }] };
    });

  const canStart = connected && armed && !active && !busy && report?.valid === true;
  const startHint = !connected
    ? "Connect to an aircraft to fly this plan."
    : !armed
      ? "Arm the aircraft to start the mission."
      : null;

  return (
    <section className="mission-panel">
      <div className="mission-header">
        <h2>Mission</h2>
        <span className={`mission-state state-${state.toLowerCase()}`}>{state}</span>
      </div>

      {status && state !== "IDLE" && <MissionProgress status={status} />}

      {!active && (
        <>
          <div className="pattern-row">
            <span className="field-label">Start from</span>
            {PATTERNS.map((p) => (
              <button key={p.kind} className="chip" onClick={() => loadPattern(p.kind, p.params)}>
                {p.label}
              </button>
            ))}
          </div>

          <div className="plan-settings">
            <label>
              <span className="field-label">Name</span>
              <input
                value={plan.name}
                onChange={(e) => setPlan({ ...plan, name: e.target.value })}
              />
            </label>
            <label>
              <span className="field-label">Speed (m/s)</span>
              <NumberInput value={plan.speed_m_s} onChange={(v) => setPlan({ ...plan, speed_m_s: v })} />
            </label>
            <label>
              <span className="field-label">When done</span>
              <select
                value={plan.finish}
                onChange={(e) => setPlan({ ...plan, finish: e.target.value as FinishAction })}
              >
                {(Object.keys(FINISH_LABELS) as FinishAction[]).map((f) => (
                  <option key={f} value={f}>{FINISH_LABELS[f]}</option>
                ))}
              </select>
            </label>
          </div>

          <table className="waypoint-table">
            <thead>
              <tr>
                <th>#</th>
                <th title="metres east of home">East (m)</th>
                <th title="metres north of home">North (m)</th>
                <th>Alt (m)</th>
                <th>Hold (s)</th>
                <th aria-label="remove" />
              </tr>
            </thead>
            <tbody>
              {plan.waypoints.map((w, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td><NumberInput value={w.x} onChange={(v) => updateWaypoint(i, { x: v })} /></td>
                  <td><NumberInput value={w.y} onChange={(v) => updateWaypoint(i, { y: v })} /></td>
                  <td><NumberInput value={w.altitude} onChange={(v) => updateWaypoint(i, { altitude: v })} /></td>
                  <td><NumberInput value={w.hold_s ?? 0} onChange={(v) => updateWaypoint(i, { hold_s: v })} /></td>
                  <td>
                    <button className="icon-btn" aria-label={`remove waypoint ${i + 1}`} onClick={() => removeWaypoint(i)}>
                      &times;
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="chip" onClick={addWaypoint}>+ Add waypoint</button>

          <PlanCheck report={report} error={checkError} />
        </>
      )}

      <div className="mission-actions">
        {!active && (
          <button
            className="btn primary"
            disabled={!canStart}
            onClick={() => run(() => api.mission.start(plan))}
          >
            START MISSION
          </button>
        )}
        {state === "RUNNING" && (
          <button className="btn" disabled={busy} onClick={() => run(api.mission.pause)}>PAUSE</button>
        )}
        {state === "PAUSED" && (
          <button className="btn primary" disabled={busy} onClick={() => run(api.mission.resume)}>RESUME</button>
        )}
        {active && (
          <button className="btn danger" disabled={busy} onClick={() => run(api.mission.abort)}>ABORT</button>
        )}
        {!active && startHint && <span className="hint">{startHint}</span>}
      </div>
    </section>
  );
}

export function MissionProgress({ status }: { status: MissionStatus }) {
  const pct = Math.round(status.progress * 100);
  const where =
    status.phase === "TAKEOFF"
      ? "Climbing to mission altitude"
      : status.phase === "LOITER"
        ? `Holding at waypoint ${status.current_index + 1}`
        : status.phase === "TRANSIT"
          ? `Flying to waypoint ${status.current_index + 1} of ${status.total_waypoints} (${status.distance_to_target_m.toFixed(1)} m)`
          : status.message;
  return (
    <div className="mission-progress">
      <div className="progress-line">
        <strong>{status.name}</strong>
        <span>{status.waypoints_reached}/{status.total_waypoints} waypoints &middot; {formatDuration(status.elapsed_s)}</span>
      </div>
      <div className="progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="progress-detail">{where}</p>
    </div>
  );
}

function PlanCheck({ report, error }: { report: PlanReport | null; error: string | null }) {
  if (error) return <div className="plan-check bad">{error}</div>;
  if (!report) return null;
  return (
    <div className={`plan-check ${report.valid ? "good" : "bad"}`}>
      {report.valid ? (
        <span>
          {report.distance_m.toFixed(0)} m &middot; ~{formatDuration(report.estimated_duration_s)} &middot; ~
          {report.estimated_battery_pct.toFixed(0)}% battery
        </span>
      ) : (
        <ul>{report.errors.map((e) => <li key={e}>{e}</li>)}</ul>
      )}
      {report.warnings.map((w) => <div key={w} className="plan-warning">{w}</div>)}
    </div>
  );
}

function NumberInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => {
    if (Number(text) !== value) setText(String(value));
    // Only resync when the value changes from outside (e.g. a pattern load).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <input
      type="number"
      inputMode="decimal"
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isFinite(n)) onChange(n);
      }}
    />
  );
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}
