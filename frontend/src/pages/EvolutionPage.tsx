import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { EvolutionComparison, TrainingCampaign, Weakness } from "../types";

/**
 * Evidence-driven improvement: training campaigns are the evidence, the
 * weakness map says where the stack fails, and the champion/challenger gate
 * decides whether a new variant is actually better on identical scenarios.
 */
export default function EvolutionPage() {
  const [campaigns, setCampaigns] = useState<TrainingCampaign[]>([]);
  const [scope, setScope] = useState<string>("");
  const [weaknesses, setWeaknesses] = useState<Weakness[]>([]);
  const [runCount, setRunCount] = useState(0);
  const [champion, setChampion] = useState("");
  const [challenger, setChallenger] = useState("");
  const [result, setResult] = useState<EvolutionComparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, report] = await Promise.all([
        api.listCampaigns(),
        api.evolution.weaknesses(scope || undefined),
      ]);
      setCampaigns(list);
      setWeaknesses(report.weaknesses);
      setRunCount(report.run_count);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [scope]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const compare = async () => {
    setResult(null);
    setError(null);
    try {
      setResult(await api.evolution.compare(champion, challenger));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const withRuns = campaigns.filter((c) => c.completed_runs > 0);

  return (
    <div className="page-content evolution-page">
      <div className="page-header">
        <h2>Evolution</h2>
        <button className="btn small" onClick={() => void refresh()}>REFRESH</button>
      </div>
      <p className="hint">
        Run training campaigns first; every finished run becomes evidence here. Nothing is promoted on one good
        number: a challenger must fly the same scenarios and seeds, beat the champion's mean score, and not get worse
        at its worst.
      </p>
      {error && <p className="error-msg">{error}</p>}

      <section className="sim-section">
        <div className="section-head">
          <h3>Weakness map</h3>
          <label className="inline-field">
            Evidence
            <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="evidence scope">
              <option value="">All campaigns</option>
              {campaigns.map((c) => (
                <option key={c.campaign_id} value={c.campaign_id}>
                  {c.name} ({c.completed_runs} runs)
                </option>
              ))}
            </select>
          </label>
        </div>
        {weaknesses.length === 0 ? (
          <p className="empty">
            No training runs yet. Start a campaign on the <Link to="/training">Training</Link> page or from{" "}
            <Link to="/forge">VantaForge</Link>.
          </p>
        ) : (
          <>
            <p className="muted small">{runCount} runs, highest priority first.</p>
            <table className="data-table weakness-table">
              <thead>
                <tr>
                  <th>Condition</th>
                  <th>Failure rate</th>
                  <th>Severity</th>
                  <th>Runs</th>
                  <th>Priority</th>
                  <th>Top causes</th>
                </tr>
              </thead>
              <tbody>
                {weaknesses.map((w) => (
                  <tr key={w.condition}>
                    <td>{w.condition}</td>
                    <td>
                      <Bar value={w.failure_rate} danger />
                      {(w.failure_rate * 100).toFixed(0)}%
                    </td>
                    <td>{w.severity.toFixed(2)}</td>
                    <td>{w.sample_count}</td>
                    <td><strong>{w.priority.toFixed(3)}</strong></td>
                    <td>
                      {w.top_causes.length === 0
                        ? "—"
                        : w.top_causes.map((c) => `${c.category.replace(/_/g, " ").toLowerCase()} ×${c.count}`).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="sim-section">
        <h3>Champion vs challenger</h3>
        {withRuns.length < 2 ? (
          <p className="empty">Needs two campaigns with finished runs on the same scenarios.</p>
        ) : (
          <div className="compare-form">
            <label className="inline-field">
              Champion
              <select value={champion} onChange={(e) => setChampion(e.target.value)} aria-label="champion">
                <option value="">Choose…</option>
                {withRuns.map((c) => <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>)}
              </select>
            </label>
            <label className="inline-field">
              Challenger
              <select value={challenger} onChange={(e) => setChallenger(e.target.value)} aria-label="challenger">
                <option value="">Choose…</option>
                {withRuns.map((c) => <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>)}
              </select>
            </label>
            <button
              className="btn primary"
              disabled={!champion || !challenger || champion === challenger}
              onClick={compare}
            >
              EVALUATE
            </button>
          </div>
        )}
        {result && (
          <div className={`verdict ${result.promote ? "promote" : "hold"}`} role="status">
            <strong>{result.promote ? "Promote the challenger" : "Keep the champion"}</strong>
            <span>{result.reason}</span>
            <table className="data-table">
              <thead>
                <tr><th /><th>Runs</th><th>Success</th><th>Mean {result.metric}</th><th>Worst</th></tr>
              </thead>
              <tbody>
                {[result.champion, result.challenger].map((s, i) => (
                  <tr key={i}>
                    <td>{i === 0 ? "Champion" : "Challenger"}</td>
                    <td>{s.runs}</td>
                    <td>{(s.success_rate * 100).toFixed(0)}%</td>
                    <td>{s.mean_score.toFixed(3)}</td>
                    <td>{s.worst_score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Bar({ value, danger }: { value: number; danger?: boolean }) {
  return (
    <span className="bar-track" aria-hidden="true">
      <span className={`bar-fill ${danger ? "danger" : ""}`} style={{ width: `${Math.round(value * 100)}%` }} />
    </span>
  );
}
