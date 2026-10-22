import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { CourseDetail, TwinWorldSnapshot } from "../types";
import { circumradius, courseStats } from "../forge/courseStats";

// WebGL is not available in jsdom: replace the 3D view with a probe.
vi.mock("../components/WorldView", () => ({
  default: ({ scene }: { scene: { gates?: unknown[]; paths?: unknown[] } }) => (
    <div data-testid="world-view" data-gates={scene.gates?.length ?? 0} data-paths={scene.paths?.length ?? 0} />
  ),
}));

const { default: ForgePage } = await import("../pages/ForgePage");
const { default: EvolutionPage } = await import("../pages/EvolutionPage");
const { MissionRegistryPanel } = await import("../pages/MissionPage");
const { snapshotToScene } = await import("../pages/TwinPage");

type Handler = (url: string, body: unknown) => unknown;
const calls: Array<{ url: string; body: unknown }> = [];

function mockFetch(handler: Handler) {
  calls.length = 0;
  globalThis.fetch = vi.fn((url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, body });
    const result = handler(url, body);
    const failed = result instanceof Error;
    return Promise.resolve({
      ok: !failed,
      status: failed ? 422 : 200,
      json: () => Promise.resolve(failed ? { detail: (result as Error).message } : result),
    });
  }) as unknown as typeof globalThis.fetch;
}

const COURSE: CourseDetail = {
  id: "slalom-3-abc",
  seed: 3,
  mode: "SLALOM",
  safe_volume: { dimensions: [30, 50, 12], floor: 0, ceiling: 12, boundary_margin: 1.5, obstacles: [] },
  // An L-shaped path: 10 m east, then 10 m north, at a constant 5 m.
  path: [[0, 0, 5], [10, 0, 5], [10, 10, 5]],
  gates: [
    { center: [5, 0, 5], normal: [1, 0, 0], size: [2, 2], order: 0 },
    { center: [10, 5, 5], normal: [0, 1, 0], size: [2, 2], order: 1 },
  ],
  difficulty: { curvature: 1.2, predicted_speed: 4 },
};

describe("course stats", () => {
  it("computes length, altitude band, turn radius and lap time", () => {
    const s = courseStats(COURSE);
    expect(s.gateCount).toBe(2);
    expect(s.lengthM).toBeCloseTo(20);
    expect(s.minAltitudeM).toBe(5);
    expect(s.maxAltitudeM).toBe(5);
    expect(s.tightestTurnM).toBeCloseTo(Math.SQRT2 * 5, 5);
    expect(s.estimatedLapS).toBeCloseTo(5);
  });

  it("treats collinear points as a straight line", () => {
    expect(circumradius([0, 0, 0], [1, 0, 0], [2, 0, 0])).toBe(Infinity);
  });
});

describe("ForgePage", () => {
  beforeEach(() =>
    mockFetch((url) => {
      if (url === "/api/courses/generate") return COURSE;
      if (url.endsWith("/validation")) return { course_id: COURSE.id, valid: true, errors: [] };
      return {};
    }),
  );

  it("generates a course and shows its stats, validation and 3D view", async () => {
    render(<MemoryRouter><ForgePage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /slalom/i }));
    fireEvent.click(screen.getByRole("button", { name: "GENERATE COURSE" }));

    expect(await screen.findByText("20.0 m")).toBeInTheDocument(); // course length
    expect(await screen.findByText("VALID")).toBeInTheDocument();
    expect(screen.getByTestId("world-view").dataset.gates).toBe("2");
    const req = calls.find((c) => c.url === "/api/courses/generate")!.body as Record<string, unknown>;
    expect(req.mode).toBe("SLALOM");
    expect(req.ceiling).toBe(12);
  });

  it("shows generator errors", async () => {
    mockFetch(() => new Error("volume too small for 10 gates"));
    render(<MemoryRouter><ForgePage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "GENERATE COURSE" }));
    expect(await screen.findByText("volume too small for 10 gates")).toBeInTheDocument();
  });
});

