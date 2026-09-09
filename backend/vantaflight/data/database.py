"""Local-first persistence.

Every simulated run is stored locally in SQLite (WAL mode for concurrent
read/write). No cloud, no network. Four tables capture a flight: the flight
itself, its telemetry samples, its timeline events, and the commands issued
(including safety rejections and errors).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..models import CommandResult, FlightEvent, Telemetry

SCHEMA = """
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


class FlightDatabase:
    """Thin SQLite wrapper. Pass ``:memory:`` for tests."""

    def __init__(self, path: str | Path = "vantaflight.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL only applies to on-disk databases; harmless (no-op) for :memory:.
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- flights ------------------------------------------------------------
    def start_flight(self, drone_id: str, drone_name: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO flights (started_at, drone_id, drone_name, status) "
            "VALUES (?, ?, ?, 'active')",
            (time.time(), drone_id, drone_name),
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
            "connection_quality) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    # -- reads (used by tests, replay later) --------------------------------
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

    def journal_mode(self) -> str:
        row = self._conn.execute("PRAGMA journal_mode;").fetchone()
        return row[0]

    def close(self) -> None:
        self._conn.close()
