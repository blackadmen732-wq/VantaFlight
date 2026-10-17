import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import {
  ACTIVE_RACE_STATES,
  type RaceActionResult,
  type RaceCourse,
  type RaceCourseCheck,
  type RaceFaultKind,
  type RaceFinish,
  type RacePerception,
  type Telemetry,
  type VantaRaceStatus,
} from "../types";
import { useFlightStream } from "../hooks/useFlightStream";
import EventTimeline from "../components/EventTimeline";
import FailsafeBanner from "../components/FailsafeBanner";
import Metric from "../components/Metric";

const POLL_MS = 400;

const FAULTS: { kind: RaceFaultKind; label: string; duration: number; value?: number; hint: string }[] = [
  { kind: "vision_blackout", label: "Vision blackout 3 s", duration: 3, hint: "No camera frames: the loop must hold, then resume." },
  { kind: "low_confidence", label: "Low confidence 3 s", duration: 3, value: 0.05, hint: "Detections at 5% confidence: below the execution floor." },
  { kind: "occlude_gate", label: "Hide next gate 4 s", duration: 4, hint: "The next gate disappears: hold, search, reacquire." },
  { kind: "setpoint_delay", label: "Late setpoints 0.5 s", duration: 0.5, value: 0.6, hint: "Setpoints arrive 600 ms late: the flight core must refuse them." },
];

// Maps onto the shared status-pill tones.
const STATE_TONE: Record<string, string> = {
  RUNNING: "ok",
  COMPLETE: "done",
  PREPARING: "pending",
  RECOVERING: "pending",
  HOLDING_STALE: "pending",
  PAUSED: "pending",
  FINISHING: "pending",
  ABORTED: "off",
  FAILED: "off",
  IDLE: "done",
};

/**
 * VantaRace: the vision-driven racing loop (VantaSight → VantaState →
 * VantaRace → VantaExecution → offboard), simulation only. Pick a course,
 * start, and watch each stage of the loop live.
 */
