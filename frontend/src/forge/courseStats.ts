import type { CourseDetail, Vec3 } from "../types";

export interface CourseGate {
  order: number;
  center: Vec3;
  normal: Vec3;
  size: [number, number];
}

export interface CourseVolume {
  dimensions: Vec3;
  floor: number;
  ceiling: number;
  obstacles: Array<{ name: string; center: Vec3; size: Vec3 }>;
}

export interface CourseStats {
  gateCount: number;
  lengthM: number;
  minAltitudeM: number;
  maxAltitudeM: number;
  /** Smallest turn radius along the path (metres); Infinity for a straight path. */
  tightestTurnM: number;
  predictedSpeedMs: number;
  /** Course length over the predicted speed, or null when no speed is known. */
  estimatedLapS: number | null;
}

function vec(a: unknown): Vec3 {
  const v = Array.isArray(a) ? a : [];
  return [Number(v[0] ?? 0), Number(v[1] ?? 0), Number(v[2] ?? 0)];
}

export function courseGates(course: CourseDetail): CourseGate[] {
  return course.gates
    .map((g) => {
      const size = Array.isArray(g.size) ? g.size : [1.5, 1.5];
      return {
        order: Number(g.order ?? 0),
        center: vec(g.center),
        normal: vec(g.normal),
        size: [Number(size[0] ?? 1.5), Number(size[1] ?? 1.5)] as [number, number],
      };
    })
    .sort((a, b) => a.order - b.order);
}

export function courseVolume(course: CourseDetail): CourseVolume {
  const v = course.safe_volume as Record<string, unknown>;
  const dims = vec(v.dimensions);
  const floor = Number(v.floor ?? 0);
  const ceiling = v.ceiling == null ? floor + dims[2] : Number(v.ceiling);
  const obstacles = Array.isArray(v.obstacles)
    ? (v.obstacles as Array<Record<string, unknown>>).map((o, i) => ({
        name: String(o.name ?? `obstacle ${i + 1}`),
        center: vec(o.center),
        size: vec(o.size),
      }))
    : [];
  return { dimensions: dims, floor, ceiling, obstacles };
}

export function pathPoints(course: CourseDetail): Vec3[] {
  return course.path.map(vec);
}

function dist(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

/** Radius of the circle through three points (Infinity if they are collinear). */
export function circumradius(a: Vec3, b: Vec3, c: Vec3): number {
  const ab = dist(a, b);
  const bc = dist(b, c);
  const ca = dist(c, a);
  // Twice the triangle area via the cross product.
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const w = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const cross = Math.hypot(u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]);
  if (cross < 1e-9) return Infinity;
  return (ab * bc * ca) / (2 * cross);
}

export function courseStats(course: CourseDetail): CourseStats {
  const pts = pathPoints(course);
  let length = 0;
  let minZ = Infinity;
  let maxZ = -Infinity;
  let tightest = Infinity;
  for (let i = 0; i < pts.length; i++) {
    minZ = Math.min(minZ, pts[i][2]);
    maxZ = Math.max(maxZ, pts[i][2]);
    if (i > 0) length += dist(pts[i - 1], pts[i]);
    if (i > 0 && i < pts.length - 1) tightest = Math.min(tightest, circumradius(pts[i - 1], pts[i], pts[i + 1]));
  }
  const speed = Number(course.difficulty.predicted_speed ?? 0);
  return {
    gateCount: course.gates.length,
    lengthM: length,
    minAltitudeM: pts.length ? minZ : 0,
    maxAltitudeM: pts.length ? maxZ : 0,
    tightestTurnM: tightest,
    predictedSpeedMs: speed,
    estimatedLapS: speed > 0 ? length / speed : null,
  };
}

/** Difficulty factors the generator reports, labelled for people. */
export const DIFFICULTY_LABELS: Record<string, string> = {
  curvature: "Curvature",
  braking_demand: "Braking demand",
  spacing: "Gate spacing",
  density: "Gate density",
  vertical: "Vertical change",
  orientation: "Gate orientation",
  clearance: "Clearance",
  gate_size: "Gate size",
  visibility: "Visibility",
  predicted_speed: "Predicted speed",
};
