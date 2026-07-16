from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from ota_simulator.domain import FaultScenario, UpdatePackage
from ota_simulator.repository import SQLiteRepository
from ota_simulator.updater import execute_update


def test_concurrent_repository_initialization_is_idempotent(database_path: Path) -> None:
    barrier = threading.Barrier(16)

    def initialize() -> int:
        barrier.wait()
        return len(SQLiteRepository(database_path).list_controllers())

    with ThreadPoolExecutor(max_workers=16) as executor:
        counts = tuple(executor.map(lambda _index: initialize(), range(16)))

    assert counts == (6,) * 16


def test_controller_transition_serializes_read_compute_and_write(database_path: Path) -> None:
    repository = SQLiteRepository(database_path)
    first_transition_started = threading.Event()
    release_first_transition = threading.Event()

    def install(version: str, wait: bool) -> None:
        def transition(controller):  # type: ignore[no-untyped-def]
            if wait:
                first_transition_started.set()
                assert release_first_transition.wait(timeout=5)
            return execute_update(
                controller,
                UpdatePackage.build(version, version.encode()),
                FaultScenario.NONE,
                clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
            )

        repository.transition_controller("gateway", transition)

    first = threading.Thread(target=install, args=("2.0.0", True))
    second = threading.Thread(target=install, args=("3.0.0", False))
    first.start()
    assert first_transition_started.wait(timeout=5)
    second.start()
    release_first_transition.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert str(repository.get_controller("gateway").current_version) == "3.0.0"  # type: ignore[union-attr]
