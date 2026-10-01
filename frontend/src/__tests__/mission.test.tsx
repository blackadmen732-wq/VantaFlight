import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import MissionPanel, { MissionProgress, formatDuration } from "../components/MissionPanel";
import { api } from "../api";
import type { MissionStatus, PlanReport } from "../types";

const VALID: PlanReport = {
  valid: true,
  errors: [],
  warnings: [],
  distance_m: 28.1,
  estimated_duration_s: 75,
  estimated_battery_pct: 11.2,
};

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  globalThis.fetch = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(handler(url, init)) }),
  ) as unknown as typeof globalThis.fetch;
}

const RUNNING: MissionStatus = {
  state: "RUNNING",
  phase: "TRANSIT",
  name: "Square 20 m",
  current_index: 1,
  waypoints_reached: 1,
  total_waypoints: 4,
  progress: 0.25,
  distance_to_target_m: 12.34,
  elapsed_s: 65,
  message: "",
  plan: null,
};

describe("MissionPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockFetch(() => VALID);
  });

  it("shows the plan check and requires arming before start", async () => {
    render(<MissionPanel connected armed={false} status={null} onResult={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/28 m/)).toBeInTheDocument());
    expect(screen.getByText(/11% battery/)).toBeInTheDocument();
    expect(screen.getByText("START MISSION")).toBeDisabled();
    expect(screen.getByText("Arm the aircraft to start the mission.")).toBeInTheDocument();
  });

  it("starts the mission when armed and the plan is valid", async () => {
    const onResult = vi.fn();
    mockFetch((url) =>
      url === "/api/mission/start"
        ? { command: "mission_start", accepted: true, message: "ok", timestamp: 0 }
        : VALID,
    );
    render(<MissionPanel connected armed status={null} onResult={onResult} />);
    const start = screen.getByText("START MISSION");
    await waitFor(() => expect(start).not.toBeDisabled());
    fireEvent.click(start);
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ accepted: true })));
    const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.find(([u]) => u === "/api/mission/start");
    expect(JSON.parse(call![1].body).waypoints).toHaveLength(2);
  });

  it("lists validation errors", async () => {
    mockFetch(() => ({ ...VALID, valid: false, errors: ["waypoint 1: too far"] }));
    render(<MissionPanel connected armed status={null} onResult={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("waypoint 1: too far")).toBeInTheDocument());
    expect(screen.getByText("START MISSION")).toBeDisabled();
  });

  it("adds and removes waypoints and reports the draft", async () => {
    const onPlanChange = vi.fn();
    render(
      <MissionPanel connected armed status={null} onResult={vi.fn()} onPlanChange={onPlanChange} />,
    );
    fireEvent.click(screen.getByText("+ Add waypoint"));
    expect(screen.getByLabelText("remove waypoint 3")).toBeInTheDocument();
    expect(onPlanChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ waypoints: expect.arrayContaining([expect.objectContaining({ x: 20, y: 10 })]) }),
    );
    fireEvent.click(screen.getByLabelText("remove waypoint 1"));
    expect(screen.queryByLabelText("remove waypoint 3")).not.toBeInTheDocument();
  });

  it("loads a pattern from the server", async () => {
    mockFetch((url) =>
      url === "/api/mission/pattern"
        ? {
            ok: true,
            error: null,
            plan: { name: "Orbit r=15 m", waypoints: [{ x: 15, y: 0, altitude: 10 }], speed_m_s: 5, finish: "return_home" },
          }
        : VALID,
    );
    render(<MissionPanel connected armed status={null} onResult={vi.fn()} />);
    fireEvent.click(screen.getByText("Orbit"));
    await waitFor(() => expect(screen.getByDisplayValue("Orbit r=15 m")).toBeInTheDocument());
  });

  it("offers pause and abort while running, and hides the editor", () => {
    render(<MissionPanel connected armed status={RUNNING} onResult={vi.fn()} />);
    expect(screen.getByText("PAUSE")).toBeInTheDocument();
    expect(screen.getByText("ABORT")).toBeInTheDocument();
    expect(screen.queryByText("START MISSION")).not.toBeInTheDocument();
    expect(screen.queryByText("+ Add waypoint")).not.toBeInTheDocument();
  });

  it("offers resume while paused", () => {
    render(<MissionPanel connected armed status={{ ...RUNNING, state: "PAUSED" }} onResult={vi.fn()} />);
    expect(screen.getByText("RESUME")).toBeInTheDocument();
  });
});

describe("MissionProgress", () => {
  it("describes the current leg", () => {
    render(<MissionProgress status={RUNNING} />);
    expect(screen.getByText("Flying to waypoint 2 of 4 (12.3 m)")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText(/1m 5s/)).toBeInTheDocument();
  });
});

describe("formatDuration", () => {
  it("formats seconds and minutes", () => {
    expect(formatDuration(42.4)).toBe("42s");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatDuration(-3)).toBe("0s");
  });
});

describe("api.post error handling", () => {
  it("turns a network failure into a rejected result", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline")) as unknown as typeof fetch;
    const res = await api.arm();
    expect(res).toMatchObject({ command: "arm", accepted: false, message: "offline" });
  });

  it("explains validation errors from the server", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ detail: [{ loc: ["body", "waypoints", 0, "altitude"], msg: "Field required" }] }),
    }) as unknown as typeof fetch;
    const res = await api.mission.start({ name: "x", waypoints: [], speed_m_s: 5, finish: "land" });
    expect(res.accepted).toBe(false);
    expect(res.message).toBe("waypoints.0.altitude: Field required");
  });
});
