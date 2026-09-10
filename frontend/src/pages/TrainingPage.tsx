import { useEffect, useState } from "react";
import { api } from "../api";
import type { TrainingCampaign, TrainingSummary } from "../types";

export default function TrainingPage() {
  const [tab, setTab] = useState<"campaigns" | "curriculum" | "results">("campaigns");
  const [campaigns, setCampaigns] = useState<TrainingCampaign[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [summary, setSummary] = useState<TrainingSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const list = await api.listCampaigns();
      setCampaigns(list);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!selectedId) { setSummary(null); return; }
    let active = true;
    const poll = async () => {
      try {
        const s = await api.campaignSummary(selectedId);
        if (active) setSummary(s);
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { active = false; clearInterval(id); };
  }, [selectedId]);

  const createDefault = async () => {
    setBusy(true);
    try {
      const c = await api.createCampaign({
        name: `Campaign ${campaigns.length + 1}`,
        course_modes: ["RANDOM", "SLALOM"],
        seed_range: [0, 5],
        gate_counts: [8, 12],
        difficulty_tiers: ["MODERATE"],
      });
      await refresh();
      setSelectedId(c.campaign_id);
    } catch (e) {
      setError(String(e));
    }
    setBusy(false);
  };

  const startCampaign = async (id: string) => {
    setBusy(true);
    try { await api.startCampaign(id); } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const pauseCampaign = async (id: string) => {
    setBusy(true);
    try { await api.pauseCampaign(id); } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  const cancelCampaign = async (id: string) => {
    setBusy(true);
    try { await api.cancelCampaign(id); } catch (e) { setError(String(e)); }
    setBusy(false);
  };

  return (
    <div className="page-content">
      <h2>Training Engine</h2>
      {error && <p className="error-msg">{error}</p>}

      <div className="tab-bar">
        <button className={`tab ${tab === "campaigns" ? "active" : ""}`} onClick={() => setTab("campaigns")}>
          Campaigns
        </button>
        <button className={`tab ${tab === "curriculum" ? "active" : ""}`} onClick={() => setTab("curriculum")}>
          Curriculum
        </button>
        <button className={`tab ${tab === "results" ? "active" : ""}`} onClick={() => setTab("results")}>
          Results
        </button>
      </div>

      {tab === "campaigns" && (
        <section className="status-card">
          <h3>Training Campaigns</h3>
          <button className="btn primary" disabled={busy} onClick={createDefault} style={{ marginBottom: "1rem" }}>
            New Campaign
          </button>

          {campaigns.length === 0 ? (
            <p className="empty">No campaigns yet. Create one to begin training.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>State</th>
                  <th>Progress</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.campaign_id} onClick={() => setSelectedId(c.campaign_id)} style={{ cursor: "pointer" }}>
                    <td>{c.campaign_id}</td>
                    <td>{c.name}</td>
                    <td>
                      <span className={`badge ${c.state === "RUNNING" ? "connected" : c.state === "FAILED" ? "disconnected" : ""}`}>
                        {c.state}
                      </span>
                    </td>
                    <td>{c.completed_runs} / {c.total_runs}</td>
                    <td>
                      {c.state === "PENDING" && (
                        <button className="btn" disabled={busy} onClick={(e) => { e.stopPropagation(); startCampaign(c.campaign_id); }}>
                          Start
                        </button>
                      )}
                      {c.state === "RUNNING" && (
                        <button className="btn" disabled={busy} onClick={(e) => { e.stopPropagation(); pauseCampaign(c.campaign_id); }}>
                          Pause
                        </button>
                      )}
                      {(c.state === "PENDING" || c.state === "RUNNING" || c.state === "PAUSED") && (
                        <button className="btn danger" disabled={busy} onClick={(e) => { e.stopPropagation(); cancelCampaign(c.campaign_id); }}>
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "curriculum" && (
        <section className="status-card">
          <h3>Curriculum Design</h3>
          <p className="empty">
            Design training curricula that progressively increase difficulty.
            Courses scale from simple straight-line runs to technical adversarial
            layouts with full fault injection.
          </p>
          <div className="metrics">
            <div className="metric">
              <span className="metric-label">Difficulty Tiers</span>
              <span className="metric-value">6 tiers</span>
            </div>
            <div className="metric">
              <span className="metric-label">Course Modes</span>
              <span className="metric-value">7 modes</span>
            </div>
            <div className="metric">
              <span className="metric-label">Fault Profiles</span>
              <span className="metric-value">8 profiles</span>
            </div>
            <div className="metric">
              <span className="metric-label">Gate Range</span>
              <span className="metric-value">3-50 gates</span>
            </div>
          </div>
        </section>
      )}

      {tab === "results" && (
        <section className="status-card">
          <h3>Training Results</h3>
          {!summary ? (
            <p className="empty">
              {selectedId
                ? "Loading summary..."
                : "Select a campaign from the Campaigns tab to view results."}
            </p>
          ) : (
            <>
              <div className="metrics">
                <div className="metric">
                  <span className="metric-label">Campaign</span>
                  <span className="metric-value">{summary.campaign_id}</span>
                </div>
                <div className="metric">
                  <span className="metric-label">State</span>
                  <span className={`metric-value ${summary.state === "COMPLETE" ? "" : "metric-warn"}`}>
                    {summary.state}
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">Completed</span>
                  <span className="metric-value">{summary.completed_runs} / {summary.total_runs}</span>
                </div>
                <div className="metric">
                  <span className="metric-label">Success Rate</span>
                  <span className={`metric-value ${summary.success_rate < 0.5 ? "metric-warn" : ""}`}>
                    {(summary.success_rate * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="metric">
                  <span className="metric-label">Avg Gate Completion</span>
                  <span className="metric-value">{(summary.avg_gate_completion * 100).toFixed(1)}%</span>
                </div>
                <div className="metric">
                  <span className="metric-label">Avg Race Time</span>
                  <span className="metric-value">{summary.avg_race_time_s.toFixed(1)}s</span>
                </div>
              </div>
              {Object.keys(summary.failure_breakdown).length > 0 && (
                <>
                  <h4 style={{ marginTop: "1rem" }}>Failure Breakdown</h4>
                  <div className="metrics">
                    {Object.entries(summary.failure_breakdown).map(([cat, count]) => (
                      <div className="metric" key={cat}>
                        <span className="metric-label">{cat}</span>
                        <span className="metric-value">{count}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
