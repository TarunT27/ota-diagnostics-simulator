"""Pure OTA transition engine plus the starter API compatibility wrapper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from enum import StrEnum

from ota_simulator.domain import (
    Controller,
    ControllerHealth,
    DiagnosticEvent,
    DiagnosticTroubleCode,
    EventSeverity,
    FaultScenario,
    SemanticVersion,
    UpdateOutcome,
    UpdatePackage,
    UpdateStage,
)

UPDATE_FAULT_CODES = frozenset({"B1101", "U0100", "P0606", "U3000"})


class UpdateState(StrEnum):
    READY = "ready"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class UpdateController:
    """Backward-compatible wrapper retained for the original public API."""

    def __init__(self, current_version: str) -> None:
        self.current_version = str(SemanticVersion.parse(current_version))
        self.previous_version: str | None = None
        self.state = UpdateState.READY

    def apply_update(self, target_version: str, checksum_valid: bool) -> bool:
        if not isinstance(checksum_valid, bool):
            raise TypeError("checksum_valid must be a boolean")
        target = SemanticVersion.parse(target_version)
        if not checksum_valid:
            self.state = UpdateState.FAILED
            return False
        if target <= SemanticVersion.parse(self.current_version):
            return False
        self.previous_version = self.current_version
        self.current_version = str(target)
        self.state = UpdateState.READY
        return True

    def rollback(self) -> bool:
        if self.previous_version is None:
            return False
        self.current_version = self.previous_version
        self.previous_version = None
        self.state = UpdateState.ROLLED_BACK
        return True


def execute_update(
    controller: Controller,
    package: UpdatePackage,
    fault: FaultScenario,
    *,
    clock: Callable[[], datetime],
) -> UpdateOutcome:
    if package.target_version <= controller.current_version:
        raise ValueError("target version must be newer than the installed version")

    events: tuple[DiagnosticEvent, ...] = ()

    def emit(
        stage: UpdateStage,
        code: str,
        message: str,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        nonlocal events
        events = (
            *events,
            DiagnosticEvent(
                sequence=None,
                timestamp=clock(),
                controller_id=controller.id,
                severity=severity,
                code=code,
                message=message,
                stage=stage,
            ),
        )

    emit(UpdateStage.PRECHECK, "UPDATE_STARTED", "Controller update workflow started.")
    if fault is FaultScenario.LOW_VOLTAGE:
        low_voltage_controller = replace(controller, voltage=10.4)
        return _fail(
            low_voltage_controller,
            events,
            clock,
            "B1101",
            "Low supply voltage blocked the update.",
        )
    emit(UpdateStage.PRECHECK, "PRECHECK_OK", "Voltage and compatibility checks passed.")

    if fault is FaultScenario.CONNECTIVITY_LOSS:
        failed = replace(controller, connectivity="offline")
        return _fail(failed, events, clock, "U0100", "Connectivity was lost during download.")
    emit(UpdateStage.DOWNLOADING, "DOWNLOAD_OK", "Firmware payload downloaded.")

    candidate_package = (
        package.with_payload(package.payload + b"-tampered")
        if fault is FaultScenario.CHECKSUM_MISMATCH
        else package
    )
    if not candidate_package.verify_checksum():
        return _fail(controller, events, clock, "P0606", "Package checksum did not match.")
    emit(UpdateStage.VALIDATING, "CHECKSUM_OK", "SHA-256 package checksum verified.")

    emit(UpdateStage.INSTALLING, "INSTALL_STARTED", "Firmware installation started.")
    if fault is FaultScenario.INSTALL_FAILURE:
        dtc = DiagnosticTroubleCode(
            code="U3000",
            title="Software installation failure",
            detail=(
                "The simulated installer failed and automatic rollback restored the prior image."
            ),
        )
        emit(UpdateStage.FAILED, dtc.code, dtc.detail, EventSeverity.ERROR)
        emit(
            UpdateStage.ROLLED_BACK,
            "AUTO_ROLLBACK",
            "Automatic rollback restored the previous firmware image.",
            EventSeverity.WARNING,
        )
        rolled_back = replace(
            _with_dtc(controller, dtc),
            stage=UpdateStage.ROLLED_BACK,
            health=ControllerHealth.ATTENTION,
        )
        return UpdateOutcome(rolled_back, events)

    emit(UpdateStage.VERIFYING, "HEALTH_CHECK_OK", "Post-install health check passed.")
    emit(UpdateStage.COMPLETED, "UPDATE_COMPLETED", "Controller update completed.")
    recovered_from_low_voltage = any(dtc.code == "B1101" for dtc in controller.active_dtcs)
    remaining_dtcs = tuple(
        dtc for dtc in controller.active_dtcs if dtc.code not in UPDATE_FAULT_CODES
    )
    updated = replace(
        controller,
        current_version=package.target_version,
        previous_version=controller.current_version,
        stage=UpdateStage.COMPLETED,
        health=(ControllerHealth.ATTENTION if remaining_dtcs else ControllerHealth.HEALTHY),
        voltage=12.4 if recovered_from_low_voltage else controller.voltage,
        connectivity="strong",
        active_dtcs=remaining_dtcs,
    )
    return UpdateOutcome(updated, events)


def execute_rollback(
    controller: Controller,
    *,
    clock: Callable[[], datetime],
) -> UpdateOutcome:
    if controller.previous_version is None:
        raise ValueError("controller has no previous version to restore")
    restored = replace(
        controller,
        current_version=controller.previous_version,
        previous_version=None,
        stage=UpdateStage.ROLLED_BACK,
    )
    event = DiagnosticEvent(
        sequence=None,
        timestamp=clock(),
        controller_id=controller.id,
        severity=EventSeverity.WARNING,
        code="MANUAL_ROLLBACK",
        message="Manual rollback restored the previous controller version.",
        stage=UpdateStage.ROLLED_BACK,
    )
    return UpdateOutcome(restored, (event,))


def _fail(
    controller: Controller,
    events: tuple[DiagnosticEvent, ...],
    clock: Callable[[], datetime],
    code: str,
    message: str,
) -> UpdateOutcome:
    dtc = DiagnosticTroubleCode(code=code, title=message, detail=message)
    failed_event = DiagnosticEvent(
        sequence=None,
        timestamp=clock(),
        controller_id=controller.id,
        severity=EventSeverity.ERROR,
        code=code,
        message=message,
        stage=UpdateStage.FAILED,
    )
    failed = replace(
        _with_dtc(controller, dtc),
        stage=UpdateStage.FAILED,
        health=ControllerHealth.ATTENTION,
    )
    return UpdateOutcome(failed, (*events, failed_event))


def _with_dtc(controller: Controller, dtc: DiagnosticTroubleCode) -> Controller:
    retained = tuple(existing for existing in controller.active_dtcs if existing.code != dtc.code)
    return replace(controller, active_dtcs=(*retained, dtc))
