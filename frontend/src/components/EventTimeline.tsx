import type { FlightEvent } from "../types";

export default function EventTimeline({ events, title = "Event Timeline" }: { events: FlightEvent[]; title?: string }) {
  return (
    <section className="timeline">
      <h2>{title}</h2>
      {events.length === 0 ? (
        <p className="empty">No events yet.</p>
      ) : (
        <ul>
          {events.map((ev, i) => (
            <li key={i} className={`ev ${ev.event_type}`}>
              <span className="ts">{new Date(ev.timestamp * 1000).toLocaleTimeString()}</span>
              <span className="tag" title={ev.event_type}>{ev.event_type.replace(/_/g, " ")}</span>
              <span className="msg">{ev.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
