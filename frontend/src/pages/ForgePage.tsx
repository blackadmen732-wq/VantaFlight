import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { CourseDetail, CourseMode, CourseValidation, Vec3 } from "../types";
import WorldView, { type WorldScene } from "../components/WorldView";
import {
  DIFFICULTY_LABELS,
  courseGates,
  courseStats,
  courseVolume,
  pathPoints,
  type CourseGate,
} from "../forge/courseStats";

export const COURSE_TYPES: Array<{ mode: CourseMode; label: string; blurb: string }> = [
  { mode: "RANDOM", label: "Random", blurb: "Endless unique courses" },
  { mode: "SLALOM", label: "Slalom", blurb: "Tight cornering practice" },
  { mode: "VERTICAL", label: "Vertical", blurb: "Climbs and dives" },
  { mode: "TECHNICAL", label: "Technical", blurb: "Complex manoeuvres" },
  { mode: "SPEED_RUN", label: "Speed Run", blurb: "Long straights, fast turns" },
  { mode: "CHALLENGE", label: "Challenge", blurb: "Everything at once" },
  { mode: "ADVERSARY", label: "Adversary", blurb: "Targets your weaknesses" },
];

const STEPS = [
  { title: "Define space", detail: "Set the room or field you can fly in" },
  { title: "Generate course", detail: "Path and gates placed inside it" },
  { title: "Validate", detail: "Clearances, spacing and flyability" },
  { title: "Train & evolve", detail: "Run campaigns, find weaknesses" },
  { title: "Deploy", detail: "Fly it for real (needs hardware)" },
];

const PATH_COLOR = 0x35a7ff;
const GATE_COLOR = 0xff3b5c;
const FIRST_GATE_COLOR = 0x2ecc71;

function courseScene(course: CourseDetail | null): WorldScene {
  if (!course) return {};
  const vol = courseVolume(course);
  const [w, l] = vol.dimensions;
  const gates = courseGates(course);
  return {
    bounds: { min: [-w / 2, -l / 2, vol.floor], max: [w / 2, l / 2, vol.ceiling] },
    paths: [{ points: pathPoints(course), color: PATH_COLOR, opacity: 0.95 }],
    gates: gates.map((g, i) => ({
      position: g.center,
      normal: g.normal,
      width: g.size[0],
      height: g.size[1],
      color: i === 0 ? FIRST_GATE_COLOR : GATE_COLOR,
    })),
    boxes: vol.obstacles.map((o) => ({ center: o.center, size: o.size, color: 0x8b98a9 })),
  };
}

