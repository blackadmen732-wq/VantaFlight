import { useEffect, useState } from "react";
import { api } from "../api";
import type { VisionStatus, FusedTargetEstimate, SceneState } from "../types";

export default function VisionPage({ wsConnected }: { wsConnected: boolean }) {
  const [vision, setVision] = useState<VisionStatus | null>(null);
  const [tracks, setTracks] = useState<FusedTargetEstimate[]>([]);
  const [scene, setScene] = useState<SceneState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const [v, t, s] = await Promise.all([
          api.visionStatus(),
          api.visionTracks(),
          api.scene(),
        ]);
        if (!active) return;
        setVision(v);
        setTracks(t);
        setScene(s);
        setError(null);
      } catch (e) {
        if (active) setError(String(e));
      }
    };
    poll();
    const id = setInterval(poll, 1000);
    return () => { active = false; clearInterval(id); };
  }, []);

  return (
    <div className="page-content">
      <h2>Vision Pipeline</h2>

      <div className="status-indicator">
        <span className={`dot ${wsConnected ? "green" : "red"}`} />
        <span>{wsConnected ? "Stream Connected" : "Stream Disconnected"}</span>
      </div>

      {error && <p className="error-msg">{error}</p>}

      {vision && (
        <section className="status-card">
          <h3>Pipeline Status</h3>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">Lock State</span>
              <span className="metric-value">{vision.lock_state}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Running</span>
              <span className="metric-value">{vision.running ? "YES" : "NO"}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Source</span>
              <span className="metric-value">{vision.source ?? "none"}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Pipeline Latency</span>
              <span className="metric-value">{vision.pipeline_latency_ms.toFixed(1)} ms</span>
            </div>
          </div>
          <h4>Frame Buffer</h4>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">Captured</span>
              <span className="metric-value">{vision.frame_metrics.frames_captured}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Processed</span>
              <span className="metric-value">{vision.frame_metrics.frames_processed}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Dropped</span>
              <span className="metric-value">{vision.frame_metrics.dropped_frames}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Queue</span>
              <span className="metric-value">{vision.frame_metrics.queue_depth}</span>
            </div>
          </div>
        </section>
      )}

      {scene && (
        <section className="status-card">
          <h3>Racing Scene</h3>
          <div className="metrics">
            {scene.current && (
              <div className="metric">
                <span className="metric-label">Current Gate</span>
                <span className="metric-value">
                  {scene.current.target_id} ({(scene.current.confidence * 100).toFixed(0)}%)
                </span>
              </div>
            )}
            {scene.next && (
              <div className="metric">
                <span className="metric-label">Next Gate</span>
                <span className="metric-value">
                  {scene.next.target_id} ({(scene.next.confidence * 100).toFixed(0)}%)
                </span>
              </div>
            )}
            {!scene.current && !scene.next && (
              <div className="metric">
                <span className="metric-label">Status</span>
                <span className="metric-value">No gates in scene</span>
              </div>
            )}
          </div>
        </section>
      )}

      <section className="status-card">
        <h3>Active Tracks ({tracks.length})</h3>
        {tracks.length === 0 ? (
          <p className="empty">No active tracks.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>State</th>
                <th>Confidence</th>
                <th>Uncertainty</th>
                <th>Age</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((t) => (
                <tr key={t.target_id}>
                  <td>{t.target_id}</td>
                  <td>{t.track_state}</td>
                  <td>{(t.confidence * 100).toFixed(0)}%</td>
                  <td>{t.uncertainty.toFixed(3)} m</td>
                  <td>{t.measurement_age_s.toFixed(2)} s</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
