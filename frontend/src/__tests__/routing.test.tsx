import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import MissionPanel, { RouteSummary } from "../components/MissionPanel";
import AirspacePanel from "../components/AirspacePanel";
import type { Airspace, PlanReport, RoutePlan } from "../types";

const VALID: PlanReport = {
  valid: true,
  errors: [],
  warnings: [],
  distance_m: 50,
  estimated_duration_s: 30,
  estimated_battery_pct: 5,
};

const ROUTE: RoutePlan = {
  plan: {
    name: "Quick hop",
    speed_m_s: 5,
    finish: "return_home",
    waypoints: [
      { x: 10, y: 10, altitude: 8, hold_s: 0, kind: "stop" },
      { x: 12, y: 3, altitude: 8, hold_s: 0, kind: "via" },
      { x: 10, y: 0, altitude: 8, hold_s: 0, kind: "stop" },
    ],
  },
  order: [1, 0],
  method: "exact",
  given_order_m: 100,
  distance_m: 66,
  saved_m: 34,
  saved_pct: 34,
  detour_points: 1,
  duration_s: 70,
  battery_used_pct: 10.5,
  battery_start_pct: 90,
  battery_end_pct: 79.5,
  feasible: true,
  point_of_no_return: null,
  budgets: [],
  warnings: [],
};

const AIRSPACE: Airspace = {
  margin_m: 5,
  zones: [{ id: "zone-1", name: "Stadium", vertices: [[20, -10], [40, -10], [40, 10], [20, 10]] }],
};

type Handler = (url: string, body: unknown) => unknown;

function mockFetch(handler: Handler) {
  globalThis.fetch = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve(handler(url, init?.body ? JSON.parse(init.body as string) : undefined)),
    }),
  ) as unknown as typeof globalThis.fetch;
}

function bodiesFor(url: string): unknown[] {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
    .filter(([u]) => u === url)
    .map(([, init]) => JSON.parse((init as RequestInit).body as string));
}

describe("RouteSummary", () => {
  it("shows savings, battery and detours", () => {
    render(<RouteSummary route={ROUTE} />);
    expect(screen.getByText("Best possible order")).toBeInTheDocument();
    expect(screen.getByText(/34% shorter than as entered \(100 m\)/)).toBeInTheDocument();
    expect(screen.getByText(/battery 90% → 80%/)).toBeInTheDocument();
    expect(screen.getByText("1 detour point around no-fly zones")).toBeInTheDocument();
  });

  it("highlights the point of no return", () => {
    const warning = "stop 2 is past the point of no return: from there the aircraft could not get home with the 25% reserve";
    const { container } = render(
      <RouteSummary route={{ ...ROUTE, feasible: false, point_of_no_return: 1, warnings: [warning] }} />,
    );
    expect(screen.getByText(warning)).toHaveClass("route-danger");
    expect(container.querySelector(".route-summary")).toHaveClass("bad");
  });
});

