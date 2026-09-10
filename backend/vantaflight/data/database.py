"""Local-first persistence with schema migration."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

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

SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS camera_profiles (
    camera_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    calibration_version TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    seed INTEGER NOT NULL,
    mode TEXT NOT NULL,
    safe_volume_json TEXT NOT NULL,
    difficulty_json TEXT NOT NULL,
    course_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS course_gates (
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    gate_order INTEGER NOT NULL,
    gate_id TEXT NOT NULL,
    gate_json TEXT NOT NULL,
    PRIMARY KEY (course_id, gate_order)
);

CREATE TABLE IF NOT EXISTS simulation_runs (
    id TEXT PRIMARY KEY,
    course_id TEXT REFERENCES courses(id),
    started_at REAL NOT NULL,
    ended_at REAL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vision_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES simulation_runs(id),
    frame_id TEXT NOT NULL,
    target_id TEXT,
    captured_at REAL NOT NULL,
    recorded_at REAL NOT NULL,
    measurement_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES simulation_runs(id),
    target_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    track_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trajectory_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES simulation_runs(id),
    trajectory_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    point_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES simulation_runs(id),
    scope TEXT NOT NULL,
    scope_id TEXT,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    unit TEXT,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS algorithm_configurations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    values_json TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    configuration_id TEXT,
    candidate_json TEXT NOT NULL,
    score REAL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS recording_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES simulation_runs(id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vision_run_time
    ON vision_measurements(run_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_tracks_run_target
    ON target_tracks(run_id, target_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_trajectory_run_time
    ON trajectory_points(run_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_run_scope
    ON run_metrics(run_id, scope, scope_id);
"""

CURRENT_SCHEMA_VERSION = 3


class FlightDatabase:
    """Thin SQLite wrapper with schema migration. Pass ``:memory:`` for tests."""

    def __init__(self, path: str | Path = "vantaflight.db") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
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
            version = 2
        if version < 3:
            self._migrate_v3()

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

    def _migrate_v3(self) -> None:
        """Add V0.5 intelligence tables without modifying existing user rows."""
        with self._lock:
            self._conn.executescript(SCHEMA_V3)
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

    # -- V0.5 intelligence persistence ------------------------------------
    @property
    def schema_version(self) -> int:
        return self._get_schema_version()

    def upsert_camera_profile(
        self, camera_id: str, profile: dict[str, Any], calibration_version: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO camera_profiles "
                "(camera_id, profile_json, calibration_version, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(camera_id) DO UPDATE SET "
                "profile_json=excluded.profile_json, "
                "calibration_version=excluded.calibration_version, "
                "updated_at=excluded.updated_at",
                (
                    camera_id,
                    json.dumps(profile, separators=(",", ":")),
                    calibration_version,
                    time.time(),
                ),
            )
            self._conn.commit()

    def save_course(self, course: dict[str, Any]) -> None:
        """Persist course metadata and gates atomically."""
        gates = list(course.get("gates", []))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO courses "
                "(id, seed, mode, safe_volume_json, difficulty_json, course_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    course["id"],
                    int(course["seed"]),
                    str(course["mode"]),
                    json.dumps(course.get("safe_volume", {}), separators=(",", ":")),
                    json.dumps(course.get("difficulty", {}), separators=(",", ":")),
                    json.dumps(course, separators=(",", ":")),
                    time.time(),
                ),
            )
            self._conn.execute(
                "DELETE FROM course_gates WHERE course_id = ?", (course["id"],)
            )
            self._conn.executemany(
                "INSERT INTO course_gates (course_id, gate_order, gate_id, gate_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        course["id"],
                        int(gate.get("course_order", index)),
                        str(gate.get("id", f"gate-{index}")),
                        json.dumps(gate, separators=(",", ":")),
                    )
                    for index, gate in enumerate(gates)
                ],
            )
            self._conn.commit()

    def start_simulation_run(
        self,
        run_id: str,
        course_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO simulation_runs "
                "(id, course_id, started_at, status, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    course_id,
                    time.time(),
                    "running",
                    json.dumps(metadata or {}, separators=(",", ":")),
                ),
            )
            self._conn.commit()

    def finish_simulation_run(self, run_id: str, status: str = "completed") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE simulation_runs SET ended_at = ?, status = ? WHERE id = ?",
                (time.time(), status, run_id),
            )
            self._conn.commit()

    def record_v05_batch(self, records: list[dict[str, Any]]) -> None:
        """Write a heterogeneous recorder batch in one transaction."""
        with self._lock:
            for record in records:
                kind = record["kind"]
                payload = record["payload"]
                if kind == "vision_measurement":
                    self._conn.execute(
                        "INSERT INTO vision_measurements "
                        "(run_id, frame_id, target_id, captured_at, recorded_at, measurement_json) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            payload.get("run_id"),
                            payload["frame_id"],
                            payload.get("target_id"),
                            float(payload["captured_at"]),
                            time.time(),
                            json.dumps(payload, separators=(",", ":")),
                        ),
                    )
                elif kind == "target_track":
                    self._conn.execute(
                        "INSERT INTO target_tracks "
                        "(run_id, target_id, timestamp, track_json) VALUES (?, ?, ?, ?)",
                        (
                            payload.get("run_id"),
                            payload["target_id"],
                            float(payload["timestamp"]),
                            json.dumps(payload, separators=(",", ":")),
                        ),
                    )
                elif kind == "trajectory_point":
                    self._conn.execute(
                        "INSERT INTO trajectory_points "
                        "(run_id, trajectory_id, timestamp, point_json) VALUES (?, ?, ?, ?)",
                        (
                            payload.get("run_id"),
                            payload["trajectory_id"],
                            float(payload["timestamp"]),
                            json.dumps(payload, separators=(",", ":")),
                        ),
                    )
                elif kind == "run_metric":
                    self._conn.execute(
                        "INSERT INTO run_metrics "
                        "(run_id, scope, scope_id, metric_name, metric_value, unit, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            payload.get("run_id"),
                            payload.get("scope", "run"),
                            payload.get("scope_id"),
                            payload["metric_name"],
                            float(payload["metric_value"]),
                            payload.get("unit"),
                            float(payload.get("timestamp", time.time())),
                        ),
                    )
                else:
                    raise ValueError(f"unsupported recorder record kind: {kind}")
            self._conn.commit()

    def get_v05_counts(self) -> dict[str, int]:
        names = (
            "courses",
            "course_gates",
            "simulation_runs",
            "vision_measurements",
            "target_tracks",
            "trajectory_points",
            "run_metrics",
            "camera_profiles",
            "algorithm_configurations",
            "parameter_experiments",
        )
        with self._lock:
            return {
                name: int(self._conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in names
            }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
