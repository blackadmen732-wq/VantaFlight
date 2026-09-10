import { useEffect, useState } from "react";

interface FlightRecord {
  flight_id: number;
  started_at: number;
  drone_name: string;
  status: string;
  duration_s: number;
  sample_count: number;
  event_count: number;
}

interface ReplayStatus {
  state: string;
  flight_id: number | null;
  cursor_s: number;
  duration_s: number;
  progress: number;
  playback_speed: number;
  total_frames: number;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`request failed: ${res.status}`);
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown = {}): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`request failed: ${res.status}`);
  return (await res.json()) as T;
}

export default function ReplayPage() {
  const [flights, setFlights] = useState<FlightRecord[]>([]);
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<{ flights: FlightRecord[] }>("/api/replay/flights")
      .then((r) => setFlights(r.flights))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const poll = async () => {
      try {
        const s = await get<ReplayStatus>("/api/replay/status");
        setStatus(s);
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 1000);
    return () => clearInterval(id);
  }, []);

  const load = async (flightId: number) => {
    try {
      await postJson(`/api/replay/load/${flightId}`);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  const play = () => postJson("/api/replay/play").catch((e) => setError(String(e)));
  const pause = () => postJson("/api/replay/pause").catch((e) => setError(String(e)));
  const stop = () => postJson("/api/replay/stop").catch((e) => setError(String(e)));

  const seek = (offset: number) =>
    postJson("/api/replay/seek", { time_offset: offset }).catch((e) => setError(String(e)));

  const setSpeed = (speed: number) =>
    postJson("/api/replay/speed", { speed }).catch((e) => setError(String(e)));

  const isLoaded = status && status.state !== "IDLE";
  const isPlaying = status?.state === "PLAYING";

  return (
    <div className="page-content">
      <h2>Flight Replay</h2>
      {error && <p className="error-msg">{error}</p>}

      {status && isLoaded && (
        <section className="status-card">
          <h3>Playback</h3>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">State</span>
              <span className="metric-value">{status.state}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Flight</span>
              <span className="metric-value">#{status.flight_id}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Position</span>
              <span className="metric-value">{status.cursor_s.toFixed(1)}s / {status.duration_s.toFixed(1)}s</span>
            </div>
            <div className="metric">
              <span className="metric-label">Speed</span>
              <span className="metric-value">{status.playback_speed}x</span>
            </div>
            <div className="metric">
              <span className="metric-label">Frames</span>
              <span className="metric-value">{status.total_frames}</span>
            </div>
          </div>

          <div style={{ margin: "1rem 0" }}>
            <input
              type="range"
              min={0}
              max={status.duration_s}
              step={0.1}
              value={status.cursor_s}
              onChange={(e) => seek(Number(e.target.value))}
              style={{ width: "100%" }}
            />
          </div>

          <div className="controls" style={{ gap: "0.5rem" }}>
            {isPlaying ? (
              <button className="btn" onClick={pause}>Pause</button>
            ) : (
              <button className="btn primary" onClick={play}>Play</button>
            )}
            <button className="btn" onClick={stop}>Stop</button>
            <button className="btn" onClick={() => setSpeed(0.5)}>0.5x</button>
            <button className="btn" onClick={() => setSpeed(1.0)}>1x</button>
            <button className="btn" onClick={() => setSpeed(2.0)}>2x</button>
            <button className="btn" onClick={() => setSpeed(4.0)}>4x</button>
          </div>
        </section>
      )}

      <section className="status-card">
        <h3>Recorded Flights</h3>
        {flights.length === 0 ? (
          <p className="empty">No recorded flights found. Complete a flight to create a recording.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Drone</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Samples</th>
                <th>Events</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {flights.map((f) => (
                <tr key={f.flight_id}>
                  <td>#{f.flight_id}</td>
                  <td>{f.drone_name}</td>
                  <td>
                    <span className={`badge ${f.status === "completed" ? "connected" : ""}`}>
                      {f.status}
                    </span>
                  </td>
                  <td>{f.duration_s.toFixed(1)}s</td>
                  <td>{f.sample_count}</td>
                  <td>{f.event_count}</td>
                  <td>
                    <button className="btn" onClick={() => load(f.flight_id)}>
                      Load
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
