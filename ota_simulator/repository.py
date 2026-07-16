"""SQLite persistence for controller snapshots and diagnostic events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ota_simulator.domain import (
    Controller,
    ControllerHealth,
    DiagnosticEvent,
    DiagnosticTroubleCode,
    EventSeverity,
    SemanticVersion,
    UpdateOutcome,
    UpdateStage,
)
from ota_simulator.scenarios import seed_controllers

SCHEMA = """
CREATE TABLE IF NOT EXISTS controllers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    current_version TEXT NOT NULL,
    previous_version TEXT,
    health TEXT NOT NULL,
    stage TEXT NOT NULL,
    voltage REAL NOT NULL,
    connectivity TEXT NOT NULL,
    active_dtcs TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostic_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    controller_id TEXT REFERENCES controllers(id) ON DELETE CASCADE,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    stage TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_controller_sequence
    ON diagnostic_events(controller_id, sequence);
"""
MAX_RETAINED_EVENTS = 2_000


class SQLiteRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM controllers").fetchone()[0]
            if count == 0:
                self._insert_controllers(connection, seed_controllers())

    def list_controllers(self) -> tuple[Controller, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM controllers ORDER BY rowid").fetchall()
        return tuple(_controller_from_row(row) for row in rows)

    def get_controller(self, controller_id: str) -> Controller | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM controllers WHERE id = ?", (controller_id,)
            ).fetchone()
        return None if row is None else _controller_from_row(row)

    def save_outcome(
        self,
        controller: Controller,
        events: Iterable[DiagnosticEvent],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._update_controller(connection, controller)
            self._insert_events(connection, events)

    def transition_controller(
        self,
        controller_id: str,
        transition: Callable[[Controller], UpdateOutcome],
    ) -> UpdateOutcome | None:
        """Serialize read, immutable transition, and persistence in one DB transaction."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM controllers WHERE id = ?", (controller_id,)
            ).fetchone()
            if row is None:
                return None
            outcome = transition(_controller_from_row(row))
            self._update_controller(connection, outcome.controller)
            self._insert_events(connection, outcome.events)
            return outcome

    def list_events(
        self,
        *,
        controller_id: str | None,
        limit: int,
    ) -> tuple[DiagnosticEvent, ...]:
        query = "SELECT * FROM diagnostic_events"
        parameters: tuple[object, ...]
        if controller_id is None:
            query += " ORDER BY sequence DESC LIMIT ?"
            parameters = (limit,)
        else:
            query += " WHERE controller_id = ? ORDER BY sequence DESC LIMIT ?"
            parameters = (controller_id, limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_event_from_row(row) for row in reversed(rows))

    def reset(self, reset_event: DiagnosticEvent) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM diagnostic_events")
            connection.execute("DELETE FROM controllers")
            connection.execute("DELETE FROM sqlite_sequence WHERE name = 'diagnostic_events'")
            self._insert_controllers(connection, seed_controllers())
            self._insert_events(connection, (reset_event,))

    @staticmethod
    def _insert_controllers(
        connection: sqlite3.Connection,
        controllers: Iterable[Controller],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO controllers (
                id, name, current_version, previous_version, health, stage,
                voltage, connectivity, active_dtcs, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_controller_values(controller) for controller in controllers),
        )

    @staticmethod
    def _update_controller(
        connection: sqlite3.Connection,
        controller: Controller,
    ) -> None:
        connection.execute(
            """
            UPDATE controllers SET
                name = ?, current_version = ?, previous_version = ?, health = ?,
                stage = ?, voltage = ?, connectivity = ?, active_dtcs = ?, last_seen = ?
            WHERE id = ?
            """,
            (*_controller_values(controller)[1:], controller.id),
        )

    @staticmethod
    def _insert_events(
        connection: sqlite3.Connection,
        events: Iterable[DiagnosticEvent],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO diagnostic_events (
                timestamp, controller_id, severity, code, message, stage
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    event.timestamp.isoformat(),
                    event.controller_id,
                    event.severity.value,
                    event.code,
                    event.message,
                    event.stage.value,
                )
                for event in events
            ),
        )
        connection.execute(
            """
            DELETE FROM diagnostic_events
            WHERE sequence NOT IN (
                SELECT sequence FROM diagnostic_events
                ORDER BY sequence DESC LIMIT ?
            )
            """,
            (MAX_RETAINED_EVENTS,),
        )


def _controller_values(controller: Controller) -> tuple[object, ...]:
    return (
        controller.id,
        controller.name,
        str(controller.current_version),
        None if controller.previous_version is None else str(controller.previous_version),
        controller.health.value,
        controller.stage.value,
        controller.voltage,
        controller.connectivity,
        json.dumps(
            [
                {"code": dtc.code, "title": dtc.title, "detail": dtc.detail}
                for dtc in controller.active_dtcs
            ]
        ),
        controller.last_seen,
    )


def _controller_from_row(row: sqlite3.Row) -> Controller:
    dtcs = tuple(DiagnosticTroubleCode(**item) for item in json.loads(row["active_dtcs"]))
    previous = row["previous_version"]
    return Controller(
        id=row["id"],
        name=row["name"],
        current_version=SemanticVersion.parse(row["current_version"]),
        previous_version=None if previous is None else SemanticVersion.parse(previous),
        health=ControllerHealth(row["health"]),
        stage=UpdateStage(row["stage"]),
        voltage=row["voltage"],
        connectivity=row["connectivity"],
        active_dtcs=dtcs,
        last_seen=row["last_seen"],
    )


def _event_from_row(row: sqlite3.Row) -> DiagnosticEvent:
    from datetime import datetime

    return DiagnosticEvent(
        sequence=row["sequence"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        controller_id=row["controller_id"],
        severity=EventSeverity(row["severity"]),
        code=row["code"],
        message=row["message"],
        stage=UpdateStage(row["stage"]),
    )
