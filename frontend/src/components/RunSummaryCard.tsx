import type { RunSummary } from "../types";

interface Props {
  summary: RunSummary;
  onDismiss: () => void;
}

export default function RunSummaryCard({ summary, onDismiss }: Props) {
  const mins = Math.floor(summary.duration / 60);
  const secs = Math.floor(summary.duration % 60);
  const batteryUsed = summary.battery_start - summary.battery_end;

  return (
    <div className="run-summary-card">
      <div className="summary-header">
        <h3>Flight Summary</h3>
        <button className="dismiss-btn" onClick={onDismiss}>Dismiss</button>
      </div>
      <div className="summary-grid">
        <SumItem label="Duration" value={`${mins}m ${secs}s`} />
        <SumItem label="Max Altitude" value={`${summary.max_altitude.toFixed(1)} m`} />
        <SumItem label="Max Speed" value={`${summary.max_speed.toFixed(1)} m/s`} />
        <SumItem label="Battery Used" value={`${batteryUsed.toFixed(1)}%`} />
        <SumItem label="Commands" value={String(summary.command_count)} />
        <SumItem label="Interruptions" value={String(summary.connection_interruptions)} />
        <SumItem label="Status" value={summary.final_status} />
      </div>
    </div>
  );
}

function SumItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="sum-item">
      <span className="sum-label">{label}</span>
      <span className="sum-value">{value}</span>
    </div>
  );
}
