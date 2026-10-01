import type { FlightEvent } from "../types";

export default function FailsafeBanner({ event, onDismiss }: { event: FlightEvent | null; onDismiss: () => void }) {
  if (!event) return null;
  return (
    <div className="failsafe-banner" role="alert">
      <strong>FAILSAFE</strong>
      <span>{event.message}</span>
      <button className="icon-btn" aria-label="dismiss failsafe" onClick={onDismiss}>
        &times;
      </button>
    </div>
  );
}
