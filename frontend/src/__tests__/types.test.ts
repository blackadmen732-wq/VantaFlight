import { describe, it, expect } from "vitest";
import { DISCONNECTED } from "../types";
import type { AdapterType, SessionState, TwinState, RunSummary } from "../types";

describe("types", () => {
  it("DISCONNECTED has expected defaults", () => {
    expect(DISCONNECTED.connected).toBe(false);
    expect(DISCONNECTED.armed).toBe(false);
    expect(DISCONNECTED.altitude).toBe(0);
    expect(DISCONNECTED.battery_percentage).toBe(0);
    expect(DISCONNECTED.connection_quality).toBe("NONE");
    expect(DISCONNECTED.flight_mode).toBe("IDLE");
  });

  it("AdapterType accepts valid values", () => {
    const mock: AdapterType = "mock";
    const px4: AdapterType = "px4_sitl";
    expect(mock).toBe("mock");
    expect(px4).toBe("px4_sitl");
  });

  it("SessionState accepts valid values", () => {
    const states: SessionState[] = [
      "NO_SESSION", "CONNECTED", "ACTIVE", "INTERRUPTED", "COMPLETED",
    ];
    expect(states).toHaveLength(5);
  });

  it("TwinState shape is usable", () => {
    const twin: TwinState = {
      connected: true,
      armed: false,
      altitude: 5.0,
      x: 0,
      y: 0,
      z: 5.0,
      heading: 90,
      velocity: 1.5,
      flight_mode: "HOLD",
      battery_percentage: 80,
      flight_duration: 30,
      trajectory: [{ timestamp: 1, x: 0, y: 0, z: 5, heading: 90 }],
    };
    expect(twin.altitude).toBe(5.0);
    expect(twin.trajectory).toHaveLength(1);
  });

  it("RunSummary shape is usable", () => {
    const summary: RunSummary = {
      duration: 120,
      max_altitude: 10,
      max_speed: 5,
      battery_start: 100,
      battery_end: 85,
      command_count: 4,
      connection_interruptions: 0,
      final_status: "completed",
    };
    expect(summary.duration).toBe(120);
    expect(summary.battery_start - summary.battery_end).toBe(15);
  });
});
