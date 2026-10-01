import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { TwinLayer, TwinWorldSnapshot, Vec3 } from "../types";
import { useFlightStream } from "../hooks/useFlightStream";
import Metric from "../components/Metric";
import WorldView, { type WorldScene } from "../components/WorldView";

const LAYERS: Array<{ layer: TwinLayer; label: string; hint: string }> = [
  { layer: "ACTUAL", label: "Actual", hint: "Where the aircraft is and has been" },
  { layer: "DESIRED", label: "Desired", hint: "The route the mission wants to fly" },
  { layer: "TRUTH", label: "World", hint: "No-fly zones, geofence, course gates" },
  { layer: "ESTIMATED", label: "Estimated", hint: "Vision and state estimates" },
];

const LAYER_COLOR: Record<TwinLayer, number> = {
  ACTUAL: 0x4f8cff,
  DESIRED: 0xf5a623,
  TRUTH: 0xff3b5c,
  ESTIMATED: 0x2ecc71,
};

/** Height of drawn no-fly columns (metres). */
const ZONE_HEIGHT_M = 30;

function v3(a: number[] | undefined): Vec3 {
  return [a?.[0] ?? 0, a?.[1] ?? 0, a?.[2] ?? 0];
}

function parseColor(color: string | undefined, fallback: number): number {
  if (color && /^#[0-9a-f]{6}$/i.test(color)) return parseInt(color.slice(1), 16);
  return fallback;
}

/** Convert the backend's TwinWorldSnapshot into what WorldView draws. */
export function snapshotToScene(snap: TwinWorldSnapshot | null, visible: Set<TwinLayer>): WorldScene {
  const scene: Required<Omit<WorldScene, "bounds" | "frame" | "aircraft">> &
    Pick<WorldScene, "bounds" | "frame" | "aircraft"> = {
    bounds: null,
    frame: null,
    paths: [],
    gates: [],
    regions: [],
    boxes: [],
    points: [],
    aircraft: null,
  };
  if (!snap) return scene;
  const actual = snap.aircraft.ACTUAL;
  if (actual && visible.has("ACTUAL")) scene.aircraft = { position: v3(actual.position), yawDeg: actual.yaw_deg };

  for (const p of snap.primitives) {
    if (!visible.has(p.layer)) continue;
    const color = parseColor(p.color, LAYER_COLOR[p.layer]);
    switch (p.kind) {
      case "PATH":
        scene.paths.push({ points: p.points.map(v3), color, opacity: p.label === "trail" ? 0.6 : 0.95 });
        break;
      case "POINT":
        scene.points.push({ position: [p.x, p.y, p.z], color, radius: p.radius });
        break;
      case "GATE":
        scene.gates.push({
          position: v3(p.position), normal: v3(p.normal), width: p.width, height: p.height,
          color: p.passed ? 0x2ecc71 : color,
        });
        break;
      case "REGION":
        scene.regions.push({
          vertices: p.vertices.map((v) => [v[0], v[1]] as [number, number]),
          height: ZONE_HEIGHT_M,
          color,
          opacity: Math.max(0.15, p.opacity),
        });
        break;
      case "ENVELOPE": {
        const c = v3(p.center);
        const r = v3(p.radii);
        // The geofence is huge; frame the view on it only if nothing else is around.
        scene.bounds = { min: [c[0] - r[0], c[1] - r[1], c[2] - r[2]], max: [c[0] + r[0], c[1] + r[1], c[2] + r[2]] };
        break;
      }
      default:
        break;
    }
  }
  return scene;
}

export default function TwinPage() {
  const stream = useFlightStream();
  const [polled, setPolled] = useState<TwinWorldSnapshot | null>(null);
  const [visible, setVisible] = useState<Set<TwinLayer>>(new Set(["ACTUAL", "DESIRED", "TRUTH", "ESTIMATED"]));
  const [view, setView] = useState<"perspective" | "top" | "side">("perspective");
  const [showFence, setShowFence] = useState(false);

  // The snapshot arrives on the stream; poll once so the page is not empty
  // before the first frame (or when the stream is down).
  useEffect(() => {
    api.twinSnapshot().then(setPolled).catch(() => {});
  }, []);

  const snapshot = stream.snapshot ?? polled;
  const scene = useMemo(() => {
    const s = snapshotToScene(snapshot, visible);
    if (!showFence) {
      // Frame on the flying area, not the full geofence.
      const pts: Vec3[] = [
        ...(s.paths ?? []).flatMap((p) => p.points),
        ...(s.regions ?? []).flatMap((r) => r.vertices.map(([x, y]) => [x, y, 0] as Vec3)),
        ...(s.aircraft ? [s.aircraft.position] : []),
      ];
      s.bounds = null;
      s.frame = pts.length ? boundsOf(pts) : null;
    }
    return s;
  }, [snapshot, visible, showFence]);

  const toggle = (layer: TwinLayer) => {
    const next = new Set(visible);
    if (next.has(layer)) next.delete(layer);
    else next.add(layer);
    setVisible(next);
  };

  const t = stream.telemetry;
  const actual = snapshot?.aircraft.ACTUAL;
  const linkAge = snapshot?.errors.find((e) => e.name === "link_age");
  // Re-frame when the framed area changes noticeably (rounded to 5 m), not every tick.
  const framed = scene.frame ?? scene.bounds;
  const frameKey = `${view}-${showFence}-${framed ? [...framed.min, ...framed.max].map((v) => Math.round(v / 5)).join(",") : "none"}`;

  return (
    <div className="page-content twin-page">
      <div className="page-header">
        <h2>Digital Twin</h2>
        <span className={`status-pill ${t.connected ? "ok" : "off"}`}>{t.connected ? "LIVE" : "NO AIRCRAFT"}</span>
        <span className="muted small">frame #{snapshot?.sequence ?? 0}</span>
      </div>

      <div className="twin-toolbar">
        <div className="chip-row" aria-label="layers">
          {LAYERS.map((l) => (
            <button
              key={l.layer}
              className={`chip ${visible.has(l.layer) ? "active" : ""}`}
              aria-pressed={visible.has(l.layer)}
              title={l.hint}
              onClick={() => toggle(l.layer)}
            >
              <span className="swatch" style={{ background: `#${LAYER_COLOR[l.layer].toString(16).padStart(6, "0")}` }} />
              {l.label}
            </button>
          ))}
        </div>
        <div className="chip-row" aria-label="camera">
          {(["perspective", "top", "side"] as const).map((v) => (
            <button key={v} className={`chip ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
              {v === "perspective" ? "3D" : v === "top" ? "Top" : "Side"}
            </button>
          ))}
          <button className={`chip ${showFence ? "active" : ""}`} onClick={() => setShowFence(!showFence)}>
            Geofence
          </button>
        </div>
      </div>

      <WorldView scene={scene} frameKey={frameKey} view={view} height={480} />
      <p className="hint">Drag to orbit, scroll to zoom, right-drag to pan.</p>

      <div className="metrics twin-metrics">
        <Metric
          label="Position"
          value={actual ? actual.position.map((v) => v.toFixed(1)).join(", ") : "UNKNOWN"}
        />
        <Metric label="Heading" value={actual ? `${actual.yaw_deg.toFixed(0)}°` : "—"} />
        <Metric label="Mode" value={t.connected ? t.flight_mode : "—"} />
        <Metric label="Link age" value={linkAge ? `${linkAge.value.toFixed(2)} s` : "—"} warn={(linkAge?.value ?? 0) > 1} />
        <Metric label="Autonomy" value={snapshot?.autonomy_state ?? "IDLE"} />
        <Metric
          label="Gates"
          value={snapshot && snapshot.total_gates > 0 ? `${snapshot.gates_passed}/${snapshot.total_gates}` : "—"}
        />
      </div>
    </div>
  );
}

function boundsOf(points: Vec3[]): { min: Vec3; max: Vec3 } {
  const min: Vec3 = [Infinity, Infinity, 0];
  const max: Vec3 = [-Infinity, -Infinity, 5];
  for (const p of points) {
    for (let i = 0; i < 3; i++) {
      min[i] = Math.min(min[i], p[i]);
      max[i] = Math.max(max[i], p[i]);
    }
  }
  return { min, max };
}
