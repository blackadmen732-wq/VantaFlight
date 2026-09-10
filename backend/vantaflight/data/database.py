"""Local-first persistence with schema migration."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..models import CommandResult, FlightEvent, Telemetry

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS flights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    drone_id    TEXT NOT NULL,
    drone_name  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id           INTEGER NOT NULL REFERENCES flights(id),
    timestamp           REAL NOT NULL,
    connected           INTEGER NOT NULL,
    armed               INTEGER NOT NULL,
    flight_mode         TEXT NOT NULL,
    x                   REAL NOT NULL,
    y                   REAL NOT NULL,
    z                   REAL NOT NULL,
    altitude            REAL NOT NULL,
    velocity            REAL NOT NULL,
    heading             REAL NOT NULL,
    battery_percentage  REAL NOT NULL,
    connection_quality  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flight_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id   INTEGER NOT NULL REFERENCES flights(id),
    timestamp   REAL NOT NULL,
    event_type  TEXT NOT NULL,
    message     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id   INTEGER NOT NULL REFERENCES flights(id),
    timestamp   REAL NOT NULL,
    command     TEXT NOT NULL,
    accepted    INTEGER NOT NULL,
    message     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_samples_flight ON telemetry_samples(flight_id);
CREATE INDEX IF NOT EXISTS idx_events_flight ON flight_events(flight_id);
CREATE INDEX IF NOT EXISTS idx_commands_flight ON commands(flight_id);
"""

MIGRATION_V2 = """
ALTER TABLE flights ADD COLUMN adapter_type TEXT NOT NULL DEFAULT 'mock';
ALTER TABLE flights ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 1;
ALTER TABLE flights ADD COLUMN software_version TEXT NOT NULL DEFAULT '0.1.0';
ALTER TABLE flights ADD COLUMN connection_type TEXT NOT NULL DEFAULT 'SIMULATED';

ALTER TABLE telemetry_samples ADD COLUMN latitude REAL;
ALTER TABLE telemetry_samples ADD COLUMN longitude REAL;
ALTER TABLE telemetry_samples ADD COLUMN ground_speed REAL NOT NULL DEFAULT 0.0;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""

CURRENT_SCHEMA_VERSION = 2


class FlightDatabase:
    """Thin SQLite wrapper with schema migration. Pass ``:memory:`` for tests."""

    def __init__(self, path: str | Path = "vantaflight.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA_V1)
        self._conn.commit()

        version = self._get_schema_version()
        if version < 2:
            self._migrate_v2()

    def _get_schema_version(self) -> int:
        try:
            row = self._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return int(row[0]) if row else 1
        except sqlite3.OperationalError:
            return 1

    def _migrate_v2(self) -> None:
        existing_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(flights)").fetchall()
        } | {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(telemetry_samples)").fetchall()
        }

        for stmt in MIGRATION_V2.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    continue
                raise
        self._conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        self._conn.commit()

    # -- flights ------------------------------------------------------------
    def start_flight(
        self,
        drone_id: str,
        drone_name: str,
        adapter_type: str = "mock",
        is_simulated: bool = True,
        connection_type: str = "SIMULATED",
        software_version: str = "0.3.0",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO flights (started_at, drone_id, drone_name, status, "
            "adapter_type, is_simulated, connection_type, software_version) "
            "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
            (time.time(), drone_id, drone_name, adapter_type,
             int(is_simulated), connection_type, software_version),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def end_flight(self, flight_id: int, status: str = "completed") -> None:
        self._conn.execute(
            "UPDATE flights SET ended_at = ?, status = ? WHERE id = ?",
            (time.time(), status, flight_id),
        )
        self._conn.commit()

    # -- writes -------------------------------------------------------------
    def record_telemetry(self, flight_id: int, t: Telemetry) -> None:
        self._conn.execute(
            "INSERT INTO telemetry_samples (flight_id, timestamp, connected, armed, "
            "flight_mode, x, y, z, altitude, velocity, heading, battery_percentage, "
            "connection_quality, latitude, longitude, ground_speed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                flight_id,
                t.timestamp,
                int(t.connected),
                int(t.armed),
                t.flight_mode.value,
                t.x,
                t.y,
                t.z,
                t.altitude,
                t.velocity,
                t.heading,
                t.battery_percentage,
                t.connection_quality.value,
                t.latitude,
                t.longitude,
                t.ground_speed,
            ),
        )
        self._conn.commit()

    def record_event(self, flight_id: int, event: FlightEvent) -> None:
        self._conn.execute(
            "INSERT INTO flight_events (flight_id, timestamp, event_type, message) "
            "VALUES (?, ?, ?, ?)",
            (flight_id, event.timestamp, event.event_type, event.message),
        )
        self._conn.commit()

    def record_command(self, flight_id: int, result: CommandResult) -> None:
        self._conn.execute(
            "INSERT INTO commands (flight_id, timestamp, command, accepted, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                flight_id,
                result.timestamp,
                result.command,
                int(result.accepted),
                result.message,
            ),
        )
        self._conn.commit()

    # -- reads --------------------------------------------------------------
    def count_telemetry(self, flight_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM telemetry_samples WHERE flight_id = ?",
            (flight_id,),
        ).fetchone()
        return int(row["n"])

    def get_events(self, flight_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT timestamp, event_type, message FROM flight_events "
            "WHERE flight_id = ? ORDER BY id",
            (flight_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_commands(self, flight_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT timestamp, command, accepted, message FROM commands "
            "WHERE flight_id = ? ORDER BY id",
            (flight_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_flight(self, flight_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM flights WHERE id = ?", (flight_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_run_summary(self, flight_id: int) -> dict | None:
        flight = self.get_flight(flight_id)
        if not flight:
            return None
        samples = self._conn.execute(
            "SELECT altitude, velocity, battery_percentage FROM telemetry_samples "
            "WHERE flight_id = ? ORDER BY id",
            (flight_id,),
        ).fetchall()
        commands = self.get_commands(flight_id)
        events = self.get_events(flight_id)

        max_alt = max((s["altitude"] for s in samples), default=0.0)
        max_speed = max((abs(s["velocity"]) for s in samples), default=0.0)
        bat_start = samples[0]["battery_percentage"] if samples else None
        bat_end = samples[-1]["battery_percentage"] if samples else None
        interruptions = sum(1 for e in events if e["event_type"] == "connection_lost")

        duration = 0.0
        if flight["ended_at"] and flight["started_at"]:
            duration = flight["ended_at"] - flight["started_at"]

        return {
            "flight_id": flight_id,
            "duration": round(duration, 1),
            "max_altitude": round(max_alt, 2),
            "max_speed": round(max_speed, 2),
            "battery_start": round(bat_start, 1) if bat_start is not None else None,
            "battery_end": round(bat_end, 1) if bat_end is not None else None,
            "command_count": len(commands),
            "connection_interruptions": interruptions,
            "final_status": flight["status"],
            "adapter_type": flight.get("adapter_type", "unknown"),
            "is_simulated": bool(flight.get("is_simulated", True)),
        }

    def journal_mode(self) -> str:
        row = self._conn.execute("PRAGMA journal_mode;").fetchone()
        return row[0]

    def close(self) -> None:
        self._conn.close()
