import { useEffect, useState } from "react";
import { api } from "../api";
import type { PerformanceState, RuntimeState, HardwareInfo } from "../types";

export default function PerformancePage() {
  const [perf, setPerf] = useState<PerformanceState | null>(null);
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [hw, setHw] = useState<HardwareInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const [p, r, h] = await Promise.all([
          api.performanceStatus(),
          api.runtimeStatus(),
          api.hardwareInfo(),
        ]);
        if (!active) return;
        setPerf(p);
        setRuntime(r);
        setHw(h);
        setError(null);
      } catch (e) {
        if (active) setError(String(e));
      }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { active = false; clearInterval(id); };
  }, []);

  return (
    <div className="page-content">
      <h2>Performance Monitor</h2>
      {error && <p className="error-msg">{error}</p>}

      {hw && (
        <section className="status-card">
          <h3>Hardware</h3>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">CPU</span>
              <span className="metric-value">{hw.cpu_arch} ({hw.cpu_count} cores)</span>
            </div>
            <div className="metric">
              <span className="metric-label">RAM</span>
              <span className="metric-value">{(hw.ram_total_mb / 1024).toFixed(1)} GB</span>
            </div>
            <div className="metric">
              <span className="metric-label">Python</span>
              <span className="metric-value">{hw.python_version}</span>
            </div>
            <div className="metric">
              <span className="metric-label">OpenCV</span>
              <span className="metric-value">{hw.opencv_version}</span>
            </div>
          </div>
        </section>
      )}

      {perf && (
        <section className="status-card">
          <h3>Adaptive Performance</h3>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">Level</span>
              <span className={`metric-value ${perf.level !== "FULL" ? "metric-warn" : ""}`}>
                {perf.level}
              </span>
            </div>
            <div className="metric">
              <span className="metric-label">Frame Age P95</span>
              <span className="metric-value">{perf.frame_age_p95_ms.toFixed(1)} ms</span>
            </div>
            <div className="metric">
              <span className="metric-label">Pipeline P95</span>
              <span className="metric-value">{perf.pipeline_p95_ms.toFixed(1)} ms</span>
            </div>
            <div className="metric">
              <span className="metric-label">CPU Pressure</span>
              <span className={`metric-value ${perf.cpu_pressure > 0.85 ? "metric-warn" : ""}`}>
                {(perf.cpu_pressure * 100).toFixed(0)}%
              </span>
            </div>
            <div className="metric">
              <span className="metric-label">Frame Drops</span>
              <span className="metric-value">{perf.frame_drops}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Queue Depth</span>
              <span className="metric-value">{perf.queue_depth}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Transitions</span>
              <span className="metric-value">{perf.transition_count}</span>
            </div>
          </div>
        </section>
      )}

      {runtime && (
        <section className="status-card">
          <h3>Services</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Service</th>
                <th>State</th>
                <th>Uptime</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(runtime.services).map((svc) => (
                <tr key={svc.name}>
                  <td>{svc.name}</td>
                  <td>
                    <span className={`badge ${svc.state === "RUNNING" ? "connected" : svc.state === "FAILED" ? "disconnected" : ""}`}>
                      {svc.state}
                    </span>
                  </td>
                  <td>{svc.uptime_s.toFixed(0)}s</td>
                  <td>{svc.error ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="metrics" style={{ marginTop: "1rem" }}>
            <div className="metric">
              <span className="metric-label">Overall</span>
              <span className="metric-value">{runtime.overall}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Mode</span>
              <span className="metric-value">{runtime.deployment_mode}</span>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