export default function ForgePage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<CourseMode>("RANDOM");
  const [dims, setDims] = useState({ width: 30, length: 50, height: 12 });
  const [gateCount, setGateCount] = useState(10);
  const [seed, setSeed] = useState(1);
  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [validation, setValidation] = useState<CourseValidation | null>(null);
  const [history, setHistory] = useState<CourseDetail[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);

  const scene = useMemo(() => courseScene(course), [course]);
  const stats = useMemo(() => (course ? courseStats(course) : null), [course]);
  const gates = useMemo(() => (course ? courseGates(course) : []), [course]);

  const generate = async (nextSeed = seed) => {
    setBusy(true);
    setError(null);
    setTrainMsg(null);
    try {
      const c = await api.generateCourse({
        seed: nextSeed,
        mode,
        gate_count: gateCount,
        width: dims.width,
        length: dims.length,
        height: dims.height,
        floor: 0,
        ceiling: dims.height,
        boundary_margin: 1.5,
      });
      setCourse(c);
      setHistory((h) => [c, ...h.filter((x) => x.id !== c.id)].slice(0, 8));
      setValidation(null);
      api.validateCourse(c.id).then(setValidation).catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const randomSeed = () => {
    const s = Math.floor(Math.random() * 100000);
    setSeed(s);
    void generate(s);
  };

  const train = async () => {
    if (!course) return;
    setTrainMsg(null);
    try {
      const c = await api.createCampaign({
        name: `${course.mode} x${course.gates.length} gates`,
        description: `From VantaForge course ${course.id}`,
        course_modes: [course.mode],
        seed_range: [course.seed, course.seed + 5],
        gate_counts: [course.gates.length],
        difficulty_tiers: ["MODERATE"],
      });
      setTrainMsg(`Created ${c.name}. Opening Training…`);
      navigate("/training");
    } catch (e) {
      setTrainMsg(`Could not create a campaign: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // Steps before `step` are done; `step` is the one to do next.
  const step = !course ? 0 : !validation ? 2 : 3;

  const dimInput = (key: keyof typeof dims, label: string) => (
    <label className="forge-field">
      <span>{label}</span>
      <input
        type="number"
        min={2}
        step={0.5}
        aria-label={label}
        value={dims[key]}
        onChange={(e) => setDims({ ...dims, [key]: Number(e.target.value) })}
      />
    </label>
  );

  return (
    <div className="page-content forge-page">
      <div className="forge-title">
        <div>
          <h2>
            VantaForge <span className="accent">CourseLab</span>
          </h2>
          <p className="muted">Automatic 3D course generation, validation and training.</p>
        </div>
        <span className="forge-tagline">GENERATE · VALIDATE · TRAIN · IMPROVE</span>
      </div>

      <ol className="forge-steps">
        {STEPS.map((s, i) => (
          <li key={s.title} className={i < step ? "done" : i === step ? "current" : ""}>
            <span className="step-num">{i + 1}</span>
            <div>
              <strong>{s.title}</strong>
              <span>{s.detail}</span>
            </div>
          </li>
        ))}
      </ol>

      <div className="forge-grid">
        <section className="forge-card forge-setup">
          <h3>1. Define space</h3>
          <div className="forge-dims">
            {dimInput("width", "Width (m)")}
            {dimInput("length", "Length (m)")}
            {dimInput("height", "Height (m)")}
          </div>
          <label className="forge-field">
            <span>Gates</span>
            <input
              type="number"
              min={3}
              max={64}
              aria-label="Gates"
              value={gateCount}
              onChange={(e) => setGateCount(Number(e.target.value))}
            />
          </label>
          <label className="forge-field">
            <span>Seed</span>
            <input type="number" aria-label="Seed" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          </label>
          <div className="forge-actions">
            <button className="btn primary" disabled={busy} onClick={() => generate()}>
              {busy ? "GENERATING…" : "GENERATE COURSE"}
            </button>
            <button className="btn" disabled={busy} onClick={randomSeed} title="New random seed">
              SURPRISE ME
            </button>
          </div>
          {error && <p className="error-msg">{error}</p>}
          <p className="hint">The same seed and settings always produce the same course.</p>
        </section>

        <section className="forge-card forge-view">
          <div className="forge-card-head">
            <h3>{course ? `Generated course · ${course.mode.replace("_", " ")} #${course.seed}` : "Generated 3D course"}</h3>
            {validation && (
              <span className={`status-pill ${validation.valid ? "ok" : "off"}`}>
                {validation.valid ? "VALID" : "INVALID"}
              </span>
            )}
          </div>
          {course ? (
            <WorldView scene={scene} frameKey={course.id} height={400} />
          ) : (
            <div className="forge-empty">Pick a course type and press Generate.</div>
          )}
          {validation && (validation.errors.length > 0 || (validation.warnings?.length ?? 0) > 0) && (
            <ul className="forge-issues">
              {validation.errors.map((e) => <li key={e} className="err">{e}</li>)}
              {(validation.warnings ?? []).map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
        </section>

        <section className="forge-card forge-stats">
          <h3>Course stats</h3>
          {stats ? (
            <dl>
              <Stat label="Total gates" value={String(stats.gateCount)} />
              <Stat label="Course length" value={`${stats.lengthM.toFixed(1)} m`} />
              <Stat label="Min altitude" value={`${stats.minAltitudeM.toFixed(1)} m`} />
              <Stat label="Max altitude" value={`${stats.maxAltitudeM.toFixed(1)} m`} />
              <Stat
                label="Tightest turn radius"
                value={Number.isFinite(stats.tightestTurnM) ? `${stats.tightestTurnM.toFixed(1)} m` : "straight"}
              />
              <Stat label="Predicted speed" value={`${stats.predictedSpeedMs.toFixed(1)} m/s`} />
              <Stat label="Estimated lap" value={stats.estimatedLapS ? `${stats.estimatedLapS.toFixed(1)} s` : "—"} />
            </dl>
          ) : (
            <p className="empty">No course yet.</p>
          )}
          {course && (
            <button className="btn primary wide" onClick={train}>
              TRAIN ON THIS COURSE TYPE
            </button>
          )}
          {trainMsg && <p className="hint">{trainMsg}</p>}
        </section>
      </div>

      {course && (
        <div className="forge-grid forge-lower">
          <section className="forge-card">
            <h3>Top view</h3>
            <Projection course={course} axis="top" />
          </section>
          <section className="forge-card">
            <h3>Side view</h3>
            <Projection course={course} axis="side" />
          </section>
          <section className="forge-card">
            <h3>Difficulty</h3>
            <DifficultyBars difficulty={course.difficulty} />
          </section>
          <section className="forge-card">
            <h3>Gate preview</h3>
            <GatePreview gate={gates[0] ?? null} />
          </section>
        </div>
      )}

      <section className="forge-card">
        <h3>Course types</h3>
        <div className="course-types">
          {COURSE_TYPES.map((t) => (
            <button
              key={t.mode}
              className={`course-type ${mode === t.mode ? "active" : ""}`}
              aria-pressed={mode === t.mode}
              onClick={() => setMode(t.mode)}
            >
              <CourseGlyph mode={t.mode} />
              <strong>{t.label}</strong>
              <span>{t.blurb}</span>
            </button>
          ))}
        </div>
      </section>

      {history.length > 1 && (
        <section className="forge-card">
          <h3>This session</h3>
          <div className="chip-row">
            {history.map((c) => (
              <button key={c.id} className={`chip ${course?.id === c.id ? "active" : ""}`} onClick={() => setCourse(c)}>
                {c.mode} #{c.seed} · {c.gates.length} gates
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="forge-card forge-loop">
        <h3>Training loop</h3>
        <ol className="loop-steps">
          <li>Generate a new course</li>
          <li>Simulate and analyse it in Training</li>
          <li>Find weaknesses on the Evolution page</li>
          <li>Evolve: generate harder variations (Adversary)</li>
        </ol>
        <p className="hint">
          Deploying a course to a real drone needs live control. PX4 can fly generated courses; Hopper cannot yet,
          because FTW does not publish a control transport.
        </p>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

/** 2D projection of the course: top (x/y) or side (along-track distance/z). */
export function Projection({ course, axis }: { course: CourseDetail; axis: "top" | "side" }) {
  const W = 260;
  const H = 160;
  const pts = pathPoints(course);
  const gates = courseGates(course);
  const vol = courseVolume(course);
  const [w, l] = vol.dimensions;

  let project: (p: Vec3) => [number, number];
  if (axis === "top") {
    const sx = W / w;
    const sy = H / l;
    const s = Math.min(sx, sy);
    project = (p) => [W / 2 + p[0] * s, H / 2 - p[1] * s];
  } else {
    // Side view along the course: x = distance travelled, y = altitude.
    const cum: number[] = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    }
    const total = cum[cum.length - 1] || 1;
    const hz = vol.ceiling - vol.floor || 1;
    const byPoint = new Map(pts.map((p, i) => [p, cum[i]]));
    project = (p) => {
      // Gates are not path points: use the nearest path sample's distance.
      let d = byPoint.get(p);
      if (d === undefined) {
        let best = 0;
        let bestD = Infinity;
        pts.forEach((q, i) => {
          const dd = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]);
          if (dd < bestD) { bestD = dd; best = cum[i]; }
        });
        d = best;
      }
      return [8 + (d / total) * (W - 16), H - 8 - ((p[2] - vol.floor) / hz) * (H - 16)];
    };
  }

  const line = pts.map((p) => project(p).map((v) => v.toFixed(1)).join(",")).join(" ");
  return (
    <svg className="projection" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${axis} view`}>
      <rect x={0.5} y={0.5} width={W - 1} height={H - 1} className="proj-frame" />
      <polyline points={line} className="proj-path" />
      {gates.map((g, i) => {
        const [x, y] = project(g.center);
        return <circle key={i} cx={x} cy={y} r={3.2} className={i === 0 ? "proj-gate first" : "proj-gate"} />;
      })}
    </svg>
  );
}

function DifficultyBars({ difficulty }: { difficulty: Record<string, number> }) {
  const entries = Object.entries(difficulty).filter(([k]) => k !== "predicted_speed");
  const max = Math.max(1, ...entries.map(([, v]) => Math.abs(v)));
  return (
    <div className="difficulty-bars">
      {entries.map(([k, v]) => (
        <div key={k} className="diff-row">
          <span className="diff-label">{DIFFICULTY_LABELS[k] ?? k}</span>
          <span className="diff-track">
            <span className="diff-fill" style={{ width: `${(Math.abs(v) / max) * 100}%` }} />
          </span>
          <span className="diff-value">{v.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

function GatePreview({ gate }: { gate: CourseGate | null }) {
  if (!gate) return <p className="empty">No gates.</p>;
  const [w, h] = gate.size;
  const r = 52;
  return (
    <div className="gate-preview">
      <svg viewBox="0 0 160 140" role="img" aria-label="first gate">
        <ellipse cx={70} cy={66} rx={r * Math.min(1, w / h)} ry={r * Math.min(1, h / w)} className="gate-ring" />
        <line x1={70} y1={66 + r} x2={70} y2={132} className="gate-post" />
        <text x={140} y={70} className="gate-dim">{h.toFixed(1)} m</text>
      </svg>
      <dl>
        <dt>Width</dt>
        <dd>{w.toFixed(2)} m</dd>
        <dt>Height</dt>
        <dd>{h.toFixed(2)} m</dd>
        <dt>Centre</dt>
        <dd>{gate.center.map((v) => v.toFixed(1)).join(", ")}</dd>
        <dt>Aligned to path</dt>
        <dd>✓</dd>
      </dl>
    </div>
  );
}

/** Small stylised icon per course type (not a real course). */
function CourseGlyph({ mode }: { mode: CourseMode }) {
  const paths: Record<CourseMode, string> = {
    RANDOM: "M6 30 C20 6, 34 40, 48 16 S70 10, 74 30",
    SLALOM: "M6 20 C14 6, 20 34, 28 20 S42 6, 50 20 S64 34, 74 20",
    VERTICAL: "M6 34 L20 8 L34 34 L48 8 L62 34 L74 14",
    TECHNICAL: "M10 30 C10 6, 40 6, 40 22 S70 38, 70 12 S40 4, 30 30",
    SPEED_RUN: "M4 32 L60 8 C70 4, 76 12, 70 18",
    CHALLENGE: "M6 22 C16 4, 26 40, 36 20 L52 34 C64 40, 70 8, 76 20",
    ADVERSARY: "M8 28 C20 2, 30 40, 42 14 S58 36, 74 8",
  };
  return (
    <svg className="course-glyph" viewBox="0 0 80 40" aria-hidden="true">
      <path d={paths[mode]} />
    </svg>
  );
}
