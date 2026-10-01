import { useEffect, useState } from "react";
import { api } from "../api";
import type { FailsafeStatus, FaultKind } from "../types";

interface Props {
  /** True when the connected aircraft is a simulator that accepts faults. */
  enabled: boolean;
}

const FAULTS: { kind: FaultKind; value: number; label: string; detail: string }[] = [
  { kind: "battery", value: 20, label: "Low battery (20%)", detail: "should return home" },
  { kind: "battery", value: 10, label: "Critical battery (10%)", detail: "should land in place" },
  { kind: "link_stall", value: 5, label: "Stall link for 5s", detail: "should hold position" },
];

/**
 * Rehearse failures on the simulator and watch the failsafes respond, before
 * they ever happen on a real aircraft.
 */
export default function FaultInjectionPanel({ enabled }: Props) {
  const [status, setStatus] = useState<FailsafeStatus | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = () =>
      api.failsafe().then((s) => active && setStatus(s)).catch(() => {});
    poll();
    const id = setInterval(poll, 2000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const inject = async (kind: FaultKind, value: number) => {
    const res = await api.injectFault(kind, value);
    setLastResult(res.accepted ? `Injected: ${res.message}` : `Not injected: ${res.message}`);
  };

  const cfg = status?.config;

  return (
    <div className="fault-panel">
      <h3>Failsafe Rehearsal</h3>
      {cfg && (
        <p className="fault-envelope">
          Return home at {cfg.battery_low_pct}% battery, land at {cfg.battery_critical_pct}%, hold
          after {cfg.link_stale_s}s without telemetry. Geofence: {cfg.geofence.radius_m} m radius,{" "}
          {cfg.geofence.max_altitude_m} m ceiling.
        </p>
      )}
      <div className="fault-buttons">
        {FAULTS.map((f) => (
          <button
            key={f.label}
            className="btn"
            disabled={!enabled}
            title={f.detail}
            onClick={() => inject(f.kind, f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {!enabled && <p className="hint">Connect the Mock Drone and take off to rehearse failsafes.</p>}
      {lastResult && <p className="fault-result">{lastResult}</p>}
      {status?.last_trigger && (
        <p className="fault-result">Last failsafe: {status.last_trigger.message}</p>
      )}
    </div>
  );
}
