from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ota_simulator.api import create_app
from ota_simulator.repository import SQLiteRepository
from ota_simulator.service import SimulatorService


class IncrementingClock:
    def __init__(self) -> None:
        self._current = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(seconds=1)
        return value


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "ota-lab.db"


@pytest.fixture
def repository(database_path: Path) -> SQLiteRepository:
    return SQLiteRepository(database_path)


@pytest.fixture
def service(repository: SQLiteRepository) -> SimulatorService:
    return SimulatorService(repository, clock=IncrementingClock())


@pytest.fixture
def client(service: SimulatorService) -> TestClient:
    app = create_app(service=service)
    with TestClient(app) as test_client:
        yield test_client
