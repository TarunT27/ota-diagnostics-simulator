from __future__ import annotations

from pathlib import Path

import pytest

from ota_simulator.domain import ControllerHealth, FaultScenario, UpdateStage
from ota_simulator.repository import SQLiteRepository
from ota_simulator.service import ControllerNotFoundError, SimulatorService


def test_service_seeds_six_controller_fleet(service: SimulatorService) -> None:
    controllers = service.list_controllers()
    summary = service.summary()

    assert [controller.name for controller in controllers] == [
        "Gateway",
        "Telematics",
        "Infotainment",
        "ADAS",
        "Battery Management",
        "Body Control",
    ]
    assert summary["total_controllers"] == 6
    assert summary["healthy_controllers"] == 5
    assert summary["attention_controllers"] == 1
    assert summary["active_dtcs"] == 2


def test_successful_update_and_manual_rollback_are_persisted(
    service: SimulatorService,
    database_path: Path,
) -> None:
    updated = service.run_update("telematics", "2.1.0", FaultScenario.NONE)

    assert str(updated.current_version) == "2.1.0"
    assert updated.stage is UpdateStage.COMPLETED

    reopened = SimulatorService(SQLiteRepository(database_path))
    assert str(reopened.get_controller("telematics").current_version) == "2.1.0"

    rolled_back = reopened.rollback("telematics")
    assert str(rolled_back.current_version) == "2.0.4"
    assert rolled_back.stage is UpdateStage.ROLLED_BACK
    with pytest.raises(ValueError, match="previous version"):
        reopened.rollback("telematics")


@pytest.mark.parametrize(
    "fault",
    [
        FaultScenario.LOW_VOLTAGE,
        FaultScenario.CONNECTIVITY_LOSS,
        FaultScenario.CHECKSUM_MISMATCH,
        FaultScenario.INSTALL_FAILURE,
    ],
)
def test_fault_scenarios_record_dtc_and_event_history(
    service: SimulatorService,
    fault: FaultScenario,
) -> None:
    controller = service.run_update("gateway", "2.0.0", fault)
    events = service.list_events(controller_id="gateway", limit=50)

    assert controller.health is ControllerHealth.ATTENTION
    assert controller.active_dtcs
    assert events
    assert events[-1].stage in {UpdateStage.FAILED, UpdateStage.ROLLED_BACK}


def test_reset_is_deterministic_and_atomic(service: SimulatorService) -> None:
    service.run_update("gateway", "2.0.0", FaultScenario.NONE)

    service.reset()

    assert str(service.get_controller("gateway").current_version) == "1.3.2"
    assert service.get_controller("gateway").previous_version is None
    assert len(service.list_events(limit=100)) == 1
    assert service.list_events(limit=100)[0].code == "LAB_RESET"


def test_unknown_controller_raises_typed_error(service: SimulatorService) -> None:
    with pytest.raises(ControllerNotFoundError):
        service.get_controller("missing")
