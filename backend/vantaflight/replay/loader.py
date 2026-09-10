"""Load flight recordings from the database into replay timelines."""
from __future__ import annotations

from ..data import FlightDatabase
from .models import FlightSummary, ReplayFrame, ReplayTimeline


class ReplayLoader:
    """Loads recorded flights from FlightDatabase into ReplayTimeline objects."""

    def __init__(self, db: FlightDatabase) -> None:
        self._db = db

    def list_flights(self) -> list[FlightSummary]:
        conn = self._db._conn
        rows = conn.execute(
            "SELECT id, started_at, ended_at, drone_id, drone_name, status "
            "FROM flights ORDER BY started_at DESC"
        ).fetchall()

        summaries = []
        for row in rows:
            fid = row[0]
            started = row[1]
            ended = row[2]
            duration = (ended - started) if ended else 0.0

            sample_count = conn.execute(
                "SELECT COUNT(*) FROM telemetry_samples WHERE flight_id=?", (fid,)
            ).fetchone()[0]
            event_count = conn.execute(
                "SELECT COUNT(*) FROM flight_events WHERE flight_id=?", (fid,)
            ).fetchone()[0]
            command_count = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE flight_id=?", (fid,)
            ).fetchone()[0]

            summaries.append(FlightSummary(
                flight_id=fid,
                started_at=started,
                ended_at=ended,
                drone_id=row[3],
                drone_name=row[4],
                status=row[5],
                duration_s=duration,
                sample_count=sample_count,
                event_count=event_count,
                command_count=command_count,
            ))
        return summaries

    def load_timeline(self, flight_id: int) -> ReplayTimeline:
        conn = self._db._conn

        flight = conn.execute(
            "SELECT started_at, ended_at FROM flights WHERE id=?", (flight_id,)
        ).fetchone()
        if flight is None:
            raise ValueError(f"flight {flight_id} not found")

        start_time = flight[0]
        end_time = flight[1] or start_time

        frames: list[ReplayFrame] = []

        telemetry_rows = conn.execute(
            "SELECT timestamp, connected, armed, flight_mode, "
            "x, y, z, altitude, velocity, heading, battery_percentage, "
            "connection_quality FROM telemetry_samples "
            "WHERE flight_id=? ORDER BY timestamp",
            (flight_id,),
        ).fetchall()

        for row in telemetry_rows:
            frames.append(ReplayFrame(
                timestamp=row[0],
                frame_type="telemetry",
                data={
                    "timestamp": row[0],
                    "connected": bool(row[1]),
                    "armed": bool(row[2]),
                    "flight_mode": row[3],
                    "x": row[4], "y": row[5], "z": row[6],
                    "altitude": row[7],
                    "velocity": row[8],
                    "heading": row[9],
                    "battery_percentage": row[10],
                    "connection_quality": row[11],
                },
            ))

        event_rows = conn.execute(
            "SELECT timestamp, event_type, message FROM flight_events "
            "WHERE flight_id=? ORDER BY timestamp",
            (flight_id,),
        ).fetchall()

        for row in event_rows:
            frames.append(ReplayFrame(
                timestamp=row[0],
                frame_type="event",
                data={
                    "timestamp": row[0],
                    "event_type": row[1],
                    "message": row[2],
                },
            ))

        command_rows = conn.execute(
            "SELECT timestamp, command, accepted, message FROM commands "
            "WHERE flight_id=? ORDER BY timestamp",
            (flight_id,),
        ).fetchall()

        for row in command_rows:
            frames.append(ReplayFrame(
                timestamp=row[0],
                frame_type="command",
                data={
                    "timestamp": row[0],
                    "command": row[1],
                    "accepted": bool(row[2]),
                    "message": row[3],
                },
            ))

        frames.sort(key=lambda f: f.timestamp)

        if frames:
            start_time = min(start_time, frames[0].timestamp)
            end_time = max(end_time, frames[-1].timestamp)

        return ReplayTimeline(
            flight_id=flight_id,
            start_time=start_time,
            end_time=end_time,
            duration_s=end_time - start_time,
            total_frames=len(frames),
            frames=frames,
        )