describe("EvolutionPage", () => {
  it("lists weaknesses and evaluates champion vs challenger", async () => {
    mockFetch((url) => {
      if (url === "/api/training/campaigns")
        return [
          { campaign_id: "c1", name: "Base", state: "COMPLETE", total_runs: 4, completed_runs: 4 },
          { campaign_id: "c2", name: "Tuned", state: "COMPLETE", total_runs: 4, completed_runs: 4 },
        ];
      if (url.startsWith("/api/evolution/weaknesses"))
        return {
          run_count: 8,
          weaknesses: [
            {
              mission: "RACE", condition: "SLALOM · HARD · clean", failure_rate: 0.5, severity: 0.6,
              sample_count: 8, priority: 0.2, top_causes: [{ category: "GATE_MISS", count: 4 }],
            },
          ],
        };
      if (url === "/api/evolution/compare")
        return {
          promote: true, reason: "promotion candidate", metric: "utility",
          champion: { name: "c1", runs: 4, success_rate: 0.5, mean_score: 1.0, worst_score: 0.5 },
          challenger: { name: "c2", runs: 4, success_rate: 0.75, mean_score: 1.2, worst_score: 0.6 },
        };
      return {};
    });
    render(<MemoryRouter><EvolutionPage /></MemoryRouter>);
    expect(await screen.findByText("SLALOM · HARD · clean")).toBeInTheDocument();
    expect(screen.getByText("gate miss ×4")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("champion"), { target: { value: "c1" } });
    fireEvent.change(screen.getByLabelText("challenger"), { target: { value: "c2" } });
    fireEvent.click(screen.getByRole("button", { name: "EVALUATE" }));
    expect(await screen.findByText("Promote the challenger")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/evolution/compare")!.body).toMatchObject({
      champion_campaign_id: "c1",
      challenger_campaign_id: "c2",
    });
  });
});

describe("MissionRegistryPanel", () => {
  it("creates a search & rescue mission over the given area", async () => {
    const saved: unknown[] = [];
    mockFetch((url, body) => {
      if (url === "/api/missions" && body) {
        const m = {
          mission_id: "mission_0001", mission_type: "SEARCH_RESCUE", status: "PENDING", phase: "PREFLIGHT",
          elapsed_s: 0, waypoints_reached: 0,
          goal: { description: "Lake", waypoints: [], search_area: [], delivery_target: null, return_home: true, max_duration_s: 600 },
        };
        saved.push(m);
        return m;
      }
      if (url === "/api/missions") return { missions: saved };
      return {};
    });
    render(<MissionRegistryPanel />);
    expect(await screen.findByText("No saved missions yet.")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Describe the mission"), { target: { value: "Lake" } });
    fireEvent.click(screen.getByRole("button", { name: "CREATE" }));
    expect(await screen.findByText("mission_0001")).toBeInTheDocument();
    const req = calls.find((c) => c.url === "/api/missions" && c.body)!.body as Record<string, unknown>;
    expect(req.mission_type).toBe("SEARCH_RESCUE");
    expect(req.search_area).toEqual([[-20, -20, 10], [20, 20, 10]]);
  });
});

describe("snapshotToScene", () => {
  const SNAP: TwinWorldSnapshot = {
    timestamp: 1, sequence: 1, autonomy_state: "IDLE", race_time_s: 0, gates_passed: 0, total_gates: 0,
    aircraft: { ACTUAL: { position: [1, 2, 3], velocity: [0, 0, 0], yaw_deg: 90, pitch_deg: 0, roll_deg: 0, layer: "ACTUAL" } },
    errors: [],
    primitives: [
      { kind: "PATH", points: [[0, 0, 0], [1, 2, 3]], layer: "ACTUAL", label: "trail", closed: false },
      { kind: "PATH", points: [[0, 0, 0], [5, 5, 5]], layer: "DESIRED", label: "Box", closed: false },
      { kind: "REGION", vertices: [[0, 0, 0], [1, 0, 0], [1, 1, 0]], layer: "TRUTH", label: "Tower", opacity: 0.15, color: "#ff3b5c" },
    ],
  };

  it("maps primitives and hides switched-off layers", () => {
    const all = snapshotToScene(SNAP, new Set(["ACTUAL", "DESIRED", "TRUTH", "ESTIMATED"]));
    expect(all.aircraft?.position).toEqual([1, 2, 3]);
    expect(all.paths).toHaveLength(2);
    expect(all.regions?.[0].vertices).toEqual([[0, 0], [1, 0], [1, 1]]);

    const noActual = snapshotToScene(SNAP, new Set(["DESIRED"]));
    expect(noActual.aircraft).toBeNull();
    expect(noActual.paths).toHaveLength(1);
    expect(noActual.regions).toHaveLength(0);
  });
});
