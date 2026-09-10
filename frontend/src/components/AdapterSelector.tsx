import { useEffect, useState } from "react";
import { api } from "../api";
import type { AdapterType, DiscoveredDrone } from "../types";

interface Props {
  selected: AdapterType;
  onSelect: (adapter: AdapterType) => void;
  disabled: boolean;
}

export default function AdapterSelector({ selected, onSelect, disabled }: Props) {
  const [drones, setDrones] = useState<DiscoveredDrone[]>([]);

  useEffect(() => {
    api.discover().then((r) => setDrones(r.drones)).catch(() => {});
  }, []);

  return (
    <div className="adapter-selector">
      <span className="selector-label">Source</span>
      <div className="selector-options">
        {drones.length === 0 ? (
          <>
            <button
              className={`selector-btn ${selected === "mock" ? "active" : ""}`}
              disabled={disabled}
              onClick={() => onSelect("mock")}
            >
              Mock
            </button>
            <button
              className={`selector-btn ${selected === "px4_sitl" ? "active" : ""}`}
              disabled={disabled}
              onClick={() => onSelect("px4_sitl")}
            >
              PX4 SITL
            </button>
          </>
        ) : (
          drones.map((d) => (
            <button
              key={d.adapter_type}
              className={`selector-btn ${selected === d.adapter_type ? "active" : ""}`}
              disabled={disabled}
              onClick={() => onSelect(d.adapter_type)}
            >
              {d.name}
              <span className="transport-tag">{d.transport}</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