export default function RacePage() {
  const stream = useFlightStream();
  const { telemetry, events, failsafe, dismissFailsafe, pushEvent } = stream;
  const [courses, setCourses] = useState<RaceCourse[]>([]);
  const [courseId, setCourseId] = useState("sitl_line_3");
  const [perception, setPerception] = useState<RacePerception>("vision");
  const [speed, setSpeed] = useState(3);
  const [finish, setFinish] = useState<RaceFinish>("land");
  const [check, setCheck] = useState<RaceCourseCheck | null>(null);
  const [status, setStatus] = useState<VantaRaceStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.vantarace.courses().then(setCourses).catch(() => setMessage("Backend unavailable. Start VantaFlight first."));
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      api.vantarace
        .status()
        .then((s) => alive && setStatus(s))
        .catch(() => {});
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!courseId) return;
    api.vantarace.check(courseId).then(setCheck).catch(() => setCheck(null));
  }, [courseId]);

  const act = useCallback(
    async (fn: () => Promise<RaceActionResult>) => {
      setBusy(true);
      try {
        const res = await fn();
        setMessage(res.message);
        if (!res.accepted) {
          pushEvent({ timestamp: Date.now() / 1000, event_type: "rejected", message: res.message });
        }
        setStatus(await api.vantarace.status().catch(() => status));
      } finally {
        setBusy(false);
      }
    },
    [pushEvent, status],
  );

  const active = status !== null && ACTIVE_RACE_STATES.includes(status.state);
  const shownCourse = active || (status?.course && status.state !== "IDLE")
    ? status?.course ?? null
    : courses.find((c) => c.course_id === courseId) ?? null;
  const raceEvents = useMemo(
    () => events.filter((e) => e.event_type.startsWith("race_") || e.event_type.startsWith("offboard") || e.event_type === "failsafe" || e.event_type === "rejected"),
    [events],
  );

  return (
    <div className="page-content race-page">
      <div className="page-header">
        <h2>VantaRace</h2>
        <span className={`status-pill ${STATE_TONE[status?.state ?? "IDLE"] ?? "done"}`} data-testid="race-state">
          {status?.state ?? "IDLE"}
        </span>
        <span className="muted small">simulation only · {telemetry.connected ? "aircraft connected" : "no aircraft"}</span>
      </div>

      {failsafe && <FailsafeBanner event={failsafe} onDismiss={dismissFailsafe} />}

      <div className="race-layout">
        <section className="card race-setup">
          <h3>Course</h3>
          <label className="field">
            <span>Course</span>
            <select value={courseId} disabled={active} onChange={(e) => setCourseId(e.target.value)}>
              {courses.map((c) => (
                <option key={c.course_id} value={c.course_id}>
                  {c.name} · {c.length_m.toFixed(0)} m
                </option>
              ))}
            </select>
          </label>
          {shownCourse && <p className="muted small">{shownCourse.description}</p>}
          <label className="field">
            <span>Perception</span>
            <select value={perception} disabled={active} onChange={(e) => setPerception(e.target.value as RacePerception)}>
              <option value="vision">VantaSight vision</option>
              <option value="scene">Course map (baseline)</option>
            </select>
          </label>
          <label className="field">
            <span>Speed limit · {speed.toFixed(1)} m/s</span>
            <input type="range" min={1} max={6} step={0.5} value={speed} disabled={active}
              onChange={(e) => setSpeed(Number(e.target.value))} aria-label="speed limit" />
          </label>
          <label className="field">
            <span>When finished</span>
            <select value={finish} disabled={active} onChange={(e) => setFinish(e.target.value as RaceFinish)}>
              <option value="land">Land</option>
              <option value="return_home">Return home</option>
              <option value="hold">Hold</option>
            </select>
          </label>
          {check && !check.valid && (
            <ul className="error-list">{check.errors.map((e) => <li key={e}>{e}</li>)}</ul>
          )}
          {check?.warnings.map((w) => <p key={w} className="muted small">⚠ {w}</p>)}

          <div className="button-row">
            {!active ? (
              <button className="btn primary wide" disabled={busy || !telemetry.connected || (check !== null && !check.valid)}
                onClick={() => act(() => api.vantarace.start({ course_id: courseId, perception, speed_m_s: speed, finish }))}>
                START RACE
              </button>
            ) : (
              <>
                {status?.state === "PAUSED" ? (
                  <button className="btn" disabled={busy} onClick={() => act(api.vantarace.resume)}>RESUME</button>
                ) : (
                  <button className="btn" disabled={busy || status?.state === "PREPARING" || status?.state === "FINISHING"}
                    onClick={() => act(api.vantarace.pause)}>PAUSE</button>
                )}
                <button className="btn danger" disabled={busy} onClick={() => act(api.vantarace.abort)}>ABORT</button>
              </>
            )}
          </div>
          {!telemetry.connected && (
            <p className="hint">Connect PX4 SITL (or the mock) on the Control page first.</p>
          )}
          {message && <p className="muted small" role="status">{message}</p>}
        </section>

        <section className="card race-map">
          <h3>Course map</h3>
          <CourseMap course={shownCourse} status={status} telemetry={telemetry} />
          {status && status.passes.length > 0 && (
            <table className="splits">
              <thead><tr><th>Gate</th><th>Split</th><th>Off centre</th></tr></thead>
              <tbody>
                {status.passes.map((p) => (
                  <tr key={p.gate_id}><td>{p.gate_id}</td><td>{p.time_s.toFixed(1)} s</td><td>{p.offset_m.toFixed(2)} m</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <LoopPanel status={status} />

      {active && (
        <section className="card race-faults">
          <h3>Rehearse a fault</h3>
          <div className="chip-row">
            {FAULTS.map((f) => (
              <button key={f.kind} className="chip" title={f.hint} disabled={busy || status?.state === "PREPARING"}
                onClick={() => act(() => api.vantarace.fault(f.kind, f.duration, f.value))}>
                {f.label}
              </button>
            ))}
          </div>
        </section>
      )}

      <EventTimeline events={raceEvents} title="Race events" />
    </div>
  );
}

/** Each stage of the loop, left to right, as the operator sees it. */
export function LoopPanel({ status }: { status: VantaRaceStatus | null }) {
  const p = status?.perception;
  const s = status?.stats;
  const exec = status?.autonomy?.execution;
  const age = exec?.metrics?.last_setpoint_age_ms;
  return (
    <section className="card loop-panel" aria-label="closed loop">
      <h3>Closed loop</h3>
      <div className="metrics">
        <Metric label="Gates" value={status && status.total_gates ? `${status.gates_passed}/${status.total_gates}` : "—"} />
        <Metric label="Race time" value={status ? `${status.race_time_s.toFixed(1)} s` : "—"} />
        <Metric label="VantaSight" value={status?.perception_status ?? "—"}
          warn={status?.perception_status === "LOST" || status?.perception_status === "BLACKOUT"} />
        <Metric label="Confidence" value={p ? p.current_confidence.toFixed(2) : "—"} warn={p ? p.current_confidence < 0.3 : false} />
        <Metric label="Detections" value={p ? String(p.detections) : "—"} />
        <Metric label="VantaRace" value={status?.autonomy?.planner_state ?? "—"} />
        <Metric label="Execution" value={exec?.mode ?? "—"} warn={exec?.mode === "HOLD"} />
        <Metric label="Setpoint age" value={age !== undefined ? `${age.toFixed(0)} ms` : "—"} warn={(age ?? 0) > 300} />
        <Metric label="Sent / refused" value={s ? `${s.setpoints_sent} / ${s.setpoints_rejected + s.setpoints_expired}` : "—"}
          warn={s ? s.setpoints_rejected + s.setpoints_expired > 0 : false} />
        <Metric label="Recoveries" value={s ? String(s.recoveries) : "—"} />
        <Metric label="Loop" value={s && s.ticks ? `${s.avg_tick_ms.toFixed(1)} ms avg` : "—"} />
      </div>
      {status?.message && <p className="muted small">{status.message}</p>}
    </section>
  );
}

/** Top-down ENU map: x east to the right, y north up. */
export function CourseMap({
  course,
  status,
  telemetry,
}: {
  course: RaceCourse | null;
  status: VantaRaceStatus | null;
  telemetry: Telemetry;
}) {
  if (!course) return <p className="empty">Choose a course.</p>;
  const passed = new Set(status?.passes.map((p) => p.gate_id) ?? []);
  const xs = [0, ...course.gates.map((g) => g.position[0]), telemetry.connected ? telemetry.x : 0];
  const ys = [0, ...course.gates.map((g) => g.position[1]), telemetry.connected ? telemetry.y : 0];
  const pad = 6;
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
  const w = maxX - minX, h = maxY - minY;
  const sx = (x: number) => x - minX;
  const sy = (y: number) => maxY - y;
  return (
    <svg className="course-map" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`map of ${course.name}`}>
      <circle cx={sx(0)} cy={sy(0)} r={1} className="home" />
      <polyline className="route" fill="none"
        points={[[0, 0], ...course.gates.map((g) => [g.position[0], g.position[1]])].map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")} />
      {course.gates.map((g) => {
        const [nx, ny] = g.normal;
        const half = g.width / 2;
        // The gate's face is perpendicular to its normal.
        const ex = -ny * half, ey = nx * half;
        const cls = passed.has(g.gate_id) ? "gate passed" : status?.next_gate === g.gate_id ? "gate next" : "gate";
        return (
          <g key={g.gate_id} data-testid={`gate-${g.gate_id}`} className={cls}>
            <line x1={sx(g.position[0] - ex)} y1={sy(g.position[1] - ey)} x2={sx(g.position[0] + ex)} y2={sy(g.position[1] + ey)} />
            <text x={sx(g.position[0]) + 1} y={sy(g.position[1]) - 1.5}>{g.order + 1}</text>
          </g>
        );
      })}
      {telemetry.connected && (
        <circle cx={sx(telemetry.x)} cy={sy(telemetry.y)} r={0.9} className="aircraft" data-testid="aircraft" />
      )}
    </svg>
  );
}
