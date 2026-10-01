import { useState } from "react";
import { api } from "../api";
import type { Airspace, AirspaceZone, ZoneSpec } from "../types";

interface Props {
  airspace: Airspace | null;
  /** Called after the server accepted a change, so the app can refresh. */
  onChanged: () => void;
}

/** Existing zones are re-sent as polygons, so edits never distort them. */
export function zoneToSpec(zone: AirspaceZone): ZoneSpec {
  return { name: zone.name, vertices: zone.vertices };
}

function zoneSummary(zone: AirspaceZone): string {
  const xs = zone.vertices.map((v) => v[0]);
  const ys = zone.vertices.map((v) => v[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const size = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  return `around ${cx.toFixed(0)} E, ${cy.toFixed(0)} N · ~${size.toFixed(0)} m across`;
}

/**
 * No-fly zones: places the aircraft must never fly over. Routes are planned
 * around them with a safety margin, and commands that would enter one are refused.
 */
export default function AirspacePanel({ airspace, onChanged }: Props) {
  const [name, setName] = useState("");
  const [x, setX] = useState("30");
  const [y, setY] = useState("0");
  const [radius, setRadius] = useState("10");
  const [margin, setMargin] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const zones = airspace?.zones ?? [];
  const marginValue = margin ?? String(airspace?.margin_m ?? 5);

  const save = async (specs: ZoneSpec[], marginM?: number) => {
    setBusy(true);
    try {
      const res = await api.airspace.set(specs, marginM);
      if (res.ok) {
        setError(null);
        onChanged();
        return true;
      }
      setError(res.error);
      return false;
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not save the airspace");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const addZone = async () => {
    const values = [x, y, radius].map(Number);
    if (values.some((v) => !Number.isFinite(v)) || values[2] <= 0) {
      setError("enter a position and a radius above 0");
      return;
    }
    const spec: ZoneSpec = {
      name: name.trim() || `Zone ${zones.length + 1}`,
      center: [values[0], values[1]],
      radius: values[2],
    };
    if (await save([...zones.map(zoneToSpec), spec])) setName("");
  };

  const removeZone = (id: string) => save(zones.filter((z) => z.id !== id).map(zoneToSpec));

  const saveMargin = async () => {
    const m = Number(marginValue);
    if (!Number.isFinite(m) || m < 0) {
      setError("margin must be 0 or more");
      return;
    }
    if (await save(zones.map(zoneToSpec), m)) setMargin(null);
  };

  return (
    <section className="airspace-panel">
      <div className="mission-header">
        <h2>No-fly zones</h2>
        <span className="hint">{zones.length === 0 ? "none" : `${zones.length} active`}</span>
      </div>

      {zones.length > 0 && (
        <ul className="zone-list">
          {zones.map((z) => (
            <li key={z.id}>
              <span className="zone-swatch" />
              <strong>{z.name}</strong>
              <span className="hint">{zoneSummary(z)}</span>
              <button
                className="icon-btn"
                aria-label={`remove zone ${z.name}`}
                disabled={busy}
                onClick={() => removeZone(z.id)}
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="zone-form">
        <label>
          <span className="field-label">Name</span>
          <input placeholder="e.g. Stadium" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span className="field-label">East (m)</span>
          <input type="number" value={x} onChange={(e) => setX(e.target.value)} />
        </label>
        <label>
          <span className="field-label">North (m)</span>
          <input type="number" value={y} onChange={(e) => setY(e.target.value)} />
        </label>
        <label>
          <span className="field-label">Radius (m)</span>
          <input type="number" value={radius} onChange={(e) => setRadius(e.target.value)} />
        </label>
        <button className="chip" disabled={busy} onClick={addZone}>+ Add zone</button>
      </div>

      <div className="zone-margin">
        <label>
          <span className="field-label">Safety margin (m)</span>
          <input type="number" value={marginValue} onChange={(e) => setMargin(e.target.value)} />
        </label>
        {margin !== null && (
          <button className="chip" disabled={busy} onClick={saveMargin}>Save margin</button>
        )}
      </div>

      {error && <div className="plan-check bad">{error}</div>}
    </section>
  );
}
