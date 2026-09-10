import { useState } from "react";

export default function TrainingPage() {
  const [tab, setTab] = useState<"campaigns" | "curriculum" | "results">("campaigns");

  return (
    <div className="page-content">
      <h2>Training Engine</h2>

      <div className="tab-bar">
        <button
          className={`tab ${tab === "campaigns" ? "active" : ""}`}
          onClick={() => setTab("campaigns")}
        >
          Campaigns
        </button>
        <button
          className={`tab ${tab === "curriculum" ? "active" : ""}`}
          onClick={() => setTab("curriculum")}
        >
          Curriculum
        </button>
        <button
          className={`tab ${tab === "results" ? "active" : ""}`}
          onClick={() => setTab("results")}
        >
          Results
        </button>
      </div>

      {tab === "campaigns" && (
        <section className="status-card">
          <h3>Training Campaigns</h3>
          <p className="empty">
            Training campaigns manage multi-run sequences across different courses,
            fault profiles, and difficulty levels. Configure and launch campaigns
            to systematically test autonomy performance.
          </p>
          <p className="empty" style={{ marginTop: "0.5rem", fontStyle: "italic" }}>
            Campaign execution requires the simulation runner.
            Connect to a simulator or use the synthetic camera source.
          </p>
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
              <span className="metric-label">Difficulty Levels</span>
              <span className="metric-value">7 modes</span>
            </div>
            <div className="metric">
              <span className="metric-label">Fault Types</span>
              <span className="metric-value">10 types</span>
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
          <p className="empty">
            No training runs completed yet. Launch a campaign to begin
            collecting performance data across courses and conditions.
          </p>
        </section>
      )}
    </div>
  );
}
