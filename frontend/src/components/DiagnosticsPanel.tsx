import { useEffect, useState } from "react";
import { api } from "../api";
import type { Diagnostics } from "../types";

interface Props {
  wsConnected: boolean;
}

export default function DiagnosticsPanel({ wsConnected }: Props) {
  const [diag, setDiag] = useState<Diagnostics | null>(null);

  useEffect(() => {
    let active = true;
    const poll = () => {
      api.diagnostics().then((d) => {
        if (active) setDiag(d);
      }).catch(() => {});
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { active = false; clearInterval(id); };
  }, []);

  return (
    <div className="diagnostics-panel">
      <h3>Diagnostics</h3>
      <div className="diag-grid">
        <DiagItem
          label="Telemetry Hz"
          value={diag ? diag.telemetry_hz.toFixed(1) : "--"}
          status={diag && diag.telemetry_hz > 5 ? "good" : diag && diag.telemetry_hz > 0 ? "warn" : "bad"}
        />
        <DiagItem
          label="Last Telemetry"
          value={diag ? `${diag.last_telemetry_age.toFixed(1)}s ago` : "--"}
          status={diag && diag.last_telemetry_age < 2 ? "good" : diag && diag.last_telemetry_age < 5 ? "warn" : "bad"}
        />
        <DiagItem
          label="Cmd RTT"
          value={diag && diag.avg_command_rtt_ms > 0 ? `${diag.avg_command_rtt_ms.toFixed(0)}ms` : "--"}
          status={diag && diag.avg_command_rtt_ms < 200 ? "good" : "warn"}
        />
        <DiagItem
          label="WebSocket"
          value={wsConnected ? "Connected" : "Disconnected"}
          status={wsConnected ? "good" : "bad"}
        />
        <DiagItem
          label="Session"
          value={diag?.session_state ?? "--"}
          status={diag?.session_state === "ACTIVE" || diag?.session_state === "CONNECTED" ? "good" : "neutral"}
        />
        <DiagItem
          label="DB Write"
          value={diag && diag.avg_db_write_ms > 0 ? `${diag.avg_db_write_ms.toFixed(1)}ms` : "--"}
          status={diag && diag.avg_db_write_ms < 50 ? "good" : "warn"}
        />
      </div>
    </div>
  );
}

function DiagItem({ label, value, status }: { label: string; value: string; status: string }) {
  return (
    <div className={`diag-item diag-${status}`}>
      <span className="diag-label">{label}</span>
      <span className="diag-value">{value}</span>
    </div>
  );
}
