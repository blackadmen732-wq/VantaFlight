import { useEffect, useState } from "react";
import { api } from "../api";
import type { AdapterType, Capabilities, DiscoveredDrone, Telemetry } from "../types";

const EMPTY_TELEMETRY: Telemetry = {
  timestamp: 0,
  connected: false,
  armed: false,
  flight_mode: "IDLE",
  x: 0,
  y: 0,
  z: 0,
  altitude: 0,
  velocity: 0,
  heading: 0,
  battery_percentage: 0,
  connection_quality: "NONE",
};

type CapabilityMap = Record<string, "SUPPORTED" | "UNSUPPORTED" | "UNAVAILABLE" | "UNKNOWN" | "DEGRADED">;

export default function HopperSetupPage() {
  const [hopper, setHopper] = useState<DiscoveredDrone | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [telemetry, setTelemetry] = useState<Telemetry>(EMPTY_TELEMETRY);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = async () => {
    try {
      const found = await api.discover();
      setHopper(found.drones.find((d) => d.adapter_type === ("hopper" as AdapterType)) ?? null);
      const [c, t] = await Promise.all([
        api.capabilities().catch(() => null),
        fetch("/api/telemetry").then((r) => r.json()).catch(() => EMPTY_TELEMETRY),
      ]);
      setCaps(c);
      setTelemetry(t as Telemetry);
    } catch {
      setMessage("Backend unavailable. Start VantaFlight first.");
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 1500);
    return () => clearInterval(id);
  }, []);

  const connect = async () => {
    setBusy(true);
    setMessage("");
    try {
      const result = await api.connect("hopper" as AdapterType);
      setMessage(result.message);
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Hopper connection failed");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      const result = await api.disconnect();
      setMessage(result.message);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const map = ((caps as (Capabilities & { capability_map?: CapabilityMap }) | null)?.capability_map ?? {}) as CapabilityMap;
  const hopperConnected = telemetry.connected && caps?.adapter_type === "hopper";
  const webBluetooth = typeof navigator !== "undefined" && "bluetooth" in navigator;

  return (
    <main>
      <section className="status-card">
        <div className="aircraft-line">
          <span className="label">FTW Robotics Hopper</span>
          <span className={`badge ${hopperConnected ? "connected" : "disconnected"}`}>
            {hopperConnected ? "CONNECTED" : "NOT CONNECTED"}
          </span>
        </div>
        <div className="metrics">
          <Metric label="Discovery" value={hopper ? "AVAILABLE" : "NOT FOUND"} />
          <Metric label="Transport" value={hopper?.transport ?? "HOPPER_WIFI"} />
          <Metric label="Camera host" value={hopper?.address ?? "http://192.168.2.1"} />
          <Metric label="Web Bluetooth" value={webBluetooth ? "AVAILABLE" : "UNAVAILABLE"} />
          <Metric label="Battery" value={(telemetry as Telemetry & { battery_available?: boolean }).battery_available === false ? "UNKNOWN" : `${telemetry.battery_percentage.toFixed(0)}%`} />
          <Metric label="Link quality" value={telemetry.connection_quality} />
        </div>
      </section>

      <section className="controls">
        <button className="btn primary" disabled={busy || hopperConnected} onClick={connect}>CONNECT HOPPER</button>
        <button className="btn danger" disabled={busy || !hopperConnected} onClick={disconnect}>DISCONNECT</button>
        <a className="btn" href="http://192.168.2.1" target="_blank" rel="noreferrer">OPEN HOPPER CAMERA</a>
      </section>

      {message && <section className="status-card"><p>{message}</p></section>}

      <section className="status-card">
        <h2>Capability truth</h2>
        <p>
          VantaFlight only marks a capability SUPPORTED when the active transport can really perform it.
          Unknown or unavailable FTW interfaces stay disabled rather than pretending to work.
        </p>
        <div className="metrics">
          {Object.keys(map).length === 0 ? (
            <Metric label="Capabilities" value="Connect Hopper to inspect" />
          ) : (
            Object.entries(map).map(([name, value]) => <Metric key={name} label={name} value={value} />)
          )}
        </div>
      </section>

      <section className="status-card">
        <h2>Connection path</h2>
        <p>Camera: Hopper Wi-Fi → HopperCameraConnector → VantaSight.</p>
        <p>Control: VantaExecution → HopperAdapter → official FTW transport → Hopper.</p>
        <p>Live-control buttons remain blocked until an official supported FTW control transport is configured, preflight passes, and hardware mode is explicitly authorized.</p>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}