describe("MissionPanel routing", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("optimizes the route and marks detour points", async () => {
    mockFetch((url) =>
      url === "/api/route/optimize" ? { ok: true, error: null, route: ROUTE } : VALID,
    );
    render(<MissionPanel connected armed status={null} onResult={vi.fn()} />);
    fireEvent.click(screen.getByText("Optimize route"));
    await waitFor(() => expect(screen.getByText("Best possible order")).toBeInTheDocument());
    expect(screen.getByText("via")).toBeInTheDocument();
    const [req] = bodiesFor("/api/route/optimize") as { stops: unknown[]; finish: string }[];
    expect(req.stops).toHaveLength(2);
    expect(req.finish).toBe("return_home");
  });

  it("sends only real stops when re-optimizing a routed plan", async () => {
    mockFetch((url) =>
      url === "/api/route/optimize" ? { ok: true, error: null, route: ROUTE } : VALID,
    );
    render(<MissionPanel connected armed status={null} onResult={vi.fn()} />);
    fireEvent.click(screen.getByText("Optimize route"));
    await waitFor(() => expect(screen.getByText("via")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Optimize route"));
    await waitFor(() => expect(bodiesFor("/api/route/optimize")).toHaveLength(2));
    const second = bodiesFor("/api/route/optimize")[1] as { stops: { kind?: string }[] };
    expect(second.stops.every((s) => s.kind !== "via")).toBe(true);
    expect(second.stops).toHaveLength(2);
  });

  it("shows why a route could not be planned", async () => {
    mockFetch((url) =>
      url === "/api/route/optimize"
        ? { ok: false, error: "point 1 (10.0, 0.0) is inside no-fly zone 'Stadium'", route: null }
        : VALID,
    );
    render(<MissionPanel connected armed status={null} onResult={vi.fn()} />);
    fireEvent.click(screen.getByText("Optimize route"));
    await waitFor(() => expect(screen.getByText(/inside no-fly zone 'Stadium'/)).toBeInTheDocument());
  });

  it("clears the route summary after a manual edit", async () => {
    mockFetch((url) =>
      url === "/api/route/optimize" ? { ok: true, error: null, route: ROUTE } : VALID,
    );
    render(<MissionPanel connected armed status={null} onResult={vi.fn()} />);
    fireEvent.click(screen.getByText("Optimize route"));
    await waitFor(() => expect(screen.getByText("Best possible order")).toBeInTheDocument());
    fireEvent.click(screen.getByText("+ Add waypoint"));
    expect(screen.queryByText("Best possible order")).not.toBeInTheDocument();
  });

  it("imports a .plan file and merges its no-fly zones", async () => {
    const onAirspaceChanged = vi.fn();
    mockFetch((url) => {
      if (url === "/api/mission/import")
        return {
          ok: true,
          error: null,
          plan: { name: "x", speed_m_s: 5, finish: "land", waypoints: [{ x: 1, y: 2, altitude: 9 }] },
          zones: [{ name: "Imported zone 1", center: [50, 50], radius: 5 }],
          warnings: ["skipped 1 unsupported item(s): survey"],
        };
      if (url === "/api/airspace") return { ok: true, error: null, airspace: AIRSPACE };
      return VALID;
    });
    render(
      <MissionPanel
        connected
        armed
        status={null}
        onResult={vi.fn()}
        airspace={AIRSPACE}
        onAirspaceChanged={onAirspaceChanged}
      />,
    );
    const file = new File([JSON.stringify({ fileType: "Plan" })], "barn.plan", { type: "application/json" });
    fireEvent.change(screen.getByTestId("plan-file"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByDisplayValue("barn")).toBeInTheDocument());
    await waitFor(() => expect(onAirspaceChanged).toHaveBeenCalled());
    expect(screen.getByText("skipped 1 unsupported item(s): survey")).toBeInTheDocument();
    expect(screen.getByText("added 1 no-fly zone(s) from the file")).toBeInTheDocument();
    const [sent] = bodiesFor("/api/airspace") as { zones: { name: string }[] }[];
    expect(sent.zones.map((z) => z.name)).toEqual(["Stadium", "Imported zone 1"]);
  });
});

describe("AirspacePanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("lists zones and adds a circle zone, keeping the existing ones", async () => {
    const onChanged = vi.fn();
    mockFetch(() => ({ ok: true, error: null, airspace: AIRSPACE }));
    render(<AirspacePanel airspace={AIRSPACE} onChanged={onChanged} />);
    expect(screen.getByText("Stadium")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("e.g. Stadium"), { target: { value: "School" } });
    fireEvent.click(screen.getByText("+ Add zone"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const [sent] = bodiesFor("/api/airspace") as { zones: Record<string, unknown>[] }[];
    expect(sent.zones[0]).toEqual({ name: "Stadium", vertices: AIRSPACE.zones[0].vertices });
    expect(sent.zones[1]).toEqual({ name: "School", center: [30, 0], radius: 10 });
  });

  it("removes a zone", async () => {
    mockFetch(() => ({ ok: true, error: null, airspace: { margin_m: 5, zones: [] } }));
    render(<AirspacePanel airspace={AIRSPACE} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("remove zone Stadium"));
    await waitFor(() => expect(bodiesFor("/api/airspace")).toHaveLength(1));
    expect((bodiesFor("/api/airspace")[0] as { zones: unknown[] }).zones).toEqual([]);
  });

  it("shows the server's reason when a zone is refused", async () => {
    mockFetch(() => ({ ok: false, error: "zone 'Home' covers home; the aircraft could not take off or land", airspace: AIRSPACE }));
    render(<AirspacePanel airspace={AIRSPACE} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("+ Add zone"));
    await waitFor(() => expect(screen.getByText(/covers home/)).toBeInTheDocument());
  });
});
