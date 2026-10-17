import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { RaceCourse, Telemetry, VantaRaceStatus } from "../types";
import { DISCONNECTED } from "../types";

// The page opens a telemetry WebSocket; give it a connected aircraft.
const connected: Telemetry = { ...DISCONNECTED, connected: true, armed: false, x: 6, y: 0, altitude: 5 };
vi.mock("../hooks/useFlightStream", () => ({
  useFlightStream: () => ({
    telemetry: connected,
    twin: null,
    snapshot: null,
    autonomy: null,
    mission: null,
    events: [
      { timestamp: 1, event_type: "race_gate_passed", message: "gate_1 passed" },
      { timestamp: 2, event_type: "armed", message: "not a race event" },
    ],
    failsafe: null,
    streamOnline: true,
    pushEvent: vi.fn(),
    setMission: vi.fn(),
    clearTwin: vi.fn(),
    dismissFailsafe: vi.fn(),
  }),
}));

const { default: RacePage, CourseMap, LoopPanel } = await import("../pages/RacePage");

const COURSE: RaceCourse = {
  course_id: "sitl_line_3",
  name: "Line (3 gates)",
  description: "Three gates.",
  start_altitude_m: 5,
  max_time_s: 120,
  length_m: 36,
  tags: [],
  gates: [0, 1, 2].map((i) => ({
    gate_id: `gate_${i + 1}`,
    position: [12 * (i + 1), 0, 5],
    normal: [1, 0, 0],
    width: 3,
    height: 3,
    yaw_deg: 0,
    pitch_deg: 0,
    order: i,
  })),
};

function status(overrides: Partial<VantaRaceStatus> = {}): VantaRaceStatus {
  return {
    state: "IDLE",
    message: "",
    course: null,
    config: { perception: "vision", speed_m_s: 3, aggression: 0.35, finish: "land" },
    gates_passed: 0,
    total_gates: 0,
    next_gate: null,
    passes: [],
    race_time_s: 0,
    elapsed_s: 0,
    perception: null,
    perception_status: null,
    perception_faults: null,
    autonomy: null,
    stats: {
      ticks: 0, setpoints_sent: 0, setpoints_rejected: 0, setpoints_expired: 0, hold_setpoints: 0,
      recoveries: 0, reacquisitions: 0, stale_telemetry_events: 0, gates_missed: 0,
      avg_tick_ms: 0, max_tick_ms: 0, max_tracking_error_m: 0, gate_pass_offsets_m: [],
    },
    offboard: { sent: 0, rejected: 0, expired: 0 },
    ...overrides,
  };
}

const calls: Array<{ url: string; body: unknown }> = [];
let current = status();

beforeEach(() => {
  calls.length = 0;
  current = status();
  globalThis.fetch = vi.fn((url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, body });
    let result: unknown = {};
    if (url === "/api/vantarace/courses") result = { courses: [COURSE] };
    else if (url === "/api/vantarace/check") result = { valid: true, errors: [], warnings: [], length_m: 36 };
    else if (url === "/api/vantarace") result = current;
    else if (url === "/api/vantarace/start") {
      current = status({ state: "RUNNING", course: COURSE, total_gates: 3, next_gate: "gate_1" });
      result = { accepted: true, message: "race started", state: "PREPARING" };
    } else if (url === "/api/vantarace/abort") {
      current = status({ state: "ABORTED", course: COURSE, total_gates: 3, message: "aborted by operator" });
      result = { accepted: true, message: "race aborted: aborted by operator" };
    } else if (url === "/api/vantarace/fault") result = { accepted: true, message: "vision blackout for 3s" };
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(result) });
  }) as unknown as typeof globalThis.fetch;
});

describe("RacePage", () => {
  it("starts a race with the chosen options and shows only race events", async () => {
    render(<MemoryRouter><RacePage /></MemoryRouter>);
    await screen.findByText(/Line \(3 gates\)/);
    fireEvent.change(screen.getByLabelText("speed limit"), { target: { value: "4.5" } });
    fireEvent.click(screen.getByText("START RACE"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/vantarace/start")).toBe(true));
    const start = calls.find((c) => c.url === "/api/vantarace/start")!.body as Record<string, unknown>;
    expect(start).toMatchObject({ course_id: "sitl_line_3", perception: "vision", speed_m_s: 4.5, finish: "land" });
    await waitFor(() => expect(screen.getByTestId("race-state").textContent).toBe("RUNNING"));
    expect(screen.getByText("gate_1 passed")).toBeTruthy();
    expect(screen.queryByText("not a race event")).toBeNull();
  });

  it("offers pause, abort and fault rehearsal while racing", async () => {
    current = status({ state: "RUNNING", course: COURSE, total_gates: 3, next_gate: "gate_1" });
    render(<MemoryRouter><RacePage /></MemoryRouter>);
    await screen.findByText("ABORT");
    expect(screen.getByText("PAUSE")).toBeTruthy();
    fireEvent.click(screen.getByText("Vision blackout 3 s"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/vantarace/fault")).toBe(true));
    expect(calls.find((c) => c.url === "/api/vantarace/fault")!.body).toMatchObject({ kind: "vision_blackout", duration_s: 3 });
    fireEvent.click(screen.getByText("ABORT"));
    await waitFor(() => expect(screen.getByTestId("race-state").textContent).toBe("ABORTED"));
  });
});

describe("CourseMap", () => {
  it("draws every gate, marks passed and next, and the aircraft", () => {
    render(
      <CourseMap
        course={COURSE}
        status={status({ state: "RUNNING", passes: [{ gate_id: "gate_1", time_s: 5, offset_m: 0.1 }], next_gate: "gate_2" })}
        telemetry={connected}
      />,
    );
    expect(screen.getByTestId("gate-gate_1").getAttribute("class")).toContain("passed");
    expect(screen.getByTestId("gate-gate_2").getAttribute("class")).toContain("next");
    expect(screen.getByTestId("gate-gate_3").getAttribute("class")).toBe("gate");
    expect(screen.getByTestId("aircraft")).toBeTruthy();
  });
});

describe("LoopPanel", () => {
  it("shows each stage and flags a hold and refused setpoints", () => {
    const s = status({
      state: "RECOVERING",
      total_gates: 3,
      perception_status: "LOST",
      autonomy: { state: "RECOVERING", planner_state: "RECOVER", execution: { mode: "HOLD", metrics: { last_setpoint_age_ms: 40 } } },
      stats: { ...status().stats, setpoints_sent: 100, setpoints_expired: 3, ticks: 10, avg_tick_ms: 7 },
    });
    render(<LoopPanel status={s} />);
    expect(screen.getByText("LOST")).toBeTruthy();
    expect(screen.getByText("HOLD")).toBeTruthy();
    expect(screen.getByText("100 / 3")).toBeTruthy();
  });
});
