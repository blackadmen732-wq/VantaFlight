import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { TrainingCampaign, TrainingSummary } from "../types";

export default function TrainingPage() {
  const [tab, setTab] = useState<"campaigns" | "curriculum" | "results">("campaigns");
  const [campaigns, setCampaigns] = useState<TrainingCampaign[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [summary, setSummary] = useState<TrainingSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Campaign IDs actively driven by this page. A run is requested one at a
  // time so pause/cancel can stop cleanly between deterministic simulations.
  const runningRef = useRef(new Set<string>());

  const refresh = async () => {
    try {
      const list = await api.listCampaigns();
      setCampaigns(list);
      setError(null);
      return list;
    } catch (e) {
      setError(String(e));
      return [];
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 2000);
    return () => {
      clearInterval(id);
      runningRef.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!selectedId) { setSummary(null); return; }
    let active = true;
    const poll = async () => {
      try {
        const s = await api.campaignSummary(selectedId);
        if (active) setSummary(s);
      } catch { /* campaign may have been deleted/restarted */ }
    };
    poll();
    const id = setInterval(poll, 1500);
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
    } finally {
      setBusy(false);
    }
  };

  const driveCampaign = async (id: string) => {
    runningRef.current.add(id);
    try {
      while (runningRef.current.has(id)) {
        const outcome = await api.runNextTraining(id);
        await refresh();
        if ("status" in outcome) break;
        // Yield to the browser between runs so Pause/Cancel remains responsive.
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    } catch (e) {
      setError(String(e));
    } finally {
      runningRef.current.delete(id);
      await refresh();
      if (selectedId === id) {
        try { setSummary(await api.campaignSummary(id)); } catch { /* ignore */ }
      }
    }
  };

  const startCampaign = async (id: string) => {
    setBusy(true);
    try {
      await api.startCampaign(id);
      setSelectedId(id);
      await refresh();
      // Do not await the entire campaign. The UI stays interactive while the
      // controlled run loop advances one deterministic run at a time.
      void driveCampaign(id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const pauseCampaign = async (id: string) => {
    runningRef.current.delete(id);
    setBusy(true);
    try {
      await api.pauseCampaign(id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const cancelCampaign = async (id: string) => {
    runningRef.current.delete(id);
    setBusy(true);
    try {
      await api.cancelCampaign(id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-content">
      <h2>Training Engine</h2>
      <p className="empty">Campaigns execute automatically after Start. Pause and Cancel take effect between simulation runs.</p>
      {error && <p className="error-msg">{error}</p>}

      <div className="tab-bar">
        <button className={`tab ${tab === "campaigns" ? "active" : ""}`} onClick={() => setTab("campaigns")}>Campaigns</button>
        <button className={`tab ${tab === "curriculum" ? "active" : ""}`} onClick={() => setTab("curriculum")}>Curriculum</button>
        <button className={`tab ${tab === "results" ? "active" : ""}`} onClick={() => setTab("results")}>Results</button>
      </div>

      {tab === "campaigns" && (
        <section className="status-card">
          <h3>Training Campaigns</h3>
          <button className="btn primary" disabled={busy} onClick={createDefault} style={{ marginBottom: "1rem" }}>New Campaign</button>

          {campaigns.length === 0 ? (
            <p className="empty">No campaigns yet. Create one to begin training.</p>
          ) : (
            <table className="data-table">
              <thead><tr><th>ID</th><th>Name</th><th>State</th><th>Progress</th><th>Actions</th></tr></thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.campaign_id} onClick={() => setSelectedId(c.campaign_id)} style={{ cursor: "pointer" }}>
                    <td>{c.campaign_id}</td>
                    <td>{c.name}</td>
                    <td><span className={`badge ${c.state === "RUNNING" ? "connected" : c.state === "FAILED" ? "disconnected" : ""}`}>{c.state}</span></td>
                    <td>{c.completed_runs} / {c.total_runs}</td>
                    <td>
                      {(c.state === "PENDING" || c.state === "PAUSED") && (
                        <button className="btn" disabled={busy} onClick={(e) => { e.stopPropagation(); void startCampaign(c.campaign_id); }}>
                          {c.state === "PAUSED" ? "Resume" : "Start"}
                        </button>
                      )}
                      {c.state === "RUNNING" && (
                        <button className="btn" disabled={busy} onClick={(e) => { e.stopPropagation(); void pauseCampaign(c.campaign_id); }}>Pause</button>
                      )}
                      {(c.state === "PENDING" || c.state === "RUNNING" || c.state === "PAUSED") && (
                        <button className="btn danger" disabled={busy} onClick={(e) => { e.stopPropagation(); void cancelCampaign(c.campaign_id); }}>Cancel</button>
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
          <p className="empty">Design deterministic curricula that progressively increase course difficulty and fault exposure.</p>
          <div className="metrics">
            <Metric label="Difficulty Tiers" value="6 tiers" />
            <Metric label="Course Modes" value="7 modes" />
            <Metric label="Fault Profiles" value="8 profiles" />
            <Metric label="Gate Range" value="3-50 gates" />
          </div>
        </section>
      )}

      {tab === "results" && (
        <section className="status-card">
          <h3>Training Results</h3>
          {!summary ? (
            <p className="empty">{selectedId ? "Loading summary..." : "Select a campaign from the Campaigns tab to view results."}</p>
          ) : (
            <>
              <div className="metrics">
                <Metric label="Campaign" value={summary.campaign_id} />
                <Metric label="State" value={summary.state} />
                <Metric label="Completed" value={`${summary.completed_runs} / ${summary.total_runs}`} />
                <Metric label="Success Rate" value={`${(summary.success_rate * 100).toFixed(1)}%`} />
                <Metric label="Avg Gate Completion" value={`${(summary.avg_gate_completion * 100).toFixed(1)}%`} />
                <Metric label="Avg Race Time" value={`${summary.avg_race_time_s.toFixed(1)}s`} />
              </div>
              {Object.keys(summary.failure_breakdown).length > 0 && (
                <>
                  <h4 style={{ marginTop: "1rem" }}>Failure Breakdown</h4>
                  <div className="metrics">
                    {Object.entries(summary.failure_breakdown).map(([cat, count]) => <Metric key={cat} label={cat} value={String(count)} />)}
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

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}
