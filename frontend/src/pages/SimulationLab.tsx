import { useEffect, useState } from "react";
import { api } from "../api";
import type { Capabilities, DiscoveredDrone } from "../types";
import DiagnosticsPanel from "../components/DiagnosticsPanel";

interface Props {
  wsConnected: boolean;
}

export default function SimulationLab({ wsConnected }: Props) {
  const [drones, setDrones] = useState<DiscoveredDrone[]>([]);
  const [caps, setCaps] = useState<Capabilities | null>(null);

  useEffect(() => {
    api.discover().then((r) => setDrones(r.drones)).catch(() => {});
    api.capabilities().then((c) => { if (c) setCaps(c); }).catch(() => {});
  }, []);

  return (
    <div className="sim-lab">
      <h2>Simulation Lab</h2>

      <section className="sim-section">
        <h3>Available Adapters</h3>
        <div className="adapter-list">
          {drones.map((d) => (
            <div key={d.adapter_type} className="adapter-card">
              <div className="adapter-card-header">
                <span className="adapter-name">{d.name}</span>
                <span className="adapter-transport">{d.transport}</span>
              </div>
              {d.capabilities && d.capabilities.length > 0 && (
                <div className="adapter-caps">
                  {d.capabilities.map((c) => (
                    <span key={c} className="cap-tag">{c}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {drones.length === 0 && <p className="empty">Discovering adapters...</p>}
        </div>
      </section>

      {caps && (
        <section className="sim-section">
          <h3>Active Adapter</h3>
          <div className="active-adapter-info">
            <div className="info-row">
              <span className="info-label">Name</span>
              <span className="info-value">{caps.name}</span>
            </div>
            <div className="info-row">
              <span className="info-label">Type</span>
              <span className="info-value">{caps.adapter_type}</span>
            </div>
            <div className="info-row">
              <span className="info-label">Simulated</span>
              <span className="info-value">{caps.is_simulated ? "Yes" : "No"}</span>
            </div>
            <div className="info-row">
              <span className="info-label">GPS</span>
              <span className="info-value">{caps.supports_gps ? "Supported" : "N/A"}</span>
            </div>
            <div className="info-row">
              <span className="info-label">Capabilities</span>
              <span className="info-value">{caps.supported_capabilities.join(", ")}</span>
            </div>
          </div>
        </section>
      )}

      <section className="sim-section">
        <DiagnosticsPanel wsConnected={wsConnected} />
      </section>

      <section className="sim-section">
        <h3>PX4 SITL Setup</h3>
        <div className="setup-info">
          <p>
            To use PX4 SITL, start the simulator separately. VantaFlight connects to
            the SITL instance via MAVLink at the configured endpoint.
          </p>
          <code className="setup-cmd">
            cd PX4-Autopilot && make px4_sitl gazebo-classic
          </code>
          <p>
            See <strong>docs/PX4.md</strong> and <strong>docs/SIMULATION.md</strong> for
            full instructions.
          </p>
        </div>
      </section>
    </div>
  );
}
