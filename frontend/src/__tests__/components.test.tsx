import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { api } from "../api";
import RunSummaryCard from "../components/RunSummaryCard";
import type { RunSummary } from "../types";

vi.mock("three", () => {
  const Vector3 = vi.fn();
  const Scene = vi.fn(() => ({ add: vi.fn(), background: null, fog: null }));
  const PerspectiveCamera = vi.fn(() => ({
    position: { set: vi.fn() },
    lookAt: vi.fn(),
    aspect: 1,
    updateProjectionMatrix: vi.fn(),
  }));
  const WebGLRenderer = vi.fn(() => ({
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
    domElement: document.createElement("canvas"),
  }));
  return {
    Scene,
    PerspectiveCamera,
    WebGLRenderer,
    Vector3,
    Color: vi.fn(),
    Fog: vi.fn(),
    AmbientLight: vi.fn(() => ({})),
    DirectionalLight: vi.fn(() => ({ position: { set: vi.fn() } })),
    GridHelper: vi.fn(() => ({})),
    Group: vi.fn(() => ({ add: vi.fn(), children: [], position: { set: vi.fn() }, rotation: { y: 0 } })),
    BoxGeometry: vi.fn(),
    CylinderGeometry: vi.fn(),
    ConeGeometry: vi.fn(),
    MeshStandardMaterial: vi.fn(() => ({ emissiveIntensity: 0 })),
    Mesh: vi.fn(() => ({ position: { set: vi.fn() }, rotation: { x: 0, z: 0 }, material: { emissiveIntensity: 0 } })),
    BufferGeometry: vi.fn(() => ({
      setAttribute: vi.fn(),
      setDrawRange: vi.fn(),
      attributes: { position: { setXYZ: vi.fn(), needsUpdate: false } },
    })),
    BufferAttribute: vi.fn(),
    LineBasicMaterial: vi.fn(),
    Line: vi.fn(() => ({ geometry: { attributes: { position: { setXYZ: vi.fn(), needsUpdate: false } }, setDrawRange: vi.fn() } })),
    MathUtils: { degToRad: (d: number) => d * Math.PI / 180 },
  };
});

describe("RunSummaryCard", () => {
  const summary: RunSummary = {
    duration: 125,
    max_altitude: 12.5,
    max_speed: 4.3,
    battery_start: 100,
    battery_end: 82,
    command_count: 5,
    connection_interruptions: 0,
    final_status: "completed",
  };

  it("renders flight summary data", () => {
    render(<RunSummaryCard summary={summary} onDismiss={vi.fn()} />);
    expect(screen.getByText("Flight Summary")).toBeInTheDocument();
    expect(screen.getByText("2m 5s")).toBeInTheDocument();
    expect(screen.getByText("12.5 m")).toBeInTheDocument();
    expect(screen.getByText("4.3 m/s")).toBeInTheDocument();
    expect(screen.getByText("18.0%")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("calls onDismiss when dismiss button clicked", async () => {
    const onDismiss = vi.fn();
    render(<RunSummaryCard summary={summary} onDismiss={onDismiss} />);
    screen.getByText("Dismiss").click();
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

describe("api GET requests", () => {
  it("rejects non-2xx JSON responses instead of returning error bodies", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response);

    await expect(api.course("missing/course")).rejects.toThrow(
      "request failed: 404",
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/courses/missing%2Fcourse");
    fetchMock.mockRestore();
  });

  it("returns decoded JSON for successful GET responses", async () => {
    const payload = { status: "ok", version: "0.5.0" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue(payload),
    } as unknown as Response);

    await expect(api.health()).resolves.toEqual(payload);
    fetchMock.mockRestore();
  });
});

describe("AdapterSelector", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({
        drones: [
          { drone_id: "mock-0", name: "Mock Drone", adapter_type: "mock", transport: "SIMULATED", address: "in-process", capabilities: ["arm"] },
          { drone_id: "px4-0", name: "PX4 SITL", adapter_type: "px4_sitl", transport: "PX4_SITL", address: "udpin://0.0.0.0:14540", capabilities: ["arm", "gps"] },
        ],
      }),
    }) as unknown as typeof globalThis.fetch;
  });

  it("renders with mock and px4 options after discovery", async () => {
    const AdapterSelector = (await import("../components/AdapterSelector")).default;
    render(<AdapterSelector selected="mock" onSelect={vi.fn()} disabled={false} />);

    await vi.waitFor(() => {
      expect(screen.getByText("Mock Drone")).toBeInTheDocument();
    });
    expect(screen.getByText("PX4 SITL")).toBeInTheDocument();
  });
});
