from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ota_simulator.domain import (
    ControllerHealth,
    FaultScenario,
    SemanticVersion,
    UpdatePackage,
    UpdateStage,
)
from ota_simulator.scenarios import seed_controllers
from ota_simulator.updater import UpdateController, UpdateState, execute_update


def test_legacy_controller_applies_newer_version() -> None:
    controller = UpdateController("1.0.0")

    assert controller.apply_update("1.1.0", checksum_valid=True)
    assert controller.current_version == "1.1.0"
    assert controller.state is UpdateState.READY


def test_legacy_controller_rejects_non_boolean_checksum() -> None:
    controller = UpdateController("1.0.0")

    with pytest.raises(TypeError, match="boolean"):
        controller.apply_update("1.1.0", checksum_valid=1)  # type: ignore[arg-type]


def test_legacy_rollback_is_one_shot() -> None:
    controller = UpdateController("1.0.0")
    controller.apply_update("1.1.0", checksum_valid=True)

    assert controller.rollback()
    assert controller.current_version == "1.0.0"
    assert not controller.rollback()


def test_successful_update_returns_new_snapshot_and_complete_stage_history() -> None:
    original = seed_controllers()[0]
    package = UpdatePackage.build("2.0.0", b"gateway-2.0.0")

    outcome = execute_update(
        original,
        package,
        FaultScenario.NONE,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert original.current_version == SemanticVersion.parse("1.3.2")
    assert outcome.controller.current_version == SemanticVersion.parse("2.0.0")
    assert outcome.controller.previous_version == original.current_version
    assert outcome.controller.stage is UpdateStage.COMPLETED
    assert [event.stage for event in outcome.events] == [
        UpdateStage.PRECHECK,
        UpdateStage.PRECHECK,
        UpdateStage.DOWNLOADING,
        UpdateStage.VALIDATING,
        UpdateStage.INSTALLING,
        UpdateStage.VERIFYING,
        UpdateStage.COMPLETED,
    ]


@pytest.mark.parametrize(
    ("fault", "expected_stage", "expected_code"),
    [
        (FaultScenario.LOW_VOLTAGE, UpdateStage.FAILED, "B1101"),
        (FaultScenario.CONNECTIVITY_LOSS, UpdateStage.FAILED, "U0100"),
        (FaultScenario.CHECKSUM_MISMATCH, UpdateStage.FAILED, "P0606"),
        (FaultScenario.INSTALL_FAILURE, UpdateStage.ROLLED_BACK, "U3000"),
    ],
)
def test_faults_emit_diagnostics_without_installing_target(
    fault: FaultScenario,
    expected_stage: UpdateStage,
    expected_code: str,
) -> None:
    original = seed_controllers()[1]
    package = UpdatePackage.build("2.1.0", b"telematics-2.1.0")

    outcome = execute_update(
        original,
        package,
        fault,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert outcome.controller.current_version == original.current_version
    assert outcome.controller.stage is expected_stage
    assert outcome.controller.active_dtcs[-1].code == expected_code
    assert any(event.code == expected_code for event in outcome.events)


@pytest.mark.parametrize(
    ("fault", "false_success_code"),
    [
        (FaultScenario.LOW_VOLTAGE, "PRECHECK_OK"),
        (FaultScenario.CONNECTIVITY_LOSS, "DOWNLOAD_OK"),
        (FaultScenario.CHECKSUM_MISMATCH, "CHECKSUM_OK"),
    ],
)
def test_fault_history_never_claims_the_failed_check_succeeded(
    fault: FaultScenario,
    false_success_code: str,
) -> None:
    original = seed_controllers()[0]
    outcome = execute_update(
        original,
        UpdatePackage.build("2.0.0", b"gateway-2.0.0"),
        fault,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert false_success_code not in {event.code for event in outcome.events}
    if fault is FaultScenario.LOW_VOLTAGE:
        assert outcome.controller.voltage < 11.0


def test_retries_deduplicate_fault_dtcs_and_success_clears_update_faults() -> None:
    original = seed_controllers()[0]
    package = UpdatePackage.build("2.0.0", b"gateway-2.0.0")
    first_failure = execute_update(
        original,
        package,
        FaultScenario.CHECKSUM_MISMATCH,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).controller
    second_failure = execute_update(
        first_failure,
        package,
        FaultScenario.CHECKSUM_MISMATCH,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).controller
    recovered = execute_update(
        second_failure,
        package,
        FaultScenario.NONE,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).controller

    assert [dtc.code for dtc in second_failure.active_dtcs] == ["P0606"]
    assert recovered.active_dtcs == ()
    assert recovered.health is ControllerHealth.HEALTHY


def test_successful_retry_restores_nominal_voltage_after_low_voltage_fault() -> None:
    original = seed_controllers()[0]
    package = UpdatePackage.build("2.0.0", b"gateway-2.0.0")
    failed = execute_update(
        original,
        package,
        FaultScenario.LOW_VOLTAGE,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).controller
    recovered = execute_update(
        failed,
        package,
        FaultScenario.NONE,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).controller

    assert failed.voltage == 10.4
    assert recovered.voltage == 12.4
    assert recovered.health is ControllerHealth.HEALTHY


def test_successful_update_preserves_unrelated_active_diagnostics() -> None:
    original = seed_controllers()[1]
    recovered = execute_update(
        original,
        UpdatePackage.build("2.1.0", b"telematics-2.1.0"),
        FaultScenario.NONE,
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
    ).controller

    assert [dtc.code for dtc in recovered.active_dtcs] == ["B1A0F"]
    assert recovered.health is ControllerHealth.ATTENTION


def test_same_or_older_target_is_rejected_without_partial_change() -> None:
    original = seed_controllers()[0]
    package = UpdatePackage.build(str(original.current_version), b"same-version")

    with pytest.raises(ValueError, match="newer"):
        execute_update(
            original,
            package,
            FaultScenario.NONE,
            clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
        )

    assert original.stage is UpdateStage.IDLE
