export default function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={`metric ${warn ? "metric-warn" : ""}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}
